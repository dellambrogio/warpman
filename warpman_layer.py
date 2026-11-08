#!/usr/bin/env python3
"""warpman layer - the drift animation as a Wayland background (wlr-layer-shell) client.

Renders straight onto each monitor with OpenGL (EGL), using the GL backend's shaders:
no frame readback and no pipe. Works as a swaylock-plugin background:

    swaylock-plugin --command '/path/to/warpman_layer.py --status'

or on a desktop compositor as an animated wallpaper. Each monitor gets its own scene
at its native resolution.

Frames are drawn only when the compositor asks for one (wl_surface.frame), capped at
--fps, so nothing is rendered while a monitor is off. With --still-on-battery the
animation pauses on battery power: the last frame stays up and is only redrawn when
the status text changes.

--layer overlay --duration N is handy for trying it out on a normal desktop: the
animation covers the screen for N seconds (clicks pass through it) and then exits.
"""

import argparse
import copy
import ctypes
import os
import select
import signal
import sys
import time

import cairo
import moderngl
from pywayland import ffi
from pywayland.client import Display
from pywayland.protocol.wayland import WlCompositor, WlOutput

import warpman_drift as dr
import warpman_gl
from wayland_protocols.wlr_layer_shell_unstable_v1 import ZwlrLayerShellV1, ZwlrLayerSurfaceV1

# --- EGL / wayland-egl through ctypes -------------------------------------------------

vp = ctypes.c_void_p
EGLint = ctypes.c_int32
egl = ctypes.CDLL("libEGL.so.1")
wegl = ctypes.CDLL("libwayland-egl.so.1")
for name, res, args in [
    ("eglGetPlatformDisplay", vp, [ctypes.c_uint, vp, vp]),
    ("eglInitialize", ctypes.c_uint, [vp, ctypes.POINTER(EGLint), ctypes.POINTER(EGLint)]),
    ("eglBindAPI", ctypes.c_uint, [ctypes.c_uint]),
    ("eglChooseConfig", ctypes.c_uint, [vp, ctypes.POINTER(EGLint), ctypes.POINTER(vp), EGLint,
                                        ctypes.POINTER(EGLint)]),
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
wegl.wl_egl_window_create.restype = vp
wegl.wl_egl_window_create.argtypes = [vp, ctypes.c_int, ctypes.c_int]
wegl.wl_egl_window_resize.argtypes = [vp, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
wegl.wl_egl_window_destroy.argtypes = [vp]

EGL_PLATFORM_WAYLAND_KHR = 0x31D8
EGL_OPENGL_API = 0x30A2
EGL_NONE = 0x3038
EGL_SURFACE_TYPE, EGL_WINDOW_BIT = 0x3033, 0x0004
EGL_RENDERABLE_TYPE, EGL_OPENGL_BIT = 0x3040, 0x0008
EGL_RED_SIZE, EGL_GREEN_SIZE, EGL_BLUE_SIZE = 0x3024, 0x3023, 0x3022
EGL_CONTEXT_MAJOR_VERSION, EGL_CONTEXT_MINOR_VERSION = 0x3098, 0x30FB
EGL_CONTEXT_OPENGL_PROFILE_MASK, EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT = 0x30FD, 0x1


def ptr(proxy) -> int:
    """Raw C pointer of a pywayland object, for passing to EGL."""
    return int(ffi.cast("uintptr_t", proxy._ptr))


def attribs(*pairs) -> ctypes.Array:
    return (EGLint * (len(pairs) + 1))(*pairs, EGL_NONE)


class EGL:
    """One EGL display + OpenGL 3.3 core context, shared by all monitors' surfaces."""

    def __init__(self, display: Display):
        self.dpy = egl.eglGetPlatformDisplay(EGL_PLATFORM_WAYLAND_KHR, ptr(display), None)
        major, minor = EGLint(), EGLint()
        if not self.dpy or not egl.eglInitialize(self.dpy, major, minor):
            raise RuntimeError(f"eglInitialize failed (0x{egl.eglGetError():x})")
        egl.eglBindAPI(EGL_OPENGL_API)
        cfg, n = vp(), EGLint()
        egl.eglChooseConfig(self.dpy, attribs(
            EGL_SURFACE_TYPE, EGL_WINDOW_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_BIT,
            EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8), cfg, 1, n)
        if n.value < 1:
            raise RuntimeError("no suitable EGL config")
        self.config = cfg
        self.ctx = egl.eglCreateContext(self.dpy, cfg, None, attribs(
            EGL_CONTEXT_MAJOR_VERSION, 3, EGL_CONTEXT_MINOR_VERSION, 3,
            EGL_CONTEXT_OPENGL_PROFILE_MASK, EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT))
        if not self.ctx:
            raise RuntimeError(f"eglCreateContext failed (0x{egl.eglGetError():x})")
        self.current(None)                  # surfaceless (Mesa supports it) until a monitor appears

    def current(self, surface):
        if not egl.eglMakeCurrent(self.dpy, surface, surface, self.ctx):
            raise RuntimeError(f"eglMakeCurrent failed (0x{egl.eglGetError():x})")


# --- one monitor ----------------------------------------------------------------------

class Output:
    def __init__(self, app: "App", global_name: int, wl_output):
        self.app, self.global_name, self.wl_output = app, global_name, wl_output
        self.name, self.scale = "?", 1
        self.surface = self.layer = self.egl_window = self.egl_surface = None
        self.size = None            # buffer size in pixels, once configured
        self.sim = self.renderer = self.status_tex = None
        self.status_key = None
        self.next_scene = 0.0
        self.frames = 0
        self.ready = True           # compositor wants a frame (frame callback arrived)
        self.due = 0.0              # earliest time for the next animated frame (fps cap)
        self.dirty = True           # still mode: something changed, redraw once
        self.callback = None
        wl_output.dispatcher["scale"] = lambda _, s: setattr(self, "scale", s)
        wl_output.dispatcher["name"] = lambda _, n: setattr(self, "name", n)
        wl_output.dispatcher["done"] = lambda _: self.create_surface()

    def create_surface(self):
        if self.surface:
            return
        app = self.app
        self.surface = app.compositor.create_surface()
        empty = app.compositor.create_region()      # clicks pass through to whatever is below
        self.surface.set_input_region(empty)
        empty.destroy()
        layer = ZwlrLayerShellV1.layer.overlay if app.args.layer == "overlay" else \
            ZwlrLayerShellV1.layer.background
        self.layer = app.layer_shell.get_layer_surface(self.surface, self.wl_output, layer, "warpman")
        a = ZwlrLayerSurfaceV1.anchor
        self.layer.set_anchor(a.top | a.bottom | a.left | a.right)
        self.layer.set_size(0, 0)                   # 0 = fill along both anchored axes
        self.layer.set_exclusive_zone(-1)           # cover panels too
        self.layer.set_keyboard_interactivity(0)
        self.layer.dispatcher["configure"] = self.configure
        self.layer.dispatcher["closed"] = lambda _: self.destroy()
        self.surface.commit()

    def configure(self, layer, serial: int, width: int, height: int):
        layer.ack_configure(serial)
        if not width or not height:
            return
        s = self.scale
        w, h = width * s, height * s
        self.surface.set_buffer_scale(s)
        if self.egl_window is None:
            self.egl_window = wegl.wl_egl_window_create(ptr(self.surface), w, h)
            self.egl_surface = egl.eglCreateWindowSurface(
                self.app.egl.dpy, self.app.egl.config, self.egl_window, None)
            if not self.egl_surface:
                raise RuntimeError(f"eglCreateWindowSurface failed (0x{egl.eglGetError():x})")
            self.app.egl.current(self.egl_surface)
            egl.eglSwapInterval(self.app.egl.dpy, 0)    # we pace frames ourselves
        elif (w, h) != self.size:
            wegl.wl_egl_window_resize(self.egl_window, w, h, 0, 0)
        if self.sim is None:
            self.sim = dr.Sim(self.app.sim_args, w, h)
            self.next_scene = self.app.args.new_scene_every
            self.renderer = warpman_gl.GLRenderer(self.app.gl)
            print(f"{self.name}: {w}x{h} (scale {s})", file=sys.stderr, flush=True)
        else:
            self.sim.resize(w, h)
        self.size = (w, h)
        self.dirty = True

    def wants_frame(self, now: float, animate: bool) -> bool:
        if not self.size or not self.ready:
            return False
        return now >= self.due if animate else self.dirty

    def frame(self, now: float, dt: float, animate: bool):
        app, sim = self.app, self.sim
        if animate:
            sim.step(dt)
            every = app.args.new_scene_every
            if every and sim.t >= self.next_scene:
                sim.new_scene(*self.size)
                self.next_scene += every
            self.due = max(self.due + dt, now)  # running behind: don't try to catch up
        if sim.rainbow:
            sim.rainbow.update(sim.t, sim.scene)
        app.egl.current(self.egl_surface)
        w, h = self.size
        self.renderer.render(sim.scene, sim.rainbow, target=app.screen)
        if app.status:
            self.draw_status(w, h)
        # ask to be told when the compositor wants the next frame; requested before the
        # swap so it rides on that commit. No callback (monitor off) = no more frames.
        self.callback = self.surface.frame()
        self.callback.dispatcher["done"] = self.frame_done
        self.ready = self.dirty = False
        egl.eglSwapBuffers(app.egl.dpy, self.egl_surface)
        if self.frames == 0:
            print(f"{self.name}: first frame", file=sys.stderr, flush=True)
        elif self.frames == int(2 * app.args.fps):
            print(f"{self.name}: 2 s of frames", file=sys.stderr, flush=True)
        self.frames += 1

    def frame_done(self, callback, time_ms: int):
        self.ready = True
        self.callback = None

    def draw_status(self, w: int, h: int):
        """Status text: drawn by cairo into a strip texture, redrawn only when it changes."""
        st, hue = self.app.status, self.sim.scene.hue
        st.update()
        size = st.font_size(h)
        sh = int(size * 3.5)
        key = (st.text, round(hue), w, h)
        if key != self.status_key:
            img = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, sh)
            st.draw(cairo.Context(img), w, sh, hue, size=size)
            img.flush()
            if self.status_tex is None or self.status_tex.size != (w, sh):
                if self.status_tex:
                    self.status_tex.release()
                self.status_tex = self.app.gl.texture((w, sh), 4)
                self.status_tex.swizzle = "BGRA"     # cairo ARGB32 is B,G,R,A bytes
            self.status_tex.write(bytes(img.get_data()))
            self.status_key = key
        self.renderer.overlay(self.status_tex, 0, h - sh, (w, h))

    def destroy(self):
        app = self.app
        if self.egl_surface:
            app.egl.current(None)
            egl.eglDestroySurface(app.egl.dpy, self.egl_surface)
        if self.egl_window:
            wegl.wl_egl_window_destroy(self.egl_window)
        if self.layer:
            self.layer.destroy()
        if self.surface:
            self.surface.destroy()
        self.egl_surface = self.egl_window = self.layer = self.surface = None
        self.size = None
        app.outputs.pop(self.global_name, None)


# --- the client -----------------------------------------------------------------------

class App:
    def __init__(self, args):
        self.args = args
        # Sim does lens motion/scenes/rainbow; we render and draw the status text ourselves
        self.sim_args = copy.copy(args)
        self.sim_args.backend, self.sim_args.status = "cairo", False
        self.status = dr.Status() if args.status else None
        self.outputs: dict[int, Output] = {}
        self.compositor = self.layer_shell = None

        self.display = Display()
        self.display.connect()
        self.registry = self.display.get_registry()
        self.registry.dispatcher["global"] = self.on_global
        self.registry.dispatcher["global_remove"] = self.on_global_remove
        self.pending_outputs = []
        self.display.roundtrip()            # learn the globals
        if not self.compositor or not self.layer_shell:
            sys.exit("compositor lacks wl_compositor or zwlr_layer_shell_v1")

        self.egl = EGL(self.display)
        self.gl = moderngl.create_context(require=330)   # attaches to our current EGL context
        self.screen = self.gl.detect_framebuffer(0)
        for name, version in self.pending_outputs:
            self.add_output(name, version)
        self.display.roundtrip()            # output info -> surfaces -> first configure

    def on_global(self, registry, name: int, interface: str, version: int):
        if interface == "wl_compositor":
            self.compositor = registry.bind(name, WlCompositor, min(version, 4))
        elif interface == "zwlr_layer_shell_v1":
            self.layer_shell = registry.bind(name, ZwlrLayerShellV1, min(version, 4))
        elif interface == "wl_output":
            if self.compositor and self.layer_shell and hasattr(self, "gl"):
                self.add_output(name, version)          # hotplugged later
            else:
                self.pending_outputs.append((name, version))

    def add_output(self, name: int, version: int):
        wl_output = self.registry.bind(name, WlOutput, min(version, 4))
        self.outputs[name] = Output(self, name, wl_output)

    def on_global_remove(self, registry, name: int):
        if name in self.outputs:
            self.outputs[name].destroy()

    def shutdown(self):
        """Tear down in dependency order: surfaces, EGL, then the Wayland connection."""
        for out in list(self.outputs.values()):
            out.destroy()
        egl.eglMakeCurrent(self.egl.dpy, None, None, None)
        egl.eglTerminate(self.egl.dpy)
        self.display.flush()
        self.display.disconnect()

    def check_power(self, now: float):
        """Every few seconds: battery -> still, AC -> animate; status text changes."""
        if now >= self.next_power_check:
            self.next_power_check = now + 5.0
            still = self.args.still_on_battery and dr.on_battery()
            if still != (not self.animate):
                self.animate = not still
                print("on battery: animation paused" if still else "animating",
                      file=sys.stderr, flush=True)
                for out in self.outputs.values():
                    out.dirty, out.due = True, now
        if self.status and not self.animate:
            self.status.update()
            if self.status.text != self.status_text:
                self.status_text = self.status.text
                for out in self.outputs.values():
                    out.dirty = True

    def run(self):
        dt = 1 / self.args.fps
        start = time.perf_counter()
        end = start + self.args.duration if self.args.duration else None
        self.animate, self.next_power_check, self.status_text = True, start, None
        fd = self.display.get_fd()
        while True:
            now = time.perf_counter()
            if end is not None and now >= end:
                break
            self.check_power(now)
            for out in list(self.outputs.values()):
                if out.wants_frame(now, self.animate):
                    out.frame(now, dt, self.animate)
            self.display.flush()
            # Sleep until the next thing that can happen: a frame falling due on a monitor
            # that's ready, a power/status check, the end, or a Wayland event (frame
            # callback, configure, hotplug) - select() wakes up for those.
            wake = [self.next_power_check, now + 1.0]
            if end is not None:
                wake.append(end)
            if self.animate:
                wake += [o.due for o in self.outputs.values() if o.size and o.ready]
            timeout = max(0.0, min(wake) - time.perf_counter())
            ready, _, _ = select.select([fd], [], [], timeout)
            if ready:
                self.display.read()
            self.display.dispatch(block=False)


def main():
    p = argparse.ArgumentParser(description="warpman drift as a Wayland background layer.")
    p.add_argument("--fps", type=float, default=30)
    p.add_argument("--speed", type=float, default=60, help="lens speed in px/s at 1280 wide")
    p.add_argument("--lenses", type=int, help="number of lenses (default: random 1-3)")
    p.add_argument("--seed", type=int, help="seed for the first scene (same on every monitor)")
    p.add_argument("--new-scene-every", type=float, default=0, metavar="SECONDS",
                   help="switch to new random settings every N seconds (0 = never)")
    p.add_argument("--status", action="store_true",
                   help="show CPU usage and battery level in the bottom-right corner")
    p.add_argument("--rainbow", choices=dr.RAINBOW_MODES)
    p.add_argument("--rainbow-speed", type=float, default=0.1, metavar="HZ")
    p.add_argument("--hue", type=float, metavar="DEG", help="fix the tint hue for every scene")
    p.add_argument("--saturation", type=float, metavar="0-1", help="fix the tint saturation")
    p.add_argument("--still-on-battery", action="store_true",
                   help="pause the animation while running on battery power")
    p.add_argument("--layer", choices=("background", "overlay"), default="background",
                   help="overlay puts it above windows, for trying it out (default: background)")
    p.add_argument("--duration", type=float, default=0, metavar="SECONDS",
                   help="exit after N seconds (0 = run until killed)")
    args = p.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    app = App(args)
    code = 0
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    except SystemExit as e:
        code = e.code or 0
    finally:
        app.shutdown()
        sys.stderr.flush()
        # Skip interpreter teardown: pywayland's garbage collection would destroy proxies
        # of the already-closed connection in arbitrary order and crash.
        os._exit(code)


if __name__ == "__main__":
    main()
