"""Onboarding: what's missing, and each platform's official instructions."""

import asyncio

import pytest

from selene import platforms, prereqs
from selene.app import Selene, parse_args
from selene.files import SetupScreen


async def wait_for(cond, timeout=5, step=0.05):
    for _ in range(int(timeout / step)):
        if cond():
            return True
        await asyncio.sleep(step)
    return False


@pytest.mark.parametrize("plat", ["macos", "linux", "windows"])
def test_links_are_the_platforms_own(plat):
    assert prereqs.tidal_need(plat).url == \
        f"https://tidalcycles.org/docs/getting-started/{plat}_install"
    ollama, = prereqs.ollama_needs("down", "gemma4:31b", plat)
    if ollama.title == "Ollama":  # not installed here
        assert ollama.url == prereqs.OLLAMA_DOWNLOAD[plat]


def test_ollama_installed_but_not_running(monkeypatch):
    monkeypatch.setattr(prereqs.shutil, "which", lambda name: "/usr/local/bin/ollama")
    need, = prereqs.ollama_needs("down", "gemma4:31b-mlx", "macos")
    assert need.title == "Ollama isn't running" and need.command == "open -a Ollama"
    need, = prereqs.ollama_needs("down", "gemma4:31b", "linux")
    assert need.command == "ollama serve"


def test_ollama_not_installed_on_linux_gets_the_install_script(monkeypatch):
    monkeypatch.setattr(prereqs.shutil, "which", lambda name: None)
    need, = prereqs.ollama_needs("down", "gemma4:31b", "linux")
    assert need.title == "Ollama" and "install.sh" in need.command


def test_missing_model_suggests_the_pull():
    need, = prereqs.ollama_needs("missing", "gemma4:31b-mlx", "macos")
    assert need.command == "ollama pull gemma4:31b-mlx"
    assert need.url == prereqs.GEMMA4 and "suggested" in need.why
    assert prereqs.ollama_needs("ready", "gemma4:31b-mlx", "macos") == []


def test_mlx_only_on_apple_silicon():
    assert prereqs.recommended_model(apple_silicon=True) == "gemma4:31b-mlx"
    assert prereqs.recommended_model(apple_silicon=False) == "gemma4:31b"


def test_sound_needs(tmp_path, monkeypatch):
    import selene.superdirt
    monkeypatch.setattr(selene.superdirt, "find_sclang", lambda: None)
    quarks, ext = tmp_path / "quarks", tmp_path / "Extensions"
    keys = lambda **kw: [n.key for n in prereqs.sound_needs(  # noqa: E731
        plat="linux", quarks=quarks, extensions=[ext], **kw)]
    assert keys() == ["supercollider"]
    assert keys(sclang="/usr/bin/sclang") == ["superdirt", "sc3plugins"]
    (quarks / "SuperDirt").mkdir(parents=True)
    (quarks / "Dirt-Samples").mkdir()
    (ext / "SC3plugins").mkdir(parents=True)
    assert keys(sclang="/usr/bin/sclang") == []
    assert keys(dirt="stray") == []  # an orphan scsynth means SuperCollider is here


def test_sound_needs_trusts_a_listening_superdirt(tmp_path):
    needs = prereqs.sound_needs("listening", "macos", quarks=tmp_path, extensions=[tmp_path])
    assert [(n.key, n.optional) for n in needs] == [("sc3plugins", True)]


def test_ghcup_ghci_off_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(platforms, "ghcup_bin", lambda plat=None: tmp_path)
    assert prereqs.find_ghci("ghci", "macos") is None
    (tmp_path / "ghci").touch()
    assert prereqs.find_ghci("ghci", "macos") == str(tmp_path / "ghci")
    assert prereqs.find_ghci("/elsewhere/ghci", "macos") is None


def test_tidal_missing_matches_ghci():
    assert prereqs.TIDAL_MISSING.search("Could not find module ‘Sound.Tidal.Context’")
    assert prereqs.TIDAL_MISSING.search(  # ghc 9.14.1, tidal installed but hidden
        "Could not load module ‘Sound.Tidal.Context’.\nIt is a member of the hidden package")


def app_with_needs(monkeypatch, real_offer, needs):
    monkeypatch.setattr(Selene, "offer_setup", real_offer)

    async def fake_needs(self):
        return needs

    monkeypatch.setattr(Selene, "_needs", fake_needs)
    app = Selene(parse_args(["--ghci", "/nonexistent/ghci"]))
    opened = []
    app.open_url = lambda url, **kw: opened.append(url)
    return app, opened


async def test_dialog_opens_the_instructions(monkeypatch, no_setup_dialog):
    needs = [prereqs.tidal_need("macos"),
             prereqs.Need("ollama", "Ollama", "why", prereqs.OLLAMA_DOWNLOAD["macos"])]
    app, opened = app_with_needs(monkeypatch, no_setup_dialog, needs)
    async with app.run_test() as pilot:
        assert await wait_for(lambda: isinstance(app.screen, SetupScreen))
        await pilot.press("tab", "enter")  # the first guide has focus; tab to the second
        assert opened == ["https://ollama.com/download/mac"]
        await pilot.click("#continue")
        assert await wait_for(lambda: not isinstance(app.screen, SetupScreen))
        log = "\n".join(line.text for line in app.query_one("#log").lines)
        assert "missing: GHC + Tidal" in log and "macos_install" in log


async def test_optional_only_logs(monkeypatch, no_setup_dialog):
    app, _ = app_with_needs(monkeypatch, no_setup_dialog, [prereqs._sc3_need("macos")])
    async with app.run_test() as pilot:
        log = lambda: "\n".join(line.text for line in app.query_one("#log").lines)  # noqa: E731
        assert await wait_for(lambda: "optional: sc3-plugins" in log())
        await pilot.pause()
        assert not isinstance(app.screen, SetupScreen)
        # /setup shows it anyway.
        await pilot.press(*"/setup", "enter")
        assert await wait_for(lambda: isinstance(app.screen, SetupScreen))
