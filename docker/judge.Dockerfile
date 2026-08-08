# AIJudge 判题沙箱镜像 (Linux)
#
# 构建:  docker build -t aijudge-judge:latest .
# 运行:  由 code/judge_docker.py 自动调用, 每个测试用例一个一次性容器
#
# 安全设计:
#   - 非 root 用户 (uid=10001), 无 shell
#   - 根文件系统只读 (运行时由 docker run --read-only 强制)
#   - 源码只读挂载 /src, 编译产物在 tmpfs /work
#   - 无网络 (docker run --network none 强制)

FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        g++ \
        coreutils \
 && rm -rf /var/lib/apt/lists/* \
 && useradd -m -u 10001 -s /usr/sbin/nologin judge

COPY entrypoint.sh /usr/local/bin/judge-entrypoint
RUN chmod 0755 /usr/local/bin/judge-entrypoint

USER judge
WORKDIR /work

ENTRYPOINT ["/usr/local/bin/judge-entrypoint"]
