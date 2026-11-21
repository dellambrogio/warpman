#include "egl.h"

#include <stdio.h>
#include <string.h>

static int has_ext(EGLDisplay dpy, const char *name)
{
    const char *exts = eglQueryString(dpy, EGL_EXTENSIONS);
    if (!exts)
        return 0;
    size_t n = strlen(name);
    for (const char *p = exts; (p = strstr(p, name)); p += n)
        if ((p == exts || p[-1] == ' ') && (p[n] == ' ' || p[n] == 0))
            return 1;
    return 0;
}

int wm_egl_init(WmEgl *e, EGLenum platform, void *native_display, long visual_id)
{
    memset(e, 0, sizeof *e);
    /* epoxy can't tell the EGL version before there is a display: look it up directly */
    PFNEGLGETPLATFORMDISPLAYPROC get_display =
        (PFNEGLGETPLATFORMDISPLAYPROC)eglGetProcAddress("eglGetPlatformDisplay");
    if (get_display) {
        e->dpy = get_display(platform, native_display, NULL);
    } else {
        PFNEGLGETPLATFORMDISPLAYEXTPROC get_display_ext =
            (PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
        e->dpy = get_display_ext ? get_display_ext(platform, native_display, NULL) : EGL_NO_DISPLAY;
    }
    EGLint major, minor;
    if (e->dpy == EGL_NO_DISPLAY || !eglInitialize(e->dpy, &major, &minor)) {
        fprintf(stderr, "eglInitialize failed (0x%x)\n", eglGetError());
        return -1;
    }
    if (!eglBindAPI(EGL_OPENGL_API)) {
        fprintf(stderr, "eglBindAPI(OpenGL) failed (0x%x)\n", eglGetError());
        return -1;
    }
    const EGLint attrs[] = {
        EGL_SURFACE_TYPE, EGL_WINDOW_BIT,
        EGL_RENDERABLE_TYPE, EGL_OPENGL_BIT,
        EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8,
        EGL_NONE};
    EGLConfig configs[64];
    EGLint n = 0;
    eglChooseConfig(e->dpy, attrs, configs, 64, &n);
    if (n < 1) {
        fprintf(stderr, "no suitable EGL config\n");
        return -1;
    }
    e->config = configs[0];
    if (visual_id >= 0) {
        int found = 0;
        for (EGLint i = 0; i < n; i++) {
            EGLint vid;
            if (eglGetConfigAttrib(e->dpy, configs[i], EGL_NATIVE_VISUAL_ID, &vid) && vid == visual_id) {
                e->config = configs[i];
                found = 1;
                break;
            }
        }
        if (!found)
            fprintf(stderr, "no EGL config matches visual 0x%lx; using the first one\n", visual_id);
    }
    const EGLint ctx_attrs[] = {
        EGL_CONTEXT_MAJOR_VERSION, 3, EGL_CONTEXT_MINOR_VERSION, 3,
        EGL_CONTEXT_OPENGL_PROFILE_MASK, EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT,
        EGL_NONE};
    e->ctx = eglCreateContext(e->dpy, e->config, EGL_NO_CONTEXT, ctx_attrs);
    if (e->ctx == EGL_NO_CONTEXT) {
        fprintf(stderr, "eglCreateContext (OpenGL 3.3 core) failed (0x%x)\n", eglGetError());
        return -1;
    }
    e->surfaceless = has_ext(e->dpy, "EGL_KHR_surfaceless_context");
    return 0;
}

EGLSurface wm_egl_window_surface(WmEgl *e, EGLNativeWindowType win)
{
    EGLSurface s = eglCreateWindowSurface(e->dpy, e->config, win, NULL);
    if (s == EGL_NO_SURFACE)
        fprintf(stderr, "eglCreateWindowSurface failed (0x%x)\n", eglGetError());
    return s;
}

int wm_egl_current(WmEgl *e, EGLSurface surface)
{
    if (surface == EGL_NO_SURFACE && !e->surfaceless) {
        if (e->pbuffer == EGL_NO_SURFACE) {
            const EGLint pb[] = {EGL_WIDTH, 1, EGL_HEIGHT, 1, EGL_NONE};
            e->pbuffer = eglCreatePbufferSurface(e->dpy, e->config, pb);
        }
        surface = e->pbuffer;
    }
    if (!eglMakeCurrent(e->dpy, surface, surface, e->ctx)) {
        fprintf(stderr, "eglMakeCurrent failed (0x%x)\n", eglGetError());
        return -1;
    }
    return 0;
}

void wm_egl_destroy_surface(WmEgl *e, EGLSurface surface)
{
    eglDestroySurface(e->dpy, surface);
}

void wm_egl_terminate(WmEgl *e)
{
    if (e->dpy == EGL_NO_DISPLAY)
        return;
    eglMakeCurrent(e->dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    if (e->pbuffer != EGL_NO_SURFACE)
        eglDestroySurface(e->dpy, e->pbuffer);
    if (e->ctx != EGL_NO_CONTEXT)
        eglDestroyContext(e->dpy, e->ctx);
    eglTerminate(e->dpy);
    e->dpy = EGL_NO_DISPLAY;
}
