#!/usr/bin/env python3
"""TV test: secondary launch and optional UI restart; never stop Sendspin or play media."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import uuid

from aiohttp import ClientSession
from dbus_next.aio import MessageBus
from importlib.util import module_from_spec, spec_from_file_location

ipc_spec = spec_from_file_location("smoke_couchside_ipc", Path(__file__).with_name("smoke-couchside.py"))
ipc = module_from_spec(ipc_spec)
ipc_spec.loader.exec_module(ipc)
request = ipc.request

APP = "io.github.shreyasajj.Couchside"


def processes():
    ui = set()
    audio = set()
    for directory in Path("/proc").iterdir():
        if not directory.name.isdigit():
            continue
        try:
            if directory.stat().st_uid != os.getuid():
                continue
            argv = (directory / "cmdline").read_bytes().split(b"\0")
            stamp = (directory / "stat").read_text().split()[21]
            identity = (int(directory.name), stamp)
            if b"/app/bin/couchside" in argv:
                ui.add(identity)
            if (b"/app/bin/couchside-sendspin" in argv
                    or b"from sendspin.cli import main" in b" ".join(argv)):
                audio.add(identity)
        except (OSError, IndexError):
            continue
    return ui, audio


async def desktop_environment():
    env = dict(os.environ)
    proc = await asyncio.create_subprocess_exec("pgrep", "-u", str(os.getuid()), "-x", "plasmashell",
                                               stdout=asyncio.subprocess.PIPE)
    out, _ = await proc.communicate()
    pid = out.decode().splitlines()[0]
    for entry in (Path("/proc") / pid / "environ").read_bytes().split(b"\0"):
        key, separator, value = entry.partition(b"=")
        name = key.decode()
        if separator and (name in {"WAYLAND_DISPLAY", "DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
                                   "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP"} or name.startswith("QT_")):
            env[name] = value.decode()
    return env


async def main(restart_ui):
    before_ui, before_audio = processes()
    assert len(before_ui) == 1 and before_audio, (before_ui, before_audio)
    bus = await MessageBus().connect()
    try:
        original = await request(bus, "get_state")
        proc = await asyncio.create_subprocess_exec("flatpak", "run", APP, env=await desktop_environment(),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        assert await asyncio.wait_for(proc.wait(), 20) == 0
        assert processes() == (before_ui, before_audio)
        after = await request(bus, "get_state")
        assert after["result"]["view"] == original["result"]["view"]
        print("PASS secondary launch reused the same UI and preserved navigation")
        if not restart_ui:
            return
        # This test deliberately terminates only the identified UI, never flatpak kill.
        os.kill(next(iter(before_ui))[0], signal.SIGTERM)
        async with asyncio.timeout(10):
            while processes()[0]:
                await asyncio.sleep(0.1)
        assert processes()[1] == before_audio
        config = json.loads((Path.home() / ".config/bigscreen-bridge/config.json").read_text())
        async with ClientSession() as session:
            async with session.ws_connect("http://127.0.0.1:8765/ws",
                    headers={"Authorization": "Bearer " + config["token"]}) as ws:
                await ws.receive_json()
                for kind, value in [("now_playing", ""), ("search", "Music")]:
                    request_id = str(uuid.uuid4())
                    await ws.send_json(dict(id=request_id, command="open", app="couchside",
                                            content_type=kind, content_id=value))
                    while True:
                        result = await ws.receive_json(timeout=60)
                        if result.get("id") == request_id:
                            break
                    assert result["ok"], result
                    assert result["result"]["view"] == {"type": kind, "id": value}
                new_ui, new_audio = processes()
                assert len(new_ui) == 1 and new_ui != before_ui and new_audio == before_audio
                print("PASS WebSocket cold launch and search after readiness; original Sendspin PIDs unchanged")
    finally:
        bus.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--restart-ui", action="store_true")
    asyncio.run(main(parser.parse_args().restart_ui))
