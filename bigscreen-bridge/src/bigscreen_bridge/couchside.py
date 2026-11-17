"""UI-only Couchside adapter. Music Assistant retains all playback ownership."""
import asyncio
import json
import re
import uuid

from dbus_next import Message, MessageType

from .adapters import BridgeError

BUS_NAME = "io.github.shreyasajj.Couchside.Control"
OBJECT_PATH = "/io/github/shreyasajj/Couchside/Control"
KINDS = ("now_playing", "search", "track", "album", "artist", "playlist")


def validate_content(kind, value):
    if kind not in KINDS:
        raise BridgeError("unsupported", "Unsupported Couchside content type")
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 32 for c in value):
        raise BridgeError("invalid_request", "Invalid content ID")
    if kind == "now_playing" and value not in ("", "now_playing"):
        raise BridgeError("invalid_request", "Now Playing does not accept an item ID")
    if kind == "search" and len(value) > 512:
        raise BridgeError("invalid_request", "Search is limited to 512 characters")
    if kind not in ("now_playing", "search") and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}://" + kind + r"/[^\s?#]{1,1536}", value):
        raise BridgeError("invalid_request", "Use a Music Assistant URI matching the content type")
    return kind, value


class CouchsideHook:
    def __init__(self, bus, enabled=False):
        self.bus = bus
        self.enabled = enabled
        self.state = None

    async def request(self, command, request_id=None, **kwargs):
        request_id = request_id or str(uuid.uuid4())
        payload = json.dumps({"id": request_id, "command": command, **kwargs})
        try:
            reply = await asyncio.wait_for(self.bus.call(Message(
                destination=BUS_NAME, path=OBJECT_PATH, interface=BUS_NAME, member="Execute",
                signature="s", body=[payload])), 25 if command == "navigate" else 3)
        except TimeoutError as exc:
            raise BridgeError("not_ready", "Couchside control timed out") from exc
        if reply.message_type == MessageType.ERROR:
            raise BridgeError("not_ready", "Couchside control endpoint is unavailable")
        try:
            response = json.loads(reply.body[0])
            if not isinstance(response, dict) or response.get("id") != request_id:
                raise ValueError("Mismatched request ID")
            if not response.get("ok"):
                error = response["error"]
                raise BridgeError(error["code"], error["message"])
            return response["result"]
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise BridgeError("invalid_response", "Invalid Couchside control response") from exc

    async def refresh(self):
        try:
            state = await self.request("get_state")
            if (not isinstance(state, dict) or state.get("protocol") != 1 or state.get("playback_owner") != "music_assistant"
                    or not isinstance(state.get("content_types"), list)):
                raise BridgeError("invalid_response", "Unsupported Couchside control protocol")
            self.state = state
            self.enabled = True
        except BridgeError:
            self.state = None

    async def navigate(self, request_id, kind, value):
        await self.refresh()
        try:
            async with asyncio.timeout(10):
                while (not self.state or not self.state.get("ready") or
                       (kind not in ("now_playing",) and not self.state.get("music_assistant_ready"))):
                    await self.refresh()
                    await asyncio.sleep(0.1)
        except TimeoutError as exc:
            raise BridgeError("readiness_timeout", "Couchside UI control did not become ready") from exc
        if kind not in self.state["content_types"]:
            raise BridgeError("unsupported", "The running Couchside does not support this content type")
        state = await self.request("navigate", request_id, content_type=kind, content_id=value)
        self.state = state
        return state
