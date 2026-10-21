// Exercise callback timeouts without loading MediaRemote or querying playback.
#define NOWPLAYING_TEST
#include "../../dolby_tool/capture/nowplaying.m"

int main(void) {
    @autoreleasepool {
        dispatch_queue_t queue = dispatch_get_global_queue(0, 0);
        dispatch_semaphore_t late = dispatch_semaphore_create(0);
        NSDictionary *timed_out = request_result(5 * NSEC_PER_MSEC, ^(ResultCompletion complete) {
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 30 * NSEC_PER_MSEC), queue, ^{
                complete(@{@"late": @YES});
                dispatch_semaphore_signal(late);
            });
        });
        if (timed_out != nil) return 1;
        // The previous late signal must not complete this independent request.
        NSDictionary *next = request_result(NSEC_PER_SEC, ^(ResultCompletion complete) {
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 60 * NSEC_PER_MSEC), queue, ^{
                complete(@{@"Title": @"expected"});
            });
        });
        if (![next[@"Title"] isEqual:@"expected"] || next[@"late"]) return 2;
        if (dispatch_semaphore_wait(late, dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC))) return 3;
        // Publication copies the result, and duplicate completions cannot replace it.
        NSDictionary *snapshot = request_result(NSEC_PER_SEC, ^(ResultCompletion complete) {
            NSMutableDictionary *values = [@{@"Title": @"original"} mutableCopy];
            complete(values);
            values[@"Title"] = @"changed";
            complete(@{@"Title": @"duplicate"});
        });
        if (![snapshot[@"Title"] isEqual:@"original"]) return 4;
        if (![NSJSONSerialization dataWithJSONObject:snapshot options:0 error:nil]) return 5;
    }
    return 0;
}
