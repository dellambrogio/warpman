import cairo
import pytest

from warpman import status


def supply(root, name, **files):
    d = root / name
    d.mkdir()
    for key, value in files.items():
        (d / key).write_text(f"{value}\n")


@pytest.fixture
def power(tmp_path, monkeypatch):
    """A fake /sys/class/power_supply."""
    monkeypatch.setattr(status.glob, "glob", lambda pattern: sorted(str(p) for p in tmp_path.iterdir()))
    return tmp_path


def test_no_battery_is_never_on_battery(power):
    supply(power, "AC", type="Mains", online=0)
    assert not status.on_battery()


def test_unplugged_laptop_is_on_battery(power):
    supply(power, "BAT0", type="Battery", status="Discharging", capacity=50)
    supply(power, "AC", type="Mains", online=0)
    assert status.on_battery()


def test_any_online_adapter_means_external_power(power):
    supply(power, "BAT0", type="Battery", status="Discharging", capacity=50)
    supply(power, "AC", type="Mains", online=0)
    supply(power, "ucsi-source-psy-1", type="USB", online=1)
    assert not status.on_battery()


@pytest.mark.parametrize("state, expected", [("Discharging", True), ("Not charging", False),
                                             ("Charging", False)])
def test_without_adapters_the_battery_status_decides(power, state, expected):
    supply(power, "BAT0", type="Battery", status=state, capacity=80)
    assert status.on_battery() is expected


def test_battery_text(power):
    supply(power, "BAT0", type="Battery", status="Charging", capacity=81)
    supply(power, "BAT1", type="Battery", status="Full", capacity=100)
    assert status.Status._battery() == "BAT 81% +  BAT 100%"


def test_update_reports_cpu_and_battery(power, monkeypatch):
    supply(power, "BAT0", type="Battery", status="Discharging", capacity=42)
    samples = iter([(1000, 800), (2000, 1550)])           # 1000 ticks, 750 idle -> 25%
    monkeypatch.setattr(status.Status, "_cpu_times", staticmethod(lambda: next(samples)))
    st = status.Status()
    st.next = 0                                             # due now
    st.update()
    assert st.text == "CPU 25%   BAT 42% -"


def test_update_is_rate_limited(monkeypatch):
    st = status.Status(interval=60)
    st.text = "cached"
    st.update()                                             # first sample not due yet
    assert st.text == "cached"


def test_cpu_times_reads_proc_stat():
    total, idle = status.Status._cpu_times()
    assert total >= idle >= 0


def test_draw_puts_text_in_the_bottom_right():
    st = status.Status()
    st.next = float("inf")
    st.text = "CPU 1%"
    w, h = 400, 200
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    st.draw(cairo.Context(surface), w, h, hue=120)
    surface.flush()
    data = surface.get_data()
    stride = surface.get_stride()
    lit = [(x, y) for y in range(h) for x in range(w) if data[y * stride + 4 * x + 3]]
    assert lit
    assert min(x for x, _ in lit) > w / 2 and min(y for _, y in lit) > h / 2
