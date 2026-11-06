"""Build a Selene soundset: render its patches, level the banks, write the
manifest, zip the pack, and make listening reels to audition before release.

    uv run python soundsets/build.py rhythm [--reels-only] [--sclang PATH]

A dev tool: it isn't part of the installed app. Output goes to
soundsets/build/<id>/ (git-ignored):
    pack/                  what gets zipped: manifest.json, samples/, patches.scd, synths.scd
    selene-<id>-<ver>.zip  the release asset, with its sha256 in the log
    reel/                  one WAV per bank (every variant in order), live.wav, and
                           groove.wav (Rhythm) or demo.wav (--check, if the pack has demo.scd)
Standard library only. sclang runs with an isolated HOME so the player's
startup.scd (which boots a server) never runs.
"""

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import wav  # noqa: E402

RATE = 48000
PEAK_CAP_DB = -0.5
CC0 = ("Creative Commons Zero v1.0 Universal (CC0-1.0)\n\n"
       "To the extent possible under law, the authors of this soundset have waived all\n"
       "copyright and related or neighboring rights to it. You can copy, modify,\n"
       "distribute and perform the sounds, even commercially, without asking.\n"
       "https://creativecommons.org/publicdomain/zero/1.0/\n")


def find_sclang(given: str | None) -> str:
    if given:
        return given
    sys.path.insert(0, str(HERE.parent))
    from selene.superdirt import find_sclang as find
    found = find()
    if not found:
        sys.exit("sclang not found; install SuperCollider or pass --sclang")
    return found


def render(sclang: str, patches: Path, raw: Path) -> None:
    raw.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="selene-sc-home-") as home:
        env = {**os.environ, "HOME": home}
        proc = subprocess.run([sclang, str(HERE / "render.scd"), str(patches), str(raw)],
                              env=env, capture_output=True, text=True, timeout=900)
    out = proc.stdout + proc.stderr
    errors = [ln for ln in out.splitlines() if "ERROR" in ln]
    done = re.search(r"RENDER DONE (\d+) files in ([\d.]+) s", out)
    if errors or not done:
        sys.exit("render failed:\n" + "\n".join(errors or out.splitlines()[-30:]))
    print(f"rendered {done[1]} files in {done[2]} s")


def level(samples: list[float], target_db: float) -> tuple[list[float], float]:
    """Scale to target loudness (loudest 50 ms RMS), but never past the peak cap."""
    loud, top = wav.loudness(samples, RATE), wav.peak(samples)
    if loud == 0:
        return samples, 0.0
    gain_db = min(target_db - wav.db(loud), PEAK_CAP_DB - wav.db(top))
    g = 10 ** (gain_db / 20)
    return [x * g for x in samples], gain_db


def level_channels(chans: list[list[float]], target_db: float) -> tuple[list[list[float]], float]:
    """level() for a stereo file: loudness from the channels' mean power, the
    peak cap from the loudest channel."""
    loud, top = wav.loudness(wav.power_mix(chans), RATE), max(wav.peak(c) for c in chans)
    if loud == 0:
        return chans, 0.0
    gain_db = min(target_db - wav.db(loud), PEAK_CAP_DB - wav.db(top))
    g = 10 ** (gain_db / 20)
    return [[x * g for x in c] for c in chans], gain_db


def process(meta: dict, raw: Path, pack: Path) -> dict:
    """Trim and level every bank variant into pack/samples; return per-bank stats.
    Mono banks are 24-bit; a pack can ask for 16-bit (long stereo banks)."""
    stats, bits = {}, meta.get("bits", 24)
    for bank, info in meta["banks"].items():
        files = sorted((raw / bank).glob("*.wav"))
        if not files:
            sys.exit(f"{bank}: no renders (does patches.scd have a voice named {bank[3:]}?)")
        rows = []
        for f in files:
            chans, rate = wav.read_channels(f)
            assert rate == RATE, f"{f}: {rate} Hz"
            dest = pack / "samples" / bank / f.name
            if len(chans) == 1:
                samples, gain_db = level(wav.trim(chans[0], RATE), info["target_db"])
                if bits == 24:
                    wav.write24(dest, samples, RATE)
                else:
                    wav.write(dest, [samples], RATE, bits)
                chans = [samples]
            else:
                chans, gain_db = level_channels(wav.trim_channels(chans, RATE), info["target_db"])
                wav.write(dest, chans, RATE, bits)
            mix = wav.power_mix(chans)
            rows.append((len(mix) / RATE, wav.db(wav.loudness(mix, RATE)),
                         wav.db(max(wav.peak(c) for c in chans)), gain_db))
        stats[bank] = rows
        lens, louds = [r[0] for r in rows], [r[1] for r in rows]
        short = sum(1 for r in rows if r[1] < info["target_db"] - 0.5)  # held back by the peak cap
        print(f"  {bank:9s} {len(rows):2d} files  {min(lens):.2f}-{max(lens):.2f} s  "
              f"loudness {min(louds):6.1f}..{max(louds):6.1f} dB (target {info['target_db']})"
              + (f"  {short} under target (peak cap)" if short else ""))
    return stats


def manifest(meta: dict, pack: Path) -> dict:
    out = {k: meta[k] for k in ("id", "name", "version", "license", "about")}
    out["drums"] = bool(meta.get("drums"))  # Flow treats every sound in it as a drum
    out["banks"] = {name: {"count": len(list((pack / "samples" / name).glob("*.wav"))),
                           "about": info["about"]} for name, info in meta["banks"].items()}
    out["synths"] = meta.get("synths", {})
    if "kit" in meta:
        out["kit"] = meta["kit"]
    return out


REPO = "jeffehobbs/selene"


def release_tag(meta: dict) -> str:
    return f"sounds-{meta['id']}-v{meta['version']}"


def update_index(meta: dict, zip_path: Path, sha: str) -> Path:
    """Point soundsets/index.json (read by Selene from main) at this build.
    The zip must be published as a release asset under release_tag()."""
    index_path = HERE / "index.json"
    try:
        index = json.loads(index_path.read_text())
    except FileNotFoundError:
        index = {"soundsets": []}
    entry = {"id": meta["id"], "name": meta["name"], "version": meta["version"],
             "about": meta["about"], "min_selene": meta.get("min_selene", "0"),
             "url": f"https://github.com/{REPO}/releases/download/{release_tag(meta)}/{zip_path.name}",
             "sha256": sha, "size": zip_path.stat().st_size}
    rest = [e for e in index["soundsets"] if e["id"] != meta["id"]]
    index["soundsets"] = sorted(rest + [entry], key=lambda e: e["id"])
    index_path.write_text(json.dumps(index, indent=2) + "\n")
    return index_path


def zip_pack(pack: Path, dest: Path) -> str:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(pack.rglob("*")):
            if f.is_file():
                info = zipfile.ZipInfo(str(f.relative_to(pack)), date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, f.read_bytes())  # fixed timestamps: same input, same zip
    return hashlib.sha256(dest.read_bytes()).hexdigest()


# ---- reels -------------------------------------------------------------------

# Reels are stereo: a mono file plays in both channels.

def load(path: Path) -> list[list[float]]:
    chans, _ = wav.read_channels(path)
    return chans if len(chans) == 2 else [chans[0], chans[0]]


def place(mix: list[list[float]], chans: list[list[float]], at: float, gain: float = 1.0) -> None:
    start = int(at * RATE)
    for m, c in zip(mix, chans):
        need = start + len(c)
        if need > len(m):
            m.extend([0.0] * (need - len(m)))
        for i, x in enumerate(c):
            m[start + i] += x * gain


def finish(mix: list[list[float]], path: Path) -> None:
    top = max(wav.peak(m) for m in mix) or 1.0
    g = min(1.0, 10 ** (-1 / 20) / top)  # never louder than the parts, never over -1 dBFS
    n = max(len(m) for m in mix)
    wav.write(path, [[x * g for x in m] + [0.0] * (n - len(m)) for m in mix], RATE)


def bank_reel(files: list[Path], path: Path) -> None:
    mix, at = [[], []], 0.25
    for f in files:
        chans = load(f)
        place(mix, chans, at)
        at += max(0.35, len(chans[0]) / RATE + 0.12)
    for m in mix:
        m.extend([0.0] * int(0.5 * RATE))
    finish(mix, path)


# 16 steps per bar; each line: bank, variant, gain, steps (x = hit, o = soft hit).
GROOVE = {
    "A": [("modkick", 4, 1.0, "x.....x.x......."), ("modsnare", 6, 0.95, "....x.......x..."),
          ("modhat", 3, 0.7, "x.xox.xox.xox.x."), ("modohat", 4, 0.6, "..............x."),
          ("modperc", 6, 0.45, "...o..o....o..o."), ("modrim", 7, 0.5, ".......o........")],
    "B": [("modkick", 4, 1.0, "x.....x.x.....x."), ("modsnare", 6, 0.95, "....x.......x..o"),
          ("modclap", 5, 0.8, "....x.......x..."), ("modhat", 3, 0.7, "x.xox.xox.xox.x."),
          ("modohat", 4, 0.6, "......x.......x."), ("modcow", 3, 0.45, "..o.......o....."),
          ("modperc", 9, 0.45, ".o.....o...o...."), ("modzap", 2, 0.5, "...........x....")],
    "fill": [("modkick", 4, 1.0, "x.....x........."), ("modsnare", 6, 0.95, "....x..........."),
             ("modhat", 3, 0.7, "x.xox.x........."), ("modtom", 3, 0.9, "........x......."),
             ("modtom", 7, 0.85, "..........x....."), ("modtom", 11, 0.85, "............x..."),
             ("modtom", 14, 0.8, "..............xx"), ("modnoise", 13, 0.55, "x...............")],
    "drop": [("modkick", 11, 1.0, "x.....x.x......."), ("modmetal", 9, 0.6, "x..............."),
             ("modsnare", 12, 0.95, "....x.......x..."), ("modclap", 12, 0.8, "............x..."),
             ("modhat", 9, 0.7, "x.xox.xox.xox.x."), ("modohat", 12, 0.55, "..x...x...x...x."),
             ("modzap", 13, 0.5, "...............x")],
}
SONG = ["A", "A", "A", "B", "B", "B", "B", "fill", "drop", "drop", "B", "fill", "A"]


def groove_reel(pack: Path, path: Path, bpm: float = 116) -> list[str]:
    step = 60 / bpm / 4
    cache: dict[tuple[str, int], list[list[float]]] = {}
    mix, bar_len, used = [[], []], step * 16, set()
    for b, name in enumerate(SONG):
        for bank, n, gain, steps in GROOVE[name]:
            key = (bank, n)
            if key not in cache:
                cache[key] = load(pack / "samples" / bank / f"{bank}_{n:02d}.wav")
            used.add(f"{bank}:{n}")
            for s, c in enumerate(steps):
                if c in "xo":
                    # The riser in the fill swells into the next bar's downbeat.
                    at = 0.1 + b * bar_len + s * step
                    if bank == "modnoise":
                        at = max(0.0, 0.1 + (b + 1) * bar_len - len(cache[key][0]) / RATE)
                    swing = step * 0.12 if s % 2 else 0.0
                    place(mix, cache[key], at + swing, gain * (0.55 if c == "o" else 1.0))
    for m in mix:
        m.extend([0.0] * int(1.0 * RATE))
    finish(mix, path)
    return sorted(used)


def live_reel(raw: Path, path: Path) -> None:
    """The live synths at their default knobs, as calibrated: these play
    through SuperDirt at the same level as the banks."""
    bank_reel(sorted((raw / "_live").glob("*.wav")), path)


# ---- check: the live synths in a real SuperDirt --------------------------------

def run_e2e(sclang: str, events: Path, packs: list[Path], out: Path) -> str:
    """Boot an isolated SuperDirt on BlackHole (e2e.scd refuses otherwise),
    load the packs the way Selene does, play the events file, record it."""
    quarks = Path.home() / "Library/Application Support/SuperCollider/downloaded-quarks"
    with tempfile.TemporaryDirectory(prefix="selene-sc-home-") as home:
        conf = Path(home) / "sclang_conf.yaml"
        conf.write_text("includePaths:\n" + "".join(
            f"    -   {quarks / q}\n" for q in ("SuperDirt", "Vowel")) + "excludePaths:\n    []\n")
        proc = subprocess.run([sclang, "-l", str(conf), str(HERE / "e2e.scd"), str(out), str(events),
                               *map(str, packs)],
                              env={**os.environ, "HOME": home}, capture_output=True, text=True,
                              timeout=240)
    return proc.stdout + proc.stderr


class Heard:
    """One check event's slice of the recording, and what it sounds like."""

    def __init__(self, samples: list[float]):
        self.samples = samples
        top = wav.peak(samples)
        above = [i for i, x in enumerate(samples) if abs(x) > top * 0.01] or [0]
        self.start = above[0]
        self.loud = wav.db(wav.loudness(samples, RATE))
        self.zc = wav.brightness(samples, RATE)
        self.rf = wav.rms_freq(samples, RATE)
        self.rings = (above[-1] - above[0]) / RATE

    def pitch(self, at: float, length: float = 0.06, fmin: float = 40.0) -> float:
        """Fundamental at `at` seconds after the sound starts (0 if none)."""
        i = self.start + int(at * RATE)
        return wav.pitch(self.samples[i:i + int(length * RATE)], RATE, fmin) or 0.0


def near(a: float, b: float, tol: float = 0.05) -> bool:
    return abs(a - b) <= tol * max(abs(a), abs(b))


def expect_rhythm(h: dict[str, Heard]) -> dict[str, bool]:
    return {
        "decay lengthens a drum": h["synkick decay 1.2"].rings > 2 * h["synkit:0 kick"].rings,
        "a long drum rings past a 16th step": h["synkick decay 1.2 on a 16th step"].rings > 1.0,
        "sustain still chokes": h["synkick decay 1.2 sustain 0.1 (choked)"].rings < 0.15,
        "Flow's fdecay shortens": h["synkick decay 1.2 fdecay 0.25"].rings < 0.4,
        "voice brightens": h["synhat voice 1"].zc > 1.5 * h["synhat voice 0"].zc,
        "Flow's fvoice brightens": h["synhat voice 0 fvoice 1"].zc > 1.5 * h["synhat voice 0"].zc,
        "synkit's n doesn't retune": near(h["synkit:5 n 5 (tom; n must not retune)"].zc, h["syntom n 0"].zc),
        "note 12 is an octave up": near(h["synkit:5 note 12"].zc, 2 * h["syntom n 0"].zc),
        "kit open hat takes decay": h["synkit:3 open hat decay 0.9"].rings > 1.5 * h["synkit:3 open hat"].rings,
    }


def expect_synth(h: dict[str, Heard]) -> dict[str, bool]:
    low, high = h["synbass note -24"].pitch(0.15, 0.1), h["synbass note -12"].pitch(0.15, 0.1)
    slide_start, slide_end = h["synlead slide 12"].pitch(0.0, 0.04, 100), h["synlead slide 12"].pitch(0.4)
    bend_start, bend_end = h["synbass accelerate 1"].pitch(0.03, 0.1), h["synbass accelerate 1"].pitch(0.8, 0.1)
    drone = h["moddrone:7 sample"].pitch(2.0, 0.2)
    pitch_class = (12 * math.log2(drone / 261.6256)) % 12 if drone else -1
    print(f"  pitches: bass {low:.1f} / {high:.1f} Hz, slide {slide_start:.1f} -> {slide_end:.1f}, "
          f"accelerate {bend_start:.1f} -> {bend_end:.1f}, moddrone:7 {drone:.1f} Hz (class {pitch_class:.2f})")
    return {
        "note -24 is C3 (65.4 Hz)": near(low, 65.41, 0.02),
        "note 12 is an octave up": near(high, 2 * low, 0.02),
        "a gated note lasts its step": h["synpad delta 2"].rings > 1.6 and h["synpad delta 0.25"].rings < 0.4,
        "legato stretches a gated note": 1.2 < h["synpad delta 0.5 legato 3"].rings < 1.8,
        "a struck note rings past its step": h["synpluck decay 2 on a 16th step"].rings > 0.8,
        "sustain chokes a struck note": h["synpluck decay 2 sustain 0.1 (choked)"].rings < 0.2,
        "Flow's fdecay shortens": h["synpluck decay 2 fdecay 0.2"].rings < 0.6 * h["synpluck decay 2 on a 16th step"].rings,
        "decay lengthens the bell": h["synbell decay 3"].rings > 2.5 * h["synbell decay 0.5"].rings,
        "voice opens the acid filter": h["synacid voice 1"].rf > 1.5 * h["synacid voice 0"].rf,
        "Flow's fvoice opens it too": h["synacid voice 0 fvoice 1"].rf > 1.5 * h["synacid voice 0"].rf,
        "voice folds the lpg": h["synlpg voice 1"].zc > 1.5 * h["synlpg voice 0"].zc,
        "slide glides up into the note": 0 < slide_start < 0.8 * slide_end and near(slide_end, 261.63, 0.03),
        "accelerate bends the note": bend_end > 1.4 * bend_start > 0,
        "moddrone:7 is a G": abs(pitch_class - 7) < 0.3,
    }


EXPECT = {"rhythm": expect_rhythm, "synth": expect_synth}


def check(sclang: str, src: Path, pack: Path, out: Path, pack_id: str) -> bool:
    log = run_e2e(sclang, src / "e2e.scd", [pack], out)
    if "E2E ABORT" in log or "E2E DONE" not in log:
        print("\n".join(ln for ln in log.splitlines() if "E2E" in ln or "ERROR" in ln)[-2000:])
        return False
    missing = re.findall(r"no synth or sample named '(\w+)'", log)
    samples, _ = wav.read(out)
    events = [(float(m[1]), m[2]) for m in re.finditer(r"E2E EVENT ([\d.]+) (.+)", log)]
    first = next(i for i, x in enumerate(samples) if abs(x) > 0.01) / RATE
    offset = first - events[0][0] - 0.005
    heard = {}
    for k, (at, label) in enumerate(events):
        # Stop short of the next event, so its attack never counts as this one's tail.
        end = (events[k + 1][0] if k + 1 < len(events) else at + 2.5) - 0.03
        h = heard[label] = Heard(samples[int((offset + at) * RATE): int((offset + end) * RATE)])
        print(f"  {label:42s} loud {h.loud:6.1f}  zc {h.zc:6.0f}  rms-freq {h.rf:6.0f}  rings {h.rings:.2f}s")
    expect = {"every event sounds": all(h.loud > -45 for h in heard.values()),
              "no unknown sounds": not missing}
    expect.update(EXPECT[pack_id](heard))
    for what, ok in expect.items():
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
    return all(expect.values())


def demo(sclang: str, src: Path, pack: Path, out: Path) -> None:
    """Record the pack's demo.scd through SuperDirt, with every other built
    pack loaded too (the Synth demo's drums come from Rhythm)."""
    others = [d / "pack" for d in sorted((HERE / "build").iterdir())
              if (d / "pack" / "manifest.json").exists() and d / "pack" != pack]
    log = run_e2e(sclang, src / "demo.scd", [pack, *others], out)
    if "E2E DONE" not in log:
        sys.exit("demo failed:\n" + "\n".join(ln for ln in log.splitlines() if "ERROR" in ln or "E2E" in ln)[-2000:])
    missing = sorted(set(re.findall(r"no synth or sample named '(\w+)'", log)))
    print(f"demo in {out}" + (f" (not loaded: {', '.join(missing)})" if missing else ""))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pack", help="soundset id: a folder under soundsets/ with pack.json + patches.scd")
    ap.add_argument("--sclang")
    ap.add_argument("--reels-only", action="store_true", help="reuse the last render and pack")
    ap.add_argument("--index", action="store_true",
                    help="point soundsets/index.json at this build (for a release)")
    ap.add_argument("--check", action="store_true",
                    help="after building, play the live synths in an isolated SuperDirt on "
                         "BlackHole (silent), verify the knobs, kit and lengths, and record "
                         "the pack's demo (reel/demo.wav)")
    a = ap.parse_args()
    src = HERE / a.pack
    meta = json.loads((src / "pack.json").read_text())
    out = HERE / "build" / a.pack
    raw, pack, reel = out / "raw", out / "pack", out / "reel"
    t0 = time.time()
    if not a.reels_only:
        for d in (raw, pack, reel):
            if d.exists():
                shutil.rmtree(d)
        render(find_sclang(a.sclang), src / "patches.scd", raw)
        print("leveling banks:")
        process(meta, raw, pack)
        shutil.copy(src / "patches.scd", pack / "patches.scd")
        shutil.copy(HERE / "live.scd", pack / "synths.scd")
        (pack / "LICENSE").write_text(CC0)
        (pack / "manifest.json").write_text(json.dumps(manifest(meta, pack), indent=2) + "\n")
        dest = out / f"selene-{a.pack}-{meta['version']}.zip"
        sha = zip_pack(pack, dest)
        print(f"{dest.name}: {dest.stat().st_size / 1e6:.1f} MB, sha256 {sha}")
        if a.index:
            print(f"updated {update_index(meta, dest, sha).relative_to(HERE.parent)}; publish with:\n"
                  f"  gh release create {release_tag(meta)} {dest} --repo {REPO} --latest=false "
                  f"--title \"{meta['name']} {meta['version']}\" --notes-file <notes>")
    reel.mkdir(parents=True, exist_ok=True)
    for bank in meta["banks"]:
        bank_reel(sorted((pack / "samples" / bank).glob("*.wav")), reel / f"{bank}.wav")
    if (raw / "_live").is_dir():
        live_reel(raw, reel / "live.wav")
    if a.pack == "rhythm":  # the groove is written for the Rhythm banks
        used = groove_reel(pack, reel / "groove.wav")
        print(f"groove uses {', '.join(used)}")
    print(f"reels in {reel}")
    if a.check and (src / "e2e.scd").exists():
        print("checking the live synths in SuperDirt:")
        if not check(find_sclang(a.sclang), src, pack, out / "e2e.wav", a.pack):
            sys.exit("check failed")
        if (src / "demo.scd").exists():
            demo(find_sclang(a.sclang), src, pack, reel / "demo.wav")
    print(f"done in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
