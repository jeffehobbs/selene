"""Where things live on each platform selene knows about.

Selene runs on macOS today; the Linux and Windows paths are here so the rest
of the code asks one place instead of hardcoding macOS's.
"""

import os
import platform as _platform
import sys
from pathlib import Path


def current() -> str:
    """"macos", "linux" or "windows"."""
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


def apple_silicon() -> bool:
    return current() == "macos" and _platform.machine() == "arm64"


def sc_support_dir(plat: str | None = None) -> Path:
    """SuperCollider's per-user folder (Platform.userAppSupportDir): its
    startup.scd, downloaded quarks and Extensions."""
    plat = plat or current()
    home = Path.home()
    if plat == "macos":
        return home / "Library/Application Support/SuperCollider"
    if plat == "windows":
        return Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local")) / "SuperCollider"
    return Path(os.environ.get("XDG_DATA_HOME") or home / ".local/share") / "SuperCollider"


def sc_extension_dirs(plat: str | None = None) -> list[Path]:
    """Folders SuperCollider loads class libraries (sc3-plugins) from."""
    plat = plat or current()
    dirs = [sc_support_dir(plat) / "Extensions"]
    if plat == "macos":
        dirs.append(Path("/Library/Application Support/SuperCollider/Extensions"))
    elif plat == "linux":
        dirs += [Path("/usr/share/SuperCollider/Extensions"),
                 Path("/usr/local/share/SuperCollider/Extensions")]
    else:
        dirs.append(Path(os.environ.get("ProgramData", "C:/ProgramData")) /
                    "SuperCollider/Extensions")
    return dirs


def sclang_candidates(plat: str | None = None) -> list[Path]:
    """Places SuperCollider installs sclang when it isn't on PATH."""
    plat = plat or current()
    if plat == "macos":
        return [Path("/Applications/SuperCollider.app/Contents/MacOS/sclang"),
                Path("/Applications/SuperCollider/SuperCollider.app/Contents/MacOS/sclang")]
    if plat == "windows":
        roots = [Path(os.environ.get(v, d)) for v, d in
                 (("ProgramFiles", "C:/Program Files"), ("ProgramFiles(x86)", "C:/Program Files (x86)"))]
        return [exe for root in roots if root.is_dir()
                for exe in sorted(root.glob("SuperCollider*/sclang.exe"), reverse=True)]
    return [Path("/usr/bin/sclang"), Path("/usr/local/bin/sclang")]


def ghcup_bin(plat: str | None = None) -> Path:
    """Where ghcup puts ghc/ghci (often not on PATH in a fresh shell)."""
    plat = plat or current()
    if plat == "windows":
        return Path(os.environ.get("GHCUP_INSTALL_BASE_PREFIX", "C:/")) / "ghcup/bin"
    return Path.home() / ".ghcup/bin"


def ollama_app_installed(plat: str | None = None) -> bool:
    """The Ollama app is installed (whether or not it's running)."""
    plat = plat or current()
    if plat == "macos":
        return any(p.exists() for p in (Path("/Applications/Ollama.app"),
                                        Path.home() / "Applications/Ollama.app"))
    if plat == "windows":
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        return (local / "Programs/Ollama/ollama app.exe").exists()
    return False
