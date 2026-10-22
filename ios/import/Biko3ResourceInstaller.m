#import "Biko3ResourceInstaller.h"
#include <CommonCrypto/CommonDigest.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>

NSString *const Biko3ResourceImportErrorDomain = @"Biko3ResourceImport";
static const size_t ChunkSize = 1024 * 1024;
static NSString *const ReceiptName = @".biko3-resources-v1.json";
typedef void (^ImportProgress)(NSString *, double);

static BOOL Failure(NSError **error, NSInteger code, NSString *message) {
    if (error) *error = [NSError errorWithDomain:Biko3ResourceImportErrorDomain
        code:code userInfo:@{NSLocalizedDescriptionKey: message}];
    return NO;
}
static BOOL DiskFailure(NSError **error, NSString *operation) {
    int code = errno;
    return Failure(error, 4, [NSString stringWithFormat:@"%@：%s", operation, strerror(code)]);
}
static BOOL Integer(id value, uint64_t *out) {
    if (![value isKindOfClass:NSNumber.class] || CFGetTypeID((__bridge CFTypeRef)value) == CFBooleanGetTypeID()) return NO;
    const char *s = [[value stringValue] UTF8String];
    if (!s || !*s || *s == '-') return NO;
    errno = 0; char *end = NULL; unsigned long long n = strtoull(s, &end, 10);
    if (errno || !end || *end || n > LLONG_MAX) return NO;
    *out = n; return YES;
}
static BOOL HashString(id value) {
    if (![value isKindOfClass:NSString.class] || [value length] != 64) return NO;
    return [value rangeOfCharacterFromSet:[[NSCharacterSet characterSetWithCharactersInString:@"0123456789abcdef"] invertedSet]].location == NSNotFound;
}
static BOOL SafeComponent(NSString *part) {
    if (![part isKindOfClass:NSString.class] || !part.length || [part isEqual:@"."] || [part isEqual:@".."]) return NO;
    if ([part hasSuffix:@"."] || [part hasSuffix:@" "] ||
        [part rangeOfCharacterFromSet:[NSCharacterSet controlCharacterSet]].location != NSNotFound ||
        [part rangeOfCharacterFromSet:[NSCharacterSet characterSetWithCharactersInString:@"/\\:<>\"|?*"]].location != NSNotFound) return NO;
    const char *bytes = part.UTF8String;
    return bytes && strlen(bytes) == [part lengthOfBytesUsingEncoding:NSUTF8StringEncoding] && strlen(bytes) <= NAME_MAX;
}
static BOOL SafePath(id value) {
    if (![value isKindOfClass:NSString.class] || ![value hasPrefix:@"Data/"] || [value length] > 4096) return NO;
    if (![value isEqual:[value precomposedStringWithCanonicalMapping]]) return NO;
    for (NSString *part in [value componentsSeparatedByString:@"/"])
        if (!SafeComponent(part)) return NO;
    return YES;
}
static NSString *Hex(const unsigned char digest[CC_SHA256_DIGEST_LENGTH]) {
    char hex[CC_SHA256_DIGEST_LENGTH * 2 + 1];
    for (size_t i = 0; i < CC_SHA256_DIGEST_LENGTH; i++) snprintf(hex + i * 2, 3, "%02x", digest[i]);
    return [NSString stringWithUTF8String:hex];
}
static NSString *DataHash(NSData *data) {
    unsigned char result[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(data.bytes, (CC_LONG)data.length, result); return Hex(result);
}
static int DirectoryAt(int parent, NSString *name, BOOL create, NSError **error) {
    int fd = openat(parent, name.fileSystemRepresentation, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0 && errno == ENOENT && create) {
        if (mkdirat(parent, name.fileSystemRepresentation, 0700) && errno != EEXIST) {
            DiskFailure(error, @"无法创建资源文件夹"); return -1;
        }
        fd = openat(parent, name.fileSystemRepresentation, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    }
    if (fd < 0) DiskFailure(error, [NSString stringWithFormat:@"无法安全打开文件夹 %@", name]);
    return fd;
}
static int ParentAt(int root, NSString *path, BOOL create, NSString **leaf, BOOL *missing, NSError **error) {
    NSArray<NSString *> *parts = [path componentsSeparatedByString:@"/"];
    int fd = dup(root); if (fd < 0) { DiskFailure(error, @"无法打开资源目录"); return -1; }
    *leaf = parts.lastObject;
    for (NSUInteger i = 0; i + 1 < parts.count; i++) {
        int next = openat(fd, parts[i].fileSystemRepresentation, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        if (next < 0 && errno == ENOENT && !create) {
            if (missing) *missing = YES; close(fd); return -1;
        }
        if (next < 0 && create && errno == ENOENT)
            next = DirectoryAt(fd, parts[i], YES, error);
        else if (next < 0) DiskFailure(error, @"资源目录包含无法安全打开的路径");
        close(fd); if (next < 0) return -1; fd = next;
    }
    return fd;
}
static int FileAt(int parent, NSString *name, BOOL *missing, NSError **error) {
    int fd = openat(parent, name.fileSystemRepresentation, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) {
        if (errno == ENOENT) { if (missing) *missing = YES; }
        else DiskFailure(error, [NSString stringWithFormat:@"无法安全读取 %@", name]);
        return -1;
    }
    struct stat st;
    if (fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_nlink != 1) {
        close(fd); Failure(error, 5, [NSString stringWithFormat:@"%@ 必须是普通文件，不能是链接", name]); return -1;
    }
    return fd;
}
static NSDictionary *Stamp(struct stat *s) {
    return @{ @"size": @((uint64_t)s->st_size), @"device": @((uint64_t)s->st_dev),
        @"inode": @((uint64_t)s->st_ino), @"mtime_sec": @(s->st_mtimespec.tv_sec),
        @"mtime_nsec": @(s->st_mtimespec.tv_nsec), @"ctime_sec": @(s->st_ctimespec.tv_sec),
        @"ctime_nsec": @(s->st_ctimespec.tv_nsec) };
}
static NSData *ReadSmall(int fd, NSError **error) {
    struct stat st;
    if (fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_size < 0 || st.st_size > 16 * 1024 * 1024) {
        Failure(error, 1, @"资源清单或校验记录大小无效"); return nil;
    }
    NSMutableData *data = [NSMutableData dataWithLength:(NSUInteger)st.st_size];
    size_t done = 0;
    while (done < data.length) {
        ssize_t n = pread(fd, (uint8_t *)data.mutableBytes + done, data.length - done, (off_t)done);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) { DiskFailure(error, @"读取资源清单失败"); return nil; }
        done += (size_t)n;
    }
    return data;
}
static BOOL WriteAll(int fd, const void *data, size_t length, NSError **error) {
    const uint8_t *p = data;
    while (length) {
        ssize_t n = write(fd, p, length);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return DiskFailure(error, @"写入资源失败，请检查可用空间");
        p += n; length -= (size_t)n;
    }
    return YES;
}

@implementation Biko3ResourceInstaller {
    NSDictionary *_manifest;
    NSString *_manifestHash;
    NSURL *_documentsURL;
    NSURL *_gameURL;
}
- (NSURL *)gameURL { return _gameURL; }
- (instancetype)initWithManifestURL:(NSURL *)manifestURL documentsURL:(NSURL *)documentsURL error:(NSError **)error {
    if (!(self = [super init])) return nil;
    if (!manifestURL.isFileURL || !documentsURL.isFileURL) {
        Failure(error, 1, @"资源清单或文档目录无效"); return nil;
    }
    int fd = open(manifestURL.fileSystemRepresentation, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) { DiskFailure(error, @"无法打开内置资源清单"); return nil; }
    NSData *data = ReadSmall(fd, error); close(fd); if (!data) return nil;
    id json = [NSJSONSerialization JSONObjectWithData:data options:0 error:error];
    if (![json isKindOfClass:NSDictionary.class]) {
        Failure(error, 1, @"内置资源清单无效"); return nil;
    }
    _manifest = json; _manifestHash = DataHash(data);
    _documentsURL = [documentsURL copy]; _gameURL = [documentsURL URLByAppendingPathComponent:@"game" isDirectory:YES];
    if (![self validateManifest:error]) return nil;
    return self;
}
- (BOOL)validateManifest:(NSError **)error {
    NSDictionary *m = _manifest; uint64_t schema, total, count, partBytes;
    if (!Integer(m[@"schema"], &schema) || schema != 1 ||
        ![m[@"format"] isEqual:@"biko3-resource-parts-v1"] || ![m[@"game"] isEqual:@"biko3"] ||
        !Integer(m[@"total_bytes"], &total) || !Integer(m[@"file_count"], &count) ||
        !Integer(m[@"part_bytes"], &partBytes) || !partBytes || partBytes > 512ull * 1024 * 1024 ||
        ![m[@"files"] isKindOfClass:NSArray.class] || ![m[@"parts"] isKindOfClass:NSArray.class])
        return Failure(error, 1, @"内置资源清单格式不受支持");
    NSArray *files = m[@"files"], *parts = m[@"parts"];
    if (count != files.count || !count || !parts.count || parts.count > 9999)
        return Failure(error, 1, @"资源清单数量无效");
    uint64_t partTotal = 0;
    for (NSUInteger i = 0; i < parts.count; i++) {
        id part = parts[i]; uint64_t size;
        NSString *name = [NSString stringWithFormat:@"Biko3Data.part%03lu.b3data", (unsigned long)i + 1];
        if (![part isKindOfClass:NSDictionary.class] || ![part[@"name"] isEqual:name] ||
            !Integer(part[@"size"], &size) || !size || size > partBytes ||
            (i + 1 < parts.count && size != partBytes) || !HashString(part[@"sha256"]) || partTotal > LLONG_MAX - size)
            return Failure(error, 1, @"资源分卷清单无效");
        partTotal += size;
    }
    if (partTotal != total) return Failure(error, 1, @"资源总大小不一致");
    uint64_t fileTotal = 0, cursorOffset = 0; NSUInteger cursorPart = 0;
    NSString *previous = nil; BOOL marker = NO;
    NSMutableSet<NSString *> *canonicalFiles = [NSMutableSet set], *canonicalDirectories = [NSMutableSet set];
    NSMutableDictionary<NSString *, NSString *> *prefixSpellings = [NSMutableDictionary dictionary];
    for (id file in files) {
        uint64_t size;
        if (![file isKindOfClass:NSDictionary.class] || !SafePath(file[@"path"]) ||
            !HashString(file[@"sha256"]) || !Integer(file[@"size"], &size) ||
            ![file[@"segments"] isKindOfClass:NSArray.class])
            return Failure(error, 1, @"资源文件清单包含无效路径或数据");
        NSString *path = file[@"path"];
        NSString *rawPrefix = @"";
        for (NSString *component in [path componentsSeparatedByString:@"/"]) {
            rawPrefix = rawPrefix.length ? [rawPrefix stringByAppendingFormat:@"/%@", component] : component;
            NSString *key = [[rawPrefix stringByFoldingWithOptions:NSCaseInsensitiveSearch
                locale:[NSLocale localeWithLocaleIdentifier:@"en_US_POSIX"]] precomposedStringWithCanonicalMapping];
            if (prefixSpellings[key] && ![prefixSpellings[key] isEqual:rawPrefix])
                return Failure(error, 1, @"资源父目录存在大小写或 Unicode 冲突");
            prefixSpellings[key] = rawPrefix;
        }
        NSString *canonical = [[[path precomposedStringWithCanonicalMapping]
            stringByFoldingWithOptions:NSCaseInsensitiveSearch locale:[NSLocale localeWithLocaleIdentifier:@"en_US_POSIX"]]
            precomposedStringWithCanonicalMapping];
        if ([canonicalFiles containsObject:canonical] || [canonicalDirectories containsObject:canonical])
            return Failure(error, 1, @"资源路径存在大小写、Unicode 或文件夹冲突");
        NSArray<NSString *> *components = [canonical componentsSeparatedByString:@"/"];
        NSString *directory = @"";
        for (NSUInteger i = 0; i + 1 < components.count; i++) {
            directory = directory.length ? [directory stringByAppendingFormat:@"/%@", components[i]] : components[i];
            if ([canonicalFiles containsObject:directory])
                return Failure(error, 1, @"资源文件与文件夹路径冲突");
            [canonicalDirectories addObject:directory];
        }
        [canonicalFiles addObject:canonical];
        if (previous && [previous compare:path options:NSLiteralSearch] != NSOrderedAscending)
            return Failure(error, 1, @"资源文件路径重复或顺序错误");
        previous = path; if ([path isEqual:@"Data/bk3_00.pp"]) marker = YES;
        uint64_t covered = 0;
        for (id segment in file[@"segments"]) {
            uint64_t index, offset, length;
            if (![segment isKindOfClass:NSDictionary.class] || !Integer(segment[@"part"], &index) ||
                !Integer(segment[@"offset"], &offset) || !Integer(segment[@"length"], &length) || !length ||
                index != cursorPart || offset != cursorOffset || index >= parts.count)
                return Failure(error, 1, @"资源分卷范围无效");
            uint64_t limit = [parts[(NSUInteger)index][@"size"] unsignedLongLongValue];
            if (offset > limit || length > limit - offset || covered > size || length > size - covered)
                return Failure(error, 1, @"资源分卷范围超出文件");
            covered += length; cursorOffset += length;
            if (cursorOffset == limit) { cursorPart++; cursorOffset = 0; }
        }
        if (covered != size || fileTotal > LLONG_MAX - size)
            return Failure(error, 1, @"资源文件大小不一致");
        fileTotal += size;
    }
    if (!marker || fileTotal != total || cursorPart != parts.count || cursorOffset)
        return Failure(error, 1, @"资源清单未完整覆盖所有分卷");
    return YES;
}
- (NSString *)hashFD:(int)fd size:(uint64_t)size buffer:(NSMutableData *)buffer
             phase:(NSString *)phase progress:(ImportProgress)progress error:(NSError **)error {
    CC_SHA256_CTX context; CC_SHA256_Init(&context); uint64_t done = 0;
    struct stat before, after;
    if (fstat(fd, &before)) { DiskFailure(error, @"读取资源属性失败"); return nil; }
    while (done < size) {
        size_t nbytes = (size_t)MIN((uint64_t)buffer.length, size - done);
        ssize_t n = pread(fd, buffer.mutableBytes, nbytes, (off_t)done);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) { Failure(error, 3, @"资源文件尚未复制完成或读取失败"); return nil; }
        CC_SHA256_Update(&context, buffer.bytes, (CC_LONG)n); done += (uint64_t)n;
        if (progress) progress(phase, size ? (double)done / (double)size : 1);
    }
    if (fstat(fd, &after) || ![Stamp(&before) isEqual:Stamp(&after)]) {
        Failure(error, 3, @"资源文件在校验期间发生变化，请等待复制完成后重试"); return nil;
    }
    unsigned char result[CC_SHA256_DIGEST_LENGTH]; CC_SHA256_Final(result, &context);
    return Hex(result);
}
- (NSDictionary *)fileStamp:(NSDictionary *)file root:(int)game error:(NSError **)error {
    NSString *leaf = nil; BOOL missing = NO;
    int parent = ParentAt(game, file[@"path"], NO, &leaf, &missing, error);
    if (parent < 0) return nil;
    int fd = FileAt(parent, leaf, &missing, error); close(parent); if (fd < 0) return nil;
    struct stat st; BOOL ok = fstat(fd, &st) == 0; close(fd);
    return ok ? Stamp(&st) : nil;
}
- (BOOL)receiptMatches:(int)game {
    BOOL missing = NO; int fd = FileAt(game, ReceiptName, &missing, NULL);
    if (fd < 0) return NO;
    NSData *data = ReadSmall(fd, NULL); close(fd); if (!data) return NO;
    NSDictionary *receipt = [NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
    if (![receipt isKindOfClass:NSDictionary.class] || ![receipt[@"manifest_sha256"] isEqual:_manifestHash] ||
        ![receipt[@"files"] isKindOfClass:NSDictionary.class]) return NO;
    for (NSDictionary *file in _manifest[@"files"]) {
        NSDictionary *stamp = [self fileStamp:file root:game error:NULL];
        if (!stamp || ![stamp isEqual:receipt[@"files"][file[@"path"]]]) return NO;
    }
    return YES;
}
- (BOOL)writeReceipt:(int)game verified:(NSDictionary *)verified error:(NSError **)error {
    NSMutableDictionary *stamps = [NSMutableDictionary dictionary];
    for (NSDictionary *file in _manifest[@"files"]) {
        NSDictionary *stamp = [self fileStamp:file root:game error:error];
        if (!stamp || ![stamp isEqual:verified[file[@"path"]]])
            return Failure(error, 4, @"资源在校验后发生变化，请等待复制完成后重试");
        stamps[file[@"path"]] = stamp;
    }
    NSData *data = [NSJSONSerialization dataWithJSONObject:@{@"schema": @1,
        @"manifest_sha256": _manifestHash, @"files": stamps} options:NSJSONWritingSortedKeys error:error];
    if (!data) return NO;
    struct stat oldReceipt;
    if (fstatat(game, ReceiptName.fileSystemRepresentation, &oldReceipt, AT_SYMLINK_NOFOLLOW) == 0 &&
        (!S_ISREG(oldReceipt.st_mode) || oldReceipt.st_nlink != 1))
        return Failure(error, 5, @"资源校验记录不能是链接");
    NSString *temporary = [@".biko3-receipt-" stringByAppendingString:NSUUID.UUID.UUIDString];
    int fd = openat(game, temporary.fileSystemRepresentation, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0) return DiskFailure(error, @"无法创建资源校验记录");
    BOOL ok = WriteAll(fd, data.bytes, data.length, error);
    if (ok && fsync(fd)) ok = DiskFailure(error, @"无法保存资源校验记录");
    close(fd);
    if (ok && renameat(game, temporary.fileSystemRepresentation, game, ReceiptName.fileSystemRepresentation))
        ok = DiskFailure(error, @"无法更新资源校验记录");
    if (!ok) unlinkat(game, temporary.fileSystemRepresentation, 0);
    return ok;
}
- (BOOL)extractFile:(NSDictionary *)file game:(int)game parts:(NSArray<NSNumber *> *)fds
             buffer:(NSMutableData *)buffer progress:(ImportProgress)progress
              stamp:(NSDictionary **)stamp error:(NSError **)error {
    NSString *leaf = nil; int parent = ParentAt(game, file[@"path"], YES, &leaf, NULL, error);
    if (parent < 0) return NO;
    NSString *temporary = [NSString stringWithFormat:@".biko3-import-%@.partial", file[@"sha256"]];
    int output = openat(parent, temporary.fileSystemRepresentation, O_WRONLY | O_CREAT | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC, 0600);
    if (output < 0) { close(parent); return DiskFailure(error, @"无法创建资源文件"); }
    struct stat partial;
    if (fstat(output, &partial) || !S_ISREG(partial.st_mode) || partial.st_nlink != 1) {
        close(output); close(parent); return Failure(error, 5, @"临时资源文件不能是链接");
    }
    if (ftruncate(output, 0)) { close(output); close(parent); return DiskFailure(error, @"无法重建临时资源文件"); }
    BOOL ok = YES; CC_SHA256_CTX context; CC_SHA256_Init(&context);
    uint64_t size = [file[@"size"] unsignedLongLongValue], done = 0;
    for (NSDictionary *segment in file[@"segments"]) {
        int fd = fds[[segment[@"part"] unsignedIntegerValue]].intValue;
        uint64_t offset = [segment[@"offset"] unsignedLongLongValue];
        uint64_t remaining = [segment[@"length"] unsignedLongLongValue];
        while (remaining) {
            size_t amount = (size_t)MIN(remaining, (uint64_t)buffer.length);
            ssize_t n = pread(fd, buffer.mutableBytes, amount, (off_t)offset);
            if (n < 0 && errno == EINTR) continue;
            if (n <= 0) { ok = Failure(error, 3, @"读取分卷失败，请等待复制完成后重试"); break; }
            if (!WriteAll(output, buffer.bytes, (size_t)n, error)) { ok = NO; break; }
            CC_SHA256_Update(&context, buffer.bytes, (CC_LONG)n);
            offset += (uint64_t)n; remaining -= (uint64_t)n; done += (uint64_t)n;
            if (progress) progress(file[@"path"], size ? (double)done / (double)size : 1);
        }
        if (!ok) break;
    }
    unsigned char hash[CC_SHA256_DIGEST_LENGTH]; CC_SHA256_Final(hash, &context);
    if (ok && (done != size || ![Hex(hash) isEqual:file[@"sha256"]]))
        ok = Failure(error, 3, [NSString stringWithFormat:@"%@ 校验失败，请重新复制对应分卷", file[@"path"]]);
    if (ok && fsync(output)) ok = DiskFailure(error, @"保存资源文件失败");
    struct stat destination;
    if (ok && fstatat(parent, leaf.fileSystemRepresentation, &destination, AT_SYMLINK_NOFOLLOW) == 0 &&
        (!S_ISREG(destination.st_mode) || destination.st_nlink != 1))
        ok = Failure(error, 5, @"目标资源路径不能是链接或文件夹");
    if (ok && renameat(parent, temporary.fileSystemRepresentation, parent, leaf.fileSystemRepresentation))
        ok = DiskFailure(error, @"替换资源文件失败");
    if (ok) {
        struct stat installed;
        if (fstat(output, &installed)) ok = DiskFailure(error, @"无法读取新资源属性");
        else if (stamp) *stamp = Stamp(&installed);
    }
    close(output);
    if (!ok) unlinkat(parent, temporary.fileSystemRepresentation, 0);
    close(parent); return ok;
}
- (BOOL)installWithProgress:(ImportProgress)callback error:(NSError **)error {
    __block CFAbsoluteTime last = 0;
    ImportProgress progress = ^(NSString *phase, double fraction) {
        CFAbsoluteTime now = CFAbsoluteTimeGetCurrent();
        if (callback && (fraction >= 1 || now - last >= 0.1)) { last = now; callback(phase, fraction); }
    };
    int docs = -1, game = -1, lock = -1;
    NSMutableArray<NSNumber *> *partFDs = [NSMutableArray array];
    @try {
        docs = open(_documentsURL.fileSystemRepresentation, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        if (docs < 0) return DiskFailure(error, @"无法安全打开文档目录");
        lock = openat(docs, ".biko3-resource-import.lock", O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0600);
        if (lock < 0 || flock(lock, LOCK_EX | LOCK_NB)) return Failure(error, 4, @"资源正在处理中，请稍后重试");
        game = DirectoryAt(docs, @"game", YES, error); if (game < 0) return NO;
        progress(@"正在检查资源", 0);
        if ([self receiptMatches:game]) { progress(@"资源就绪", 1); return YES; }
        NSMutableData *buffer = [NSMutableData dataWithLength:ChunkSize];
        NSMutableArray<NSDictionary *> *pending = [NSMutableArray array];
        NSMutableDictionary *verified = [NSMutableDictionary dictionary];
        for (NSDictionary *file in _manifest[@"files"]) {
            @autoreleasepool {
                NSString *leaf = nil; BOOL missing = NO; NSError *readError = nil;
                int parent = ParentAt(game, file[@"path"], NO, &leaf, &missing, &readError);
                int fd = parent >= 0 ? FileAt(parent, leaf, &missing, &readError) : -1;
                if (parent >= 0) close(parent);
                if (readError) { if (error) *error = readError; return NO; }
                if (fd < 0) { [pending addObject:file]; continue; }
                struct stat st; BOOL sizeMatches = fstat(fd, &st) == 0 && (uint64_t)st.st_size == [file[@"size"] unsignedLongLongValue];
                NSString *actual = sizeMatches ? [self hashFD:fd size:(uint64_t)st.st_size buffer:buffer
                    phase:[@"正在校验 " stringByAppendingString:file[@"path"]] progress:progress error:&readError] : nil;
                NSDictionary *stamp = nil;
                if ([actual isEqual:file[@"sha256"]] && fstat(fd, &st) == 0) stamp = Stamp(&st);
                close(fd);
                if (readError) { if (error) *error = readError; return NO; }
                if (!stamp) [pending addObject:file];
                else verified[file[@"path"]] = stamp;
            }
        }
        if (!pending.count) {
            BOOL ok = [self writeReceipt:game verified:verified error:error]; if (ok) progress(@"资源就绪", 1); return ok;
        }
        NSMutableArray<NSString *> *missingNames = [NSMutableArray array];
        for (NSDictionary *part in _manifest[@"parts"]) {
            BOOL missing = NO; NSError *readError = nil;
            int fd = FileAt(docs, part[@"name"], &missing, &readError);
            if (fd < 0 && missing) {
                int packs = openat(docs, "ResourcePacks", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
                if (packs >= 0) { missing = NO; fd = FileAt(packs, part[@"name"], &missing, &readError); close(packs); }
                else if (errno != ENOENT) DiskFailure(&readError, @"无法安全打开 ResourcePacks 文件夹");
            }
            if (readError) { if (error) *error = readError; return NO; }
            if (fd < 0) { [missingNames addObject:part[@"name"]]; continue; }
            [partFDs addObject:@(fd)];
        }
        if (missingNames.count) return Failure(error, 2,
            [NSString stringWithFormat:@"请将全部分卷复制到 Biko3 文档目录或 ResourcePacks 文件夹。缺少：\n%@",
                [missingNames componentsJoinedByString:@"\n"]]);
        NSArray *parts = _manifest[@"parts"];
        for (NSUInteger i = 0; i < parts.count; i++) {
            NSDictionary *part = parts[i]; int fd = partFDs[i].intValue;
            struct stat st;
            if (fstat(fd, &st) || (uint64_t)st.st_size != [part[@"size"] unsignedLongLongValue])
                return Failure(error, 3, [NSString stringWithFormat:@"%@ 大小不符，请等待复制完成或重新复制", part[@"name"]]);
            NSString *phase = [NSString stringWithFormat:@"正在校验分卷 %lu/%lu", (unsigned long)i + 1, (unsigned long)parts.count];
            NSString *actual = [self hashFD:fd size:(uint64_t)st.st_size buffer:buffer phase:phase progress:progress error:error];
            if (!actual) return NO;
            if (![actual isEqual:part[@"sha256"]]) return Failure(error, 3,
                [NSString stringWithFormat:@"%@ 校验失败，请重新复制此分卷", part[@"name"]]);
        }
        for (NSDictionary *file in pending) {
            ImportProgress fileProgress = ^(NSString *name, double fraction) {
                progress([@"正在解包 " stringByAppendingString:name], fraction);
            };
            NSDictionary *stamp = nil;
            if (![self extractFile:file game:game parts:partFDs buffer:buffer progress:fileProgress stamp:&stamp error:error]) return NO;
            verified[file[@"path"]] = stamp;
        }
        BOOL ok = [self writeReceipt:game verified:verified error:error]; if (ok) progress(@"资源就绪", 1); return ok;
    } @finally {
        for (NSNumber *number in partFDs) close(number.intValue);
        if (game >= 0) close(game);
        if (lock >= 0) close(lock);
        if (docs >= 0) close(docs);
    }
}
@end
