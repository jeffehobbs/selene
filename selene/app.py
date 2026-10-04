"""selene: words in, TidalCycles out, sound via GHCi -> SuperDirt."""

import argparse
import asyncio
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Input, RichLog, Static, TextArea

from . import blocks, catalog
from .files import DEFAULT_DIR, ConfirmScreen, PathScreen, resolve
from .flow import (DEFAULT_DEPTH, DEPTHS, CtrlSender, FlowDirector, TidalClock, ctrl_name,
                   free_udp_port)
from .ghci import BUNDLED_BOOT, Ghci
from .lanes import LaneModel, Lanes
from .llm import (Ollama, build_system_prompt, evolve_message, fix_message,
                  unknown_sounds_message, user_message)
from .superdirt import SuperDirt, dirt_status

SESSIONS = Path.home() / ".local/share/selene"
HISTORY_TURNS = 4  # prompt/reply pairs of context sent back to the model
# Test hook: run Flow's clock faster than real time.
FLOW_SPEED = float(os.environ.get("SELENE_FLOW_SPEED", "1"))


class PromptInput(Input):
    """Single-line prompt with shell-style up/down history."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.history: list[str] = []
        self._pos = 0

    def remember(self, text: str) -> None:
        if text and (not self.history or self.history[-1] != text):
            self.history.append(text)
        self._pos = len(self.history)

    def on_key(self, event) -> None:
        if event.key not in ("up", "down") or not self.history:
            return
        event.prevent_default()
        step = -1 if event.key == "up" else 1
        self._pos = max(0, min(len(self.history), self._pos + step))
        self.value = self.history[self._pos] if self._pos < len(self.history) else ""
        self.cursor_position = len(self.value)


@dataclass
class Held:
    """A layer still playing in Tidal that the current code doesn't mention."""
    stmt: str | None  # None: heard on the tap, but not from code selene ran
    source: str  # where it came from, for the chip's tooltip


class OrbitChip(Static):
    """A clickable dN in the status bar: click mutes/unmutes, shift-click solos."""

    def __init__(self, orbit: str):
        super().__init__(f" {orbit} ")
        self.orbit = orbit

    def set_muted(self, muted: bool) -> None:
        self.set_class(muted, "muted")
        self.tooltip = f"{'unmute' if muted else 'mute'} {self.orbit} · shift-click solo"

    def on_click(self, event: events.Click) -> None:
        self.app.toggle_orbit(self.orbit, solo=event.shift)


class Selene(App):
    TITLE = "selene"
    CSS = """
    Screen { layout: vertical; }
    #bar { height: 1; padding: 0 1; background: $panel; }
    #status, #orbits { width: auto; }
    OrbitChip { width: auto; margin-left: 1; color: $success; background: $success 15%; }
    OrbitChip:hover { background: $success 35%; }
    OrbitChip.muted { color: $text-muted; background: $error 15%; text-style: strike; }
    OrbitChip.muted:hover { background: $error 30%; }
    OrbitChip.hit { background: $success 60%; color: $text; }
    OrbitChip.held { color: $warning; background: $warning 12%; text-style: italic; }
    OrbitChip.held.hit { background: $warning 45%; }
    OrbitChip.fading { text-style: italic dim; }
    #main { height: 1fr; }
    #code { width: 1fr; border: round $primary 50%; }
    #code, #log { border-title-color: $text-muted; }
    #code.playing { border: round $success; }
    #code.failed { border: round $error; }
    #code.busy { border: round $warning; }
    #log { width: 1fr; border: round $primary 50%; padding: 0 1; overflow-x: hidden;
           scrollbar-size-vertical: 1; }
    #prompt { border: round $accent; }
    """
    BINDINGS = [
        Binding("escape", "hush", "Hush", priority=True),
        Binding("ctrl+e", "play_editor", "Play", priority=True),
        Binding("ctrl+r", "retry", "Retry", priority=True),
        Binding("ctrl+n", "new_session", "New", priority=True),
        Binding("ctrl+f", "flow", "Flow", priority=True),
        Binding("ctrl+s", "save", "Save", priority=True),
        Binding("ctrl+o", "open", "Open", priority=True),
        Binding("ctrl+l", "lanes", "Lanes", priority=True),
        Binding("ctrl+b", "boot_dirt", "SuperDirt", priority=True),
        Binding("ctrl+q", "quit", "Quit", priority=True),
    ]

    def __init__(self, args: argparse.Namespace):
        super().__init__()
        self.args = args
        self.ollama = Ollama(args.ollama_url, args.model)
        self.ghci = Ghci(args.ghci, Path(args.boot), on_output=self._ghci_line)
        self.dirt = SuperDirt(on_output=self._dirt_line)
        self.system_prompt = build_system_prompt(catalog.sample_banks(), catalog.synth_names())
        self.known_sounds = set(catalog.sample_banks()) | set(catalog.synth_names())
        self.history: list[dict] = []
        self.playing_code = ""  # the current layers: what the editor last played
        # Layers Tidal keeps playing that the current code doesn't mention
        # (e.g. from a file opened before this one): orbit -> Held.
        self.held: dict[str, Held] = {}
        self.current_label = "earlier code"
        self.fading: dict[str, object] = {}  # orbit -> token of its pending fade
        self.last_known: dict[str, float] = {}  # orbit -> when selene last tracked it
        self._known_prev: set[str] = set()
        self.muted: set[str] = set()
        self.last_prompt = ""
        self.fresh = False  # next prompt ignores what's playing
        # Whatever the model last wrote into the editor (streaming, failed, or
        # cut off by esc). It can differ from what's playing without being the
        # player's edit.
        self.model_text = ""
        self.state = {"model": args.model, "ghci": "booting", "dirt": "?", "llm": "", "flow": ""}
        # Flow: off at launch. `flow` lives on through the glide home after
        # Flow is turned off; `flow_on` is whether new code gets the wrappers.
        self.flow: FlowDirector | None = None
        self.flow_on = False
        self.flow_ctrl: CtrlSender | None = None
        self.flow_timer = None
        self.flow_t0 = 0.0
        self.flow_depth = args.flow_depth
        self.flow_pending: list[asyncio.TimerHandle] = []  # stepped sends in flight
        self.flow_dtime_cps: float | None = None
        # Tidal's cycle position, from the events it copies to our tap port.
        self.clock = TidalClock()
        self.lane_model = LaneModel(synths=set(catalog.synth_names()))
        self.lanes_on = True
        self.tap_port = free_udp_port()
        # The .tidal file the editor was last saved to / opened from.
        self.current_file: Path | None = None
        self.saved_text = ""
        self.file_dir = Path(args.dir).expanduser()
        SESSIONS.mkdir(parents=True, exist_ok=True)
        self.session_file = SESSIONS / f"{datetime.now():%Y-%m-%d}.tidal"

    def compose(self) -> ComposeResult:
        with Horizontal(id="bar"):
            yield Static(id="status")
            yield Horizontal(id="orbits")
        yield Lanes(self.lane_model, self.clock, OrbitChip, id="lanes")
        with Horizontal(id="main"):
            yield RichLog(id="log", wrap=True, markup=True, max_lines=2000)
            yield TextArea("", id="code", show_line_numbers=True, tab_behavior="indent",
                           soft_wrap=False)
        yield PromptInput(placeholder="describe some music…  (!code runs raw Tidal)",
                          id="prompt")
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#code").border_title = "pattern"
        self.query_one("#log").border_title = "ghci"
        self.query_one("#prompt").focus()
        self._render_bar()
        self.start_services()
        self.set_interval(1.0, self._discover)

    # ── status ────────────────────────────────────────────────────────────

    def _render_bar(self) -> None:
        s = self.state
        bar = Text()
        bar.append("selene", style="bold")
        dot = {"ready": "green", "booting": "yellow", "dead": "red"}.get(s["ghci"], "yellow")
        dirt = {"listening": "green", "booting": "yellow"}.get(s["dirt"], "red")
        bar.append("   ")
        bar.append("● ", style=dot).append(f"ghci {s['ghci']}")
        bar.append("   ")
        label = {"sclang": "not started", "?": "checking"}.get(s["dirt"], s["dirt"])
        bar.append("● ", style=dirt).append(f"superdirt {label}")
        bar.append("   ")
        bar.append(s["model"], style="dim")
        if s["llm"]:
            bar.append(f"  {s['llm']}", style="italic yellow")
        if s["flow"]:
            bar.append(f"   ☯{self.flow_depth} {s['flow']}", style="magenta")
        orbits = self._all_orbits()
        # Remember when each layer was last tracked, including the moment one
        # stops being tracked (see _discover).
        now = time.time()
        for orbit in self._known_prev | set(orbits):
            self.last_known[orbit] = now
        self._known_prev = set(orbits)
        lanes = self.lanes_on and bool(orbits)
        if orbits and not lanes:
            bar.append("   ▶", style="green")
        self.query_one("#status", Static).update(bar)
        # With the lanes showing, the chips head their rows instead of the bar.
        self.query_one("#lanes", Lanes).display = lanes
        self.query_one("#orbits").display = not lanes
        self._sync_orbits(orbits)

    def _sync_orbits(self, orbits: list[str]) -> None:
        box = self.query_one("#orbits", Horizontal)
        chips = list(box.query(OrbitChip))
        if [c.orbit for c in chips] != orbits:
            box.remove_children()
            chips = [OrbitChip(o) for o in orbits]
            box.mount(*chips)
        chips += self.query_one("#lanes", Lanes).set_orbits(orbits, set(self.held))
        for chip in chips:
            chip.set_muted(chip.orbit in self.muted)
            chip.set_class(chip.orbit in self.held, "held")
            chip.set_class(chip.orbit in self.fading, "fading")
            if chip.orbit in self.held:
                n = chip.orbit[1:]
                chip.tooltip = (f"held from {self.held[chip.orbit].source} · "
                                f"/fade {n} · /stop {n} · /take {n}")
        # An orbit that left the code keeps its Tidal mute flag; clear it so
        # it isn't silent when the model brings it back.
        if gone := self.muted - set(orbits):
            self.muted -= gone
            self._apply_mutes({o: False for o in gone})

    def _all_orbits(self) -> list[str]:
        """Current layers first, then held ones."""
        current = blocks.sounding_orbits(self.playing_code)
        held = sorted((o for o in self.held if o not in current), key=lambda o: int(o[1:]))
        return current + held

    def _replace_current(self, code: str, label: str) -> None:
        """New code becomes the current layers. Tidal keeps playing any dN the
        new code doesn't mention, so those become held rather than vanishing."""
        stmts = blocks.split_statements(code)
        if any(st.strip() == "hush" for st in stmts):
            self.held.clear()
        mentioned = set(blocks.orbits_used(code))
        for stmt in blocks.split_statements(self.playing_code):
            orbits = blocks.sounding_orbits(stmt)
            if orbits and orbits[0] not in mentioned:
                self.held[orbits[0]] = Held(stmt, self.current_label)
        for orbit in mentioned:
            self.held.pop(orbit, None)
            self.fading.pop(orbit, None)
        self.playing_code = code
        self.current_label = label

    def toggle_orbit(self, orbit: str, solo: bool = False) -> None:
        orbits = self._all_orbits()
        if solo:
            soloed = self.muted == set(orbits) - {orbit}
            target = set() if soloed else set(orbits) - {orbit}
        else:
            target = self.muted ^ {orbit}
        changes = {o: o in target for o in orbits if (o in target) != (o in self.muted)}
        self.muted = target & set(orbits)
        if self.flow:
            self.flow.set_muted(self.muted, self._flow_now())
        self._render_bar()
        self._apply_mutes(changes)

    @work(group="mute")
    async def _apply_mutes(self, changes: dict[str, bool]) -> None:
        for orbit, muted in sorted(changes.items()):
            result = await self.ghci.set_muted(orbit, muted)
            if not result.ok:
                self.log_line(f"couldn't {'mute' if muted else 'unmute'} {orbit}", "red")
        if changes:
            on = [o for o, m in sorted(changes.items()) if m]
            off = [o for o, m in sorted(changes.items()) if not m]
            parts = ([f"muted {' '.join(on)}"] if on else []) + \
                    ([f"unmuted {' '.join(off)}"] if off else [])
            self.log_line(" · ".join(parts), "magenta")

    def _set(self, **kw) -> None:
        self.state.update(kw)
        self._render_bar()

    def log_line(self, text: str, style: str = "") -> None:
        log = self.query_one("#log", RichLog)
        log.write(Text(text, style=style) if style else Text(text))

    def _ghci_line(self, line: str) -> None:
        style = "red" if ("error" in line or "Exception" in line) else "dim"
        if "Connected to SuperDirt" in line:
            style = "green"
            self._set(dirt="listening")
        self.log_line(line, style)

    def _dirt_line(self, line: str) -> None:
        self.log_line(f"sc  {line}", "blue dim")
        if "SuperDirt: listening" in line:
            self._set(dirt="listening")

    def _code_state(self, cls: str | None) -> None:
        code = self.query_one("#code", TextArea)
        code.remove_class("playing", "failed", "busy")
        if cls:
            code.add_class(cls)

    # ── services ──────────────────────────────────────────────────────────

    @work(group="services")
    async def start_services(self) -> None:
        self._set(dirt=await asyncio.to_thread(dirt_status))
        if self.args.superdirt and self.state["dirt"] == "off":
            self.action_boot_dirt()
        elif self.state["dirt"] == "sclang":
            self.log_line("sclang is running but SuperDirt isn't answering; "
                          "run SuperDirt.start in SuperCollider", "yellow")
        try:
            model = await self.ollama.resolve_model()
            self._set(model=model)
        except Exception as e:  # noqa: BLE001 - surface any Ollama failure
            self.log_line(f"ollama: {e}", "red")
            self._set(model=f"{self.args.model} (unavailable)")
        try:
            await self._listen_tap()
            result = await self.ghci.start(ctrl_port=free_udp_port(), tap_port=self.tap_port)
        except FileNotFoundError:
            self.log_line(f"can't run {self.args.ghci!r}; install GHC + tidal", "red")
            self._set(ghci="dead")
            return
        self._set(ghci="ready" if result.ok else "dead")
        if not result.ok:
            self.log_line("GHCi failed to boot Tidal:\n" + result.error_text, "red")

    # ── actions ───────────────────────────────────────────────────────────

    @on(Input.Submitted, "#prompt")
    def submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        prompt = self.query_one(PromptInput)
        prompt.remember(text)
        prompt.value = ""
        if not text:
            return
        if text.startswith("!"):
            self.evaluate(text[1:].strip(), source="raw")
        elif text.startswith("/"):
            self.command(text[1:])
        else:
            self.last_prompt = text
            self.generate(text)

    def command(self, cmd: str) -> None:
        name, _, arg = cmd.partition(" ")
        if name == "hush":
            self.action_hush()
        elif name == "cps" and arg:
            self.evaluate(f"setcps ({arg})", source="raw")
        elif name == "bpm" and arg:
            self.evaluate(f"setcps ({arg}/60/4)", source="raw")
        elif name == "model" and arg:
            self.ollama.model = self.ollama.requested = arg.strip()
            self._set(model=arg.strip())
            self.run_worker(self._resolve_model(), group="services")
        elif name in ("mute", "unmute", "solo"):
            self.orbit_command(name, arg)
        elif name in ("fade", "stop", "take"):
            self.held_command(name, arg.strip())
        elif name == "flow":
            self.flow_command(arg.strip())
        elif name == "save":
            self._save_as(arg.strip()) if arg.strip() else self.action_save()
        elif name == "open":
            self.action_open(arg.strip())
        elif name == "new":
            self.action_new_session()
        else:
            self.log_line("commands: /hush /cps N /bpm N /mute N /unmute [N] /solo N "
                          "/fade N|held /stop N|held /take N /flow [1-5] /save [NAME] /open [NAME] "
                          "/model NAME /new", "yellow")

    def orbit_command(self, name: str, arg: str) -> None:
        orbits = self._all_orbits()
        wanted = [f"d{a.lstrip('d')}" for a in arg.split()]
        if name == "unmute" and not wanted:  # bare /unmute: everything
            wanted = sorted(self.muted)
        if not wanted or any(o not in orbits for o in wanted):
            self.log_line(f"playing orbits: {' '.join(orbits) or 'none'}", "yellow")
            return
        if name == "solo":
            self.toggle_orbit(wanted[0], solo=True)
            return
        for orbit in wanted:
            if (orbit in self.muted) != (name == "mute"):
                self.toggle_orbit(orbit)

    # ── held layers ───────────────────────────────────────────────────────

    def _held_code(self) -> str:
        return "\n".join(h.stmt for h in self.held.values() if h.stmt)

    def held_command(self, name: str, arg: str) -> None:
        """/fade N|held, /stop N|held, /take N."""
        orbits = self._all_orbits()
        if arg == "held" and name != "take":
            targets = [o for o in orbits if o in self.held]
        else:
            targets = [f"d{a.lstrip('d')}" for a in arg.split()]
        if not targets or any(o not in orbits for o in targets):
            held = " ".join(o for o in orbits if o in self.held) or "none"
            self.log_line(f"playing: {' '.join(orbits) or 'none'} · held: {held}", "yellow")
            return
        if name == "take":
            self._take(targets[0])
        else:
            self._release(targets, fade=name == "fade")

    def _take(self, orbit: str) -> None:
        """A held layer's code back into the editor; it's current again."""
        held = self.held.get(orbit)
        if held is None:
            self.log_line(f"{orbit} isn't held", "yellow")
        elif held.stmt is None:
            self.log_line(f"{orbit} wasn't started by selene, so there's no code to take", "yellow")
        elif self._editor_dirty():
            self.log_line("play or save your edits first (ctrl+e / ctrl+s), then /take", "yellow")
        else:
            del self.held[orbit]
            self.playing_code = merge_layers(self.playing_code, held.stmt)
            self.query_one("#code", TextArea).text = self.playing_code
            self._flow_rebase(player=True)
            self.log_line(f"took {orbit} from {held.source}", "cyan")
            self._render_bar()

    @work(group="play", exclusive=False)
    async def _release(self, orbits: list[str], fade: bool) -> None:
        cycles = 8
        for orbit in orbits:
            n = int(orbit[1:])
            code = f"xfadeIn {n} {cycles} $ silence" if fade else f"{orbit} silence"
            result = await self.ghci.eval(code)
            if not result.ok:
                continue
            if fade:
                token = object()
                self.fading[orbit] = token
                seconds = cycles / (self.clock.cps or 0.5625) + 0.3
                self.set_timer(seconds, lambda o=orbit, t=token: self._gone(o, t))
                self.log_line(f"fading {orbit} out over {cycles} cycles", "magenta")
            else:
                self._gone(orbit, None)
                self.log_line(f"stopped {orbit}", "magenta")
        self._render_bar()

    def _gone(self, orbit: str, token) -> None:
        """A layer finished fading or was stopped: it's no longer playing."""
        if token is not None and self.fading.get(orbit) is not token:
            return  # re-taken or replaced since
        self.fading.pop(orbit, None)
        if self.held.pop(orbit, None) is None and orbit in blocks.sounding_orbits(self.playing_code):
            clean = not self._editor_dirty()
            self.playing_code = merge_layers(self.playing_code, f"{orbit} silence")
            if clean:
                self.query_one("#code", TextArea).text = self.playing_code
            self._flow_rebase(player=False)
        self._render_bar()

    def _discover(self) -> None:
        """Safety net: any orbit heard on the tap gets a lane, even if selene
        didn't start it (or lost track of it). A layer selene just stopped
        still has ~0.15 s of events in flight, so an unknown orbit only counts
        if it's heard well after selene last knew of it."""
        now = time.time()
        known = set(self._all_orbits())
        phrase_seconds = 4 / (self.clock.cps or 0.5625)
        changed = False
        for orbit, t in self.lane_model.last_event.items():
            if orbit not in known and t > self.last_known.get(orbit, 0) + 1.0 and now - t < 2:
                self.held[orbit] = Held(None, "outside selene")
                changed = True
        for orbit, held in list(self.held.items()):
            quiet = now - self.lane_model.last_event.get(orbit, 0) > 2 * phrase_seconds
            if held.stmt is None and quiet and orbit not in self.muted:
                del self.held[orbit]
                changed = True
        if changed:
            self._render_bar()

    async def _resolve_model(self) -> None:
        try:
            self._set(model=await self.ollama.resolve_model())
        except Exception as e:  # noqa: BLE001
            self.log_line(f"ollama: {e}", "red")

    def action_play_editor(self) -> None:
        self.evaluate(self.query_one("#code", TextArea).text, source="editor")

    def action_retry(self) -> None:
        if self.last_prompt:
            # Drop the last exchange so the model answers fresh, not as a revision.
            if len(self.history) >= 2 and self.history[-2].get("prompt") == self.last_prompt:
                self.history = self.history[:-2]
            self.generate(self.last_prompt)

    def action_new_session(self) -> None:
        self.history.clear()
        self.log_line("── new context: next prompt starts from scratch ──", "magenta")
        self.fresh = True

    def action_lanes(self) -> None:
        self.lanes_on = not self.lanes_on
        self._render_bar()

    def _modal(self) -> bool:
        return len(self.screen_stack) > 1

    def action_hush(self) -> None:
        if self._modal():  # esc in a dialog closes it; it must not hush the music
            self.screen.action_cancel()
            return
        self.workers.cancel_group(self, "play")  # escape also stops a generation
        self._set(llm="")
        self._hush()

    @work(group="hush")
    async def _hush(self) -> None:
        if self.flow:
            # Everything is about to be silent, so no glide: straight to neutral.
            self._flow_stop(log=False)
        await self.ghci.hush()
        self.playing_code = ""
        self.held.clear()
        self.fading.clear()
        self.muted.clear()
        self._code_state(None)
        self.log_line("hush", "magenta")
        self._render_bar()

    @work(group="services", exclusive=False)
    async def action_boot_dirt(self) -> None:
        status = await asyncio.to_thread(dirt_status)
        if status == "listening":
            self._set(dirt="listening")
            self.log_line("SuperDirt already listening on 57120", "green")
            return
        if status == "sclang":
            # Another sclang (e.g. the SC IDE) owns 57120; a second one can't bind it.
            self._set(dirt="sclang")
            self.log_line("sclang already holds 57120; start SuperDirt there", "yellow")
            return
        self._set(dirt="booting")
        ok = await self.dirt.boot()
        self._set(dirt="listening" if ok else "off")

    # ── generate / evaluate ───────────────────────────────────────────────

    @work(group="play", exclusive=True)
    async def evaluate(self, code: str, source: str) -> None:
        if not code.strip():
            return
        self._code_state("busy")
        result = await self.ghci.eval(self._wrap(code))
        if result.ok:
            if source == "editor":
                label = self.current_file.name if self.current_file else "the editor"
                self._replace_current(code, label)
            elif source == "raw":
                editor = self.query_one("#code", TextArea)
                clean = not self._editor_dirty()
                if code.strip() == "hush":
                    self.playing_code = ""
                    self.held.clear()
                else:
                    self.playing_code = merge_layers(self.playing_code, code)
                    for orbit in blocks.orbits_used(code):  # it's current now
                        self.held.pop(orbit, None)
                if clean:  # keep the editor showing what plays, unless mid-edit
                    editor.text = self.playing_code
            self._code_state("playing")
            if source != "flow":  # Flow re-wrapping what plays isn't news
                self._save(code, f"({source})")
            self._flow_rebase(player=source != "flow")
        else:
            self._code_state("failed")
        self._render_bar()

    @work(group="play", exclusive=True)
    async def generate(self, prompt: str) -> None:
        editor = self.query_one("#code", TextArea)
        # Unplayed edits in the editor are the base for this prompt, even after
        # ctrl+n: they're what the player has in front of them.
        edits = editor.text if self._editor_dirty() else ""
        context = "" if self.fresh else self.playing_code
        self.fresh = False
        messages = [{"role": "system", "content": self.system_prompt}]
        messages += [{"role": m["role"], "content": m["content"]} for m in self.history]
        messages.append({"role": "user", "content": user_message(prompt, context, edits,
                                                                 held=self._held_code())})
        if edits:
            self.log_line("(building on your unplayed edits)", "dim cyan")
        self.log_line(f"» {prompt}", "bold cyan")

        for attempt in range(self.args.fix_attempts + 1):
            self._set(llm="thinking…" if attempt == 0 else f"fixing ({attempt})…")
            self._code_state("busy")
            reply = ""
            try:
                async for chunk in self.ollama.chat(messages):
                    reply += chunk
                    editor.text = self.model_text = reply
                    editor.scroll_end(animate=False)
            except Exception as e:  # noqa: BLE001 - network/model errors go to the log
                self.log_line(f"ollama: {e}", "red")
                self._set(llm="")
                self._code_state("failed")
                return
            code = blocks.extract_code(reply)
            editor.text = self.model_text = code
            messages.append({"role": "assistant", "content": reply})
            unknown = blocks.sound_names(code) - self.known_sounds
            if unknown and attempt < self.args.fix_attempts:
                self.log_line(f"not installed: {', '.join(sorted(unknown))}; asking for a fix",
                              "yellow")
                messages.append({"role": "user", "content": unknown_sounds_message(unknown)})
                continue
            self._set(llm="evaluating…")
            result = await self.ghci.eval(self._wrap(code))
            if result.ok:
                break
            messages.append({"role": "user", "content": fix_message(result.error_text, code)})
        self._set(llm="")

        if not result.ok:
            self._code_state("failed")
            self.log_line("still failing; edit the pattern and press ctrl+e, or ctrl+r", "red")
            return
        self._replace_current(code, "a prompt")
        self._code_state("playing")
        self.history += [{"role": "user", "content": prompt, "prompt": prompt},
                         {"role": "assistant", "content": f"```haskell\n{code}\n```"}]
        self.history = self.history[-2 * HISTORY_TURNS:]
        unknown = blocks.sound_names(code) - self.known_sounds
        if unknown:
            self.log_line(f"unknown sounds (will be silent): {', '.join(sorted(unknown))}", "yellow")
        self._save(code, prompt)
        self._flow_rebase(player=True)
        self._render_bar()

    # ── flow ──────────────────────────────────────────────────────────────

    def _wrap(self, code: str) -> str:
        return blocks.flowify(code) if self.flow_on else code

    def _editor_dirty(self) -> bool:
        """The editor holds the player's own unplayed edits."""
        text = self.query_one("#code", TextArea).text.strip()
        return text != self.playing_code.strip() and text != self.model_text.strip()

    def _flow_now(self) -> float:
        return (time.monotonic() - self.flow_t0) * FLOW_SPEED

    def _playing_statements(self) -> dict[str, str]:
        out = {}
        for stmt in blocks.split_statements(self.playing_code):
            orbits = blocks.sounding_orbits(stmt)
            if orbits:
                out[orbits[0]] = stmt
        return out

    def _flow_rebase(self, player: bool) -> None:
        if not self.flow:
            return
        now = self._flow_now()
        for name, value in self.flow.rebase(self._playing_statements(), now, player=player):
            self.flow_ctrl.send(name, value)
        self.flow.set_muted(self.muted, now)

    async def _listen_tap(self) -> None:
        clock, lanes = self.clock, self.lane_model

        class Tap(asyncio.DatagramProtocol):
            def datagram_received(self, data, addr):
                for timetag, event in clock.feed(data, time.time()):
                    lanes.add(timetag, event)

        try:
            await asyncio.get_running_loop().create_datagram_endpoint(
                Tap, local_addr=("127.0.0.1", self.tap_port))
        except OSError as e:
            self.log_line(f"flow can't hear Tidal's clock ({e}); changes won't be on the beat",
                          "yellow")

    def flow_command(self, arg: str) -> None:
        """/flow toggles; /flow N sets the depth (and starts Flow if it's off)."""
        if not arg:
            self.action_flow()
            return
        if not arg.isdigit() or int(arg) not in DEPTHS:
            self.log_line(f"flow depth is 1–{max(DEPTHS)} (1 = gentle drift, "
                          f"{max(DEPTHS)} = most active)", "yellow")
            return
        self.flow_depth = int(arg)
        if self.flow_on:
            self._schedule(self.flow.set_depth(self.flow_depth, self._flow_now(), self._cycle()),
                           cancel=True)
            self.log_line(f"☯ flow depth {self.flow_depth}", "magenta")
            self._render_bar()
        else:
            self.action_flow()

    def _cycle(self) -> float | None:
        return self.clock.cycle_at(time.time())

    def _schedule(self, steps, cancel: bool = False) -> None:
        """Send each stepped change just before Tidal processes its cycle."""
        if cancel:  # a batch going home overrides anything still queued
            for handle in self.flow_pending:
                handle.cancel()
            self.flow_pending.clear()
        loop = asyncio.get_running_loop()
        self.flow_pending = [h for h in self.flow_pending if not h.cancelled()]
        for step in steps:
            delay = self.clock.send_time(step.cycle) - time.time() if self.clock.known else 0
            if self.flow_ctrl:
                self.flow_pending.append(
                    loop.call_later(max(0.0, delay), self.flow_ctrl.send, step.name, step.value))

    def _update_dtime(self) -> None:
        """Flow's delay throws echo a dotted eighth; SuperDirt wants seconds."""
        cps = self.clock.cps
        if cps and cps != self.flow_dtime_cps and self.flow_ctrl:
            self.flow_dtime_cps = cps
            self.flow_ctrl.send("fl_dtime", 0.1875 / cps)

    def action_flow(self) -> None:
        if self.state["ghci"] != "ready":
            self.log_line("flow waits for GHCi", "yellow")
            return
        if self.flow_on:
            self.flow_on = False
            self._schedule(self.flow.start_exit(self._flow_now(), self._cycle()), cancel=True)
            self._set(flow="easing out")
            self.log_line("☯ flow off · easing home", "magenta")
            return
        self.flow_on = True
        if self.flow:  # still gliding home from a moment ago
            self.flow.resume(self._flow_now())
            self.flow.set_depth(self.flow_depth, self._flow_now(), self._cycle())
        else:
            self.flow = FlowDirector(depth=self.flow_depth)
            self.flow_ctrl = CtrlSender(self.ghci.ctrl_port)
            self.flow_t0 = time.monotonic()
            self.flow_timer = self.set_interval(0.1, self._flow_tick)
        self._flow_rebase(player=False)
        self._set(flow="flow")
        self.log_line(f"☯ flow on · depth {self.flow_depth}", "magenta")
        if not self._boot_supports_flow():
            self.log_line("flow needs the SELENE_CTRL_PORT and SELENE_TAP_PORT lines from the "
                          "bundled BootTidal.hs in your --boot file", "yellow")
        if self.playing_code:
            # Re-evaluate with the wrappers; at neutral they change nothing audible.
            self.evaluate(self.playing_code, source="flow")

    def _boot_supports_flow(self) -> bool:
        try:
            text = Path(self.args.boot).read_text(errors="ignore")
        except OSError:
            return False
        return "SELENE_CTRL_PORT" in text and "SELENE_TAP_PORT" in text

    def _flow_stop(self, log: bool = True) -> None:
        for handle in self.flow_pending:
            handle.cancel()
        self.flow_pending.clear()
        self.flow_dtime_cps = None
        for name, value in self.flow.neutral_updates():
            self.flow_ctrl.send(name, value)
        self.flow_timer.stop()
        self.flow_ctrl.close()
        self.flow = self.flow_ctrl = self.flow_timer = None
        self.flow_on = False
        self._set(flow="")
        if log:
            self.log_line("☯ flow stopped", "magenta")

    def _flow_tick(self) -> None:
        if not self.flow:
            return
        self._update_dtime()
        updates, steps, events = self.flow.tick(self._flow_now(), self._cycle())
        for name, value in updates:
            self.flow_ctrl.send(name, value)
        self._schedule(steps)
        for e in events:
            if e.kind == "log":
                self.log_line(f"flow · {e.detail}", "magenta dim")
            elif e.kind == "silence":
                self._flow_silence(e.orbit)
            elif e.kind == "evolve":
                busy = any(w.group == "play" and w.is_running for w in self.workers)
                if busy or self._editor_dirty() or not self.flow_on:
                    self.flow.evolved(e.orbit, e.detail, ok=False, now=self._flow_now())
                else:
                    self._flow_evolve(e.orbit, e.detail)
        if self.flow.exited:
            self._flow_stop()

    @work(group="flow-evolve", exclusive=True)
    async def _flow_silence(self, orbit: str) -> None:
        result = await self.ghci.eval(f"{orbit} silence")
        if result.ok:
            self._adopt(f"{orbit} silence", f"(flow) {orbit} retired")
            self.log_line(f"flow · {orbit} retired", "magenta dim")

    def _adopt(self, stmt: str, label: str) -> None:
        """Merge a Flow change into what's playing (and the editor, if untouched)."""
        clean = not self._editor_dirty()
        self.playing_code = merge_layers(self.playing_code, stmt)
        if clean:
            self.query_one("#code", TextArea).text = self.playing_code
        self._save(stmt, label)
        self._flow_rebase(player=False)
        self._render_bar()

    @work(group="flow-evolve", exclusive=True)
    async def _flow_evolve(self, orbit: str, kind: str) -> None:
        verb = "adding" if kind == "add" else "evolving"
        self._set(flow=f"{verb} {orbit}")
        messages = [{"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": evolve_message(self.playing_code, orbit, kind,
                                                               bold=self.flow.depth.bold)}]
        keep_scales = blocks.scales(self.playing_code)
        stmt, ok = None, False
        for _ in range(self.args.fix_attempts + 1):
            try:
                reply = "".join([c async for c in self.ollama.chat(messages)])
            except Exception as e:  # noqa: BLE001
                self.log_line(f"flow · ollama: {e}", "red")
                break
            messages.append({"role": "assistant", "content": reply})
            stmt = blocks.statement_for(blocks.extract_code(reply), orbit)
            problem = None
            if stmt is None:
                problem = f"Reply with only the {orbit} statement, starting `{orbit} $`."
            elif unknown := blocks.sound_names(stmt) - self.known_sounds:
                problem = (f"Not installed: {', '.join(sorted(unknown))}. "
                           "Use only sounds from the lists.")
            elif keep_scales and blocks.scales(stmt) - keep_scales:
                problem = (f"Keep the key: use only these scales: {', '.join(sorted(keep_scales))}.")
            if problem:
                messages.append({"role": "user", "content": problem})
                continue
            if not self.flow_on:  # turned off while the model was thinking
                break
            if kind == "add":
                # Arrive silent: the gain control is 0 before the pattern exists.
                self.flow_ctrl.send(ctrl_name("gain", orbit), 0.0)
                result = await self.ghci.eval(blocks.flowify(stmt))
            else:
                result = await self.ghci.eval(
                    blocks.as_xfade(blocks.flowify(stmt), self.flow.depth.xfade))
            if result.ok:
                ok = True
                break
            if kind == "add" and self.flow_ctrl:
                self.flow_ctrl.send(ctrl_name("gain", orbit), 1.0)
            messages.append({"role": "user", "content": fix_message(result.error_text, stmt)})
        if self.flow:
            if ok:
                self._adopt(stmt, f"(flow) {verb} {orbit}")
                how = ("swelling in" if kind == "add"
                       else f"crossfading over {self.flow.depth.xfade} cycles")
                self.log_line(f"flow · {verb} {orbit}, {how}", "magenta")
            self.flow.evolved(orbit, kind, ok, self._flow_now())
            self._set(flow="flow" if self.flow_on else "easing out")

    # ── files ─────────────────────────────────────────────────────────────

    @on(TextArea.Changed, "#code")
    def _code_changed(self) -> None:
        self._update_title()

    def _update_title(self) -> None:
        code = self.query_one("#code", TextArea)
        if self.current_file is None:
            code.border_title = "pattern"
            return
        unsaved = code.text.strip() != self.saved_text.strip()
        code.border_title = self.current_file.name + (" •" if unsaved else "")

    def _unsaved_edits(self) -> bool:
        """Editor text that would be lost: not saved, and not playing (what plays
        is in the session log)."""
        text = self.query_one("#code", TextArea).text.strip()
        return bool(text) and text != self.saved_text.strip() and text != self.playing_code.strip()

    def action_save(self) -> None:
        if self._modal():
            return
        if self.current_file:
            self._write(self.current_file)
        else:
            self._save_as()

    def _save_as(self, name: str = "") -> None:
        if name:
            self._confirm_write(resolve(name, self.file_dir, save=True))
            return
        initial = self.current_file.name if self.current_file else ""
        self.push_screen(PathScreen("save", self.file_dir, initial),
                         lambda path: path and self._confirm_write(path))

    def _confirm_write(self, path: Path) -> None:
        if path.exists() and path != self.current_file:
            self.push_screen(ConfirmScreen(f"Replace {path.name}?", "Replace"),
                             lambda yes: yes and self._write(path))
        else:
            self._write(path)

    def _write(self, path: Path) -> None:
        text = self.query_one("#code", TextArea).text
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text.rstrip("\n") + "\n")
        except OSError as e:
            self.log_line(f"couldn't save {path}: {e.strerror or e}", "red")
            return
        self.current_file, self.saved_text, self.file_dir = path, text, path.parent
        self._update_title()
        self.log_line(f"saved {self._pretty_path(path)}", "green")

    def action_open(self, name: str = "") -> None:
        if self._modal():
            return
        if name:
            self._confirm_open(resolve(name, self.file_dir, save=False))
            return
        self.push_screen(PathScreen("open", self.file_dir),
                         lambda path: path and self._confirm_open(path))

    def _confirm_open(self, path: Path) -> None:
        if not path.is_file():
            self.log_line(f"no such file: {self._pretty_path(path)}", "red")
        elif self._unsaved_edits():
            self.push_screen(ConfirmScreen("The editor has unsaved edits. Discard them?",
                                           "Discard"),
                             lambda yes: yes and self._load(path))
        else:
            self._load(path)

    def _load(self, path: Path) -> None:
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError) as e:
            self.log_line(f"couldn't open {path}: {e}", "red")
            return
        self.current_file, self.saved_text, self.file_dir = path, text, path.parent
        self.query_one("#code", TextArea).text = text
        self._code_state(None)
        self._update_title()
        self.log_line(f"opened {self._pretty_path(path)} · ctrl+e plays it", "green")

    @staticmethod
    def _pretty_path(path: Path) -> str:
        try:
            return "~/" + str(path.relative_to(Path.home()))
        except ValueError:
            return str(path)

    def _save(self, code: str, label: str) -> None:
        with self.session_file.open("a") as f:
            f.write(f"-- {datetime.now():%H:%M:%S}  {label}\n{code}\n\n")

    async def action_quit(self) -> None:
        self.log_line("shutting down…", "dim")
        await self._shut_down()
        self.exit()

    async def _shut_down(self) -> None:
        if self.flow:
            self._flow_stop(log=False)
        await self.ghci.stop()
        await self.dirt.stop()
        await self.ollama.close()

    async def on_unmount(self) -> None:
        # However the app ends (ctrl+q, ctrl+c, a crash, a test), never leave
        # Tidal playing on its own. Each step is a no-op if already done.
        await self._shut_down()


def merge_layers(playing: str, raw: str) -> str:
    """Raw `dN $ ...` lines replace that orbit's statement in the playing code."""
    if not playing:
        return raw
    replaced = set(blocks.orbits_used(raw))
    kept = [s for s in blocks.split_statements(playing)
            if not set(blocks.orbits_used(s)) & replaced]
    return "\n".join(kept + blocks.split_statements(raw))


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", default="gemma4:31b-mlx", help="Ollama model or family (default: gemma4:31b-mlx)")
    p.add_argument("--ollama-url", default="http://localhost:11434")
    p.add_argument("--ghci", default="ghci", help="GHCi executable")
    p.add_argument("--boot", default=str(BUNDLED_BOOT), help="BootTidal.hs to load")
    p.add_argument("--superdirt", action="store_true",
                   help="boot SuperCollider + SuperDirt at startup if it isn't running")
    p.add_argument("--dir", default=str(DEFAULT_DIR),
                   help=f"folder for .tidal files (default: {DEFAULT_DIR})".replace(str(Path.home()), "~"))
    p.add_argument("--flow-depth", type=int, choices=sorted(DEPTHS), default=DEFAULT_DEPTH,
                   help=f"how active Flow is when you turn it on, 1 (gentle drift) to "
                        f"{max(DEPTHS)} (default: {DEFAULT_DEPTH}); change live with /flow N")
    p.add_argument("--fix-attempts", type=int, default=2,
                   help="times to feed GHCi errors back to the model (default: 2)")
    return p.parse_args(argv)


def main() -> None:
    Selene(parse_args()).run()


if __name__ == "__main__":
    main()
