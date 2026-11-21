/* The drift animation drawn into an X11 window (port of plugins/i3/saver.py).
 *
 * Its main use is as an xsecurelock saver: xsecurelock locks the screen, handles the
 * password, and runs the saver in a window it creates on each monitor, passing the
 * window id in $XSCREENSAVER_WINDOW. The window can also be given with --window-id;
 * with neither, a window of -W x -H is opened (the preview).
 *
 * Renders with OpenGL (EGL on X11) straight into the window. The window size is
 * checked every second, and nothing is drawn while the window is unmapped or the
 * monitor is off (DPMS). With --still-on-battery the animation pauses on battery
 * power: the last frame stays up and is only redrawn when the status text changes or
 * the window becomes visible again.
 */
#include <math.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <X11/Xatom.h>
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/extensions/dpms.h>

#include "drift.h"
#include "egl.h"
#include "gl_render.h"
#include "opts.h"
#include "saver.h"
#include "status.h"

/* The window belongs to xsecurelock and can vanish at any moment (unlock, monitor
 * unplugged). Xlib's default handlers print and exit; exit quietly instead. */
static int x_error(Display *dpy, XErrorEvent *ev)
{
    fprintf(stderr, "X error (window gone?), exiting\n");
    _exit(0);
}

static int x_io_error(Display *dpy)
{
    fprintf(stderr, "X connection lost, exiting\n");
    _exit(0);
}

typedef struct {
    const WmOpts *o;
    Display *dpy;
    Window win;
    int own_window;
    Atom wm_delete;
    int dpms;
    WmEgl egl;
    EGLSurface surface;
    WmGl gl;
    int have_gl;
    WmStatus status;
    WmStatusStrip strip;
    WmSim sim;
    int w, h;
    double next_scene;
} Saver;

static int monitor_on(Saver *s)
{
    if (!s->dpms)
        return 1;
    CARD16 level;
    BOOL enabled;
    if (!DPMSInfo(s->dpy, &level, &enabled))
        return 1;
    return !enabled || level == DPMSModeOn;
}

static void frame(Saver *s, double dt, int animate)
{
    WmSim *sim = &s->sim;
    if (animate) {
        wm_sim_step(sim, dt);
        double every = s->o->new_scene_every;
        if (every > 0 && sim->t >= s->next_scene) {
            wm_sim_new_scene(sim, s->w, s->h);
            s->next_scene += every;
        }
    }
    const WmRainbow *rb = NULL;
    if (sim->rainbow.mode != WM_RAINBOW_OFF) {
        wm_rainbow_update(&sim->rainbow, sim->t, &sim->scene);
        rb = &sim->rainbow;
    }
    wm_gl_render(&s->gl, &sim->scene, rb);
    if (s->o->status)
        wm_status_strip_draw(&s->strip, &s->gl, &s->status, sim->scene.hue, s->w, s->h);
    eglSwapBuffers(s->egl.dpy, s->surface);
}

/* Handle pending X events (only our own window gets any); returns 1 to quit. */
static int events(Saver *s)
{
    while (XPending(s->dpy)) {
        XEvent ev;
        XNextEvent(s->dpy, &ev);
        if (ev.type == ClientMessage && (Atom)ev.xclient.data.l[0] == s->wm_delete)
            return 1;
    }
    return 0;
}

static int setup(Saver *s, const WmOpts *o)
{
    memset(s, 0, sizeof *s);
    s->o = o;
    s->surface = EGL_NO_SURFACE;
    s->dpy = XOpenDisplay(NULL);
    if (!s->dpy) {
        fprintf(stderr, "cannot open X display\n");
        return -1;
    }
    XSetErrorHandler(x_error);
    XSetIOErrorHandler(x_io_error);
    const char *wid = o->window_id ? o->window_id : getenv("XSCREENSAVER_WINDOW");
    if (wid && *wid) {
        char *end;
        s->win = (Window)strtoul(wid, &end, 0);
        if (end == wid || *end) {
            fprintf(stderr, "bad window id: %s\n", wid);
            return -1;
        }
    } else {
        s->own_window = 1;
        s->win = XCreateSimpleWindow(s->dpy, DefaultRootWindow(s->dpy), 0, 0, o->width,
                                     o->height, 0, 0, 0);
        XStoreName(s->dpy, s->win, "warpman");
        XClassHint hint = {"warpman", "warpman"};
        XSetClassHint(s->dpy, s->win, &hint);
        s->wm_delete = XInternAtom(s->dpy, "WM_DELETE_WINDOW", False);
        XSetWMProtocols(s->dpy, s->win, &s->wm_delete, 1);
        XMapWindow(s->dpy, s->win);
        XSync(s->dpy, False);
    }
    XWindowAttributes a;
    XGetWindowAttributes(s->dpy, s->win, &a);
    int ev, err;
    s->dpms = DPMSQueryExtension(s->dpy, &ev, &err) && DPMSCapable(s->dpy);

    if (wm_egl_init(&s->egl, EGL_PLATFORM_X11_KHR, s->dpy, (long)XVisualIDFromVisual(a.visual)))
        return -1;
    s->surface = wm_egl_window_surface(&s->egl, (EGLNativeWindowType)s->win);
    if (s->surface == EGL_NO_SURFACE || wm_egl_current(&s->egl, s->surface))
        return -1;
    eglSwapInterval(s->egl.dpy, 0);             /* we pace frames ourselves */
    if (wm_gl_init(&s->gl)) {
        fprintf(stderr, "shader setup failed\n");
        return -1;
    }
    s->have_gl = 1;
    if (o->status)
        wm_status_init(&s->status);
    s->w = a.width;
    s->h = a.height;
    wm_sim_init(&s->sim, &o->sim, s->w, s->h);
    fprintf(stderr, "window 0x%lx: %dx%d\n", (unsigned long)s->win, s->w, s->h);
    return 0;
}

static void teardown(Saver *s)
{
    if (!s->dpy)
        return;
    if (s->have_gl) {
        wm_status_strip_destroy(&s->strip);
        wm_gl_destroy(&s->gl);
    }
    if (s->egl.dpy != EGL_NO_DISPLAY) {
        wm_egl_current(&s->egl, EGL_NO_SURFACE);
        if (s->surface != EGL_NO_SURFACE)
            wm_egl_destroy_surface(&s->egl, s->surface);
        wm_egl_terminate(&s->egl);
    }
    if (s->own_window)
        XDestroyWindow(s->dpy, s->win);
    XCloseDisplay(s->dpy);
    s->dpy = NULL;
}

int wm_saver_run(const WmOpts *o)
{
    static Saver saver;
    Saver *s = &saver;
    if (setup(s, o)) {
        teardown(s);
        return 1;
    }
    double dt = 1 / o->fps;
    double start = wm_monotonic();
    double end = o->duration > 0 ? start + o->duration : INFINITY;
    s->next_scene = o->new_scene_every;
    int animate = 1, visible = 1, dirty = 1;
    char status_text[160] = "";
    double next_check = start, due = start;
    int fd = ConnectionNumber(s->dpy);
    while (!wm_quit) {
        double now = wm_monotonic();
        if (now >= end)
            break;
        if (events(s))
            break;
        if (now >= next_check) {
            /* once a second: size, visibility, monitor power, battery, status text */
            next_check = now + 1.0;
            XWindowAttributes a;
            XGetWindowAttributes(s->dpy, s->win, &a);
            int was_visible = visible;
            visible = a.map_state == IsViewable && monitor_on(s);
            if ((a.width != s->w || a.height != s->h) && a.width > 0 && a.height > 0) {
                s->w = a.width;
                s->h = a.height;
                wm_sim_resize(&s->sim, s->w, s->h);
                dirty = 1;
            }
            if (visible && !was_visible) {
                dirty = 1;
                due = now;
            }
            int still = o->still_on_battery && wm_on_battery("/sys/class/power_supply");
            if (still == animate) {
                animate = !still;
                fprintf(stderr, still ? "on battery: animation paused\n" : "animating\n");
                dirty = 1;
                due = now;
            }
            if (o->status && !animate) {
                wm_status_update(&s->status);
                if (strcmp(s->status.text, status_text)) {
                    snprintf(status_text, sizeof status_text, "%.159s", s->status.text);
                    dirty = 1;
                }
            }
        }
        if (visible && ((animate && now >= due) || (!animate && dirty))) {
            frame(s, dt, animate);
            dirty = 0;
            due = fmax(due + dt, now);              /* running behind: don't catch up */
        }
        double wake = fmin(next_check, end);
        if (animate && visible)
            wake = fmin(wake, due);
        double pause = wake - wm_monotonic();
        if (pause > 0) {
            XFlush(s->dpy);
            struct pollfd pfd = {fd, POLLIN, 0};
            poll(&pfd, 1, (int)ceil(pause * 1000));
        }
    }
    teardown(s);
    return 0;
}
