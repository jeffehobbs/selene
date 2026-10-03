"""selene: words in, TidalCycles out, sound via GHCi -> SuperDirt."""

import argparse
import asyncio
from datetime import datetime
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Input, RichLog, Static, TextArea

from . import blocks, catalog
from .ghci import BUNDLED_BOOT, Ghci
from .llm import Ollama, build_system_prompt, fix_message, unknown_sounds_message, user_message
from .superdirt import SuperDirt, dirt_status

SESSIONS = Path.home() / ".local/share/selene"
HISTORY_TURNS = 4  # prompt/reply pairs of context sent back to the model


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


class Selene(App):
    TITLE = "selene"
    CSS = """
    Screen { layout: vertical; }
    #bar { height: 1; padding: 0 1; background: $panel; }
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
        Binding("ctrl+e", "play_editor", "Play editor", priority=True),
        Binding("ctrl+r", "retry", "Regenerate", priority=True),
        Binding("ctrl+n", "new_session", "New context", priority=True),
        Binding("ctrl+b", "boot_dirt", "Boot SuperDirt", priority=True),
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
        self.playing_code = ""
        self.last_prompt = ""
        self.fresh = False  # next prompt ignores what's playing
        self.state = {"model": args.model, "ghci": "booting", "dirt": "?", "llm": ""}
        SESSIONS.mkdir(parents=True, exist_ok=True)
        self.session_file = SESSIONS / f"{datetime.now():%Y-%m-%d}.tidal"

    def compose(self) -> ComposeResult:
        yield Static(id="bar")
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
        if orbits := blocks.sounding_orbits(self.playing_code):
            bar.append("   ▶ " + " ".join(orbits), style="green")
        self.query_one("#bar", Static).update(bar)

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
            result = await self.ghci.start()
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
        elif name == "new":
            self.action_new_session()
        else:
            self.log_line("commands: /hush /cps N /bpm N /model NAME /new", "yellow")

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

    def action_hush(self) -> None:
        self.workers.cancel_group(self, "play")  # escape also stops a generation
        self._set(llm="")
        self._hush()

    @work(group="hush")
    async def _hush(self) -> None:
        await self.ghci.hush()
        self.playing_code = ""
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
        result = await self.ghci.eval(code)
        if result.ok:
            if source == "editor":
                self.playing_code = code
            elif source == "raw":
                self.playing_code = ("" if code.strip() == "hush"
                                     else merge_layers(self.playing_code, code))
            self._code_state("playing")
            self._save(code, f"({source})")
        else:
            self._code_state("failed")
        self._render_bar()

    @work(group="play", exclusive=True)
    async def generate(self, prompt: str) -> None:
        editor = self.query_one("#code", TextArea)
        context = "" if self.fresh else self.playing_code
        self.fresh = False
        messages = [{"role": "system", "content": self.system_prompt}]
        messages += [{"role": m["role"], "content": m["content"]} for m in self.history]
        messages.append({"role": "user", "content": user_message(prompt, context)})
        self.log_line(f"» {prompt}", "bold cyan")

        for attempt in range(self.args.fix_attempts + 1):
            self._set(llm="thinking…" if attempt == 0 else f"fixing ({attempt})…")
            self._code_state("busy")
            reply = ""
            try:
                async for chunk in self.ollama.chat(messages):
                    reply += chunk
                    editor.text = reply
                    editor.scroll_end(animate=False)
            except Exception as e:  # noqa: BLE001 - network/model errors go to the log
                self.log_line(f"ollama: {e}", "red")
                self._set(llm="")
                self._code_state("failed")
                return
            code = blocks.extract_code(reply)
            editor.text = code
            messages.append({"role": "assistant", "content": reply})
            unknown = blocks.sound_names(code) - self.known_sounds
            if unknown and attempt < self.args.fix_attempts:
                self.log_line(f"not installed: {', '.join(sorted(unknown))}; asking for a fix",
                              "yellow")
                messages.append({"role": "user", "content": unknown_sounds_message(unknown)})
                continue
            self._set(llm="evaluating…")
            result = await self.ghci.eval(code)
            if result.ok:
                break
            messages.append({"role": "user", "content": fix_message(result.error_text)})
        self._set(llm="")

        if not result.ok:
            self._code_state("failed")
            self.log_line("still failing; edit the pattern and press ctrl+e, or ctrl+r", "red")
            return
        self.playing_code = code
        self._code_state("playing")
        self.history += [{"role": "user", "content": prompt, "prompt": prompt},
                         {"role": "assistant", "content": f"```haskell\n{code}\n```"}]
        self.history = self.history[-2 * HISTORY_TURNS:]
        unknown = blocks.sound_names(code) - self.known_sounds
        if unknown:
            self.log_line(f"unknown sounds (will be silent): {', '.join(sorted(unknown))}", "yellow")
        self._save(code, prompt)
        self._render_bar()

    def _save(self, code: str, label: str) -> None:
        with self.session_file.open("a") as f:
            f.write(f"-- {datetime.now():%H:%M:%S}  {label}\n{code}\n\n")

    async def action_quit(self) -> None:
        self.log_line("shutting down…", "dim")
        await self.ghci.stop()
        await self.dirt.stop()
        await self.ollama.close()
        self.exit()


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
    p.add_argument("--fix-attempts", type=int, default=2,
                   help="times to feed GHCi errors back to the model (default: 2)")
    return p.parse_args(argv)


def main() -> None:
    Selene(parse_args()).run()


if __name__ == "__main__":
    main()
