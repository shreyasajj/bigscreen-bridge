"""Error paths of the bridge's system services: audio, display power and logind, the
bigscreen:// link opener, the updater's real side effects, and the service lifecycle."""
import asyncio
import hashlib
import http.server
import io
import json
import os
import signal
import tarfile
import threading
from types import SimpleNamespace

from dbus_next import MessageType, Variant
import pytest

from bigscreen_bridge import audio as audio_module
from bigscreen_bridge import open_link, server, update
from bigscreen_bridge.adapters import BridgeError
from bigscreen_bridge.audio import AudioManager, identify_app, parse_streams, parse_volume, process_app_id
from bigscreen_bridge.power import PowerManager, blocked_inhibitors, network_inventory, parse_dpms


# --- audio ------------------------------------------------------------------------------

def test_flatpak_app_from_the_process_cgroup(tmp_path):
    (tmp_path / "42").mkdir()
    (tmp_path / "42/cgroup").write_text("0::/user.slice/app-flatpak-io.github.shreyasajj.StremioTV-1234.scope\n")
    (tmp_path / "43").mkdir()
    (tmp_path / "43/cgroup").write_text("0::/user.slice/session.scope\n")
    assert process_app_id(42, tmp_path) == "io.github.shreyasajj.StremioTV"
    assert process_app_id(43, tmp_path) is None
    assert process_app_id(44, tmp_path) is None             # gone
    assert process_app_id("not a pid", tmp_path) is None


def test_identify_app_by_flatpak_process(monkeypatch):
    monkeypatch.setattr(audio_module, "process_app_id", lambda pid: "io.github.shreyasajj.StremioTV")
    assert identify_app("", "", 42) == "stremio"
    assert identify_app("org.sendspin.cli") == "couchside"
    assert identify_app("unknown.App", "Something") is None


@pytest.mark.parametrize("text", ["", "Volume: loud", "Volume: 0.4 [MUTED] extra"])
def test_unreadable_volume(text):
    with pytest.raises(BridgeError):
        parse_volume(text)


def test_streams_skip_malformed_rows():
    rows = ["not a row", {"properties": "nope"}, {"properties": {}, "index": "x"},
            {"properties": {"application.name": "VLC"}, "index": 3, "corked": False, "mute": False,
             "volume": {"front-left": {"value_percent": "50%"}}}]
    assert parse_streams({"not": "a list"}) == []
    (stream,) = parse_streams(rows)
    assert stream["index"] == 3 and stream["volume"] == 0.5 and stream["stream_active"] is True


class Runner:
    """wpctl/pactl stand-in that keeps a volume and mute state like PipeWire would."""

    def __init__(self, volume=0.5, muted=False, fail=(), obey=True):
        self.volume, self.muted, self.fail, self.obey = volume, muted, set(fail), obey
        self.calls = []

    async def __call__(self, *argv, env=None):
        self.calls.append(argv[1:])
        if argv[1] in self.fail or argv[0] in self.fail:
            raise BridgeError("system_error", "System operation failed")
        if argv[1] == "get-volume":
            return f"Volume: {self.volume:.2f}{' [MUTED]' if self.muted else ''}"
        if argv[1] == "set-volume" and self.obey:
            self.volume = float(argv[3])
        if argv[1] == "set-mute" and self.obey:
            self.muted = argv[3] == "1"
        if argv[1] == "--format=json":
            return "[]"
        return ""


async def test_audio_without_wpctl_is_unavailable_but_lists_streams():
    manager = AudioManager(Runner(), wpctl=None, pactl="pactl")
    manager.wpctl = None
    state = await manager.refresh()
    assert state["available"] is False and state["capabilities"] == [] and state["streams"] == []


async def test_audio_when_both_tools_fail():
    manager = AudioManager(Runner(fail={"wpctl", "pactl"}), wpctl="wpctl", pactl="pactl")
    state = await manager.refresh()
    assert state["available"] is False and state["streams"] == []
    with pytest.raises(BridgeError) as raised:
        await manager.execute("mute")
    assert raised.value.code == "unsupported"


@pytest.mark.parametrize("level", [-0.1, 1.5, float("nan"), float("inf"), True, "0.5", None])
async def test_out_of_range_volume_is_refused_before_anything_runs(level):
    runner = Runner()
    with pytest.raises(BridgeError) as raised:
        await AudioManager(runner, wpctl="wpctl", pactl="pactl").execute("set_volume", level)
    assert raised.value.code == "invalid_request" and runner.calls == []


async def test_unknown_audio_command():
    with pytest.raises(BridgeError) as raised:
        await AudioManager(Runner(), wpctl="wpctl").execute("louder")
    assert raised.value.code == "unsupported"


async def test_volume_and_mute_change_and_confirm():
    runner = Runner()
    manager = AudioManager(runner, wpctl="wpctl", pactl="pactl")
    result = await manager.execute("set_volume", 0.3)
    assert result["changed"] and result["completion_confirmed"] and result["volume"] == 0.3
    result = await manager.execute("mute")
    assert result["changed"] and result["muted"] is True
    assert (await manager.execute("mute"))["changed"] is False


async def test_a_sink_that_ignores_the_change_is_only_accepted():
    manager = AudioManager(Runner(obey=False), wpctl="wpctl", pactl="pactl")
    result = await manager.execute("set_volume", 0.9)
    assert result["status"] == "accepted" and result["completion_confirmed"] is False


# --- display power, logind and the screen locker ----------------------------------------

def test_dpms_parsing():
    assert parse_dpms("garbage") == {"supported": False, "outputs": {}, "display_on": None}
    state = parse_dpms("dpms mode for screen HDMI-A-1: off\ndpms mode for screen DP-1: on\n")
    assert state["display_on"] is True and state["outputs"] == {"HDMI-A-1": "off", "DP-1": "on"}


def test_only_blocking_inhibitors_for_the_operation_count():
    rows = [["sleep:shutdown", "Updater", "Installing", "block"], ["sleep", "Player", "Playing", "delay"],
            ["idle", "Video", "Watching", "block"], ["short"]]
    assert [row["who"] for row in blocked_inhibitors(rows, "shutdown")] == ["Updater"]
    assert [row["who"] for row in blocked_inhibitors(rows, "sleep")] == ["Updater"]


def test_network_inventory_skips_loopback_and_bad_addresses(tmp_path):
    for name, mac in (("lo", "00:00:00:00:00:00"), ("eth0", "aa:bb:cc:dd:ee:ff"), ("weird", "nope")):
        (tmp_path / name).mkdir()
        (tmp_path / name / "address").write_text(mac + "\n")
    (tmp_path / "eth0/device/power").mkdir(parents=True)
    (tmp_path / "eth0/device/power/wakeup").write_text("enabled\n")
    (tmp_path / "noaddress").mkdir()
    rows = network_inventory(tmp_path)
    assert [(r["interface"], r["hardware_wakeup"]) for r in rows] == [("eth0", "enabled")]
    assert network_inventory(tmp_path / "missing") == []


def reply(*body):
    return SimpleNamespace(message_type=MessageType.METHOD_RETURN, body=list(body), error_name=None)


class Logind:
    """System and session bus with logind and the screen locker."""

    def __init__(self, can="yes", inhibitors=(), preparing=False, locked=False, missing=False):
        self.can, self.inhibitors, self.preparing, self.locked, self.missing = can, list(inhibitors), preparing, locked, missing
        self.calls, self.handlers = [], []

    def add_message_handler(self, handler):
        self.handlers.append(handler)

    async def call(self, message):
        self.calls.append(message.member)
        if self.missing and message.member != "AddMatch":
            return SimpleNamespace(message_type=MessageType.ERROR, body=[], error_name="org.freedesktop.DBus.Error.ServiceUnknown")
        member = message.member
        if member in ("AddMatch",):
            return reply()
        if member == "GetNameOwner":
            return reply(":1.2")
        if member.startswith("Can"):
            return reply(self.can)
        if member == "ListInhibitors":
            return reply(self.inhibitors)
        if member == "GetAll":
            return reply({"PreparingForShutdown": Variant("b", self.preparing), "PreparingForSleep": Variant("b", False)})
        if member == "GetActive":
            return reply(self.locked)
        if member == "Lock":
            self.locked = True
            return reply()
        if member == "Get":       # systemd user environment
            return reply(["WAYLAND_DISPLAY=wayland-0"])
        return reply()


class Screen:
    """kscreen-doctor stand-in."""

    def __init__(self, mode="on", obey=True, fail=False):
        self.mode, self.obey, self.fail = mode, obey, fail

    async def __call__(self, *argv, env=None):
        if self.fail:
            raise BridgeError("system_error", "System operation failed")
        if argv[2] in ("on", "off") and self.obey:
            self.mode = argv[2]
        return f"dpms mode for screen HDMI-A-1: {self.mode}\n"


def power(bus=None, screen=None, allowed=("suspend",), clock=None, **kwargs):
    bus = bus or Logind()
    ticks = iter(range(0, 1000))
    return PowerManager(bus, bus, {"allowed_system_actions": list(allowed)}, runner=screen or Screen(),
                        display_command="kscreen-doctor", delay=lambda _: asyncio.sleep(0),
                        clock=clock or (lambda: next(ticks)), **kwargs)


def test_unknown_allowed_actions_are_a_configuration_error():
    with pytest.raises(ValueError):
        PowerManager(None, None, {"allowed_system_actions": ["format_disk"]})
    with pytest.raises(ValueError):
        PowerManager(None, None, {"allowed_system_actions": "suspend"})


async def test_capabilities_follow_configuration_authorization_and_inhibitors():
    manager = power()
    await manager.initialize()
    assert set(manager.state["capabilities"]) == {"screen_off", "screen_on", "lock_screen", "suspend"}
    manager.system_bus.inhibitors = [["sleep", "Updater", "Installing", "block"]]
    await manager.refresh()
    assert "suspend" not in manager.state["capabilities"]


async def test_display_failure_hides_screen_controls():
    manager = power(screen=Screen(fail=True))
    await manager.refresh()
    assert "screen_off" not in manager.state["capabilities"]
    with pytest.raises(BridgeError) as raised:
        await manager.display(False)
    assert raised.value.code == "unsupported"


async def test_display_off_and_a_display_that_ignores_it():
    manager = power()
    result = await manager.display(False)
    assert result["changed"] and result["completion_confirmed"] and result["display_on"] is False
    stubborn = power(screen=Screen(obey=False))
    result = await stubborn.display(False)
    assert result["status"] == "accepted" and result["completion_confirmed"] is False


@pytest.mark.parametrize("bus,allowed,action,code", [
    (Logind(), ("suspend",), "hibernate", "unsupported"),
    (Logind(), (), "suspend", "not_authorized"),
    (Logind(can="challenge"), ("suspend",), "suspend", "authorization_required"),
    (Logind(can="no"), ("suspend",), "suspend", "unsupported"),
    (Logind(inhibitors=[["sleep", "Updater", "Installing", "block"]]), ("suspend",), "suspend", "inhibited"),
    (Logind(preparing=True), ("shutdown",), "shutdown", "busy"),
])
async def test_system_actions_refused(bus, allowed, action, code):
    manager = power(bus, allowed=allowed)
    with pytest.raises(BridgeError) as raised:
        await manager.submit_system(action)
    assert raised.value.code == code
    assert not {"Suspend", "PowerOff", "Reboot"} & set(bus.calls)


async def test_allowed_suspend_is_submitted():
    bus = Logind()
    assert (await power(bus).submit_system("suspend"))["status"] == "submitted"
    assert "Suspend" in bus.calls


async def test_without_logind_or_a_locker():
    manager = power(Logind(missing=True))
    await manager.refresh()
    assert manager.state["system_state"] == "unknown" and manager.state["locked"] is None
    assert "lock_screen" not in manager.state["capabilities"]
    with pytest.raises(BridgeError) as raised:
        await manager.lock()
    assert raised.value.code == "unsupported"


async def test_lock_screen():
    manager = power()
    await manager.refresh()
    assert (await manager.lock())["completion_confirmed"] is True


async def test_prepare_for_sleep_signal_from_logind_only():
    from dbus_next import Message
    seen = []
    manager = power(changed=lambda: seen.append(manager.state["system_state"]))
    await manager.initialize()

    def signal_from(sender, value, member="PrepareForSleep"):
        message = Message.new_signal("/org/freedesktop/login1", "org.freedesktop.login1.Manager", member, "b", [value])
        message.sender = sender
        manager._signal(message)

    signal_from(":1.66", True)                   # not logind
    signal_from(":1.2", True)
    signal_from(":1.2", False)
    signal_from(":1.2", True, member="PrepareForShutdown")
    assert seen == ["preparing_sleep", "awake", "preparing_shutdown"]


# --- bigscreen:// link opener -------------------------------------------------------------

def link_config(tmp_path, http, token="a" * 48):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"token": token, "device_id": "test-device", "host": "0.0.0.0",
                                "port": http.server.port}))
    os.chmod(path, 0o600)
    return path


async def test_open_uri_delivers_the_link(bridge, tmp_path):
    b, http = bridge
    b.wm.windows = [{"id": "w", "desktop": "io.github.shreyasajj.Couchside"}]
    result = await open_link.open_uri("bigscreen://couchside", link_config(tmp_path, http))
    assert b.wm.focused == ["w"] and result


async def test_open_uri_with_a_revoked_token(bridge, tmp_path):
    _, http = bridge
    with pytest.raises(BridgeError) as raised:
        await open_link.open_uri("bigscreen://couchside", link_config(tmp_path, http, token="b" * 48))
    assert raised.value.code == "invalid_auth"


async def test_open_uri_the_bridge_refuses(bridge, tmp_path):
    _, http = bridge
    with pytest.raises(BridgeError) as raised:
        await open_link.open_uri("bigscreen://stremio/movie/tt0000000", link_config(tmp_path, http))
    assert raised.value.code


def test_main_rejects_a_bad_link_and_notifies(monkeypatch, tmp_path, capsys):
    notified = []
    monkeypatch.setattr("sys.argv", ["bigscreen-bridge-open", "https://example.com", "--notify",
                                     "--config", str(tmp_path / "none.json")])
    monkeypatch.setattr(open_link.shutil, "which", lambda name: "/usr/bin/notify-send")
    monkeypatch.setattr(open_link.subprocess, "run", lambda argv, check: notified.append(argv))
    with pytest.raises(SystemExit) as exited:
        open_link.main()
    assert exited.value.code == 1
    assert "bigscreen://" in capsys.readouterr().err
    assert notified and notified[0][:3] == ["notify-send", "--app-name=Bigscreen Bridge", "Bigscreen link failed"]


def test_main_without_a_config_says_so(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("sys.argv", ["bigscreen-bridge-open", "bigscreen://couchside", "--config",
                                     str(tmp_path / "missing.json")])
    with pytest.raises(SystemExit):
        open_link.main()
    assert capsys.readouterr().err.strip()


def test_main_prints_the_result(monkeypatch, capsys):
    async def delivered(uri, config):
        return {"focused": True}

    monkeypatch.setattr("sys.argv", ["bigscreen-bridge-open", "bigscreen://couchside"])
    monkeypatch.setattr(open_link, "open_uri", delivered)
    open_link.main()
    assert json.loads(capsys.readouterr().out) == {"focused": True}


# --- the updater's real side effects -----------------------------------------------------

class Served:
    """A local HTTP server answering each path with (status, body)."""

    def __init__(self, routes):
        routes_ = routes

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                status, body = routes_.get(self.path, (404, b""))
                self.send_response(status)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def served():
    servers = []

    def start(routes):
        servers.append(Served(routes))
        return servers[-1]
    yield start
    for s in servers:
        s.close()


@pytest.mark.parametrize("status,message", [(403, "rate limit"), (429, "rate limit"), (500, "HTTP 500")])
def test_get_reports_github_errors(served, status, message):
    server_ = served({"/x": (status, b"")})
    with pytest.raises(update.UpdateError, match=message):
        update._get(server_.url + "/x")


def test_get_unreachable():
    with pytest.raises(update.UpdateError, match="unreachable"):
        update._get("http://127.0.0.1:9/")


def test_fetch_releases_shapes(served, monkeypatch):
    server_ = served({"/list": (200, b'[{"tag_name": "v1"}]'), "/dict": (200, b"{}"), "/bad": (200, b"<html>")})
    for path, expected in (("/list", [{"tag_name": "v1"}]), ("/dict", [])):
        monkeypatch.setattr(update, "RELEASES", server_.url + path)
        assert update.fetch_releases() == expected
    monkeypatch.setattr(update, "RELEASES", server_.url + "/bad")
    with pytest.raises(update.UpdateError, match="Unreadable"):
        update.fetch_releases()


def release_archive(tmp_path, version, script):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as tar:
        body = script.encode()
        info = tarfile.TarInfo(f"bigscreen-bridge-{version}/scripts/install-tv.sh")
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
    name = f"bigscreen-bridge-{version}.tar.gz"
    (tmp_path / name).write_bytes(data.getvalue())
    (tmp_path / "SHA256SUMS").write_text(f"{hashlib.sha256(data.getvalue()).hexdigest()}  {name}\n")
    return {"tag_name": f"v{version}", "assets": [
        {"name": name, "browser_download_url": (tmp_path / name).as_uri()},
        {"name": "SHA256SUMS", "browser_download_url": (tmp_path / "SHA256SUMS").as_uri()}]}


def test_install_unpacks_and_runs_the_release_installer(tmp_path):
    marker = tmp_path / "installed"
    release = release_archive(tmp_path, "9.9.9", f"touch {marker}\n")
    update.make_install(tmp_path / "root")(release)
    assert marker.exists() and (tmp_path / "root/releases/9.9.9/scripts/install-tv.sh").exists()
    update.make_install(tmp_path / "root")(release)              # the same release again replaces it
    assert (tmp_path / "root/releases/9.9.9/scripts/install-tv.sh").exists()


def test_install_failures(tmp_path):
    failing = release_archive(tmp_path, "9.9.8", "exit 4\n")
    with pytest.raises(update.UpdateError, match="exited with 4"):
        update.make_install(tmp_path / "root")(failing)
    with pytest.raises(update.UpdateError, match="has no"):
        update.make_install(tmp_path / "root")({"tag_name": "v9.9.7", "assets": []})


def test_a_corrupt_archive_is_refused(tmp_path):
    name = "bigscreen-bridge-1.0.0.tar.gz"
    (tmp_path / name).write_bytes(b"not a tarball")
    sums = f"{hashlib.sha256(b'not a tarball').hexdigest()}  {name}\n"
    with pytest.raises(update.UpdateError, match="Could not unpack"):
        update.verify_and_extract(tmp_path / name, sums, name, tmp_path / "root/releases/1.0.0")
    assert not (tmp_path / "root/releases").exists()


def test_health_waits_for_ok_and_gives_up(served, monkeypatch, tmp_path):
    monkeypatch.setattr(update.time, "sleep", lambda _: None)
    healthy = served({"/health": (200, b'{"ok": true}')})
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"host": "127.0.0.1", "port": int(healthy.url.rsplit(":", 1)[1])}))
    assert update.make_health(config)() is True
    sick = served({"/health": (200, b'{"ok": false}')})
    config.write_text(json.dumps({"host": "127.0.0.1", "port": int(sick.url.rsplit(":", 1)[1])}))
    monkeypatch.setattr(update, "HEALTH_SECONDS", 0.2)
    assert update.make_health(config)() is False


def test_settings_from_an_unreadable_config(tmp_path):
    assert update._settings(tmp_path / "missing.json") == {"channel": "stable", "auto": True}
    (tmp_path / "bad.json").write_text("{")
    assert update._settings(tmp_path / "bad.json") == {"channel": "stable", "auto": True}


def test_relink_replaces_a_stale_temporary_link(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / ".venv-rollback").symlink_to(tmp_path / "a")
    update._relink(tmp_path, tmp_path / "b")
    assert os.readlink(tmp_path / "venv") == str(tmp_path / "b")
    assert not (tmp_path / ".venv-rollback").exists()


def test_main_wires_the_real_side_effects(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(update, "run", lambda config, **kwargs: seen.update(config=config, **kwargs) or 0)
    assert update.main(["--config", str(tmp_path / "c.json"), "--check", "--now"]) == 0
    assert seen["config"] == tmp_path / "c.json" and seen["check"] and seen["now"]
    assert seen["fetch"] is update.fetch_releases and seen["restart"] is update.restart


def test_restart_asks_systemd(monkeypatch):
    commands = []
    monkeypatch.setattr(update.subprocess, "run", lambda argv, check: commands.append(argv))
    update.restart()
    assert commands == [["systemctl", "--user", "restart", "bigscreen-bridge.service"]]


# --- the service lifecycle ---------------------------------------------------------------

async def test_process_and_launch_errors():
    assert await server.process("/bin/sh", "-c", "printf ' ok \n'") == "ok"
    with pytest.raises(BridgeError) as failed:
        await server.process("/bin/sh", "-c", "exit 2")
    assert failed.value.code == "launch_failed"
    with pytest.raises(BridgeError):
        await server.launch_app("/bin/sh", "-c", "exit 1")
    await server.launch_app("/bin/sh", "-c", "exit 0")


class LifecycleBus:
    """Session or system bus for serve(): every call answers, nothing is on it."""

    def __init__(self):
        self.exported, self.disconnected, self.gone = [], False, asyncio.Event()

    async def connect(self):
        return self

    def export(self, path, interface):
        self.exported.append(interface.name)

    def add_message_handler(self, handler):
        pass

    async def call(self, message):
        # The bus daemon itself answers; no other service is running.
        if message.destination == "org.freedesktop.DBus" and message.member in ("AddMatch", "ListNames"):
            return reply([]) if message.member == "ListNames" else reply()
        return SimpleNamespace(message_type=MessageType.ERROR, body=[], error_name="org.freedesktop.DBus.Error.ServiceUnknown")

    async def wait_for_disconnect(self):
        await self.gone.wait()

    def disconnect(self):
        self.disconnected = True


class LifecycleKWin:
    def __init__(self, monitor):
        self.windows, self.ready, self.lost = [], asyncio.Event(), asyncio.Event()
        self.bus, self.closed, self.changed = LifecycleBus(), False, None

    async def start(self):
        self.ready.set()

    async def refresh(self):
        pass

    async def close(self):
        self.closed = True


@pytest.fixture
def lifecycle(monkeypatch, tmp_path, unused_tcp_port):
    made = SimpleNamespace(kwin=None, system=LifecycleBus(), lights=None)

    def kwin(monitor):
        made.kwin = LifecycleKWin(monitor)
        return made.kwin

    class Lights(server.Lights):
        async def close(self):
            made.lights_closed = True
            await super().close()

    async def process(*argv):
        return "io.github.shreyasajj.Couchside\n"

    async def no_advertising(*args):
        pass

    monkeypatch.setattr(server, "KWin", kwin)
    monkeypatch.setattr(server, "MessageBus", lambda bus_type=None: made.system)
    monkeypatch.setattr(server, "process", process)
    monkeypatch.setattr(server, "Lights", Lights)
    monkeypatch.setattr(server, "advertise", no_advertising)
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"token": "a" * 48, "device_id": "test-device", "host": "127.0.0.1",
                                  "port": unused_tcp_port, "monitor_path": str(tmp_path / "monitor.js")}))
    os.chmod(config, 0o600)
    made.config, made.port = config, unused_tcp_port
    return made


async def until_healthy(port):
    import aiohttp
    async with aiohttp.ClientSession() as session:
        for _ in range(200):
            try:
                async with session.get(f"http://127.0.0.1:{port}/health") as response:
                    return await response.json()
            except aiohttp.ClientConnectionError:
                await asyncio.sleep(0.02)
    raise AssertionError("never served /health")


async def test_serve_answers_then_stops_cleanly_on_sigterm(lifecycle):
    task = asyncio.create_task(server.serve(lifecycle.config))
    assert await until_healthy(lifecycle.port) == {"ok": True, "protocol": 1}
    assert {"org.bigscreen.Bridge.Status", "org.bigscreen.Bridge.Pairing"} <= set(lifecycle.kwin.bus.exported)
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(task, 5)
    assert lifecycle.kwin.closed and lifecycle.system.disconnected and lifecycle.lights_closed


async def test_serve_exits_for_a_restart_when_kwin_goes(lifecycle):
    task = asyncio.create_task(server.serve(lifecycle.config))
    await until_healthy(lifecycle.port)
    lifecycle.kwin.lost.set()
    with pytest.raises(RuntimeError, match="restart required"):
        await asyncio.wait_for(task, 5)
    assert lifecycle.kwin.closed and lifecycle.system.disconnected


def test_main_with_a_bad_config_path(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.argv", ["bigscreen-bridge", "--config", str(tmp_path / "missing.json")])
    with pytest.raises((OSError, ValueError)):
        server.main()
