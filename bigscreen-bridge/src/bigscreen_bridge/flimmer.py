"""Flimmer's local channel catalogue and acknowledged player controls."""
import asyncio
import re

from .adapters import BridgeError
from .hooks import DBusHook, validate_page  # noqa: F401  (re-exported for callers)

BUS_NAME = "io.github.jclsn.Flimmer.Control"
OBJECT_PATH = "/io/github/jclsn/Flimmer/Control"
KINDS = ("channel",)
_ID = re.compile(r"channel-[0-9a-f]{64}")


def validate_channel(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise BridgeError("invalid_request", "Use a Flimmer channel ID from media browsing")
    return value


class FlimmerHook(DBusHook):
    app = "flimmer"
    label = "Flimmer"
    kinds = KINDS
    bus_name = BUS_NAME
    object_path = OBJECT_PATH

    def accept(self, state):
        state = super().accept(state)
        if (not isinstance(state.get("ready"), bool) or state["content_types"] != ["channel"]
                or not isinstance(state.get("media"), dict) or not isinstance(state.get("capabilities"), list)):
            raise BridgeError("invalid_response", "Unsupported Flimmer control protocol")
        return state

    async def open(self, request_id, channel_id):
        validate_channel(channel_id)
        try:
            async with asyncio.timeout(20):
                await self.wait_ready()
                # A cold start reports ready before its catalogue has loaded.
                while True:
                    try:
                        result = await self.request("select_channel", request_id, channel_id=channel_id)
                        break
                    except BridgeError as exc:
                        if exc.code != "not_ready":
                            raise
                        await asyncio.sleep(0.2)
        except TimeoutError as exc:
            raise BridgeError("readiness_timeout", "Flimmer channel catalogue did not become ready") from exc
        await self.refresh()
        return result

    @property
    def browsable(self):
        return self.enabled  # the channel catalogue is part of the hook itself

    async def browse(self, offset=0, limit=50):
        validate_page(offset, limit)
        await self.refresh()
        if not self.is_ready():
            raise BridgeError("not_ready", "Open Flimmer to load its channel catalogue")
        result = await self.request("list_channels", offset=offset, limit=limit)
        try:
            items = result["items"]
            if not isinstance(items, list) or len(items) > limit:
                raise ValueError("Invalid channel page")
            for item in items:
                validate_channel(item["id"])
                if not isinstance(item["name"], str):
                    raise ValueError("Invalid channel name")
            next_offset = result["next_offset"]
            if next_offset is not None and (type(next_offset) is not int or not offset < next_offset <= 1000000):
                raise ValueError("Invalid page continuation")
        except (KeyError, TypeError, ValueError, BridgeError) as exc:
            raise BridgeError("invalid_response", "Invalid Flimmer channel catalogue") from exc
        self.recorder.hook_channels(self, result.get("total"))
        return result
