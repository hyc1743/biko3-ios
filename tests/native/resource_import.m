#import <Foundation/Foundation.h>
#import "Biko3ResourceInstaller.h"
#include <sys/stat.h>
#include <stdio.h>
#include <stdlib.h>

static NSFileManager *fm;
static NSURL *manifestURL, *partsURL, *sourceURL, *workURL;
static NSDictionary *manifest;
static unsigned checks;

static void Require(BOOL ok, NSString *message) {
    if (!ok) { fprintf(stderr, "RESOURCE_IMPORT_FAIL %s\n", message.UTF8String); exit(1); }
}
static NSURL *Child(NSURL *root, NSString *path) { return [root URLByAppendingPathComponent:path]; }
static void Mkdir(NSURL *url) {
    NSError *error = nil;
    Require([fm createDirectoryAtURL:url withIntermediateDirectories:YES attributes:nil error:&error], error.description ?: @"mkdir");
}
static NSURL *Docs(NSString *name) { NSURL *url = Child(workURL, name); Mkdir(url); return url; }
static void CopyParts(NSURL *docs, BOOL split, NSInteger omit) {
    Mkdir(Child(docs, @"ResourcePacks")); NSUInteger i = 0;
    for (NSDictionary *part in manifest[@"parts"]) {
        if ((NSInteger)i != omit) {
            NSURL *root = split && (i & 1) ? Child(docs, @"ResourcePacks") : docs;
            NSError *error = nil;
            Require([fm copyItemAtURL:Child(partsURL, part[@"name"]) toURL:Child(root, part[@"name"]) error:&error], error.description ?: @"copy part");
        }
        i++;
    }
}
static BOOL Install(NSURL *docs, NSURL *custom, NSError **error) {
    Biko3ResourceInstaller *installer = [[Biko3ResourceInstaller alloc]
        initWithManifestURL:custom ?: manifestURL documentsURL:docs error:error];
    return installer && [installer installWithProgress:^(NSString *phase, double fraction) {
        Require(phase.length && fraction >= 0 && fraction <= 1, @"progress values");
    } error:error];
}
static void VerifyFiles(NSURL *docs) {
    for (NSDictionary *file in manifest[@"files"]) {
        NSURL *actual = Child(Child(docs, @"game"), file[@"path"]);
        NSURL *expected = Child(sourceURL, [file[@"path"] substringFromIndex:5]);
        Require([[NSData dataWithContentsOfURL:actual] isEqual:[NSData dataWithContentsOfURL:expected]], file[@"path"]);
    }
}
static struct stat FileStat(NSURL *url) {
    struct stat result; Require(lstat(url.fileSystemRepresentation, &result) == 0, @"stat installed file"); return result;
}
static void Pass(const char *name) { checks++; printf("RESOURCE_IMPORT_PASS %s\n", name); }
static NSURL *ManifestCopy(NSString *name, void (^change)(NSMutableDictionary *)) {
    NSMutableDictionary *copy = [NSJSONSerialization JSONObjectWithData:
        [NSData dataWithContentsOfURL:manifestURL] options:NSJSONReadingMutableContainers error:NULL];
    change(copy); NSURL *url = Child(workURL, name);
    Require([[NSJSONSerialization dataWithJSONObject:copy options:0 error:NULL] writeToURL:url atomically:YES], @"write fixture manifest");
    return url;
}

int main(int argc, char **argv) {
    @autoreleasepool {
        Require(argc == 5, @"expected manifest, parts, source Data, workspace");
        fm = NSFileManager.defaultManager;
        manifestURL = [NSURL fileURLWithPath:@(argv[1])]; partsURL = [NSURL fileURLWithPath:@(argv[2])];
        sourceURL = [NSURL fileURLWithPath:@(argv[3])]; workURL = [NSURL fileURLWithPath:@(argv[4])];
        manifest = [NSJSONSerialization JSONObjectWithData:[NSData dataWithContentsOfURL:manifestURL] options:0 error:NULL];
        Require(manifest != nil, @"fixture manifest"); Mkdir(workURL);
        NSError *error = nil;

        NSURL *docs = Docs(@"cross-part"); CopyParts(docs, YES, -1);
        Require(Install(docs, nil, &error), error.description); VerifyFiles(docs);
        Require([fm fileExistsAtPath:Child(docs, @"game/.biko3-resources-v1.json").path], @"receipt exists");
        Pass("cross_part_reassembly_and_hashes");

        NSURL *stable = Child(docs, @"game/Data/nested/other.bin"); struct stat oldStable = FileStat(stable);
        for (NSDictionary *part in manifest[@"parts"]) {
            [fm removeItemAtURL:Child(docs, part[@"name"]) error:NULL];
            [fm removeItemAtURL:Child(Child(docs, @"ResourcePacks"), part[@"name"]) error:NULL];
        }
        error = nil; Require(Install(docs, nil, &error), error.description);
        struct stat fastStable = FileStat(stable);
        Require(oldStable.st_ino == fastStable.st_ino && oldStable.st_mtimespec.tv_nsec == fastStable.st_mtimespec.tv_nsec, @"receipt preserves files");
        Pass("receipt_without_parts");

        NSURL *damaged = Child(docs, @"game/Data/bk3_00.pp");
        NSDictionary *attributes = [fm attributesOfItemAtPath:damaged.path error:NULL];
        NSMutableData *bad = [[NSData dataWithContentsOfURL:damaged] mutableCopy];
        ((uint8_t *)bad.mutableBytes)[0] ^= 0xff;
        Require([bad writeToURL:damaged atomically:NO], @"same-size corruption");
        [fm setAttributes:@{NSFileModificationDate: attributes[NSFileModificationDate]} ofItemAtPath:damaged.path error:NULL];
        error = nil; Require(!Install(docs, nil, &error) && error != nil, @"same-size corruption invalidates receipt");
        CopyParts(docs, YES, -1); error = nil;
        Require(Install(docs, nil, &error), error.description); VerifyFiles(docs);
        struct stat resumedStable = FileStat(stable);
        Require(oldStable.st_ino == resumedStable.st_ino && oldStable.st_mtimespec.tv_sec == resumedStable.st_mtimespec.tv_sec &&
                oldStable.st_mtimespec.tv_nsec == resumedStable.st_mtimespec.tv_nsec, @"completed file preserved on retry");
        Pass("same_size_corruption_and_completed_file_retry");

        NSURL *manual = Docs(@"manual"); Mkdir(Child(manual, @"game"));
        Require([fm copyItemAtURL:sourceURL toURL:Child(manual, @"game/Data") error:&error], error.description);
        error = nil; Require(Install(manual, nil, &error), error.description); VerifyFiles(manual);
        Pass("existing_manual_data_without_parts");

        NSURL *missing = Docs(@"missing-part"); CopyParts(missing, NO, (NSInteger)[manifest[@"parts"] count] - 1);
        error = nil; Require(!Install(missing, nil, &error) && error.code == 2, @"missing part rejected");
        Require(![fm fileExistsAtPath:Child(missing, @"game/Data/bk3_00.pp").path], @"missing parts do not install final files");
        Pass("missing_part_is_retryable");

        NSURL *corrupt = Docs(@"corrupt-part"); CopyParts(corrupt, NO, -1);
        NSString *firstPart = manifest[@"parts"][0][@"name"];
        NSMutableData *partData = [[NSData dataWithContentsOfURL:Child(corrupt, firstPart)] mutableCopy];
        ((uint8_t *)partData.mutableBytes)[0] ^= 1;
        Require([partData writeToURL:Child(corrupt, firstPart) atomically:NO], @"corrupt part");
        error = nil; Require(!Install(corrupt, nil, &error) && error.code == 3, @"part SHA rejected");
        Require(![fm fileExistsAtPath:Child(corrupt, @"game/Data/bk3_00.pp").path], @"bad part never publishes file");
        Pass("part_sha256_failure");

        NSURL *badManifest = ManifestCopy(@"traversal.json", ^(NSMutableDictionary *m) { m[@"files"][0][@"path"] = @"Data/../outside"; });
        error = nil; Require(!Install(Docs(@"traversal"), badManifest, &error) && error.code == 1, @"traversal rejected at manifest validation");
        Pass("manifest_traversal_rejected");

        NSURL *outside = Child(workURL, @"outside"); Mkdir(outside);
        NSData *sentinel = [@"must remain unchanged" dataUsingEncoding:NSUTF8StringEncoding];
        Require([sentinel writeToURL:Child(outside, @"bk3_00.pp") atomically:YES], @"outside fixture");
        NSURL *linked = Docs(@"linked-directory"); Mkdir(Child(linked, @"game")); CopyParts(linked, NO, -1);
        Require([fm createSymbolicLinkAtPath:Child(linked, @"game/Data").path withDestinationPath:outside.path error:&error], @"symlink directory fixture");
        error = nil; Require(!Install(linked, nil, &error), @"symlink directory rejected");
        Require([[NSData dataWithContentsOfURL:Child(outside, @"bk3_00.pp")] isEqual:sentinel], @"outside file unchanged");
        Pass("symlink_destination_rejected");

        NSURL *linkedPart = Docs(@"linked-part");
        for (NSDictionary *part in manifest[@"parts"])
            Require([fm createSymbolicLinkAtPath:Child(linkedPart, part[@"name"]).path withDestinationPath:Child(partsURL, part[@"name"]).path error:NULL], @"symlink part fixture");
        error = nil; Require(!Install(linkedPart, nil, &error), @"symlink source rejected");
        Pass("symlink_part_rejected");

        NSURL *interrupted = Docs(@"interrupted-partial"); CopyParts(interrupted, NO, -1); Mkdir(Child(interrupted, @"game/Data"));
        NSString *partial = [NSString stringWithFormat:@"game/Data/.biko3-import-%@.partial", manifest[@"files"][0][@"sha256"]];
        Require([sentinel writeToURL:Child(interrupted, partial) atomically:YES], @"interrupted file fixture");
        error = nil; Require(Install(interrupted, nil, &error), error.description); VerifyFiles(interrupted);
        Require(![fm fileExistsAtPath:Child(interrupted, partial).path], @"partial reclaimed by retry");
        Pass("interrupted_partial_is_reused_safely");

        NSURL *wrongFileHash = ManifestCopy(@"wrong-file-hash.json", ^(NSMutableDictionary *m) {
            m[@"files"][0][@"sha256"] = [@"" stringByPaddingToLength:64 withString:@"0" startingAtIndex:0];
        });
        NSURL *atomic = Docs(@"file-hash-failure"); CopyParts(atomic, NO, -1);
        error = nil; Require(!Install(atomic, wrongFileHash, &error) && error.code == 3, @"file SHA rejected after extraction");
        Require(![fm fileExistsAtPath:Child(atomic, @"game/Data/bk3_00.pp").path], @"failed file remains unpublished");
        NSArray *left = [fm contentsOfDirectoryAtPath:Child(atomic, @"game/Data").path error:NULL];
        for (NSString *name in left) Require(![name hasPrefix:@".biko3-import-"], @"failed extraction temporary removed");
        Pass("file_sha_failure_keeps_atomic_output");

        printf("RESOURCE_IMPORT_ALL_PASSED checks=%u\n", checks);
    }
    return 0;
}
