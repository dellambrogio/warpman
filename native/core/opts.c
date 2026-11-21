#include <errno.h>
#include <fcntl.h>
#include <getopt.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <time.h>
#include <unistd.h>

#include "opts.h"

static int parse_double(const char *s, double *out)
{
    char *end;
    errno = 0;
    double v = strtod(s, &end);
    if (end == s || *end || errno || !isfinite(v))
        return -1;
    *out = v;
    return 0;
}

static int parse_long(const char *s, long long *out)
{
    char *end;
    errno = 0;
    long long v = strtoll(s, &end, 0);
    if (end == s || *end || errno)
        return -1;
    *out = v;
    return 0;
}

static int is_random(const char *s)
{
    return !*s || !strcasecmp(s, "random") || !strcasecmp(s, "none");
}

static int parse_bool(const char *s)
{
    return !(!strcmp(s, "0") || !strcasecmp(s, "no") || !strcasecmp(s, "false") ||
             !strcasecmp(s, "off"));
}

void wm_opts_defaults(WmOpts *o)
{
    memset(o, 0, sizeof *o);
    o->sim.speed = 60;
    o->sim.has_hue = 1;
    o->sim.hue = 120;               /* green tint */
    o->sim.has_saturation = 1;
    o->sim.saturation = 0.5;
    o->sim.rainbow = WM_RAINBOW_OFF;
    o->sim.rainbow_speed = 0.1;
    o->fps = 24;
    o->new_scene_every = 300;
    o->status = 1;
    o->still_on_battery = 1;
    o->width = 1280;
    o->height = 800;

    const char *v;
    double d;
    long long l;
    if ((v = getenv("DRIFT_FPS")) && !parse_double(v, &d) && d > 0)
        o->fps = d;
    if ((v = getenv("DRIFT_SCENE_SECONDS")) && !parse_double(v, &d) && d >= 0)
        o->new_scene_every = d;
    if ((v = getenv("DRIFT_HUE"))) {
        if (is_random(v))
            o->sim.has_hue = 0;
        else if (!parse_double(v, &d))
            o->sim.hue = d;
    }
    if ((v = getenv("DRIFT_SATURATION"))) {
        if (is_random(v))
            o->sim.has_saturation = 0;
        else if (!parse_double(v, &d))
            o->sim.saturation = d;
    }
    if ((v = getenv("DRIFT_STATUS")))
        o->status = parse_bool(v);
    if ((v = getenv("DRIFT_ANIMATE_ON_BATTERY")))
        o->still_on_battery = !parse_bool(v);
    if ((v = getenv("DRIFT_SPEED")) && !parse_double(v, &d))
        o->sim.speed = d;
    if ((v = getenv("DRIFT_LENSES")) && !parse_long(v, &l) && l >= 0 && l <= WM_MAX_LENSES)
        o->sim.lenses = (int)l;
    if ((v = getenv("DRIFT_SEED")) && *v && !parse_long(v, &l)) {
        o->sim.has_seed = 1;
        o->sim.seed = l;
    }
    if ((v = getenv("DRIFT_RAINBOW")) && *v)
        wm_rainbow_parse(v, &o->sim.rainbow);
    if ((v = getenv("DRIFT_RAINBOW_SPEED")) && !parse_double(v, &d))
        o->sim.rainbow_speed = d;
}

enum {
    O_FPS = 0x100, O_SPEED, O_LENSES, O_SEED, O_SCENE, O_STATUS, O_NO_STATUS, O_RAINBOW,
    O_RAINBOW_SPEED, O_HUE, O_SAT, O_STILL, O_ANIMATE, O_DURATION, O_WINDOW_ID, O_LAYER,
};

void wm_opts_usage(FILE *f, const char *prog, int which, const char *usage_extra)
{
    fprintf(f, "usage: %s %s\n\n", prog, usage_extra ? usage_extra : "[options]");
    fprintf(f,
        "drift options (defaults from DRIFT_* environment variables):\n"
        "  --fps N                  frame rate (DRIFT_FPS, default 24)\n"
        "  --speed PX               lens speed in px/s at 1280 wide (DRIFT_SPEED, 60)\n"
        "  --lenses N               number of lenses, 0 = random 1-3 (DRIFT_LENSES)\n"
        "  --seed N                 seed for the first scene (DRIFT_SEED)\n"
        "  --new-scene-every SEC    new random scene every SEC seconds, 0 = never\n"
        "                           (DRIFT_SCENE_SECONDS, 300)\n"
        "  --hue DEG|random         tint hue (DRIFT_HUE, 120)\n"
        "  --saturation S|random    tint saturation 0-1 (DRIFT_SATURATION, 0.5)\n"
        "  --rainbow MODE           off, wave, ripple or cycle (DRIFT_RAINBOW)\n"
        "  --rainbow-speed HZ       spectrum cycles per second (DRIFT_RAINBOW_SPEED, 0.1)\n"
        "  --status / --no-status   CPU / battery text in the corner (DRIFT_STATUS, on)\n"
        "  --still-on-battery / --animate-on-battery\n"
        "                           pause the animation on battery power\n"
        "                           (DRIFT_ANIMATE_ON_BATTERY=1 animates; default: pause)\n"
        "  --duration SEC           exit after SEC seconds (0 = run until killed)\n");
    if (which & WM_OPTS_X11)
        fprintf(f,
            "  --window-id XID          draw into this window (default: $XSCREENSAVER_WINDOW)\n"
            "  -W, --width N            size of the window opened otherwise (1280)\n"
            "  -H, --height N           (800)\n");
    if (which & WM_OPTS_WAYLAND)
        fprintf(f,
            "  --layer background|overlay  layer-shell layer (default: background)\n");
    fprintf(f, "  -h, --help               this help\n");
}

int wm_opts_parse(WmOpts *o, int argc, char **argv, int which, const char *prog,
                  const char *usage_extra, int *rest)
{
    wm_opts_defaults(o);
    static const struct option common[] = {
        {"fps", required_argument, 0, O_FPS},
        {"speed", required_argument, 0, O_SPEED},
        {"lenses", required_argument, 0, O_LENSES},
        {"seed", required_argument, 0, O_SEED},
        {"new-scene-every", required_argument, 0, O_SCENE},
        {"status", no_argument, 0, O_STATUS},
        {"no-status", no_argument, 0, O_NO_STATUS},
        {"rainbow", required_argument, 0, O_RAINBOW},
        {"rainbow-speed", required_argument, 0, O_RAINBOW_SPEED},
        {"hue", required_argument, 0, O_HUE},
        {"saturation", required_argument, 0, O_SAT},
        {"still-on-battery", no_argument, 0, O_STILL},
        {"animate-on-battery", no_argument, 0, O_ANIMATE},
        {"duration", required_argument, 0, O_DURATION},
        {"help", no_argument, 0, 'h'},
    };
    struct option opts[32];
    size_t n = sizeof common / sizeof *common;
    memcpy(opts, common, sizeof common);
    if (which & WM_OPTS_X11) {
        opts[n++] = (struct option){"window-id", required_argument, 0, O_WINDOW_ID};
        opts[n++] = (struct option){"width", required_argument, 0, 'W'};
        opts[n++] = (struct option){"height", required_argument, 0, 'H'};
    }
    if (which & WM_OPTS_WAYLAND)
        opts[n++] = (struct option){"layer", required_argument, 0, O_LAYER};
    opts[n] = (struct option){0, 0, 0, 0};
    /* "+": stop at the first non-option, like POSIX (keeps "-- command" intact) */
    const char *shortopts = which & WM_OPTS_X11 ? "+hW:H:" : "+h";

    optind = 1;
    opterr = 1;
    int c;
    double d = 0;
    long long l = 0;
    while ((c = getopt_long(argc, argv, shortopts, opts, NULL)) != -1) {
        const char *a = optarg;
        int bad = 0;
        switch (c) {
        case O_FPS: bad = parse_double(a, &d) || d <= 0; o->fps = d; break;
        case O_SPEED: bad = parse_double(a, &o->sim.speed); break;
        case O_LENSES:
            bad = parse_long(a, &l) || l < 0 || l > WM_MAX_LENSES;
            o->sim.lenses = (int)l;
            break;
        case O_SEED: bad = parse_long(a, &l); o->sim.has_seed = 1; o->sim.seed = l; break;
        case O_SCENE: bad = parse_double(a, &d) || d < 0; o->new_scene_every = d; break;
        case O_STATUS: o->status = 1; break;
        case O_NO_STATUS: o->status = 0; break;
        case O_RAINBOW: bad = wm_rainbow_parse(a, &o->sim.rainbow); break;
        case O_RAINBOW_SPEED: bad = parse_double(a, &o->sim.rainbow_speed); break;
        case O_HUE:
            if (is_random(a))
                o->sim.has_hue = 0;
            else
                bad = parse_double(a, &o->sim.hue), o->sim.has_hue = 1;
            break;
        case O_SAT:
            if (is_random(a))
                o->sim.has_saturation = 0;
            else
                bad = parse_double(a, &o->sim.saturation), o->sim.has_saturation = 1;
            break;
        case O_STILL: o->still_on_battery = 1; break;
        case O_ANIMATE: o->still_on_battery = 0; break;
        case O_DURATION:
            bad = parse_double(a, &d) || d < 0;
            o->duration = d;
            o->duration_set = 1;
            break;
        case O_WINDOW_ID: o->window_id = a; break;
        case 'W': bad = parse_long(a, &l) || l < 1; o->width = (int)l; break;
        case 'H': bad = parse_long(a, &l) || l < 1; o->height = (int)l; break;
        case O_LAYER:
            o->layer_set = 1;
            if (!strcmp(a, "overlay"))
                o->overlay = 1;
            else if (!strcmp(a, "background"))
                o->overlay = 0;
            else
                bad = 1;
            break;
        case 'h':
            wm_opts_usage(stdout, prog, which, usage_extra);
            return 1;
        default:
            wm_opts_usage(stderr, prog, which, usage_extra);
            return -1;
        }
        if (bad) {
            fprintf(stderr, "%s: bad value for %s: %s\n", prog, argv[optind - 1], a ? a : "");
            return -1;
        }
    }
    if (rest) {
        *rest = optind;
    } else if (optind < argc) {
        fprintf(stderr, "%s: unexpected argument: %s\n", prog, argv[optind]);
        return -1;
    }
    return 0;
}

/* --- running under a locker -------------------------------------------------------- */

volatile sig_atomic_t wm_quit;

static void on_signal(int sig)
{
    (void)sig;
    wm_quit = 1;
}

void wm_install_signals(void)
{
    struct sigaction sa = {0};
    sa.sa_handler = on_signal;
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = 0;                /* no SA_RESTART: poll()/nanosleep() return EINTR */
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGHUP, &sa, NULL);
    signal(SIGPIPE, SIG_IGN);
}

const char *wm_log_path(const char *name, char *buf, size_t len)
{
    const char *dir = getenv("XDG_RUNTIME_DIR");
    snprintf(buf, len, "%s/%s", dir && *dir ? dir : "/tmp", name);
    return buf;
}

void wm_log_redirect(const char *name)
{
    char path[512];
    int fd = open(wm_log_path(name, path, sizeof path), O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0600);
    if (fd < 0)
        return;
    dup2(fd, STDERR_FILENO);
    close(fd);
    setvbuf(stderr, NULL, _IOLBF, 0);
}

void wm_log_truncate(const char *name)
{
    char path[512];
    int fd = open(wm_log_path(name, path, sizeof path), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (fd >= 0)
        close(fd);
}

void wm_sleep_interruptible(double seconds)
{
    struct timespec ts = {(time_t)seconds, (long)((seconds - (time_t)seconds) * 1e9)};
    while (!wm_quit && nanosleep(&ts, &ts) == -1 && errno == EINTR)
        ;
}
