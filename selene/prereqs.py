"""What selene needs installed, what's missing, and where each platform's
official instructions for it are."""

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import platforms

TIDAL_DOCS = {
    "macos": "https://tidalcycles.org/docs/getting-started/macos_install",
    "linux": "https://tidalcycles.org/docs/getting-started/linux_install",
    "windows": "https://tidalcycles.org/docs/getting-started/windows_install",
}
OLLAMA_DOWNLOAD = {
    "macos": "https://ollama.com/download/mac",
    "linux": "https://ollama.com/download/linux",
    "windows": "https://ollama.com/download/windows",
}
OLLAMA_INSTALL_LINUX = "curl -fsSL https://ollama.com/install.sh | sh"
OLLAMA_START = {
    "macos": "open -a Ollama",
    "linux": "ollama serve",
    "windows": "",  # the Start menu's Ollama
}
GEMMA4 = "https://ollama.com/library/gemma4"
SC3_PLUGINS = "https://supercollider.github.io/sc3-plugins/"
THIS = {"macos": "this Mac", "linux": "this machine", "windows": "this PC"}

# GHCi found, but not the Tidal library (cabal install --lib tidal not run,
# or run for another GHC).
TIDAL_MISSING = re.compile(r"Could not (find|load) module .Sound\.Tidal")


@dataclass
class Need:
    key: str
    title: str
    why: str
    url: str
    command: str = ""  # something to run, shown under the link
    optional: bool = False


def recommended_model(apple_silicon: bool | None = None) -> str:
    """gemma4 31B: the MLX build where it runs (Apple silicon), else the
    standard one. Smaller gemma4s write broken Tidal too often."""
    if apple_silicon is None:
        apple_silicon = platforms.apple_silicon()
    return "gemma4:31b-mlx" if apple_silicon else "gemma4:31b"


def find_ghci(name: str = "ghci", plat: str | None = None) -> str | None:
    """`name` on PATH, or (for plain "ghci") ghcup's copy, which a fresh
    install often leaves off PATH."""
    if found := shutil.which(name):
        return found
    if name != "ghci":
        return None
    exe = "ghci.exe" if (plat or platforms.current()) == "windows" else "ghci"
    ghcup = platforms.ghcup_bin(plat) / exe
    return str(ghcup) if ghcup.exists() else None


def tidal_need(plat: str | None = None, ghci_found: bool = False) -> Need:
    plat = plat or platforms.current()
    if ghci_found:
        return Need("tidal", "Tidal (the Haskell library)",
                    "GHCi is here but can't load Tidal, which selene plays your patterns with.",
                    TIDAL_DOCS[plat], "cabal update && cabal install --lib tidal")
    return Need("tidal", "GHC + Tidal",
                "Selene plays your patterns in GHCi with the Tidal library. "
                "The guide also covers SuperCollider + SuperDirt.",
                TIDAL_DOCS[plat])


def sound_needs(dirt: str = "off", plat: str | None = None, sclang: str | None = None,
                quarks: Path | None = None, extensions: list[Path] | None = None) -> list[Need]:
    """SuperCollider, SuperDirt (with its Dirt-Samples) and sc3-plugins.
    `dirt` is superdirt.dirt_status(): whatever's answering proves its part."""
    from .superdirt import find_sclang
    plat = plat or platforms.current()
    if dirt == "listening":
        return [] if _has_sc3_plugins(plat, extensions) else [_sc3_need(plat)]
    has_sclang = dirt in ("sclang", "stray") or bool(sclang or find_sclang())
    quarks = quarks or platforms.sc_support_dir(plat) / "downloaded-quarks"
    has_dirt = (quarks / "SuperDirt").is_dir() and (quarks / "Dirt-Samples").is_dir()
    needs = []
    if not has_sclang:
        needs.append(Need("supercollider", "SuperCollider + SuperDirt",
                          "SuperDirt, running in SuperCollider, makes the sound.",
                          TIDAL_DOCS[plat]))
    elif not has_dirt:
        needs.append(Need("superdirt", "SuperDirt",
                          "SuperCollider is here, but not the SuperDirt quark that makes "
                          "the sound. Run this in SuperCollider, then recompile "
                          "(Language → Recompile Class Library):",
                          TIDAL_DOCS[plat], 'Quarks.install("SuperDirt")'))
    if has_sclang and not _has_sc3_plugins(plat, extensions):
        needs.append(_sc3_need(plat))
    return needs


def _has_sc3_plugins(plat: str, extensions: list[Path] | None) -> bool:
    for root in extensions if extensions is not None else platforms.sc_extension_dirs(plat):
        if root.is_dir() and any(p.name.lower() == "sc3plugins" for p in root.iterdir()):
            return True
    return False


def _sc3_need(plat: str) -> Need:
    return Need("sc3plugins", "sc3-plugins",
                "Adds SuperDirt's extra synths (superpiano, superhoover…). "
                "Selene works without them" +
                ("; on Linux, try your distribution's sc3-plugins package." if plat == "linux"
                 else "."), SC3_PLUGINS, optional=True)


def ollama_needs(status: str, model: str, plat: str | None = None) -> list[Need]:
    """From the model light: "down" (no answer) or "missing" (no such model)."""
    plat = plat or platforms.current()
    if status == "down":
        if shutil.which("ollama") or platforms.ollama_app_installed(plat):
            start = OLLAMA_START[plat]
            return [Need("ollama", "Ollama isn't running",
                         "Ollama runs the model that writes the Tidal code. Start it" +
                         (":" if start else " from the Start menu."),
                         OLLAMA_DOWNLOAD[plat], start)]
        return [Need("ollama", "Ollama",
                     "Ollama runs the model that writes the Tidal code, on " + THIS[plat] + ".",
                     OLLAMA_DOWNLOAD[plat], OLLAMA_INSTALL_LINUX if plat == "linux" else "")]
    if status == "missing":
        why = ("Selene's suggested model, Gemma 4 31B (about 19 GB to download)."
               if model.startswith("gemma4:31b") else
               f"Selene is set to {model}, which Ollama doesn't have. "
               f"Pick another with ctrl+k, or get it:")
        url = GEMMA4 if model.startswith("gemma4") else f"https://ollama.com/library/{model.split(':')[0]}"
        return [Need("model", f"the {model} model", why, url, f"ollama pull {model}")]
    return []
