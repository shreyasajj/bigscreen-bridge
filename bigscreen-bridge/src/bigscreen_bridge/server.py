"""Authenticated local bridge for KDE app lifecycle and verified media controls."""
import argparse
import asyncio
from collections import OrderedDict
import contextlib
import copy
from dataclasses import dataclass
import hmac
import json
import logging
from pathlib import Path
import socket
import signal

from aiohttp import web
from dbus_next import BusType
from dbus_next.aio import MessageBus

from .audio import AudioManager
from .mpris import MprisRegistry
from .power import PowerManager
from .sessions import SessionController

from .config import load_config
from .deep_links import parse_uri
from .adapters import APPS, BridgeError
from .kwin import KWin
from .couchside import CouchsideHook, KINDS, validate_content
from .vacuumtube import VacuumTubeHook, validate_content as validate_youtube, validate_volume
from .flimmer import FlimmerHook, validate_channel, validate_page
from .stremio import StremioHook, validate_content as validate_stremio, validate_seek

LOG = logging.getLogger(__name__)


async def process(*argv):
    proc = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE,
                                               stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 15)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    if proc.returncode:
        raise BridgeError("launch_failed", "Application command failed")
    return out.decode().strip()


async def launch_app(*argv):
    proc = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.DEVNULL,
                                               stderr=asyncio.subprocess.DEVNULL)
    if await proc.wait():
        raise BridgeError("launch_failed", "Application exited unsuccessfully")


FAST_POLL = 2     # seconds between checks of hooks that cannot announce changes
SLOW_POLL = 10    # seconds between full refreshes, including system state
PLAYER_COMMANDS = ("play", "pause", "stop", "seek", "volume_set", "volume_mute")


def _open_couchside(request):
    return validate_content(request.get("content_type"), request.get("content_id"))


def _open_stremio(request):
    resume = request.get("resume", False)
    return (*validate_stremio(request.get("content_type"), request.get("content_id"), resume), resume)


def _open_flimmer(request):
    if request.get("content_type") != "channel":
        raise BridgeError("unsupported", "Flimmer supports channel IDs without resume")
    return (validate_channel(request.get("content_id")),)


def _open_youtube(request):
    return validate_youtube(request.get("content_type"), request.get("content_id"))


@dataclass(frozen=True)
class Adapter:
    """What the bridge needs to know about one application hook; the rest is shared."""
    app: str
    prefix: str                     # Home Assistant media content type prefix
    kinds: tuple                    # content types assumed until the app reports its own
    parse_open: callable            # validated request -> arguments for `deliver`
    deliver: str = "open"           # hook coroutine that performs the navigation
    commands: tuple = ()            # player commands the app may negotiate
    resume: bool = False
    owner: str | None = None        # an external playback owner; never add transport controls

    def content_types(self, hook):
        return [self.prefix + kind for kind in getattr(hook, "content_types", self.kinds)] if hook.enabled else []


ADAPTERS = {adapter.app: adapter for adapter in (
    Adapter("couchside", "couchside_", KINDS, _open_couchside, deliver="navigate", owner="music_assistant"),
    Adapter("stremio", "stremio_", (), _open_stremio, commands=("play", "pause", "stop", "seek"), resume=True),
    Adapter("flimmer", "flimmer_", ("channel",), _open_flimmer, commands=("play", "pause", "stop")),
    Adapter("vacuumtube", "youtube_", (), _open_youtube, commands=PLAYER_COMMANDS),
)}


class Bridge:
    def __init__(self, config, wm, launcher=launch_app):
        self.config = config
        self.wm = wm
        self.launcher = launcher
        self.installed = set()
        self.clients = set()
        self.lock = asyncio.Lock()
        self.launch_tasks = {}
        self.couchside = None
        self.stremio = None
        self.flimmer = None
        self.vacuumtube = None
        self.power = None
        self.audio = None
        self.sessions = None

    def hook(self, app_id, enabled=True):
        hook = getattr(self, app_id, None) if app_id in ADAPTERS else None
        return hook if hook and (hook.enabled or not enabled) else None

    def _describe(self, app, adapter, hook):
        """Add what an application hook reports to its snapshot entry."""
        state = hook.state or {}
        app["content_types"] = adapter.content_types(hook)
        if adapter.owner:
            app["playback_owner"] = adapter.owner
            if state:
                app["ma_player_id"] = state.get("ma_player_id")
                app["view"] = state.get("view")
            return
        app["hook_installed"] = hook.enabled
        app["control_available"] = bool(state.get("ready"))
        if not app["control_available"]:
            return
        media = state["media"]
        app["media"] = media
        app["state"] = media.get("state", "idle")
        if "view" in state:
            app["view"] = state["view"]
        negotiated = state.get("capabilities", [])
        # Volume belongs to the app; transport only exists while something is loaded.
        loaded = app["state"] in ("playing", "paused", "buffering")
        app["capabilities"] += [c for c in adapter.commands if c in negotiated
                                and (loaded or c.startswith("volume_"))
                                and (c != "seek" or media.get("duration", 0) > 0)]

    def snapshot(self):
        active = next((app.id for app in APPS for w in self.wm.windows
                       if w.get("active") and app.matches(w)), None)
        snapshot = {"type": "state", "protocol": 1, "device_id": self.config["device_id"],
                "name": self.config.get("name", socket.gethostname()), "active_app": active,
                "available": self.wm.ready.is_set(),
                "apps": [{"id": a.id, "name": a.name, "installed": a.flatpak in self.installed,
                          "running": any(a.matches(w) for w in self.wm.windows),
                          "capabilities": ["focus"] if a.flatpak in self.installed else [],
                          "state": "idle" if any(a.matches(w) for w in self.wm.windows) else "off"}
                         for a in APPS]}
        for app in snapshot["apps"]:
            hook = self.hook(app["id"], enabled=False)
            if hook:
                self._describe(app, ADAPTERS[app["id"]], hook)
        if self.power:
            snapshot['power'] = self.power.state
        if self.audio:
            snapshot['audio'] = self.audio.state
        if self.sessions:
            snapshot['sessions'] = list(self.sessions.players.values())
            snapshot['paused_by_bridge'] = list(self.sessions.receipts)
        return copy.deepcopy(snapshot)

    async def notify(self):
        if self.clients:
            await asyncio.gather(*(self.send(c, self.snapshot()) for c in list(self.clients)))

    async def send(self, client, data):
        try:
            async with asyncio.timeout(5):
                await client.send_json(data)
        except (ConnectionError, RuntimeError, TimeoutError):
            self.clients.discard(client)
            await client.close()

    async def focus(self, app_id):
        app = next((a for a in APPS if a.id == app_id), None)
        if not app:
            raise BridgeError("unknown_app", "Unknown application")
        if app.flatpak not in self.installed:
            raise BridgeError("not_installed", "Application is not installed")
        if not self.wm.ready.is_set():
            raise BridgeError("window_manager_unavailable", "No authoritative window snapshot")
        async with self.lock:
            return await self._focus(app)

    async def _focus(self, app):
        # Compositor closing animations can outlive a process; query live windows before launch decisions.
        await self.wm.refresh()
        hook = self.hook(app.id)
        if hook:
            await hook.refresh()
        # The IPC name disappears before KWin finishes animating a closing window.
        # A patched app's D-Bus name lock prevents another UI during a repeated launch.
        need_control = bool(hook and hook.state is None)
        windows = [w for w in self.wm.windows if app.matches(w)]
        if not windows or need_control:
            # Keep the subprocess reaped without waiting for the GUI process to exit.
            task = self.launch_tasks.get(app.id)
            if task is None or task.done() or need_control:
                running = (await process("flatpak", "ps", "--columns=application")).splitlines()
                if app.flatpak in running and not hook:
                    raise BridgeError("readiness_timeout", "App is running without a controllable window; no duplicate launched")
                LOG.info("Launching %s; missing window=%s, missing control=%s", app.id, not windows, need_control)
                task = asyncio.create_task(self.launcher("flatpak", "run", app.flatpak))
                self.launch_tasks[app.id] = task
                # Retrieve errors even when the application exits after readiness.
                task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
            if need_control:
                windows = []
            try:
                async with asyncio.timeout(25):
                    while not windows:
                        if task.done():
                            task.result()  # surface immediate launch failure
                        await asyncio.sleep(0.1)
                        if need_control:
                            await hook.refresh()
                        windows = [w for w in self.wm.windows if app.matches(w)]
                        if need_control and (not hook.state or not hook.state.get("ready")):
                            windows = []
            except TimeoutError as exc:
                raise BridgeError("readiness_timeout", "Application did not acquire a window") from exc
        window = next((w for w in windows if w.get("active")), windows[0])
        await self.wm.focus(window["id"])
        return {"app": app.id}

    async def open_content(self, request):
        """Validate everything first, so unsupported content never launches or focuses an app."""
        adapter = ADAPTERS.get(request.get("app"))
        hook = self.hook(request.get("app"))
        if not adapter or not hook:
            raise BridgeError("unsupported", "No verified content hook for this application")
        if "resume" in request and not adapter.resume:
            raise BridgeError("unsupported", "Resume is supported only by Stremio")
        arguments = adapter.parse_open(request)
        if adapter.prefix + request["content_type"] not in adapter.content_types(hook):
            raise BridgeError("unsupported", "The application does not advertise this navigation type")
        app = next(a for a in APPS if a.id == adapter.app)
        if app.flatpak not in self.installed:
            raise BridgeError("not_installed", "Application is not installed")
        if not self.wm.ready.is_set():
            raise BridgeError("window_manager_unavailable", "No authoritative window snapshot")
        async with self.lock:
            await self._focus(app)
            result = await getattr(hook, adapter.deliver)(request["id"], *arguments)
            # UI navigation cannot bypass Wayland's focus policy; KWin confirms it.
            await self._focus(app)
            await self.notify()
            if adapter.owner:
                return {"app": adapter.app, "view": result["view"], "playback_owner": adapter.owner}
            return {"app": adapter.app, **result}

    async def player_command(self, request):
        if self.sessions:
            self.sessions.invalidate_app(request.get("app"))
        adapter = ADAPTERS.get(request.get("app"))
        hook = self.hook(request.get("app"))
        if not adapter or not hook or not adapter.commands:
            raise BridgeError("unsupported", "No verified playback controller for this application")
        command = request["command"]
        if command not in adapter.commands:
            raise BridgeError("unsupported", "This application does not support that player command")
        kwargs = {}
        if command == "seek":
            validate_seek(request.get("position"))
            kwargs["position"] = request["position"]
        if command == "volume_set":
            validate_volume(request.get("volume_level"))
            kwargs["volume_level"] = request["volume_level"]
        if command == "volume_mute":
            if not isinstance(request.get("muted"), bool):
                raise BridgeError("invalid_request", "Muted must be boolean")
            kwargs["muted"] = request["muted"]
        async with self.lock:
            result = await hook.control(request["id"], command, **kwargs)
            await self.notify()
            return result

    async def execute(self, request):
        command = request.get("command")
        global_media = ('pause_active', 'pause_all', 'resume_active', 'resume_previously_paused', 'stop_active', 'stop_all')
        local_power = ('screen_off', 'screen_on', 'lock_screen', 'get_power_state')
        system_audio = ('mute', 'unmute', 'set_volume')
        if command in global_media + local_power + system_audio:
            allowed = {'id', 'command', 'volume_level'} if command == 'set_volume' else {'id', 'command'}
            if request.keys() - allowed:
                raise BridgeError('invalid_request', 'Unexpected command arguments')
            if command in global_media and self.sessions:
                result = await self.sessions.execute(command)
            elif command in local_power and self.power:
                if command == 'get_power_state':
                    result = await self.power.refresh()
                elif command == 'lock_screen':
                    result = await self.power.lock()
                else:
                    result = await self.power.display(command == 'screen_on')
            elif command in system_audio and self.audio:
                result = await self.audio.execute(command, request.get('volume_level'))
            else:
                raise BridgeError('unsupported', 'The requested system module is unavailable')
            await self.notify()
            return result
        if command not in ("get_state", "browse", "focus", "open", "open_uri", "play", "pause", "stop", "seek", "volume_set", "volume_mute"):
            raise BridgeError("unsupported", "Command is not supported")
        allowed = {"id", "command", "app"} if command == "focus" else {"id", "command"}
        if command == "open_uri":
            allowed = {"id", "command", "uri", "app"}
        if command == "browse":
            allowed = {"id", "command", "app", "offset", "limit"}
        if command == "open":
            allowed = {"id", "command", "app", "content_type", "content_id", "resume"}
        if command in ("play", "pause", "stop", "seek", "volume_set", "volume_mute"):
            arguments = {"seek": {"position"}, "volume_set": {"volume_level"}, "volume_mute": {"muted"}}
            allowed = {"id", "command", "app"} | arguments.get(command, set())
        if request.keys() - allowed:
            raise BridgeError("unsupported", "Content and additional arguments are not supported yet")
        if command == "open_uri":
            resolved = parse_uri(request.get("uri"))
            if "app" in request and request["app"] != resolved["app"]:
                raise BridgeError("unsupported", "This URI targets a different app player")
            return await self.execute({"id": request["id"], **resolved})
        if command in ("play", "pause", "stop", "seek", "volume_set", "volume_mute"):
            return await self.player_command(request)
        if command == "open":
            return await self.open_content(request)
        if command == "focus":
            return await self.focus(request.get("app"))
        if command == "browse":
            if "app" in request:
                if request["app"] != "flimmer" or not self.flimmer or not self.flimmer.enabled:
                    raise BridgeError("unsupported", "No channel catalogue for this application")
                offset, limit = request.get("offset", 0), request.get("limit", 50)
                validate_page(offset, limit)
                return await self.flimmer.browse(offset, limit)
            if "offset" in request or "limit" in request:
                raise BridgeError("invalid_request", "Pagination requires an application catalogue")
            return {"items": [{"id": a.id, "title": a.name, "media_content_type": "app"}
                              for a in APPS if a.flatpak in self.installed]}
        return self.snapshot()

    async def websocket(self, request):
        supplied = request.headers.get("Authorization", "")
        expected = "Bearer " + self.config["token"]
        if request.headers.get("Origin") or not hmac.compare_digest(supplied.encode(), expected.encode()):
            raise web.HTTPUnauthorized()
        ws = web.WebSocketResponse(max_msg_size=16384, heartbeat=20)
        await ws.prepare(request)
        self.clients.add(ws)
        cache = OrderedDict()
        await self.send(ws, self.snapshot())
        try:
            async for msg in ws:
                if msg.type != web.WSMsgType.TEXT:
                    break
                request_id = None
                try:
                    data = json.loads(msg.data)
                    if not isinstance(data, dict):
                        raise BridgeError("invalid_request", "Expected an object")
                    request_id = data.get("id")
                    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
                        request_id = None
                        raise BridgeError("invalid_request", "A request ID is required")
                    if request_id in cache:
                        original, response = cache[request_id]
                        if data != original:
                            raise BridgeError("invalid_request", "Request ID reused with different data")
                        await self.send(ws, response)
                        continue
                    result = await self.execute(data)
                    response = {"type": "result", "id": request_id, "ok": True, "result": result}
                except (json.JSONDecodeError, BridgeError) as exc:
                    response = {"type": "result", "id": request_id, "ok": False,
                                "error": {"code": getattr(exc, "code", "invalid_request"),
                                          "message": str(exc)}}
                except Exception:
                    LOG.exception("Request failed")
                    response = {"type": "result", "id": request_id, "ok": False,
                                "error": {"code": "internal_error", "message": "Bridge operation failed"}}
                if request_id and request_id not in cache:
                    cache[request_id] = (data, response)
                    if len(cache) > 128:
                        cache.popitem(last=False)
                await self.send(ws, response)
        finally:
            self.clients.discard(ws)
        return ws


def application(bridge):
    app = web.Application(client_max_size=16384)
    app.router.add_get("/ws", bridge.websocket)

    async def health(request):
        return web.json_response({"ok": bridge.wm.ready.is_set(), "protocol": 1})

    app.router.add_get("/health", health)
    return app


async def serve(config_path):
    config = load_config(config_path)
    monitor = Path(config["monitor_path"])
    wm = KWin(monitor)
    bridge = Bridge(config, wm)
    notification = None

    def changed():
        nonlocal notification
        if notification is None or notification.done():
            notification = asyncio.create_task(bridge.notify())

    wm.changed = changed
    runner = None
    hook_poll = None
    system_bus = None
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopping.set)
    try:
        bridge.installed = set((await process("flatpak", "list", "--app", "--columns=application")).splitlines())
        await wm.start()
        bridge.couchside = CouchsideHook(wm.bus, config.get("couchside_control", False))
        bridge.stremio = StremioHook(wm.bus, config.get("stremio_control", False))
        bridge.flimmer = FlimmerHook(wm.bus, config.get("flimmer_control", False))
        bridge.vacuumtube = VacuumTubeHook(config.get("vacuumtube_control", False))
        hooks = [getattr(bridge, app) for app in ADAPTERS]

        system_bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        bridge.power = PowerManager(wm.bus, system_bus, config.get('power', {}), changed=changed)
        bridge.audio = AudioManager()
        mpris = MprisRegistry(wm.bus)
        bridge.sessions = SessionController(bridge, mpris)
        mpris.changed = bridge.sessions.transition

        # Applications and MPRIS players announce their own changes; the timer is only a
        # safety net, plus the sole source for hooks that never announce anything.
        wake = asyncio.Event()
        mpris.on_change = wake.set
        for hook in hooks:
            hook.on_change = wake.set
            await hook.subscribe()
        await asyncio.gather(bridge.power.initialize(), bridge.audio.refresh(), mpris.initialize())

        async def observe():
            slow_at = 0
            while True:
                now = loop.time()
                slow = now >= slow_at
                previous = bridge.snapshot()
                jobs = [bridge.sessions.observe(full=slow)]
                if slow or bridge.couchside.stale:
                    jobs.append(bridge.couchside.refresh())
                if slow:
                    # Display, logind and PipeWire state need subprocesses; keep them off the fast path.
                    slow_at = now + SLOW_POLL
                    jobs += [bridge.power.refresh(), bridge.audio.refresh()]
                results = await asyncio.gather(*jobs, return_exceptions=True)
                for failure in (r for r in results if isinstance(r, Exception)):
                    LOG.warning("State refresh failed: %r", failure)
                if previous != bridge.snapshot():
                    await bridge.notify()
                wake.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(wake.wait(), FAST_POLL)
                await asyncio.sleep(0.05)  # coalesce bursts of notifications

        await asyncio.gather(*(hook.refresh() for hook in hooks))
        hook_poll = asyncio.create_task(observe())
        runner = web.AppRunner(application(bridge))
        await runner.setup()
        site = web.TCPSite(runner, config.get("host", "127.0.0.1"), config.get("port", 8765))
        await site.start()
        lost = asyncio.create_task(wm.lost.wait())
        disconnected = asyncio.create_task(wm.bus.wait_for_disconnect())
        shutdown = asyncio.create_task(stopping.wait())
        try:
            done, _ = await asyncio.wait((lost, disconnected, shutdown), return_when=asyncio.FIRST_COMPLETED)
            if shutdown not in done:
                raise RuntimeError("KWin or session bus disappeared; restart required")
        finally:
            for task in (lost, disconnected, shutdown):
                task.cancel()
            await asyncio.gather(lost, disconnected, shutdown, return_exceptions=True)
    finally:
        if hook_poll:
            hook_poll.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await hook_poll
        if runner:
            for client in list(bridge.clients):
                await client.close(code=1001, message=b"Bridge stopping")
            await runner.cleanup()
        if notification:
            notification.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await notification
        if system_bus:
            system_bus.disconnect()
        await wm.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(serve(args.config))
    except KeyboardInterrupt:
        pass
