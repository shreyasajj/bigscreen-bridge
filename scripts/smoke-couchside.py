#!/usr/bin/env python3
"""Run on the TV with the bridge venv; exercise only UI navigation, never playback."""

import argparse
import asyncio
import json
import uuid
from pathlib import Path

from dbus_next import Message, MessageType
from dbus_next.aio import MessageBus

BUS = "io.github.shreyasajj.Couchside.Control"
PATH = "/io/github/shreyasajj/Couchside/Control"


async def request(bus, command, request_id=None, **kwargs):
    request_id = request_id or str(uuid.uuid4())
    reply = await asyncio.wait_for(
        bus.call(
            Message(
                destination=BUS,
                path=PATH,
                interface=BUS,
                member="Execute",
                signature="s",
                body=[json.dumps(dict(id=request_id, command=command, **kwargs))],
            )
        ),
        30,
    )
    assert reply.message_type != MessageType.ERROR, reply.error_name
    data = json.loads(reply.body[0])
    assert data["id"] == request_id
    return data


async def main(items_path):
    bus = await MessageBus().connect()
    try:
        async with asyncio.timeout(30):
            while True:
                state = await request(bus, "get_state")
                if state["ok"] and state["result"].get("ma_player_id"):
                    break
                await asyncio.sleep(0.5)
        for kind, value in [("now_playing", ""), ("search", ""), ("search", "Björk")]:
            result = await request(bus, "navigate", content_type=kind, content_id=value)
            assert result["ok"], result
            assert result["result"]["view"] == {"type": kind, "id": value}
            print("PASS QML navigation:", kind)
        if items_path:
            items = json.loads(await asyncio.to_thread(Path(items_path).read_text))
            for kind, uri in items.items():
                result = await request(bus, "navigate", content_type=kind, content_id=uri)
                assert result["ok"], result
                print("PASS exact music item:", kind)
        result = await request(bus, "navigate", content_type="track", content_id="library://album/1")
        assert result["error"]["code"] == "invalid_request"
        result = await request(bus, "play")
        assert result["error"]["code"] == "unsupported"
        await request(bus, "navigate", content_type="now_playing", content_id="")
        print("PASS unsupported playback and malformed content rejection")
    finally:
        bus.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--items", help="JSON file of real MA kind-to-URI mappings")
    asyncio.run(main(parser.parse_args().items))
