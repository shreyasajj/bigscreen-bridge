import asyncio

import pytest

from bigscreen_bridge.adapters import APPS
from bigscreen_bridge.server import Bridge, application


class WindowManager:
    def __init__(self):
        self.ready = asyncio.Event()
        self.ready.set()
        self.windows = []
        self.focused = []

    async def refresh(self):
        pass

    async def focus(self, window_id):
        self.focused.append(window_id)
        for window in self.windows:
            window["active"] = window["id"] == window_id


@pytest.fixture
async def bridge(aiohttp_client):
    bridge = Bridge({"token": "a" * 48, "device_id": "test-device", "name": "TV"}, WindowManager())
    bridge.installed = {a.flatpak for a in APPS}
    client = await aiohttp_client(application(bridge))
    return bridge, client


