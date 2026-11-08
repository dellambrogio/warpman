#!/usr/bin/env python3
"""warpman drift - animated warped grid, no editor.

Starts with random settings; lens centres wander organically
(smoothly turning headings, varying speed, steered softly away from edges).

Keys: space / n = new random settings, r = next rainbow mode,
      s = mute / unmute (with --sound), v = start / stop recording to MP4,
      f / F11 = fullscreen, Esc / q = quit

--sound plays generative audio that follows the lenses (see warpman_sound.py).
With --sound the picture is held back by the measured audio latency (--av-delay).

--raw writes headless BGR0 frames to stdout instead of opening a window, e.g. to
record a video with ffmpeg. (The lock screen uses warpman_layer.py instead.)

--status draws CPU usage and battery level in the bottom-right corner.

--rainbow colours the grid like an RGB keyboard:
    wave    a rainbow band sweeping diagonally across the screen
    ripple  rainbow rings spreading out from the first lens, following it
    cycle   the whole grid fading through the spectrum together
"""

import argparse
import collections
import colorsys
import glob
import math
import random
import sys
import time

import cairo

import warpman as wm


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


def on_battery() -> bool:
    """True when a battery is present and no external power (mains / USB-PD) is online."""
    battery, external, discharging = False, None, False
    for d in glob.glob("/sys/class/power_supply/*"):
        try:
            with open(f"{d}/type") as f:
                kind = f.read().strip()
            if kind == "Battery":
                battery = True
                with open(f"{d}/status") as f:
                    discharging |= f.read().strip() == "Discharging"
            elif kind in ("Mains", "USB", "USB_C", "USB_PD"):
                with open(f"{d}/online") as f:
                    external = bool(external) or f.read().strip() == "1"
        except OSError:
            continue
    if not battery:
        return False
    # No adapter reported at all: go by the battery itself. ("Not charging" can mean
    # plugged in with a charge limit, so only "Discharging" counts.)
    return discharging if external is None else not external


class Status:
    """CPU usage (from /proc/stat) and battery level (from /sys), refreshed every few seconds."""

    def __init__(self, interval: float = 2.0):
        self.interval = interval
        self.next = time.monotonic() + 1.0     # let a real CPU sample accumulate first
        self.prev_cpu = self._cpu_times()
        self.text = ""

    @staticmethod
    def _cpu_times():
        try:
            with open("/proc/stat") as f:
                vals = [int(v) for v in f.readline().split()[1:]]
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)   # idle + iowait
            return sum(vals), idle
        except (OSError, ValueError, IndexError):
            return None

    @staticmethod
    def _battery():
        parts = []
        for d in sorted(glob.glob("/sys/class/power_supply/*")):
            try:
                with open(f"{d}/type") as f:
                    if f.read().strip() != "Battery":
                        continue
                with open(f"{d}/capacity") as f:
                    cap = int(f.read())
                with open(f"{d}/status") as f:
                    st = f.read().strip()
            except (OSError, ValueError):
                continue
            mark = {"Charging": " +", "Discharging": " -"}.get(st, "")
            parts.append(f"BAT {cap}%{mark}")
        return "  ".join(parts)

    def update(self):
        now = time.monotonic()
        if now < self.next:
            return
        self.next = now + self.interval
        fields = []
        cur = self._cpu_times()
        if cur and self.prev_cpu:
            total, idle = cur[0] - self.prev_cpu[0], cur[1] - self.prev_cpu[1]
            if total > 0:
                fields.append(f"CPU {100 * (total - idle) / total:.0f}%")
        self.prev_cpu = cur
        bat = self._battery()
        if bat:
            fields.append(bat)
        self.text = "   ".join(fields)

    @staticmethod
    def font_size(h: int) -> float:
        return max(8, h / 110)

    def draw(self, ctx: cairo.Context, w: int, h: int, hue: float, size: float | None = None):
        """Draw at the bottom-right of a w x h area; size defaults to font_size(h)."""
        self.update()
        if not self.text:
            return
        size = size or self.font_size(h)
        ctx.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        ctx.set_font_size(size)
        ext = ctx.text_extents(self.text)
        margin = size * 1.5
        x = w - margin - ext.x_advance
        y = h - margin
        ctx.set_source_rgba(0, 0, 0, 0.55)        # soft backing so text stays legible over lines
        pad = size * 0.3
        ctx.rectangle(x - pad, y + ext.y_bearing - pad, ext.x_advance + 2 * pad, ext.height + 2 * pad)
        ctx.fill()
        r, g, b = colorsys.hsv_to_rgb(hue / 360, 0.3, 0.85)
        ctx.set_source_rgb(r, g, b)
        ctx.move_to(x, y)
        ctx.show_text(self.text)


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
        import warpman_gl
        return warpman_gl.GLRenderer()
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


def warpman_sound_rate():
    import warpman_sound
    return warpman_sound.SR


def print_keys(sound: bool):
    """Key cheatsheet on stderr, shown when the window opens."""
    keys = [("space / n", "new random scene"),
            ("r", "next rainbow mode (off, wave, ripple, cycle)"),
            ("s", "mute / unmute sound" if sound else "mute (needs --sound)"),
            ("v", "start / stop recording to MP4" + (" (with sound)" if sound else "")),
            ("f / F11", "toggle fullscreen"),
            ("q / Esc", "quit")]
    width = max(len(k) for k, _ in keys)
    lines = ["warpman drift keys:"] + [f"  {k:<{width}}  {what}" for k, what in keys]
    print("\n".join(lines) + "\n", file=sys.stderr, flush=True)


class App:
    def __init__(self, root, args):
        import tkinter as tk
        self.root, self.args = root, args
        print_keys(args.sound)
        root.title("warpman drift")
        root.configure(bg="black")
        self.label = tk.Label(root, bg="black", bd=0, highlightthickness=0)
        self.label.pack(fill="both", expand=True)
        self.photo = None
        self.fullscreen = False
        self.sound = None
        self.history = collections.deque()    # (time, sim state), for --av-delay
        self.recorder = None
        # REC marker floats over the picture, so it never ends up in the video
        self.rec_label = tk.Label(root, text="\u25cf REC", fg="#ff3b30", bg="black",
                                  font=("monospace", 11, "bold"))
        if args.sound:
            import warpman_sound
            self.sound = warpman_sound.Sound(args.volume)
            if self.sound.start():
                root.bind("s", lambda e: self.sound.toggle_mute())
            else:
                self.sound = None

        root.bind("<Escape>", lambda e: self.quit())
        root.bind("q", lambda e: self.quit())
        root.protocol("WM_DELETE_WINDOW", self.quit)
        for k in ("n", "<space>"):
            root.bind(k, lambda e: self.sim.new_scene(*self.size()))
        root.bind("r", lambda e: self.sim.next_rainbow())
        root.bind("v", lambda e: self.toggle_recording())
        for k in ("f", "<F11>"):
            root.bind(k, lambda e: self.toggle_fullscreen())

        root.update()
        self.sim = Sim(args, *self.size())
        self.last = time.perf_counter()
        self.tick()

    def quit(self):
        if self.recorder:
            self.stop_recording(wait=True)
        if self.sound:
            self.sound.close()
        self.root.destroy()

    def toggle_recording(self):
        if self.recorder:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self, size=None):
        import pathlib
        import warpman_record
        w, h = size or (self.sim.scene.width, self.sim.scene.height)
        out = pathlib.Path(self.args.record_dir)
        out.mkdir(parents=True, exist_ok=True)
        path = out / time.strftime("drift-%Y%m%d-%H%M%S.mp4")
        n = 2
        while path.exists():                 # two recordings in the same second
            path = out / time.strftime(f"drift-%Y%m%d-%H%M%S-{n}.mp4")
            n += 1
        snd = self.sound if self.sound and self.sound.running else None
        try:
            self.recorder = warpman_record.Recorder(
                path, w, h, self.args.fps,
                audio_rate=snd and warpman_sound_rate(), audio_delay=self.av_delay())
        except (RuntimeError, OSError) as e:
            print(f"recording: {e}", file=sys.stderr, flush=True)
            return
        if snd:
            snd.tap = self.recorder.audio
        self.rec_label.place(relx=1.0, x=-12, y=10, anchor="ne")
        print(f"recording to {path}{' with sound' if snd else ''} ({w}x{h}); v to stop",
              file=sys.stderr, flush=True)

    def stop_recording(self, wait=False):
        rec, self.recorder = self.recorder, None
        if self.sound:
            self.sound.tap = None
        self.rec_label.place_forget()
        rec.stop(wait=wait)

    def av_delay(self) -> float:
        """Seconds to hold the picture back so it lines up with the sound."""
        if not self.sound:
            return 0.0
        if self.args.av_delay != "auto":
            return max(0.0, float(self.args.av_delay) / 1000)
        # the picture has its own lag too (~1 frame + the compositor's vsync)
        return max(0.0, self.sound.latency - (1 / self.args.fps + 0.016))

    def size(self):
        return max(self.label.winfo_width(), 64), max(self.label.winfo_height(), 64)

    def toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        self.root.attributes("-fullscreen", self.fullscreen)

    def tick(self):
        from PIL import Image, ImageTk
        now = time.perf_counter()
        dt = min(now - self.last, 0.1)
        self.last = now

        self.sim.resize(*self.size())
        self.sim.step(dt)
        if self.sound:
            self.sound.update(self.sim)
        delay = self.av_delay()
        if delay > 0:
            # the sound heard now was made from the state `latency` ago, so show that one
            self.history.append((now, self.sim.state()))
            while len(self.history) > 1 and self.history[1][0] <= now - delay:
                self.history.popleft()
            surface = self.sim.render_state(self.history[0][1])
        else:
            self.history.clear()
            surface = self.sim.render()
        w, h = surface.get_width(), surface.get_height()
        data = bytes(surface.get_data())
        if self.recorder:
            if self.recorder.size != (w, h):
                # rawvideo has one fixed size: close this file, carry on in a new one
                print("recording: window size changed, starting a new file", file=sys.stderr)
                self.stop_recording()
                self.start_recording((w, h))
            if self.recorder:
                self.recorder.frame(data)
        img = Image.frombuffer("RGB", (w, h), data, "raw", "BGRX", surface.get_stride())
        if self.photo is None or self.photo.width() != w or self.photo.height() != h:
            self.photo = ImageTk.PhotoImage(img)
            self.label.configure(image=self.photo)
        else:
            self.photo.paste(img)

        spent = time.perf_counter() - now
        self.root.after(max(1, int(1000 / self.args.fps - spent * 1000)), self.tick)


def run_raw(args):
    """Stream BGR0 frames (args.width x args.height) to stdout at args.fps, forever."""
    w, h = args.width, args.height
    sim = Sim(args, w, h)
    out = sys.stdout.buffer
    frame_dt = 1 / args.fps
    scene_every = args.new_scene_every
    next_scene = scene_every
    deadline = time.perf_counter()
    try:
        while True:
            sim.step(frame_dt)          # fixed timestep: mpv plays at exactly args.fps
            if scene_every and sim.t >= next_scene:
                sim.new_scene(w, h)
                next_scene += scene_every
            surface = sim.render()
            data = surface.get_data()
            stride = surface.get_stride()
            if stride == w * 4:
                out.write(data)
            else:
                mv = memoryview(data)
                for y in range(h):
                    out.write(mv[y * stride: y * stride + w * 4])
            out.flush()
            deadline += frame_dt
            pause = deadline - time.perf_counter()
            if pause > 0:
                time.sleep(pause)
            else:
                deadline = time.perf_counter()   # running behind: don't try to catch up
    except (BrokenPipeError, KeyboardInterrupt):
        pass


def main():
    p = argparse.ArgumentParser(description="Animated warped grid with drifting lenses.")
    p.add_argument("-W", "--width", type=int, default=1280)
    p.add_argument("-H", "--height", type=int, default=800)
    p.add_argument("--fps", type=float, default=30)
    p.add_argument("--speed", type=float, default=60, help="lens speed in px/s at 1280 wide")
    p.add_argument("--lenses", type=int, help="number of lenses (default: random 1-3)")
    p.add_argument("--seed", type=int, help="seed for the first scene")
    p.add_argument("--hue", type=float, metavar="DEG",
                   help="fix the tint hue for every scene, 0-360 (default: random per scene)")
    p.add_argument("--saturation", type=float, metavar="0-1",
                   help="fix the tint saturation for every scene (default: random 0.08-0.25)")
    p.add_argument("--backend", choices=("cairo", "gl"), default="cairo",
                   help="cairo (CPU, default) or gl (GPU via EGL; falls back to cairo)")
    p.add_argument("--fullscreen", action="store_true")
    p.add_argument("--raw", action="store_true",
                   help="no window: write raw BGR0 frames of WIDTHxHEIGHT to stdout")
    p.add_argument("--new-scene-every", type=float, default=0, metavar="SECONDS",
                   help="with --raw: switch to new random settings every N seconds (0 = never)")
    p.add_argument("--status", action="store_true",
                   help="show CPU usage and battery level in the bottom-right corner")
    p.add_argument("--rainbow", choices=RAINBOW_MODES,
                   help="RGB-keyboard style colouring instead of a single tint")
    p.add_argument("--rainbow-speed", type=float, default=0.1, metavar="HZ",
                   help="rainbow animation speed in spectrum cycles per second (default 0.1)")
    p.add_argument("--sound", action="store_true",
                   help="generative sound effects that follow the lenses (needs pw-cat, pacat or aplay)")
    p.add_argument("--volume", type=float, default=0.5, metavar="0-1",
                   help="sound volume (default 0.5)")
    p.add_argument("--record-dir", default="out", metavar="DIR",
                   help="where v saves recordings (default out/)")
    p.add_argument("--av-delay", default="auto", metavar="MS|auto",
                   help="with --sound: hold the picture back by MS so it lines up with the audio; "
                        "auto (default) reads the output latency from PipeWire, e.g. Bluetooth")
    args = p.parse_args()
    if args.av_delay != "auto":
        try:
            float(args.av_delay)
        except ValueError:
            p.error("--av-delay takes milliseconds or 'auto'")

    if args.raw:
        if args.sound:
            p.error("--sound only works in the window, not with --raw")
        if sys.stdout.isatty():
            p.error("--raw writes binary video to stdout; pipe it somewhere (e.g. mpv)")
        run_raw(args)
        return

    import tkinter as tk
    root = tk.Tk()
    root.geometry(f"{args.width}x{args.height}")
    if args.fullscreen:
        root.attributes("-fullscreen", True)
    app = App(root, args)
    app.fullscreen = args.fullscreen
    root.mainloop()


if __name__ == "__main__":
    main()
