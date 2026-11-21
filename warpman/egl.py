"""Minimal EGL through ctypes: an OpenGL 3.3 core context drawing onto native windows.

Shared by the on-screen clients (plugins/sway: Wayland, plugins/i3: X11). Once a
context is current, moderngl.create_context() attaches to it and warpman.gl
renders into the window's default framebuffer.
"""

import ctypes

vp = ctypes.c_void_p
EGLint = ctypes.c_int32

PLATFORM_X11 = 0x31D5           # EGL_PLATFORM_X11_KHR
PLATFORM_WAYLAND = 0x31D8       # EGL_PLATFORM_WAYLAND_KHR

EGL_OPENGL_API = 0x30A2
EGL_NONE = 0x3038
EGL_SURFACE_TYPE, EGL_WINDOW_BIT = 0x3033, 0x0004
EGL_RENDERABLE_TYPE, EGL_OPENGL_BIT = 0x3040, 0x0008
EGL_RED_SIZE, EGL_GREEN_SIZE, EGL_BLUE_SIZE = 0x3024, 0x3023, 0x3022
EGL_NATIVE_VISUAL_ID = 0x302E
EGL_CONTEXT_MAJOR_VERSION, EGL_CONTEXT_MINOR_VERSION = 0x3098, 0x30FB
EGL_CONTEXT_OPENGL_PROFILE_MASK, EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT = 0x30FD, 0x1

_lib = None


def lib() -> ctypes.CDLL:
    """libEGL with the prototypes we use (loaded on first use)."""
    global _lib
    if _lib is None:
        egl = ctypes.CDLL("libEGL.so.1")
        for name, res, args in [
            ("eglGetPlatformDisplay", vp, [ctypes.c_uint, vp, vp]),
            ("eglInitialize", ctypes.c_uint, [vp, ctypes.POINTER(EGLint), ctypes.POINTER(EGLint)]),
            ("eglBindAPI", ctypes.c_uint, [ctypes.c_uint]),
            ("eglChooseConfig", ctypes.c_uint, [vp, ctypes.POINTER(EGLint), ctypes.POINTER(vp),
                                                EGLint, ctypes.POINTER(EGLint)]),
            ("eglGetConfigAttrib", ctypes.c_uint, [vp, vp, EGLint, ctypes.POINTER(EGLint)]),
            ("eglCreateContext", vp, [vp, vp, vp, ctypes.POINTER(EGLint)]),
            ("eglCreateWindowSurface", vp, [vp, vp, vp, ctypes.POINTER(EGLint)]),
            ("eglDestroySurface", ctypes.c_uint, [vp, vp]),
            ("eglMakeCurrent", ctypes.c_uint, [vp, vp, vp, vp]),
            ("eglSwapBuffers", ctypes.c_uint, [vp, vp]),
            ("eglSwapInterval", ctypes.c_uint, [vp, EGLint]),
            ("eglGetError", EGLint, []),
            ("eglTerminate", ctypes.c_uint, [vp]),
        ]:
            getattr(egl, name).restype = res
            getattr(egl, name).argtypes = args
        _lib = egl
    return _lib


def attribs(*pairs) -> ctypes.Array:
    """An EGL attribute list: key, value, ..., EGL_NONE."""
    return (EGLint * (len(pairs) + 1))(*pairs, EGL_NONE)


class EGLError(RuntimeError):
    pass


class Display:
    """One EGL display and one GL 3.3 core context, shared by all its window surfaces.

    native_display is the platform's display pointer (wl_display* or X11 Display*).
    visual_id, if given (X11), prefers a config that matches the window's visual.
    """

    def __init__(self, platform: int, native_display: int, visual_id: int | None = None):
        egl = self.egl = lib()
        self.dpy = egl.eglGetPlatformDisplay(platform, native_display, None)
        major, minor = EGLint(), EGLint()
        if not self.dpy or not egl.eglInitialize(self.dpy, major, minor):
            raise EGLError(f"eglInitialize failed (0x{egl.eglGetError():x})")
        egl.eglBindAPI(EGL_OPENGL_API)
        self.config = self._pick_config(visual_id)
        self.ctx = egl.eglCreateContext(self.dpy, self.config, None, attribs(
            EGL_CONTEXT_MAJOR_VERSION, 3, EGL_CONTEXT_MINOR_VERSION, 3,
            EGL_CONTEXT_OPENGL_PROFILE_MASK, EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT))
        if not self.ctx:
            raise EGLError(f"eglCreateContext failed (0x{egl.eglGetError():x})")

    def _pick_config(self, visual_id: int | None):
        egl = self.egl
        configs, n = (vp * 64)(), EGLint()
        egl.eglChooseConfig(self.dpy, attribs(
            EGL_SURFACE_TYPE, EGL_WINDOW_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_BIT,
            EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8), configs, 64, n)
        if n.value < 1:
            raise EGLError("no suitable EGL config")
        if visual_id is not None:
            vid = EGLint()
            for c in configs[:n.value]:
                if egl.eglGetConfigAttrib(self.dpy, c, EGL_NATIVE_VISUAL_ID, vid) and \
                        vid.value == visual_id:
                    return vp(c)
        return vp(configs[0])

    def window_surface(self, native_window: int):
        """A surface for a native window (wl_egl_window* or an X11 window id)."""
        surface = self.egl.eglCreateWindowSurface(self.dpy, self.config, native_window, None)
        if not surface:
            raise EGLError(f"eglCreateWindowSurface failed (0x{self.egl.eglGetError():x})")
        return surface

    def current(self, surface):
        """Make the context current on surface (None: surfaceless, which Mesa supports)."""
        if not self.egl.eglMakeCurrent(self.dpy, surface, surface, self.ctx):
            raise EGLError(f"eglMakeCurrent failed (0x{self.egl.eglGetError():x})")

    def swap_interval(self, n: int):
        self.egl.eglSwapInterval(self.dpy, n)

    def swap(self, surface):
        self.egl.eglSwapBuffers(self.dpy, surface)

    def destroy_surface(self, surface):
        self.egl.eglDestroySurface(self.dpy, surface)

    def terminate(self):
        self.egl.eglMakeCurrent(self.dpy, None, None, None)
        self.egl.eglTerminate(self.dpy)
