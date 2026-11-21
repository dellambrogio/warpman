/* Minimal EGL: one OpenGL 3.3 core context drawing onto native windows (port of
 * warpman/egl.py). Shared by the Wayland and X11 clients. */
#pragma once

#include <epoxy/egl.h>

typedef struct {
    EGLDisplay dpy;
    EGLConfig config;
    EGLContext ctx;
    EGLSurface pbuffer;     /* 1x1 fallback when surfaceless contexts aren't supported */
    int surfaceless;
} WmEgl;

/* platform: EGL_PLATFORM_WAYLAND_KHR or EGL_PLATFORM_X11_KHR. visual_id >= 0 (X11)
 * prefers a config matching the window's visual. Returns 0 on success, logging errors. */
int wm_egl_init(WmEgl *e, EGLenum platform, void *native_display, long visual_id);
EGLSurface wm_egl_window_surface(WmEgl *e, EGLNativeWindowType win);
/* Make the context current on surface; EGL_NO_SURFACE = surfaceless (or the pbuffer). */
int wm_egl_current(WmEgl *e, EGLSurface surface);
void wm_egl_destroy_surface(WmEgl *e, EGLSurface surface);
void wm_egl_terminate(WmEgl *e);
