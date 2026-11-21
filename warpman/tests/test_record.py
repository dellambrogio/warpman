import shutil
import subprocess

import pytest

from warpman import record


class Trickle:
    """A file that accepts at most n bytes per write, like an unbuffered pipe can."""

    def __init__(self, n):
        self.n, self.data = n, bytearray()

    def write(self, view):
        chunk = bytes(view[: self.n])
        self.data += chunk
        return len(chunk)


def test_write_all_finishes_partial_writes():
    f = Trickle(7)
    record._write_all(f, bytes(range(100)))
    assert f.data == bytes(range(100))


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_frames_follow_the_wall_clock(monkeypatch):
    """Frame pacing alone, with ffmpeg and the writer threads left out."""
    clock = Clock()
    monkeypatch.setattr(record.time, "monotonic", clock)
    rec = record.Recorder.__new__(record.Recorder)
    rec.fps, rec.t0, rec.frames, rec.owed = 10, None, 0, 0
    rec._video = record.queue.Queue(maxsize=100)

    rec.frame(b"a")                  # first frame
    clock.t += 0.05
    rec.frame(b"b")                  # drawing faster than 10 fps: dropped
    clock.t += 0.30
    rec.frame(b"c")                  # 0.35 s in: due frame 4 -> three frames of "c"
    got = []
    while not rec._video.empty():
        got.append(rec._video.get())
    assert got == [(b"a", 1), (b"c", 3)]
    assert rec.frames == 4


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_records_a_playable_mp4(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(record.time, "monotonic", clock)
    path = tmp_path / "clip.mp4"
    w, h = 64, 48
    rec = record.Recorder(path, w, h, fps=8)
    for i in range(8):
        rec.frame(bytes([i * 20]) * (w * h * 4))
        clock.t += 0.125                # exact in binary, so no frame lands a hair early
    rec.stop(wait=True)
    assert not rec.failed
    if shutil.which("ffprobe"):
        out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                              "-show_entries", "stream=nb_read_frames,width,height",
                              "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout
        assert out.strip() == f"{w},{h},8"
    else:
        assert path.stat().st_size > 0
