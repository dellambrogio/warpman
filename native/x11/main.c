/* i3lock-warpman: xsecurelock with the warpman drift animation as its saver (X11 / i3).
 *
 * One binary, three modes:
 *   i3lock-warpman [drift options] [-- command]
 *       lock the screen: exec xsecurelock with XSECURELOCK_SAVER set to this binary.
 *       A "-- command" is passed on to xsecurelock (run while locked; see its man page).
 *   i3lock-warpman            (with $XSCREENSAVER_WINDOW set: run by xsecurelock)
 *   i3lock-warpman --saver [drift options]
 *       the saver: draws into $XSCREENSAVER_WINDOW (or --window-id). xsecurelock runs one
 *       per monitor and restarts it if it exits. Logs to
 *       $XDG_RUNTIME_DIR/warpman-lock-x11.log and waits 10 s before exiting on failure.
 *   i3lock-warpman --preview [drift options]
 *       the animation in a normal window, for trying it out.
 *
 * Drift options given when locking reach the savers through $WARPMAN_SAVER_ARGS.
 */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "opts.h"
#include "saver.h"

#define PROG "i3lock-warpman"
#define ARGS_ENV "WARPMAN_SAVER_ARGS"
#define SEP '\x1f'

static void usage(FILE *f)
{
    fprintf(f,
        "i3lock-warpman " WARPMAN_VERSION ": xsecurelock with the warpman drift animation\n\n"
        "usage:\n"
        "  i3lock-warpman [drift options] [-- command]   lock the screen (xsecurelock)\n"
        "  i3lock-warpman --preview [drift options]      the animation in a window\n"
        "  i3lock-warpman --saver [drift options]        (run by xsecurelock)\n\n"
        "When locking, XSECURELOCK_SAVER is set to this program, and\n"
        "XSECURELOCK_SHOW_DATETIME=1 and XSECURELOCK_SAVER_RESET_ON_AUTH_CLOSE=0 unless\n"
        "already set. Other XSECURELOCK_* variables pass through (see man xsecurelock).\n\n");
    wm_opts_usage(f, PROG, WM_OPTS_X11, "[drift options]");
}

static void self_path(char *buf, size_t len)
{
    ssize_t n = readlink("/proc/self/exe", buf, len - 1);
    if (n <= 0) {
        snprintf(buf, len, "%s", PROG);
        return;
    }
    buf[n] = 0;
    const char *deleted = " (deleted)";          /* upgraded while running */
    size_t dl = strlen(deleted);
    if ((size_t)n > dl && !strcmp(buf + n - dl, deleted))
        buf[n - dl] = 0;
}

/* argv[0] + the words of $WARPMAN_SAVER_ARGS + argv[1..] */
static char **saver_argv(int argc, char **argv, int *out_argc)
{
    const char *env = getenv(ARGS_ENV);
    char *copy = strdup(env ? env : "");
    int extra = 0;
    for (char *p = copy; *p; p++)
        extra += *p == SEP;
    char **args = calloc((size_t)argc + extra + 3, sizeof *args);
    int n = 0;
    args[n++] = argv[0];
    if (*copy) {
        char *p = copy;
        for (;;) {
            char *q = strchr(p, SEP);
            if (q)
                *q = 0;
            args[n++] = p;
            if (!q)
                break;
            p = q + 1;
        }
    }
    for (int i = 1; i < argc; i++)
        args[n++] = argv[i];
    args[n] = NULL;
    *out_argc = n;
    return args;
}

static int run_saver(int argc, char **argv, int preview)
{
    if (!preview)
        wm_log_redirect(WARPMAN_LOG_NAME);
    int n;
    char **args = preview ? argv : saver_argv(argc, argv, &n);
    if (preview)
        n = argc;
    WmOpts o;
    int r = wm_opts_parse(&o, n, args, WM_OPTS_X11, PROG " --preview", "[drift options]", NULL);
    if (r)
        return r > 0 ? 0 : 2;
    wm_install_signals();
    int code = wm_saver_run(&o);
    if (!preview) {
        fprintf(stderr, "saver exited (%d)\n", code);
        /* Failed (e.g. no GL): the lock stays black. Back off before xsecurelock
         * restarts us, so a broken setup doesn't respawn in a tight loop. */
        if (code != 0 && !wm_quit)
            wm_sleep_interruptible(10);
    }
    return code;
}

static int lock(int argc, char **argv)
{
    WmOpts o;
    int rest;
    int r = wm_opts_parse(&o, argc, argv, WM_OPTS_X11, PROG, "[drift options] [-- command]", &rest);
    if (r)
        return r > 0 ? 0 : 2;
    /* getopt has consumed a "--": the drift options end before it, and the "--" and
     * the command after it go to xsecurelock */
    int drift_end = rest;
    if (rest > 1 && !strcmp(argv[rest - 1], "--")) {
        drift_end = rest - 1;
        rest = rest - 1;
    } else if (rest < argc) {
        fprintf(stderr, PROG ": unexpected argument: %s (a command to run goes after --)\n",
                argv[rest]);
        return 2;
    }
    /* the drift options, for the savers */
    size_t len = 1;
    for (int i = 1; i < drift_end; i++)
        len += strlen(argv[i]) + 1;
    char *joined = calloc(len, 1);
    for (int i = 1; i < drift_end; i++) {
        if (i > 1)
            joined[strlen(joined)] = SEP;
        strcat(joined, argv[i]);
    }
    if (drift_end > 1)
        setenv(ARGS_ENV, joined, 1);
    else
        unsetenv(ARGS_ENV);
    free(joined);

    char self[4096];
    self_path(self, sizeof self);
    setenv("XSECURELOCK_SAVER", self, 1);
    setenv("XSECURELOCK_SAVER_RESET_ON_AUTH_CLOSE", "0", 0);
    setenv("XSECURELOCK_SHOW_DATETIME", "1", 0);
    unsetenv("XSCREENSAVER_WINDOW");
    wm_log_truncate(WARPMAN_LOG_NAME);          /* fresh log per lock */

    char **args = calloc((size_t)argc + 2, sizeof *args);
    int n = 0;
    args[n++] = "xsecurelock";
    for (int i = rest; i < argc; i++)
        args[n++] = argv[i];
    args[n] = NULL;
    execvp("xsecurelock", args);
    fprintf(stderr, PROG ": cannot run xsecurelock: %s%s\n", strerror(errno),
            errno == ENOENT ? " (sudo apt install xsecurelock)" : "");
    return 127;
}

int main(int argc, char **argv)
{
    if (argc > 1) {
        const char *a = argv[1];
        if (!strcmp(a, "--saver") || !strcmp(a, "--preview")) {
            argv[1] = argv[0];
            return run_saver(argc - 1, argv + 1, !strcmp(a, "--preview"));
        }
        if (!strcmp(a, "-h") || !strcmp(a, "--help")) {
            usage(stdout);
            return 0;
        }
        /* xscreensaver-style hacks take -window-id with a single dash */
        for (int i = 1; i < argc; i++)
            if (!strcmp(argv[i], "-window-id"))
                argv[i] = "--window-id";
    }
    const char *win = getenv("XSCREENSAVER_WINDOW");
    int has_window_id = 0;
    for (int i = 1; i < argc; i++)
        has_window_id |= !strcmp(argv[i], "--window-id");
    if ((win && *win) || has_window_id)
        return run_saver(argc, argv, 0);
    return lock(argc, argv);
}
