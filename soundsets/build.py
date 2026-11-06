"""Build a Selene soundset: render its patches, level the banks, write the
manifest, zip the pack, and make listening reels to audition before release.

    uv run python soundsets/build.py rhythm [--reels-only] [--sclang PATH]

A dev tool: it isn't part of the installed app. Output goes to
soundsets/build/<id>/ (git-ignored):
    pack/                  what gets zipped: manifest.json, samples/, patches.scd, synths.scd
    selene-<id>-<ver>.zip  the release asset, with its sha256 in the log
    reel/                  one WAV per bank (every variant in order), live.wav, groove.wav
Standard library only. sclang runs with an isolated HOME so the player's
startup.scd (which boots a server) never runs.
"""

import argparse
import hashlib
import json
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


def process(meta: dict, raw: Path, pack: Path) -> dict:
    """Trim and level every bank variant into pack/samples; return per-bank stats."""
    stats = {}
    for bank, info in meta["banks"].items():
        files = sorted((raw / bank).glob("*.wav"))
        if not files:
            sys.exit(f"{bank}: no renders (does patches.scd have a voice named {bank[3:]}?)")
        rows = []
        for f in files:
            samples, rate = wav.read(f)
            assert rate == RATE, f"{f}: {rate} Hz"
            samples, gain_db = level(wav.trim(samples, RATE), info["target_db"])
            wav.write24(pack / "samples" / bank / f.name, samples, RATE)
            rows.append((len(samples) / RATE, wav.db(wav.loudness(samples, RATE)),
                         wav.db(wav.peak(samples)), gain_db))
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

def place(mix: list[float], samples: list[float], at: float, gain: float = 1.0) -> None:
    start = int(at * RATE)
    need = start + len(samples)
    if need > len(mix):
        mix.extend([0.0] * (need - len(mix)))
    for i, x in enumerate(samples):
        mix[start + i] += x * gain


def finish(mix: list[float], path: Path) -> None:
    top = wav.peak(mix) or 1.0
    g = min(1.0, 10 ** (-1 / 20) / top)  # never louder than the parts, never over -1 dBFS
    wav.write24(path, [x * g for x in mix], RATE)


def bank_reel(files: list[Path], path: Path) -> None:
    mix, at = [], 0.25
    for f in files:
        samples, _ = wav.read(f)
        place(mix, samples, at)
        at += max(0.35, len(samples) / RATE + 0.12)
    mix.extend([0.0] * int(0.5 * RATE))
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
    cache: dict[tuple[str, int], list[float]] = {}
    mix, bar_len, used = [], step * 16, set()
    for b, name in enumerate(SONG):
        for bank, n, gain, steps in GROOVE[name]:
            key = (bank, n)
            if key not in cache:
                cache[key] = wav.read(pack / "samples" / bank / f"{bank}_{n:02d}.wav")[0]
            used.add(f"{bank}:{n}")
            for s, c in enumerate(steps):
                if c in "xo":
                    # The riser in the fill swells into the next bar's downbeat.
                    at = 0.1 + b * bar_len + s * step
                    if bank == "modnoise":
                        at = max(0.0, 0.1 + (b + 1) * bar_len - len(cache[key]) / RATE)
                    swing = step * 0.12 if s % 2 else 0.0
                    place(mix, cache[key], at + swing, gain * (0.55 if c == "o" else 1.0))
    mix.extend([0.0] * int(1.0 * RATE))
    finish(mix, path)
    return sorted(used)


def live_reel(raw: Path, path: Path) -> None:
    """The live synths at their default knobs, as calibrated: these play
    through SuperDirt at the same level as the banks."""
    bank_reel(sorted((raw / "_live").glob("*.wav")), path)


# ---- check: the live synths in a real SuperDirt --------------------------------

def run_e2e(sclang: str, pack: Path, out: Path) -> str:
    """Boot an isolated SuperDirt on BlackHole (e2e.scd refuses otherwise),
    load the pack the way Selene does, play e2e.scd's events, record them."""
    quarks = Path.home() / "Library/Application Support/SuperCollider/downloaded-quarks"
    with tempfile.TemporaryDirectory(prefix="selene-sc-home-") as home:
        conf = Path(home) / "sclang_conf.yaml"
        conf.write_text("includePaths:\n" + "".join(
            f"    -   {quarks / q}\n" for q in ("SuperDirt", "Vowel")) + "excludePaths:\n    []\n")
        proc = subprocess.run([sclang, "-l", str(conf), str(HERE / "e2e.scd"), str(pack), str(out)],
                              env={**os.environ, "HOME": home}, capture_output=True, text=True,
                              timeout=120)
    return proc.stdout + proc.stderr


def check(sclang: str, pack: Path, out: Path) -> bool:
    log = run_e2e(sclang, pack, out)
    if "E2E ABORT" in log or "E2E DONE" not in log:
        print("\n".join(ln for ln in log.splitlines() if "E2E" in ln or "ERROR" in ln)[-2000:])
        return False
    missing = re.findall(r"no synth or sample named '(\w+)'", log)
    samples, _ = wav.read(out)
    events = [(float(m[1]), m[2]) for m in re.finditer(r"E2E EVENT ([\d.]+) (.+)", log)]
    first = next(i for i, x in enumerate(samples) if abs(x) > 0.01) / RATE
    seen = {}
    for k, (at, label) in enumerate(events):
        end = events[k + 1][0] if k + 1 < len(events) else at + 2.5
        seg = samples[int((first + at - 0.505) * RATE): int((first + end - 0.505) * RATE)]
        top = wav.peak(seg)
        above = [i for i, x in enumerate(seg) if abs(x) > top * 0.01] or [0]
        seen[label] = (wav.db(wav.loudness(seg, RATE)), wav.brightness(seg, RATE),
                       (above[-1] - above[0]) / RATE)
        print(f"  {label:42s} loud {seen[label][0]:6.1f}  zc {seen[label][1]:6.0f}  rings {seen[label][2]:.2f}s")

    def near(a, b, tol=0.05):
        return abs(a - b) <= tol * max(abs(a), abs(b))
    rings = {k: v[2] for k, v in seen.items()}
    zc = {k: v[1] for k, v in seen.items()}
    expect = {
        "every event sounds": all(v[0] > -45 for v in seen.values()),
        "no unknown sounds": not missing,
        "decay lengthens a drum": rings["synkick decay 1.2"] > 2 * rings["synkit:0 kick"],
        "a long drum rings past a 16th step": rings["synkick decay 1.2 on a 16th step"] > 1.0,
        "sustain still chokes": rings["synkick decay 1.2 sustain 0.1 (choked)"] < 0.15,
        "Flow's fdecay shortens": rings["synkick decay 1.2 fdecay 0.25"] < 0.4,
        "voice brightens": zc["synhat voice 1"] > 1.5 * zc["synhat voice 0"],
        "Flow's fvoice brightens": zc["synhat voice 0 fvoice 1"] > 1.5 * zc["synhat voice 0"],
        "synkit's n doesn't retune": near(zc["synkit:5 n 5 (tom; n must not retune)"], zc["syntom n 0"]),
        "note 12 is an octave up": near(zc["synkit:5 note 12"], 2 * zc["syntom n 0"]),
        "kit open hat takes decay": rings["synkit:3 open hat decay 0.9"] > 1.5 * rings["synkit:3 open hat"],
    }
    for what, ok in expect.items():
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
    return all(expect.values())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pack", help="soundset id: a folder under soundsets/ with pack.json + patches.scd")
    ap.add_argument("--sclang")
    ap.add_argument("--reels-only", action="store_true", help="reuse the last render and pack")
    ap.add_argument("--index", action="store_true",
                    help="point soundsets/index.json at this build (for a release)")
    ap.add_argument("--check", action="store_true",
                    help="after building, play the live synths in an isolated SuperDirt on "
                         "BlackHole (silent) and verify the knobs, kit and lengths")
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
    used = groove_reel(pack, reel / "groove.wav")
    print(f"reels in {reel} (groove uses {', '.join(used)})")
    if a.check and "kit" in meta:  # e2e.scd's events are written for the Rhythm kit
        print("checking the live synths in SuperDirt:")
        if not check(find_sclang(a.sclang), pack, out / "e2e.wav"):
            sys.exit("check failed")
    print(f"done in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
