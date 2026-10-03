"""What SuperDirt can actually play on this machine, so the model names real sounds."""

import re
from pathlib import Path

QUARKS = Path.home() / "Library/Application Support/SuperCollider/downloaded-quarks"

# Used when SuperDirt isn't installed where we look (e.g. Linux paths).
FALLBACK_SAMPLES = (
    "808 808bd 808sd 808hc 808oh bd sn sd hh hh27 oh cp realclaps cb cr rs lt mt ht "
    "drum kicklinn linnhats jazz house techno tech hardkick clubkick breaks125 "
    "breaks152 amencutup jungle arpy bass bass1 bass3 jvbass jungbass moog juno "
    "pad padlong stab hoover rave sitar tabla gtr pluck sax bleep blip glitch "
    "noise wind birds bubble east world fm casio feel metal perc click tink"
).split()
FALLBACK_SYNTHS = (
    "superpiano supersaw superfm supersquare superpwm superchip superhammond "
    "superreese superhoover supergong supermandolin supervibe superzow superkick "
    "supersnare superhat superclap soskick sossnare soshats"
).split()


def sample_banks(root: Path = QUARKS / "Dirt-Samples") -> list[str]:
    if not root.is_dir():
        return FALLBACK_SAMPLES
    return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))


def synth_names(root: Path = QUARKS) -> list[str]:
    if not root.is_dir():
        return FALLBACK_SYNTHS
    names: set[str] = set()
    for scd in root.glob("SuperDirt/**/*.scd"):
        try:
            text = scd.read_text(errors="ignore")
        except OSError:
            continue
        names.update(re.findall(r"SynthDef\(\\((?:super|sos)\w+)", text))
    return sorted(names) or FALLBACK_SYNTHS
