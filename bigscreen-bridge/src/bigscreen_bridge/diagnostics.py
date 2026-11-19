"""Bounded, privacy-safe diagnostics about the bridge itself.

Other modules call the small `Recorder` hooks below when something happens; the
fields that are already in memory (clients, windows, hook flags, MPRIS players) are
read live by `Recorder.to_dict()`. Every hook swallows its own failures, so
recording can never raise into the code it observes.

Never recorded: the token, MQTT credentials, content IDs, URIs, search text,
volume levels, window captions or app payloads. Error messages are the bridge's own
fixed strings; text an application sent back is replaced by a fixed one.
"""
import functools
import importlib.metadata
import logging
import os
import re
import time
from collections import OrderedDict, deque
from datetime import UTC, datetime

from .adapters import APPS

LOG = logging.getLogger(__name__)

SCHEMA = 1
COMMAND_LIMIT = 50
WARNING_LIMIT = 50
MESSAGE_LIMIT = 200
ID_LIMIT = 128
NEVER_SIGNALLED_AFTER = 30      # seconds a reachable hook may stay silent before it is logged
BURST_FAILURES, BURST_WINDOW, BURST_QUIET = 5, 15, 60
COMMANDS = frozenset((
    "get_state", "diagnostics", "browse", "focus", "open", "open_uri", "play", "pause", "stop", "seek",
    "volume_set", "volume_mute", "pause_active", "pause_all", "resume_active", "resume_previously_paused",
    "stop_active", "stop_all", "screen_off", "screen_on", "lock_screen", "get_power_state",
    "mute", "unmute", "set_volume", "ledfx_pause", "ledfx_light", "ledfx_scene", "hyperhdr_light"))
APP_IDS = frozenset(app.id for app in APPS)
CODE = re.compile(r"[a-z][a-z0-9_]{0,47}")
PLAIN = re.compile(r"[^\x00-\x1f\x7f]*")


def utc(moment):
    """UTC ISO 8601 with milliseconds and a Z suffix, like `media.updated_at`."""
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def package_version():
    try:
        return importlib.metadata.version("bigscreen-bridge")
    except importlib.metadata.PackageNotFoundError:
        return None


def safe(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:  # diagnostics must never break what they observe
            LOG.debug("Diagnostics recording failed", exc_info=True)
            return None
    return wrapper


def error_code(code):
    return code if isinstance(code, str) and CODE.fullmatch(code) else "error"


def endpoint(hook):
    if getattr(hook, "bus_name", ""):
        return "dbus", hook.bus_name
    path = getattr(hook, "socket_path", None)
    if path is None:
        return None, None
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    text = str(path)
    if text.startswith(runtime.rstrip("/") + "/"):
        text = "$XDG_RUNTIME_DIR" + text[len(runtime.rstrip("/")):]
    return "socket", text


def close_reason(ws, unexpected=False):
    """Why a WebSocket ended, from aiohttp's own bookkeeping."""
    if isinstance(ws.exception(), TimeoutError):
        return "heartbeat_timeout"   # aiohttp closes with 1006 and records the missing PONG
    if unexpected:
        return "unexpected_message"
    if ws.close_code in (1000, 1001):
        return "normal"
    return "connection_lost"


class Null:
    """Stand-in for objects created without a recorder (tests, fixtures)."""
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


NULL = Null()


class WarningBuffer(logging.Handler):
    """The last log records of `bigscreen_bridge.*` at INFO and above, repeats folded."""

    def __init__(self, recorder, limit=WARNING_LIMIT):
        super().__init__(logging.INFO)
        self.recorder, self.limit = recorder, limit
        self.rows = OrderedDict()

    def emit(self, record):
        try:
            # Only the formatted message: tracebacks and exception text can carry payloads.
            message = self.recorder.scrub(record.getMessage())
            key = (record.levelname, record.name, message)
            row = self.rows.pop(key, None) or {"at": None, "level": record.levelname, "logger": record.name,
                                               "message": message, "count": 0}
            row["count"] += 1
            row["at"] = utc(datetime.fromtimestamp(record.created, UTC))
            self.rows[key] = row
            while len(self.rows) > self.limit:
                self.rows.popitem(last=False)
            self.recorder.changed()
        except Exception:  # a logging handler must not raise or print its own errors
            pass


class Recorder:
    def __init__(self, clock=None, monotonic=time.monotonic):
        self.clock = clock or (lambda: datetime.now(UTC))
        self.monotonic = monotonic
        self.listeners = []
        self.secrets = set()
        self.service = {"version": package_version(), "started_at": None, "port": None}
        self.websocket = {"last_connected_at": None, "last_disconnected_at": None, "last_disconnect_reason": None}
        self.discovery = {"enabled": False, "announced": False, "error": None, "service_type": None,
                          "instance": None}
        self.mqtt = {"configured": False, "connected": False, "broker": None, "tls": None,
                     "commands_enabled": None, "last_connected_at": None, "last_error": None, "failures": 0,
                     "retry_seconds": None}
        self.kwin = {"last_report_at": None}
        self.hooks = {}
        self.commands = deque(maxlen=COMMAND_LIMIT)
        self.bursts = OrderedDict()     # client -> recent failure times and when it was last logged
        self.handler = WarningBuffer(self)

    # -- plumbing -------------------------------------------------------------

    def now(self):
        return utc(self.clock())

    def changed(self):
        for listener in list(self.listeners):
            try:
                listener()
            except Exception:
                LOG.debug("Diagnostics listener failed", exc_info=True)

    def install(self, logger="bigscreen_bridge"):
        logger = logging.getLogger(logger)
        self._level = logger.level
        if logger.getEffectiveLevel() > logging.INFO:
            logger.setLevel(logging.INFO)   # INFO lines must reach the buffer, not only warnings
        logger.addHandler(self.handler)

    def uninstall(self, logger="bigscreen_bridge"):
        logger = logging.getLogger(logger)
        logger.removeHandler(self.handler)
        logger.setLevel(getattr(self, "_level", logging.NOTSET))

    @safe
    def protect(self, *values):
        """Values that must never appear, even if a message somehow contained them."""
        self.secrets.update(v for v in values if isinstance(v, str) and len(v) >= 4)

    def scrub(self, text):
        text = str(text)
        for secret in self.secrets:
            text = text.replace(secret, "***")
        return "".join(PLAIN.findall(text))[:MESSAGE_LIMIT]

    # -- service, transports and KWin ----------------------------------------

    @safe
    def started(self, port):
        self.service.update(started_at=self.now(), port=port)
        self.changed()

    @safe
    def ws_connected(self):
        self.websocket["last_connected_at"] = self.now()
        self.changed()

    @safe
    def ws_disconnected(self, reason):
        self.websocket.update(last_disconnected_at=self.now(), last_disconnect_reason=error_code(reason))
        self.changed()

    @safe
    def discovery_result(self, record, service_type, error=None):
        self.discovery = {"enabled": record is not None, "announced": record is not None and error is None,
                          "error": self.scrub(error) if error is not None else None,
                          "service_type": service_type if record is not None else None,
                          "instance": self.scrub(record["instance"]) if record is not None else None}
        self.changed()

    @safe
    def mqtt_configured(self, options, retry):
        self.protect(options.get("username"), options.get("password"))
        self.mqtt.update(configured=True, broker=f"{options['host']}:{options['port']}", tls=bool(options["tls"]),
                         commands_enabled=bool(options["commands"]), retry_seconds=retry)
        self.changed()

    @safe
    def mqtt_connected(self):
        self.mqtt.update(connected=True, last_connected_at=self.now(), failures=0)
        self.changed()

    @safe
    def mqtt_disconnected(self, exc=None):
        self.mqtt["connected"] = False
        if exc is not None:
            # The class name only: broker messages can quote credentials or topics.
            self.mqtt["last_error"] = type(exc).__name__
            self.mqtt["failures"] += 1
        self.changed()

    @safe
    def kwin_report(self):
        self.kwin["last_report_at"] = self.now()
        self.changed()

    # -- application hooks ---------------------------------------------------

    def _hook(self, hook):
        return self.hooks.setdefault(hook.app, {
            "signals_received": 0, "last_signal_at": None, "last_read_at": None, "last_read_ok": None,
            "consecutive_failures": 0, "last_error": None, "catalogue_loading_since": None, "channels": None,
            "_reachable_since": None, "_silence_logged": False, "_outage_logged": False})

    def _error(self, hook, exc):
        # Text an application sent back is its payload, not the bridge's; keep only the code.
        message = (f"{hook.label} reported an error" if getattr(exc, "from_app", False)
                   else self.scrub(str(exc)))
        return {"code": error_code(getattr(exc, "code", None)), "message": message, "at": self.now()}

    @safe
    def hook_signal(self, hook):
        row = self._hook(hook)
        row["signals_received"] += 1
        row["last_signal_at"] = self.now()
        self.changed()

    @safe
    def hook_read(self, hook, exc=None):
        row = self._hook(hook)
        row["last_read_at"] = self.now()
        if exc is not None:
            row.update(last_read_ok=False, last_error=self._error(hook, exc), _reachable_since=None,
                       _silence_logged=False, consecutive_failures=row["consecutive_failures"] + 1)
            row["catalogue_loading_since"] = None
            # A socket that stops answering while the app's MPRIS player is still there is a real fault;
            # one that is simply not running is not worth a line.
            if getattr(hook, "socket_path", None) and getattr(hook, "pushed", False) and not row["_outage_logged"]:
                row["_outage_logged"] = True
                LOG.warning("%s control socket unavailable (%s)", hook.label, row["last_error"]["code"])
            self.changed()
            return
        state = hook.state if isinstance(hook.state, dict) else {}
        row.update(last_read_ok=True, consecutive_failures=0, _outage_logged=False)
        now = self.monotonic()
        if row["_reachable_since"] is None:
            row["_reachable_since"] = now
        if getattr(hook, "pushed", True):
            row["_silence_logged"] = False
        elif not row["_silence_logged"] and now - row["_reachable_since"] >= NEVER_SIGNALLED_AFTER:
            row["_silence_logged"] = True
            if getattr(hook, "bus_name", ""):
                LOG.info("%s has not emitted StateChanged; polling every 2 s", hook.label)
            else:
                LOG.info("%s has no MPRIS player to announce changes; polling every 2 s", hook.label)
        if state.get("catalogue_loading") is True:
            row["catalogue_loading_since"] = row["catalogue_loading_since"] or self.now()
        else:
            row["catalogue_loading_since"] = None
        self.changed()

    @safe
    def hook_error(self, hook, exc):
        """A failed open, navigate or player command."""
        self._hook(hook)["last_error"] = self._error(hook, exc)
        self.changed()

    @safe
    def hook_channels(self, hook, total):
        if type(total) is int and total >= 0:
            self._hook(hook)["channels"] = total
            self.changed()

    # -- requests --------------------------------------------------------------

    @safe
    def command_started(self, request_id, data, transport):
        data = data if isinstance(data, dict) else {}
        command, app = data.get("command"), data.get("app")
        entry = {"id": request_id[:ID_LIMIT] if isinstance(request_id, str) else None,
                 "command": command if command in COMMANDS else (None if command is None else "unknown"),
                 "app": app if app in APP_IDS else (None if app is None else "unknown"),
                 "transport": transport if transport in ("websocket", "mqtt") else "other",
                 "started_at": self.now(), "finished_at": None, "ok": None, "error_code": None,
                 "duration_ms": None, "_started": self.monotonic()}
        self.commands.append(entry)
        self.changed()
        return entry

    @safe
    def command_finished(self, entry, response, client=None):
        if entry is None:
            return
        entry["duration_ms"] = max(0, round((self.monotonic() - entry.pop("_started")) * 1000))
        entry["finished_at"] = self.now()
        entry["ok"] = response.get("ok") is True
        if not entry["ok"]:
            entry["error_code"] = error_code((response.get("error") or {}).get("code"))
            self._burst(client, entry["transport"])
        self.changed()

    def _burst(self, client, transport):
        if client is None:
            return
        now = self.monotonic()
        times, logged = self.bursts.pop(client, (deque(maxlen=BURST_FAILURES * 4), None))
        self.bursts[client] = (times, logged)
        while len(self.bursts) > 32:
            self.bursts.popitem(last=False)
        times.append(now)
        while times and now - times[0] > BURST_WINDOW:
            times.popleft()
        if len(times) >= BURST_FAILURES and (logged is None or now - logged > BURST_QUIET):
            self.bursts[client] = (times, now)
            LOG.warning("%d failed commands from one %s client in %d s", len(times), transport, BURST_WINDOW)

    # -- the read model --------------------------------------------------------

    def _hook_entry(self, bridge, hook):
        row = self._hook(hook)
        transport, where = endpoint(hook)
        state = hook.state if isinstance(hook.state, dict) else {}
        reachable = hook.state is not None
        followers = getattr(getattr(getattr(bridge, "sessions", None), "mpris", None), "followers", {}) or {}
        source = "StateChanged" if getattr(hook, "bus_name", "") else "mpris" if hook.app in followers else None
        polled = bool(hook.polled)
        app = next(a for a in APPS if a.id == hook.app)
        window = any(app.matches(w) for w in getattr(bridge.wm, "windows", []))
        reason = None
        if polled:
            # An endpoint that does not answer while the app has a window is a fault, not silence.
            reason = ("not_running" if not reachable and not window
                      else "not_ready" if not reachable
                      else "never_signalled" if not hook.pushed else "not_ready")
        entry = {"transport": transport, "endpoint": where, "enabled": bool(hook.enabled), "reachable": reachable,
                 "ready": bool(hook.is_ready()), "mode": "polled" if polled else "pushed", "poll_reason": reason,
                 "signal_source": source,
                 **{key: row[key] for key in ("signals_received", "last_signal_at", "last_read_at",
                                              "last_read_ok", "consecutive_failures")},
                 "last_error": dict(row["last_error"]) if row["last_error"] else None}
        if hook.app == "couchside":
            ma = state.get("music_assistant_ready")
            entry["music_assistant_ready"] = ma if isinstance(ma, bool) else None
        if hook.app == "flimmer":
            entry.update(catalogue_loading=state.get("catalogue_loading") is True,
                         catalogue_loading_since=row["catalogue_loading_since"], channels=row["channels"])
        return entry

    def _mpris(self, bridge):
        sessions = getattr(bridge, "sessions", None)
        registry = getattr(sessions, "mpris", None)
        if registry is None:
            return []
        native = {key[4:] for key in getattr(sessions, "players", {}) if key.startswith("app:")}
        rows = []
        for player in registry.players.values():
            app = player.get("app")
            rows.append({"bus_name": player.get("bus_name"), "identity": self.scrub(player.get("name", "")),
                         "desktop": player.get("desktop") or None, "app": app, "state": player.get("state"),
                         "role": player.get("role"),
                         "superseded_by": f"app:{app}" if app and app in native else None})
        return sorted(rows, key=lambda row: str(row["bus_name"]))

    def to_dict(self, bridge):
        ready = bridge.wm.ready.is_set()
        hooks = {}
        for app in APPS:
            hook = getattr(bridge, app.id, None)
            if hook is not None:
                hooks[app.id] = self._hook_entry(bridge, hook)
        return {
            "schema": SCHEMA,
            "generated_at": self.now(),
            "service": {**self.service, "health": "ok" if ready else "degraded"},
            "websocket": {"clients": len(bridge.clients), **self.websocket},
            "discovery": dict(self.discovery),
            "mqtt": dict(self.mqtt),
            "kwin": {"ready": ready, "windows": len(bridge.wm.windows), **self.kwin},
            "hooks": hooks,
            "pairing": (bridge.pairing.summary() if getattr(bridge, "pairing", None) else
                        {"active": False, "reason": None, "paired_clients": 0, "last_paired_at": None,
                         "last_paired_name": None}),
            "mpris": self._mpris(bridge),
            "commands": [{k: v for k, v in entry.items() if not k.startswith("_")} for entry in self.commands],
            "warnings": [dict(row) for row in self.handler.rows.values()],
        }
