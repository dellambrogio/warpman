/* swaylock-warpman: swaylock-plugin with the warpman drift animation as its background.
 *
 * One binary, three modes:
 *   swaylock-warpman [swaylock options] [-- drift options]
 *       lock the screen (swaylock-plugin, linked in); the background is this binary
 *       again, run by swaylock-plugin as `swaylock-warpman --background [drift options]`
 *   swaylock-warpman --background [drift options]
 *       the background client (inside swaylock-plugin's nested compositor). Logs to
 *       $XDG_RUNTIME_DIR/warpman-lock.log and waits 10 s before exiting on failure,
 *       since swaylock-plugin restarts it.
 *   swaylock-warpman --preview [drift options]
 *       the animation on the overlay layer of the running compositor (clicks pass
 *       through), for 10 s unless --duration says otherwise.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "layer.h"
#include "opts.h"
#include "pam_service.h"

int swaylock_plugin_main(int argc, char **argv);

#define PROG "swaylock-warpman"

static void usage(FILE *f)
{
    fprintf(f,
        "swaylock-warpman " WARPMAN_VERSION ": swaylock-plugin with the warpman drift background\n\n"
        "usage:\n"
        "  swaylock-warpman [swaylock options] [-- drift options]   lock the screen\n"
        "  swaylock-warpman --preview [drift options]               try the animation (10 s)\n"
        "  swaylock-warpman --background [drift options]            (run by the locker)\n"
        "  swaylock-warpman --help-swaylock                         swaylock-plugin's options\n\n"
        "When locking, -f (daemonize) and -c 000000 are passed unless --no-daemonize is\n"
        "given; --command is set to the drift background.\n\n");
    wm_opts_usage(f, PROG " --preview", WM_OPTS_WAYLAND, "[drift options]");
}

/* append s to buf single-quoted for sh -c */
static void shell_quote(char *buf, size_t len, const char *s)
{
    size_t n = strlen(buf);
    if (n + 3 >= len)
        return;
    buf[n++] = ' ';
    buf[n++] = '\'';
    for (; *s && n + 6 < len; s++) {
        if (*s == '\'') {
            memcpy(buf + n, "'\\''", 4);
            n += 4;
        } else {
            buf[n++] = *s;
        }
    }
    buf[n++] = '\'';
    buf[n] = 0;
}

static void self_path(char *buf, size_t len)
{
    ssize_t n = readlink("/proc/self/exe", buf, len - 1);
    if (n <= 0) {
        snprintf(buf, len, "%s", PROG);
        return;
    }
    buf[n] = 0;
    /* upgraded while running: exec the new file at the same path */
    const char *deleted = " (deleted)";
    size_t dl = strlen(deleted);
    if ((size_t)n > dl && !strcmp(buf + n - dl, deleted))
        buf[n - dl] = 0;
}

static int run_client(int argc, char **argv, int background)
{
    if (background)
        wm_log_redirect(WARPMAN_LOG_NAME);
    WmOpts o;
    int r = wm_opts_parse(&o, argc, argv, WM_OPTS_WAYLAND, PROG " --preview", "[drift options]", NULL);
    if (r)
        return r > 0 ? 0 : 2;
    if (!background) {          /* preview: above the windows for 10 s, unless told otherwise */
        if (!o.layer_set)
            o.overlay = 1;
        if (!o.duration_set)
            o.duration = 10;
    }
    wm_install_signals();
    int code = wm_layer_run(&o);
    if (background) {
        fprintf(stderr, "background exited (%d)\n", code);
        /* swaylock-plugin restarts us: don't respawn in a tight loop if it's broken */
        if (code != 0 && !wm_quit)
            wm_sleep_interruptible(10);
    }
    return code;
}

static int lock(int argc, char **argv)
{
    char self[4096], command[16384];
    self_path(self, sizeof self);
    command[0] = 0;
    shell_quote(command, sizeof command, self);
    snprintf(command + strlen(command), sizeof command - strlen(command), " --background");

    char **args = calloc((size_t)argc + 8, sizeof *args);
    int n = 0, daemonize = 1;
    args[n++] = argv[0];
    int i = 1;
    for (; i < argc; i++)
        if (!strcmp(argv[i], "--no-daemonize"))
            daemonize = 0;
    if (daemonize)
        args[n++] = "-f";
    args[n++] = "-c";
    args[n++] = "000000";
    for (i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--"))
            break;
        if (!strcmp(argv[i], "--no-daemonize"))
            continue;
        args[n++] = argv[i];
    }
    /* drift options after "--" go to the background */
    for (i++; i < argc; i++)
        shell_quote(command, sizeof command, argv[i]);
    args[n++] = "--command";
    args[n++] = command;
    args[n] = NULL;

    /* never lock without a way to unlock: PAM's "other" service usually denies */
    if (!warpman_pam_service_found()) {
        fprintf(stderr, PROG ": no PAM service found (/etc/pam.d/swaylock-warpman, "
                "swaylock-plugin or swaylock); install the package first\n");
        return 1;
    }
    wm_log_truncate(WARPMAN_LOG_NAME);      /* fresh log per lock */
    return swaylock_plugin_main(n, args);
}

int main(int argc, char **argv)
{
    if (argc > 1) {
        const char *a = argv[1];
        if (!strcmp(a, "--background") || !strcmp(a, "--preview")) {
            argv[1] = argv[0];
            return run_client(argc - 1, argv + 1, !strcmp(a, "--background"));
        }
        if (!strcmp(a, "-h") || !strcmp(a, "--help")) {
            usage(stdout);
            return 0;
        }
        if (!strcmp(a, "--help-swaylock")) {
            char *args[] = {argv[0], "--help", NULL};
            return swaylock_plugin_main(2, args);
        }
    }
    return lock(argc, argv);
}
