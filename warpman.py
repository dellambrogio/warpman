#!/usr/bin/env python3
"""warpman - generate warped-grid wallpapers.

A regular grid on black, bent by a few smooth radial "lenses".
Each lens pushes points outward (bulge) or inward (pinch) and fades to
exactly zero at its radius, so lines outside every lens stay straight.

Examples:
    ./warpman.py                         # one random 1280x800 PNG
    ./warpman.py -n 8 --seed 42          # 8 reproducible variations
    ./warpman.py -W 1024 -H 768 --hue 90 --lenses 2
    ./warpman.py --format svg
"""

import argparse
import colorsys
import functools
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

import cairo
import numpy as np


@dataclass
class Lens:
    cx: float
    cy: float
    radius: float
    strength: float  # >0 bulge, <0 pinch; keep |s| < ~0.8 so the grid never folds

    def displace(self, x: float, y: float) -> tuple[float, float]:
        dx, dy = x - self.cx, y - self.cy
        r = math.hypot(dx, dy)
        if r == 0 or r >= self.radius:
            return 0.0, 0.0
        t = r / self.radius
        # t*(1-t^2)^2: zero at centre and edge, smooth (C1) at the edge,
        # peak around t≈0.45 -> matches the look of the originals.
        mag = self.strength * self.radius * t * (1 - t * t) ** 2
        return dx / r * mag, dy / r * mag


def warp(x: float, y: float, lenses: list[Lens]) -> tuple[float, float, float]:
    """Return warped point and displacement magnitude."""
    ox = oy = 0.0
    for lens in lenses:
        dx, dy = lens.displace(x, y)
        ox += dx
        oy += dy
    return x + ox, y + oy, math.hypot(ox, oy)


def warp_array(xs: np.ndarray, ys: np.ndarray, lenses: list[Lens]):
    """Vectorized warp(): arrays of points in, (x, y, displacement magnitude) arrays out."""
    ox = np.zeros_like(xs)
    oy = np.zeros_like(ys)
    for lens in lenses:
        dx, dy = xs - lens.cx, ys - lens.cy
        r = np.hypot(dx, dy)
        inside = (r > 0) & (r < lens.radius)
        if not inside.any():
            continue
        r_in = r[inside]
        t = r_in / lens.radius
        k = lens.strength * lens.radius * t * (1 - t * t) ** 2 / r_in   # mag / r
        ox[inside] += dx[inside] * k
        oy[inside] += dy[inside] * k
    return xs + ox, ys + oy, np.hypot(ox, oy)


def grid_lines(w: int, h: int, spacing: float, offset: tuple[float, float], step: float):
    """Yield polylines (lists of points) for every vertical and horizontal grid line."""
    margin = spacing  # extend past edges so displaced lines still reach the border
    ox, oy = offset
    x = ox % spacing - margin
    while x <= w + margin:
        n = int((h + 2 * margin) / step) + 1
        yield [(x, -margin + i * step) for i in range(n)]
        x += spacing
    y = oy % spacing - margin
    while y <= h + margin:
        n = int((w + 2 * margin) / step) + 1
        yield [(-margin + i * step, y) for i in range(n)]
        y += spacing


def grid_arrays(w: int, h: int, spacing: float, offset: tuple[float, float], step: float):
    """grid_lines() as NumPy arrays: a list of (vertical, xs, ys), same points and order."""
    margin = spacing
    ox, oy = offset
    out = []
    x = ox % spacing - margin          # positions accumulate exactly as in grid_lines()
    while x <= w + margin:
        n = int((h + 2 * margin) / step) + 1
        out.append((True, np.full(n, x), -margin + np.arange(n) * step))
        x += spacing
    y = oy % spacing - margin
    while y <= h + margin:
        n = int((w + 2 * margin) / step) + 1
        out.append((False, -margin + np.arange(n) * step, np.full(n, y)))
        y += spacing
    return out


@functools.lru_cache(maxsize=8)
def _grid_flat(w: int, h: int, spacing: float, offset: tuple[float, float], step: float):
    """grid_arrays() plus all points concatenated; cached, since the undisplaced grid
    only changes with size/spacing/offset (the returned arrays must not be modified)."""
    lines = grid_arrays(w, h, spacing, offset, step)
    return lines, np.concatenate([l[1] for l in lines]), np.concatenate([l[2] for l in lines])


@dataclass
class Scene:
    width: int = 1280
    height: int = 800
    spacing: float = 160
    offset: tuple[float, float] = (0.0, 0.0)
    lenses: list[Lens] = field(default_factory=list)
    hue: float = 280  # degrees
    saturation: float = 0.15
    brightness: float = 1.0
    line_scale: float = 1.0
    seed: int = 0  # drives the per-line width/brightness jitter


def random_scene(rng: random.Random, width=1280, height=800, spacing=None, lenses=1,
                 pinch_chance=0.0, hue=None, random_offset=False, seed=0) -> Scene:
    w, h = width, height
    spacing = spacing or rng.choice([110, 128, 160, 175])
    offset = (rng.uniform(0, spacing), rng.uniform(0, spacing)) if random_offset else (0.0, 0.0)
    ls = []
    for _ in range(lenses):
        radius = rng.uniform(0.22, 0.38) * max(w, h)
        strength = rng.uniform(0.3, 0.55) * (1 if rng.random() > pinch_chance else -1)
        ls.append(Lens(rng.uniform(0.15, 0.85) * w, rng.uniform(0.2, 0.8) * h, radius, strength))
    return Scene(w, h, spacing, offset, ls,
                 hue if hue is not None else rng.uniform(0, 360),
                 rng.uniform(0.08, 0.25), seed=seed)


@dataclass
class LineStyle:
    """One grid line after warping: its samples plus how it should be drawn."""
    vertical: bool
    pos: float           # x of a vertical line / y of a horizontal one, before warping
    xs: np.ndarray       # warped sample points
    ys: np.ndarray
    ds: np.ndarray       # displacement magnitude at each sample
    width: float
    value: float         # HSV value (brightness)
    sat: float


def line_styles(scene: Scene, step: float = 4) -> list[LineStyle]:
    """Warp every grid line and pick its width/brightness; shared by all backends.

    step is the sample spacing along each line in px. The GL backend warps the lines
    itself on the GPU and only needs the styles, so it samples more coarsely.

    Lines are returned vertical first (left to right), then horizontal (top to bottom),
    which is also the drawing order.
    """
    rng = random.Random(scene.seed)
    lenses = scene.lenses
    peak = max((abs(l.strength) * l.radius * 0.286 for l in lenses), default=1) or 1
    lines, fx, fy = _grid_flat(scene.width, scene.height, scene.spacing, tuple(scene.offset), step)
    # warp every point of every line in one vectorized pass
    wx, wy, wd = warp_array(fx, fy, lenses)
    out, start = [], 0
    for vertical, lx, ly in lines:
        end = start + len(lx)
        xs, ys, ds = wx[start:end], wy[start:end], wd[start:end]
        start = end
        # lines that get bent more are drawn a bit thicker/brighter, like the originals
        bend = min(ds.max() / peak, 1.5)
        jitter = rng.uniform(0.85, 1.15)
        out.append(LineStyle(
            vertical, lx[0] if vertical else ly[0], xs, ys, ds,
            width=(0.7 + 1.6 * bend) * jitter * scene.line_scale,
            value=(0.2 + 0.14 * bend) * jitter * scene.brightness,
            sat=min(scene.saturation + 0.1 * bend, 1)))
    return out


BACKGROUND = (0.005, 0.005, 0.005)


def draw(scene: Scene, ctx: cairo.Context, paint=None) -> None:
    """Draw scene in its own pixel coordinates (apply ctx.scale beforehand to resize).

    paint, if given, is called as paint(ctx, value, sat) for each line and sets the
    line's source itself (e.g. a gradient), replacing the plain scene.hue tint.
    """
    ctx.set_source_rgb(*BACKGROUND)
    ctx.paint()
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    ctx.set_line_join(cairo.LINE_JOIN_ROUND)
    for ln in line_styles(scene):
        if paint:
            paint(ctx, ln.value, ln.sat)
        else:
            ctx.set_source_rgb(*colorsys.hsv_to_rgb(scene.hue / 360, ln.sat, min(ln.value, 1)))
        ctx.set_line_width(ln.width)
        # undisplaced runs are collinear: keep only their ends (plus all bent points)
        moved = ln.ds > 0
        keep = moved.copy()
        keep[1:] |= moved[:-1]
        keep[:-1] |= moved[1:]
        keep[0] = keep[-1] = True
        xs, ys = ln.xs[keep], ln.ys[keep]
        ctx.move_to(xs[0], ys[0])
        for x, y in zip(xs[1:].tolist(), ys[1:].tolist()):
            ctx.line_to(x, y)
        ctx.stroke()


def save(scene: Scene, path: Path) -> None:
    """Write scene to path; format from the extension (.png or .svg)."""
    path = Path(path)
    if path.suffix.lower() == ".svg":
        surface = cairo.SVGSurface(str(path), scene.width, scene.height)
        draw(scene, cairo.Context(surface))
    else:
        surface = cairo.ImageSurface(cairo.FORMAT_RGB24, scene.width, scene.height)
        draw(scene, cairo.Context(surface))
        surface.write_to_png(str(path))
    surface.finish()


def render(args, rng: random.Random, path: Path, seed: int) -> None:
    scene = random_scene(rng, args.width, args.height, args.spacing, args.lenses,
                         args.pinch_chance, args.hue, args.random_offset, seed)
    scene.brightness = args.brightness
    scene.line_scale = args.line_scale
    save(scene, path)


def main() -> None:
    p = argparse.ArgumentParser(description="Generate warped-grid wallpapers.")
    p.add_argument("-W", "--width", type=int, default=1280)
    p.add_argument("-H", "--height", type=int, default=800)
    p.add_argument("-n", "--count", type=int, default=1, help="number of images")
    p.add_argument("-s", "--seed", type=int, help="random seed (reproducible output)")
    p.add_argument("-o", "--out", type=Path, default=Path("out"), help="output directory")
    p.add_argument("--format", choices=["png", "svg"], default="png")
    p.add_argument("--spacing", type=float, help="grid spacing in px (default: random)")
    p.add_argument("--lenses", type=int, default=1, help="number of warp lenses")
    p.add_argument("--pinch-chance", type=float, default=0.0, help="probability a lens pinches instead of bulges")
    p.add_argument("--hue", type=float, help="tint hue in degrees 0-360 (default: random)")
    p.add_argument("--brightness", type=float, default=1.0)
    p.add_argument("--line-scale", type=float, default=1.0, help="multiply line widths")
    p.add_argument("--random-offset", action="store_true", help="shift the grid randomly")
    args = p.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    base_seed = args.seed if args.seed is not None else random.randrange(1 << 30)
    for i in range(args.count):
        seed = base_seed + i
        path = args.out / f"warp-{seed}.{args.format}"
        render(args, random.Random(seed), path, seed)
        print(path)


if __name__ == "__main__":
    main()
