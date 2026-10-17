"""MIDI keyboard in, Tidal mini-notation out.

Notes are kept as they're played. On capture, the last phrase (everything
since a gap of a cycle or more) is placed on Tidal's cycle grid and written
as one layer: `n "<[c5 ~ [e5 g5] ~] [~ a5 ~ ~]>" # s "supersquare"`.
"""

import math
import socket
import statistics
import struct
import time
from dataclasses import dataclass, field
from typing import Callable

from .superdirt import DIRT_PORT

NAMES = ["c", "cs", "d", "ds", "e", "f", "fs", "g", "gs", "a", "as", "b"]
# Steps per cycle a phrase can be placed on, and the order they're preferred
# in when two fit equally well.
GRIDS = (4, 8, 3, 6, 16, 12)
# Each step of grid resolution costs this much (in cycles of timing error),
# so a slightly loose 8th stays an 8th rather than becoming a 16th. Triplet
# steps cost half again, so they're only chosen when they're clearly meant.
GRID_COST = 0.002
TRIPLET_COST = 1.5
CHORD_SECONDS = 0.03  # notes struck this close together are one chord
LENGTHS = (1, 2, 4)  # cycles a capture can be
WIDTH = 60  # longer captures are written over several lines
GAIN_SPREAD = 0.15  # velocities spread less than this (of 127) leave gain out
PHRASE_GAP = 1.0  # cycles of silence that end one phrase and start the next
KEEP_SECONDS = 120.0
MONITOR_ORBIT = 10  # d11: auditioning what's played, outside the layers
# Ports that aren't keyboards: macOS's IAC bus, ALSA's Through port, and the
# DAW-control port controllers like Novation's Launchkey add beside their keys.
NOT_KEYBOARDS = ("iac", "through", "daw")


def note_name(midi: int) -> str:
    """Tidal's names: MIDI 60 is c5 (n 0)."""
    return f"{NAMES[midi % 12]}{midi // 12}"


@dataclass
class Played:
    """A key as played: seconds since the epoch, end None while it's held."""
    on: float
    note: int
    velocity: int
    off: float | None = None


@dataclass
class NoteBuffer:
    """Notes as they're played, the sustain pedal holding their ends."""
    notes: list[Played] = field(default_factory=list)
    held: dict[int, Played] = field(default_factory=dict)
    pedal: bool = False
    pedalled: list[Played] = field(default_factory=list)

    def note_on(self, note: int, velocity: int, t: float) -> None:
        if velocity == 0:
            self.note_off(note, t)
            return
        self.note_off(note, t)  # a key struck again ends its last note
        played = Played(t, note, velocity)
        self.held[note] = played
        self.notes.append(played)
        self.notes = [p for p in self.notes if p.on > t - KEEP_SECONDS]

    def note_off(self, note: int, t: float) -> None:
        if (played := self.held.pop(note, None)) is None:
            return
        if self.pedal:
            self.pedalled.append(played)
        else:
            played.off = t

    def sustain(self, down: bool, t: float) -> None:
        self.pedal = down
        if not down:
            for played in self.pedalled:
                played.off = t
            self.pedalled.clear()


@dataclass
class Note:
    """A played note placed in Tidal's time: cycle position, held seconds."""
    cycle: float
    note: int
    velocity: int
    seconds: float


def last_phrase(notes: list[Note]) -> list[Note]:
    """The notes since the last gap of PHRASE_GAP cycles."""
    notes = sorted(notes, key=lambda n: n.cycle)
    start = 0
    for i in range(1, len(notes)):
        if notes[i].cycle - notes[i - 1].cycle >= PHRASE_GAP + 0.25:
            start = i
    return notes[start:]


@dataclass
class Capture:
    code: str
    cycles: int
    start: int  # the cycle it was played from
    notes: int
    trimmed: bool = False  # longer than LENGTHS allow: only the end was kept


def transcribe(notes: list[Note], sound: str, cps: float, orbit: str = "d1") -> Capture | None:
    """Write a phrase as a Tidal layer, its first note's cycle as cycle 1."""
    if not notes:
        return None
    notes = sorted(notes, key=lambda n: n.cycle)
    events = _chords(notes, CHORD_SECONDS * cps)
    # A note a hair early for a cycle belongs to it.
    first = math.floor(events[0][0] + 1 / 32)
    span = math.floor(events[-1][0] + 1 / 32) - first + 1
    trimmed = span > max(LENGTHS)
    if trimmed:
        first += span - max(LENGTHS)
        events = [e for e in events if e[0] + 1 / 32 >= first]
    length = next(n for n in LENGTHS if n >= min(span, max(LENGTHS)))
    cycles: list[list[tuple[float, list[Note]]]] = [[] for _ in range(length)]
    for pos, chord in events:
        rel = pos - first
        i = min(int(math.floor(rel + 1 / 32)), length - 1)
        cycles[i].append((min(max(rel - i, 0.0), 0.999), chord))
    grids = [_place(c) for c in cycles]
    pitches = [_render(g, _pitch) for g in grids]
    controls = [f's "{sound}"']
    velocities = [n.velocity for n in notes]
    gains = None
    if (max(velocities) - min(velocities)) / 127 > GAIN_SPREAD:
        gains = [_render(g, _gain) for g in grids]
    held = statistics.median(n.seconds for n in notes)
    sustain = f"sustain {min(max(held, 0.1), 4.0):.2g}"
    line = f'{orbit} $ n "{_join(pitches)}" # ' + " # ".join(
        controls + ([f'gain "{_join(gains)}"'] if gains else []) + [sustain])
    if len(line) <= WIDTH:
        return Capture(line, length, first, len(notes), trimmed)
    # Too long for the editor: a cycle a line (cat plays them in turn, as
    # <…> does) and a control a line.
    lines = [f"{orbit} $ n {_cycles(pitches, '  ')}"]
    lines += [f"  # {c}" for c in controls]
    if gains:
        lines.append(f"  # gain {_cycles(gains, '    ')}")
    lines.append(f"  # {sustain}")
    return Capture("\n".join(lines), length, first, len(notes), trimmed)


def _chords(notes: list[Note], window: float) -> list[tuple[float, list[Note]]]:
    events: list[tuple[float, list[Note]]] = []
    for n in notes:
        if events and n.cycle - events[-1][0] <= window:
            events[-1][1].append(n)
        else:
            events.append((n.cycle, [n]))
    return events


def _place(events: list[tuple[float, list[Note]]]) -> list[list[Note] | None]:
    """The grid that fits a cycle's onsets best, with each onset on it."""
    if not events:
        return [None]
    best, best_cost = None, math.inf
    for g in GRIDS:
        steps: list[list[Note] | None] = [None] * g
        error = 0.0
        for pos, chord in events:
            k = round(pos * g)
            if k == g:  # rounds onto the next cycle's downbeat; keep it at the end
                k = g - 1
            error += abs(pos - k / g)
            if steps[k] is not None:  # two onsets on one step: too coarse
                break
            steps[k] = chord
        else:
            cost = error + GRID_COST * g * len(events) * (TRIPLET_COST if g % 3 == 0 else 1)
            if cost < best_cost - 1e-9:
                best, best_cost = steps, cost
    if best is None:  # denser than any grid: the finest one, sharing steps
        g = max(GRIDS)
        best = [None] * g
        for pos, chord in events:
            k = min(round(pos * g), g - 1)
            best[k] = (best[k] or []) + chord
    return best


def _pitch(chord: list[Note]) -> str:
    names = sorted({n.note for n in chord})
    return note_name(names[0]) if len(names) == 1 else \
        "[" + ",".join(note_name(n) for n in names) + "]"


def _gain(chord: list[Note]) -> str:
    v = statistics.mean(n.velocity for n in chord) / 127
    return f"{0.7 + 0.5 * v:.2f}"


def _render(steps: list[list[Note] | None], token: Callable[[list[Note]], str]) -> str:
    """A cycle's grid as mini-notation: beats as groups, a beat that only
    starts with a note written as just that note (same onset)."""
    g = len(steps)
    if steps[0] is not None and all(s is None for s in steps[1:]):
        return token(steps[0])
    beats = 4 if g % 4 == 0 else 2 if g == 6 else g
    size = g // beats
    out = [_beat(steps[i:i + size], token) for i in range(0, g, size)]
    if all(t == "~" for t in out):
        return "~"
    return " ".join(out)


def _beat(steps: list[list[Note] | None], token: Callable[[list[Note]], str]) -> str:
    if all(s is None for s in steps):
        return "~"
    if steps[0] is not None and all(s is None for s in steps[1:]):
        return token(steps[0])
    while len(steps) % 2 == 0 and all(s is None for s in steps[1::2]):
        steps = steps[::2]
    return "[" + " ".join("~" if s is None else token(s) for s in steps) + "]"


def _cycles(cycles: list[str], indent: str) -> str:
    """A pattern for multi-line code: a string, or cat with a cycle a line."""
    if len(cycles) == 1:
        return f'"{cycles[0]}"'
    rows = ",\n".join(f'{indent}  "{c}"' for c in cycles)
    return f"(cat [\n{rows}\n{indent}])"


def _join(cycles: list[str]) -> str:
    if len(cycles) == 1:
        return cycles[0]
    return "<" + " ".join(c if " " not in c else f"[{c}]" for c in cycles) + ">"


class Monitor:
    """Plays keys straight to SuperDirt as they're struck, so the player hears
    themselves. Untimed, so SuperDirt plays it at once."""

    def __init__(self, port: int = DIRT_PORT):
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    @staticmethod
    def _s(text: str) -> bytes:
        b = text.encode() + b"\0"
        return b + b"\0" * (-len(b) % 4)

    def play(self, sound: str, note: int, velocity: int, sustain: float = 0.6) -> None:
        args = [("s", "s"), (sound, "s"), ("n", "s"), (float(note - 60), "f"),
                ("gain", "s"), (0.7 + 0.5 * velocity / 127, "f"),
                ("sustain", "s"), (sustain, "f"), ("orbit", "s"), (MONITOR_ORBIT, "i")]
        tags = "," + "".join(t for _, t in args)
        body = b"".join(self._s(v) if t == "s" else
                        struct.pack(">f" if t == "f" else ">i", v) for v, t in args)
        try:
            self.sock.sendto(self._s("/dirt/play") + self._s(tags) + body,
                             ("127.0.0.1", self.port))
        except OSError:
            pass


def keyboards(names: list[str]) -> list[str]:
    return [n for n in names if not any(k in n.lower() for k in NOT_KEYBOARDS)]


class MidiInput:
    """One MIDI input port. Messages arrive on rtmidi's thread; `deliver` is
    expected to hand them to the app's loop."""

    def __init__(self, deliver: Callable[[object, float], None]):
        self.deliver = deliver
        self.port = None
        self.name: str | None = None

    @staticmethod
    def available() -> list[str]:
        try:
            import mido
            return list(dict.fromkeys(mido.get_input_names()))
        except Exception:  # noqa: BLE001 - no backend, no CoreMIDI/ALSA
            return []

    def open(self, name: str) -> None:
        import mido
        self.close()
        self.port = mido.open_input(name, callback=lambda m: self.deliver(m, time.time()))
        self.name = name

    def close(self) -> None:
        if self.port is not None:
            try:
                self.port.close()
            except Exception:  # noqa: BLE001
                pass
        self.port, self.name = None, None
