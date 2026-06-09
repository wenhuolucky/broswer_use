from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Slot:
    index: int
    display: str
    web_port: int


class DisplayPool:
    def __init__(self, *, size: int, display_base: int, port_base: int) -> None:
        self._size = size
        self._display_base = display_base
        self._port_base = port_base
        self._free = set(range(size))
        self._lock = asyncio.Lock()

    @property
    def in_use(self) -> int:
        return self._size - len(self._free)

    async def acquire(self) -> Slot:
        async with self._lock:
            if not self._free:
                raise RuntimeError(f"remote login capacity reached ({self._size})")
            index = min(self._free)
            self._free.remove(index)
            return Slot(
                index=index,
                display=f":{self._display_base + index}",
                web_port=self._port_base + index,
            )

    async def release(self, slot: Slot) -> None:
        async with self._lock:
            self._free.add(slot.index)
