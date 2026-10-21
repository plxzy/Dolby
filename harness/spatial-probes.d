#!/usr/sbin/dtrace -s
/*
 * spatial-probes.d — capture the AVF spatialization gate observed
 * against TV.app (see the dtrace evidence in reports/01_gate_model.md), but against an
 * arbitrary AVPlayer-using process (e.g. SpatialProbe).
 *
 * Usage:
 *   sudo dtrace -p <pid> -s spatial-probes.d
 *
 * Requires SIP debug+dtrace restrictions disabled.
 */

#pragma D option quiet
#pragma D option flowindent=0

dtrace:::BEGIN
{
    printf("=== spatial-probes start pid=%d ===\n", $target);
}

/* Player-side allow-mask setters. Wildcard module match because the
 * Swift AVFoundation path goes through AVFCore rather than the CF
 * bridge, and we want to catch whichever framework actually sets it. */
pid$target::*AllowedAudioSpatializationFormats*:entry
{
    printf("[setter] %s`%s arg0=%p arg1=0x%x (=%d)\n",
        probemod, probefunc, arg0, arg1, arg1);
}

pid$target::*allowedAudioSpatializationFormats*:entry
{
    printf("[setter-objc] %s`%s arg0=%p arg1=0x%x arg2=0x%x\n",
        probemod, probefunc, arg0, arg1, arg2);
}

/* FigItem-derived update path. */
pid$target::*updateAllowedAudioSpatialization*:entry
{
    printf("[figitem] %s`%s (FigItem path engaged)\n",
        probemod, probefunc);
}

/* Format-side eligibility check. */
pid$target:MediaToolbox:FPSupport_GetAudioFormatDescriptionSpatializationEligibility:entry
{
    self->fpEnter = 1;
    printf("FPSupport_Eligibility ENTER arg0=%p arg1=%p arg2=%p\n", arg0, arg1, arg2);
}

pid$target:MediaToolbox:FPSupport_GetAudioFormatDescriptionSpatializationEligibility:return
/self->fpEnter/
{
    printf("FPSupport_Eligibility RETURN -> 0x%x (=%d)\n", arg1, arg1);
    self->fpEnter = 0;
}

/* The intersection gate site. Capture both entry and return so we see
 * the verdict. */
pid$target:AudioToolbox:*CheckSpatialization*:entry
{
    self->csEnter = 1;
    printf("AudioQueueObject::CheckSpatialization ENTER arg0=%p arg1=%p\n", arg0, arg1);
}

pid$target:AudioToolbox:*CheckSpatialization*:return
/self->csEnter/
{
    printf("AudioQueueObject::CheckSpatialization RETURN -> 0x%x\n", arg1);
    self->csEnter = 0;
}

pid$target:AudioToolbox:*AllowsSpatialization*:entry
{
    self->asEnter = 1;
    printf("AudioQueueObject::AllowsSpatialization ENTER arg0=%p\n", arg0);
}

pid$target:AudioToolbox:*AllowsSpatialization*:return
/self->asEnter/
{
    printf("AudioQueueObject::AllowsSpatialization RETURN -> 0x%x (=%d)\n", arg1, arg1);
    self->asEnter = 0;
}

pid$target:AudioToolbox:*ShouldRouteBypassSpatialization*:return
{
    printf("ShouldRouteBypassSpatialization -> %d\n", arg1);
}

pid$target:AudioToolbox:*EnableInternalSpatializationAUs*:entry
{
    printf("SpatializationManager::EnableInternalSpatializationAUs\n");
}

/* Auto-stop after 30s. */
tick-30s
{
    printf("=== TIMER EXIT ===\n");
    exit(0);
}
