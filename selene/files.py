"""Open / save .tidal files from the editor."""

from pathlib import Path
from typing import Iterable

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Input, Label, Static

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
PathScreen, ConfirmScreen { align: center middle; }
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
    """A yes/no for anything that throws work away. Cancel is the default."""

    DEFAULT_CSS = DIALOG_CSS
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, message: str, verb: str):
        super().__init__()
        self.message = message
        self.verb = verb

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self.message)
            # Destructive choice at the far end from Cancel, which has focus.
            with Horizontal():
                yield Button(self.verb, variant="error", id="yes")
                yield Static(classes="spacer")
                yield Button("Cancel", variant="primary", id="no")

    def on_mount(self) -> None:
        self.query_one("#no", Button).focus()

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_cancel(self) -> None:
        self.dismiss(False)
