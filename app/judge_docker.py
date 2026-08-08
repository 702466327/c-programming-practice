"""Docker 沙箱判题模块 - 替代 judge.py 的 Windows 本地执行路径

把「编译 + 运行」整体放入一次性 Linux 容器:
  - 非 root、只读根文件系统、无网络、默认 seccomp、cap-drop ALL、no-new-privileges
  - 内存/CPU/进程数/文件描述符配额, 每测试用例一个容器, 运行完即销毁 (--rm)
  - Docker 不可用/失联时拒绝判题 (fail-closed), 绝不回退到本机直接执行

输出结构与 judge.py 完全一致, server.py 与前端无需改动。
"""

import os
import shutil
import subprocess
import tempfile
import threading
import uuid

from judge import _scan_code_safety, format_execution_summary

JUDGE_IMAGE = os.environ.get("JUDGE_IMAGE", "aijudge-judge:latest")
EXEC_TIMEOUT = int(os.environ.get("JUDGE_TIMEOUT", "5"))              # 容器内运行超时(秒)
CONTAINER_TIMEOUT = int(os.environ.get("JUDGE_CONTAINER_TIMEOUT", "40"))  # docker run 整体超时
MAX_OUTPUT_BYTES = 128 * 1024
MAX_CONCURRENCY = int(os.environ.get("JUDGE_MAX_CONCURRENCY", "2"))

_semaphore = threading.BoundedSemaphore(MAX_CONCURRENCY)

# docker run 安全基线参数 (勿随意删减)
DOCKER_RUN_FLAGS = [
    "--rm",
    "--network", "none",                          # 无网络
    "--memory", "512m", "--memory-swap", "512m",  # 内存上限 (含编译)
    "--cpus", "1.0",                              # CPU 上限
    "--pids-limit", "64",                         # 进程数上限
    "--ulimit", "nofile=64:64",
    "--ulimit", "core=0",
    "--cap-drop", "ALL",                          # 剥离全部能力
    "--security-opt", "no-new-privileges",
    "--read-only",                                # 根文件系统只读
    "--tmpfs", "/tmp:rw,size=64m,noexec,nosuid,mode=1777",
    "--tmpfs", "/work:rw,size=128m,nosuid,exec,mode=1777",
    "--workdir", "/work",
    "--user", "10001:10001",                      # 非 root
    "--log-driver", "none",
    "--pull", "never",                            # 禁止每次运行时拉取
    "--stop-timeout", "5",
    "--env", "JUDGE_TIMEOUT=" + str(EXEC_TIMEOUT),
    "-i",
]


def is_docker_available(env=None):
    path = (env if env is not None else os.environ).get("PATH", "")
    return shutil.which("docker", path=path) is not None


def _run_one(code, test_input, name, env):
    """启动一个一次性容器编译并运行, 返回原始结果 dict"""
    src_dir = tempfile.mkdtemp(prefix="judge_src_")
    try:
        with open(os.path.join(src_dir, "main.cpp"), "w", encoding="utf-8") as f:
            f.write(code)
        # mkdtemp 默认 0700 且属主为服务账户; 容器内 uid=10001 需要可读
        os.chmod(src_dir, 0o755)
        os.chmod(os.path.join(src_dir, "main.cpp"), 0o644)

        cmd = ["docker", "run", *DOCKER_RUN_FLAGS, "--name", name,
               "-v", f"{src_dir}:/src:ro", JUDGE_IMAGE]
        try:
            proc = subprocess.run(
                cmd,
                input=test_input.encode("utf-8", errors="replace"),
                capture_output=True,
                timeout=CONTAINER_TIMEOUT,
                env=env,
            )
        except subprocess.TimeoutExpired:
            # Python 侧兜底: 强杀容器
            try:
                subprocess.run(["docker", "kill", name],
                               capture_output=True, timeout=10, env=env)
            except Exception:
                pass
            return {"timed_out": True, "stdout": "", "stderr": "timeout"}
        except Exception as e:
            return {"sandbox_error": str(e), "stdout": "", "stderr": ""}

        stdout = proc.stdout[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        stderr = proc.stderr[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        rc = proc.returncode

        # Docker 守护进程不可用 / 镜像缺失等环境错误
        low = stderr.lower()
        daemon_errs = ("cannot connect to the docker daemon",
                       "is the docker daemon running",
                       "error during connect",
                       "dial unix",
                       "connection refused",
                       "no such image",
                       "pull access denied")
        if rc != 0 and any(e in low for e in daemon_errs):
            return {"sandbox_error": stderr.strip()[:300], "stdout": stdout, "stderr": stderr}

        if "COMPILE_ERROR" in stderr:
            return {"compile_error": True, "stdout": stdout, "stderr": stderr}
        if rc == 124:
            return {"timed_out": True, "stdout": stdout, "stderr": stderr}
        return {"exit_code": rc, "stdout": stdout, "stderr": stderr}
    finally:
        shutil.rmtree(src_dir, ignore_errors=True)


def run_test_cases(code, test_cases, _env=None):
    """判题入口, 输出结构与 judge.run_test_cases 一致"""
    env = _env if _env is not None else os.environ

    # 第一层防御: 保留原有源码黑名单 (纵深防御, 非唯一防线)
    safe, warn = _scan_code_safety(code)
    if not safe:
        return {"safety_check": False, "warning": warn,
                "passed": 0, "total": len(test_cases), "results": []}

    # fail-closed: 沙箱不可用直接拒绝, 不降级到本机执行
    if not is_docker_available(env):
        return {
            "safety_check": False,
            "warning": "判题沙箱不可用: 未检测到 Docker (fail-closed, 已拒绝执行)",
            "passed": 0, "total": len(test_cases), "results": [],
        }

    results = []
    passed = 0
    for i, tc in enumerate(test_cases):
        with _semaphore:
            name = "aijudge-" + uuid.uuid4().hex[:12]
            er = _run_one(code, tc.get("input", ""), name, env)

        if er.get("sandbox_error"):
            return {
                "safety_check": False,
                "warning": "判题沙箱错误: " + er["sandbox_error"],
                "passed": 0, "total": len(test_cases), "results": [],
            }
        if er.get("compile_error"):
            ok = False
            detail = "compile error"
            stderr = er["stderr"]
        elif er.get("timed_out"):
            ok = False
            detail = "timeout"
            stderr = ""
        else:
            rc = er.get("exit_code", 0)
            actual = er["stdout"].strip()
            expected = tc["expected"].strip()
            if rc != 0:
                ok = False
                detail = "exit=" + str(rc)
            elif actual == expected:
                ok = True
                detail = "match"
            else:
                ok = False
                detail = "expected:\n" + expected + "\n\nactual:\n" + (actual or "(none)")
            stderr = er["stderr"] if not ok else ""

        if ok:
            passed += 1
        results.append({
            "case_index": i + 1,
            "passed": ok,
            "input": tc.get("input", ""),
            "expected": tc.get("expected", ""),
            "actual": er.get("stdout", "").strip(),
            "detail": detail,
            "stderr": stderr,
        })

    return {"safety_check": True, "passed": passed,
            "total": len(test_cases), "results": results}
