#!/usr/bin/env python3
"""warpman still - generate warped-grid wallpapers (PNG or SVG).

Examples:
    apps/still.py                         # one random 1280x800 PNG in out/
    apps/still.py -n 8 --seed 42          # 8 reproducible variations
    apps/still.py -W 1024 -H 768 --hue 90 --lenses 2
    apps/still.py --format svg
"""

import argparse
import random
from pathlib import Path

from warpman.scene import random_scene, save


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
