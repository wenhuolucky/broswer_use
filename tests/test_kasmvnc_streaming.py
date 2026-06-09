from __future__ import annotations

import asyncio

import pytest


def test_display_pool_allocates_and_reuses_slots():
    from app.streaming.display_pool import DisplayPool

    async def run():
        pool = DisplayPool(size=2, display_base=100, port_base=6900)

        first = await pool.acquire()
        second = await pool.acquire()

        assert first.display == ":100"
        assert first.web_port == 6900
        assert second.display == ":101"
        assert second.web_port == 6901
        assert pool.in_use == 2

        await pool.release(first)
        reused = await pool.acquire()

        assert reused == first

    asyncio.run(run())


def test_display_pool_raises_when_full():
    from app.streaming.display_pool import DisplayPool

    async def run():
        pool = DisplayPool(size=1, display_base=100, port_base=6900)
        await pool.acquire()

        with pytest.raises(RuntimeError, match="remote login capacity"):
            await pool.acquire()

    asyncio.run(run())


def test_start_stream_builds_xvnc_command(monkeypatch, tmp_path):
    from app.streaming.display_pool import Slot
    from app.streaming.kasmvnc import start_stream

    created = {}
    www = tmp_path / "www"
    www.mkdir()
    (www / "index.html").write_text("ok", encoding="utf-8")

    class FakeProc:
        returncode = None

        async def wait(self):
            return 0

        def terminate(self):
            created["terminated"] = True

        def kill(self):
            created["killed"] = True

    async def fake_create_subprocess_exec(*args, env, stdout, stderr):
        created["args"] = args
        created["env"] = env
        created["stdout"] = stdout
        created["stderr"] = stderr
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    async def fake_wait_display_ready(*args, **kwargs):
        return True

    monkeypatch.setattr("app.streaming.kasmvnc._wait_display_ready", fake_wait_display_ready)

    async def run():
        return await start_stream(
            Slot(index=0, display=":100", web_port=6900),
            kasmvnc_bin="Xvnc",
            screen="1440x900x24",
            www_dir=str(www),
        )

    stream = asyncio.run(run())

    assert created["args"] == (
        "Xvnc",
        ":100",
        "-geometry",
        "1440x900",
        "-depth",
        "24",
        "-SecurityTypes",
        "None",
        "-DisableBasicAuth",
        "-interface",
        "127.0.0.1",
        "-websocketPort",
        "6900",
        "-httpd",
        str(www),
    )
    assert created["env"]["DISPLAY"] == ":100"
    assert stream.slot.display == ":100"
