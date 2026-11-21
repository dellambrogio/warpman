"""GPU renderer. Skipped when no OpenGL 3.3 context can be made (e.g. CI without a GPU)."""

import random

import cairo
import numpy as np
import pytest

from warpman import drift
from warpman import scene as wm

gl = pytest.importorskip("warpman.gl")


@pytest.fixture(scope="module")
def renderer():
    try:
        return gl.GLRenderer()
    except Exception as e:
        pytest.skip(f"no GL context: {e}")


def cairo_frame(scene, paint=None):
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, scene.width, scene.height)
    wm.draw(scene, cairo.Context(surface), paint)
    surface.flush()
    stride = surface.get_stride()
    px = np.frombuffer(surface.get_data(), np.uint8).reshape(scene.height, stride // 4, 4)
    return px[:, :scene.width, :3].astype(int)


def gl_frame(renderer, scene, rainbow=None):
    buf = renderer.render(scene, rainbow)
    return np.frombuffer(buf, np.uint8).reshape(scene.height, scene.width, 4)[..., :3].astype(int)


@pytest.mark.parametrize("seed", range(4))
def test_matches_cairo(renderer, seed):
    scene = wm.random_scene(random.Random(seed), 320, 200, lenses=2, pinch_chance=0.5, seed=seed)
    diff = np.abs(gl_frame(renderer, scene) - cairo_frame(scene))
    assert diff.mean() < 0.2
    assert diff.max() <= 8


def test_resizes_between_frames(renderer):
    for w, h in [(160, 100), (333, 211), (160, 100)]:
        scene = wm.random_scene(random.Random(1), w, h, lenses=1)
        assert len(renderer.render(scene)) == w * h * 4


def test_cycle_rainbow_matches_cairo(renderer):
    scene = wm.random_scene(random.Random(2), 320, 200, lenses=2, seed=2)
    rb = drift.Rainbow("cycle", 0.1)
    rb.update(3.0, scene)
    diff = np.abs(gl_frame(renderer, scene, rb) - cairo_frame(scene, rb.paint))
    assert diff.mean() < 0.5


def test_grid_segments_cover_every_line():
    scene = wm.Scene(320, 200, spacing=64)
    lines = wm.grid_arrays(320, 200, 64, (0.0, 0.0), gl.STEP)
    segs, ids = gl.grid_segments(scene)
    assert len(segs) == len(ids) == sum(len(xs) - 1 for _, xs, _ in lines)
    assert set(np.unique(ids)) == set(range(len(lines)))
