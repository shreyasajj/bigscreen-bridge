"""Flimmer's local channel catalogue and acknowledged player controls."""
import asyncio
import json
import re
import uuid

from dbus_next import Message, MessageType

from .adapters import BridgeError

BUS_NAME = "io.github.jclsn.Flimmer.Control"
OBJECT_PATH = "/io/github/jclsn/Flimmer/Control"
_ID = re.compile(r"channel-[0-9a-f]{64}")


def validate_channel(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise BridgeError("invalid_request", "Use a Flimmer channel ID from media browsing")
    return value


def validate_page(offset=0, limit=50):
    if type(offset) is not int or not 0 <= offset <= 1000000 or type(limit) is not int or not 1 <= limit <= 50:
        raise BridgeError("invalid_request", "Invalid channel page bounds")


class FlimmerHook:
    def __init__(self, bus, enabled=False):
        self.bus = bus
        self.enabled = enabled
        self.state = None

    async def request(self, command, request_id=None, **kwargs):
        request_id = request_id or str(uuid.uuid4())
        try:
            reply = await asyncio.wait_for(self.bus.call(Message(
                destination=BUS_NAME, path=OBJECT_PATH, interface=BUS_NAME, member="Execute", signature="s",
                body=[json.dumps({"id": request_id, "command": command, **kwargs})])), 5)
        except TimeoutError as exc:
            raise BridgeError("command_timeout", "Flimmer did not acknowledge the command") from exc
        if reply.message_type == MessageType.ERROR:
            raise BridgeError("not_ready", "Flimmer control endpoint is unavailable")
        try:
            response = json.loads(reply.body[0])
            if not isinstance(response, dict) or response.get("id") != request_id:
                raise ValueError("Mismatched response ID")
            if not response.get("ok"):
                raise BridgeError(response["error"]["code"], response["error"]["message"])
            if not isinstance(response["result"], dict):
                raise ValueError("Expected result object")
            return response["result"]
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise BridgeError("invalid_response", "Invalid Flimmer control response") from exc

    async def refresh(self):
        try:
            state = await self.request("get_state")
            if (state.get("protocol") != 1 or not isinstance(state.get("ready"), bool)
                    or state.get("content_types") != ["channel"] or not isinstance(state.get("media"), dict)
                    or not isinstance(state.get("capabilities"), list)):
                raise BridgeError("invalid_response", "Unsupported Flimmer control protocol")
            self.state = state
            self.enabled = True
        except BridgeError:
            self.state = None

    async def wait_ready(self):
        try:
            async with asyncio.timeout(15):
                await self.refresh()
                while not self.state or not self.state["ready"]:
                    await asyncio.sleep(0.2)
                    await self.refresh()
        except TimeoutError as exc:
            raise BridgeError("readiness_timeout", "Flimmer did not become ready") from exc

    async def open(self, request_id, channel_id):
        validate_channel(channel_id)
        try:
            async with asyncio.timeout(20):
                await self.wait_ready()
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

    async def browse(self, offset=0, limit=50):
        validate_page(offset, limit)
        await self.refresh()
        if not self.state or not self.state["ready"]:
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
        return result

    async def control(self, request_id, command):
        await self.refresh()
        if not self.state or not self.state["ready"]:
            raise BridgeError("not_ready", "Flimmer player is unavailable")
        if command not in ("play", "pause", "stop") or command not in self.state["capabilities"]:
            raise BridgeError("unsupported", "Flimmer does not support this player command")
        result = await self.request(command, request_id)
        await self.refresh()
        return result
