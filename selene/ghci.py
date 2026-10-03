"""A GHCi child process running Tidal, driven over stdin like Pulsar's plugin.

Each statement goes in as a :{ ... :} block followed by a sentinel putStrLn, so
we know exactly which output lines belong to which evaluation. stderr is merged
into stdout to keep errors in order with the sentinel.
"""

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .blocks import split_statements, wrap

BUNDLED_BOOT = Path(__file__).with_name("BootTidal.hs")
SENTINEL = "@@selene:{}@@"
ERROR_LINE = re.compile(r"error:|\*\*\* Exception|parse error|Syntax error")


@dataclass
class EvalResult:
    ok: bool
    output: list[str] = field(default_factory=list)

    @property
    def error_text(self) -> str:
        return "\n".join(self.output).strip()


class Ghci:
    def __init__(self, ghci: str = "ghci", boot: Path = BUNDLED_BOOT,
                 on_output: Callable[[str], None] = lambda line: None):
        self.ghci = ghci
        self.boot = boot
        self.on_output = on_output
        self.proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._seq = 0
        self._pending: dict[str, asyncio.Future] = {}
        self._capture: list[str] | None = None
        self._reader: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def start(self, timeout: float = 90) -> EvalResult:
        self.proc = await asyncio.create_subprocess_exec(
            self.ghci, "-ghci-script", str(self.boot),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._reader = asyncio.create_task(self._read())
        # A user-supplied boot file may set "tidal> " prompts or leave stdout
        # unbuffered (Tidal's threads then interleave mid-line with ours).
        return await self._run([':set prompt ""', ':set prompt-cont ""',
                                "System.IO.hSetBuffering System.IO.stdout System.IO.LineBuffering"],
                               timeout)

    async def eval(self, code: str, timeout: float = 30) -> EvalResult:
        """Evaluate each top-level statement in order; stop at the first error."""
        output: list[str] = []
        for stmt in split_statements(code):
            result = await self._run([wrap(stmt).rstrip("\n")], timeout)
            output += result.output
            if not result.ok:
                return EvalResult(False, output)
        return EvalResult(True, output)

    async def hush(self) -> EvalResult:
        return await self._run(["hush"], 10)

    async def _run(self, lines: list[str], timeout: float) -> EvalResult:
        if not self.running:
            return EvalResult(False, ["GHCi is not running"])
        async with self._lock:
            self._seq += 1
            token = SENTINEL.format(self._seq)
            done = asyncio.get_running_loop().create_future()
            self._pending[token] = done
            self._capture = []
            payload = "\n".join(lines) + f'\nPrelude.putStrLn "{token}"\n'
            self.proc.stdin.write(payload.encode())
            await self.proc.stdin.drain()
            try:
                await asyncio.wait_for(done, timeout)
            except asyncio.TimeoutError:
                self._capture.append(f"(no response from GHCi after {timeout:.0f}s)")
                captured, self._capture = self._capture, None
                self._pending.pop(token, None)
                return EvalResult(False, captured)
            captured, self._capture = self._capture, None
            ok = not any(ERROR_LINE.search(ln) for ln in captured)
            return EvalResult(ok, captured)

    async def _read(self) -> None:
        assert self.proc and self.proc.stdout
        while raw := await self.proc.stdout.readline():
            line = raw.decode(errors="replace").rstrip("\n")
            token = next((t for t in self._pending if t in line), None)
            if token:
                before = line.replace(token, "").strip()
                if before:
                    self._emit(before)
                fut = self._pending.pop(token)
                if not fut.done():
                    fut.set_result(None)
                continue
            self._emit(line)
        for fut in self._pending.values():
            if not fut.done():
                fut.set_result(None)
        self._pending.clear()
        self.on_output("GHCi exited")

    def _emit(self, line: str) -> None:
        if self._capture is not None:
            self._capture.append(line)
        self.on_output(line)

    async def stop(self) -> None:
        if not self.running:
            return
        try:
            self.proc.stdin.write(b"hush\n:quit\n")
            await self.proc.stdin.drain()
            await asyncio.wait_for(self.proc.wait(), 3)
        except (asyncio.TimeoutError, BrokenPipeError, ConnectionResetError):
            self.proc.kill()
            await self.proc.wait()
