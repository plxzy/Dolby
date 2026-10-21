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

static void wait_for(dispatch_semaphore_t done) {
    dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, kTimeout));
}

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
            dispatch_semaphore_t done = dispatch_semaphore_create(0);
            request_queue(request, nil, nil, queue, ^(id playback_queue, __unused NSError *error) {
                id item = [kvc(playback_queue, @"contentItems") firstObject];
                id md = kvc(item, @"metadata");
                if (md) {
                    put(out, @"Title", kvc(md, @"title"));
                    put(out, @"Album", kvc(md, @"albumName"));
                    put(out, @"Artist", kvc(md, @"trackArtistName"));
                    put(out, @"Duration", kvc(md, @"duration"));
                    put(out, @"PlaybackRate", kvc(md, @"playbackRate"));
                    put(out, @"MediaType", kvc(md, @"mediaType"));
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
                        out[@"ElapsedTime"] = @(value);
                    }
                }
                dispatch_semaphore_signal(done);
            });
            wait_for(done);
        }

        if (out.count) {
            GetPidFn get_pid = (GetPidFn)dlsym(mr, "MRMediaRemoteGetNowPlayingApplicationPID");
            GetIsPlayingFn get_playing = (GetIsPlayingFn)dlsym(mr, "MRMediaRemoteGetNowPlayingApplicationIsPlaying");
            GetClientFn get_client = (GetClientFn)dlsym(mr, "MRMediaRemoteGetNowPlayingClient");
            dispatch_semaphore_t done = dispatch_semaphore_create(0);
            if (get_pid) {
                get_pid(queue, ^(int pid) { if (pid > 0) out[@"ApplicationPID"] = @(pid); dispatch_semaphore_signal(done); });
                wait_for(done);
            }
            if (get_playing) {
                get_playing(queue, ^(BOOL playing) { out[@"IsPlaying"] = @(playing); dispatch_semaphore_signal(done); });
                wait_for(done);
            }
            if (get_client) {
                get_client(queue, ^(id client) { put(out, @"BundleIdentifier", kvc(client, @"bundleIdentifier")); dispatch_semaphore_signal(done); });
                wait_for(done);
            }
        }

        NSData *json = [NSJSONSerialization dataWithJSONObject:out options:0 error:nil];
        if (json) fwrite(json.bytes, 1, json.length, stdout);
        fputc('\n', stdout);
        fflush(stdout);
    }
}
