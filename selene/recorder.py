"""Record what SuperCollider plays to a WAV file.

sclang won't run code sent over OSC, so the recorder is a few OSCdefs that
have to live in sclang already. When selene started sclang itself it types
them in; otherwise they go in the player's startup.scd (with their OK).
"""

import asyncio
import re
import struct
from pathlib import Path

from .superdirt import DIRT_PORT

VERSION = 1
BEGIN = "// >>> selene recorder"
END = "// <<< selene recorder"

# Messages to and from sclang, all on its language port (SuperDirt's 57120).
# Replies go back to whoever asked.
SC_CODE = f"""{BEGIN} v{VERSION}: lets selene record to WAV (/record). Added by selene;
// delete everything down to the end marker to remove it.
(
OSCdef(\\seleneRecPing, {{ |msg, time, addr|
    addr.sendMsg('/selene/rec/pong', {VERSION}, s.serverRunning.binaryValue,
        s.isRecording.binaryValue);
}}, '/selene/rec/ping');
OSCdef(\\seleneRecPrepare, {{ |msg, time, addr|
    var path = msg[1].asString;
    case
    {{ s.serverRunning.not }} {{ addr.sendMsg('/selene/rec/error', "the server isn't running") }}
    {{ s.isRecording }} {{ addr.sendMsg('/selene/rec/error', "SuperCollider is already recording") }}
    {{
        fork {{
            if(s.recorder.path.notNil) {{ s.stopRecording }};  // an unused earlier prepare
            s.recHeaderFormat = "wav";
            s.recSampleFormat = "int24";
            s.prepareForRecord(path, 2);
            s.sync;
            addr.sendMsg('/selene/rec/ready', path);
        }}
    }};
}}, '/selene/rec/prepare');
OSCdef(\\seleneRecGo, {{ |msg, time, addr|
    // Unprepared, s.record would invent a file of its own.
    if(s.recorder.path.isNil) {{ addr.sendMsg('/selene/rec/error', "nothing prepared") }} {{
        s.record;
        addr.sendMsg('/selene/rec/started', s.recorder.path.asString);
    }};
}}, '/selene/rec/go');
OSCdef(\\seleneRecStop, {{ |msg, time, addr|
    var path = s.recorder.path;
    if(path.notNil) {{ s.stopRecording }};
    addr.sendMsg('/selene/rec/stopped', path.asString);
}}, '/selene/rec/stop');
);
{END}
"""


# ── startup.scd ───────────────────────────────────────────────────────────

_BLOCK = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END) + r"[^\n]*\n?", re.S)


def installed_version(startup: Path) -> int | None:
    """The recorder version in startup.scd, or None if it isn't there."""
    try:
        text = startup.read_text(errors="ignore")
    except OSError:
        return None
    m = re.search(re.escape(BEGIN) + r" v(\d+)", text)
    return int(m.group(1)) if m else None


def install(startup: Path) -> Path | None:
    """Add (or update) the recorder block at the end of startup.scd, outside
    any other code. Returns the backup of the old file, if there was one."""
    try:
        old = startup.read_text()
    except FileNotFoundError:
        old = None
    backup = None
    if old is not None:
        backup = startup.with_name(startup.name + ".selene-bak")
        backup.write_text(old)
    body = _BLOCK.sub("", old or "").rstrip()
    startup.parent.mkdir(parents=True, exist_ok=True)
    startup.write_text((body + "\n\n" if body else "") + SC_CODE)
    return backup


# ── talking to sclang ─────────────────────────────────────────────────────

def _pad(b: bytes) -> bytes:
    return b + b"\0" * (-len(b) % 4)


def osc(address: str, *args: str | int) -> bytes:
    tags = "," + "".join("s" if isinstance(a, str) else "i" for a in args)
    out = _pad(address.encode() + b"\0") + _pad(tags.encode() + b"\0")
    for a in args:
        out += _pad(a.encode() + b"\0") if isinstance(a, str) else struct.pack(">i", a)
    return out


class Pong:
    def __init__(self, args: list):
        self.version = int(args[0]) if args else 0
        self.server_running = bool(args[1]) if len(args) > 1 else False
        self.recording = bool(args[2]) if len(args) > 2 else False


class Recorder:
    """Asks sclang's recorder OSCdefs to record, and waits for their replies."""

    def __init__(self, port: int = DIRT_PORT):
        self.port = port
        self.transport: asyncio.DatagramTransport | None = None
        self.replies: asyncio.Queue[tuple[str, list]] = asyncio.Queue()

    async def _open(self) -> None:
        if self.transport:
            return
        from .flow import osc_messages
        replies = self.replies

        class Listener(asyncio.DatagramProtocol):
            def datagram_received(self, data, addr):
                for message in osc_messages(data):
                    replies.put_nowait(message)

        self.transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            Listener, local_addr=("127.0.0.1", 0))

    async def _ask(self, address: str, *args, expect: str, timeout: float) -> list:
        """Send, then wait for `expect`. Raises RuntimeError on /selene/rec/error
        or no answer."""
        await self._open()
        while not self.replies.empty():  # anything left over from a timed-out ask
            self.replies.get_nowait()
        self.transport.sendto(osc(address, *args), ("127.0.0.1", self.port))
        try:
            async with asyncio.timeout(timeout):
                while True:
                    addr, reply = await self.replies.get()
                    if addr == expect:
                        return reply
                    if addr == "/selene/rec/error":
                        raise RuntimeError(reply[0] if reply else "recorder error")
        except TimeoutError:
            raise RuntimeError("SuperCollider didn't answer") from None

    async def ping(self, timeout: float = 1.0) -> Pong | None:
        try:
            return Pong(await self._ask("/selene/rec/ping", expect="/selene/rec/pong",
                                        timeout=timeout))
        except RuntimeError:
            return None

    async def prepare(self, path: Path) -> None:
        """Open the file and get the disk buffer ready, so `go` starts at once."""
        await self._ask("/selene/rec/prepare", str(path), expect="/selene/rec/ready", timeout=5)

    async def go(self) -> None:
        await self._ask("/selene/rec/go", expect="/selene/rec/started", timeout=2)

    async def stop(self) -> None:
        await self._ask("/selene/rec/stop", expect="/selene/rec/stopped", timeout=3)

    def close(self) -> None:
        if self.transport:
            self.transport.close()
            self.transport = None
