"""Detect or boot SuperCollider + SuperDirt (sclang listening on UDP 57120)."""

import asyncio
import os
import re
import shutil
import signal
import socket
import subprocess
from pathlib import Path
from typing import Callable

DIRT_PORT = 57120
STARTUP = Path.home() / "Library/Application Support/SuperCollider/startup.scd"
BUNDLED_STARTUP = Path(__file__).with_name("superdirt_startup.scd")
SCLANG_CANDIDATES = [
    "/Applications/SuperCollider.app/Contents/MacOS/sclang",
    "/Applications/SuperCollider/SuperCollider.app/Contents/MacOS/sclang",
]


def dirt_listening(port: int = DIRT_PORT) -> bool:
    """True if something already holds the SuperDirt UDP port.

    sclang binds 57120 as its language port even without SuperDirt, so this
    alone doesn't mean SuperDirt is up; see dirt_status.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return True
    return False


ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# scsynth quits when the input device runs at another rate than the output,
# e.g. AirPods as both: their mic is 24 kHz, their playback 48 kHz.
RATE_MISMATCH = "Input sample rate is"
# SuperDirt never listens, so any built-in mic will do; opening the AirPods mic
# would also drop them into low-quality headset mode.
USE_BUILTIN_MIC = ('s.options.inDevice = ServerOptions.inDevices.detect { |d| '
                   'd.contains("MacBook") or: { d.contains("Built-in") } or: '
                   '{ d.contains("Mac mini") } or: { d.contains("iMac") } };')
HANDSHAKE = b"/dirt/handshake\0,\0\0\0"  # OSC message, no arguments


def dirt_status(port: int = DIRT_PORT, timeout: float = 1.0) -> str:
    """'listening' if SuperDirt answers a handshake; 'stray' if the port is
    held only by a leftover scsynth (see stray_servers); 'sclang' if it's held
    otherwise; else 'off'. Blocking; call from a thread."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        s.bind(("127.0.0.1", 0))
        try:
            s.sendto(HANDSHAKE, ("127.0.0.1", port))
            reply, _ = s.recvfrom(65536)
            if reply.startswith(b"/dirt/handshake/reply"):
                return "listening"
        except (TimeoutError, ConnectionRefusedError, OSError):
            pass
    if not dirt_listening(port):
        return "off"
    return "stray" if stray_servers(port) else "sclang"


def repl_line(code: str) -> bytes:
    """Code as one REPL entry. sclang 3.14's REPL runs each line on Enter
    (^L only clears the screen); older ones run on ^L. So: one line, no
    `//` comments (they'd swallow the rest), then ^L and Enter."""
    parts = []
    for line in code.splitlines():
        if (i := line.find("//")) >= 0:
            line = line[:i]
        if line.strip():
            parts.append(line.strip())
    return " ".join(parts).encode() + b"\x0c\n"


def find_sclang() -> str | None:
    return shutil.which("sclang") or next((p for p in SCLANG_CANDIDATES if Path(p).exists()), None)


class SuperDirt:
    def __init__(self, on_output: Callable[[str], None] = lambda line: None):
        self.on_output = on_output
        self.proc: asyncio.subprocess.Process | None = None
        self.startup: str | None = None  # the file that starts SuperDirt
        self.retried_input = False

    @property
    def owned(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def boot(self, timeout: float = 60) -> bool:
        """Launch sclang; resolve once SuperDirt says it's listening."""
        sclang = find_sclang()
        if not sclang:
            self.on_output("sclang not found; install SuperCollider")
            return False
        # Always the REPL: given a script file, sclang ignores stdin, and stdin
        # is how selene quits it and adds the recorder.
        self.proc = await asyncio.create_subprocess_exec(
            sclang, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        ready = asyncio.get_running_loop().create_future()
        asyncio.create_task(self._read(ready))
        # sclang always runs the user's startup.scd. If that already starts
        # SuperDirt, loading our script too would boot it twice.
        uses_startup = STARTUP.exists() and "SuperDirt" in STARTUP.read_text(errors="ignore")
        self.startup = str(STARTUP if uses_startup else BUNDLED_STARTUP)
        self.retried_input = False
        if not uses_startup:
            await self.send(self._load_startup())
        try:
            return await asyncio.wait_for(ready, timeout)
        except asyncio.TimeoutError:
            self.on_output(f"SuperDirt not listening after {timeout:.0f}s")
            return dirt_listening()

    async def _read(self, ready: asyncio.Future) -> None:
        assert self.proc and self.proc.stdout
        while raw := await self.proc.stdout.readline():
            # The REPL clears the screen and echoes a prompt; keep the text.
            line = ANSI.sub("", raw.decode(errors="replace")).replace("sc3> ", "").rstrip()
            if line:
                self.on_output(line)
            if "SuperDirt: listening" in line and not ready.done():
                ready.set_result(True)
            if RATE_MISMATCH in line and not self.retried_input:
                self.retried_input = True
                asyncio.create_task(self._retry_with_builtin_mic())
        if not ready.done():
            ready.set_result(False)

    def _load_startup(self) -> str:
        path = self.startup.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{path}".load;'

    async def _retry_with_builtin_mic(self) -> None:
        """The server quit over the input's sample rate: boot it again with the
        built-in mic as input. The startup file sets the other options and
        starts SuperDirt; it leaves inDevice alone."""
        self.on_output("the input device's sample rate doesn't match the output's "
                       "(AirPods?); retrying with the built-in mic as input")
        await asyncio.sleep(1)  # let the dead server's boot attempt give up
        await self.send(USE_BUILTIN_MIC + " " + self._load_startup())

    async def send(self, code: str) -> bool:
        """Run code in the sclang selene started."""
        if not self.owned:
            return False
        try:
            self.proc.stdin.write(repl_line(code))
            await self.proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            return False
        return True

    async def stop(self) -> None:
        if not self.owned:
            return
        try:
            await self.send("Server.default.quit; 0.exit;")  # killAll would take every scsynth
            await asyncio.wait_for(self.proc.wait(), 5)
        except (asyncio.TimeoutError, BrokenPipeError, ConnectionResetError):
            self.proc.kill()
            await self.proc.wait()


def port_holders(port: int = DIRT_PORT) -> list[int]:
    """PIDs of the processes bound to a UDP port (here: someone else's sclang)."""
    try:
        out = subprocess.run(["lsof", "-nP", "-t", f"-iUDP:{port}"], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [int(p) for p in out.split() if p.isdigit() and int(p) != os.getpid()]


def _processes(pids) -> dict[int, tuple[int, str, str]]:
    """pid -> (parent pid, command name, start time) for the ones still running."""
    pids = [str(p) for p in pids]
    if not pids:
        return {}
    try:
        out = subprocess.run(["ps", "-o", "pid=,ppid=,lstart=,comm=", "-p", ",".join(pids)],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    rows = {}
    for line in out.splitlines():
        # lstart is five words: "Tue Oct  6 06:12:27 2026"
        parts = line.split(None, 7)
        if len(parts) == 8 and parts[0].isdigit():
            rows[int(parts[0])] = (int(parts[1]), Path(parts[7]).name, " ".join(parts[2:7]))
    return rows


def stray_servers(port: int = DIRT_PORT) -> dict[int, str]:
    """Leftover scsynths holding the SuperDirt port: pid -> when it started.

    scsynth inherits sclang's open sockets, so when an sclang dies without
    quitting its server (killed, crashed), the orphaned scsynth keeps 57120
    and nothing else can bind it. Counts only if every holder is an scsynth
    and none of them still has an sclang parent; otherwise it's somebody's
    working SuperCollider."""
    holders = _processes(port_holders(port))
    if not holders or any(comm != "scsynth" for _, comm, _ in holders.values()):
        return {}
    parents = _processes({ppid for ppid, _, _ in holders.values()})
    if any(comm == "sclang" for _, comm, _ in parents.values()):
        return {}
    return {pid: started for pid, (_, _, started) in holders.items()}


async def stop_strays(pids, port: int = DIRT_PORT, timeout: float = 5) -> bool:
    """Stop the given leftover scsynths (only ones that are still strays, in
    case a pid was reused) and wait for the port to come free."""
    targets = set(pids) & set(stray_servers(port))
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in targets:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        for _ in range(int(timeout / 0.1)):
            if not dirt_listening(port):
                return True
            await asyncio.sleep(0.1)
    return False


async def stop_external(port: int = DIRT_PORT, timeout: float = 5) -> bool:
    """Stop the sclang holding the SuperDirt port, and the scsynth it started.
    Only that one: other SuperCollider instances keep running."""
    for pid in port_holders(port):
        children = subprocess.run(["pgrep", "-P", str(pid), "scsynth"], capture_output=True,
                                  text=True).stdout.split()
        for target in [*map(int, children), pid]:
            try:
                os.kill(target, signal.SIGTERM)
            except ProcessLookupError:
                pass
    for _ in range(int(timeout / 0.1)):
        if not dirt_listening(port):
            return True
        await asyncio.sleep(0.1)
    return False
