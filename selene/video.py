"""Video takes: Selene's own Terminal window, with the take's WAV as its soundtrack.

While a take is being recorded, macOS's `screencapture -v -l<window>` records
just Selene's window. The WAV is still SuperDirt's own recording (recorder.py),
so the sound is clean. Afterwards ffmpeg trims the video to the instant the WAV
began and puts the two together as an MP4 next to the WAV.

Sync: screencapture starts recording 0.13-0.19 s after it's launched, and the
delay varies from take to take, too much for pattern highlights to land on the
beat. So while the take is arming, the app flashes its status bar magenta at a
moment it notes (`mark`), and `find_marker` finds that flash in the video.
That fixes where video time 0 sits on the wall clock. The flash comes before
the sound starts, so the trim cuts it out.

Terminal.app only for now: it can name the window that holds our tty.
"""

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# The flash: pure magenta (xterm-256 colour 201, so Terminal.app's 256 colours
# show it exactly). find_marker looks for a band of rows this colour.
MARKER = "#ff00ff"
# How long after launching screencapture to flash, comfortably after the
# 0.13-0.19 s it takes to start; and how long the flash stays up.
MARK_AFTER = 0.5
MARK_FOR = 0.15
# Where video time 0 sits if the flash can't be found (window covered, or a
# thumbnail in Stage Manager's strip): the typical delay, good to ~±30 ms.
TYPICAL_START = 0.16
# The scaled frame find_marker inspects: wide enough to average out text, tall
# enough that the one-row bar fills whole rows of it.
SCAN_W, SCAN_H = 48, 192


class VideoError(Exception):
    pass


def unavailable() -> str | None:
    """Why video takes can't work here, or None if they can."""
    if sys.platform != "darwin":
        return "video takes need macOS"
    if os.environ.get("TERM_PROGRAM") != "Apple_Terminal":
        return "video takes work in macOS's Terminal app for now"
    if not shutil.which("ffmpeg"):
        return "video takes need ffmpeg (brew install ffmpeg)"
    if not shutil.which("screencapture"):
        return "screencapture is missing"
    return None


def our_tty() -> str | None:
    for fd in (sys.__stdout__, sys.__stdin__, sys.__stderr__):
        try:
            return os.ttyname(fd.fileno())
        except (AttributeError, OSError, ValueError):
            continue
    return None


def window_script(tty: str) -> str:
    return (
        'tell application "Terminal"\n'
        "  repeat with w in windows\n"
        "    repeat with t in tabs of w\n"
        f"      if tty of t is {json.dumps(tty)} then return id of w\n"
        "    end repeat\n"
        "  end repeat\n"
        "end tell\n"
        'return ""')


def selene_window() -> int | None:
    """The CGWindowID of the Terminal window running Selene."""
    tty = our_tty()
    if not tty:
        return None
    try:
        out = subprocess.run(["osascript", "-e", window_script(tty)], capture_output=True,
                             text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    return int(out) if out.isdigit() else None


@dataclass
class Take:
    """One screen recording, from launch to the finished MP4."""
    window: int
    mov: Path
    launched: float = 0.0
    marked: float | None = None  # when the flash went up (wall clock)
    audio_start: float | None = None  # when the WAV's first sample played
    proc: asyncio.subprocess.Process | None = field(default=None, repr=False)

    async def start(self) -> None:
        self.mov.unlink(missing_ok=True)
        self.launched = time.time()
        self.proc = await asyncio.create_subprocess_exec(
            "screencapture", "-v", "-x", f"-l{self.window}", str(self.mov),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT)

    async def stop(self) -> None:
        """End the recording; screencapture finishes the file on SIGINT."""
        if not self.proc or self.proc.returncode is not None:
            return
        self.proc.send_signal(signal.SIGINT)
        try:
            await asyncio.wait_for(self.proc.wait(), 20)
        except asyncio.TimeoutError:
            self.proc.kill()
            await self.proc.wait()

    def kill(self) -> None:
        """Quitting: never leave a screen recording running behind us."""
        if self.proc and self.proc.returncode is None:
            self.proc.kill()

    def discard(self) -> None:
        self.kill()
        self.mov.unlink(missing_ok=True)

    async def finish(self, wav: Path, mp4: Path) -> str:
        """Find the flash, trim, mux. Returns a note on the sync for the log."""
        if not self.mov.exists() or self.mov.stat().st_size == 0:
            raise VideoError("macOS didn't record the window; allow Terminal in System Settings › "
                             "Privacy & Security › Screen Recording")
        if self.audio_start is None:
            raise VideoError("the take never started")
        found = await asyncio.to_thread(find_marker, self.mov, MARK_AFTER + 3)
        if found is not None and self.marked is not None:
            zero, note = self.marked - found, "synced"
        else:
            zero, note = self.launched + TYPICAL_START, (
                "sync estimated (±30 ms): the flash wasn't visible, so keep Selene's window "
                "in front while recording")
        await asyncio.to_thread(mux, self.mov, wav, mp4, self.audio_start - zero)
        self.mov.unlink(missing_ok=True)
        return note


def find_marker(mov: Path, within: float = 5.0) -> float | None:
    """Video time (s) of the first frame showing the flash: a band of rows
    that are pure magenta across most of the window's width."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "info", "-t", f"{within}", "-i", str(mov),
         "-vf", f"scale={SCAN_W}:{SCAN_H}:flags=area,showinfo", "-fps_mode", "passthrough", "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-"], capture_output=True, timeout=120)
    times = [float(line.split("pts_time:")[1].split()[0])
             for line in proc.stderr.decode(errors="replace").splitlines()
             if "Parsed_showinfo" in line and "pts_time:" in line]
    size = SCAN_W * SCAN_H * 3
    frames = [proc.stdout[i:i + size] for i in range(0, len(proc.stdout) - size + 1, size)]
    for t, frame in zip(times, frames):
        if _shows_marker(frame):
            return t
    return None


def _shows_marker(frame: bytes) -> bool:
    for row in range(SCAN_H):
        line = frame[row * SCAN_W * 3:(row + 1) * SCAN_W * 3]
        hits = sum(1 for i in range(0, len(line), 3)
                   if line[i] > 180 and line[i + 2] > 180 and line[i + 1] < 90)
        if hits > SCAN_W * 0.6:
            return True
    return False


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=60)
    try:
        return float(out.stdout.strip())
    except ValueError as e:
        raise VideoError(f"couldn't read {path.name}") from e


def mux(mov: Path, wav: Path, mp4: Path, offset: float) -> None:
    """mov trimmed by offset seconds (where the WAV starts), the WAV as its
    sound, cut to the WAV's length: an MP4 that plays anywhere."""
    if offset < 0:
        raise VideoError(f"the video started {-offset:.2f} s after the sound")
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-ss", f"{offset:.4f}", "-i", str(mov), "-i", str(wav),
         "-map", "0:v:0", "-map", "1:a:0", "-t", f"{duration(wav):.4f}",
         "-vf", "fps=60,scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-tune", "stillimage",
         "-c:a", "aac", "-b:a", "320k", "-movflags", "+faststart", str(mp4)],
        capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        raise VideoError("ffmpeg: " + (proc.stderr.strip().splitlines() or ["failed"])[-1])
