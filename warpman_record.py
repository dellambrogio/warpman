"""warpman record - record the drift window (and its sound) to an MP4 with ffmpeg.

Frames arrive whenever the window happens to draw; they are written at a fixed
frame rate by wall clock (repeating a frame when drawing ran slow), so the video
plays back in real time. Audio blocks are teed from the sound thread. Both go
to ffmpeg through queues and writer threads, so a slow encoder never stalls the
window or the speakers.

The window shows frames held back by the A/V delay (see warpman_drift --av-delay),
while each audio block is made from the live state. Shifting the audio by the
same delay (-itsoffset) puts matching picture and sound together in the file.
"""

import os
import queue
import shutil
import subprocess
import sys
import threading
import time


def _write_all(f, data):
    """Unbuffered writes may be partial; keep going until everything is out."""
    view = memoryview(data)
    while view:
        view = view[f.write(view):]


class Recorder:
    def __init__(self, path, width: int, height: int, fps: float,
                 audio_rate: int | None = None, audio_delay: float = 0.0):
        if not shutil.which("ffmpeg"):
            raise RuntimeError("ffmpeg not found")
        self.path, self.size, self.fps = path, (width, height), fps
        self.t0 = None
        self.frames = 0            # frames handed to ffmpeg so far (including repeats)
        self.owed = 0              # repeats not queued yet because the encoder fell behind
        self.failed = False
        self._video = queue.Queue(maxsize=max(4, int(fps)))   # ~1 s of frames
        self._audio = queue.Queue() if audio_rate else None

        # raw inputs need no probing; without this ffmpeg reads ~5 s of audio while
        # opening it, and reads no video meanwhile (the window's frames back up)
        quick = ["-probesize", "32", "-analyzeduration", "0", "-thread_queue_size", "1024"]
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *quick,
               "-f", "rawvideo", "-pix_fmt", "bgr0", "-s", f"{width}x{height}",
               "-framerate", f"{fps}", "-i", "pipe:0"]
        pass_fds = ()
        if audio_rate:
            rfd, self._afd = os.pipe()
            pass_fds = (rfd,)
            cmd += [*quick, "-itsoffset", f"{audio_delay:.3f}",
                    "-f", "s16le", "-ar", str(audio_rate), "-ac", "2", "-i", f"pipe:{rfd}"]
        # yuv420p needs even dimensions; a window can be any size
        cmd += ["-vf", "crop=trunc(iw/2)*2:trunc(ih/2)*2",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]
        if audio_rate:
            # -shortest: the sound runs ahead of the picture by the delay, so end with the video
            cmd += ["-c:a", "aac", "-b:a", "192k", "-shortest"]
        cmd += ["-movflags", "+faststart", str(path)]

        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, bufsize=0, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.PIPE, pass_fds=pass_fds)
        if audio_rate:
            os.close(rfd)          # ffmpeg has its own copy now
        self._threads = [threading.Thread(target=self._write_video, daemon=True)]
        if audio_rate:
            self._threads.append(threading.Thread(target=self._write_audio, daemon=True))
        for t in self._threads:
            t.start()

    # ---- producers (window thread / sound thread) -------------------------------

    def frame(self, data: bytes):
        """Offer the frame now on screen; written as many times as wall clock says."""
        now = time.monotonic()
        if self.t0 is None:
            self.t0 = now
        due = int((now - self.t0) * self.fps) + 1
        new = max(0, min(due - self.frames, int(self.fps)))   # frames the clock wants now
        if new == 0 and self.owed == 0:
            return                 # drawing faster than the recording frame rate
        self.frames += new
        count = new + self.owed
        try:
            self._video.put_nowait((data, count))
            self.owed = 0
        except queue.Full:         # encoder behind: the next frame offered is repeated instead
            self.owed = count

    def audio(self, pcm: bytes):
        if self._audio is not None and self.t0 is not None:
            self._audio.put_nowait(pcm)

    # ---- writer threads ---------------------------------------------------------

    def _write_video(self):
        try:
            while (item := self._video.get()) is not None:
                data, count = item
                for _ in range(count):
                    _write_all(self.proc.stdin, data)
            self.proc.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            self.failed = True

    def _write_audio(self):
        try:
            with os.fdopen(self._afd, "wb", buffering=0) as f:
                while (pcm := self._audio.get()) is not None:
                    _write_all(f, pcm)
        except (BrokenPipeError, OSError, ValueError):
            self.failed = True

    # ---- finishing --------------------------------------------------------------

    def stop(self, wait: bool = False):
        """Finish the file; in the background unless wait is set."""
        def finish():
            self._video.put(None)
            if self._audio is not None:
                self._audio.put(None)
            deadline = time.monotonic() + 10
            for t in self._threads:
                t.join(max(0.1, deadline - time.monotonic()))
            if any(t.is_alive() for t in self._threads):
                self.proc.kill()        # encoder stuck: don't hang the app on exit
                self.failed = True
            err = self.proc.stderr.read().decode(errors="replace").strip()
            code = self.proc.wait()
            secs = self.frames / self.fps
            if code == 0 and not self.failed:
                print(f"recording saved: {self.path} ({secs:.1f} s)", file=sys.stderr, flush=True)
            else:
                print(f"recording failed ({code}): {err.splitlines()[-1] if err else 'ffmpeg exited'}",
                      file=sys.stderr, flush=True)
        if wait:
            finish()
        else:
            threading.Thread(target=finish, daemon=False).start()
