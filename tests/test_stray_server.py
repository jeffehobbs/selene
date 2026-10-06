"""A leftover scsynth holding SuperDirt's port is named and, with an OK, stopped."""

import asyncio
import os
import shutil
import subprocess

import pytest

from selene import app as app_module
from selene import superdirt
from selene.app import Selene, parse_args
from selene.files import ConfirmScreen
from selene.superdirt import dirt_listening, stop_strays, stray_servers

PORT = 57997  # not SuperDirt's, so a real SuperCollider is never involved


def fake_ps(table):
    return lambda pids: {p: table[p] for p in pids if p in table}


def test_only_orphaned_scsynths_count(monkeypatch):
    table = {100: (1, "scsynth", "Tue Oct 6 06:12:27 2026"),
             200: (300, "scsynth", "Tue Oct 6 09:00:00 2026"),
             300: (1, "sclang", "Tue Oct 6 09:00:00 2026"),
             400: (1, "Python", "Tue Oct 6 09:00:00 2026")}
    monkeypatch.setattr(superdirt, "_processes", fake_ps(table))
    for holders, strays in (([100], {100: "Tue Oct 6 06:12:27 2026"}),
                            ([200], {}),        # its sclang is alive: someone's working SC
                            ([300, 200], {}),   # sclang holds it too
                            ([400], {}),        # not scsynth at all
                            ([], {})):
        monkeypatch.setattr(superdirt, "port_holders", lambda port=0, h=holders: h)
        assert stray_servers(PORT) == strays


FAKE_SCSYNTH = r"""
#include <arpa/inet.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <unistd.h>
int main(int argc, char **argv) {
    int s = socket(AF_INET, SOCK_DGRAM, 0);
    struct sockaddr_in a = {.sin_family = AF_INET, .sin_port = htons(atoi(argv[1]))};
    a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (bind(s, (struct sockaddr *)&a, sizeof a)) return 1;
    printf("up\n");
    fflush(stdout);
    sleep(60);
    return 0;
}
"""


@pytest.mark.skipif(shutil.which("cc") is None, reason="needs a C compiler")
async def test_finds_and_stops_a_real_stray(tmp_path):
    # A real process named scsynth (its parent is pytest, not sclang) on the port.
    # Compiled, because Python on macOS re-execs itself under another name.
    (tmp_path / "scsynth.c").write_text(FAKE_SCSYNTH)
    subprocess.run(["cc", "-o", str(tmp_path / "scsynth"), str(tmp_path / "scsynth.c")],
                   check=True)
    proc = subprocess.Popen([str(tmp_path / "scsynth"), str(PORT)], stdout=subprocess.PIPE,
                            text=True)
    try:
        assert proc.stdout.readline().strip() == "up"
        strays = stray_servers(PORT)
        assert list(strays) == [proc.pid]
        assert superdirt.dirt_status(PORT, timeout=0.2) == "stray"
        assert await stop_strays([proc.pid, os.getpid()], port=PORT)  # never us
        assert not dirt_listening(PORT)
        assert proc.wait(5) is not None
    finally:
        proc.kill()


async def wait_for(cond, timeout=15, step=0.05):  # roomy: a busy Mac (SuperDirt booting) is slow
    for _ in range(int(timeout / step)):
        if cond():
            return True
        await asyncio.sleep(step)
    return False


def dialog(app):
    return isinstance(app.screen, ConfirmScreen) and bool(app.screen.query("Button"))


async def test_app_asks_before_stopping_a_stray(monkeypatch):
    stopped, booted = [], []
    state = {"strays": {52975: "Tue Oct 6 06:12:27 2026"}}

    async def stop(pids, **kw):
        stopped.append(list(pids))
        state["strays"] = {}
        return True

    async def boot(*a, **kw):
        booted.append(1)
        return True

    async def send(code):
        return True

    monkeypatch.setattr(app_module, "dirt_status",
                        lambda *a, **k: "stray" if state["strays"] else "off")
    monkeypatch.setattr(app_module, "stray_servers", lambda *a, **k: dict(state["strays"]))
    monkeypatch.setattr(app_module, "stop_strays", stop)

    def launch():
        app = Selene(parse_args(["--ghci", "/nonexistent/ghci", "--superdirt"]))
        app.dirt.boot, app.dirt.send = boot, send
        return app

    # Even with --superdirt it asks; Not now (the default) leaves it running.
    app = launch()
    async with app.run_test(size=(120, 34)) as pilot:
        assert await wait_for(lambda: dialog(app))
        assert "pid 52975, running since Tue Oct 6 06:12:27 2026" in str(
            app.screen.query_one("Static").render())
        assert app.screen.focused.id == "no"
        await pilot.press("escape")
        assert await wait_for(lambda: not app._modal())
        await pilot.pause()
        assert not stopped and not booted and app.state["dirt"] == "stray"
        assert "blocked" in str(app.query_one("#status").render())

    app = launch()
    async with app.run_test(size=(120, 34)) as pilot:
        assert await wait_for(lambda: dialog(app))
        app.screen.query_one("#yes").press()
        assert await wait_for(lambda: booted)
        assert stopped == [[52975]] and app.state["dirt"] == "listening"
