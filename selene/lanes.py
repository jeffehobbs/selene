"""Lanes: what each dN is playing, one phrase wide, wiping like a tracker.

Fed from Flow's tap (every event Tidal plays, with its play time). Events are
drawn when they *sound*, not when they arrive (~0.15 s early), at their
position in the 4-cycle phrase. Each pass overwrites the last: ahead of the
playhead you see the previous pass dimmed, so a repeating pattern is a still
picture and whatever changed stands out.
"""

import time
from dataclasses import dataclass

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Static

PHRASE = 4
HIT_SECONDS = 0.09  # how long a row's chip lights up on a hit
SHADES = "░▒▓█"
NOTE_NAMES = "CcDdEFfGgAaB"  # lowercase = sharp
PALETTE = ("#e5c07b", "#61afef", "#98c379", "#e06c75", "#c678dd", "#56b6c2",
           "#d19a66", "#a9b1d6", "#f7768e", "#73daca")


@dataclass
class Hit:
    phrase: int
    pos: float  # cycles into the phrase, 0..4
    glyph: str
    color: str
    level: float  # gain; 0 = sent but silent (e.g. a Flow drop)


def color_for(sound: str) -> str:
    return PALETTE[sum(map(ord, sound)) % len(PALETTE)]


class LaneModel:
    """Events by orbit, placed in time. Pure: tests drive it with fake clocks."""

    def __init__(self, synths: set[str] = frozenset()):
        self.synths = set(synths)
        self.pending: list[tuple[float, str, Hit]] = []
        self.hits: dict[str, list[Hit]] = {}
        self.last_hit: dict[str, float] = {}
        self.last_event: dict[str, float] = {}  # any event, audible or not
        # orbit -> {sound label: (cycle last heard, color)}
        self.sounds: dict[str, dict[str, tuple[float, str]]] = {}

    def add(self, timetag: float, ev: dict) -> None:
        pid, cycle = str(ev.get("_id_", "")), ev.get("cycle")
        if not pid and ev.get("orbit") is not None:
            # Tidal's transitions (xfadeIn) drop `_id_`, e.g. the outgoing side
            # of a Flow evolution or a /fade. dN routes to orbit N-1.
            pid = str(int(float(ev["orbit"])) + 1)
        if not pid.isdigit() or cycle is None:
            return
        cycle = float(cycle) + 1e-6
        phrase = int(cycle // PHRASE)
        hit = Hit(phrase, cycle - phrase * PHRASE, *self._look(ev))
        self.pending.append((timetag, f"d{pid}", hit))
        sound = str(ev.get("s", "?"))
        if sound not in self.synths and ev.get("n") is not None and float(ev["n"]) >= 1:
            sound += f":{int(float(ev['n']))}"
        if hit.level > 1e-3:
            self.sounds.setdefault(f"d{pid}", {})[sound] = (cycle, hit.color)

    def _look(self, ev: dict) -> tuple[str, str, float]:
        sound = str(ev.get("s", "?"))
        level = float(ev.get("gain", 1.0))
        pitch = ev.get("note", ev.get("n") if sound in self.synths else None)
        if pitch is not None:  # melodic: the note's letter
            glyph = NOTE_NAMES[round(float(pitch)) % 12]
        else:
            glyph = SHADES[min(3, int(level / 0.3))] if level > 1e-3 else "·"
        return glyph, color_for(sound), level

    def advance(self, now: float) -> None:
        """Move events that have sounded by `now` onto the lanes."""
        due = [p for p in self.pending if p[0] <= now]
        if not due:
            return
        self.pending = [p for p in self.pending if p[0] > now]
        for t, orbit, hit in due:
            self.hits.setdefault(orbit, []).append(hit)
            self.last_event[orbit] = max(t, self.last_event.get(orbit, 0))
            if hit.level > 1e-3:
                self.last_hit[orbit] = t

    def prune(self, phrase: int) -> None:
        for orbit, hits in self.hits.items():
            self.hits[orbit] = [h for h in hits if h.phrase >= phrase - 1]

    def lit(self, orbit: str, now: float) -> bool:
        return now - self.last_hit.get(orbit, -1e9) < HIT_SECONDS

    def row(self, orbit: str, width: int, cycle: float | None) -> Text:
        """One lane as text: │cycle│cycle│cycle│cycle│ with the playhead."""
        steps = next((s for s in (32, 16, 8, 4) if PHRASE * s + PHRASE + 1 <= width), 4)
        cells: list[tuple[str, str] | None] = [None] * (PHRASE * steps)
        loud = [0.0] * (PHRASE * steps)
        head = -1
        if cycle is not None:
            phrase, pos = int(cycle // PHRASE), cycle % PHRASE
            head = int(pos * steps)
            for h in self.hits.get(orbit, []):
                if h.phrase == phrase and h.pos <= pos:
                    style = h.color if h.level > 1e-3 else f"{h.color} dim"
                elif h.phrase == phrase - 1 and h.pos > pos:  # last pass, not yet overwritten
                    style = f"{h.color} dim"
                else:
                    continue
                i = min(len(cells) - 1, int(h.pos * steps))
                if cells[i] is None or h.level > loud[i]:
                    cells[i], loud[i] = (h.glyph, style), h.level
        text = Text()
        for i, cell in enumerate(cells):
            if i % steps == 0:
                text.append("│", style="dim")
            glyph, style = cell if cell else ("·" if i % (steps // 4 or 1) == 0 else " ", "dim")
            if i == head:
                style += " reverse"
            text.append(glyph, style=style)
        text.append("│", style="dim")
        # What it's been playing this past phrase, in the space that's left.
        room = width - len(text) - 2
        if cycle is not None and room > 3:
            recent = sorted(((c, name, color) for name, (c, color) in
                             self.sounds.get(orbit, {}).items() if c > cycle - PHRASE),
                            reverse=True)
            text.append("  ")
            for _, name, color in recent:
                if len(name) + 1 > room:
                    break
                text.append(name + " ", style=color)
                room -= len(name) + 1
        return text


class LaneStrip(Widget):
    """The cells of one lane."""

    DEFAULT_CSS = "LaneStrip { height: 1; width: 1fr; }"

    def __init__(self, model: LaneModel, orbit: str, clock):
        super().__init__()
        self.model, self.orbit, self.clock = model, orbit, clock

    def render(self) -> Text:
        return self.model.row(self.orbit, self.size.width, self.clock.cycle_at(time.time()))


class Lanes(Vertical):
    """A row per orbit: its chip (click to mute, shift-click to solo) and its lane."""

    DEFAULT_CSS = """
    Lanes { height: auto; border: round $primary 50%; border-title-color: $text-muted;
            padding: 0 1; }
    Lanes > Horizontal { height: 1; }
    Lanes #ruler { height: 1; color: $text-muted; }
    Lanes .held-divider { height: 1; color: $warning 60%; }
    """

    def __init__(self, model: LaneModel, clock, chip_factory, **kwargs):
        super().__init__(**kwargs)
        self.model, self.clock, self.chip_factory = model, clock, chip_factory
        self.orbits: list[str] = []
        self.held: set[str] = set()
        self.chips: list = []

    def compose(self) -> ComposeResult:
        yield Static(id="ruler")

    def on_mount(self) -> None:
        self.border_title = "lanes"
        self.set_interval(1 / 30, self.tick)

    def set_orbits(self, orbits: list[str], held: set[str] = frozenset()) -> list:
        """Rebuild rows if the orbits changed; held ones go below a divider.
        Returns the row chips."""
        held = set(held) & set(orbits)
        if orbits != self.orbits or held != self.held:
            self.orbits, self.held = list(orbits), held
            for row in list(self.query("Lanes > Horizontal, Lanes > .held-divider")):
                row.remove()
            self.chips = [self.chip_factory(orbit) for orbit in orbits]
            divided = False
            for orbit, chip in zip(orbits, self.chips):
                if orbit in held and not divided and len(held) < len(orbits):
                    self.mount(Static("╌" * 6 + " held", classes="held-divider"))
                    divided = True
                self.mount(Horizontal(chip, LaneStrip(self.model, orbit, self.clock)))
        return list(self.chips)

    def tick(self) -> None:
        if not self.display or not self.orbits:
            return
        now = time.time()
        self.model.advance(now)
        cycle = self.clock.cycle_at(now)
        if cycle is not None:
            self.model.prune(int(cycle // PHRASE))
            self._ruler(cycle)
        for chip in self.chips:
            chip.set_class(self.model.lit(chip.orbit, now), "hit")
        for strip in self.query(LaneStrip):
            strip.refresh()

    def _ruler(self, cycle: float) -> None:
        strips = list(self.query(LaneStrip))
        if not strips:
            return
        width = strips[0].size.width
        steps = next((s for s in (32, 16, 8, 4) if PHRASE * s + PHRASE + 1 <= width), 4)
        first = int(cycle // PHRASE) * PHRASE
        ruler = " " * 6  # under the chips
        for k in range(PHRASE):
            ruler += f" {first + k}".ljust(steps + 1)[:steps + 1]
        self.query_one("#ruler", Static).update(ruler)
