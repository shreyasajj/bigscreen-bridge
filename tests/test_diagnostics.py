"""The bridge's own diagnostics: what sets each field, bounds, privacy and how it is exposed."""
import asyncio
import json
import logging
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from bigscreen_bridge import diagnostics as diag_module
from bigscreen_bridge import discovery
from bigscreen_bridge.adapters import APPS, BridgeError
from bigscreen_bridge.couchside import CouchsideHook
from bigscreen_bridge.diagnostics import COMMAND_LIMIT, WARNING_LIMIT, Recorder, close_reason, utc
from bigscreen_bridge.flimmer import FlimmerHook
from bigscreen_bridge.kwin import Receiver
from bigscreen_bridge.mqtt import MqttLink, settings
from bigscreen_bridge.stremio import StremioHook
from bigscreen_bridge.vacuumtube import VacuumTubeHook

AUTH = {"Authorization": "Bearer " + "a" * 48}
FLATPAK = {app.id: app.flatpak for app in APPS}
STREMIO_STATE = {"protocol": 1, "ready": True, "content_types": ["movie"], "capabilities": ["pause"],
                 "media": {"state": "playing"}}


def wire(bridge, recorder):
    bridge.diagnostics = recorder
    recorder.protect(bridge.config["token"])
    return bridge


def stremio(recorder, state=STREMIO_STATE):
    hook = StremioHook(None, True)
    hook.recorder = recorder
    hook.request = AsyncMock(return_value=state)
    return hook


def test_time_format_is_utc_milliseconds_with_z():
    moment = datetime(2026, 11, 17, 18, 42, 0, 123456, tzinfo=timezone(timedelta(hours=2)))
    assert utc(moment) == "2026-11-17T16:42:00.123Z"
    assert Recorder().now().endswith("Z") and len(Recorder().now()) == len("2026-11-17T16:42:00.123Z")


async def test_service_kwin_and_health(bridge, recorder, clock):
    b = wire(bridge[0], recorder)
    recorder.started(8765)
    receiver = Receiver(SimpleNamespace(windows=[], ready=asyncio.Event(), changed=Mock(), recorder=recorder))
    clock.tick(2)
    receiver.ReportWindows(json.dumps([{"id": "a", "caption": "Secret Movie Title"}, {"id": "b"}]))
    b.wm.windows = receiver.manager.windows
    data = recorder.to_dict(b)
    assert data["schema"] == 1 and data["generated_at"] == "2026-11-17T16:42:02.123Z"
    assert data["service"] == {"version": diag_module.package_version(), "started_at": "2026-11-17T16:42:00.123Z",
                               "port": 8765, "health": "ok"}
    assert data["kwin"] == {"ready": True, "windows": 2, "last_report_at": "2026-11-17T16:42:02.123Z"}
    assert "Secret Movie Title" not in json.dumps(data)
    b.wm.ready.clear()
    assert recorder.to_dict(b)["service"]["health"] == "degraded"


async def test_websocket_connect_and_close_reasons(bridge, recorder, clock):
    b, http = bridge
    wire(b, recorder)
    async with http.ws_connect("/ws", headers=AUTH) as ws:
        await ws.receive_json()
        data = recorder.to_dict(b)["websocket"]
        assert data["clients"] == 1 and data["last_connected_at"] and data["last_disconnected_at"] is None
    for _ in range(50):
        if not b.clients:
            break
        await asyncio.sleep(0.01)
    data = recorder.to_dict(b)["websocket"]
    assert data["clients"] == 0 and data["last_disconnect_reason"] == "normal" and data["last_disconnected_at"]
    assert any(w["message"] == "WebSocket client closed (normal)" and w["level"] == "INFO"
               for w in recorder.to_dict(b)["warnings"])


def test_close_reason_mapping():
    def ws(code=None, exc=None):
        return SimpleNamespace(close_code=code, exception=lambda: exc)
    assert close_reason(ws(1006, TimeoutError("No PONG"))) == "heartbeat_timeout"
    assert close_reason(ws(1000)) == close_reason(ws(1001)) == "normal"
    assert close_reason(ws(1006, ConnectionResetError())) == "connection_lost"
    assert close_reason(ws(None), unexpected=True) == "unexpected_message"


async def test_send_timeout_is_the_reason_the_bridge_dropped_a_client(bridge):
    b, _ = bridge
    class Client:
        send_json = AsyncMock(side_effect=TimeoutError)
        close = AsyncMock()
    client = Client()
    b.clients.add(client)
    await b.send(client, {})
    assert client not in b.clients and b.close_reasons[client] == "send_timeout"


async def test_discovery_records_announcement_and_failure(recorder, monkeypatch):
    config = {"device_id": "3f2c9a1e-7b4d", "host": "0.0.0.0", "port": 8765}
    monkeypatch.setattr(discovery, "call", AsyncMock(return_value=["/group"]))
    await discovery.advertise(None, config, "tv", recorder)
    assert recorder.discovery == {"enabled": True, "announced": True, "error": None,
                                  "service_type": "_bigscreen-bridge._tcp", "instance": "tv (3f2c9a1e)"}
    monkeypatch.setattr(discovery, "call", AsyncMock(side_effect=BridgeError("service_unavailable",
                                                                             "System service did not respond")))
    await discovery.advertise(None, config, "tv", recorder)
    assert recorder.discovery["announced"] is False and recorder.discovery["error"] == "System service did not respond"
    await discovery.advertise(None, {**config, "host": "127.0.0.1"}, "tv", recorder)
    assert recorder.discovery["enabled"] is False and recorder.discovery["service_type"] is None


class Broker:
    def __init__(self, fail=None):
        self.fail, self.published = fail, []

    async def __aenter__(self):
        if self.fail:
            raise self.fail
        return self

    async def __aexit__(self, *exc):
        return False

    async def publish(self, topic, payload, qos=0, retain=False):
        self.published.append(topic)

    async def subscribe(self, topic, qos=0):
        pass

    @property
    def messages(self):
        async def none():
            return
            yield
        return none()


async def test_mqtt_fields_follow_the_link(bridge, recorder):
    b, _ = bridge
    wire(b, recorder)
    options = settings({**b.config, "mqtt": {"host": "mqtt.lan", "port": 8883, "tls": True,
                                             "username": "mqtt-user", "password": "hunter2-secret"}})
    broker = Broker(fail=ConnectionRefusedError("auth failed for mqtt-user:hunter2-secret"))
    link = MqttLink(b, options, connect=lambda _: broker, retry=0)
    assert recorder.mqtt == {"configured": True, "connected": False, "broker": "mqtt.lan:8883", "tls": True,
                             "commands_enabled": False, "last_connected_at": None, "last_error": None,
                             "failures": 0, "retry_seconds": 0}
    task = asyncio.create_task(link.run())
    for _ in range(20):
        await asyncio.sleep(0)
    assert recorder.mqtt["failures"] >= 2 and recorder.mqtt["last_error"] == "ConnectionRefusedError"
    assert not recorder.mqtt["connected"]
    hold = asyncio.Event()

    class Held(Broker):
        @property
        def messages(self):
            async def wait():
                await hold.wait()
                return
                yield
            return wait()
    link.connect = lambda _: Held()
    for _ in range(20):
        await asyncio.sleep(0)
    assert recorder.mqtt["connected"] and recorder.mqtt["failures"] == 0 and recorder.mqtt["last_connected_at"]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not recorder.mqtt["connected"]
    text = json.dumps(recorder.to_dict(b))
    assert "hunter2-secret" not in text and "mqtt-user" not in text


async def test_hook_reads_signals_and_failures(bridge, recorder, clock):
    b, _ = bridge
    wire(b, recorder)
    b.stremio = hook = stremio(recorder)
    b.wm.windows = [{"id": "w", "desktop": FLATPAK["stremio"]}]
    await hook.refresh()
    entry = recorder.to_dict(b)["hooks"]["stremio"]
    assert entry["transport"] == "dbus" and entry["endpoint"] == "io.github.shreyasajj.StremioTV.Control"
    assert entry["enabled"] and entry["reachable"] and entry["ready"] and entry["last_read_ok"]
    assert (entry["mode"], entry["poll_reason"], entry["signal_source"]) == ("polled", "never_signalled",
                                                                             "StateChanged")
    assert entry["signals_received"] == 0 and entry["last_signal_at"] is None
    clock.tick(1)
    hook.announce()
    entry = recorder.to_dict(b)["hooks"]["stremio"]
    # The re-read a signal asks for is transient; the hook is not polled between changes.
    assert entry["mode"] == "pushed" and entry["poll_reason"] is None
    assert entry["signals_received"] == 1 and entry["last_signal_at"] == "2026-11-17T16:42:01.123Z"
    hook.announce(received=False)  # the bridge's own follow-up read is not a signal
    assert recorder.to_dict(b)["hooks"]["stremio"]["signals_received"] == 1
    hook.request = AsyncMock(side_effect=BridgeError("not_ready", "Stremio control endpoint is unavailable"))
    clock.tick(1)
    await hook.refresh()
    await hook.refresh()
    entry = recorder.to_dict(b)["hooks"]["stremio"]
    assert entry["reachable"] is False and entry["last_read_ok"] is False and entry["consecutive_failures"] == 2
    assert entry["last_error"] == {"code": "not_ready", "message": "Stremio control endpoint is unavailable",
                                   "at": "2026-11-17T16:42:02.123Z"}
    hook.request = AsyncMock(return_value=STREMIO_STATE)
    await hook.refresh()
    assert recorder.to_dict(b)["hooks"]["stremio"]["consecutive_failures"] == 0
    hook.pushed, hook.request = False, AsyncMock(side_effect=BridgeError("not_ready", "x"))
    await hook.refresh()
    assert recorder.to_dict(b)["hooks"]["stremio"]["poll_reason"] == "not_ready"   # window, no endpoint
    b.wm.windows = []
    assert recorder.to_dict(b)["hooks"]["stremio"]["poll_reason"] == "not_running"


async def test_app_error_text_is_never_recorded(bridge, recorder):
    b, _ = bridge
    wire(b, recorder)
    b.stremio = hook = stremio(recorder)
    del hook.request   # go through the real request/response parsing
    hook.transport = AsyncMock(side_effect=lambda payload, timeout: json.dumps({
        "id": json.loads(payload)["id"], "ok": False,
        "error": {"code": "Not Found: tt0133093 <script>", "message": "No title tt0133093 at /home/me"}}))
    await hook.refresh()
    error = recorder.to_dict(b)["hooks"]["stremio"]["last_error"]
    assert error["code"] == "error" and error["message"] == "Stremio reported an error"


async def test_player_command_failure_is_the_hooks_last_error(bridge, recorder):
    b, _ = bridge
    wire(b, recorder)
    b.stremio = hook = stremio(recorder)
    hook.control = AsyncMock(side_effect=BridgeError("command_timeout", "Stremio did not acknowledge the command"))
    with pytest.raises(BridgeError):
        await b.execute({"id": "1", "command": "pause", "app": "stremio"})
    assert recorder.to_dict(b)["hooks"]["stremio"]["last_error"]["code"] == "command_timeout"


async def test_app_specific_fields(bridge, recorder, clock, monkeypatch, tmp_path):
    b, _ = bridge
    wire(b, recorder)
    b.couchside = CouchsideHook(None, True)
    b.couchside.recorder = recorder
    b.couchside.request = AsyncMock(return_value={"protocol": 1, "ready": True, "content_types": ["search"],
                                                  "playback_owner": "music_assistant",
                                                  "music_assistant_ready": False})
    await b.couchside.refresh()
    assert recorder.to_dict(b)["hooks"]["couchside"]["music_assistant_ready"] is False

    b.flimmer = FlimmerHook(None, True)
    b.flimmer.recorder = recorder
    loading = {"protocol": 1, "ready": True, "content_types": ["channel"], "catalogue_loading": True,
               "capabilities": [], "media": {"state": "idle"}}
    b.flimmer.request = AsyncMock(return_value=loading)
    await b.flimmer.refresh()
    clock.tick(5)
    await b.flimmer.refresh()
    entry = recorder.to_dict(b)["hooks"]["flimmer"]
    assert entry["catalogue_loading"] is True and entry["catalogue_loading_since"] == "2026-11-17T16:42:00.123Z"
    assert entry["channels"] is None
    b.flimmer.request = AsyncMock(side_effect=[{**loading, "catalogue_loading": False},
                                               {"items": [], "next_offset": None, "total": 312}])
    await b.flimmer.browse()
    entry = recorder.to_dict(b)["hooks"]["flimmer"]
    assert (entry["catalogue_loading"], entry["catalogue_loading_since"], entry["channels"]) == (False, None, 312)

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    b.vacuumtube = VacuumTubeHook(True)
    b.vacuumtube.recorder = recorder
    b.sessions = SimpleNamespace(players={}, mpris=SimpleNamespace(players={}, followers={"vacuumtube": b.vacuumtube}))
    b.vacuumtube.pushed = True
    b.vacuumtube.state = {"ready": False}
    entry = recorder.to_dict(b)["hooks"]["vacuumtube"]
    assert entry["transport"] == "socket" and entry["endpoint"] == "$XDG_RUNTIME_DIR/bigscreen-bridge/vacuumtube.sock"
    assert (entry["signal_source"], entry["mode"], entry["poll_reason"]) == ("mpris", "polled", "not_ready")


async def test_mpris_lists_every_player_and_what_supersedes_it(bridge, recorder):
    b, _ = bridge
    wire(b, recorder)
    chromium = {"bus_name": "org.mpris.MediaPlayer2.chromium.instance5", "name": "Chromium", "desktop": "",
                "app": "vacuumtube", "state": "playing", "role": "other", "pid": 5, "owner": ":1.5",
                "media": {"title": "Private video title"}}
    sendspin = {"bus_name": "org.mpris.MediaPlayer2.Sendspin", "name": "Sendspin", "desktop": "org.sendspin.cli",
                "app": "couchside", "state": "paused", "role": "music_assistant", "pid": 6, "owner": ":1.6"}
    b.sessions = SimpleNamespace(players={"app:vacuumtube": {}, "mpris::1.6": sendspin},
                                 mpris=SimpleNamespace(players={"mpris::1.5": chromium, "mpris::1.6": sendspin},
                                                       followers={}))
    rows = recorder.to_dict(b)["mpris"]
    assert rows == [
        {"bus_name": "org.mpris.MediaPlayer2.Sendspin", "identity": "Sendspin", "desktop": "org.sendspin.cli",
         "app": "couchside", "state": "paused", "role": "music_assistant", "superseded_by": None},
        {"bus_name": "org.mpris.MediaPlayer2.chromium.instance5", "identity": "Chromium", "desktop": None,
         "app": "vacuumtube", "state": "playing", "role": "other", "superseded_by": "app:vacuumtube"}]
    assert "Private video title" not in json.dumps(rows)


async def test_commands_ring_records_every_request_once(bridge, recorder, clock):
    b, _ = bridge
    wire(b, recorder)
    cache = OrderedDict()
    await b.handle(json.dumps({"id": "r1", "command": "get_state"}), cache)
    await b.handle(json.dumps({"id": "r1", "command": "get_state"}), cache)  # cached retry
    await b.handle(json.dumps({"id": "r1", "command": "focus", "app": "stremio"}), cache)  # reused ID
    await b.handle("{", cache, transport="mqtt")
    await b.handle(json.dumps({"id": "r2", "command": "launch_rockets", "app": "toaster"}), cache)
    rows = recorder.to_dict(b)["commands"]
    assert [(r["id"], r["command"], r["app"], r["transport"], r["ok"], r["error_code"]) for r in rows] == [
        ("r1", "get_state", None, "websocket", True, None),
        ("r1", "focus", "stremio", "websocket", False, "invalid_request"),
        (None, None, None, "mqtt", False, "invalid_request"),
        ("r2", "unknown", "unknown", "websocket", False, "unsupported")]
    assert all(r["started_at"] and r["finished_at"] and r["duration_ms"] == 0 for r in rows)

    release = asyncio.Event()

    async def slow(request):
        clock.tick(1.25)
        await release.wait()
        return {}
    b.execute = slow
    task = asyncio.create_task(b.handle(json.dumps({"id": "slow", "command": "focus", "app": "flimmer"}), cache))
    await asyncio.sleep(0)
    running = recorder.to_dict(b)["commands"][-1]
    assert running["id"] == "slow" and running["ok"] is None and running["finished_at"] is None
    assert running["duration_ms"] is None
    release.set()
    await task
    assert recorder.to_dict(b)["commands"][-1]["duration_ms"] == 1250
    for n in range(COMMAND_LIMIT + 10):
        await b.handle(json.dumps({"id": f"x{n}", "command": "get_state"}), cache)
    rows = recorder.to_dict(b)["commands"]
    assert len(rows) == COMMAND_LIMIT and rows[-1]["id"] == f"x{COMMAND_LIMIT + 9}"


async def test_burst_of_failed_commands_is_logged_once(bridge, recorder, clock):
    b, _ = bridge
    wire(b, recorder)
    cache = OrderedDict()
    for n in range(9):
        await b.handle(json.dumps({"id": f"bad{n}", "command": "nope"}), cache)
        clock.tick(1)
    lines = [w for w in recorder.to_dict(b)["warnings"] if "failed commands" in w["message"]]
    assert lines == [{"at": lines[0]["at"], "level": "WARNING", "logger": "bigscreen_bridge.diagnostics",
                      "message": "5 failed commands from one websocket client in 15 s", "count": 1}]
    await b.handle(json.dumps({"id": "bad-other", "command": "nope"}), OrderedDict())
    assert len([w for w in recorder.to_dict(b)["warnings"] if "failed commands" in w["message"]]) == 1


def test_warning_buffer_folds_repeats_and_is_bounded(recorder):
    log = logging.getLogger("bigscreen_bridge.mqtt")
    for _ in range(3):
        log.warning("MQTT link lost: %s", "MqttError")
    logging.getLogger("bigscreen_bridge.server").debug("not recorded")
    logging.getLogger("aiohttp.access").warning("not ours")
    rows = list(recorder.handler.rows.values())
    assert [(r["message"], r["count"], r["logger"]) for r in rows] == [
        ("MQTT link lost: MqttError", 3, "bigscreen_bridge.mqtt")]
    for n in range(WARNING_LIMIT + 5):
        logging.getLogger("bigscreen_bridge.hooks").info("line %d", n)
    rows = list(recorder.handler.rows.values())
    assert len(rows) == WARNING_LIMIT and rows[-1]["message"] == f"line {WARNING_LIMIT + 4}"
    try:
        raise ValueError("payload-in-traceback")
    except ValueError:
        logging.getLogger("bigscreen_bridge.server").exception("Request failed")
    assert "payload-in-traceback" not in json.dumps(list(recorder.handler.rows.values()))


async def test_proposed_log_lines(bridge, recorder, clock, caplog):
    b, _ = bridge
    wire(b, recorder)
    caplog.set_level(logging.INFO, logger="bigscreen_bridge")
    b.stremio = hook = stremio(recorder)
    await hook.refresh()
    clock.tick(31)
    await hook.refresh()
    await hook.refresh()
    assert caplog.messages.count("Stremio has not emitted StateChanged; polling every 2 s") == 1

    tube = VacuumTubeHook(True, "/nonexistent/vacuumtube.sock")
    tube.recorder, tube.pushed = recorder, True   # its MPRIS player is there; the socket is not
    await tube.refresh()
    await tube.refresh()
    assert caplog.messages.count("VacuumTube control socket unavailable (not_ready)") == 1
    tube.pushed = False
    other = VacuumTubeHook(True, "/nonexistent/other.sock")
    other.recorder = Recorder()
    await other.refresh()   # simply not running: nothing to say
    assert caplog.messages.count("VacuumTube control socket unavailable (not_ready)") == 1


async def test_couchside_navigation_is_logged_without_its_target(bridge, recorder, caplog):
    b, _ = bridge
    wire(b, recorder)
    caplog.set_level(logging.INFO, logger="bigscreen_bridge")
    hook = CouchsideHook(None, True)
    hook.recorder = recorder
    hook.content_types = list(hook.kinds)
    hook.navigate = AsyncMock(return_value={"view": {"type": "album"}})
    b.couchside = hook
    b.wm.windows = [{"id": "c", "desktop": FLATPAK["couchside"]}]
    hook.state = {"ready": True}
    hook.refresh = AsyncMock()
    await b.execute({"id": "nav-1234-5678", "command": "open", "app": "couchside", "content_type": "album",
                     "content_id": "library://album/12"})
    assert "Couchside acknowledged navigation to album (request nav-1234)" in caplog.messages
    assert "library://album/12" not in caplog.text


def test_recording_never_raises():
    def broken():
        raise RuntimeError("clock failed")
    recorder = Recorder(clock=broken)
    hook = StremioHook(None, True)
    for call in (lambda: recorder.started(1), recorder.ws_connected, recorder.kwin_report,
                 lambda: recorder.hook_signal(hook), lambda: recorder.hook_read(hook),
                 lambda: recorder.command_started("x", {}, "websocket"),
                 lambda: recorder.command_finished({}, {"ok": True})):
        assert call() is None
    recorder.listeners.append(Mock(side_effect=RuntimeError))
    recorder.changed()
    hook.recorder = recorder
    hook.announce()   # the observed code carries on
    assert hook.pushed


async def test_privacy_nothing_sensitive_reaches_diagnostics(bridge, recorder, caplog):
    b, _ = bridge
    wire(b, recorder)
    secrets = ["a" * 48, "hunter2-secret", "mqtt-user", "library://album/12", "Clair de Lune search",
               "tt0133093", "https://example.org/stream.m3u8", "0.37"]
    caplog.set_level(logging.INFO)
    b.stremio = stremio(recorder)
    del b.stremio.request
    b.stremio.transport = AsyncMock(side_effect=lambda payload, timeout: json.dumps({
        "id": json.loads(payload)["id"], "ok": False,
        "error": {"code": "not_found", "message": "No tt0133093 from https://example.org/stream.m3u8"}}))
    cache = OrderedDict()
    requests = [
        {"id": "p1", "command": "open", "app": "couchside", "content_type": "search",
         "content_id": "Clair de Lune search"},
        {"id": "p2", "command": "open", "app": "stremio", "content_type": "movie", "content_id": "tt0133093"},
        {"id": "p3", "command": "open_uri", "uri": "bigscreen://couchside/album/library://album/12"},
        {"id": "p4", "command": "set_volume", "volume_level": 0.37},
        {"id": "p5", "command": "focus", "app": "stremio", "token": "a" * 48},
        {"id": "p6", "command": "open", "app": "stremio", "content_type": "movie",
         "content_id": "https://example.org/stream.m3u8"},
    ]
    for request in requests:
        await b.handle(json.dumps(request), cache)
    await b.stremio.refresh()
    b.execute = AsyncMock(side_effect=RuntimeError("crash with tt0133093 and " + "a" * 48))
    await b.handle(json.dumps({"id": "p7", "command": "get_state"}), cache)
    options = settings({**b.config, "mqtt": {"host": "mqtt.lan", "username": "mqtt-user",
                                             "password": "hunter2-secret"}})
    link = MqttLink(b, options, connect=lambda _: Broker(fail=OSError("bad login mqtt-user:hunter2-secret")),
                    retry=0)
    task = asyncio.create_task(link.run())
    for _ in range(10):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Even a line that quotes the token or the MQTT password directly is scrubbed.
    logging.getLogger("bigscreen_bridge.server").warning("leak %s %s", "a" * 48, "hunter2-secret")
    del b.execute   # read back through the real command
    text = json.dumps(await b.execute({"id": "d", "command": "diagnostics"}))
    for secret in secrets:
        assert secret not in text, secret
    assert len(json.loads(text)["commands"]) == 7 and json.loads(text)["warnings"]


async def test_failed_requests_reach_the_journal_without_their_content(bridge, recorder, caplog):
    b, _ = bridge
    wire(b, recorder)
    caplog.set_level(logging.INFO, logger="bigscreen_bridge")
    cache = OrderedDict()
    await b.handle(json.dumps({"id": "abcdef123456", "command": "open", "app": "vacuumtube",
                               "content_type": "search", "content_id": "secret search"}), cache)
    b.stremio = stremio(recorder)
    b.stremio.control = AsyncMock(side_effect=BridgeError("command_timeout", "Stremio did not acknowledge"))
    await b.handle(json.dumps({"id": "fedcba654321", "command": "pause", "app": "stremio"}), cache, "mqtt")
    lines = [(r.levelname, r.getMessage()) for r in caplog.records if r.getMessage().startswith("Request ")]
    assert lines == [("INFO", "Request abcdef12 failed: open vacuumtube via websocket (unsupported)"),
                     ("WARNING", "Request fedcba65 failed: pause stremio via mqtt (command_timeout)")]
    assert "secret search" not in caplog.text


async def test_a_window_without_its_control_endpoint_is_named_as_such(bridge, monkeypatch):
    b, _ = bridge
    b.flimmer = FlimmerHook(None, True)
    b.flimmer.request = AsyncMock(side_effect=BridgeError("not_ready", "Flimmer control endpoint is unavailable"))
    b.wm.windows = [{"id": "f", "desktop": FLATPAK["flimmer"]}]

    async def processes(*argv):
        return FLATPAK["flimmer"]
    monkeypatch.setattr("bigscreen_bridge.server.process", processes)
    b.launcher = AsyncMock()
    monkeypatch.setattr("asyncio.timeout", lambda _: asyncio.timeouts.timeout(0.3))
    with pytest.raises(BridgeError, match="control endpoint did not answer") as exc:
        await b.focus("flimmer")
    assert exc.value.code == "readiness_timeout"
