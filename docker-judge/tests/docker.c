/* 测试用假 docker (编译为 docker.exe, 行为与真实 docker CLI 同构)
 * 编译:  g++ -O2 -o docker.exe docker.c
 * 行为:  按环境变量 FAKE_MODE 返回固定结果; 参数记录到 FAKE_LOG
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv) {
    const char *log = getenv("FAKE_LOG");
    if (log) {
        FILE *f = fopen(log, "a");
        if (f) {
            int i;
            for (i = 0; i < argc; i++) fprintf(f, "%s ", argv[i]);
            fprintf(f, "\n");
            fclose(f);
        }
    }
    const char *mode = getenv("FAKE_MODE");
    if (mode && strcmp(mode, "compile") == 0) {
        fprintf(stderr, "COMPILE_ERROR\nmain.cpp:1:1: error: fake compile error\n");
        return 2;
    }
    if (mode && strcmp(mode, "timeout") == 0) {
        fprintf(stderr, "TIMEOUT\n");
        return 124;
    }
    if (mode && strcmp(mode, "exit3") == 0) return 3;
    if (mode && strcmp(mode, "sandbox") == 0) {
        fprintf(stderr, "Cannot connect to the Docker daemon\n");
        return 1;
    }
    printf("30\n");
    return 0;
}
