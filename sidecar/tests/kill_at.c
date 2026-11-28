/* Kills this process (SIGKILL) at one exact step of a delta-rs write, for test_delta_step_kills.py.

delta-rs writes each file under a staging name (<name>#<n>) and then puts it in place: rename(2) for
data files, checkpoints and _last_checkpoint, linkat(2) for a commit (so it can't replace another
writer's). Loaded with LD_PRELOAD, this wraps both calls. KF_KILL_AT is "<call> <when> <text> <nth>":
the nth (from 1) rename or linkat whose target path contains text, and before or after it runs. Every
other call goes straight through. Linux only (glibc's RTLD_NEXT). */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static char call[16], when[16], text[256];
static int nth = -1, seen;

static int matches(const char *name, const char *target) {
    if (nth < 0) {
        const char *spec = getenv("KF_KILL_AT");
        nth = 0;
        if (spec && sscanf(spec, "%15s %15s %255s %d", call, when, text, &nth) != 4) nth = 0;
    }
    return nth > 0 && strcmp(name, call) == 0 && strstr(target, text) != NULL && ++seen == nth;
}

static void die(void) { kill(getpid(), SIGKILL); for (;;) pause(); }

#define WRAPPED(name, target, invoke)                                    \
    int hit = matches(name, target);                                     \
    if (hit && strcmp(when, "before") == 0) die();                       \
    int result = invoke;                                                 \
    if (hit) die();                                                      \
    return result;

int rename(const char *old, const char *new) {
    static int (*next)(const char *, const char *);
    if (!next) next = (int (*)(const char *, const char *))dlsym(RTLD_NEXT, "rename");
    WRAPPED("rename", new, next(old, new))
}

int linkat(int olddir, const char *old, int newdir, const char *new, int flags) {
    static int (*next)(int, const char *, int, const char *, int);
    if (!next) next = (int (*)(int, const char *, int, const char *, int))dlsym(RTLD_NEXT, "linkat");
    WRAPPED("linkat", new, next(olddir, old, newdir, new, flags))
}
