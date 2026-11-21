"""CPU / battery status: the small text in the corner, and on-battery detection."""

import colorsys
import glob
import time

import cairo


def on_battery() -> bool:
    """True when a battery is present and no external power (mains / USB-PD) is online."""
    battery, external, discharging = False, None, False
    for d in glob.glob("/sys/class/power_supply/*"):
        try:
            with open(f"{d}/type") as f:
                kind = f.read().strip()
            if kind == "Battery":
                battery = True
                with open(f"{d}/status") as f:
                    discharging |= f.read().strip() == "Discharging"
            elif kind in ("Mains", "USB", "USB_C", "USB_PD"):
                with open(f"{d}/online") as f:
                    external = bool(external) or f.read().strip() == "1"
        except OSError:
            continue
    if not battery:
        return False
    # No adapter reported at all: go by the battery itself. ("Not charging" can mean
    # plugged in with a charge limit, so only "Discharging" counts.)
    return discharging if external is None else not external


class Status:
    """CPU usage (from /proc/stat) and battery level (from /sys), refreshed every few seconds."""

    def __init__(self, interval: float = 2.0):
        self.interval = interval
        self.next = time.monotonic() + 1.0     # let a real CPU sample accumulate first
        self.prev_cpu = self._cpu_times()
        self.text = ""

    @staticmethod
    def _cpu_times():
        try:
            with open("/proc/stat") as f:
                vals = [int(v) for v in f.readline().split()[1:]]
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)   # idle + iowait
            return sum(vals), idle
        except (OSError, ValueError, IndexError):
            return None

    @staticmethod
    def _battery():
        parts = []
        for d in sorted(glob.glob("/sys/class/power_supply/*")):
            try:
                with open(f"{d}/type") as f:
                    if f.read().strip() != "Battery":
                        continue
                with open(f"{d}/capacity") as f:
                    cap = int(f.read())
                with open(f"{d}/status") as f:
                    st = f.read().strip()
            except (OSError, ValueError):
                continue
            mark = {"Charging": " +", "Discharging": " -"}.get(st, "")
            parts.append(f"BAT {cap}%{mark}")
        return "  ".join(parts)

    def update(self):
        now = time.monotonic()
        if now < self.next:
            return
        self.next = now + self.interval
        fields = []
        cur = self._cpu_times()
        if cur and self.prev_cpu:
            total, idle = cur[0] - self.prev_cpu[0], cur[1] - self.prev_cpu[1]
            if total > 0:
                fields.append(f"CPU {100 * (total - idle) / total:.0f}%")
        self.prev_cpu = cur
        bat = self._battery()
        if bat:
            fields.append(bat)
        self.text = "   ".join(fields)

    @staticmethod
    def font_size(h: int) -> float:
        return max(8, h / 110)

    def draw(self, ctx: cairo.Context, w: int, h: int, hue: float, size: float | None = None):
        """Draw at the bottom-right of a w x h area; size defaults to font_size(h)."""
        self.update()
        if not self.text:
            return
        size = size or self.font_size(h)
        ctx.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        ctx.set_font_size(size)
        ext = ctx.text_extents(self.text)
        margin = size * 1.5
        x = w - margin - ext.x_advance
        y = h - margin
        ctx.set_source_rgba(0, 0, 0, 0.55)        # soft backing so text stays legible over lines
        pad = size * 0.3
        ctx.rectangle(x - pad, y + ext.y_bearing - pad, ext.x_advance + 2 * pad, ext.height + 2 * pad)
        ctx.fill()
        r, g, b = colorsys.hsv_to_rgb(hue / 360, 0.3, 0.85)
        ctx.set_source_rgb(r, g, b)
        ctx.move_to(x, y)
        ctx.show_text(self.text)
