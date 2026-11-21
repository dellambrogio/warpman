import random
import types

import pytest

from warpman import drift
from warpman import scene as wm


def sim_args(**kw):
    args = dict(seed=5, lenses=2, speed=60, hue=None, saturation=None,
                rainbow=None, rainbow_speed=0.1, backend="cairo", status=False)
    args.update(kw)
    return types.SimpleNamespace(**args)


def lens_state(sim):
    return [(l.cx, l.cy, l.radius, l.strength) for l in sim.scene.lenses]


def test_seed_makes_the_first_scene_reproducible():
    a, b = drift.Sim(sim_args(), 640, 400), drift.Sim(sim_args(), 640, 400)
    assert lens_state(a) == lens_state(b)
    for _ in range(50):
        a.step(1 / 30)
        b.step(1 / 30)
    assert lens_state(a) == lens_state(b)


def test_hue_and_saturation_overrides_apply_to_every_scene():
    sim = drift.Sim(sim_args(hue=120, saturation=0.5), 320, 200)
    sim.new_scene(320, 200)
    assert (sim.scene.hue, sim.scene.saturation) == (120, 0.5)


def test_lenses_move_and_stay_on_screen():
    sim = drift.Sim(sim_args(lenses=3, seed=11), 640, 400)
    start = lens_state(sim)
    for _ in range(30 * 60):                          # one simulated minute
        sim.step(1 / 30)
        for l in sim.scene.lenses:
            assert -0.05 * 640 < l.cx < 1.05 * 640
            assert -0.05 * 400 < l.cy < 1.05 * 400
    assert lens_state(sim) != start
    assert sim.t == pytest.approx(60)


def test_drifter_breathes_around_its_base_size():
    lens = wm.Lens(320, 200, 100, 0.4)
    d = drift.Drifter(lens, random.Random(0), speed=60)
    radii = []
    for i in range(3000):
        d.step(0.05, i * 0.05, 640, 400)
        radii.append(lens.radius)
    assert 0.9 * 100 < min(radii) < max(radii) < 1.1 * 100


def test_resize_scales_lens_positions():
    sim = drift.Sim(sim_args(), 640, 400)
    before = [(l.cx, l.cy) for l in sim.scene.lenses]
    sim.resize(1280, 400)
    assert (sim.scene.width, sim.scene.height) == (1280, 400)
    for (x0, y0), l in zip(before, sim.scene.lenses):
        assert (l.cx, l.cy) == pytest.approx((2 * x0, y0))


def test_render_state_draws_the_past_and_restores_the_present():
    sim = drift.Sim(sim_args(), 160, 100)
    past = sim.state()
    past_frame = bytes(sim.render().get_data())
    for _ in range(60):
        sim.step(1 / 30)
    live = lens_state(sim)
    assert bytes(sim.render_state(past).get_data()) == past_frame
    assert lens_state(sim) == live


def test_render_returns_a_frame_of_the_scene_size():
    sim = drift.Sim(sim_args(rainbow="wave"), 160, 100)
    surface = sim.render()
    assert (surface.get_width(), surface.get_height()) == (160, 100)


def test_next_rainbow_cycles_through_all_modes():
    sim = drift.Sim(sim_args(), 160, 100)
    seen = []
    for _ in range(len(drift.RAINBOW_MODES) + 1):
        sim.next_rainbow()
        seen.append(sim.rainbow.mode if sim.rainbow else None)
    assert seen == [*drift.RAINBOW_MODES, None]


@pytest.mark.parametrize("mode", drift.RAINBOW_MODES)
def test_rainbow_update_sets_up_its_geometry(mode):
    rb = drift.Rainbow(mode, speed=0.25)
    scene = wm.Scene(400, 300, lenses=[wm.Lens(100, 80, 100, 0.3)])
    rb.update(6.0, scene)
    assert rb.phase == pytest.approx(0.5)
    if mode == "wave":
        assert rb.pattern is not None and len(rb.wave_vec) == 2
    elif mode == "ripple":
        assert rb.center == (100, 80) and rb.lam > 0
    else:
        assert rb.pattern is None


def test_cairo_backend_needs_no_renderer():
    assert drift.make_backend("cairo") is None
