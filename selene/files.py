"""Open / save .tidal files from the editor."""

from pathlib import Path
from typing import Iterable

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Input, OptionList, Static, TextArea
from textual.widgets.option_list import Option

DEFAULT_DIR = Path.home() / "Documents/selene"
SUFFIX = ".tidal"


def resolve(text: str, base: Path, save: bool) -> Path:
    """A typed name or path -> an absolute path. A bare name means name.tidal:
    always when saving, and when opening if that's the file that exists."""
    path = Path(text.strip()).expanduser()
    if not path.is_absolute():
        path = base / path
    if not path.suffix and (save or not path.exists()):
        path = path.with_suffix(SUFFIX)
    return path


class TidalTree(DirectoryTree):
    """Folders and .tidal files only."""

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return [p for p in paths if not p.name.startswith(".")
                and (p.is_dir() or p.suffix == SUFFIX)]


DIALOG_CSS = """
PathScreen, ConfirmScreen, PrefsScreen, ModelScreen { align: center middle; }
#dialog { width: 72; height: auto; max-height: 80%; padding: 1 2;
          border: round $accent; background: $surface; }
#dialog TidalTree { height: 16; margin-top: 1; }
#dialog Horizontal { height: auto; margin-top: 1; }
#dialog .spacer { width: 1fr; }
"""


class PathScreen(ModalScreen[Path | None]):
    """Pick a file: type a path, or choose one in the tree."""

    DEFAULT_CSS = DIALOG_CSS
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, mode: str, start_dir: Path, initial: str = ""):
        super().__init__()
        self.mode = mode  # "open" or "save"
        self.start_dir = start_dir
        self.initial = initial

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog") as dialog:
            dialog.border_title = "open" if self.mode == "open" else "save as"
            yield Input(value=self.initial, placeholder=f"name or path ({SUFFIX})", id="path")
            if self.start_dir.is_dir():
                yield TidalTree(self.start_dir)

    def on_mount(self) -> None:
        field = self.query_one("#path", Input)
        field.focus()
        field.cursor_position = len(field.value)

    @on(Input.Submitted, "#path")
    def submitted(self, event: Input.Submitted) -> None:
        if event.value.strip():
            self.dismiss(resolve(event.value, self.start_dir, save=self.mode == "save"))

    @on(DirectoryTree.FileSelected)
    def picked(self, event: DirectoryTree.FileSelected) -> None:
        if self.mode == "open":
            self.dismiss(Path(event.path))
        else:  # saving: a picked file fills the name, the player still confirms
            field = self.query_one("#path", Input)
            field.value = str(event.path)
            field.focus()

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    """A yes/no. For anything that throws work away (danger), Cancel is the
    default and sits apart; otherwise the verb is, and esc still declines."""

    DEFAULT_CSS = DIALOG_CSS
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, message: str, verb: str, danger: bool = True, cancel: str = "Cancel"):
        super().__init__()
        self.message = message
        self.verb = verb
        self.danger = danger
        self.cancel = cancel

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self.message)
            with Horizontal():
                if self.danger:  # destructive choice at the far end from Cancel
                    yield Button(self.verb, variant="error", id="yes")
                    yield Static(classes="spacer")
                    yield Button(self.cancel, variant="primary", id="no")
                else:
                    yield Button(self.cancel, id="no")
                    yield Static(classes="spacer")
                    yield Button(self.verb, variant="primary", id="yes")

    def on_mount(self) -> None:
        self.query_one("#no" if self.danger else "#yes", Button).focus()

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_cancel(self) -> None:
        self.dismiss(False)


class PrefsScreen(ModalScreen[str | None]):
    """Edit the player's preferences for the model. ctrl+s saves; esc asks
    before throwing away unsaved changes."""

    DEFAULT_CSS = DIALOG_CSS + """
    PrefsScreen #dialog { width: 90; }
    PrefsScreen TextArea { height: 14; }
    """

    def __init__(self, text: str):
        super().__init__()
        self.original = text

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog") as dialog:
            dialog.border_title = "preferences"
            yield TextArea(self.original, id="prefs", soft_wrap=True,
                           placeholder="e.g. prefer 808 kits · keep it under 120 bpm · "
                                       "lots of reverb, sparse drums")
            with Horizontal():
                yield Button("Cancel", id="no")
                yield Static(classes="spacer")
                yield Button("Save", variant="primary", id="yes")

    def on_mount(self) -> None:
        self.query_one("#prefs", TextArea).focus()

    @property
    def text(self) -> str:
        return self.query_one("#prefs", TextArea).text

    def action_save(self) -> None:
        self.dismiss(self.text)

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "yes":
            self.action_save()
        else:
            self.action_cancel()

    def action_cancel(self) -> None:
        if self.text.strip() == self.original.strip():
            self.dismiss(None)
            return
        self.app.push_screen(ConfirmScreen("Discard your changes to the preferences?", "Discard"),
                             lambda yes: yes and self.dismiss(None))


def model_row(model: dict, width: int, current: bool, in_memory: bool) -> Text:
    """One installed model: name, size on disk, parameters and quantization."""
    details = model.get("details") or {}
    text = Text().append("● " if current else "  ", style="green")
    text.append(model["name"].ljust(width), style="bold" if current else "")
    text.append(f"  {model.get('size', 0) / 1e9:5.1f} GB", style="dim")
    about = " ".join(filter(None, (details.get("parameter_size"),
                                   details.get("quantization_level"))))
    text.append(f"  {about:<12}", style="dim")
    if in_memory:
        text.append("  in memory", style="green")
    return text


class ModelScreen(ModalScreen[str | None]):
    """Pick the Ollama model that writes the code. Enter switches; esc keeps
    the current one."""

    DEFAULT_CSS = DIALOG_CSS + """
    ModelScreen #dialog { width: 80; }
    ModelScreen OptionList, ModelScreen OptionList:focus {
        height: auto; max-height: 20; border: none; padding: 0; background: $surface; }
    """
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, models: list[dict], loaded: set[str], current: str):
        super().__init__()
        self.models, self.loaded, self.current = models, loaded, current

    def compose(self) -> ComposeResult:
        width = max((len(m["name"]) for m in self.models), default=0)
        with Vertical(id="dialog") as dialog:
            dialog.border_title = "model"
            if not self.models:
                yield Static("No models installed. Try `ollama pull gemma4`.")
                return
            yield OptionList(*(Option(model_row(m, width, m["name"] == self.current,
                                                m["name"] in self.loaded), id=m["name"])
                               for m in self.models), id="models")

    def on_mount(self) -> None:
        if self.models:
            options = self.query_one("#models", OptionList)
            names = [m["name"] for m in self.models]
            options.highlighted = names.index(self.current) if self.current in names else 0
            options.focus()

    @on(OptionList.OptionSelected)
    def picked(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)
