/* Options shared by both lock-screen clients. Defaults come from the DRIFT_* environment
 * variables (DRIFT_FPS, DRIFT_SCENE_SECONDS, DRIFT_HUE, DRIFT_SATURATION, DRIFT_STATUS,
 * DRIFT_ANIMATE_ON_BATTERY, DRIFT_SPEED, DRIFT_LENSES, DRIFT_SEED, DRIFT_RAINBOW,
 * DRIFT_RAINBOW_SPEED), then the command line overrides them. */
#pragma once

#include <signal.h>
#include <stdio.h>

#include "drift.h"

enum { WM_OPTS_X11 = 1, WM_OPTS_WAYLAND = 2 };

typedef struct {
    WmSimArgs sim;
    double fps;
    double new_scene_every;     /* seconds, 0 = never */
    int status;                 /* CPU / battery text */
    int still_on_battery;
    double duration;            /* seconds, 0 = until killed */
    int duration_set, layer_set;    /* given on the command line */
    /* X11 */
    int width, height;
    const char *window_id;
    /* Wayland */
    int overlay;                /* overlay layer instead of background */
} WmOpts;

/* Fill defaults from the environment, then parse argv (from optind 1). which selects the
 * platform-only flags. With rest == NULL, leftover arguments are an error; otherwise the
 * index of the first one (after a "--", or argc) is stored there.
 * Returns 0 to run, 1 if it printed help (exit 0), -1 on error. */
int wm_opts_parse(WmOpts *o, int argc, char **argv, int which, const char *prog,
                  const char *usage_extra, int *rest);
void wm_opts_defaults(WmOpts *o);
void wm_opts_usage(FILE *f, const char *prog, int which, const char *usage_extra);

/* --- running under a locker -------------------------------------------------------- */

extern volatile sig_atomic_t wm_quit;     /* set by SIGTERM / SIGINT / SIGHUP */
void wm_install_signals(void);
/* $XDG_RUNTIME_DIR/<name> (or /tmp/<name>) into buf */
const char *wm_log_path(const char *name, char *buf, size_t len);
/* Append stderr to the log file. */
void wm_log_redirect(const char *name);
void wm_log_truncate(const char *name);
/* Sleep up to `seconds`, returning early on SIGTERM etc. */
void wm_sleep_interruptible(double seconds);
