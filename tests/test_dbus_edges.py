"""KWin window monitor, MPRIS registry and D-Bus helpers against stand-in session buses.

The fakes act like the real services rather than recording calls. The fake KWin reads the
script it was asked to load and calls back the way that script would. The fake MPRIS
player changes its PlaybackStatus when told to Play or Pause.
"""

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from bigscreen_bridge import kwin as kwin_module
from bigscreen_bridge import runtime_io
from bigscreen_bridge.adapters import BridgeError
from bigscreen_bridge.kwin import KWin, Receiver
from bigscreen_bridge.mpris import MprisRegistry, media_state
from dbus_next import Message, MessageType, Variant
from dbus_next.constants import RequestNameReply

WINDOWS = [
    {"id": "w1", "desktop": "io.github.shreyasajj.StremioTV", "active": False},
    {"id": "w2", "desktop": "io.github.shreyasajj.Couchside", "active": True},
]


def ok(*body):
    return SimpleNamespace(message_type=MessageType.METHOD_RETURN, body=list(body), error_name=None)


def error(name):
    return SimpleNamespace(message_type=MessageType.ERROR, body=[], error_name=name)


@pytest.fixture
def fast(monkeypatch):
    """Shrink the bridge's fixed KWin waits so timeouts are reached in milliseconds."""
    real_wait_for, real_timeout = asyncio.wait_for, asyncio.timeout
    monkeypatch.setattr(
        kwin_module.asyncio, "wait_for", lambda aw, timeout: real_wait_for(aw, min(timeout, 0.05))
    )
    monkeypatch.setattr(kwin_module.asyncio, "timeout", lambda delay: real_timeout(min(delay, 0.05)))


class FakeKWin:
    """A session bus with KWin on it. `behaviour` decides what a started script does:
    "report" (the monitor reports windows), "silent", or a callable(bus, script_text)."""

    def __init__(self, owner=RequestNameReply.PRIMARY_OWNER, behaviour="report", windows=None):
        self.owner, self.behaviour, self.windows = owner, behaviour, windows or WINDOWS
        self.sent, self.unloaded, self.loaded, self.owner_handlers = [], [], [], []
        self.load_reply, self.start_reply = None, None
        self.receiver = None
        self.disconnected = False

    async def connect(self):
        return self

    async def request_name(self, name, flags):
        return self.owner

    def export(self, path, interface):
        self.receiver = interface

    async def introspect(self, destination, path):
        return None

    def get_proxy_object(self, destination, path, introspection):
        if destination == "org.freedesktop.DBus":
            interface = SimpleNamespace(on_name_owner_changed=self.owner_handlers.append)
        else:

            async def unload(name):
                self.unloaded.append(name)

            interface = SimpleNamespace(call_unload_script=unload)
        return SimpleNamespace(get_interface=lambda name: interface)

    def disconnect(self):
        self.disconnected = True

    async def call(self, message):
        self.sent.append(message.member)
        if message.member == "loadScript":
            path = Path(message.body[0])
            self.loaded.append(await asyncio.to_thread(lambda: path.read_text() if path.exists() else ""))
            return self.load_reply or ok(len(self.loaded))
        if message.member == "start":
            if self.start_reply:
                return self.start_reply
            script = self.loaded[-1]
            loop = asyncio.get_running_loop()
            if callable(self.behaviour):
                loop.call_soon(self.behaviour, self, script)
            elif self.behaviour == "report":
                loop.call_soon(self.answer, script)
            return ok()
        raise AssertionError(message.member)

    def answer(self, script):
        """Do what the loaded script would: report, refresh, or focus."""
        windows = json.dumps(self.windows)
        if "RefreshWindows" in script:
            request = re.search(r'JSON\.stringify\(windows\), ("[^"]+")', script)[1]
            self.receiver.RefreshWindows(windows, json.loads(request))
        elif "FocusResult" in script:
            target = json.loads(re.search(r"=== (\"[^\"]*\")", script)[1])
            request = json.loads(re.search(r'"FocusResult", ("[^"]+")', script)[1])
            found = any(w["id"] == target for w in self.windows)
            for w in self.windows:
                w["active"] = w["id"] == target
            self.receiver.FocusResult(request, found)
            if found:
                self.receiver.ReportWindows(json.dumps(self.windows))
        else:
            self.receiver.ReportWindows(windows)


async def started(monkeypatch, bus, changed=lambda: None):
    monkeypatch.setattr(kwin_module, "MessageBus", lambda: bus)
    manager = KWin(Path("/nonexistent/monitor.js"), changed)
    await manager.start()
    return manager


async def test_start_loads_the_monitor_and_waits_for_its_report(monkeypatch):
    bus = FakeKWin()
    manager = await started(monkeypatch, bus)
    assert manager.ready.is_set() and manager.windows == WINDOWS
    assert bus.unloaded == ["bigscreen-bridge-monitor"]
    assert bus.sent == ["loadScript", "start"]


async def test_start_refuses_a_second_bridge(monkeypatch):
    with pytest.raises(RuntimeError, match="Another bridge"):
        await started(monkeypatch, FakeKWin(owner=RequestNameReply.EXISTS))


async def test_a_silent_monitor_is_loaded_again_then_reported(monkeypatch, fast):
    attempts = []

    def second_time(bus, script):
        attempts.append(1)
        if len(attempts) == 2:
            bus.answer(script)

    bus = FakeKWin(behaviour=second_time)
    manager = await started(monkeypatch, bus)
    assert manager.ready.is_set()
    assert bus.unloaded == ["bigscreen-bridge-monitor"] * 2


async def test_a_monitor_that_never_reports_fails_after_three_loads(monkeypatch, fast):
    bus = FakeKWin(behaviour="silent")
    with pytest.raises(TimeoutError):
        await started(monkeypatch, bus)
    assert bus.sent.count("start") == kwin_module.MONITOR_ATTEMPTS


async def test_kwin_restarting_drops_its_windows(monkeypatch):
    changes = []
    bus = FakeKWin()
    manager = await started(monkeypatch, bus, lambda: changes.append(1))
    changes.clear()
    (handler,) = bus.owner_handlers
    handler("org.kde.plasmashell", ":1.5", ":1.9")  # someone else: ignored
    handler("org.kde.KWin", "", ":1.7")  # KWin appearing for the first time
    assert manager.ready.is_set() and changes == []
    handler("org.kde.KWin", ":1.7", ":1.8")
    assert not manager.ready.is_set() and manager.windows == [] and manager.lost.is_set()
    assert changes == [1]


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "{}",
        json.dumps([{"desktop": "no id"}]),
        json.dumps([{"id": 5}]),
        json.dumps([{"id": str(n)} for n in range(513)]),
    ],
)
def test_malformed_window_reports_keep_the_last_good_list(payload):
    manager = KWin(Path("/x"))
    manager.windows = list(WINDOWS)
    Receiver(manager).ReportWindows(payload)
    assert manager.windows == WINDOWS and not manager.ready.is_set()


def test_late_or_unknown_replies_are_ignored():
    manager = KWin(Path("/x"))
    receiver = Receiver(manager)
    receiver.FocusResult("nobody-asked", True)
    receiver.RefreshWindows(json.dumps(WINDOWS), "nobody-asked")
    assert manager.windows == WINDOWS and manager.pending == {}


async def test_refresh_reads_fresh_windows_and_unloads_its_script(monkeypatch):
    bus = FakeKWin()
    manager = await started(monkeypatch, bus)
    bus.windows = [{"id": "w9", "desktop": "x", "active": True}]
    await manager.refresh()
    assert manager.windows == bus.windows
    assert bus.unloaded[-1].startswith("bigscreen-bridge-refresh-") and manager.pending == {}


async def test_refresh_without_an_answer_is_window_manager_unavailable(monkeypatch, fast):
    bus = FakeKWin()
    manager = await started(monkeypatch, bus)
    bus.behaviour = "silent"
    with pytest.raises(BridgeError) as raised:
        await manager.refresh()
    assert raised.value.code == "window_manager_unavailable"
    assert bus.unloaded[-1].startswith("bigscreen-bridge-refresh-") and manager.pending == {}
    assert manager.windows == WINDOWS


async def test_focus_activates_the_window(monkeypatch):
    bus = FakeKWin()
    manager = await started(monkeypatch, bus)
    await manager.focus("w1")
    assert [w["id"] for w in manager.windows if w["active"]] == ["w1"]
    assert bus.unloaded[-1].startswith("bigscreen-bridge-focus-")


@pytest.mark.parametrize("behaviour,window", [("report", "gone"), ("silent", "w1")])
async def test_focus_failures(monkeypatch, fast, behaviour, window):
    bus = FakeKWin()
    manager = await started(monkeypatch, bus)
    bus.behaviour = behaviour
    with pytest.raises(BridgeError) as raised:
        await manager.focus(window)
    assert raised.value.code == "focus_failed"
    assert manager.pending == {} and bus.unloaded[-1].startswith("bigscreen-bridge-focus-")


async def test_focus_found_but_never_shown_active(monkeypatch, fast):
    def found_only(bus, script):
        request = json.loads(re.search(r'"FocusResult", ("[^"]+")', script)[1])
        bus.receiver.FocusResult(request, True)

    bus = FakeKWin()
    manager = await started(monkeypatch, bus)
    bus.behaviour = found_only
    with pytest.raises(BridgeError, match="did not confirm"):
        await manager.focus("w1")


@pytest.mark.parametrize(
    "load,start,message",
    [
        (error("org.kde.Error"), None, "loadScript failed"),
        (ok(-1), None, "refused script"),
        (None, error("org.kde.Error"), "start failed"),
    ],
)
async def test_script_loading_errors(load, start, message):
    manager = KWin(Path("/x"))
    manager.bus = FakeKWin()
    manager.bus.load_reply, manager.bus.start_reply = load, start
    with pytest.raises(RuntimeError, match=message):
        await manager.run(Path("/x"), "name")


async def test_close_unloads_the_monitor_and_leaves_the_bus(monkeypatch):
    bus = FakeKWin()
    manager = await started(monkeypatch, bus)
    await manager.close()
    assert bus.unloaded[-1] == "bigscreen-bridge-monitor" and bus.disconnected
    await KWin(Path("/x")).close()  # never started: nothing to undo


# --- runtime_io -------------------------------------------------------------------------


class ReplyBus:
    def __init__(self, reply=None, raises=None, delay=0):
        self.reply, self.raises, self.delay = reply, raises, delay

    async def call(self, message):
        await asyncio.sleep(self.delay)
        if self.raises:
            raise self.raises
        return self.reply


def test_unwrap_nested_variants():
    value = Variant("a{sv}", {"a": Variant("as", ["x", "y"]), "b": Variant("v", Variant("i", 3))})
    assert runtime_io.unwrap([value, (Variant("s", "z"),)]) == [{"a": ["x", "y"], "b": 3}, ["z"]]


@pytest.mark.parametrize(
    "bus,code",
    [
        (None, "service_unavailable"),
        (ReplyBus(raises=EOFError()), "service_unavailable"),
        (ReplyBus(reply=None), "system_error"),
        (ReplyBus(reply=error("org.x.Failed")), "system_error"),
        (ReplyBus(reply=error("org.freedesktop.DBus.Error.AccessDenied")), "authorization_required"),
        (ReplyBus(reply=error("org.freedesktop.PolicyKit1.Error.NotAuthorized")), "authorization_required"),
    ],
)
async def test_call_errors(bus, code):
    with pytest.raises(BridgeError) as raised:
        await runtime_io.call(bus, "org.example.Service", "/p", "org.example.I", "M")
    assert raised.value.code == code


async def test_call_times_out():
    with pytest.raises(BridgeError) as raised:
        await runtime_io.call(
            ReplyBus(reply=ok(), delay=1), "org.example.Service", "/p", "org.example.I", "M", timeout=0.01
        )
    assert raised.value.code == "service_unavailable"


async def test_run_failures():
    with pytest.raises(BridgeError) as failed:
        await runtime_io.run("/bin/sh", "-c", "exit 3")
    assert failed.value.code == "system_error"
    with pytest.raises(BridgeError) as slow:
        await runtime_io.run("/bin/sh", "-c", "sleep 10", timeout=0.1)
    assert slow.value.code == "command_timeout"
    assert await runtime_io.run("/bin/sh", "-c", "printf ok") == "ok"


async def test_desktop_environment_keeps_only_display_variables(monkeypatch):
    monkeypatch.setenv("KEEP_ME", "1")
    bus = ReplyBus(reply=ok(["WAYLAND_DISPLAY=wayland-0", "SECRET=x", "BROKEN", "DISPLAY=:0"]))
    env = await runtime_io.desktop_environment(bus)
    assert env["WAYLAND_DISPLAY"] == "wayland-0" and env["DISPLAY"] == ":0"
    assert "SECRET" not in env or env["SECRET"] != "x"
    assert env["KEEP_ME"] == "1"


async def test_desktop_environment_without_systemd(monkeypatch):
    monkeypatch.setenv("KEEP_ME", "1")
    env = await runtime_io.desktop_environment(None)
    assert env["KEEP_ME"] == "1"


# --- MPRIS ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,expected",
    [
        ("Playing", "playing"),
        ("Paused", "paused"),
        ("Stopped", "idle"),
        ("Weird", "unknown"),
        (None, "unknown"),
    ],
)
def test_every_playback_status_maps(status, expected):
    from bigscreen_bridge.mpris import STATES

    assert STATES.get(status, "unknown") == expected


def test_media_state_drops_bad_metadata_and_never_exposes_urls():
    state = media_state(
        {
            "Metadata": {
                "xesam:title": "T" * 3000,
                "mpris:length": 2_000_000,
                "xesam:url": "file:///home/private.mkv",
            },
            "Position": 1_000_000,
        }
    )
    assert len(state["title"]) == 2048 and state["duration"] == 2 and state["position"] == 1
    assert "file://" not in json.dumps(state)
    bad = media_state({"Metadata": "nonsense", "Position": -5})
    assert bad["title"] == "" and "position" not in bad and "duration" not in bad


class FakeMpris:
    """A session bus with MPRIS players on it, each a dict of its Player properties."""

    def __init__(self, players):
        self.players = players  # bus name -> {"owner", "pid", "root", "player"}
        self.handlers, self.matches, self.calls = [], [], []
        self.obey = True

    def add_message_handler(self, handler):
        self.handlers.append(handler)

    async def call(self, message):
        member, body = message.member, message.body
        self.calls.append(member)
        if member == "AddMatch":
            self.matches.append(body[0])
            return ok()
        if member == "ListNames":
            return ok(["org.freedesktop.DBus", *self.players])
        name = body[0] if message.destination == "org.freedesktop.DBus" else message.destination
        player = self.players.get(name)
        if player is None:
            return error("org.freedesktop.DBus.Error.NameHasNoOwner")
        if member == "GetNameOwner":
            return ok(player["owner"])
        if member == "GetConnectionUnixProcessID":
            return ok(player["pid"]) if player.get("pid") else error("org.freedesktop.DBus.Error.Failed")
        if member == "GetAll":
            props = player["root"] if body[0] == "org.mpris.MediaPlayer2" else player["player"]
            return (
                ok(
                    {
                        k: Variant("s" if isinstance(v, str) else "b" if isinstance(v, bool) else "x", v)
                        for k, v in props.items()
                    }
                )
                if props is not None
                else error("org.x.NoInterface")
            )
        if member in ("Play", "Pause", "Stop"):
            if self.obey:
                player["player"]["PlaybackStatus"] = {
                    "Play": "Playing",
                    "Pause": "Paused",
                    "Stop": "Stopped",
                }[member]
            return ok()
        raise AssertionError(member)

    def signal(self, sender, interface, member, body, path="/org/mpris/MediaPlayer2"):
        message = Message.new_signal(path, interface, member, signature="", body=[])
        message.sender, message.body = sender, body
        for handler in self.handlers:
            handler(message)


def player(owner, status="Playing", identity="VLC media player", desktop="vlc", **extra):
    return {
        "owner": owner,
        "pid": None,
        "root": {"Identity": identity, "DesktopEntry": desktop},
        "player": {"PlaybackStatus": status, "CanControl": True, "CanPause": True, "CanPlay": True, **extra},
    }


async def test_registry_reads_players_and_follows_them_coming_and_going():
    bus = FakeMpris({"org.mpris.MediaPlayer2.vlc": player(":1.10")})
    changes = []
    registry = MprisRegistry(bus, lambda *change: changes.append(change))
    await registry.initialize()
    assert len(bus.matches) == 3
    (record,) = registry.players.values()
    assert record["state"] == "playing" and record["capabilities"] == ["pause", "stop"]
    bus.signal(
        "org.freedesktop.DBus",
        "org.freedesktop.DBus",
        "NameOwnerChanged",
        ["org.mpris.MediaPlayer2.vlc", ":1.10", ""],
    )
    assert registry.players[record["id"]]["available"] is False and registry.dirty
    assert changes[-1] == (record["id"], "playing", "unavailable")
    del bus.players["org.mpris.MediaPlayer2.vlc"]
    await registry.refresh()
    assert registry.players == {} and changes[-1] == (record["id"], "playing", "unavailable")


async def test_properties_changed_updates_the_state():
    bus = FakeMpris({"org.mpris.MediaPlayer2.vlc": player(":1.10")})
    changes = []
    registry = MprisRegistry(bus, lambda *change: changes.append(change))
    await registry.initialize()
    registry.dirty = False
    bus.signal(
        ":1.10",
        "org.freedesktop.DBus.Properties",
        "PropertiesChanged",
        ["org.mpris.MediaPlayer2.Player", {"PlaybackStatus": Variant("s", "Paused")}, []],
    )
    (record,) = registry.players.values()
    assert record["state"] == "paused" and changes == [(record["id"], "playing", "paused")] and registry.dirty
    bus.signal(
        ":1.99",
        "org.freedesktop.DBus.Properties",
        "PropertiesChanged",
        ["org.mpris.MediaPlayer2.Player", {"PlaybackStatus": Variant("s", "Playing")}, []],
    )
    assert record["state"] == "paused"  # a stranger's signal changes nothing


async def test_a_player_that_will_not_answer_is_skipped():
    bus = FakeMpris(
        {
            "org.mpris.MediaPlayer2.good": player(":1.10"),
            "org.mpris.MediaPlayer2.broken": dict(player(":1.11"), player=None),
        }
    )
    registry = MprisRegistry(bus)
    await registry.initialize()
    assert [r["bus_name"] for r in registry.players.values()] == ["org.mpris.MediaPlayer2.good"]


async def test_control_plays_and_confirms():
    bus = FakeMpris({"org.mpris.MediaPlayer2.vlc": player(":1.10", status="Paused")})
    registry = MprisRegistry(bus)
    await registry.initialize()
    (key,) = registry.players
    assert await registry.control(key, "play") == {
        "changed": True,
        "state": "playing",
        "completion_confirmed": True,
    }
    assert await registry.control(key, "play") == {
        "changed": False,
        "state": "playing",
        "completion_confirmed": True,
    }


@pytest.mark.parametrize(
    "setup,command,code",
    [
        (lambda r, b: r.players.clear(), "pause", "not_ready"),
        (
            lambda r, b: next(iter(r.players.values())).update(role="music_assistant"),
            "pause",
            "music_assistant_required",
        ),
        (lambda r, b: b.players["org.mpris.MediaPlayer2.vlc"].update(owner=":1.77"), "pause", "not_ready"),
        (lambda r, b: None, "next", "unsupported"),
        (
            lambda r, b: b.players["org.mpris.MediaPlayer2.vlc"]["player"].update(CanPause=False),
            "pause",
            "unsupported",
        ),
    ],
)
async def test_control_refusals(setup, command, code):
    bus = FakeMpris({"org.mpris.MediaPlayer2.vlc": player(":1.10")})
    registry = MprisRegistry(bus)
    await registry.initialize()
    (key,) = registry.players
    setup(registry, bus)
    with pytest.raises(BridgeError) as raised:
        await registry.control(key, command)
    assert raised.value.code == code
    assert "Pause" not in bus.calls


async def test_control_not_confirmed(monkeypatch):
    from bigscreen_bridge import mpris

    bus = FakeMpris({"org.mpris.MediaPlayer2.vlc": player(":1.10")})
    registry = MprisRegistry(bus)
    await registry.initialize()
    (key,) = registry.players
    bus.obey = False
    real_sleep = asyncio.sleep
    monkeypatch.setattr(mpris.asyncio, "sleep", lambda _: real_sleep(0))
    with pytest.raises(BridgeError) as raised:
        await registry.control(key, "pause")
    assert raised.value.code == "state_not_confirmed"


async def test_list_names_failure_leaves_no_players():
    bus = FakeMpris({"org.mpris.MediaPlayer2.vlc": player(":1.10")})
    registry = MprisRegistry(bus)
    await registry.initialize()
    registry.bus = None
    assert await registry.refresh() == [] and registry.players == {}
