// Prints the system "Now Playing" item as one JSON object on stdout.
//
// MediaRemote only answers Apple-signed clients since macOS 15.4, so this is built
// as a dylib and loaded into /usr/bin/perl (an Apple platform binary that may load
// unsigned extension modules):
//
//     clang -dynamiclib -fobjc-arc -framework Foundation nowplaying.m -o nowplaying.dylib
//     /usr/bin/perl -e 'use DynaLoader; DynaLoader::dl_load_file("nowplaying.dylib")'
//
// The item comes from the playback-queue request: MRMediaRemoteGetNowPlayingInfo
// returns an empty dictionary for TV.app after a while even though TV stays the
// Now Playing client. Prints {} when nothing is playing. Needs no admin rights and
// no TCC permission.
#import <Foundation/Foundation.h>
#import <dlfcn.h>

// (request, origin, client, queue, completion): origin/client nil = this Mac's Now Playing player.
typedef void (*RequestQueueFn)(id, id, id, dispatch_queue_t, void (^)(id, NSError *));
typedef void (*GetPidFn)(dispatch_queue_t, void (^)(int));
typedef void (*GetIsPlayingFn)(dispatch_queue_t, void (^)(BOOL));
typedef void (*GetClientFn)(dispatch_queue_t, void (^)(id));

static const int64_t kTimeout = 2 * NSEC_PER_SEC;

static id kvc(id object, NSString *key) {
    @try { return [object valueForKey:key]; } @catch (__unused id e) { return nil; }
}

static void put(NSMutableDictionary *out, NSString *key, id value) {
    if ([value isKindOfClass:[NSString class]] || [value isKindOfClass:[NSNumber class]]) out[key] = value;
}

typedef void (^ResultCompletion)(NSDictionary *);

static NSDictionary *request_result(int64_t timeout, void (^start)(ResultCompletion)) {
    NSObject *lock = [NSObject new];
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    __block BOOL finished = NO;
    __block NSDictionary *result = nil;
    start(^(NSDictionary *value) {
        @synchronized (lock) {
            if (finished) return;
            result = [value copy];
            finished = YES;
            dispatch_semaphore_signal(done);
        }
    });
    long status = dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, timeout));
    @synchronized (lock) {
        finished = YES;  // A timeout closes this request; late callbacks cannot publish.
        return status == 0 ? result : nil;
    }
}

#ifndef NOWPLAYING_TEST
__attribute__((constructor)) static void print_now_playing(void) {
    @autoreleasepool {
        void *mr = dlopen("/System/Library/PrivateFrameworks/MediaRemote.framework/MediaRemote", RTLD_NOW);
        NSMutableDictionary *out = [NSMutableDictionary dictionary];
        dispatch_queue_t queue = dispatch_get_global_queue(0, 0);

        RequestQueueFn request_queue = mr ? (RequestQueueFn)dlsym(mr, "MRMediaRemoteRequestNowPlayingPlaybackQueue") : NULL;
        Class request_class = NSClassFromString(@"MRPlaybackQueueRequest");
        if (request_queue && request_class) {
            id request = [[request_class alloc] init];
            @try {
                [request setValue:@YES forKey:@"includeMetadata"];
                [request setValue:@0 forKey:@"location"];
                [request setValue:@1 forKey:@"length"];
            } @catch (__unused id e) {}
            NSDictionary *metadata = request_result(kTimeout, ^(ResultCompletion complete) {
              request_queue(request, nil, nil, queue, ^(id playback_queue, __unused NSError *error) {
                NSMutableDictionary *values = [NSMutableDictionary dictionary];
                id item = [kvc(playback_queue, @"contentItems") firstObject];
                id md = kvc(item, @"metadata");
                if (md) {
                    put(values, @"Title", kvc(md, @"title"));
                    put(values, @"Album", kvc(md, @"albumName"));
                    put(values, @"Artist", kvc(md, @"trackArtistName"));
                    put(values, @"Duration", kvc(md, @"duration"));
                    put(values, @"PlaybackRate", kvc(md, @"playbackRate"));
                    put(values, @"MediaType", kvc(md, @"mediaType"));
                    // elapsedTime is a snapshot taken at elapsedTimeTimestamp; project it to now.
                    NSNumber *elapsed = kvc(md, @"elapsedTime");
                    // A CFAbsoluteTime (seconds since 2001) as NSNumber; tolerate an NSDate too.
                    id stamp = kvc(md, @"elapsedTimeTimestamp");
                    NSNumber *rate = kvc(md, @"playbackRate");
                    if ([elapsed isKindOfClass:[NSNumber class]]) {
                        double value = elapsed.doubleValue;
                        double taken = [stamp isKindOfClass:[NSDate class]] ? [(NSDate *)stamp timeIntervalSinceReferenceDate]
                                     : [stamp isKindOfClass:[NSNumber class]] ? [(NSNumber *)stamp doubleValue] : 0;
                        if (taken > 0 && [rate isKindOfClass:[NSNumber class]])
                            value += (CFAbsoluteTimeGetCurrent() - taken) * rate.doubleValue;
                        values[@"ElapsedTime"] = @(value);
                    }
                }
                complete(values);
              });
            });
            if (metadata) [out addEntriesFromDictionary:metadata];
        }

        if (out.count) {
            GetPidFn get_pid = (GetPidFn)dlsym(mr, "MRMediaRemoteGetNowPlayingApplicationPID");
            GetIsPlayingFn get_playing = (GetIsPlayingFn)dlsym(mr, "MRMediaRemoteGetNowPlayingApplicationIsPlaying");
            GetClientFn get_client = (GetClientFn)dlsym(mr, "MRMediaRemoteGetNowPlayingClient");
            if (get_pid) {
                NSDictionary *value = request_result(kTimeout, ^(ResultCompletion complete) {
                    get_pid(queue, ^(int pid) { complete(pid > 0 ? @{@"ApplicationPID": @(pid)} : @{}); });
                });
                if (value) [out addEntriesFromDictionary:value];
            }
            if (get_playing) {
                NSDictionary *value = request_result(kTimeout, ^(ResultCompletion complete) {
                    get_playing(queue, ^(BOOL playing) { complete(@{@"IsPlaying": @(playing)}); });
                });
                if (value) [out addEntriesFromDictionary:value];
            }
            if (get_client) {
                NSDictionary *value = request_result(kTimeout, ^(ResultCompletion complete) {
                    get_client(queue, ^(id client) {
                        NSMutableDictionary *values = [NSMutableDictionary dictionary];
                        put(values, @"BundleIdentifier", kvc(client, @"bundleIdentifier"));
                        complete(values);
                    });
                });
                if (value) [out addEntriesFromDictionary:value];
            }
        }

        NSData *json = [NSJSONSerialization dataWithJSONObject:[out copy] options:0 error:nil];
        if (json) fwrite(json.bytes, 1, json.length, stdout);
        fputc('\n', stdout);
        fflush(stdout);
    }
}
#endif
