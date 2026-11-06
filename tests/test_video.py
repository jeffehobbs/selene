"""Video takes (selene/video.py): finding the sync flash and lining the video
up with the WAV. The real screen capture needs a Mac with a window to film;
these use synthetic videos made by ffmpeg."""

import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import pytest

from selene import video

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def make_video(path: Path, seconds: float, boxes: list[str]) -> None:
    """A dark 320x200 VFR-free clip with drawbox filters (ffmpeg syntax)."""
    vf = ",".join(boxes) if boxes else "null"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c=0x202830:s=320x200:r=60:d={seconds}", "-vf", vf,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)


def make_wav(path: Path, seconds: float, clicks: list[float]) -> None:
    rate = 48000
    samples = [0.0] * int(rate * seconds)
    for at in clicks:
        start = int(at * rate)
        for k in range(240):
            samples[start + k] = 0.8 * math.sin(2 * math.pi * 1000 * k / rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(x * 32767)) for x in samples))


def flash_times(mp4: Path) -> list[float]:
    """When the picture turns white (frame times)."""
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(mp4), "-vf",
                          "scale=32:20,signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-",
                          "-f", "null", "-"], capture_output=True, text=True).stdout
    import re
    frames = [(float(t), float(y)) for t, y in re.findall(r"pts_time:([\d.]+).*?YAVG=([\d.]+)", out, re.S)]
    on, prev = [], False
    for t, y in frames:
        if (y > 128) and not prev:
            on.append(t)
        prev = y > 128
    return on


def click_times(mp4: Path) -> list[float]:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(mp4), "-map", "0:a", "-ac", "1",
                          "-ar", "48000", "-f", "s16le", "-"], capture_output=True).stdout
    a = struct.unpack(f"<{len(raw) // 2}h", raw)
    out, i = [], 0
    while i < len(a):
        if abs(a[i]) > 8000:
            out.append(i / 48000)
            i += 12000
        else:
            i += 1
    return out


def test_the_window_script_asks_for_our_tty():
    script = video.window_script('/dev/ttys006')
    assert 'tty of t is "/dev/ttys006"' in script and "return id of w" in script


def test_unavailable_says_why(monkeypatch):
    monkeypatch.setattr(video.sys, "platform", "linux")
    assert "macOS" in video.unavailable()
    monkeypatch.setattr(video.sys, "platform", "darwin")
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    assert "Terminal" in video.unavailable()
    monkeypatch.setenv("TERM_PROGRAM", "Apple_Terminal")
    monkeypatch.setattr(video.shutil, "which", lambda name: None if name == "ffmpeg" else "/x")
    assert "ffmpeg" in video.unavailable()
    monkeypatch.setattr(video.shutil, "which", lambda name: "/x")
    assert video.unavailable() is None


@needs_ffmpeg
def test_find_marker_sees_the_magenta_bar(tmp_path):
    clip = tmp_path / "c.mov"
    make_video(clip, 2, ["drawbox=x=0:y=12:w=iw:h=6:color=0xff00ff:t=fill:enable='between(t,0.75,0.9)'",
                         # magenta "text" further down: thin strokes, never a solid row
                         *[f"drawbox=x={10 + 12 * k}:y=60:w=2:h=14:color=0xff00ff:t=fill" for k in range(20)]])
    assert video.find_marker(clip) == pytest.approx(0.75, abs=0.02)


@needs_ffmpeg
def test_find_marker_ignores_magenta_text_and_returns_none_without_a_flash(tmp_path):
    clip = tmp_path / "c.mov"
    make_video(clip, 1, ["drawbox=x=20:y=40:w=40:h=10:color=0xff00ff:t=fill"])  # a small patch
    assert video.find_marker(clip) is None


@needs_ffmpeg
def test_mux_lines_the_picture_up_with_the_sound(tmp_path):
    clip, wav, mp4 = tmp_path / "c.mov", tmp_path / "t.wav", tmp_path / "t.mp4"
    # The sound starts 1.3 s into the film; a flash at 2.0 s of film is a click at 0.7 s of sound.
    make_video(clip, 5, ["drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='between(t,2.0,2.2)'"])
    make_wav(wav, 3, [0.7])
    video.mux(clip, wav, mp4, 1.3)
    assert video.duration(mp4) == pytest.approx(3, abs=0.05)
    assert flash_times(mp4) == [pytest.approx(0.7, abs=0.017)]
    assert click_times(mp4) == [pytest.approx(0.7, abs=0.002)]


@needs_ffmpeg
async def test_finish_uses_the_flash_to_place_video_time_zero(tmp_path):
    clip, wav, mp4 = tmp_path / ".t.capture.mov", tmp_path / "t.wav", tmp_path / "t.mp4"
    # Film starts at wall time 100.0 (unknown to Selene); the flash shows at film 0.6 s
    # (wall 100.6, which Selene noted); the sound starts at wall 101.0; a white frame at
    # film 1.8 s is wall 101.8, so 0.8 s into the sound, where the click is.
    make_video(clip, 4, ["drawbox=x=0:y=10:w=iw:h=6:color=0xff00ff:t=fill:enable='between(t,0.6,0.75)'",
                         "drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='between(t,1.8,2.0)'"])
    make_wav(wav, 2, [0.8])
    take = video.Take(1, clip, launched=99.7, marked=100.6, audio_start=101.0)
    assert await take.finish(wav, mp4) == "synced"
    assert not clip.exists()
    assert flash_times(mp4) == [pytest.approx(0.8, abs=0.017)]


@needs_ffmpeg
async def test_finish_estimates_without_a_flash_and_explains_a_missing_film(tmp_path):
    clip, wav, mp4 = tmp_path / ".t.capture.mov", tmp_path / "t.wav", tmp_path / "t.mp4"
    make_video(clip, 3, [])
    make_wav(wav, 1, [0.2])
    take = video.Take(1, clip, launched=100.0, marked=100.5, audio_start=100.9)
    assert "estimated" in await take.finish(wav, mp4)
    assert video.duration(mp4) == pytest.approx(1, abs=0.05)
    with pytest.raises(video.VideoError, match="Screen Recording"):
        await video.Take(1, tmp_path / "never.mov", audio_start=1.0).finish(wav, mp4)
