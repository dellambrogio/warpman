#!/usr/bin/env python3
"""warpman drift - animated warped grid, no editor.

Starts with random settings; lens centres wander organically
(smoothly turning headings, varying speed, steered softly away from edges).

Keys: space / n = new random settings, r = next rainbow mode,
      s = mute / unmute (with --sound), v = start / stop recording to MP4,
      f / F11 = fullscreen, Esc / q = quit

--sound plays generative audio that follows the lenses (see warpman/sound.py).
With --sound the picture is held back by the measured audio latency (--av-delay).

--raw writes headless BGR0 frames to stdout instead of opening a window, e.g. to
record a video with ffmpeg. (The lock screens are the native programs in native/.)

--status draws CPU usage and battery level in the bottom-right corner.

--rainbow colours the grid like an RGB keyboard:
    wave    a rainbow band sweeping diagonally across the screen
    ripple  rainbow rings spreading out from the first lens, following it
    cycle   the whole grid fading through the spectrum together
"""

import argparse
import collections
import sys
import time

from warpman.drift import RAINBOW_MODES, Sim


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
            from warpman.sound import Sound
            self.sound = Sound(args.volume)
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
        from warpman.record import Recorder
        from warpman.sound import SR
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
            self.recorder = Recorder(
                path, w, h, self.args.fps,
                audio_rate=snd and SR, audio_delay=self.av_delay())
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
