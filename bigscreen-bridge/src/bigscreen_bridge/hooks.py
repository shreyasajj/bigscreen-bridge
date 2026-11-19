"""The adapter contract every application hook shares, over two local transports.

An application exposes one request/response channel carrying strict JSON
(`{"id", "command", ...}` -> `{"id", "ok", "result" | "error"}`) and may emit a
`StateChanged` notification. Subclasses declare identity, content types and
validation; everything else lives here.
"""

import asyncio
import contextlib
import json
import os
import re
import stat
import uuid
from pathlib import Path
from typing import ClassVar

from dbus_next import Message, MessageType

from .adapters import BridgeError
from .diagnostics import NULL

BROWSE_PATH = re.compile(r"[a-z_]{1,32}")


def validate_page(offset=0, limit=50, path=None):
    if (
        type(offset) is not int
        or not 0 <= offset <= 1000000
        or type(limit) is not int
        or not 1 <= limit <= 50
    ):
        raise BridgeError("invalid_request", "Invalid page bounds")
    if path is not None and (not isinstance(path, str) or not BROWSE_PATH.fullmatch(path)):
        raise BridgeError("invalid_request", "Invalid library section")


class Hook:
    app = ""
    label = ""
    kinds = ()  # content types this bridge can validate
    timeouts: ClassVar[dict[str, float]] = {}  # command -> seconds; others use `timeout`
    timeout = 5
    timeout_code = "command_timeout"
    require_result_object = True

    def __init__(self, enabled=False):
        self.enabled = enabled
        self.state = None
        self.content_types = list(self.kinds) if enabled else []
        self.pushed = False  # the app has announced a change itself
        self.dirty = True  # a refresh is owed
        self.on_change = lambda: None
        self.recorder = NULL

    @property
    def polled(self):
        """Read on every pass even when nothing announced a change."""
        return not self.pushed

    @property
    def stale(self):
        """Apps that never announce changes have to be polled."""
        return self.dirty or self.polled

    def announce(self, received=True):
        """`received` is false for the bridge's own follow-up read, which is not a signal."""
        self.pushed = True
        self.dirty = True
        if received:
            self.recorder.hook_signal(self)
        self.on_change()

    async def subscribe(self):
        """Start listening for the app's own change notifications, if the transport has any."""

    async def transport(self, payload):
        """Send `payload` and return the app's raw reply; `request` sets the deadline."""
        raise NotImplementedError

    async def request(self, command, request_id=None, **kwargs):
        request_id = request_id or str(uuid.uuid4())
        payload = json.dumps({"id": request_id, "command": command, **kwargs})
        try:
            async with asyncio.timeout(self.timeouts.get(command, self.timeout)):
                raw = await self.transport(payload)
        except TimeoutError as exc:
            raise BridgeError(self.timeout_code, f"{self.label} did not acknowledge the command") from exc
        try:
            response = json.loads(raw)
            if not isinstance(response, dict) or response.get("id") != request_id:
                raise ValueError("Mismatched response ID")
            if response.get("ok") is not True:
                error = BridgeError(response["error"]["code"], response["error"]["message"])
                error.from_app = True  # its text is the app's payload; diagnostics keep only the code
                raise error
            if self.require_result_object and not isinstance(response["result"], dict):
                raise ValueError("Expected result object")
            return response["result"]
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise BridgeError("invalid_response", f"Invalid {self.label} control response") from exc

    def accept(self, state):
        """Validate a `get_state` result and return the state to keep."""
        if (
            not isinstance(state, dict)
            or state.get("protocol") != 1
            or not isinstance(state.get("content_types"), list)
        ):
            raise BridgeError("invalid_response", f"Unsupported {self.label} control protocol")
        return state

    async def refresh(self):
        self.dirty = False
        try:
            state = self.accept(await self.request("get_state"))
            self.state = state
            self.content_types = [kind for kind in self.kinds if kind in state["content_types"]]
            self.enabled = True
            self.recorder.hook_read(self)
        except BridgeError as exc:
            # A verified hook stays enabled so the bridge can cold-launch it again.
            self.state = None
            self.recorder.hook_read(self, exc)

    def is_ready(self):
        return bool(self.state and self.state.get("ready"))

    # Owns its deadline: a timeout becomes `readiness_timeout`.
    async def wait_ready(self, timeout=15, ready=None, message=None):  # noqa: ASYNC109
        ready = ready or self.is_ready
        try:
            async with asyncio.timeout(timeout):
                await self.refresh()
                while not ready():
                    await asyncio.sleep(0.2)
                    await self.refresh()
        except TimeoutError as exc:
            raise BridgeError("readiness_timeout", message or f"{self.label} did not become ready") from exc

    @property
    def browsable(self):
        """Whether the running app offers a library listing."""
        return bool(self.state and self.state.get("browse") is True)

    def check_item(self, kind, value):
        """Raise unless a listed item is something this hook would also agree to open."""

    async def browse(self, path=None, offset=0, limit=50):
        """One page of the app's library: openable items and sections to descend into."""
        validate_page(offset, limit, path)
        await self.refresh()
        if not self.is_ready():
            raise BridgeError("not_ready", f"Open {self.label} to browse its library")
        if not self.browsable:
            raise BridgeError("unsupported", f"The running {self.label} does not offer a library")
        arguments = {"offset": offset, "limit": limit, **({"path": path} if path else {})}
        result = await self.request("browse", **arguments)
        try:
            if not isinstance(result["items"], list) or len(result["items"]) > 50:
                raise ValueError("Invalid page")
            items = []
            for item in result["items"]:
                kind, section = item.get("content_type"), item.get("path")
                if (
                    not isinstance(item["id"], str)
                    or not 1 <= len(item["id"]) <= 2048
                    or not isinstance(item["name"], str)
                    or (kind is None) == (section is None)
                ):
                    raise ValueError("Invalid item")
                if kind is None:
                    validate_page(path=section)
                elif kind not in self.kinds:
                    raise ValueError("Unknown content type")
                else:
                    self.check_item(kind, item["id"])
                items.append(
                    {"id": item["id"], "name": item["name"][:512], "content_type": kind, "path": section}
                )
            next_offset = result.get("next_offset")
            if next_offset is not None and (
                type(next_offset) is not int or not offset < next_offset <= 1000000
            ):
                raise ValueError("Invalid page continuation")
        except (KeyError, TypeError, ValueError, AttributeError, BridgeError) as exc:
            raise BridgeError("invalid_response", f"Invalid {self.label} library page") from exc
        return {"items": items, "next_offset": next_offset}

    async def control(self, request_id, command, **kwargs):
        await self.refresh()
        if not self.is_ready():
            raise BridgeError("not_ready", f"{self.label} player is unavailable")
        if command not in self.state.get("capabilities", []):
            raise BridgeError("unsupported", f"{self.label} does not support this player command")
        result = await self.request(command, request_id, **kwargs)
        await self.refresh()
        return result


class DBusHook(Hook):
    """`Execute(s) -> s` on a session-bus name the sandboxed app owns."""

    bus_name = ""
    object_path = ""

    def __init__(self, bus, enabled=False):
        super().__init__(enabled)
        self.bus = bus

    async def transport(self, payload):
        reply = await self.bus.call(
            Message(
                destination=self.bus_name,
                path=self.object_path,
                interface=self.bus_name,
                member="Execute",
                signature="s",
                body=[payload],
            )
        )
        if reply.message_type == MessageType.ERROR:
            raise BridgeError("not_ready", f"{self.label} control endpoint is unavailable")
        return reply.body[0]

    async def subscribe(self):
        self.bus.add_message_handler(self._signal)
        rules = [
            f"type='signal',sender='{self.bus_name}',path='{self.object_path}',"
            f"interface='{self.bus_name}',member='StateChanged'",
            "type='signal',sender='org.freedesktop.DBus',interface='org.freedesktop.DBus',"
            f"member='NameOwnerChanged',arg0='{self.bus_name}'",
        ]
        for rule in rules:
            await self.bus.call(
                Message(
                    destination="org.freedesktop.DBus",
                    path="/org/freedesktop/DBus",
                    interface="org.freedesktop.DBus",
                    member="AddMatch",
                    signature="s",
                    body=[rule],
                )
            )

    def _signal(self, message):
        if message.message_type != MessageType.SIGNAL:
            return
        if (
            message.interface == self.bus_name
            and message.path == self.object_path
            and message.member == "StateChanged"
        ):
            self.announce()
        elif (
            message.sender == "org.freedesktop.DBus"
            and message.member == "NameOwnerChanged"
            and message.body
            and message.body[0] == self.bus_name
        ):
            # Appearing or vanishing is a change, but says nothing about later notifications.
            self.dirty = True
            self.on_change()


class SocketHook(Hook):
    """One bounded JSON line per connection on a user-private Unix socket."""

    socket_name = ""

    def __init__(self, enabled=False, socket_path=None):
        super().__init__(enabled)
        runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
        self.socket_path = (
            Path(socket_path) if socket_path else runtime / "bigscreen-bridge" / self.socket_name
        )

    def _validate_socket(self):
        try:
            parent = self.socket_path.parent.lstat()
            info = self.socket_path.lstat()
            if (
                not stat.S_ISDIR(parent.st_mode)
                or not stat.S_ISSOCK(info.st_mode)
                or info.st_uid != os.getuid()
                or parent.st_uid != os.getuid()
                or (info.st_mode | parent.st_mode) & 0o077
            ):
                raise BridgeError("not_ready", f"{self.label} control socket is not private")
        except OSError as exc:
            raise BridgeError("not_ready", f"{self.label} control endpoint is unavailable") from exc

    async def transport(self, payload):
        self._validate_socket()
        writer = None
        try:
            reader, writer = await asyncio.open_unix_connection(str(self.socket_path), limit=65536)
            writer.write(payload.encode() + b"\n")
            await writer.drain()
            return await reader.readline()
        except OSError as exc:
            raise BridgeError("not_ready", f"{self.label} control endpoint is unavailable") from exc
        finally:
            if writer:
                writer.close()
                with contextlib.suppress(ConnectionError, TimeoutError):
                    await asyncio.wait_for(writer.wait_closed(), 1)
