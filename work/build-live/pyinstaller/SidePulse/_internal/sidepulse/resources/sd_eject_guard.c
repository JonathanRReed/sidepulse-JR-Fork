// SidePulse Pro Eject Prevention - veto software ejects of cards in the built-in SD reader.
//
// macOS loginwindow dissents the mount and ejects any disk that appears
// while the screen is locked (e.g. the SD slot re-enumerating after a
// hibernate wake). The eject goes through the same DiskArbitration approval
// mechanism, so a registered client can dissent it right back. This tool does
// that, then retries the mount every few seconds; the retries are dissented
// while locked and succeed after unlock.
//
// Build: clang -o "SidePulse Pro Eject Prevention" sd_eject_guard.c \
//          -framework DiskArbitration -framework CoreFoundation
// Run:   ./sd_eject_guard [-n]   (leave running; Ctrl-C to stop)
//        -n / --no-mount: only veto ejects; don't retry mounting.

#include <CoreFoundation/CoreFoundation.h>
#include <DiskArbitration/DiskArbitration.h>
#include <stdio.h>
#include <string.h>

#define MOUNT_RETRY_SECONDS 5.0

static DASessionRef g_session;
static bool g_no_mount = false;

static void log_msg(const char *fmt, CFStringRef s) {
    char buf[256] = "?";
    if (s) CFStringGetCString(s, buf, sizeof(buf), kCFStringEncodingUTF8);
    printf(fmt, buf);
    fflush(stdout);
}

static bool is_builtin_sd(DADiskRef disk) {
    CFDictionaryRef desc = DADiskCopyDescription(disk);
    if (!desc) return false;
    CFStringRef proto = CFDictionaryGetValue(desc, kDADiskDescriptionDeviceProtocolKey);
    CFStringRef model = CFDictionaryGetValue(desc, kDADiskDescriptionDeviceModelKey);
    bool match =
        (proto && CFStringFind(proto, CFSTR("Secure Digital"), 0).location != kCFNotFound) ||
        (model && CFStringFind(model, CFSTR("SDXC"), 0).location != kCFNotFound);
    CFRelease(desc);
    return match;
}

static bool is_mounted(DADiskRef disk) {
    CFDictionaryRef desc = DADiskCopyDescription(disk);
    if (!desc) return false;
    bool mounted = CFDictionaryGetValue(desc, kDADiskDescriptionVolumePathKey) != NULL;
    CFRelease(desc);
    return mounted;
}

static void mount_done(DADiskRef disk, DADissenterRef dissenter, void *ctx);

static void retry_mount(CFRunLoopTimerRef timer, void *info) {
    DADiskRef disk = (DADiskRef)info;
    if (is_mounted(disk)) {
        printf("mounted; stopping retries for %s\n", DADiskGetBSDName(disk));
        fflush(stdout);
        CFRunLoopTimerInvalidate(timer);
        CFRelease(disk);
        return;
    }
    DADiskMount(disk, NULL, kDADiskMountOptionDefault, mount_done, NULL);
}

static void mount_done(DADiskRef disk, DADissenterRef dissenter, void *ctx) {
    if (dissenter)
        printf("mount of %s dissented (0x%x), will retry\n",
               DADiskGetBSDName(disk), DADissenterGetStatus(dissenter));
    else
        printf("mount of %s succeeded\n", DADiskGetBSDName(disk));
    fflush(stdout);
}

static void start_mount_retries(DADiskRef disk) {
    CFRetain(disk);
    CFRunLoopTimerContext tctx = { 0, (void *)disk, NULL, NULL, NULL };
    CFRunLoopTimerRef timer = CFRunLoopTimerCreate(
        kCFAllocatorDefault, CFAbsoluteTimeGetCurrent() + MOUNT_RETRY_SECONDS,
        MOUNT_RETRY_SECONDS, 0, 0, retry_mount, &tctx);
    CFRunLoopAddTimer(CFRunLoopGetCurrent(), timer, kCFRunLoopDefaultMode);
    CFRelease(timer);
}

static DADissenterRef eject_approval(DADiskRef disk, void *ctx) {
    if (!is_builtin_sd(disk))
        return NULL;  // not ours, allow
    CFDictionaryRef desc = DADiskCopyDescription(disk);
    CFStringRef vol = desc ? CFDictionaryGetValue(desc, kDADiskDescriptionVolumeNameKey) : NULL;
    printf("vetoing eject of %s", DADiskGetBSDName(disk));
    log_msg(" (volume: %s)\n", vol);
    if (desc) CFRelease(desc);
    if (!g_no_mount)
        start_mount_retries(disk);
    return DADissenterCreate(kCFAllocatorDefault, kDAReturnNotPermitted,
                             CFSTR("SidePulse Pro Eject Prevention: keeping SD card attached"));
}

static void disk_appeared(DADiskRef disk, void *ctx) {
    if (!is_builtin_sd(disk)) return;
    printf("SD disk appeared: %s\n", DADiskGetBSDName(disk));
    fflush(stdout);
}

int main(int argc, char **argv) {
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-n") || !strcmp(argv[i], "--no-mount")) {
            g_no_mount = true;
        } else {
            fprintf(stderr, "usage: %s [-n | --no-mount]\n", argv[0]);
            return 2;
        }
    }
    g_session = DASessionCreate(kCFAllocatorDefault);
    if (!g_session) {
        fprintf(stderr, "DASessionCreate failed\n");
        return 1;
    }
    DARegisterDiskEjectApprovalCallback(g_session, NULL, eject_approval, NULL);
    DARegisterDiskAppearedCallback(g_session, NULL, disk_appeared, NULL);
    DASessionScheduleWithRunLoop(g_session, CFRunLoopGetCurrent(), kCFRunLoopDefaultMode);
    printf("SidePulse Pro Eject Prevention: watching for ejects of the built-in SD reader%s...\n",
           g_no_mount ? " (mount retries disabled)" : "");
    fflush(stdout);
    CFRunLoopRun();
    return 0;
}
