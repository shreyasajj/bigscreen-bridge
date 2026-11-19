"""Session-bus receiver plus narrowly scoped Plasma 6 activation scripts."""
import asyncio
import json
import logging
import tempfile
import uuid
from pathlib import Path

from dbus_next import Message, MessageType
from dbus_next.aio import MessageBus
from dbus_next.constants import NameFlag, RequestNameReply
from dbus_next.service import ServiceInterface, method

from .adapters import BridgeError
from .diagnostics import NULL

LOG = logging.getLogger(__name__)
MONITOR_ATTEMPTS = 3   # loads of the window monitor before the bridge gives up and restarts


class Receiver(ServiceInterface):
    def __init__(self, manager):
        super().__init__("org.bigscreen.Bridge")
        self.manager = manager

    @method()
    def ReportWindows(self, payload: 's'):
        try:
            windows = json.loads(payload)
            if not isinstance(windows, list) or len(windows) > 512:
                return
            if not all(isinstance(w, dict) and isinstance(w.get("id"), str) for w in windows):
                return
        except (ValueError, TypeError):
            return
        self.manager.windows = windows
        self.manager.ready.set()
        self.manager.recorder.kwin_report()
        self.manager.changed()

    @method()
    def RefreshWindows(self, payload: 's', request_id: 's'):
        self.ReportWindows(payload)
        future = self.manager.pending.get(request_id)
        if future and not future.done():
            future.set_result(True)

    @method()
    def FocusResult(self, request_id: 's', found: 'b'):
        future = self.manager.pending.get(request_id)
        if future and not future.done():
            future.set_result(found)


class KWin:
    def __init__(self, monitor: Path, changed=lambda: None):
        self.monitor = monitor
        self.changed = changed
        self.windows = []
        self.ready = asyncio.Event()
        self.lost = asyncio.Event()
        self.pending = {}
        self.bus = None
        self.scripting = None
        self.monitor_name = "bigscreen-bridge-monitor"
        self.recorder = NULL

    async def start(self):
        self.bus = await MessageBus().connect()
        reply = await self.bus.request_name("org.bigscreen.Bridge", NameFlag.DO_NOT_QUEUE)
        if reply != RequestNameReply.PRIMARY_OWNER:
            raise RuntimeError("Another bridge owns the session bus name")
        self.bus.export("/org/bigscreen/Bridge", Receiver(self))
        dbus_intro = await self.bus.introspect("org.freedesktop.DBus", "/org/freedesktop/DBus")
        dbus = self.bus.get_proxy_object("org.freedesktop.DBus", "/org/freedesktop/DBus", dbus_intro)

        def owner_changed(name, old, new):
            if name == "org.kde.KWin" and old:
                self.ready.clear()
                self.windows = []
                self.lost.set()
                self.changed()

        dbus.get_interface("org.freedesktop.DBus").on_name_owner_changed(owner_changed)
        introspection = await self.bus.introspect("org.kde.KWin", "/Scripting")
        self.scripting = self.bus.get_proxy_object("org.kde.KWin", "/Scripting", introspection).get_interface(
            "org.kde.kwin.Scripting")
        for attempt in range(1, MONITOR_ATTEMPTS + 1):
            await self.scripting.call_unload_script(self.monitor_name)
            await self.run(self.monitor, self.monitor_name)
            try:
                await asyncio.wait_for(self.ready.wait(), 10)
                return
            except TimeoutError:
                if attempt == MONITOR_ATTEMPTS:
                    raise
                LOG.warning("KWin has not reported its windows; loading the monitor again (%d of %d)",
                            attempt + 1, MONITOR_ATTEMPTS)

    async def run(self, path: Path, name: str):
        # Qt exposes overloaded loadScript methods; select the two-string signature explicitly.
        reply = await self.bus.call(Message(destination="org.kde.KWin", path="/Scripting",
                                            interface="org.kde.kwin.Scripting", member="loadScript",
                                            signature="ss", body=[str(path), name]))
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(f"KWin loadScript failed: {reply.error_name}")
        if reply.body[0] < 0:
            raise RuntimeError("KWin refused script")
        # KWin numbers a script by how many scripts are loaded, so after an unload a new
        # script can get the number, and the /Scripting/ScriptN path, of one still running;
        # calling run on that path starts the wrong script. start() runs every loaded script
        # that is not running yet, whatever its number.
        reply = await self.bus.call(Message(destination="org.kde.KWin", path="/Scripting",
                                            interface="org.kde.kwin.Scripting", member="start"))
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(f"KWin start failed: {reply.error_name}")

    async def refresh(self):
        request_id = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        name = "bigscreen-bridge-refresh-" + request_id
        code = f'''
const active = workspace.activeWindow;
const windows = workspace.windowList().filter(w => w.normalWindow && !w.deleted).map(w => ({{
    id: String(w.internalId), desktop: String(w.desktopFileName),
    resource: String(w.resourceName), class: String(w.resourceClass), active: w === active
}}));
callDBus("org.bigscreen.Bridge", "/org/bigscreen/Bridge", "org.bigscreen.Bridge",
         "RefreshWindows", JSON.stringify(windows), {json.dumps(request_id)});
'''
        try:
            with tempfile.TemporaryDirectory(prefix="bigscreen-kwin-") as directory:
                path = Path(directory) / "refresh.js"
                path.write_text(code)
                await self.run(path, name)
                await asyncio.wait_for(future, 5)
        except TimeoutError as exc:
            raise BridgeError("window_manager_unavailable", "KWin did not return a fresh window snapshot") from exc
        finally:
            self.pending.pop(request_id, None)
            await self.scripting.call_unload_script(name)

    async def focus(self, window_id: str):
        request_id = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        name = "bigscreen-bridge-focus-" + request_id
        code = f'''
const target = workspace.windowList().find(w => !w.deleted && String(w.internalId) === {json.dumps(window_id)});
if (target) {{
    target.minimized = false;
    workspace.activeWindow = target;
}}
callDBus("org.bigscreen.Bridge", "/org/bigscreen/Bridge", "org.bigscreen.Bridge",
         "FocusResult", {json.dumps(request_id)}, Boolean(target));
'''
        try:
            with tempfile.TemporaryDirectory(prefix="bigscreen-kwin-") as directory:
                path = Path(directory) / "focus.js"
                path.write_text(code)
                await self.run(path, name)
                if not await asyncio.wait_for(future, 5):
                    raise BridgeError("focus_failed", "Window disappeared before activation")
                async with asyncio.timeout(5):
                    while not any(w["id"] == window_id and w.get("active") for w in self.windows):
                        await asyncio.sleep(0.05)
        except TimeoutError as exc:
            raise BridgeError("focus_failed", "KWin did not confirm window activation") from exc
        finally:
            self.pending.pop(request_id, None)
            await self.scripting.call_unload_script(name)

    async def close(self):
        if self.scripting:
            await self.scripting.call_unload_script(self.monitor_name)
        if self.bus:
            self.bus.disconnect()
