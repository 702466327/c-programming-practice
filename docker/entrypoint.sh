#!/bin/sh
# AIJudge 沙箱入口: 编译 + 运行, 输出稳定可解析
#
# 约定:
#   - 源码从只读挂载 /src/main.cpp 复制到 tmpfs /work 后编译
#   - 编译失败: stderr 首行 "COMPILE_ERROR", 退出码 2
#   - 运行超时: 由 coreutils timeout 终止, 退出码 124
#   - 测试输入经 stdin 传入, stdout/stderr 由 docker run 捕获

set -u

# 源码经环境变量 MAIN_SRC 传入 (兼容判题器运行在宿主机或应用容器内的场景)
printf '%s\n' "${MAIN_SRC:-}" > /work/main.cpp || exit 9
[ -s /work/main.cpp ] || exit 9
cd /work || exit 9

# 编译 (O2, 与旧 MinGW 参数对齐)
g++ -std=c++17 -O2 -pipe -fmax-errors=5 -o main main.cpp 2>compile_err.txt
rc=$?
if [ $rc -ne 0 ]; then
    echo "COMPILE_ERROR" >&2
    cat compile_err.txt >&2
    exit 2
fi

# 运行 (超时由 timeout 强杀; 外部还有 docker stop-timeout 与 Python 侧兜底)
TIMEOUT_SEC="${JUDGE_TIMEOUT:-5}"
timeout -k 2 --foreground "$TIMEOUT_SEC" ./main
rc=$?
if [ $rc -eq 124 ]; then
    echo "TIMEOUT" >&2
fi
exit $rc
