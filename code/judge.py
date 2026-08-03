"""判题模块 - C++ 代码编译 + 受限执行

安全设计（分层防御）：
1. 源码文本扫描：拦截已知危险 API/模式
2. 编译后二进制扫描：宏混淆/字符串拼接骗过源码扫描后，
   链接器导入表和 .rdata 中的危险符号仍然可查
3. 执行限制：临时目录隔离、超时、输出大小截断、进程树强杀、
   Windows Job Object（尽力而为，失败自动降级）

注意：Windows 单机方案无法做到完整沙箱（如 Docker）。
如需对不可信用户提供公网判题服务，应把判题放到独立容器/VM 中。
"""

import os
import re
import subprocess
import tempfile
import shutil
from pathlib import Path

MAX_CODE_LENGTH = 64 * 1024          # 代码最大 64KB
MAX_OUTPUT_BYTES = 128 * 1024        # 每个测试用例最多截取 128KB 输出

DANGEROUS_PATTERNS = [
    "system(", "popen(",
    "_popen", "_wsystem", "_wspawn",
    "execvp(", "execlp(", "execv(", "execl(",
    "execve(", "execle(", "execvpe(",
    "spawnl(", "spawnv(", "spawnlp(", "spawnvp(",
    "fopen(", "_wfopen", "ofstream", "ifstream", "fstream",
    "freopen(", "open(", "_open",
    "socket(", "connect(",
    "removedirectory", "createdirectory", "deletefile",
    "movefile", "copyfile", "findfirstfile",
    "createprocess", "shellexecute", "winexec", "loadlibrary",
    "getprocaddress", "createremotethread", "writeprocessmemory",
    "virtualalloc", "openprocess", "terminateprocess",
    "signal(", "raise(", "abort(", "setjmp", "longjmp",
    "fork(", "dlopen", "loadlibrary",
    "std::thread", "pthread",
    "getprocaddress", "loadlibrary",
    "getenv", "putenv", "_dupenv",
    "#include <windows.h>",
    "#include <fstream>",
    "#include <thread>",
    "#include <process.h>",
    "#include <direct.h>",
    # 常见命令字符串（防止通过 system() 拼接执行）
    "net user", "net localgroup", "whoami", "powershell",
    "cmd /c", "certutil", "reg add", "schtasks", "mshta",
    "wscript", "cscript", "bitsadmin", "wmic ", "taskkill",
    "netstat", "ipconfig", "curl ", "wget ", "ncat", "nc -",
    "adduser", "useradd",
]

# 编译产物（exe）中的危险导入/API 符号
BINARY_IMPORT_PATTERNS = [
    b"system", b"_wsystem", b"popen", b"_popen",
    b"spawnl", b"spawnv", b"spawnlp", b"spawnvp",
    b"CreateProcess", b"ShellExecute", b"WinExec",
    b"LoadLibrary", b"GetProcAddress", b"CreateRemoteThread",
    b"WriteProcessMemory", b"ReadProcessMemory", b"VirtualAlloc",
    b"OpenProcess", b"TerminateProcess", b"SetWindowsHookEx",
    b"RegOpenKey", b"RegSetValue", b"NetUserAdd",
    b"InternetOpen", b"URLDownloadToFile", b"WinHttpOpen",
    b"HttpSendRequest", b"DeleteFile", b"MoveFile",
    b"CopyFile", b"RemoveDirectory", b"CreateDirectory",
    b"FindFirstFile", b"SetTimer",
]

# 编译产物中的危险字符串（如命令、绝对路径）
BINARY_STRING_PATTERNS = [
    b"whoami", b"net user", b"net localgroup", b"cmd.exe", b"cmd /c",
    b"powershell", b"certutil", b"reg add", b"schtasks", b"mshta",
    b"bitsadmin", b"wmic", b"taskkill", b"nc.exe", b"ncat",
    b"key.txt", b"flag.txt", b"c:\\", b"d:\\",
]

EXECUTION_TIMEOUT = 5
COMPILE_TIMEOUT = 10

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
RUNTIME_ROOT = Path(os.environ.get("PROJECT_RUNTIME_ROOT", PROJECT_ROOT / "runtime"))
BUNDLED_COMPILER = os.environ.get("BUNDLED_COMPILER", "").strip()
COMPILER_SEARCH = [
    RUNTIME_ROOT / "mingw" / "bin" / "g++.exe",
    RUNTIME_ROOT / "mingw" / "bin" / "clang++.exe",
    RUNTIME_ROOT / "mingw" / "bin" / "c++.exe",
]


def _detect_compiler():
    if BUNDLED_COMPILER and Path(BUNDLED_COMPILER).exists():
        return BUNDLED_COMPILER

    for candidate in COMPILER_SEARCH:
        if candidate.exists():
            return str(candidate)

    for cc in ["g++", "clang++", "c++"]:
        found = shutil.which(cc)
        if found:
            return found

    return None


COMPILER = _detect_compiler()


def is_compiler_available():
    return COMPILER is not None


def get_compiler_name():
    return COMPILER or ""


def _scan_code_safety(code):
    if len(code.encode("utf-8")) > MAX_CODE_LENGTH:
        return False, "code too large"
    code_lower = code.lower()
    found = []
    for p in DANGEROUS_PATTERNS:
        if p in code_lower:
            found.append(p)
    # 额外结构检查：宏拼接、本地 include、pragma
    if "##" in code:
        found.append("macro token pasting (##)")
    if '#include "' in code_lower:
        found.append('local #include "..."')
    if "#pragma" in code_lower:
        found.append("#pragma")
    if found:
        return False, "code contains unsafe: " + ", ".join(found)
    return True, ""


def _scan_binary_safety(exe_path):
    """扫描编译产物中的危险导入/字符串，对抗宏混淆等源码级绕过。

    采用“基准差异”策略：先编译一个最小程序作为基线，
    只标记用户二进制中比基线多出来的危险符号，
    避免 MinGW 运行库自身的导入（LoadLibrary 等）造成误伤。
    """
    try:
        with open(exe_path, "rb") as f:
            data = f.read(8 * 1024 * 1024)
    except IOError:
        return False, "cannot read compiled binary"

    baseline = _get_baseline_binary()
    if baseline is None:
        # 基线不可用时跳过二进制扫描（仍有源码扫描兜底）
        return True, ""
    base_data, base_lower = baseline

    data_lower = data.lower()
    found = []
    for pat in BINARY_IMPORT_PATTERNS:
        if pat in data and pat not in base_data:
            found.append(pat.decode("ascii", "replace"))
    for pat in BINARY_STRING_PATTERNS:
        if pat in data_lower and pat not in base_lower:
            found.append(pat.decode("ascii", "replace"))
    if found:
        return False, "compiled code contains unsafe: " + ", ".join(dict.fromkeys(found))
    return True, ""


_BASELINE_BINARY = None


def _get_baseline_binary():
    """编译并缓存一个最小 C++ 程序的二进制，作为安全扫描基线"""
    global _BASELINE_BINARY
    if _BASELINE_BINARY is not None:
        return _BASELINE_BINARY
    if not is_compiler_available():
        return None
    try:
        work_dir = tempfile.mkdtemp(prefix="judge_base_")
        cpp = os.path.join(work_dir, "base.cpp")
        exe = os.path.join(work_dir, "base.exe")
        with open(cpp, "w", encoding="utf-8") as f:
            f.write("int main(){return 0;}\n")
        cp = subprocess.run(
            [COMPILER, "-std=c++17", "-O2", "-o", exe, cpp],
            capture_output=True,
            text=True,
            timeout=COMPILE_TIMEOUT,
            cwd=work_dir,
            env=_safe_env(),
        )
        if cp.returncode != 0:
            return None
        with open(exe, "rb") as f:
            data = f.read(8 * 1024 * 1024)
        _BASELINE_BINARY = (data, data.lower())
        return _BASELINE_BINARY
    except Exception:
        return None


def _safe_env():
    env = {}
    for key in ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP"):
        value = os.environ.get(key)
        if value:
            env[key] = value

    if COMPILER and Path(COMPILER).is_absolute():
        compiler_dir = str(Path(COMPILER).parent)
        current_path = env.get("PATH", "")
        if compiler_dir.lower() not in current_path.lower().split(";"):
            env["PATH"] = compiler_dir + (";" + current_path if current_path else "")
    return env


def _read_limited(filepath, limit):
    try:
        with open(filepath, "rb") as f:
            return f.read(limit).decode("utf-8", errors="replace")
    except IOError:
        return ""


def _kill_process_tree(pid):
    """强杀进程树（Windows taskkill /T）"""
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            timeout=5,
        )
    except Exception:
        pass


# ---- Windows Job Object 辅助（尽力而为，失败自动降级） ----
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x0008
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x0100
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CREATE_SUSPENDED = getattr(subprocess, "CREATE_SUSPENDED", 0x00000004)


def _create_job_object():
    """创建带限制的 Job 对象；不支持时返回 None"""
    try:
        import ctypes
        from ctypes import wintypes

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = (
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | JOB_OBJECT_LIMIT_PROCESS_MEMORY
        )
        info.BasicLimitInformation.ActiveProcessLimit = 8
        info.ProcessMemoryLimit = 256 * 1024 * 1024  # 256MB

        ok = kernel32.SetInformationJobObject(
            job, 9, ctypes.byref(info), ctypes.sizeof(info)
        )
        if not ok:
            kernel32.CloseHandle(job)
            return None
        return (kernel32, job)
    except Exception:
        return None


def _assign_job(job_ctx, process_handle):
    try:
        kernel32, job = job_ctx
        return bool(kernel32.AssignProcessToJobObject(job, process_handle))
    except Exception:
        return False


def _resume_process(process_handle):
    try:
        import ctypes
        ntdll = ctypes.WinDLL("ntdll")
        ntdll.NtResumeProcess(ctypes.c_void_p(process_handle))
    except Exception:
        pass


def _close_job(job_ctx):
    try:
        kernel32, job = job_ctx
        kernel32.CloseHandle(job)
    except Exception:
        pass


def _run_executable(exe_path, test_input=""):
    result = {"stdout": "", "stderr": "", "exit_code": 0, "timed_out": False, "compile_error": False}
    work_dir = str(Path(exe_path).parent)
    stdout_path = os.path.join(work_dir, "stdout.txt")
    stderr_path = os.path.join(work_dir, "stderr.txt")

    job_ctx = _create_job_object()
    creationflags = CREATE_NO_WINDOW
    if job_ctx is not None:
        creationflags |= CREATE_SUSPENDED

    proc = None
    try:
        with open(stdout_path, "wb") as so, open(stderr_path, "wb") as se:
            try:
                proc = subprocess.Popen(
                    [str(exe_path)],
                    stdin=subprocess.PIPE,
                    stdout=so,
                    stderr=se,
                    cwd=work_dir,
                    env=_safe_env(),
                    creationflags=creationflags,
                )
            except Exception as e:
                result["stderr"] = str(e)
                return result

            if job_ctx is not None:
                if _assign_job(job_ctx, proc._handle):
                    _resume_process(proc._handle)
                else:
                    _resume_process(proc._handle)
                    job_ctx = None  # 降级：仅依赖超时+进程树强杀

            try:
                proc.communicate(
                    input=test_input.encode("utf-8", errors="replace"),
                    timeout=EXECUTION_TIMEOUT,
                )
                result["exit_code"] = proc.returncode
            except subprocess.TimeoutExpired:
                result["timed_out"] = True
                result["stderr"] = "timeout " + str(EXECUTION_TIMEOUT) + "s"
                _kill_process_tree(proc.pid)
            except Exception as e:
                result["stderr"] = str(e)
                _kill_process_tree(proc.pid)
    finally:
        if proc is not None and proc.poll() is None:
            _kill_process_tree(proc.pid)
        if job_ctx is not None:
            _close_job(job_ctx)

    result["stdout"] = _read_limited(stdout_path, MAX_OUTPUT_BYTES)
    result["stderr"] = _read_limited(stderr_path, MAX_OUTPUT_BYTES)
    return result


def _compile_code(code, work_dir):
    result = {"stderr": "", "compile_error": False, "exe_path": ""}
    if not is_compiler_available():
        result["compile_error"] = True
        result["stderr"] = "C++ compiler not found"
        return result

    cpp = os.path.join(work_dir, "main.cpp")
    exe = os.path.join(work_dir, "main.exe")
    try:
        with open(cpp, "w", encoding="utf-8") as tmp:
            tmp.write(code)
        cp = subprocess.run(
            [COMPILER, "-std=c++17", "-O2", "-o", exe, cpp],
            capture_output=True,
            text=True,
            timeout=COMPILE_TIMEOUT,
            cwd=work_dir,
            env=_safe_env(),
        )
        if cp.returncode != 0:
            result["compile_error"] = True
            result["stderr"] = cp.stderr.strip()
            return result
        result["exe_path"] = exe
        return result
    except subprocess.TimeoutExpired:
        result["compile_error"] = True
        result["stderr"] = "compile timeout " + str(COMPILE_TIMEOUT) + "s"
    except Exception as e:
        result["stderr"] = str(e)
        result["compile_error"] = True
    return result


def run_test_cases(code, test_cases):
    safe, warn = _scan_code_safety(code)
    if not safe:
        return {"safety_check": False, "warning": warn, "passed": 0, "total": len(test_cases), "results": []}
    with tempfile.TemporaryDirectory(prefix="judge_") as work_dir:
        compiled = _compile_code(code, work_dir)
        if compiled["compile_error"]:
            return {
                "safety_check": True,
                "passed": 0,
                "total": len(test_cases),
                "compile_error": True,
                "compile_stderr": compiled["stderr"],
                "results": [],
            }
        # 编译后二进制安全扫描（对抗宏混淆绕过源码扫描）
        bin_safe, bin_warn = _scan_binary_safety(compiled["exe_path"])
        if not bin_safe:
            return {
                "safety_check": False,
                "warning": bin_warn,
                "passed": 0,
                "total": len(test_cases),
                "results": [],
            }
        results = []
        passed = 0
        for i, tc in enumerate(test_cases):
            er = _run_executable(compiled["exe_path"], tc.get("input", ""))
            actual = er["stdout"].strip()
            expected = tc["expected"].strip()
            if er["timed_out"]:
                ok = False
                detail = "timeout"
            elif er["exit_code"] != 0:
                ok = False
                detail = "exit=" + str(er["exit_code"])
            elif actual == expected:
                ok = True
                detail = "match"
            else:
                ok = False
                detail = "expected:\n" + expected + "\n\nactual:\n" + (actual or "(none)")
            if ok:
                passed += 1
            results.append({
                "case_index": i + 1,
                "passed": ok,
                "input": tc.get("input", ""),
                "expected": expected,
                "actual": actual,
                "detail": detail,
                "stderr": er["stderr"] if not ok else "",
            })
        return {"safety_check": True, "passed": passed, "total": len(test_cases), "results": results}


def format_execution_summary(judge_result):
    if not judge_result["safety_check"]:
        return "SAFETY\n" + judge_result["warning"]
    if judge_result.get("compile_error"):
        return "COMPILE ERROR\n" + judge_result.get("compile_stderr", "")
    lines = ["passed: " + str(judge_result["passed"]) + "/" + str(judge_result["total"])]
    for r in judge_result["results"]:
        icon = "OK" if r["passed"] else "FAIL"
        lines.append(icon + " case " + str(r["case_index"]) + ": " + r["detail"])
    return "\n".join(lines)
