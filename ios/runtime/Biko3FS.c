/* devkitPro newlib's devoptab ABI; guest FILE and _reent stay in guest newlib.
 * Source: devkitPro/newlib, branch devkitPro, sys/iosupport.h and sys/stat.h.
 * Biko3's 104-byte stat stack buffer and deviceData offset 0xb0 agree. */
#include "Biko3FS.h"
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

typedef struct { int64_t sec, ns; } GuestTimespec;
typedef struct {
    int16_t dev; uint16_t ino; uint32_t mode;
    uint16_t nlink, uid, gid; int16_t rdev;
    int64_t size; GuestTimespec atim, mtim, ctim;
    int64_t blksize, blocks, spare[2];
} GuestStat;
_Static_assert(sizeof(GuestStat) == 104, "Biko3 newlib stat ABI");
_Static_assert(offsetof(GuestStat, size) == 16, "Biko3 stat size offset");
typedef struct { int device; void *state; } GuestDir;
typedef struct {
    const char *name; size_t file_size;
    int (*open)(void *, void *, const char *, int, int);
    int (*close)(void *, void *);
    ssize_t (*write)(void *, void *, const char *, size_t);
    ssize_t (*read)(void *, void *, char *, size_t);
    int64_t (*seek)(void *, void *, int64_t, int);
    int (*fstat)(void *, void *, GuestStat *);
    int (*stat)(void *, const char *, GuestStat *);
    void *link;
    int (*unlink)(void *, const char *);
    int (*chdir)(void *, const char *);
    int (*rename)(void *, const char *, const char *);
    int (*mkdir)(void *, const char *, int);
    size_t dir_size;
    GuestDir *(*diropen)(void *, GuestDir *, const char *);
    int (*dirreset)(void *, GuestDir *);
    int (*dirnext)(void *, GuestDir *, char *, GuestStat *);
    int (*dirclose)(void *, GuestDir *);
    void *statvfs;
    int (*truncate)(void *, void *, int64_t);
    int (*sync)(void *, void *);
    void *device_data;
    void *tail[9];
} GuestDevice;
_Static_assert(offsetof(GuestDevice, device_data) == 0xb0, "devoptab ABI");
typedef struct { DIR *dir; char root[PATH_MAX]; } HostDir;

static int fail(void *reent) {
    /* Common POSIX errno 1..34 are identical; newlib EAGAIN is 11. */
    int value = errno;
    if (errno == EAGAIN) value = 11;
    else if (errno == ENOSYS) value = 88;
    else if (errno == ENOTEMPTY) value = 90;
    else if (errno == ENAMETOOLONG) value = 91;
    else if (errno == EOVERFLOW) value = 139;
    *(int *)reent = value;
    return -1;
}
static const char *mapped(const char *path, char out[PATH_MAX]) {
    const char *root = NULL, *suffix = path;
    if (!strncmp(path, "romfs:", 6)) { root = getenv("BIKO3_GAME_ROOT"); suffix += 6; }
    else if (!strncmp(path, "save:/biko3", 11)) { root = getenv("BIKO3_SAVE_ROOT"); suffix += 11; }
    else if (!strncmp(path, "save:", 5)) { root = getenv("BIKO3_SAVE_ROOT"); suffix += 5; }
    else if (!strncmp(path, "sdmc:/switch/biko3/game", 23)) { root = getenv("BIKO3_GAME_ROOT"); suffix += 23; }
    else if (!strncmp(path, "sdmc:/switch/biko3/captures", 27)) { root = getenv("BIKO3_CAPTURE_ROOT"); suffix += 27; }
    else if (strchr(path, ':')) { errno = ENOENT; return NULL; }
    if (!root) {
        if (strlen(path) >= PATH_MAX) { errno = ENAMETOOLONG; return NULL; }
        return path;
    }
    while (*suffix == '/') suffix++;
    int count = snprintf(out, PATH_MAX, "%s/%s", root, suffix);
    if (count < 0 || count >= PATH_MAX) { errno = ENAMETOOLONG; return NULL; }
    return out;
}
static int op_open(void *r, void *file, const char *path, int flags, int mode) {
    char buf[PATH_MAX]; const char *p = mapped(path, buf);
    if (!p) return fail(r);
    int host_flags = flags & 3;
    if (flags & 0x8) host_flags |= O_APPEND;
    if (flags & 0x200) host_flags |= O_CREAT;
    if (flags & 0x400) host_flags |= O_TRUNC;
    if (flags & 0x800) host_flags |= O_EXCL;
    int fd = open(p, host_flags, mode);
    if (fd < 0) return fail(r);
    *(int *)file = fd; return 0;
}
static int op_close(void *r, void *file) { return close(*(int *)file) ? fail(r) : 0; }
static ssize_t op_read(void *r, void *file, char *buf, size_t len) {
    ssize_t n = read(*(int *)file, buf, len); return n < 0 ? fail(r) : n;
}
static ssize_t op_write(void *r, void *file, const char *buf, size_t len) {
    ssize_t n = write(*(int *)file, buf, len); return n < 0 ? fail(r) : n;
}
static ssize_t op_log(void *r, void *file, const char *buf, size_t len) {
    (void)r; (void)file;
    return (ssize_t)fwrite(buf, 1, len, stderr);
}
static int64_t op_seek(void *r, void *file, int64_t offset, int whence) {
    off_t n = lseek(*(int *)file, (off_t)offset, whence); return n < 0 ? fail(r) : n;
}
static void guest_stat(GuestStat *g, const struct stat *s) {
    memset(g, 0, sizeof *g);
    g->dev = (int16_t)s->st_dev; g->ino = (uint16_t)s->st_ino;
    g->mode = (uint32_t)s->st_mode; g->nlink = (uint16_t)s->st_nlink;
    g->uid = (uint16_t)s->st_uid; g->gid = (uint16_t)s->st_gid;
    g->rdev = (int16_t)s->st_rdev; g->size = s->st_size;
    g->atim.sec = s->st_atime; g->mtim.sec = s->st_mtime; g->ctim.sec = s->st_ctime;
    g->blksize = s->st_blksize; g->blocks = s->st_blocks;
}
static int op_stat(void *r, const char *path, GuestStat *out) {
    char buf[PATH_MAX]; const char *p = mapped(path, buf); struct stat s;
    if (!p || stat(p, &s)) {
        int saved = errno;
        if (getenv("BIKO3_FS_TRACE"))
            fprintf(stderr, "[biko3] newlib stat %s -> -1 errno=%d\n", path, saved);
        errno = saved;
        return fail(r);
    }
    if (getenv("BIKO3_FS_TRACE"))
        fprintf(stderr, "[biko3] newlib stat %s -> 0 bytes=%lld\n", path, (long long)s.st_size);
    guest_stat(out, &s); return 0;
}
static int op_fstat(void *r, void *file, GuestStat *out) {
    struct stat s; if (fstat(*(int *)file, &s)) return fail(r);
    guest_stat(out, &s); return 0;
}
static int op_unlink(void *r, const char *path) {
    char buf[PATH_MAX]; const char *p = mapped(path, buf);
    return !p || unlink(p) ? fail(r) : 0;
}
static int op_chdir(void *r, const char *path) {
    char buf[PATH_MAX]; const char *p = mapped(path, buf);
    return !p || chdir(p) ? fail(r) : 0;
}
static int op_rename(void *r, const char *old, const char *new_path) {
    char a[PATH_MAX], b[PATH_MAX]; const char *pa = mapped(old, a), *pb = mapped(new_path, b);
    return !pa || !pb || rename(pa, pb) ? fail(r) : 0;
}
static int op_mkdir(void *r, const char *path, int mode) {
    char buf[PATH_MAX]; const char *p = mapped(path, buf);
    return !p || mkdir(p, (mode_t)mode) ? fail(r) : 0;
}
static int op_truncate(void *r, void *file, int64_t size) {
    return ftruncate(*(int *)file, (off_t)size) ? fail(r) : 0;
}
static int op_sync(void *r, void *file) { return fsync(*(int *)file) ? fail(r) : 0; }
static GuestDir *op_diropen(void *r, GuestDir *d, const char *path) {
    HostDir *state = d->state; char buf[PATH_MAX]; const char *p = mapped(path, buf);
    if (!p || !(state->dir = opendir(p))) { fail(r); return NULL; }
    snprintf(state->root, sizeof state->root, "%s", p); return d;
}
static int op_dirreset(void *r, GuestDir *d) { (void)r; rewinddir(((HostDir *)d->state)->dir); return 0; }
static int op_dirclose(void *r, GuestDir *d) { return closedir(((HostDir *)d->state)->dir) ? fail(r) : 0; }
static int op_dirnext(void *r, GuestDir *d, char *name, GuestStat *out) {
    HostDir *state = d->state; struct dirent *ent;
    do { ent = readdir(state->dir); } while (ent && (!strcmp(ent->d_name, ".") || !strcmp(ent->d_name, "..")));
    if (!ent) { errno = ENOENT; return fail(r); }
    if (strlen(ent->d_name) > 255) { errno = ENAMETOOLONG; return fail(r); }
    strcpy(name, ent->d_name);
    char path[PATH_MAX]; int count = snprintf(path, sizeof path, "%s/%s", state->root, name);
    if (count < 0 || (size_t)count >= sizeof path) { errno = ENAMETOOLONG; return fail(r); }
    return op_stat(r, path, out);
}
static const GuestDevice device = {
    .name = "ios", .file_size = sizeof(int), .open = op_open, .close = op_close,
    .write = op_write, .read = op_read, .seek = op_seek, .fstat = op_fstat, .stat = op_stat,
    .unlink = op_unlink, .chdir = op_chdir, .rename = op_rename, .mkdir = op_mkdir,
    .dir_size = sizeof(HostDir), .diropen = op_diropen, .dirreset = op_dirreset,
    .dirnext = op_dirnext, .dirclose = op_dirclose, .truncate = op_truncate, .sync = op_sync
};
static const GuestDevice log_device = {.name = "ioslog", .write = op_log};
int biko3_find_device(const char *path) { (void)path; return 3; }
void biko3_fs_install(uint8_t *base) {
    const GuestDevice **list = (const GuestDevice **)(base + 0xf9a910);
    list[1] = &log_device; list[2] = &log_device; list[3] = &device;
    fprintf(stderr, "[biko3] installed native newlib file devices\n");
}
