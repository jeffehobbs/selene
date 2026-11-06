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


def test_stereo_16_bit_round_trip_with_reproducible_dither(tmp_path):
    left, right = tone(freq=220), tone(freq=330, amp=0.25)
    wav.write(tmp_path / "s.wav", [left, right], RATE, bits=16)
    (back_l, back_r), rate = wav.read_channels(tmp_path / "s.wav")
    assert rate == RATE and len(back_l) == len(left)
    assert max(abs(a - b) for a, b in zip(left, back_l)) < 2 / 32768  # a step plus dither
    assert max(abs(a - b) for a, b in zip(right, back_r)) < 2 / 32768
    wav.write(tmp_path / "copy" / "s.wav", [left, right], RATE, bits=16)
    assert (tmp_path / "copy" / "s.wav").read_bytes() == (tmp_path / "s.wav").read_bytes()


def test_trim_channels_cuts_both_where_the_louder_one_ends():
    ping = tone(0.3, decay=0.03) + [0.0] * RATE
    long = tone(0.6, decay=0.1, amp=0.3) + [0.0] * (len(ping) - int(0.6 * RATE))
    out = wav.trim_channels([ping, long], RATE)
    assert len(out[0]) == len(out[1]) > len(wav.trim(ping, RATE))
    assert out[0][-1] == pytest.approx(0, abs=1e-9) and out[1][-1] == pytest.approx(0, abs=1e-9)


def test_level_channels_uses_the_mean_power_of_both():
    out, _ = build.level_channels([tone(amp=0.1), tone(amp=0.1)], -12)
    assert wav.db(wav.loudness(wav.power_mix(out), RATE)) == pytest.approx(-12, abs=0.05)


@pytest.mark.parametrize("freq", [55, 65.41, 261.63, 1000])
def test_pitch_finds_the_fundamental_not_an_octave_off(freq):
    rich = [a + 0.6 * b + 0.3 * c for a, b, c in zip(tone(0.1, freq), tone(0.1, 2 * freq), tone(0.1, 3 * freq))]
    assert wav.pitch(rich, RATE) == pytest.approx(freq, rel=0.005)


def test_pitch_is_none_for_noise():
    import random
    rng = random.Random(1)
    assert wav.pitch([rng.uniform(-1, 1) for _ in range(4800)], RATE) is None


def test_rms_freq_hears_a_brighter_tone_on_the_same_fundamental():
    dull = tone(freq=100)
    bright = [a + 0.5 * b for a, b in zip(tone(freq=100), tone(freq=2000))]
    assert wav.rms_freq(dull, RATE) == pytest.approx(100, rel=0.01)
    assert wav.rms_freq(bright, RATE) > 5 * wav.rms_freq(dull, RATE)


@pytest.mark.parametrize("pack", ["rhythm", "synth"])
def test_every_listed_sound_has_a_patch(pack):
    """pack.json (what the model is told) and patches.scd (what plays) agree:
    each bank comes from a voice with a vary, each syn* from a live voice."""
    import json
    import re
    src = Path(build.HERE) / pack
    meta = json.loads((src / "pack.json").read_text())
    patches = (src / "patches.scd").read_text()
    blocks = dict(re.findall(r"\n    (\w+): \(\n(.*?)\n    \),", patches, re.S))
    for bank in meta["banks"]:
        assert "vary:" in blocks[bank.removeprefix("mod")], bank
    for synth in meta["synths"]:
        assert "live: true" in blocks[synth.removeprefix("syn")], synth
    lives = {name for name, body in blocks.items() if "live: true" in body}
    assert {f"syn{n}" for n in lives} == set(meta["synths"])
