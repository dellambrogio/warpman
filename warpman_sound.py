"""warpman sound - generative audio for warpman_drift.

Everything is synthesised with NumPy and streamed as raw 16-bit stereo to an
external player (pw-cat, pacat or aplay), so there are no extra Python deps.

What you hear:
  * each lens hums a note of a pentatonic scale whose key follows the scene hue.
    Its pitch comes from its size: big lenses are low, small ones high. It is
    panned by the lens's x position, gets brighter the harder the lens is
    bending the grid right now (and a little when it moves faster), and swells
    as the lens "breathes". Pinching lenses add a sub-octave and sound darker.
  * where the warp squeezes the grid lines into a tight band (mostly where two
    lenses overlap), a high partial shimmers: louder and faster-beating the tighter
    the squeeze, panned to where it is.
  * a lens that runs toward an edge and turns back rings a soft bell at the
    moment it turns, louder the faster it was heading out.
  * a new scene hits with a downward swoosh on the cut frame, and the drones
    switch over within ~0.15 s, like the picture.
  * changing rainbow mode plays a short rising arpeggio, and while a rainbow is
    on, each lens's tone follows the colour passing over it (reds darker, cyans
    brighter).

The display thread calls Sound.update(sim) once per frame; a separate audio
thread renders blocks and blocks on the player's pipe, which paces it.
"""

import fcntl
import json
import math
import os
import queue
import random
import shutil
import subprocess
import sys
import threading
import time

import numpy as np

import warpman as wm

SR = 48000
BLOCK = 512                       # ~11 ms per block
TWO_PI = 2 * math.pi
PENTA = (0, 3, 5, 7, 10)          # minor pentatonic, in semitones
_RAMP = np.arange(BLOCK) / BLOCK  # 0 .. 1 across a block, for per-sample interpolation


PIPE_BYTES = 8192                 # player pipe: ~43 ms of audio (the 64 KiB default is ~340 ms)


def _player_cmd():
    """First available raw-PCM player, as an argv list, or None."""
    if shutil.which("pw-cat"):
        # warpman.pid lets us find our own stream in pw-dump to read its latency
        return ["pw-cat", "--playback", "--raw", "--format", "s16", "--rate", str(SR),
                "--channels", "2", "--latency", "30ms", "--media-role", "Game",
                "-P", f'{{ media.name = "warpman drift", warpman.pid = "{os.getpid()}" }}', "-"]
    if shutil.which("pacat"):
        return ["pacat", "--playback", "--raw", "--format=s16le", f"--rate={SR}",
                "--channels=2", "--latency-msec=30", "--stream-name=warpman drift"]
    if shutil.which("aplay"):
        return ["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(SR), "-c", "2",
                "-B", "50000"]
    return None


def _pan_gains(p):
    """Equal-power stereo gains for pan p in 0 (left) .. 1 (right)."""
    a = np.clip(p, 0.0, 1.0) * (math.pi / 2)
    return np.cos(a), np.sin(a)


class Drone:
    """The continuous voice of one lens."""

    ATTACK, RELEASE = 1.5, 2.5    # seconds (time constants of the gate follower)

    def __init__(self, freq: float, pinch: bool, attack: float = ATTACK):
        self.attack, self.release = attack, self.RELEASE
        self.freq = freq
        self.pinch = pinch
        self.phase = random.uniform(0, 4 * math.pi)  # kept mod 4π so the sub-octave stays continuous
        self.phase2 = random.uniform(0, TWO_PI)      # slightly detuned twin, for warmth
        self.level = 0.0          # attack/release envelope, 0..1
        self.gate = 1.0
        # smoothed controls: current value, ramped toward target each block
        self.amp = self.bright = 0.0
        self.pan = 0.5
        self.bend = 1.0
        self.target = (0.0, 0.0, 0.5, 1.0)

    def set(self, amp, bright, pan, bend):
        self.target = (amp, bright, pan, bend)

    @property
    def dead(self):
        return self.gate == 0 and self.level < 1e-3

    def render(self, out: np.ndarray):
        tau = self.attack if self.gate > self.level else self.release
        lvl0 = self.level
        self.level += (self.gate - self.level) * (1 - math.exp(-BLOCK / SR / tau))
        ta, tb, tp, tbend = self.target
        a0, b0, p0, f0 = self.amp, self.bright, self.pan, self.bend
        self.amp, self.bright, self.pan, self.bend = ta, tb, tp, tbend

        env = (lvl0 + (self.level - lvl0) * _RAMP) * (a0 + (ta - a0) * _RAMP)
        bright = b0 + (tb - b0) * _RAMP
        freq = self.freq * (f0 + (tbend - f0) * _RAMP)

        ph = self.phase + np.cumsum(freq) * (TWO_PI / SR)
        ph2 = self.phase2 + np.cumsum(freq * 1.0035) * (TWO_PI / SR)
        self.phase = float(ph[-1]) % (4 * math.pi)
        self.phase2 = float(ph2[-1]) % TWO_PI

        sig = (np.sin(ph) + 0.6 * np.sin(ph2)
               + 0.45 * bright * np.sin(2 * ph)
               + 0.25 * bright * bright * np.sin(3 * ph2))
        if self.pinch:
            sig += 0.55 * np.sin(0.5 * ph)
        sig *= env * 0.4
        gl, gr = _pan_gains(p0 + (tp - p0) * _RAMP)
        out[:, 0] += sig * gl
        out[:, 1] += sig * gr


class Shimmer:
    """Beating high partial that follows how tightly the grid is squeezed."""

    def __init__(self, freq: float):
        self.freq = freq
        self.phase = self.phase2 = 0.0
        self.trem = random.uniform(0, TWO_PI)
        self.amp = 0.0
        self.squeeze = 0.0
        self.target = (0.0, 0.5, 0.0)     # amplitude, pan, squeeze 0..1
        self.pan = 0.5

    def render(self, out: np.ndarray):
        ta, tp, sq = self.target
        # short glide: follows the picture closely but never clicks
        a1 = self.amp + (ta - self.amp) * (1 - math.exp(-BLOCK / SR / 0.12))
        amp = self.amp + (a1 - self.amp) * _RAMP
        self.amp = a1
        if a1 < 1e-5 and ta == 0:
            return
        self.squeeze += (sq - self.squeeze) * 0.3
        n = np.arange(1, BLOCK + 1)
        # tighter squeeze -> the two partials drift further apart and beat faster
        ph = self.phase + n * (TWO_PI * self.freq / SR)
        ph2 = self.phase2 + n * (TWO_PI * self.freq * (1.003 + 0.012 * self.squeeze) / SR)
        tr = self.trem + n * (TWO_PI * (3 + 7 * self.squeeze) / SR)
        self.phase, self.phase2 = float(ph[-1]) % TWO_PI, float(ph2[-1]) % TWO_PI
        self.trem = float(tr[-1]) % TWO_PI
        sig = (np.sin(ph) + np.sin(ph2)) * (0.6 + 0.4 * np.sin(tr)) * amp * 0.12
        p0 = self.pan
        self.pan = tp
        gl, gr = _pan_gains(p0 + (tp - p0) * _RAMP)
        out[:, 0] += sig * gl
        out[:, 1] += sig * gr


def _bell(freq: float, pan: float, gain=0.22) -> np.ndarray:
    t = np.arange(int(SR * 2.2)) / SR
    env = np.exp(-t * 2.2) * (1 - np.exp(-t * 300))
    sig = (np.sin(TWO_PI * freq * t)
           + 0.35 * np.sin(TWO_PI * freq * 2.76 * t) * np.exp(-t * 5)     # inharmonic bell partial
           + 0.2 * np.sin(TWO_PI * freq * 5.4 * t) * np.exp(-t * 11))
    sig *= env * gain
    gl, gr = _pan_gains(pan)
    return np.stack([sig * gl, sig * gr], axis=1)


def _swoosh(rng: random.Random) -> np.ndarray:
    """Partials gliding downward from a sharp onset (on the cut frame), plus a low thump."""
    dur = 1.6
    t = np.arange(int(SR * dur)) / SR
    env = (1 - np.exp(-t * 150)) * np.exp(-t * 2.4)
    out = np.zeros((t.size, 2))
    thump = np.sin(TWO_PI * 90 * (1 - np.exp(-t * 6)) / 6) * (1 - np.exp(-t * 400)) * np.exp(-t * 9) * 0.35
    out += thump[:, None] * 0.7
    for _ in range(28):
        f0 = rng.uniform(400, 3200)
        f1 = f0 * rng.uniform(0.18, 0.35)
        # exponential glide: f(t) = f0 * (f1/f0)^(t/dur); phase is its integral
        k = math.log(f1 / f0) / dur
        ph = TWO_PI * f0 * (np.exp(k * t) - 1) / k + rng.uniform(0, TWO_PI)
        sig = np.sin(ph) * env * (0.018 * 1000 / (f0 + 600))
        gl, gr = _pan_gains(rng.random())
        out[:, 0] += sig * gl
        out[:, 1] += sig * gr
    return out


def _arpeggio(root: float) -> np.ndarray:
    step = int(SR * 0.09)
    notes = [root * 2 ** (s / 12) for s in (0, 7, 12, 19)]
    out = np.zeros((step * len(notes) + int(SR * 1.2), 2))
    for i, f in enumerate(notes):
        b = _bell(f, 0.25 + 0.5 * i / (len(notes) - 1), gain=0.12)[: out.shape[0] - i * step]
        out[i * step: i * step + b.shape[0]] += b
    return out


class Sound:
    """Audio engine. start() it, call update(sim) every frame, close() at the end."""

    def __init__(self, volume: float = 0.5):
        self.volume = volume
        self.muted = False
        self.proc = None
        self.thread = None
        self.running = False
        self.latency = 0.0         # seconds from update() to the speaker, best estimate
        self.tap = None            # optional callable(bytes) fed every PCM block
        self._ours = 0.0
        self.events = queue.Queue()
        self.snapshot = None       # latest lens state from the display thread
        # display-side bookkeeping
        self._scene = None
        self._rainbow = None
        self._prev = {}            # lens id -> (x, y, time)
        self._out = {}             # lens id -> (outward speed last frame, peak outward speed)
        self._last_ping = {}
        self._last_bell = -9.0
        self._notes = {}           # lens id -> base frequency
        self._root = 110.0
        self.squeeze = (0.0, 0.5)  # (amount 0..1, pan) of the tightest grid squeeze
        n = 32
        gx, gy = np.meshgrid((np.arange(n) + 0.5) / n, (np.arange(n * 5 // 8) + 0.5) / (n * 5 // 8))
        self._grid = (gx.ravel(), gy.ravel())     # sample points, as fractions of the screen
        # audio-side state
        self._drones = {}
        self._shimmer = Shimmer(220.0)
        self._cut_at = None        # block counter of the last scene cut
        self._blocks = 0
        self._oneshots = []        # [array, position]
        dl, dr = int(SR * 0.31), int(SR * 0.43)
        self._delays = (dl, dr)
        self._dlen = dr + BLOCK + 2
        self._dbuf = np.zeros((self._dlen, 2))
        self._dpos = 0

    # ---- lifecycle -------------------------------------------------------------

    def start(self) -> bool:
        cmd = _player_cmd()
        if cmd is None:
            print("sound: no pw-cat, pacat or aplay found; running silent", file=sys.stderr)
            return False
        try:
            # bufsize=0: a buffered pipe holds io.DEFAULT_BUFFER_SIZE before writing anything
            # (128 KiB = 683 ms of audio on Python 3.14), which batches the sound into bursts
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, bufsize=0,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except OSError as e:
            print(f"sound: could not start {cmd[0]} ({e}); running silent", file=sys.stderr)
            return False
        pipe = 65536
        try:
            pipe = fcntl.fcntl(self.proc.stdin.fileno(), fcntl.F_SETPIPE_SZ, PIPE_BYTES)
        except (OSError, AttributeError):
            pass
        # what we know without asking the sound server: the full pipe, half a block, and
        # the player's own buffer; pw-cat's real figure (incl. the device) is read below
        self._ours = (pipe / 4 + BLOCK / 2) / SR
        self.latency = self._ours + {"pw-cat": 0.03, "pacat": 0.03, "aplay": 0.05}[cmd[0]]
        self.running = True
        self.thread = threading.Thread(target=self._loop, name="warpman-sound", daemon=True)
        self.thread.start()
        if cmd[0] == "pw-cat" and shutil.which("pw-dump"):
            threading.Thread(target=self._watch_latency, name="warpman-latency", daemon=True).start()
        print(f"sound: playing through {cmd[0]}", file=sys.stderr, flush=True)
        return True

    def _watch_latency(self):
        """Every few seconds, read our stream's downstream latency from PipeWire.

        The Latency param on a stream's ports carries everything after it (for a
        Bluetooth headset that is the codec and radio, ~150-250 ms); add one graph
        quantum and our own pipe. Re-read so switching outputs is picked up.
        """
        tag, shown = str(os.getpid()), None
        while self.running:
            try:
                dump = json.loads(subprocess.run(["pw-dump"], capture_output=True, timeout=2).stdout)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                dump = []
            for o in dump:
                info = o.get("info") or {}
                props = info.get("props") or {}
                if str(props.get("warpman.pid")) != tag or not o.get("type", "").endswith("Node"):
                    continue
                dev = max((p.get("maxNs", 0) for p in (info.get("params") or {}).get("Latency", [])
                           if p.get("direction") == "Input"), default=0) / 1e9
                try:
                    num, den = props.get("node.latency", "1440/48000").split("/")
                    quantum = int(num) / int(den)
                except ValueError:
                    quantum = 0.03
                self.latency = self._ours + quantum + dev
                if shown is None or abs(self.latency - shown) > 0.01:
                    shown = self.latency
                    print(f"sound: output latency {1000 * self.latency:.0f} ms "
                          f"(device {1000 * dev:.0f} ms)", file=sys.stderr, flush=True)
            for _ in range(30):
                if not self.running:
                    return
                time.sleep(0.1)

    def close(self):
        self.running = False
        if self.proc:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            try:
                self.proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None

    def toggle_mute(self):
        self.muted = not self.muted
        print(f"sound: {'muted' if self.muted else 'on'}", file=sys.stderr, flush=True)

    # ---- display thread --------------------------------------------------------

    def update(self, sim):
        """Read the simulation state; detect scene / rainbow / edge events."""
        if not self.running:
            return
        scene = sim.scene
        w, h = scene.width, scene.height
        now = time.monotonic()

        if scene is not self._scene:
            first = self._scene is None
            self._scene = scene
            self._new_key(scene)
            self._prev.clear()
            self._out.clear()
            if not first:
                self.events.put(("cut",))
            self._shimmer_freq = self._root * 6           # two octaves and a fifth up, in key

        mode = sim.rainbow.mode if sim.rainbow else None
        if mode != self._rainbow:
            if mode is not None:
                self.events.put(("arp", self._root * 4))
            self._rainbow = mode

        rb = sim.rainbow
        if rb:
            rb.update(sim.t, scene)       # same geometry the frame about to be drawn uses
        lenses = []
        for i, d in enumerate(sim.drifters):
            l = d.lens
            key = id(l)
            x, y = l.cx / w, l.cy / h
            px, py, pt = self._prev.get(key, (l.cx, l.cy, now - 1))
            dt = max(now - pt, 1e-3)
            base = max(d.base_speed, 1e-6)
            vx, vy = (l.cx - px) / dt / base, (l.cy - py) / dt / base   # in units of cruising speed
            speed = math.hypot(vx, vy)
            self._prev[key] = (l.cx, l.cy, now)
            self._bounce(key, l, vx, vy, w, h, now)

            # visible bend: peak displacement of the lens falloff, in grid cells
            bend = abs(l.strength) * l.radius * 0.286 / scene.spacing
            colour = self._colour_at(rb, l.cx, l.cy) if rb else None
            lenses.append((key, self._notes[key], l.strength < 0, x, y,
                           l.radius / d.base_radius, abs(l.strength) / max(abs(d.base_strength), 1e-6),
                           speed, bend, colour))
        self.squeeze = self._measure_squeeze(scene)
        self.snapshot = lenses

    @staticmethod
    def _colour_at(rb, x, y):
        """Hue (0..1) the rainbow paints at (x, y) this frame, mirroring Rainbow.paint."""
        if rb.mode == "wave":
            dx, dy = rb.wave_vec
            off = (x * dx + y * dy) / (dx * dx + dy * dy)
        elif rb.mode == "ripple":
            off = math.hypot(x - rb.center[0], y - rb.center[1]) / rb.lam
        else:
            return rb.phase
        return (off - rb.phase) % 1.0

    def _measure_squeeze(self, scene):
        """How tightly the warp packs the grid lines, from the area ratio of the mapping."""
        if len(scene.lenses) == 0:
            return (0.0, 0.5)
        w, h = scene.width, scene.height
        fx, fy = self._grid[0] * w, self._grid[1] * h
        e = 2.0
        x0, y0, _ = wm.warp_array(fx, fy, scene.lenses)
        x1, y1, _ = wm.warp_array(fx + e, fy, scene.lenses)
        x2, y2, _ = wm.warp_array(fx, fy + e, scene.lenses)
        det = ((x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)) / (e * e)
        # 1 = untouched; one lens alone rarely goes below ~0.5, overlaps reach ~0.05,
        # and < 0 means the grid actually folds over itself
        weight = np.clip(0.45 - det, 0, None)
        amount = min(1.0, (0.45 - float(det.min())) / 0.4) if det.min() < 0.45 else 0.0
        pan = float((x0 * weight).sum() / weight.sum() / w) if weight.sum() > 0 else 0.5
        return (amount, pan)

    def _bounce(self, key, l, vx, vy, w, h, now):
        """Ring a bell when a lens near an edge stops heading out and turns back."""
        # inward direction, as the Drifter's steering sees it (soft box 10% in from each edge)
        mx, my = 0.1 * w, 0.1 * h
        tx = mx - l.cx if l.cx < mx else (w - mx) - l.cx if l.cx > w - mx else 0.0
        ty = my - l.cy if l.cy < my else (h - my) - l.cy if l.cy > h - my else 0.0
        norm = math.hypot(tx, ty)
        if not norm:
            self._out.pop(key, None)
            return
        out = -(vx * tx + vy * ty) / norm          # speed toward the edge (cruise = 1)
        prev, peak = self._out.get(key, (None, 0.0))
        peak = max(peak, out)
        # fire once it is clearly heading back (hysteresis), so a wobble that then
        # pushes further out doesn't ring early; costs only a frame or two of latency
        if (prev is not None and prev > -0.08 >= out and peak > 0.25
                and now - self._last_ping.get(key, -9) > 3 and now - self._last_bell > 1.5):
            gain = 0.08 + 0.14 * min(peak, 1.2) / 1.2
            self.events.put(("bell", self._notes[key] * 4, l.cx / w, gain))
            self._last_ping[key] = self._last_bell = now
            peak = 0.0
        self._out[key] = (out, peak)

    def _new_key(self, scene):
        """Pick a root from the hue; each lens gets a scale degree from its size (big = low)."""
        semis = round(scene.hue / 30) % 12
        self._root = 55.0 * 2 ** (semis / 12)            # A1 .. G#2
        slots = [(o, s) for o in (1, 2) for s in PENTA]  # two octaves, low to high
        top = len(slots) - 1
        side = max(scene.width, scene.height)
        # random_scene draws radius uniformly in 0.22 .. 0.38 * max(w, h)
        by_size = sorted(scene.lenses, key=lambda l: -l.radius)
        idx = [round((1 - min(max((l.radius / side - 0.22) / 0.16, 0), 1)) * top) for l in by_size]
        for k in range(1, len(idx)):                     # keep notes distinct, smaller = higher
            idx[k] = max(idx[k], idx[k - 1] + 1)
        over = max(0, (idx[-1] if idx else 0) - top)
        idx = [max(0, i - over) for i in idx]
        self._notes = {}
        for l, i in zip(by_size, idx):
            o, s = slots[min(i, top)]
            self._notes[id(l)] = self._root * 2 ** (o + s / 12)

    # ---- audio thread ----------------------------------------------------------

    def _loop(self):
        rng = random.Random()
        try:
            while self.running:
                self._handle_events(rng)
                block = self._render()
                pcm = (np.tanh(block * self.volume * (0 if self.muted else 1)) * 32000).astype("<i2")
                data = pcm.tobytes()
                tap = self.tap
                if tap is not None:        # e.g. the video recorder; must not block
                    tap(data)
                view = memoryview(data)
                while view:                # unbuffered: a write may be partial
                    view = view[self.proc.stdin.write(view):]
        except (BrokenPipeError, ValueError, OSError, AttributeError):
            pass       # player went away or we are closing
        proc, was_running = self.proc, self.running
        self.running = False
        if was_running and proc is not None:      # not a close(): the player died on its own
            err = b""
            try:
                err = proc.stderr.read() or b""
            except (OSError, ValueError):
                pass
            first = err.decode(errors="replace").strip().splitlines()[:2]
            print(f"sound: player exited ({proc.poll()}); now silent"
                  + (": " + " / ".join(first) if first else ""), file=sys.stderr, flush=True)

    def _handle_events(self, rng):
        while True:
            try:
                ev = self.events.get_nowait()
            except queue.Empty:
                return
            if ev[0] == "cut":
                self._oneshots.append([_swoosh(rng), 0])
                for v in self._drones.values():
                    v.release = 0.12           # old scene's drones end with the old picture
                self._cut_at = self._blocks
            elif ev[0] == "bell":
                self._oneshots.append([_bell(ev[1], ev[2], ev[3]), 0])
            elif ev[0] == "arp":
                self._oneshots.append([_arpeggio(ev[1]), 0])

    def _render(self) -> np.ndarray:
        out = np.zeros((BLOCK, 2))
        snap = self.snapshot or []
        live = set()
        self._blocks += 1
        after_cut = self._cut_at is not None and self._blocks - self._cut_at < SR // BLOCK
        for key, freq, pinch, x, y, rad, stren, speed, bend, colour in snap:
            live.add(key)
            v = self._drones.get(key)
            if v is None:
                v = self._drones[key] = Drone(freq, pinch, attack=0.08 if after_cut else Drone.ATTACK)
            amp = 0.55 + 0.45 * min(rad * stren, 1.3) ** 2
            # 0.15 .. 0.68 grid cells of bend covers random_scene's range
            bright = 0.1 + 0.8 * min(max((bend - 0.12) / 0.56, 0.0), 1.0)
            bright += 0.2 * max(0.0, speed - 0.8)
            bright = min(1.0, bright * (0.7 if pinch else 1.0))
            if colour is not None:
                # the rainbow colour over the lens: red dark .. cyan bright
                warm = 0.5 - 0.5 * math.cos(TWO_PI * colour)
                bright = min(1.0, bright * (0.45 + 0.9 * warm))
                amp *= 0.85 + 0.3 * warm
            bend = 1 + 0.004 * (rad - 1) / 0.08                 # tiny pitch sway with the breathing
            v.set(amp / max(1, len(snap)) ** 0.5, bright, 0.1 + 0.8 * x, bend)
        for key, v in list(self._drones.items()):
            if key not in live:
                v.gate = 0.0
            v.render(out)
            if v.dead:
                del self._drones[key]

        amount, pan = self.squeeze
        sh = self._shimmer
        sh.freq = getattr(self, "_shimmer_freq", sh.freq)
        sh.target = (amount ** 1.5, 0.1 + 0.8 * pan, amount)
        sh.render(out)

        for shot in self._oneshots:
            buf, pos = shot
            chunk = buf[pos: pos + BLOCK]
            out[: chunk.shape[0]] += chunk
            shot[1] = pos + BLOCK
        self._oneshots = [s for s in self._oneshots if s[1] < s[0].shape[0]]

        return self._echo(out)

    def _echo(self, x: np.ndarray) -> np.ndarray:
        """Stereo feedback delay (delays > BLOCK, so each block vectorises), lightly damped."""
        idx = (self._dpos + np.arange(BLOCK)) % self._dlen
        wet = np.empty_like(x)
        for ch, d in enumerate(self._delays):
            r = (idx - d) % self._dlen
            wet[:, ch] = 0.5 * (self._dbuf[r, ch] + self._dbuf[(r - 1) % self._dlen, ch])
        # cross-feed the channels for a wider tail
        self._dbuf[idx] = x + 0.42 * wet[:, ::-1]
        self._dpos = (self._dpos + BLOCK) % self._dlen
        return x + 0.35 * wet
