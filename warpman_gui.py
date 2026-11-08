#!/usr/bin/env python3
"""warpman GUI - live editor for warped-grid wallpapers.

Mouse on the preview:
    click empty space     add a lens
    click + drag lens     select & move it
    wheel                 resize selected lens
    shift + wheel         change strength (negative = pinch)
    right-click lens      delete it
Keys: r = randomize, Delete = remove selected lens, Ctrl+S = save
"""

import base64
import io
import math
import random
import tkinter as tk
from tkinter import filedialog, ttk

import cairo

import warpman as wm

PRESETS = ["1280x800", "1024x768", "1920x1080", "2560x1440", "2560x1600", "3840x2160"]
PREVIEW_MAX = (960, 640)


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("warpman")
        root.configure(bg="#1b1b1b")
        self.scene = wm.Scene(lenses=[wm.Lens(380, 490, 380, 0.5)])
        self.selected: int | None = 0
        self.drag = None
        self._pending = None
        self.photo = None

        main = ttk.Frame(root, padding=8)
        main.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(main, bg="black", highlightthickness=0, cursor="crosshair")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(0, weight=1)

        side = ttk.Frame(main, padding=(10, 0, 0, 0))
        side.grid(row=0, column=1, sticky="ns")

        self.size_var = tk.StringVar(value="1280x800")
        ttk.Label(side, text="Size").pack(anchor="w")
        size = ttk.Combobox(side, textvariable=self.size_var, values=PRESETS, width=14)
        size.pack(fill="x", pady=(0, 8))
        size.bind("<<ComboboxSelected>>", lambda e: self.set_size())
        size.bind("<Return>", lambda e: self.set_size())

        self.vars = {}
        self.slider(side, "spacing", "Grid spacing", 40, 300, self.scene.spacing)
        self.slider(side, "hue", "Hue", 0, 360, self.scene.hue)
        self.slider(side, "saturation", "Saturation", 0, 1, self.scene.saturation)
        self.slider(side, "brightness", "Brightness", 0.2, 3, self.scene.brightness)
        self.slider(side, "line_scale", "Line width", 0.2, 4, self.scene.line_scale)

        ttk.Separator(side).pack(fill="x", pady=8)
        self.lens_label = ttk.Label(side, text="Selected lens")
        self.lens_label.pack(anchor="w")
        self.slider(side, "radius", "Radius", 20, 1500, 380, lens=True)
        self.slider(side, "strength", "Strength (− pinch)", -0.8, 0.8, 0.5, lens=True)
        self.show_guides = tk.BooleanVar(value=True)
        ttk.Checkbutton(side, text="Show lens outlines", variable=self.show_guides,
                        command=self.redraw).pack(anchor="w", pady=4)

        ttk.Separator(side).pack(fill="x", pady=8)
        for text, cmd in [("Randomize (r)", self.randomize), ("New jitter", self.new_jitter),
                          ("Clear lenses", self.clear), ("Save… (Ctrl+S)", self.save)]:
            ttk.Button(side, text=text, command=cmd).pack(fill="x", pady=2)

        self.status = ttk.Label(side, text="", foreground="#888", wraplength=180)
        self.status.pack(anchor="w", pady=(10, 0))

        c = self.canvas
        c.bind("<Configure>", lambda e: self.redraw())
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", lambda e: setattr(self, "drag", None))
        c.bind("<ButtonPress-3>", self.on_right)
        c.bind("<MouseWheel>", lambda e: self.on_wheel(e, 1 if e.delta > 0 else -1))
        c.bind("<Button-4>", lambda e: self.on_wheel(e, 1))
        c.bind("<Button-5>", lambda e: self.on_wheel(e, -1))
        root.bind("r", lambda e: self.randomize())
        root.bind("<Delete>", lambda e: self.delete_selected())
        root.bind("<Control-s>", lambda e: self.save())
        self.sync_lens_controls()

    # ---------- widgets ----------
    def slider(self, parent, key, label, lo, hi, init, lens=False):
        var = tk.DoubleVar(value=init)
        row = ttk.Frame(parent)
        row.pack(fill="x")
        ttk.Label(row, text=label).pack(side="left")
        val = ttk.Label(row, text="", width=6, anchor="e")
        val.pack(side="right")
        scale = ttk.Scale(parent, from_=lo, to=hi, variable=var, length=180,
                          command=lambda _=None: self.on_slider(key, lens))
        scale.pack(fill="x", pady=(0, 6))
        self.vars[key] = (var, val)
        val.config(text=f"{init:.2f}" if hi <= 4 else f"{init:.0f}")

    def on_slider(self, key, lens):
        var, val = self.vars[key]
        v = var.get()
        val.config(text=f"{v:.2f}" if abs(v) < 5 else f"{v:.0f}")
        if lens:
            if self.selected is None:
                return
            setattr(self.scene.lenses[self.selected], key, v)
        else:
            setattr(self.scene, key, v)
        self.redraw()

    def set_var(self, key, v):
        var, val = self.vars[key]
        var.set(v)
        val.config(text=f"{v:.2f}" if abs(v) < 5 else f"{v:.0f}")

    def sync_lens_controls(self):
        n = len(self.scene.lenses)
        if self.selected is None or self.selected >= n:
            self.selected = None
            self.lens_label.config(text=f"Selected lens: none  ({n} total)")
            return
        l = self.scene.lenses[self.selected]
        self.lens_label.config(text=f"Selected lens: {self.selected + 1} of {n}")
        self.set_var("radius", l.radius)
        self.set_var("strength", l.strength)

    def sync_scene_controls(self):
        for k in ("spacing", "hue", "saturation", "brightness", "line_scale"):
            self.set_var(k, getattr(self.scene, k))
        self.size_var.set(f"{self.scene.width}x{self.scene.height}")

    # ---------- geometry ----------
    def view_scale(self):
        cw = max(self.canvas.winfo_width(), 100)
        ch = max(self.canvas.winfo_height(), 100)
        return min(cw / self.scene.width, ch / self.scene.height)

    def to_scene(self, e):
        s = self.view_scale()
        return e.x / s, e.y / s

    def hit(self, x, y):
        """Index of the lens whose centre is nearest (within 40 preview px)."""
        s = self.view_scale()
        best, bd = None, 40 / s
        for i, l in enumerate(self.scene.lenses):
            d = math.hypot(l.cx - x, l.cy - y)
            if d < bd:
                best, bd = i, d
        return best

    # ---------- mouse ----------
    def on_press(self, e):
        x, y = self.to_scene(e)
        i = self.hit(x, y)
        if i is None:
            r = self.scene.lenses[self.selected].radius if self.selected is not None else 0.3 * max(self.scene.width, self.scene.height)
            self.scene.lenses.append(wm.Lens(x, y, r, 0.45))
            i = len(self.scene.lenses) - 1
        self.selected = i
        l = self.scene.lenses[i]
        self.drag = (x - l.cx, y - l.cy)
        self.sync_lens_controls()
        self.redraw()

    def on_drag(self, e):
        if self.drag is None or self.selected is None:
            return
        x, y = self.to_scene(e)
        l = self.scene.lenses[self.selected]
        l.cx, l.cy = x - self.drag[0], y - self.drag[1]
        self.redraw()

    def on_right(self, e):
        i = self.hit(*self.to_scene(e))
        if i is not None:
            self.selected = i
            self.delete_selected()

    def on_wheel(self, e, direction):
        if self.selected is None:
            return
        l = self.scene.lenses[self.selected]
        if e.state & 0x1:  # shift
            l.strength = max(-0.8, min(0.8, l.strength + 0.03 * direction))
        else:
            l.radius = max(20, l.radius * (1.06 if direction > 0 else 1 / 1.06))
        self.sync_lens_controls()
        self.redraw()

    # ---------- actions ----------
    def set_size(self):
        try:
            w, h = (int(v) for v in self.size_var.get().lower().split("x"))
        except ValueError:
            self.status.config(text="Size must look like 1920x1080")
            return
        sx, sy = w / self.scene.width, h / self.scene.height
        for l in self.scene.lenses:  # keep the composition when resizing
            l.cx *= sx
            l.cy *= sy
            l.radius *= max(sx, sy)
        self.scene.width, self.scene.height = w, h
        self.sync_lens_controls()
        self.redraw()

    def randomize(self):
        seed = random.randrange(1 << 30)
        rng = random.Random(seed)
        self.scene = wm.random_scene(rng, self.scene.width, self.scene.height,
                                     lenses=rng.choice([1, 1, 1, 2, 2, 3]), seed=seed)
        self.selected = 0
        self.sync_scene_controls()
        self.sync_lens_controls()
        self.redraw()

    def new_jitter(self):
        self.scene.seed = random.randrange(1 << 30)
        self.redraw()

    def clear(self):
        self.scene.lenses.clear()
        self.selected = None
        self.sync_lens_controls()
        self.redraw()

    def delete_selected(self):
        if self.selected is not None:
            del self.scene.lenses[self.selected]
            self.selected = len(self.scene.lenses) - 1 if self.scene.lenses else None
            self.sync_lens_controls()
            self.redraw()

    def save(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".png", initialfile=f"warp-{self.scene.seed}.png",
            filetypes=[("PNG", "*.png"), ("SVG", "*.svg")])
        if path:
            wm.save(self.scene, path)
            self.status.config(text=f"Saved {path}")

    # ---------- rendering ----------
    def redraw(self):
        # coalesce bursts of slider/drag events into one render
        if self._pending is None:
            self._pending = self.root.after(15, self._render)

    def _render(self):
        self._pending = None
        s = self.view_scale()
        w, h = max(1, int(self.scene.width * s)), max(1, int(self.scene.height * s))
        surface = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
        ctx = cairo.Context(surface)
        ctx.scale(s, s)
        wm.draw(self.scene, ctx)
        if self.show_guides.get():
            for i, l in enumerate(self.scene.lenses):
                sel = i == self.selected
                ctx.set_source_rgba(1, 0.6, 0.2, 0.7 if sel else 0.25)
                ctx.set_line_width((1.5 if sel else 1) / s)
                ctx.set_dash([6 / s, 6 / s])
                ctx.arc(l.cx, l.cy, l.radius, 0, 2 * math.pi)
                ctx.stroke()
                ctx.set_dash([])
                ctx.arc(l.cx, l.cy, 5 / s, 0, 2 * math.pi)
                ctx.fill()

        buf = io.BytesIO()
        surface.write_to_png(buf)
        self.photo = tk.PhotoImage(data=base64.b64encode(buf.getvalue()))
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
        self.status.config(text=f"{self.scene.width}×{self.scene.height}, "
                                f"{len(self.scene.lenses)} lens(es), seed {self.scene.seed}")


def main():
    root = tk.Tk()
    root.geometry("1200x720")
    try:
        ttk.Style().theme_use("clam")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
