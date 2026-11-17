"""Private Unix-socket control for the tested Electron/Leanback application hook."""
import math
import re

from .adapters import BridgeError
from .hooks import SocketHook

VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
PLAYLIST_ID = re.compile(r"[A-Za-z0-9_-]{10,128}")
KINDS = ("video", "playlist", "search")


def validate_content(kind, value):
    if kind not in KINDS:
        raise BridgeError("unsupported", "Unsupported YouTube content type")
    if not isinstance(value, str):
        raise BridgeError("invalid_request", "YouTube content ID must be a string")
    if ((kind == "video" and not VIDEO_ID.fullmatch(value))
            or (kind == "playlist" and not PLAYLIST_ID.fullmatch(value))
            or (kind == "search" and (not value.strip() or len(value) > 200
                                      or any(ord(c) < 32 or ord(c) == 127 for c in value)))):
        raise BridgeError("invalid_request", "Invalid YouTube content ID")
    return kind, value


def validate_volume(value):
    try:
        valid = (not isinstance(value, bool) and isinstance(value, (int, float))
                 and math.isfinite(value) and 0 <= value <= 1)
    except OverflowError:
        valid = False
    if not valid:
        raise BridgeError("invalid_request", "Volume must be between zero and one")


class VacuumTubeHook(SocketHook):
    app = "vacuumtube"
    label = "VacuumTube"
    kinds = KINDS
    socket_name = "vacuumtube.sock"
    timeouts = {"open": 25}

    def accept(self, state):
        state = super().accept(state)
        if (not isinstance(state.get("ready"), bool) or not isinstance(state.get("capabilities"), list)
                or not isinstance(state.get("media"), dict)):
            raise BridgeError("invalid_response", "Unsupported VacuumTube control protocol")
        media = state["media"]
        video_id = media.get("video_id")
        if video_id:
            validate_content("video", video_id)
            # Artwork comes from the fixed thumbnail host, never from app-supplied URLs.
            media = {**media, "content_id": video_id, "type": "video",
                     "poster": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"}
        return {**state, "media": media}

    async def open(self, request_id, kind, value):
        validate_content(kind, value)
        await self.wait_ready(20, lambda: self.is_ready() and kind in self.content_types,
                              "YouTube Leanback did not become ready")
        result = await self.request("open", request_id, content_type=kind, content_id=value)
        await self.refresh()
        return result
