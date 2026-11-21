#!/usr/bin/env python3
"""warpman x11 - the drift animation drawn into an X11 window, for i3 and other X desktops.

Its main use is as an xsecurelock saver: xsecurelock locks the screen and handles the
password, and runs a saver program in a window it creates on each monitor, passing the
window id in $XSCREENSAVER_WINDOW. plugins/i3/lock.sh sets this up:

    XSECURELOCK_SAVER=/path/to/warpman/plugins/i3/saver.sh xsecurelock

The window can also be given with --window-id (or -window-id, as xscreensaver hacks
take it). With neither, a window of -W x -H is opened, for trying it out.

Renders with OpenGL (EGL on X11) using the GL backend's shaders, straight into the
window: no frame readback. The window size is checked every second, and nothing is
drawn while the window is unmapped or the monitor is off (DPMS). With
--still-on-battery the animation pauses on battery power: the last frame stays up and
is only redrawn when the status text changes or the window is exposed again.
"""

import argparse
import copy
import ctypes
import os
import signal
import sys
import time

import moderngl

from warpman import egl
from warpman.drift import RAINBOW_MODES, Sim
from warpman.gl import GLRenderer, StatusStrip
from warpman.status import Status, on_battery

# --- Xlib through ctypes (EGL itself is in warpman.egl) --------------------------------

vp = ctypes.c_void_p
Window = ctypes.c_ulong


class XWindowAttributes(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_int), ("y", ctypes.c_int),
        ("width", ctypes.c_int), ("height", ctypes.c_int),
        ("border_width", ctypes.c_int), ("depth", ctypes.c_int),
        ("visual", vp), ("root", Window), ("class_", ctypes.c_int),
        ("bit_gravity", ctypes.c_int), ("win_gravity", ctypes.c_int),
        ("backing_store", ctypes.c_int),
        ("backing_planes", ctypes.c_ulong), ("backing_pixel", ctypes.c_ulong),
        ("save_under", ctypes.c_int), ("colormap", ctypes.c_ulong),
        ("map_installed", ctypes.c_int), ("map_state", ctypes.c_int),
        ("all_event_masks", ctypes.c_long), ("your_event_mask", ctypes.c_long),
        ("do_not_propagate_mask", ctypes.c_long),
        ("override_redirect", ctypes.c_int), ("screen", vp),
    ]


IsViewable = 2

x11 = ctypes.CDLL("libX11.so.6")
for name, res, args in [
    ("XOpenDisplay", vp, [ctypes.c_char_p]),
    ("XCloseDisplay", ctypes.c_int, [vp]),
    ("XDefaultRootWindow", Window, [vp]),
    ("XCreateSimpleWindow", Window, [vp, Window, ctypes.c_int, ctypes.c_int, ctypes.c_uint,
                                     ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong,
                                     ctypes.c_ulong]),
    ("XStoreName", ctypes.c_int, [vp, Window, ctypes.c_char_p]),
    ("XMapWindow", ctypes.c_int, [vp, Window]),
    ("XDestroyWindow", ctypes.c_int, [vp, Window]),
    ("XGetWindowAttributes", ctypes.c_int, [vp, Window, ctypes.POINTER(XWindowAttributes)]),
    ("XVisualIDFromVisual", ctypes.c_ulong, [vp]),
    ("XFlush", ctypes.c_int, [vp]),
    ("XSync", ctypes.c_int, [vp, ctypes.c_int]),
    ("XSetErrorHandler", vp, [vp]),
    ("XQueryExtension", ctypes.c_int, [vp, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int),
                                       ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]),
]:
    getattr(x11, name).restype = res
    getattr(x11, name).argtypes = args

try:                                        # DPMS lives in libXext; optional
    xext = ctypes.CDLL("libXext.so.6")
    xext.DPMSCapable.argtypes = [vp]
    xext.DPMSInfo.argtypes = [vp, ctypes.POINTER(ctypes.c_uint16), ctypes.POINTER(ctypes.c_ubyte)]
except OSError:
    xext = None

# The window belongs to xsecurelock and can vanish at any moment (unlock, monitor
# unplugged). Xlib's default error handler prints and exits; keep the exit but quietly.
@ctypes.CFUNCTYPE(ctypes.c_int, vp, vp)
def x_error(dpy, event):
    print("X error (window gone?), exiting", file=sys.stderr, flush=True)
    os._exit(0)


class App:
    def __init__(self, args):
        self.args = args
        self.sim_args = copy.copy(args)
        self.sim_args.backend, self.sim_args.status = "cairo", False
        self.status = Status() if args.status else None

        self.dpy = x11.XOpenDisplay(None)
        if not self.dpy:
            sys.exit("cannot open X display")
        x11.XSetErrorHandler(ctypes.cast(x_error, vp))
        wid = args.window_id or os.environ.get("XSCREENSAVER_WINDOW")
        self.own_window = not wid
        if wid:
            self.win = int(wid, 0)
        else:
            self.win = x11.XCreateSimpleWindow(self.dpy, x11.XDefaultRootWindow(self.dpy),
                                               0, 0, args.width, args.height, 0, 0, 0)
            x11.XStoreName(self.dpy, self.win, b"warpman")
            x11.XMapWindow(self.dpy, self.win)
            x11.XSync(self.dpy, 0)
        attrs = self.attributes()
        i = [ctypes.c_int() for _ in range(3)]
        self.dpms = bool(xext and x11.XQueryExtension(self.dpy, b"DPMS", *i)
                         and xext.DPMSCapable(self.dpy))

        self.egl = egl.Display(egl.PLATFORM_X11, self.dpy, x11.XVisualIDFromVisual(attrs.visual))
        self.surface = self.egl.window_surface(self.win)
        self.egl.current(self.surface)
        self.egl.swap_interval(0)               # we pace frames ourselves

        self.gl = moderngl.create_context(require=330)
        self.screen = self.gl.detect_framebuffer(0)
        self.renderer = GLRenderer(self.gl)
        self.status_strip = StatusStrip(self.gl) if self.status else None
        self.size = (attrs.width, attrs.height)
        self.sim = Sim(self.sim_args, *self.size)
        print(f"window 0x{self.win:x}: {self.size[0]}x{self.size[1]}", file=sys.stderr, flush=True)

    def attributes(self) -> XWindowAttributes:
        a = XWindowAttributes()
        x11.XGetWindowAttributes(self.dpy, self.win, a)
        return a

    def monitor_on(self) -> bool:
        if not self.dpms:
            return True
        level, enabled = ctypes.c_uint16(), ctypes.c_ubyte()
        xext.DPMSInfo(self.dpy, level, enabled)
        return not enabled.value or level.value == 0        # DPMSModeOn

    def frame(self, dt: float, animate: bool):
        sim = self.sim
        if animate:
            sim.step(dt)
            every = self.args.new_scene_every
            if every and sim.t >= self.next_scene:
                sim.new_scene(*self.size)
                self.next_scene += every
        if sim.rainbow:
            sim.rainbow.update(sim.t, sim.scene)
        self.renderer.render(sim.scene, sim.rainbow, target=self.screen)
        if self.status_strip:
            self.status_strip.draw(self.renderer, self.status, sim.scene.hue, self.size)
        self.egl.swap(self.surface)

    def run(self):
        args = self.args
        dt = 1 / args.fps
        start = time.monotonic()
        end = start + args.duration if args.duration else None
        self.next_scene = args.new_scene_every
        animate, visible, dirty, status_text = True, True, True, None
        next_check, due = start, start
        while True:
            now = time.monotonic()
            if end is not None and now >= end:
                break
            if now >= next_check:
                # Once a second: size, visibility, monitor power, battery, status text.
                next_check = now + 1.0
                a = self.attributes()
                was_visible = visible
                visible = a.map_state == IsViewable and self.monitor_on()
                if (a.width, a.height) != self.size and a.width > 0 and a.height > 0:
                    self.size = (a.width, a.height)
                    self.sim.resize(*self.size)
                    dirty = True
                if visible and not was_visible:
                    dirty, due = True, now
                still = args.still_on_battery and on_battery()
                if still == animate:
                    animate = not still
                    print("on battery: animation paused" if still else "animating",
                          file=sys.stderr, flush=True)
                    dirty, due = True, now
                if self.status and not animate:
                    self.status.update()
                    if self.status.text != status_text:
                        status_text, dirty = self.status.text, True
            if visible and (animate and now >= due or not animate and dirty):
                self.frame(dt, animate)
                dirty = False
                due = max(due + dt, now)                # running behind: don't catch up
            wake = [next_check] + ([due] if animate and visible else []) + \
                ([end] if end is not None else [])
            pause = min(wake) - time.monotonic()
            if pause > 0:
                time.sleep(pause)

    def shutdown(self):
        self.egl.current(None)
        self.egl.destroy_surface(self.surface)
        self.egl.terminate()
        if self.own_window:
            x11.XDestroyWindow(self.dpy, self.win)
        x11.XCloseDisplay(self.dpy)


def main():
    # xscreensaver hacks take -window-id with a single dash
    argv = ["--window-id" if a == "-window-id" else a for a in sys.argv[1:]]
    p = argparse.ArgumentParser(description="warpman drift in an X11 window (xsecurelock saver).")
    p.add_argument("--window-id", metavar="XID",
                   help="draw into this window (default: $XSCREENSAVER_WINDOW, else open one)")
    p.add_argument("-W", "--width", type=int, default=1280, help="size of the window we open")
    p.add_argument("-H", "--height", type=int, default=800)
    p.add_argument("--fps", type=float, default=30)
    p.add_argument("--speed", type=float, default=60, help="lens speed in px/s at 1280 wide")
    p.add_argument("--lenses", type=int, help="number of lenses (default: random 1-3)")
    p.add_argument("--seed", type=int, help="seed for the first scene")
    p.add_argument("--new-scene-every", type=float, default=0, metavar="SECONDS",
                   help="switch to new random settings every N seconds (0 = never)")
    p.add_argument("--status", action="store_true",
                   help="show CPU usage and battery level in the bottom-right corner")
    p.add_argument("--rainbow", choices=RAINBOW_MODES)
    p.add_argument("--rainbow-speed", type=float, default=0.1, metavar="HZ")
    p.add_argument("--hue", type=float, metavar="DEG", help="fix the tint hue for every scene")
    p.add_argument("--saturation", type=float, metavar="0-1", help="fix the tint saturation")
    p.add_argument("--still-on-battery", action="store_true",
                   help="pause the animation while running on battery power")
    p.add_argument("--duration", type=float, default=0, metavar="SECONDS",
                   help="exit after N seconds (0 = run until killed)")
    args = p.parse_args(argv)
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
    sys.exit(code)


if __name__ == "__main__":
    main()
