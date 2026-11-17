"""Private Unix-socket control for the tested Electron/Leanback application hook."""
import asyncio
import contextlib
import json
import math
import os
from pathlib import Path
import re
import stat
import uuid

from .adapters import BridgeError

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
        valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= 1
    except OverflowError:
        valid = False
    if not valid:
        raise BridgeError("invalid_request", "Volume must be between zero and one")


class VacuumTubeHook:
    def __init__(self, enabled=False, socket_path=None):
        runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
        self.socket_path = Path(socket_path) if socket_path else runtime / "bigscreen-bridge/vacuumtube.sock"
        self.enabled = enabled
        self.state = None
        self.content_types = list(KINDS) if enabled else []

    def _validate_socket(self):
        try:
            parent = self.socket_path.parent.lstat()
            info = self.socket_path.lstat()
            if (not stat.S_ISDIR(parent.st_mode) or not stat.S_ISSOCK(info.st_mode)
                    or info.st_uid != os.getuid() or parent.st_uid != os.getuid()
                    or (info.st_mode | parent.st_mode) & 0o077):
                raise BridgeError("not_ready", "VacuumTube control socket is not private")
        except OSError as exc:
            raise BridgeError("not_ready", "VacuumTube control endpoint is unavailable") from exc

    async def request(self, command, request_id=None, **kwargs):
        self._validate_socket()
        request_id = request_id or str(uuid.uuid4())
        writer = None
        try:
            async with asyncio.timeout(25 if command == "open" else 5):
                reader, writer = await asyncio.open_unix_connection(str(self.socket_path), limit=65536)
                writer.write(json.dumps({"id": request_id, "command": command, **kwargs}).encode() + b"\n")
                await writer.drain()
                response = json.loads(await reader.readline())
            if not isinstance(response, dict) or response.get("id") != request_id:
                raise ValueError("Mismatched response ID")
            if response.get("ok") is not True:
                raise BridgeError(response["error"]["code"], response["error"]["message"])
            if not isinstance(response["result"], dict):
                raise ValueError("Expected result object")
            return response["result"]
        except TimeoutError as exc:
            raise BridgeError("command_timeout", "VacuumTube did not acknowledge the command") from exc
        except OSError as exc:
            raise BridgeError("not_ready", "VacuumTube control endpoint is unavailable") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise BridgeError("invalid_response", "Invalid VacuumTube control response") from exc
        finally:
            if writer:
                writer.close()
                with contextlib.suppress(ConnectionError, TimeoutError):
                    await asyncio.wait_for(writer.wait_closed(), 1)

    async def refresh(self):
        try:
            state = await self.request("get_state")
            if (state.get("protocol") != 1 or not isinstance(state.get("ready"), bool)
                    or not isinstance(state.get("content_types"), list)
                    or not isinstance(state.get("capabilities"), list) or not isinstance(state.get("media"), dict)):
                raise BridgeError("invalid_response", "Unsupported VacuumTube control protocol")
            media = state["media"]
            video_id = media.get("video_id")
            if video_id:
                validate_content("video", video_id)
                media = {**media, "content_id": video_id, "type": "video",
                         "poster": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"}
            self.state = {**state, "media": media}
            self.content_types = [kind for kind in KINDS if kind in state["content_types"]]
            self.enabled = True
        except BridgeError:
            self.state = None

    async def open(self, request_id, kind, value):
        validate_content(kind, value)
        try:
            async with asyncio.timeout(20):
                await self.refresh()
                while not self.state or not self.state["ready"] or kind not in self.content_types:
                    await asyncio.sleep(0.2)
                    await self.refresh()
        except TimeoutError as exc:
            raise BridgeError("readiness_timeout", "YouTube Leanback did not become ready") from exc
        if kind not in self.content_types:
            raise BridgeError("unsupported", "The running VacuumTube does not support this navigation type")
        result = await self.request("open", request_id, content_type=kind, content_id=value)
        await self.refresh()
        return result

    async def control(self, request_id, command, **kwargs):
        await self.refresh()
        if not self.state or not self.state["ready"]:
            raise BridgeError("not_ready", "YouTube player is unavailable")
        if command not in ("play", "pause", "stop", "seek", "volume_set", "volume_mute") or command not in self.state["capabilities"]:
            raise BridgeError("unsupported", "YouTube does not support this player command")
        result = await self.request(command, request_id, **kwargs)
        await self.refresh()
        return result
