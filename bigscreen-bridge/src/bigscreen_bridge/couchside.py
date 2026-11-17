"""UI-only Couchside adapter. Music Assistant retains all playback ownership."""
import re

from .adapters import BridgeError
from .hooks import DBusHook

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


class CouchsideHook(DBusHook):
    app = "couchside"
    label = "Couchside"
    kinds = KINDS
    bus_name = BUS_NAME
    object_path = OBJECT_PATH
    timeout = 3
    timeouts = {"navigate": 25}
    timeout_code = "not_ready"
    require_result_object = False

    def accept(self, state):
        state = super().accept(state)
        if state.get("playback_owner") != "music_assistant":
            raise BridgeError("invalid_response", "Unsupported Couchside control protocol")
        return state

    async def navigate(self, request_id, kind, value):
        # Now Playing needs only the UI; everything else is resolved through Music Assistant.
        await self.wait_ready(10, lambda: self.is_ready() and (
            kind == "now_playing" or self.state.get("music_assistant_ready")),
            "Couchside UI control did not become ready")
        if kind not in self.state["content_types"]:
            raise BridgeError("unsupported", "The running Couchside does not support this content type")
        self.state = await self.request("navigate", request_id, content_type=kind, content_id=value)
        return self.state
