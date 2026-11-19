#!/usr/bin/env python3
"""Authenticated Wayland end-to-end and cold-launch tests for the YouTube adapter."""

import asyncio
import importlib.util
import json
import os
import signal
import uuid
from pathlib import Path

import aiohttp

spec = importlib.util.spec_from_file_location("native", Path(__file__).with_name("smoke-vacuumtube.py"))
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


async def main():
    config = json.loads((Path.home() / ".config/bigscreen-bridge/config.json").read_text())
    async with (
        aiohttp.ClientSession(headers={"Authorization": "Bearer " + config["token"]}) as session,
        session.ws_connect("http://127.0.0.1:8765/ws") as ws,
    ):
        await ws.receive_json()

        async def command(command, request_id=None, **kwargs):
            request_id = request_id or str(uuid.uuid4())
            await ws.send_json(dict(id=request_id, command=command, **kwargs))
            async with asyncio.timeout(55):
                while True:
                    response = await ws.receive_json()
                    if response.get("id") == request_id:
                        assert response["ok"], response.get("error", {}).get("code")
                        return response["result"]

        async def wait_for(predicate):
            async with asyncio.timeout(30):
                while True:
                    state = await command("get_state")
                    app = next(a for a in state["apps"] if a["id"] == "vacuumtube")
                    if predicate(state, app):
                        return app
                    await asyncio.sleep(0.3)

        before = native.ui_processes()
        await command("focus", app="vacuumtube")
        await wait_for(lambda s, a: s["active_app"] == "vacuumtube" and a["control_available"])
        assert len(before) == 1 and before == native.ui_processes()
        print("PASS authenticated KWin focus preserves the existing VacuumTube process")
        request_id = str(uuid.uuid4())
        args = dict(app="vacuumtube", content_type="video", content_id="M7lc1UVf-VE")
        opened = await command("open", request_id, **args)
        assert opened["delivery"] == "video_selected"
        assert await command("open", request_id, **args) == opened
        await wait_for(lambda s, a: a.get("media", {}).get("state") == "playing" and a["media"].get("title"))
        print("PASS authenticated video selection, replay acknowledgement and live metadata")
        await command("pause", app="vacuumtube")
        await wait_for(lambda s, a: a.get("state") == "paused")
        await command("play", app="vacuumtube")
        await wait_for(lambda s, a: a.get("state") == "playing")
        await command("seek", app="vacuumtube", position=12)
        await wait_for(lambda s, a: 11 <= a.get("media", {}).get("position", -1) <= 20)
        print("PASS authenticated pause, play and absolute seek")
        app_state = await wait_for(lambda s, a: "volume_set" in a["capabilities"])
        original_volume = app_state["media"]["volume"]
        original_muted = app_state["media"]["muted"]
        try:
            await command("volume_mute", app="vacuumtube", muted=False)
            await command("volume_set", app="vacuumtube", volume_level=0.25)
            await wait_for(lambda s, a: abs(a.get("media", {}).get("output_volume", -1) - 0.25) < 0.01)
            await command("volume_mute", app="vacuumtube", muted=True)
            await wait_for(
                lambda s, a: a.get("media", {}).get("muted") is True and a["media"].get("output_volume") == 0
            )
            print("PASS authenticated volume and mute share the native app controller")
        finally:
            await command("volume_set", app="vacuumtube", volume_level=original_volume)
            await command("volume_mute", app="vacuumtube", muted=original_muted)
        await command("open", app="vacuumtube", content_type="search", content_id="KDE Bigscreen")
        await wait_for(lambda s, a: a.get("view", {}).get("query") == "KDE Bigscreen")
        print("PASS authenticated search confirms the real Leanback query")

        # Restart only the main GUI; the bridge delivers content to the normal profile.
        processes = native.ui_processes()
        assert len(processes) == 1
        for pid in processes:
            os.kill(pid, signal.SIGTERM)
        async with asyncio.timeout(10):
            while native.ui_processes():  # noqa: ASYNC110 - polls the process list; nothing to await
                await asyncio.sleep(0.1)
        selected = await command(
            "open", app="vacuumtube", content_type="playlist", content_id="PLBCF2DAC6FFB574DE"
        )
        assert selected["delivery"] == "playlist_selected"
        app = await wait_for(
            lambda s, a: (
                a.get("media", {}).get("state") == "playing"
                and a.get("view", {}).get("playlist_id") == "PLBCF2DAC6FFB574DE"
            )
        )
        assert len(native.ui_processes()) == 1
        print("PASS cold-launch playlist delivery to one normal-profile Electron instance")
        assert app["media"]["duration"] > 0
        await command("stop", app="vacuumtube")
        await wait_for(lambda s, a: a.get("state") == "idle")
        await command("focus", app="couchside")
        print("PASS test playback stopped; Couchside focus restored")


if __name__ == "__main__":
    asyncio.run(main())
