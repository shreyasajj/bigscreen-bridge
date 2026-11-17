"""Stremio's native application control; its MQTT transport stays independent."""
import asyncio
import json
import math
import re
import uuid

from dbus_next import Message, MessageType

from .adapters import BridgeError

BUS_NAME = "io.github.shreyasajj.StremioTV.Control"
OBJECT_PATH = "/io/github/shreyasajj/StremioTV/Control"
KINDS = ("movie", "series", "episode")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,255}")


def valid_id(value):
    return isinstance(value, str) and bool(_ID.fullmatch(value))


def validate_content(kind, value, resume=False):
    if kind not in KINDS:
        raise BridgeError("unsupported", "Unsupported Stremio content type")
    if not isinstance(resume, bool):
        raise BridgeError("invalid_request", "Resume must be boolean")
    if kind == "episode":
        if (not isinstance(value, dict) or set(value) != {"series_id", "video_id"}
                or not all(valid_id(v) for v in value.values())):
            raise BridgeError("invalid_request", "Episode requires series_id and video_id")
    elif not valid_id(value):
        raise BridgeError("invalid_request", "Invalid Stremio ID")
    return kind, value


def validate_seek(value):
    try:
        valid = not isinstance(value, bool) and isinstance(value, (float, int)) and math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid:
        raise BridgeError("invalid_request", "Seek position must be finite and nonnegative")


class StremioHook:
    def __init__(self, bus, enabled=False):
        self.bus = bus
        self.enabled = enabled
        self.state = None
        self.content_types = list(KINDS) if enabled else []

    async def request(self, command, request_id=None, **kwargs):
        request_id = request_id or str(uuid.uuid4())
        try:
            reply = await asyncio.wait_for(self.bus.call(Message(
                destination=BUS_NAME, path=OBJECT_PATH, interface=BUS_NAME, member="Execute", signature="s",
                body=[json.dumps({"id": request_id, "command": command, **kwargs})])),
                25 if command == "open" else 5)
        except TimeoutError as exc:
            raise BridgeError("command_timeout", "Stremio did not acknowledge the command") from exc
        if reply.message_type == MessageType.ERROR:
            raise BridgeError("not_ready", "Stremio control endpoint is unavailable")
        try:
            response = json.loads(reply.body[0])
            if not isinstance(response, dict) or response.get("id") != request_id:
                raise ValueError("Mismatched response ID")
            if not response.get("ok"):
                raise BridgeError(response["error"]["code"], response["error"]["message"])
            return response["result"]
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise BridgeError("invalid_response", "Invalid Stremio control response") from exc

    async def refresh(self):
        try:
            state = await self.request("get_state")
            if (not isinstance(state, dict) or state.get("protocol") != 1
                    or not isinstance(state.get("content_types"), list)
                    or not isinstance(state.get("media"), dict)):
                raise BridgeError("invalid_response", "Unsupported Stremio control protocol")
            self.state = state
            self.content_types = [kind for kind in KINDS if kind in state["content_types"]]
            self.enabled = True
        except BridgeError:
            self.state = None

    async def open(self, request_id, kind, value, resume=False):
        try:
            async with asyncio.timeout(15):
                await self.refresh()
                while not self.state or not self.state.get("ready"):
                    await asyncio.sleep(0.2)
                    await self.refresh()
        except TimeoutError as exc:
            raise BridgeError("readiness_timeout", "Stremio renderer did not become ready") from exc
        if kind not in self.content_types:
            raise BridgeError("unsupported", "The running Stremio does not support this content type")
        result = await self.request("open", request_id, content_type=kind, content_id=value, resume=resume)
        await self.refresh()
        return result

    async def control(self, request_id, command, **kwargs):
        await self.refresh()
        if not self.state or not self.state.get("ready"):
            raise BridgeError("not_ready", "Stremio renderer is unavailable")
        if command not in self.state.get("capabilities", []):
            raise BridgeError("unsupported", "Stremio does not support this player command")
        result = await self.request(command, request_id, **kwargs)
        await self.refresh()
        return result
