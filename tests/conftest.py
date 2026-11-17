import asyncio
import os
from pathlib import Path
import sys

import pytest


from bigscreen_bridge.adapters import APPS
from bigscreen_bridge.server import Bridge, application

# The integration is a separate repository; these tests run it against the real bridge.
INTEGRATION = Path(os.environ.get("BIGSCREEN_INTEGRATION", Path(__file__).resolve().parents[2] / "ha-bigscreen-bridge"))
if not (INTEGRATION / "custom_components/bigscreen_bridge/manifest.json").is_file():
    raise pytest.UsageError(f"Check out ha-bigscreen-bridge at {INTEGRATION} or set BIGSCREEN_INTEGRATION")
sys.path.insert(0, str(INTEGRATION))


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


