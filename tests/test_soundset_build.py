"""The soundset build's WAV handling (soundsets/wav.py, soundsets/build.py).
The renders themselves need SuperCollider: `build.py rhythm --check`."""

import math
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "soundsets"))
import build  # noqa: E402
import wav  # noqa: E402

RATE = 48000


def tone(seconds=0.2, freq=440, amp=0.5, decay=None):
    n = int(RATE * seconds)
    env = (lambda i: math.exp(-i / (RATE * decay))) if decay else (lambda i: 1.0)
    return [amp * env(i) * math.sin(2 * math.pi * freq * i / RATE) for i in range(n)]


def write_float_wav(path: Path, samples, channels=1):
    """What scsynth's NRT writes: IEEE float, which the wave module can't read."""
    body = struct.pack(f"<{len(samples)}f", *samples)
    fmt = struct.pack("<HHIIHH", 3, channels, RATE, RATE * 4 * channels, 4 * channels, 32)
    path.write_bytes(b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(body)) + b"WAVE"
                     + b"fmt " + struct.pack("<I", len(fmt)) + fmt
                     + b"data" + struct.pack("<I", len(body)) + body)


def test_24_bit_round_trip(tmp_path):
    src = tone() + [0.999, -1.0, 0.0]
    wav.write24(tmp_path / "t.wav", src, RATE)
    back, rate = wav.read(tmp_path / "t.wav")
    assert rate == RATE and len(back) == len(src)
    assert max(abs(a - b) for a, b in zip(src, back)) < 2e-7  # one 24-bit step


def test_reads_float_wavs_and_mixes_channels_down(tmp_path):
    write_float_wav(tmp_path / "f.wav", [0.25, -0.25, 0.5, 0.5], channels=2)
    samples, rate = wav.read(tmp_path / "f.wav")
    assert rate == RATE and samples == [0.0, 0.5]


def test_rejects_what_it_cant_read(tmp_path):
    (tmp_path / "x.wav").write_bytes(b"not a wav at all")
    with pytest.raises(ValueError):
        wav.read(tmp_path / "x.wav")


def test_trim_cuts_the_silent_tail_and_fades_to_zero():
    hit = tone(0.3, decay=0.03) + [0.0] * RATE  # a decaying ping, then a second of silence
    out = wav.trim(hit, RATE)
    assert len(out) < len(hit) * 0.4
    assert out[-1] == pytest.approx(0, abs=1e-9)
    assert out[:100] == hit[:100]  # the attack is untouched


def test_loudness_ignores_long_quiet_tails():
    short = tone(0.05)
    long = short + [0.001] * RATE * 2
    assert wav.loudness(long, RATE) == pytest.approx(wav.loudness(short, RATE), rel=0.01)


def test_brightness_orders_low_and_high():
    assert wav.brightness(tone(freq=60), RATE) < wav.brightness(tone(freq=6000), RATE)


def test_level_hits_the_target_unless_the_peak_cap_stops_it():
    out, _ = build.level(tone(amp=0.1), -12)
    assert wav.db(wav.loudness(out, RATE)) == pytest.approx(-12, abs=0.05)
    spiky = [0.0] * 5000 + [0.9] + [0.0] * 5000 + tone(0.05, amp=0.01)
    out, _ = build.level(spiky, -6)
    assert wav.db(wav.peak(out)) == pytest.approx(build.PEAK_CAP_DB, abs=0.01)
