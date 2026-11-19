"""Stremio's native application control; its MQTT transport stays independent."""

import math
import re
from typing import ClassVar

from .adapters import BridgeError
from .hooks import DBusHook

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
        if (
            not isinstance(value, dict)
            or set(value) != {"series_id", "video_id"}
            or not all(valid_id(v) for v in value.values())
        ):
            raise BridgeError("invalid_request", "Episode requires series_id and video_id")
    elif not valid_id(value):
        raise BridgeError("invalid_request", "Invalid Stremio ID")
    return kind, value


def validate_seek(value):
    try:
        valid = (
            not isinstance(value, bool)
            and isinstance(value, (float, int))
            and math.isfinite(value)
            and value >= 0
        )
    except OverflowError:
        valid = False
    if not valid:
        raise BridgeError("invalid_request", "Seek position must be finite and nonnegative")


class StremioHook(DBusHook):
    app = "stremio"
    label = "Stremio"
    kinds = KINDS
    bus_name = BUS_NAME
    object_path = OBJECT_PATH
    timeouts: ClassVar[dict[str, float]] = {"open": 25, "browse": 25}
    require_result_object = False

    def check_item(self, kind, value):
        validate_content(kind, value)

    def accept(self, state):
        state = super().accept(state)
        if not isinstance(state.get("media"), dict):
            raise BridgeError("invalid_response", "Unsupported Stremio control protocol")
        return state

    async def open(self, request_id, kind, value, resume=False):
        await self.wait_ready(15, message="Stremio renderer did not become ready")
        if kind not in self.content_types:
            raise BridgeError("unsupported", "The running Stremio does not support this content type")
        result = await self.request("open", request_id, content_type=kind, content_id=value, resume=resume)
        await self.refresh()
        return result
