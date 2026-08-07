"""judge_docker.py 的 Python 层验证 (无需真实 Docker)

用 fake_docker.cmd 模拟 docker CLI, 验证:
  1. 安全参数齐全 (network none / cap-drop ALL / read-only / 非root / 资源配额 ...)
  2. 正常判题: 输出匹配 -> passed
  3. 编译失败 / 超时 / 非零退出 的解析
  4. fail-closed: 无 docker 时拒绝执行
运行:  python tests/test_docker_judge.py
"""

import os
import sys
import tempfile

TEST_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_CODE = os.path.abspath(os.path.join(TEST_DIR, "..", "..", "code"))

sys.path.insert(0, PROJECT_CODE)
import judge_docker  # noqa: E402


FAILED = []


def check(name, cond, extra=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name} {extra}")
    if not cond:
        FAILED.append(name)


def make_env(mode, log_path):
    fake_dir = TEST_DIR
    # Windows CreateProcess 用父进程 PATH 搜索可执行文件, 必须同时加入进程级 PATH
    os.environ["PATH"] = fake_dir + os.pathsep + os.environ.get("PATH", "")
    env = dict(os.environ)
    env["FAKE_MODE"] = mode
    env["FAKE_LOG"] = log_path
    return env


def run_case(code, test_cases, mode):
    log = tempfile.mktemp(suffix=".log")
    env = make_env(mode, log)
    result = judge_docker.run_test_cases(code, test_cases, _env=env)
    with open(log, "r", encoding="utf-8", errors="replace") as f:
        args = f.read()
    return result, args


def main():
    code_ab = "#include <iostream>\nint main(){int a,b;std::cin>>a>>b;std::cout<<a+b;return 0;}"
    tcs = [{"input": "10 20", "expected": "30"}]

    # 1. 正常判题
    result, args = run_case(code_ab, tcs, "normal")
    check("正常判题 passed", result["passed"] == 1 and result["results"][0]["detail"] == "match",
          str(result))

    # 2. 安全参数断言
    required = ["--network", "none", "--memory", "512m", "--cpus", "1.0",
                "--pids-limit", "64", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--read-only",
                "--tmpfs", "/tmp:rw,size=64m,noexec,nosuid,mode=1777",
                "--tmpfs", "/work:rw,size=128m,nosuid,exec,mode=1777",
                "--user", "10001:10001", "--pull", "never", "--rm",
                ":/src:ro", "aijudge-judge:latest", "JUDGE_TIMEOUT=5"]
    missing = [r for r in required if r not in args]
    check("安全参数齐全", not missing, "缺失: " + str(missing))

    # 3. 编译失败
    result, _ = run_case("int main(){ syntax error }", tcs, "compile")
    r0 = result["results"][0]
    check("编译失败解析", "compile error" in r0["detail"] and "COMPILE_ERROR" in r0["stderr"],
          str(r0))

    # 4. 超时
    result, _ = run_case("int main(){for(;;);}", tcs, "timeout")
    check("超时解析", result["results"][0]["detail"] == "timeout", str(result["results"][0]))

    # 5. 非零退出
    result, _ = run_case("int main(){return 3;}", tcs, "exit3")
    check("非零退出", result["results"][0]["detail"] == "exit=3", str(result["results"][0]))

    # 6. 守护进程错误 -> fail-closed
    result, _ = run_case(code_ab, tcs, "sandbox")
    check("守护进程错误 fail-closed", result["safety_check"] is False
          and "判题沙箱错误" in result["warning"], str(result))

    # 7. 无 docker -> fail-closed
    env = dict(os.environ)
    env["PATH"] = r"C:\Windows\System32"
    result = judge_docker.run_test_cases(code_ab, tcs, _env=env)
    check("无docker fail-closed", result["safety_check"] is False
          and "未检测到 Docker" in result["warning"], str(result))

    # 8. 源码黑名单仍生效 (纵深防御)
    result = judge_docker.run_test_cases(
        "#include <fstream>", tcs, _env=make_env("normal", tempfile.mktemp()))
    check("黑名单第一层仍生效", result["safety_check"] is False
          and "fstream" in result["warning"], str(result))

    print()
    if FAILED:
        print("FAILED: " + ", ".join(FAILED))
        sys.exit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
