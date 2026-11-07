"""Microtonal mode: an opt-in, per-session N-EDO tuning (/tuning 19|24|31).

Off (12) by default, and while off nothing here reaches GHCi, the model or
the lanes. Turned on, selene defines two helpers in the running GHCi:

    edo 31 "0 5 10"                      -- EDO steps, played as `note`
    edo 31 (escale 31 "major" "0 .. 7")  -- scale degrees in that EDO

`edo` converts steps to fractional semitones (SuperDirt's `note` is
continuous) and tags each event with `edo`, which SuperDirt ignores but the
lanes read. Tidal's own `scale` assumes 12 steps per octave, hence `escale`.
The scale tables live here only; the Haskell is generated from them.
"""

import re

EDOS = (19, 24, 31)
OFF = 12

# Steps per scale degree, octave excluded. 24-EDO's maqamat are their
# common quarter-tone spellings; 31-EDO is meantone-like (fifth = 18).
SCALES: dict[int, dict[str, list[int]]] = {
    19: {
        "major": [0, 3, 6, 8, 11, 14, 17],
        "minor": [0, 3, 5, 8, 11, 13, 16],
        "dorian": [0, 3, 5, 8, 11, 14, 16],
        "pentatonic": [0, 3, 6, 11, 14],
    },
    24: {
        "major": [0, 4, 8, 10, 14, 18, 22],
        "minor": [0, 4, 6, 10, 14, 16, 20],
        "rast": [0, 4, 7, 10, 14, 18, 21],
        "bayati": [0, 3, 6, 10, 14, 16, 20],
        "saba": [0, 3, 6, 8, 14, 16, 20],
        "sikah": [0, 3, 7, 11, 14, 17, 21],
        "hijaz": [0, 2, 8, 10, 14, 16, 20],
    },
    31: {
        "major": [0, 5, 10, 13, 18, 23, 28],
        "minor": [0, 5, 8, 13, 18, 21, 26],
        "mohajira": [0, 4, 9, 13, 18, 22, 27],
        "septimal": [0, 5, 10, 13, 18, 23, 25],
        "orwell": [0, 4, 7, 11, 14, 18, 21, 25, 28],
        "pentatonic": [0, 5, 10, 18, 23],
    },
}

HEADER = re.compile(r"^--\s*selene:\s*tuning\s+(\d+)\s*$")
ESCALE = re.compile(r'\bescale\s+(\d+)\s+"([^"]+)"')


def parse(arg: str) -> int | None:
    """/tuning's argument -> an EDO (12 = off), or None if it isn't one."""
    word = arg.strip().lower().removesuffix("-edo").removesuffix("edo")
    if word in ("off", "12"):
        return OFF
    return int(word) if word.isdigit() and int(word) in EDOS else None


def helpers() -> str:
    """The Haskell selene defines in GHCi when the mode first goes on.
    One :{ :} block; it covers every EDO, so it's sent once a session."""
    table = ", ".join(f'(({k}, "{name}"), {steps})'
                      for k, scales in SCALES.items() for name, steps in scales.items())
    return (
        "seleneEdoScales :: [((Int, String), [Int])]\n"
        f"seleneEdoScales = [{table}]\n"
        "edo :: Note -> Pattern Note -> ControlPattern\n"
        'edo k p = note (fmap (* (12 / k)) p) # pF "edo" (pure (realToFrac k))\n'
        "escale :: Int -> Pattern String -> Pattern Int -> Pattern Note\n"
        "escale k sp p = innerJoin $ (\\name -> step (maybe [0 .. k - 1] id "
        "(lookup (k, name) seleneEdoScales)) <$> p) <$> sp\n"
        "  where step s i = fromIntegral (s !! (i `mod` length s) + k * (i `div` length s))"
    )


def prompt_section(k: int) -> str:
    """What the model is told while the session is in k-EDO."""
    names = SCALES[k]
    listed = "; ".join(f'"{n}" ({len(s)} degrees: 0–{len(s) - 1}, {len(s)} = octave up)'
                       for n, s in names.items())
    first = next(iter(names))
    return f"""\
Microtonal mode: this session is in {k}-EDO ({k} equal steps per octave). \
Write every pitched layer in {k}-EDO; drums and unpitched samples are unchanged.
- `edo {k} (escale {k} "{first}" "0 2 4 7")` plays SCALE DEGREES, exactly like \
`n (scale "major" "0 2 4 7")`: small numbers, 0 = root, the next degree up is 1. \
This is the usual way to write melodies and basslines. {k}-EDO scales: {listed}. \
Only these names work.
- `edo {k} "0 5 10"` plays raw {k}-EDO steps ({k} steps = one octave); use it for \
drones or exact intervals, in place of `note`/`n`.
- Inside `edo` everything counts {k}-EDO steps: transpose with \
`edo {k} (escale {k} "{first}" "0 2 4" |+ "<0 {k // 6}>")`, an octave up is `|+ {k}` there.
- Outside `edo`, `note` counts ordinary semitones: an octave is `|+ note 12` or \
`# octave 4`, never `|+ note {k}`, `|+ edo ...` or `|+ n 12` (n is ignored once note is set).
- Never use note names ("c4"), chord names ("c'maj"), `scale`, `arp` or `up` for pitch here.
Example:
```haskell
d2 $ off 0.25 (|+ note 12) $ edo {k} (escale {k} "{first}" "0 [2 4] <5 3> 7")
  # s "superpiano" # legato 1.2 # gain 0.8
```"""


def back_to_12() -> str:
    """Said once to the model after the mode goes off, if what plays used it."""
    return ("Microtonal mode is now off: rewrite any pitched layers that use `edo` or "
            "`escale` in ordinary 12-EDO (`note`, `n`, `scale`).")


def unknown_scales(code: str) -> set[str]:
    """`escale K "name"` uses that aren't in the tables (they'd play chromatic)."""
    bad = set()
    for k, names in ESCALE.findall(code):
        known = SCALES.get(int(k), {})
        for name in re.findall(r"[A-Za-z]\w*", names):  # "<major minor>" patterns
            if name not in known:
                bad.add(f'{k} "{name}"')
    return bad


def unknown_scales_message(bad: set[str]) -> str:
    listing = " ".join(f"{k}-EDO: {', '.join(names)}." for k, names in
                       ((k, SCALES[k]) for k in EDOS))
    return (f"These escale names don't exist and would play chromatically: "
            f"{', '.join(sorted(bad))}. Scales: {listing} "
            "Return the complete code using only those.")


EDO_USE = re.compile(r"\b(?:edo|escale)\s+(\d+)")


def uses_edo(code: str) -> bool:
    return edo_of(code) is not None


def edo_of(code: str) -> int | None:
    """The EDO code is written in (its first `edo N` / `escale N`), if any."""
    for k in EDO_USE.findall(code):
        if int(k) in EDOS:
            return int(k)
    return None


def header(k: int) -> str:
    return f"-- selene: tuning {k}"


def split_header(text: str) -> tuple[int | None, str]:
    """A .tidal file's first-line tuning header, and the text without it."""
    first, _, rest = text.partition("\n")
    if (m := HEADER.match(first.strip())) and int(m.group(1)) in EDOS:
        return int(m.group(1)), rest
    return None, text


def cents_off(semitones: float) -> float:
    """How far a pitch is from the nearest 12-EDO note, in cents (signed)."""
    return (semitones - round(semitones)) * 100
