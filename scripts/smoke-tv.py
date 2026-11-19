#!/usr/bin/env python3
"""Run on the TV using the bridge venv. Never print the pairing token."""

import argparse
import asyncio
import json
import uuid
from collections import Counter
from pathlib import Path

import aiohttp


async def process_ids():
    proc = await asyncio.create_subprocess_exec(
        "flatpak", "ps", "--columns=application", stdout=asyncio.subprocess.PIPE
    )
    out, _ = await proc.communicate()
    return Counter(out.decode().splitlines())


async def main(focus):
    config = json.loads((Path.home() / ".config/bigscreen-bridge/config.json").read_text())
    url = "http://127.0.0.1:" + str(config.get("port", 8765))
    async with aiohttp.ClientSession() as session:
        async with session.get(url + "/ws") as response:
            assert response.status == 401
        async with session.ws_connect(
            url + "/ws", headers={"Authorization": "Bearer " + config["token"]}
        ) as ws:
            initial = await ws.receive_json()
            assert initial["protocol"] == 1
            print(json.dumps({"active_app": initial["active_app"], "apps": initial["apps"]}))

            async def command(command, **kwargs):
                request_id = str(uuid.uuid4())
                await ws.send_json({"id": request_id, "command": command, **kwargs})
                while True:
                    message = await ws.receive_json(timeout=35)
                    if message.get("id") == request_id:
                        return message

            assert len((await command("browse"))["result"]["items"]) == 4
            unsupported = await command("focus", app="stremio", content={"movie": "tt0133093"})
            assert not unsupported["ok"] and unsupported["error"]["code"] == "unsupported"
            if focus:
                before = await process_ids()
                try:
                    for app in initial["apps"]:
                        if not app["running"]:
                            continue
                        ack = await command("focus", app=app["id"])
                        assert ack["ok"], ack
                        state = (await command("get_state"))["result"]
                        assert state["active_app"] == app["id"], state
                        print("PASS existing-window focus:", app["id"])
                finally:
                    if initial["active_app"]:
                        restored = await command("focus", app=initial["active_app"])
                        assert restored["ok"]
                assert await process_ids() == before, "Unexpected app process count change"
                print("PASS no extra Flatpak instances")
            print("PASS authentication, snapshot, app browsing, unsupported content rejection")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--focus", action="store_true", help="Activate existing app windows and restore initial app"
    )
    args = parser.parse_args()
    asyncio.run(main(args.focus))
