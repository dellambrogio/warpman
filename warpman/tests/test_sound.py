"""Sound synthesis, without a player: the engine is fed and rendered by hand."""

import random
import types

import numpy as np
import pytest

from warpman import drift, sound
from warpman import scene as wm


def test_pan_gains_are_equal_power():
    for p in np.linspace(0, 1, 11):
        gl, gr = sound._pan_gains(p)
        assert gl ** 2 + gr ** 2 == pytest.approx(1)
    assert sound._pan_gains(0) == pytest.approx((1, 0))
    assert sound._pan_gains(1) == pytest.approx((0, 1))


def test_bell_decays_and_pans():
    b = sound._bell(440, pan=0.0)
    assert b.shape[1] == 2
    assert np.abs(b[:, 1]).max() == pytest.approx(0, abs=1e-12)     # hard left
    head, tail = np.abs(b[: sound.SR // 10, 0]).max(), np.abs(b[-sound.SR // 10:, 0]).max()
    assert tail < 0.05 * head


def test_swoosh_and_arpeggio_are_bounded():
    for buf in (sound._swoosh(random.Random(1)), sound._arpeggio(220)):
        assert np.isfinite(buf).all() and 0 < np.abs(buf).max() < 1


def test_drone_fades_in_and_dies_after_release():
    d = sound.Drone(110, pinch=False, attack=0.05)
    d.set(1.0, 0.5, 0.5, 1.0)
    out = np.zeros((sound.BLOCK, 2))
    for _ in range(20):
        out[:] = 0
        d.render(out)
    assert np.abs(out).max() > 0.1
    d.gate = 0.0
    for _ in range(int(20 * sound.SR / sound.BLOCK)):          # 20 s of release
        out[:] = 0
        d.render(out)
    assert d.dead


def test_shimmer_is_silent_without_squeeze():
    sh = sound.Shimmer(880)
    out = np.zeros((sound.BLOCK, 2))
    sh.render(out)
    assert not out.any()


def sim(seed=4, lenses=2):
    args = types.SimpleNamespace(seed=seed, lenses=lenses, speed=60, hue=None, saturation=None,
                                 rainbow=None, rainbow_speed=0.1, backend="cairo", status=False)
    return drift.Sim(args, 640, 400)


@pytest.fixture
def engine():
    snd = sound.Sound()
    snd.running = True              # as if start() had launched a player
    return snd


def test_notes_follow_lens_size(engine):
    s = sim(lenses=3)
    engine.update(s)
    notes = {id(l): engine._notes[id(l)] for l in s.scene.lenses}
    by_size = sorted(s.scene.lenses, key=lambda l: -l.radius)
    freqs = [notes[id(l)] for l in by_size]
    assert freqs == sorted(freqs) and len(set(freqs)) == 3     # bigger = lower, all distinct


def test_engine_renders_audio_for_the_lenses(engine):
    s = sim()
    peak = 0.0
    for _ in range(100):
        s.step(1 / 30)
        engine.update(s)
        block = engine._render()
        assert block.shape == (sound.BLOCK, 2) and np.isfinite(block).all()
        peak = max(peak, np.abs(block).max())
    assert peak > 0.01
    assert len(engine._drones) == 2


def test_new_scene_queues_a_cut(engine):
    s = sim()
    engine.update(s)
    assert engine.events.empty()
    s.new_scene(640, 400)
    engine.update(s)
    assert engine.events.get_nowait() == ("cut",)


def test_squeeze_measures_overlap(engine):
    assert engine._measure_squeeze(wm.Scene(640, 400)) == (0.0, 0.5)
    one = wm.Scene(640, 400, lenses=[wm.Lens(320, 200, 200, 0.3)])
    two = wm.Scene(640, 400, lenses=[wm.Lens(300, 200, 250, 0.6), wm.Lens(400, 200, 250, -0.6)])
    a1, _ = engine._measure_squeeze(one)
    a2, pan = engine._measure_squeeze(two)
    assert 0 <= a1 < a2 <= 1 and 0 <= pan <= 1


def test_colour_at_mirrors_the_rainbow(engine):
    scene = wm.Scene(400, 300, lenses=[wm.Lens(100, 100, 100, 0.3)])
    rb = drift.Rainbow("ripple", 0.0)
    rb.update(0.0, scene)
    assert engine._colour_at(rb, 100, 100) == pytest.approx(0)
    assert engine._colour_at(rb, 100 + rb.lam / 2, 100) == pytest.approx(0.5)
    rb = drift.Rainbow("cycle", 0.25)
    rb.update(1.0, scene)
    assert engine._colour_at(rb, 0, 0) == pytest.approx(0.25)
