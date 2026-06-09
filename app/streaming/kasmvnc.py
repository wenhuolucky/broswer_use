from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from app.streaming.display_pool import Slot


_WWW_CANDIDATES = (
    "/usr/share/kasmvnc/www",
    "/usr/local/share/kasmvnc/www",
    "/usr/share/kasmvncserver/www",
)


@dataclass(slots=True)
class StreamProcess:
    slot: Slot
    procs: list[asyncio.subprocess.Process] = field(default_factory=list)
    log_path: str | None = None
    _logf: object | None = None

    async def stop(self) -> None:
        for proc in self.procs:
            if proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
        for proc in self.procs:
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except (TimeoutError, asyncio.TimeoutError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
        if self._logf is not None:
            try:
                self._logf.close()
            except Exception:
                pass


def _resolve_www(configured: str | None) -> str | None:
    if configured:
        return configured if Path(configured, "index.html").is_file() else None
    for candidate in _WWW_CANDIDATES:
        if Path(candidate, "index.html").is_file():
            return candidate
    for index_file in Path("/usr/share").glob("kasmvnc*/www/index.html"):
        return str(index_file.parent)
    return None


async def start_stream(
    slot: Slot, *, kasmvnc_bin: str, screen: str, www_dir: str | None = None
) -> StreamProcess:
    parts = screen.split("x")
    if len(parts) == 3:
        geometry = f"{parts[0]}x{parts[1]}"
        depth = parts[2]
    else:
        geometry = screen
        depth = "24"

    stream = StreamProcess(slot=slot)
    display_num = slot.display.lstrip(":")
    stream.log_path = str(Path(tempfile.gettempdir()) / f"kasmvnc-X{display_num}.log")
    stream._logf = open(stream.log_path, "wb")
    args = [
        kasmvnc_bin,
        slot.display,
        "-geometry",
        geometry,
        "-depth",
        depth,
        "-SecurityTypes",
        "None",
        "-DisableBasicAuth",
        "-interface",
        "127.0.0.1",
        "-websocketPort",
        str(slot.web_port),
    ]
    www = _resolve_www(www_dir)
    if www:
        args.extend(["-httpd", www])

    env = {**os.environ, "DISPLAY": slot.display}
    proc = await asyncio.create_subprocess_exec(
        *args,
        env=env,
        stdout=stream._logf,
        stderr=stream._logf,
    )
    stream.procs.append(proc)
    if not await _wait_display_ready(proc, display_num, timeout=12.0):
        await stream.stop()
        raise RuntimeError(f"KasmVNC display {slot.display} did not become ready")
    return stream


async def _wait_display_ready(
    proc: asyncio.subprocess.Process, display_num: str, *, timeout: float
) -> bool:
    sock = Path(f"/tmp/.X11-unix/X{display_num}")
    waited = 0.0
    while waited < timeout:
        if proc.returncode is not None:
            return False
        if sock.exists():
            return True
        await asyncio.sleep(0.2)
        waited += 0.2
    return proc.returncode is None and sock.exists()
