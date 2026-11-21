#!/usr/bin/env python3
"""Write golden.txt: reference values from the Python warpman for the C parity tests.

    PYTHONPATH=. .venv/bin/python native/tests/gen_golden.py > native/tests/golden.txt

Each line is `<tag> <values...>`, floats in repr() (shortest round-trip) form. The
C side (test_parity.c) replays the same runs and compares.

Sections:
  rand <seed> ...             random.Random(seed): random() x4, gauss(0, 1.2) x3,
                              _randbelow(5) x4, randrange(1 << 30) x2, uniform(0.85, 1.15)
  case <name> <w> <h> <lenses> <hue|-> <sat|-> <steps> <resize_at> <rw> <rh>
  scene <spacing> <hue> <saturation> <n>    then `lens cx cy r s` per lens
  styles <step-index> <n>     then `style vertical width value sat` per line
  state <step-index> <t>      then `lens cx cy r s` and `drift heading turn` per lens
"""

import argparse
import random
import sys
from types import SimpleNamespace

from warpman import scene as wm
from warpman.drift import Sim

DT = 1 / 24


def rand_section(seed):
    r = random.Random(seed)
    vals = [r.random() for _ in range(4)]
    vals += [r.gauss(0, 1.2) for _ in range(3)]
    vals += [r._randbelow(5) for _ in range(4)]
    vals += [r.randrange(1 << 30) for _ in range(2)]
    vals += [r.uniform(0.85, 1.15)]
    print("rand", seed, *(repr(v) for v in vals))


def styles(sim, step_index):
    st = wm.line_styles(sim.scene, step=16.0)
    print("styles", step_index, len(st))
    for l in st:
        print("style", int(l.vertical), repr(float(l.width)), repr(float(l.value)), repr(float(l.sat)))


def state(sim, step_index):
    print("state", step_index, repr(sim.t))
    for d in sim.drifters:
        l = d.lens
        print("lens", repr(l.cx), repr(l.cy), repr(l.radius), repr(l.strength))
        print("drift", repr(d.heading), repr(d.turn))


def case(name, w, h, lenses, hue, sat, steps, resize_at=0, rw=0, rh=0, seed=42):
    print("case", name, w, h, lenses or 0, "-" if hue is None else repr(hue),
          "-" if sat is None else repr(sat), steps, resize_at, rw, rh, seed)
    args = SimpleNamespace(seed=seed, lenses=lenses, speed=60.0, hue=hue, saturation=sat,
                           rainbow=None, rainbow_speed=0.1, backend="cairo", status=False)
    sim = Sim(args, w, h)
    s = sim.scene
    print("scene", repr(s.spacing), repr(s.hue), repr(s.saturation), len(s.lenses))
    for l in s.lenses:
        print("lens", repr(l.cx), repr(l.cy), repr(l.radius), repr(l.strength))
    styles(sim, 0)
    for i in range(1, steps + 1):
        sim.step(DT)
        if resize_at and i == resize_at:
            sim.resize(rw, rh)
        if i % 100 == 0:
            state(sim, i)
        if i % 600 == 0:
            styles(sim, i)
    print("end")


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    for seed in (42, 0, 7, -7, 2 ** 40 + 5):
        rand_section(seed)
    case("default", 1920, 1080, None, None, None, 2400)
    case("lock", 3840, 1600, None, 120.0, 0.5, 2400)
    case("three", 1920, 1080, 3, 120.0, 0.5, 2400, resize_at=1200, rw=2560, rh=1440)
    case("seed7", 1280, 800, None, None, None, 1200, seed=7)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
