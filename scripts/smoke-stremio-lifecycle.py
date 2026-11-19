#!/usr/bin/env python3
"""Authenticated TV lifecycle test. Uses metadata pages, never starts streams."""

import argparse
import asyncio
import json
import os
import signal
import uuid
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from aiohttp import ClientSession

spec = spec_from_file_location("smoke_stremio", Path(__file__).with_name("smoke-stremio.py"))
smoke = module_from_spec(spec)
spec.loader.exec_module(smoke)


async def main(restart_ui):
    path = Path.home() / ".config/bigscreen-bridge/config.json"
    config = json.loads(path.read_text())
    async with (
        ClientSession() as session,
        session.ws_connect(
            "http://127.0.0.1:8765/ws", headers={"Authorization": "Bearer " + config["token"]}
        ) as ws,
    ):
        initial = await ws.receive_json()
        original_app = initial.get("active_app")

        async def command(command, **kwargs):
            request_id = str(uuid.uuid4())
            await ws.send_json(dict(id=request_id, command=command, **kwargs))
            while True:
                message = await ws.receive_json(timeout=60)
                if message.get("id") == request_id:
                    return message

        assert (await command("focus", app="stremio"))["ok"]
        assert (await command("open", app="stremio", content_type="movie", content_id="tt0133093"))["ok"]
        print("PASS authenticated WebSocket movie navigation and Wayland focus")
        if restart_ui:
            before = smoke.ui_processes()
            assert len(before) == 1
            for pid in before:
                os.kill(pid, signal.SIGTERM)
            async with asyncio.timeout(10):
                while smoke.ui_processes():  # noqa: ASYNC110 - polls the process list; nothing to await
                    await asyncio.sleep(0.1)
            result = await command(
                "open",
                app="stremio",
                content_type="episode",
                content_id={"series_id": "tt0944947", "video_id": "tt0944947:1:2"},
            )
            assert result["ok"], result
            assert result["result"]["video_id"] == "tt0944947:1:2"
            after = smoke.ui_processes()
            assert len(after) == 1 and after != before
            print("PASS cold launch delivers the exact episode after renderer readiness")
        result = await command("pause", app="stremio")
        assert not result["ok"] and result["error"]["code"] == "not_playing"
        state = (await command("get_state"))["result"]
        app = next(a for a in state["apps"] if a["id"] == "stremio")
        assert app["control_available"] and app["state"] == "idle" and app["capabilities"] == ["focus"]
        assert state["active_app"] == "stremio"
        print("PASS idle state, unsupported control gating, and no duplicate player")
        if original_app and original_app != "stremio":
            assert (await command("focus", app=original_app))["ok"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--restart-ui", action="store_true")
    asyncio.run(main(parser.parse_args().restart_ui))
