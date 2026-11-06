"""Just enough WAV for the soundset build: read PCM/float, write 16/24-bit PCM,
and measure. Standard library only, so the build needs nothing installed.
Mono functions take a list of samples; the *_channels ones a list of channels."""

import math
import operator
import random
import struct
import wave
from array import array
from pathlib import Path


def read(path: Path) -> tuple[list[float], int]:
    """Mono samples in -1..1 (channels averaged) and the sample rate.
    Reads 16/24/32-bit PCM and 32-bit float (which `wave` refuses)."""
    values, channels, rate = _decode(path)
    if channels > 1:
        values = [sum(values[i:i + channels]) / channels for i in range(0, len(values), channels)]
    return values, rate


def read_channels(path: Path) -> tuple[list[list[float]], int]:
    """One list of samples per channel, and the sample rate."""
    values, channels, rate = _decode(path)
    return [values[c::channels] for c in range(channels)], rate


def _decode(path: Path) -> tuple[list[float], int, int]:
    data = Path(path).read_bytes()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError(f"{path}: not a WAV file")
    pos, fmt, body = 12, None, None
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        chunk = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt = chunk
        elif cid == b"data":
            body = chunk
        pos += 8 + size + (size & 1)
    if fmt is None or body is None:
        raise ValueError(f"{path}: no fmt or data chunk")
    tag, channels, rate = struct.unpack("<HHI", fmt[:8])
    bits = struct.unpack("<H", fmt[14:16])[0]
    if tag == 0xFFFE:  # WAVE_FORMAT_EXTENSIBLE: the real tag opens the subformat GUID
        tag = struct.unpack("<H", fmt[24:26])[0]
    if tag == 3 and bits == 32:
        samples = array("f", body[: len(body) // 4 * 4])
        values = list(samples)
    elif tag == 1 and bits in (16, 24, 32):
        width = bits // 8
        n = len(body) // width
        if bits == 24:
            # Widen to 32-bit by padding a low zero byte, then read as int32.
            wide = bytearray(n * 4)
            wide[1::4], wide[2::4], wide[3::4] = body[0::3][:n], body[1::3][:n], body[2::3][:n]
            ints, scale = array("i", bytes(wide)), 2.0 ** 31
        else:
            ints, scale = array("h" if bits == 16 else "i", body[: n * width]), 2.0 ** (bits - 1)
        values = [v / scale for v in ints]
    else:
        raise ValueError(f"{path}: unsupported WAV format (tag {tag}, {bits} bits)")
    return values, channels, rate


def write24(path: Path, samples: list[float], rate: int) -> None:
    """Mono 24-bit PCM, clipped to full scale."""
    ints = array("i", (int(max(-1.0, min(1.0, x)) * 2147483647) for x in samples))
    wide = ints.tobytes()
    out = bytearray(len(ints) * 3)
    out[0::3], out[1::3], out[2::3] = wide[1::4], wide[2::4], wide[3::4]  # top three bytes
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(3)
        w.setframerate(rate)
        w.writeframes(bytes(out))


def write(path: Path, chans: list[list[float]], rate: int, bits: int = 24) -> None:
    """PCM, any number of channels, clipped to full scale. 16-bit files get
    TPDF dither, seeded from the file name so rebuilds stay byte-identical."""
    n, width = len(chans[0]), bits // 8
    frames = [x for frame in zip(*chans) for x in frame]
    if bits == 16:
        rng, lsb = random.Random(Path(path).name), 1 / 32768
        ints = array("h", (max(-32768, min(32767, round((x + (rng.random() - rng.random()) * lsb) * 32767)))
                           for x in frames))
        out = ints.tobytes()
    elif bits == 24:
        ints = array("i", (int(max(-1.0, min(1.0, x)) * 2147483647) for x in frames))
        wide = ints.tobytes()
        out = bytearray(len(ints) * 3)
        out[0::3], out[1::3], out[2::3] = wide[1::4], wide[2::4], wide[3::4]
    else:
        raise ValueError(f"can't write {bits}-bit WAV")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(len(chans))
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(bytes(out))
    assert len(frames) == n * len(chans)


def power_mix(chans: list[list[float]]) -> list[float]:
    """One signal whose square is the channels' mean power: what loudness()
    and brightness-free level checks should see of a stereo file."""
    if len(chans) == 1:
        return chans[0]
    k = len(chans)
    return [math.sqrt(sum(x * x for x in frame) / k) for frame in zip(*chans)]


def trim_channels(chans: list[list[float]], rate: int, floor_db: float = -60,
                  fade: float = 0.01) -> list[list[float]]:
    """trim() for several channels: one cut point, where the loudest channel fades out."""
    if len(chans) == 1:
        return [trim(chans[0], rate, floor_db, fade)]
    top = max(peak(c) for c in chans)
    if top == 0:
        return chans
    floor = top * 10 ** (floor_db / 20)
    end = len(chans[0])
    while end > 0 and max(abs(c[end - 1]) for c in chans) < floor:
        end -= 1
    end = min(len(chans[0]), end + int(rate * fade))
    out = [c[:end] for c in chans]
    nf = min(end, int(rate * fade))
    for c in out:
        for k in range(nf):
            c[end - nf + k] *= 0.5 * (1 + math.cos(math.pi * (k + 1) / nf))
    return out


def pitch(samples: list[float], rate: int, fmin: float = 40.0, fmax: float = 2000.0) -> float | None:
    """The fundamental in Hz, by normalized autocorrelation: the first peak
    nearly as strong as the best one (so it doesn't fall an octave). None
    when nothing repeats (noise, silence)."""
    lo, hi = max(1, int(rate / fmax)), int(rate / fmin)
    w = len(samples) - hi
    if w < hi:
        return None
    base = samples[:w]
    e0 = sum(x * x for x in base)
    corr = []
    for lag in range(lo, hi + 1):
        seg = samples[lag:lag + w]
        e = sum(x * x for x in seg)
        corr.append(sum(map(operator.mul, base, seg)) / math.sqrt(e0 * e) if e0 and e else 0.0)
    best = max(corr)
    if best < 0.5:
        return None
    for i in range(1, len(corr) - 1):
        if corr[i] >= 0.9 * best and corr[i] >= corr[i - 1] and corr[i] >= corr[i + 1]:
            a, b, c = corr[i - 1], corr[i], corr[i + 1]
            shift = 0.5 * (a - c) / (a - 2 * b + c) if a - 2 * b + c else 0.0
            return rate / (lo + i + shift)
    return None


def db(x: float) -> float:
    return 20 * math.log10(x) if x > 0 else -math.inf


def peak(samples: list[float]) -> float:
    return max((abs(x) for x in samples), default=0.0)


def loudness(samples: list[float], rate: int, window: float = 0.05) -> float:
    """The loudest 50 ms RMS: a one-shot's punch, in linear amplitude.
    (Whole-file RMS would punish long tails.)"""
    n = max(1, int(rate * window))
    best, acc = 0.0, 0.0
    sq = [x * x for x in samples]
    for i, v in enumerate(sq):
        acc += v
        if i >= n:
            acc -= sq[i - n]
        if i >= n - 1 or i == len(sq) - 1:
            best = max(best, acc / min(n, i + 1))
    return math.sqrt(best)


def brightness(samples: list[float], rate: int) -> float:
    """Zero crossings per second over the loud part: a cheap stand-in for
    spectral centroid (kicks low, hats high)."""
    top = peak(samples)
    loud = [x for x in samples if abs(x) > top * 0.05] or samples
    crossings = sum(1 for a, b in zip(loud, loud[1:]) if (a < 0) != (b < 0))
    return crossings * rate / max(1, len(loud)) / 2


def rms_freq(samples: list[float], rate: int) -> float:
    """RMS frequency in Hz (from the energy of the first difference): a
    spectral-centroid stand-in that hears a filter opening even when the
    fundamental, and so the zero crossings, stay put."""
    e = sum(x * x for x in samples)
    d = sum((b - a) ** 2 for a, b in zip(samples, samples[1:]))
    if not e:
        return 0.0
    return rate / math.pi * math.asin(min(1.0, 0.5 * math.sqrt(d / e)))


def lead_silence(samples: list[float], rate: int, floor: float = 1e-4) -> float:
    """Seconds before the first sample above floor × peak."""
    top = peak(samples)
    for i, x in enumerate(samples):
        if abs(x) > top * floor:
            return i / rate
    return len(samples) / rate


def trim(samples: list[float], rate: int, floor_db: float = -60, fade: float = 0.01) -> list[float]:
    """Cut the tail where it falls below floor_db under the peak, with a short
    fade so the cut is silent."""
    top = peak(samples)
    if top == 0:
        return samples
    floor = top * 10 ** (floor_db / 20)
    end = len(samples)
    while end > 0 and abs(samples[end - 1]) < floor:
        end -= 1
    end = min(len(samples), end + int(rate * fade))
    out = samples[:end]
    nf = min(len(out), int(rate * fade))
    for k in range(nf):
        out[len(out) - nf + k] *= 0.5 * (1 + math.cos(math.pi * (k + 1) / nf))
    return out
