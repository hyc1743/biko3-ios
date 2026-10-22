#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include "Biko3FS.c"

static void put(const char *path, const char *value) {
    FILE *f = fopen(path, "wb"); assert(f);
    assert(fputs(value, f) >= 0); assert(!fclose(f));
}
int main(int argc, char **argv) {
    assert(argc == 2);
    assert(!setenv("BIKO3_GAME_ROOT", argv[1], 1));
    char root[PATH_MAX], data[PATH_MAX], other[PATH_MAX], buffer[PATH_MAX];
    snprintf(root, sizeof root, "%s/patch.pp", argv[1]);
    snprintf(data, sizeof data, "%s/Data/patch.pp", argv[1]);
    snprintf(other, sizeof other, "%s/other.pp", argv[1]);
    put(data, "DATA");
    assert(!strcmp(read_patch_path(root, buffer), data));
    GuestStat info; int reent = 0, fd; char content[4];
    assert(!op_stat(&reent, root, &info) && info.size == 4);
    assert(!op_open(&reent, &fd, root, 0, 0));
    assert(op_read(&reent, &fd, content, 4) == 4 && !memcmp(content, "DATA", 4));
    assert(!op_close(&reent, &fd));
    assert(op_open(&reent, &fd, root, 1, 0) == -1);
    assert(!strcmp(read_patch_path(other, buffer), other));
    put(root, "ROOT");
    assert(!strcmp(read_patch_path(root, buffer), root));
    assert(!op_open(&reent, &fd, root, 0, 0));
    assert(op_read(&reent, &fd, content, 4) == 4 && !memcmp(content, "ROOT", 4));
    assert(!op_close(&reent, &fd));
    puts("FS_PATCH_ALL_PASSED");
    return 0;
}
