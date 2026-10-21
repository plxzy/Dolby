// Prints the system "Now Playing" item as one JSON object on stdout.
//
// MediaRemote only answers Apple-signed clients since macOS 15.4, so this is built
// as a dylib and loaded into /usr/bin/perl (an Apple platform binary that may load
// unsigned extension modules):
//
//     clang -dynamiclib -fobjc-arc -framework Foundation nowplaying.m -o nowplaying.dylib
//     /usr/bin/perl -e 'use DynaLoader; DynaLoader::dl_load_file("nowplaying.dylib")'
//
// Prints {} when nothing is playing. Needs no admin rights and no TCC permission.
#import <Foundation/Foundation.h>
#import <dlfcn.h>

typedef void (*GetInfoFn)(dispatch_queue_t, void (^)(NSDictionary *));
typedef void (*GetPidFn)(dispatch_queue_t, void (^)(int));

static id json_value(id value) {
    if ([value isKindOfClass:[NSString class]] || [value isKindOfClass:[NSNumber class]]) return value;
    if ([value isKindOfClass:[NSDate class]]) return @([(NSDate *)value timeIntervalSince1970]);
    return nil;  // artwork data, opaque objects
}

__attribute__((constructor)) static void print_now_playing(void) {
    @autoreleasepool {
        void *mr = dlopen("/System/Library/PrivateFrameworks/MediaRemote.framework/MediaRemote", RTLD_NOW);
        GetInfoFn get_info = mr ? (GetInfoFn)dlsym(mr, "MRMediaRemoteGetNowPlayingInfo") : NULL;
        GetPidFn get_pid = mr ? (GetPidFn)dlsym(mr, "MRMediaRemoteGetNowPlayingApplicationPID") : NULL;
        NSMutableDictionary *out = [NSMutableDictionary dictionary];
        if (get_info) {
            dispatch_semaphore_t done = dispatch_semaphore_create(0);
            get_info(dispatch_get_global_queue(0, 0), ^(NSDictionary *info) {
                for (NSString *key in info) {
                    id value = json_value(info[key]);
                    NSString *name = [key stringByReplacingOccurrencesOfString:@"kMRMediaRemoteNowPlayingInfo" withString:@""];
                    if (value) out[name] = value;
                }
                dispatch_semaphore_signal(done);
            });
            dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 2 * NSEC_PER_SEC));
        }
        if (get_pid && out.count) {
            dispatch_semaphore_t done = dispatch_semaphore_create(0);
            get_pid(dispatch_get_global_queue(0, 0), ^(int pid) {
                if (pid > 0) out[@"ApplicationPID"] = @(pid);
                dispatch_semaphore_signal(done);
            });
            dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 2 * NSEC_PER_SEC));
        }
        NSData *json = [NSJSONSerialization dataWithJSONObject:out options:0 error:nil];
        if (json) fwrite(json.bytes, 1, json.length, stdout);
        fputc('\n', stdout);
        fflush(stdout);
    }
}
