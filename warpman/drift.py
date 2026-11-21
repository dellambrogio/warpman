"""The drift animation, independent of any display.

Sim holds a scene whose lens centres wander organically (smoothly turning headings,
varying speed, steered softly away from the edges), switches scenes and keeps the
rainbow colouring. It renders with cairo or, via warpman.gl, on the GPU.
"""

import colorsys
import math
import random
import sys

import cairo

from warpman import scene as wm
from warpman.status import Status


class Drifter:
    """Moves one lens like a slowly wandering creature."""

    def __init__(self, lens: wm.Lens, rng: random.Random, speed: float):
        self.lens = lens
        self.rng = rng
        self.heading = rng.uniform(0, 2 * math.pi)
        self.turn = 0.0                       # angular velocity, itself random-walks
        self.base_speed = speed * rng.uniform(0.6, 1.4)
        self.phase = rng.uniform(0, 100)       # for speed / radius / strength breathing
        self.base_radius = lens.radius
        self.base_strength = lens.strength

    def step(self, dt: float, t: float, w: float, h: float):
        rng, l = self.rng, self.lens
        # turn rate wanders and decays -> smooth curvy paths, no jitter
        self.turn += rng.gauss(0, 1.2) * math.sqrt(dt)
        self.turn *= math.exp(-0.8 * dt)
        self.heading += self.turn * dt

        # steer back when outside a soft box around the screen
        mx, my = 0.1 * w, 0.1 * h
        tx = (mx - l.cx if l.cx < mx else (w - mx) - l.cx if l.cx > w - mx else 0)
        ty = (my - l.cy if l.cy < my else (h - my) - l.cy if l.cy > h - my else 0)
        if tx or ty:
            want = math.atan2(ty, tx)
            diff = (want - self.heading + math.pi) % (2 * math.pi) - math.pi
            self.heading += diff * min(1.0, 1.5 * dt)

        p = self.phase + t
        speed = self.base_speed * (0.75 + 0.25 * math.sin(p * 0.37) + 0.1 * math.sin(p * 0.91))
        l.cx += math.cos(self.heading) * speed * dt
        l.cy += math.sin(self.heading) * speed * dt
        # gentle breathing of size and strength
        l.radius = self.base_radius * (1 + 0.08 * math.sin(p * 0.21))
        l.strength = self.base_strength * (1 + 0.12 * math.sin(p * 0.29 + 1.3))


RAINBOW_MODES = ("wave", "ripple", "cycle")


class Rainbow:
    """Time-animated rainbow line colouring; pass .paint to warpman.draw."""

    STOPS = 12          # hue samples per wavelength (cairo interpolates in RGB between them)
    SAT, BOOST = 0.85, 1.6   # rainbow needs more saturation / value than the subtle tint

    def __init__(self, mode: str, speed: float):
        self.mode, self.speed = mode, speed
        self.phase = 0.0
        self.pattern = None

    def update(self, t: float, scene: wm.Scene):
        """Set up geometry for this frame; speed is in spectrum cycles per second."""
        self.phase = (t * self.speed) % 1.0
        w, h = scene.width, scene.height
        lam = 0.6 * math.hypot(w, h)          # one full spectrum per wavelength
        # geometry is kept as attributes too, for the GL backend's shader
        if self.mode == "wave":
            dx, dy = 0.93 * lam, 0.37 * lam    # diagonal, mostly left -> right
            self.wave_vec = (dx, dy)
            self.pattern = lambda: cairo.LinearGradient(0, 0, dx, dy)
        elif self.mode == "ripple":
            if scene.lenses:
                cx, cy = scene.lenses[0].cx, scene.lenses[0].cy
            else:
                cx, cy = w / 2, h / 2
            lam *= 0.5
            self.center, self.lam = (cx, cy), lam
            self.pattern = lambda: cairo.RadialGradient(cx, cy, 0, cx, cy, lam)
        else:
            self.pattern = None

    def paint(self, ctx: cairo.Context, value: float, sat: float):
        value = min(value * self.BOOST, 1)
        sat = max(sat, self.SAT)
        if self.pattern is None:               # cycle: one colour for everything
            ctx.set_source_rgb(*colorsys.hsv_to_rgb(self.phase, sat, value))
            return
        pat = self.pattern()
        pat.set_extend(cairo.EXTEND_REPEAT)
        for k in range(self.STOPS + 1):
            off = k / self.STOPS
            # hue drifts with phase, so bands move away from the start (outward for ripple)
            r, g, b = colorsys.hsv_to_rgb((off - self.phase) % 1.0, sat, value)
            pat.add_color_stop_rgb(off, r, g, b)
        ctx.set_source(pat)


def make_backend(name: str):
    """GL renderer for "gl", None for cairo; falls back to cairo if the GPU path fails."""
    if name != "gl":
        return None
    try:
        from warpman.gl import GLRenderer
        return GLRenderer()
    except Exception as e:      # missing moderngl, no EGL/GPU, shader error, ...
        print(f"GL backend unavailable ({e}); using cairo", file=sys.stderr, flush=True)
        return None


class Sim:
    """Scene + drifting lenses, independent of any display."""

    def __init__(self, args, w: int, h: int):
        self.args = args
        self.t = 0.0
        self.scene = None
        self.surface = None             # reused between frames while the size stays the same
        self.gl = make_backend(getattr(args, "backend", "cairo"))
        self.status = Status() if getattr(args, "status", False) else None
        mode = getattr(args, "rainbow", None)
        self.rainbow = Rainbow(mode, args.rainbow_speed) if mode else None
        self.new_scene(w, h)

    def new_scene(self, w: int, h: int):
        first = self.scene is None
        seed = self.args.seed if first and self.args.seed is not None else random.randrange(1 << 30)
        rng = random.Random(seed)
        n = self.args.lenses or rng.choice([1, 1, 2, 2, 3])
        self.scene = wm.random_scene(rng, w, h, lenses=n, pinch_chance=0.2, seed=seed,
                                     hue=getattr(self.args, "hue", None))
        if getattr(self.args, "saturation", None) is not None:
            self.scene.saturation = self.args.saturation
        speed = self.args.speed * max(w, h) / 1280
        self.drifters = [Drifter(l, rng, speed) for l in self.scene.lenses]
        print(f"seed {seed}: {n} lens(es), hue {self.scene.hue:.0f}, spacing {self.scene.spacing:.0f}",
              file=sys.stderr, flush=True)

    def next_rainbow(self):
        """Cycle off -> wave -> ripple -> cycle -> off."""
        order = (None,) + RAINBOW_MODES
        cur = self.rainbow.mode if self.rainbow else None
        mode = order[(order.index(cur) + 1) % len(order)]
        self.rainbow = Rainbow(mode, self.args.rainbow_speed) if mode else None
        print(f"rainbow: {mode or 'off'}", file=sys.stderr, flush=True)

    def resize(self, w: int, h: int):
        s = self.scene
        if (w, h) != (s.width, s.height):
            sx, sy = w / s.width, h / s.height
            for d in self.drifters:
                d.lens.cx *= sx
                d.lens.cy *= sy
                d.base_radius *= max(sx, sy)
            s.width, s.height = w, h

    def step(self, dt: float):
        self.t += dt
        s = self.scene
        for d in self.drifters:
            d.step(dt, self.t, s.width, s.height)

    def state(self):
        """Everything render() reads, cheap to keep: for drawing a frame a little late."""
        return (self.scene, self.t, self.rainbow,
                [(l.cx, l.cy, l.radius, l.strength) for l in self.scene.lenses])

    def render_state(self, state) -> cairo.ImageSurface:
        """Render a state saved earlier by state(), then put the live one back."""
        live = self.state()
        try:
            self._apply(state)
            return self.render()
        finally:
            self._apply(live)

    def _apply(self, state):
        self.scene, self.t, self.rainbow, lenses = state
        for l, (cx, cy, r, st) in zip(self.scene.lenses, lenses):
            l.cx, l.cy, l.radius, l.strength = cx, cy, r, st

    def render(self) -> cairo.ImageSurface:
        s = self.scene
        if self.rainbow:
            self.rainbow.update(self.t, s)
        if self.gl:
            # GPU frame comes back as BGRX bytes: wrap it so the status text can go on top
            buf = self.gl.render(s, self.rainbow)
            surface = cairo.ImageSurface.create_for_data(
                buf, cairo.FORMAT_RGB24, s.width, s.height, s.width * 4)
            if self.status:
                self.status.draw(cairo.Context(surface), s.width, s.height, s.hue)
            surface.flush()
            return surface
        surface = self.surface
        if surface is None or (surface.get_width(), surface.get_height()) != (s.width, s.height):
            surface = self.surface = cairo.ImageSurface(cairo.FORMAT_RGB24, s.width, s.height)
        ctx = cairo.Context(surface)        # draw() paints the background, so no clearing needed
        if self.rainbow:
            wm.draw(s, ctx, self.rainbow.paint)
        else:
            wm.draw(s, ctx)
        if self.status:
            self.status.draw(ctx, s.width, s.height, s.hue)
        surface.flush()
        return surface
