import math
import random

import cairo
import numpy as np
import pytest

from warpman import scene as wm


def test_lens_displacement_vanishes_at_centre_edge_and_outside():
    lens = wm.Lens(100, 100, 50, 0.5)
    assert lens.displace(100, 100) == (0.0, 0.0)
    assert lens.displace(150, 100) == (0.0, 0.0)
    assert lens.displace(300, 300) == (0.0, 0.0)


def test_bulge_pushes_out_and_pinch_pulls_in():
    dx, dy = wm.Lens(0, 0, 100, 0.5).displace(30, 0)
    assert dx > 0 and dy == 0
    dx, _ = wm.Lens(0, 0, 100, -0.5).displace(30, 0)
    assert dx < 0


def test_displacement_follows_the_falloff_formula():
    r, R, s = 40.0, 100.0, 0.4
    dx, dy = wm.Lens(0, 0, R, s).displace(0, r)
    t = r / R
    assert dx == 0
    assert dy == pytest.approx(s * R * t * (1 - t * t) ** 2)


def test_warp_sums_lenses():
    a, b = wm.Lens(0, 0, 100, 0.3), wm.Lens(60, 0, 100, -0.2)
    x, y, mag = wm.warp(30, 10, [a, b])
    ax, ay = a.displace(30, 10)
    bx, by = b.displace(30, 10)
    assert (x, y) == pytest.approx((30 + ax + bx, 10 + ay + by))
    assert mag == pytest.approx(math.hypot(ax + bx, ay + by))


def test_warp_array_matches_scalar_warp():
    rng = np.random.default_rng(0)
    xs, ys = rng.uniform(-50, 700, 500), rng.uniform(-50, 450, 500)
    lenses = [wm.Lens(200, 150, 180, 0.5), wm.Lens(400, 250, 120, -0.4)]
    wx, wy, wd = wm.warp_array(xs, ys, lenses)
    for i in range(0, 500, 37):
        assert (wx[i], wy[i], wd[i]) == pytest.approx(wm.warp(xs[i], ys[i], lenses))


def test_grid_arrays_match_grid_lines():
    args = (300, 200, 64.0, (10.0, 5.0), 4.0)
    lines = list(wm.grid_lines(*args))
    arrays = wm.grid_arrays(*args)
    assert len(lines) == len(arrays)
    for pts, (_, xs, ys) in zip(lines, arrays):
        assert np.allclose(np.array(pts), np.stack([xs, ys], axis=1))


def test_grid_covers_the_frame_with_a_margin():
    arrays = wm.grid_arrays(300, 200, 50.0, (0.0, 0.0), 4.0)
    vx = [xs[0] for vertical, xs, _ in arrays if vertical]
    hy = [ys[0] for vertical, _, ys in arrays if not vertical]
    assert min(vx) < 0 and max(vx) > 300
    assert min(hy) < 0 and max(hy) > 200
    assert np.allclose(np.diff(vx), 50)


def test_random_scene_is_reproducible_and_honours_arguments():
    a = wm.random_scene(random.Random(7), 800, 600, lenses=3, hue=42, seed=7)
    b = wm.random_scene(random.Random(7), 800, 600, lenses=3, hue=42, seed=7)
    assert a == b
    assert (a.width, a.height, a.hue, len(a.lenses)) == (800, 600, 42, 3)
    for l in a.lenses:
        assert 0 < l.cx < 800 and 0 < l.cy < 600 and l.strength > 0


def test_random_scene_pinch_chance_one_pinches_everything():
    s = wm.random_scene(random.Random(1), lenses=4, pinch_chance=1.0)
    assert all(l.strength < 0 for l in s.lenses)


def test_line_styles_order_and_bend():
    flat = wm.Scene(400, 300, spacing=100)
    styles = wm.line_styles(flat)
    verticals = [s.vertical for s in styles]
    assert verticals == sorted(verticals, reverse=True)      # vertical lines come first
    assert all(s.ds.max() == 0 for s in styles)

    bent = wm.Scene(400, 300, spacing=100, lenses=[wm.Lens(200, 150, 150, 0.5)])
    widths = {s.pos: s.width for s in wm.line_styles(bent) if s.vertical}
    flat_widths = {s.pos: s.width for s in styles if s.vertical}
    # same seed -> same jitter, so any difference comes from the bend
    assert widths[200] > flat_widths[200]


def test_line_styles_are_deterministic_per_seed():
    s = wm.random_scene(random.Random(3), 320, 200, lenses=2, seed=3)
    a = [(l.width, l.value) for l in wm.line_styles(s)]
    b = [(l.width, l.value) for l in wm.line_styles(s)]
    assert a == b


def pixels(surface):
    surface.flush()
    w, h, stride = surface.get_width(), surface.get_height(), surface.get_stride()
    return np.frombuffer(surface.get_data(), np.uint8).reshape(h, stride // 4, 4)[:, :w, :3]


def test_draw_paints_background_and_lines():
    scene = wm.Scene(200, 120, spacing=50, lenses=[wm.Lens(100, 60, 60, 0.5)])
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 200, 120)
    wm.draw(scene, cairo.Context(surface))
    px = pixels(surface)
    background = round(wm.BACKGROUND[0] * 255)
    assert np.median(px) <= background + 1          # mostly black
    assert px.max() > 30                            # but the lines show


def test_draw_custom_paint_is_called_per_line():
    calls = []

    def paint(ctx, value, sat):
        calls.append((value, sat))
        ctx.set_source_rgb(1, 0, 0)

    scene = wm.Scene(200, 120, spacing=50)
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 200, 120)
    wm.draw(scene, cairo.Context(surface), paint)
    assert len(calls) == len(wm.line_styles(scene))
    px = pixels(surface)
    assert px[..., 2].max() > 100 and px[..., 1].max() < 10      # BGR: red lines only


@pytest.mark.parametrize("ext, magic", [(".png", b"\x89PNG"), (".svg", b"<?xml")])
def test_save(tmp_path, ext, magic):
    path = tmp_path / f"out{ext}"
    wm.save(wm.Scene(64, 48, spacing=16), path)
    assert path.read_bytes().startswith(magic)
