/* The drift animation as a Wayland background (wlr-layer-shell) client: the
 * swaylock-warpman background, and a preview / animated wallpaper on a desktop
 * compositor (port of plugins/sway/layer.py).
 *
 * Renders straight onto each monitor with OpenGL (EGL). Each monitor gets its own scene
 * at its native resolution. Frames are drawn only when the compositor asks for one
 * (wl_surface.frame), capped at --fps, so nothing is rendered while a monitor is off.
 * With --still-on-battery the animation pauses on battery power: the last frame stays
 * up and is only redrawn when the status text changes.
 */
#include <errno.h>
#include <math.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <wayland-client.h>
#include <wayland-egl.h>

#include "drift.h"
#include "egl.h"
#include "gl_render.h"
#include "layer.h"
#include "opts.h"
#include "status.h"
#include "wlr-layer-shell-unstable-v1-client-protocol.h"

struct app;

struct output {
    struct wl_list link;
    struct app *app;
    uint32_t global_name;
    struct wl_output *wl_output;
    uint32_t version;
    char name[64];
    int scale;
    struct wl_surface *surface;
    struct zwlr_layer_surface_v1 *layer;
    struct wl_egl_window *egl_window;
    EGLSurface egl_surface;
    int w, h;                   /* buffer size in pixels, once configured (0 before) */
    int have_sim;
    WmSim sim;
    int have_gl;
    WmGl gl;
    WmStatusStrip strip;
    double next_scene;
    long frames;
    int ready;                  /* the compositor wants a frame (frame callback arrived) */
    double due;                 /* earliest time for the next animated frame (fps cap) */
    int dirty;                  /* still mode: something changed, redraw once */
    struct wl_callback *callback;
};

struct pending {
    uint32_t name, version;
};

struct app {
    const WmOpts *o;
    struct wl_display *display;
    struct wl_registry *registry;
    struct wl_compositor *compositor;
    struct zwlr_layer_shell_v1 *layer_shell;
    struct wl_list outputs;
    struct pending pending[16];
    int n_pending;
    int egl_ready;
    WmEgl egl;
    int have_status;
    WmStatus status;
    int animate;
    double next_power_check;
    char status_text[160];
};

static void output_destroy(struct output *out);

/* --- frame callbacks ----------------------------------------------------------------- */

static void frame_done(void *data, struct wl_callback *cb, uint32_t time_ms)
{
    struct output *out = data;
    wl_callback_destroy(cb);
    out->callback = NULL;
    out->ready = 1;
}

static const struct wl_callback_listener frame_listener = {.done = frame_done};

/* --- layer surface ------------------------------------------------------------------- */

static void layer_configure(void *data, struct zwlr_layer_surface_v1 *layer, uint32_t serial,
                            uint32_t width, uint32_t height)
{
    struct output *out = data;
    struct app *app = out->app;
    zwlr_layer_surface_v1_ack_configure(layer, serial);
    if (!width || !height)
        return;
    int s = out->scale > 0 ? out->scale : 1;
    int w = (int)width * s, h = (int)height * s;
    wl_surface_set_buffer_scale(out->surface, s);
    if (!out->egl_window) {
        out->egl_window = wl_egl_window_create(out->surface, w, h);
        out->egl_surface = wm_egl_window_surface(&app->egl, (EGLNativeWindowType)out->egl_window);
        if (out->egl_surface == EGL_NO_SURFACE || wm_egl_current(&app->egl, out->egl_surface)) {
            fprintf(stderr, "%s: cannot create an EGL surface\n", out->name);
            output_destroy(out);
            return;
        }
        eglSwapInterval(app->egl.dpy, 0);           /* we pace frames ourselves */
    } else if (w != out->w || h != out->h) {
        wl_egl_window_resize(out->egl_window, w, h, 0, 0);
    }
    if (!out->have_sim) {
        wm_sim_init(&out->sim, &app->o->sim, w, h);
        out->have_sim = 1;
        out->next_scene = app->o->new_scene_every;
        wm_egl_current(&app->egl, out->egl_surface);
        if (wm_gl_init(&out->gl)) {
            fprintf(stderr, "%s: shader setup failed\n", out->name);
            output_destroy(out);
            return;
        }
        out->have_gl = 1;
        fprintf(stderr, "%s: %dx%d (scale %d)\n", out->name, w, h, s);
    } else {
        wm_sim_resize(&out->sim, w, h);
    }
    out->w = w;
    out->h = h;
    out->dirty = 1;
}

static void layer_closed(void *data, struct zwlr_layer_surface_v1 *layer)
{
    output_destroy(data);
}

static const struct zwlr_layer_surface_v1_listener layer_listener = {
    .configure = layer_configure,
    .closed = layer_closed,
};

static void create_surface(struct output *out)
{
    struct app *app = out->app;
    if (out->surface)
        return;
    out->surface = wl_compositor_create_surface(app->compositor);
    struct wl_region *empty = wl_compositor_create_region(app->compositor);
    wl_surface_set_input_region(out->surface, empty);   /* clicks pass through */
    wl_region_destroy(empty);
    uint32_t layer = app->o->overlay ? ZWLR_LAYER_SHELL_V1_LAYER_OVERLAY
                                     : ZWLR_LAYER_SHELL_V1_LAYER_BACKGROUND;
    out->layer = zwlr_layer_shell_v1_get_layer_surface(app->layer_shell, out->surface,
                                                       out->wl_output, layer, "warpman");
    zwlr_layer_surface_v1_set_anchor(out->layer,
        ZWLR_LAYER_SURFACE_V1_ANCHOR_TOP | ZWLR_LAYER_SURFACE_V1_ANCHOR_BOTTOM |
        ZWLR_LAYER_SURFACE_V1_ANCHOR_LEFT | ZWLR_LAYER_SURFACE_V1_ANCHOR_RIGHT);
    zwlr_layer_surface_v1_set_size(out->layer, 0, 0);          /* fill both axes */
    zwlr_layer_surface_v1_set_exclusive_zone(out->layer, -1);  /* cover panels too */
    zwlr_layer_surface_v1_set_keyboard_interactivity(out->layer, 0);
    zwlr_layer_surface_v1_add_listener(out->layer, &layer_listener, out);
    wl_surface_commit(out->surface);
}

/* --- wl_output ----------------------------------------------------------------------- */

static void out_geometry(void *data, struct wl_output *o, int32_t x, int32_t y, int32_t pw,
                         int32_t ph, int32_t subpixel, const char *make, const char *model,
                         int32_t transform) {}
static void out_mode(void *data, struct wl_output *o, uint32_t flags, int32_t w, int32_t h,
                     int32_t refresh) {}
static void out_done(void *data, struct wl_output *o)
{
    create_surface(data);
}
static void out_scale(void *data, struct wl_output *o, int32_t factor)
{
    ((struct output *)data)->scale = factor;
}
static void out_name(void *data, struct wl_output *o, const char *name)
{
    struct output *out = data;
    snprintf(out->name, sizeof out->name, "%s", name);
}
static void out_description(void *data, struct wl_output *o, const char *d) {}

static const struct wl_output_listener output_listener = {
    .geometry = out_geometry,
    .mode = out_mode,
    .done = out_done,
    .scale = out_scale,
    .name = out_name,
    .description = out_description,
};

static void add_output(struct app *app, uint32_t name, uint32_t version)
{
    struct output *out = calloc(1, sizeof *out);
    out->app = app;
    out->global_name = name;
    out->version = version < 4 ? version : 4;
    out->scale = 1;
    out->ready = 1;
    out->dirty = 1;
    out->egl_surface = EGL_NO_SURFACE;
    snprintf(out->name, sizeof out->name, "output-%u", name);
    out->wl_output = wl_registry_bind(app->registry, name, &wl_output_interface, out->version);
    wl_output_add_listener(out->wl_output, &output_listener, out);
    wl_list_insert(app->outputs.prev, &out->link);
    if (out->version < 2)           /* no done event: create the surface right away */
        create_surface(out);
}

static void output_destroy(struct output *out)
{
    struct app *app = out->app;
    if (out->have_gl || out->egl_surface != EGL_NO_SURFACE) {
        if (out->egl_surface != EGL_NO_SURFACE)
            wm_egl_current(&app->egl, out->egl_surface);
        if (out->have_gl) {
            wm_status_strip_destroy(&out->strip);
            wm_gl_destroy(&out->gl);
        }
        wm_egl_current(&app->egl, EGL_NO_SURFACE);
        if (out->egl_surface != EGL_NO_SURFACE)
            wm_egl_destroy_surface(&app->egl, out->egl_surface);
    }
    if (out->egl_window)
        wl_egl_window_destroy(out->egl_window);
    if (out->callback)
        wl_callback_destroy(out->callback);
    if (out->layer)
        zwlr_layer_surface_v1_destroy(out->layer);
    if (out->surface)
        wl_surface_destroy(out->surface);
    if (out->wl_output) {
        if (out->version >= 3)
            wl_output_release(out->wl_output);
        else
            wl_output_destroy(out->wl_output);
    }
    wl_list_remove(&out->link);
    free(out);
}

/* --- registry ------------------------------------------------------------------------ */

static void on_global(void *data, struct wl_registry *reg, uint32_t name, const char *iface,
                      uint32_t version)
{
    struct app *app = data;
    if (!strcmp(iface, wl_compositor_interface.name)) {
        app->compositor = wl_registry_bind(reg, name, &wl_compositor_interface,
                                           version < 4 ? version : 4);
    } else if (!strcmp(iface, zwlr_layer_shell_v1_interface.name)) {
        app->layer_shell = wl_registry_bind(reg, name, &zwlr_layer_shell_v1_interface,
                                            version < 4 ? version : 4);
    } else if (!strcmp(iface, wl_output_interface.name)) {
        if (app->compositor && app->layer_shell && app->egl_ready)
            add_output(app, name, version);         /* hotplugged later */
        else if (app->n_pending < 16)
            app->pending[app->n_pending++] = (struct pending){name, version};
    }
}

static void on_global_remove(void *data, struct wl_registry *reg, uint32_t name)
{
    struct app *app = data;
    struct output *out, *tmp;
    wl_list_for_each_safe(out, tmp, &app->outputs, link) {
        if (out->global_name == name) {
            fprintf(stderr, "%s: removed\n", out->name);
            output_destroy(out);
        }
    }
}

static const struct wl_registry_listener registry_listener = {
    .global = on_global,
    .global_remove = on_global_remove,
};

/* --- drawing ------------------------------------------------------------------------- */

static int wants_frame(struct output *out, double now, int animate)
{
    if (!out->w || !out->ready || !out->have_gl)
        return 0;
    return animate ? now >= out->due : out->dirty;
}

static void draw_frame(struct output *out, double now, double dt, int animate)
{
    struct app *app = out->app;
    WmSim *sim = &out->sim;
    double every = app->o->new_scene_every;
    if (animate) {
        wm_sim_step(sim, dt);
        if (every > 0 && sim->t >= out->next_scene) {
            wm_sim_new_scene(sim, out->w, out->h);
            out->next_scene += every;
        }
        out->due = fmax(out->due + dt, now);    /* running behind: don't try to catch up */
    }
    const WmRainbow *rb = NULL;
    if (sim->rainbow.mode != WM_RAINBOW_OFF) {
        wm_rainbow_update(&sim->rainbow, sim->t, &sim->scene);
        rb = &sim->rainbow;
    }
    wm_egl_current(&app->egl, out->egl_surface);
    wm_gl_render(&out->gl, &sim->scene, rb);
    if (app->have_status)
        wm_status_strip_draw(&out->strip, &out->gl, &app->status, sim->scene.hue, out->w, out->h);
    /* ask to be told when the compositor wants the next frame; requested before the swap
     * so it rides on that commit. No callback (monitor off) = no more frames. */
    out->callback = wl_surface_frame(out->surface);
    wl_callback_add_listener(out->callback, &frame_listener, out);
    out->ready = out->dirty = 0;
    eglSwapBuffers(app->egl.dpy, out->egl_surface);
    if (out->frames == 0)
        fprintf(stderr, "%s: first frame\n", out->name);
    else if (out->frames == (long)(2 * app->o->fps))
        fprintf(stderr, "%s: 2 s of frames\n", out->name);
    out->frames++;
}

static void check_power(struct app *app, double now)
{
    if (now >= app->next_power_check) {
        app->next_power_check = now + 5.0;
        int still = app->o->still_on_battery && wm_on_battery(app->status.sysfs_root
                                                              ? app->status.sysfs_root
                                                              : "/sys/class/power_supply");
        if (still == app->animate) {
            app->animate = !still;
            fprintf(stderr, still ? "on battery: animation paused\n" : "animating\n");
            struct output *out;
            wl_list_for_each(out, &app->outputs, link) {
                out->dirty = 1;
                out->due = now;
            }
        }
    }
    if (app->have_status && !app->animate) {
        wm_status_update(&app->status);
        if (strcmp(app->status.text, app->status_text)) {
            snprintf(app->status_text, sizeof app->status_text, "%s", app->status.text);
            struct output *out;
            wl_list_for_each(out, &app->outputs, link)
                out->dirty = 1;
        }
    }
}

/* --- run ----------------------------------------------------------------------------- */

int wm_layer_run(const WmOpts *o)
{
    struct app app_ = {0}, *app = &app_;
    app->o = o;
    wl_list_init(&app->outputs);
    if (o->status) {
        wm_status_init(&app->status);
        app->have_status = 1;
    } else {
        app->status.sysfs_root = "/sys/class/power_supply";
    }

    app->display = wl_display_connect(NULL);
    if (!app->display) {
        fprintf(stderr, "cannot connect to the Wayland display\n");
        return 1;
    }
    app->registry = wl_display_get_registry(app->display);
    wl_registry_add_listener(app->registry, &registry_listener, app);
    wl_display_roundtrip(app->display);                 /* learn the globals */
    if (!app->compositor || !app->layer_shell) {
        fprintf(stderr, "compositor lacks wl_compositor or zwlr_layer_shell_v1\n");
        wl_display_disconnect(app->display);
        return 1;
    }
    if (wm_egl_init(&app->egl, EGL_PLATFORM_WAYLAND_KHR, app->display, -1) ||
        wm_egl_current(&app->egl, EGL_NO_SURFACE)) {     /* surfaceless until a monitor appears */
        wm_egl_terminate(&app->egl);
        wl_display_disconnect(app->display);
        return 1;
    }
    app->egl_ready = 1;
    for (int i = 0; i < app->n_pending; i++)
        add_output(app, app->pending[i].name, app->pending[i].version);
    app->n_pending = 0;
    wl_display_roundtrip(app->display);     /* output info -> surfaces -> first configure */

    double dt = 1 / o->fps;
    double start = wm_monotonic();
    double end = o->duration > 0 ? start + o->duration : INFINITY;
    app->animate = 1;
    app->next_power_check = start;
    int fd = wl_display_get_fd(app->display), code = 0;
    while (!wm_quit) {
        double now = wm_monotonic();
        if (now >= end)
            break;
        check_power(app, now);
        struct output *out, *tmp;
        wl_list_for_each_safe(out, tmp, &app->outputs, link)
            if (wants_frame(out, now, app->animate))
                draw_frame(out, now, dt, app->animate);

        /* Sleep until the next thing that can happen: a frame falling due on a monitor
         * that's ready, a power/status check, the end, or a Wayland event. */
        double wake = fmin(app->next_power_check, now + 1.0);
        wake = fmin(wake, end);
        if (app->animate)
            wl_list_for_each(out, &app->outputs, link)
                if (out->w && out->ready)
                    wake = fmin(wake, out->due);
        while (wl_display_prepare_read(app->display) != 0)
            wl_display_dispatch_pending(app->display);
        if (wl_display_flush(app->display) < 0 && errno != EAGAIN) {
            wl_display_cancel_read(app->display);
            fprintf(stderr, "Wayland connection lost\n");
            code = 0;      /* the compositor / locker went away */
            break;
        }
        double timeout = wake - wm_monotonic();
        int ms = timeout <= 0 ? 0 : (int)ceil(timeout * 1000);
        struct pollfd pfd = {fd, POLLIN, 0};
        int r = poll(&pfd, 1, ms);
        if (r > 0 && (pfd.revents & POLLIN)) {
            if (wl_display_read_events(app->display) < 0) {
                fprintf(stderr, "Wayland connection lost\n");
                code = 0;      /* the compositor / locker went away */
                break;
            }
        } else {
            wl_display_cancel_read(app->display);
            if (r > 0 && (pfd.revents & (POLLERR | POLLHUP))) {
                fprintf(stderr, "Wayland connection closed\n");
                code = 0;      /* the compositor / locker went away */
                break;
            }
        }
        if (wl_display_dispatch_pending(app->display) < 0) {
            fprintf(stderr, "Wayland protocol error\n");
            code = 1;
            break;
        }
    }

    /* tear down in dependency order: surfaces, EGL, then the Wayland connection */
    struct output *out, *tmp;
    wl_list_for_each_safe(out, tmp, &app->outputs, link)
        output_destroy(out);
    wm_egl_terminate(&app->egl);
    if (app->layer_shell)
        zwlr_layer_shell_v1_destroy(app->layer_shell);
    if (app->compositor)
        wl_compositor_destroy(app->compositor);
    wl_registry_destroy(app->registry);
    wl_display_flush(app->display);
    wl_display_disconnect(app->display);
    return code;
}
