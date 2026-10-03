"""Detect or boot SuperCollider + SuperDirt (sclang listening on UDP 57120)."""

import asyncio
import shutil
import socket
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


HANDSHAKE = b"/dirt/handshake\0,\0\0\0"  # OSC message, no arguments


def dirt_status(port: int = DIRT_PORT, timeout: float = 1.0) -> str:
    """'listening' if SuperDirt answers a handshake, 'sclang' if only the port
    is held, else 'off'. Blocking; call from a thread."""
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
    return "sclang" if dirt_listening(port) else "off"


def find_sclang() -> str | None:
    return shutil.which("sclang") or next((p for p in SCLANG_CANDIDATES if Path(p).exists()), None)


class SuperDirt:
    def __init__(self, on_output: Callable[[str], None] = lambda line: None):
        self.on_output = on_output
        self.proc: asyncio.subprocess.Process | None = None

    @property
    def owned(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def boot(self, timeout: float = 60) -> bool:
        """Launch sclang; resolve once SuperDirt says it's listening."""
        sclang = find_sclang()
        if not sclang:
            self.on_output("sclang not found; install SuperCollider")
            return False
        # sclang always runs the user's startup.scd. If that already starts
        # SuperDirt, passing our script too would boot it twice.
        uses_startup = STARTUP.exists() and "SuperDirt" in STARTUP.read_text(errors="ignore")
        args = [sclang] if uses_startup else [sclang, str(BUNDLED_STARTUP)]
        self.proc = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        ready = asyncio.get_running_loop().create_future()
        asyncio.create_task(self._read(ready))
        try:
            return await asyncio.wait_for(ready, timeout)
        except asyncio.TimeoutError:
            self.on_output(f"SuperDirt not listening after {timeout:.0f}s")
            return dirt_listening()

    async def _read(self, ready: asyncio.Future) -> None:
        assert self.proc and self.proc.stdout
        while raw := await self.proc.stdout.readline():
            line = raw.decode(errors="replace").rstrip()
            if line:
                self.on_output(line)
            if "SuperDirt: listening" in line and not ready.done():
                ready.set_result(True)
        if not ready.done():
            ready.set_result(False)

    async def stop(self) -> None:
        if not self.owned:
            return
        try:
            self.proc.stdin.write(b"Server.killAll; 0.exit;\x0c")
            await self.proc.stdin.drain()
            await asyncio.wait_for(self.proc.wait(), 5)
        except (asyncio.TimeoutError, BrokenPipeError, ConnectionResetError):
            self.proc.kill()
            await self.proc.wait()
