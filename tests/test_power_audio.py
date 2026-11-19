from types import SimpleNamespace

import pytest
from bigscreen_bridge.adapters import BridgeError
from bigscreen_bridge.audio import AudioManager, parse_streams, parse_volume
from bigscreen_bridge.power import PowerManager, blocked_inhibitors, parse_dpms
from dbus_next import MessageType, Variant


class Bus:
    def __init__(self):
        self.calls = []
        self.auth = "yes"
        self.inhibitors = []
        self.locked = False
        self.sleeping = False
        self.handlers = []

    def add_message_handler(self, handler):
        self.handlers.append(handler)

    async def call(self, message):
        self.calls.append(message)
        if message.member.startswith("Can"):
            body = [self.auth]
        elif message.member == "GetNameOwner":
            body = [":1.50"]
        elif message.member == "ListInhibitors":
            body = [self.inhibitors]
        elif message.member == "GetAll":
            body = [
                {
                    "PreparingForSleep": Variant("b", self.sleeping),
                    "PreparingForShutdown": Variant("b", False),
                }
            ]
        elif message.member == "GetActive":
            body = [self.locked]
        elif message.member == "Get":
            body = [Variant("as", ["WAYLAND_DISPLAY=wayland-0", "UNRELATED_SECRET=never-import"])]
        else:
            if message.member == "Lock":
                self.locked = True
            body = []
        return SimpleNamespace(message_type=MessageType.METHOD_RETURN, body=body)


class Display:
    def __init__(self):
        self.mode = "on"
        self.calls = []

    async def __call__(self, *argv, **kwargs):
        self.calls.append(argv)
        if argv[-1] != "show":
            assert argv[-2] == "--dpms"
            assert argv[-1] in ("on", "off")
            self.mode = argv[-1]
        return f"dpms mode for screen HDMI-A-1: {self.mode}\n"


def power():
    bus, display = Bus(), Display()
    manager = PowerManager(
        bus,
        bus,
        {"allowed_system_actions": ["suspend", "shutdown", "restart"]},
        runner=display,
        display_command="/usr/bin/kscreen-doctor",
    )
    return manager, bus, display


def test_dpms_and_inhibitors_keep_display_and_machine_distinct():
    assert parse_dpms("dpms mode for screen HDMI-A-1: off\n")["display_on"] is False
    assert not parse_dpms("not supported")["supported"]
    rows = [
        ["handle-power-key:handle-suspend-key", "PowerDevil", "handles keys", "block", 1000, 1],
        ["sleep", "NetworkManager", "drain", "delay", 0, 2],
        ["sleep:shutdown", "Recorder", "recording", "block", 1000, 3],
    ]
    assert [row["who"] for row in blocked_inhibitors(rows, "sleep")] == ["Recorder"]


async def test_power_checks_authorization_and_inhibitors_before_fixed_logind_method():
    manager, bus, _ = power()
    await manager.initialize()
    assert manager.env["WAYLAND_DISPLAY"] == "wayland-0"
    assert "UNRELATED_SECRET" not in manager.env
    assert "suspend" in manager.state["capabilities"]
    bus.auth = "challenge"
    with pytest.raises(BridgeError) as error:
        await manager.submit_system("suspend")
    assert error.value.code == "authorization_required"
    assert not any(message.member == "Suspend" for message in bus.calls)
    bus.auth = "yes"
    bus.inhibitors = [["sleep", "Recorder", "recording", "block", 1000, 1]]
    with pytest.raises(BridgeError) as error:
        await manager.submit_system("suspend")
    assert error.value.code == "inhibited"
    bus.inhibitors = []
    result = await manager.submit_system("suspend")
    assert result["status"] == "submitted" and result["completion_confirmed"] is False
    sent = next(message for message in bus.calls if message.member == "Suspend")
    assert sent.signature == "b" and sent.body == [False]


async def test_display_control_is_idempotent_and_never_requests_system_power():
    manager, bus, display = power()
    await manager.initialize()
    first = await manager.display(False)
    second = await manager.display(False)
    assert first["completion_confirmed"] and first["display_on"] is False
    assert first["physical_tv_power"] == "unknown"
    assert second["changed"] is False
    assert sum(argv[-1] == "off" for argv in display.calls) == 1
    assert not any(message.member in ("Suspend", "PowerOff", "Reboot") for message in bus.calls)
    assert (await manager.display(True))["display_on"] is True


async def test_power_signal_sender_and_busy_state():
    manager, bus, _ = power()
    await manager.initialize()
    message = SimpleNamespace(
        message_type=MessageType.SIGNAL,
        sender=":fake",
        interface="org.freedesktop.login1.Manager",
        path="/org/freedesktop/login1",
        member="PrepareForSleep",
        body=[True],
    )
    manager._signal(message)
    assert manager.state["system_state"] == "awake"
    message.sender = ":1.50"
    manager._signal(message)
    assert manager.state["system_state"] == "preparing_sleep"
    bus.sleeping = True
    with pytest.raises(BridgeError) as error:
        await manager.submit_system("shutdown")
    assert error.value.code == "busy"


def test_audio_parser_does_not_equate_uncorked_stream_with_nonzero_audio():
    assert parse_volume("Volume: 0.50 [MUTED]\n")["muted"] is True
    rows = [
        {
            "index": 1,
            "corked": False,
            "mute": False,
            "volume": {"front-left": {"value_percent": "50%"}},
            "properties": {"application.id": "org.sendspin.cli", "application.name": "Sendspin"},
        }
    ]
    stream = parse_streams(rows)[0]
    assert stream["role"] == "sendspin" and stream["app"] == "couchside"
    assert stream["stream_active"] is True and stream["producing_audio"] is None


async def test_explicit_global_volume_and_mute_are_confirmed_without_toggle():
    calls = []
    level, muted = 1.0, False

    async def runner(*argv):
        nonlocal level, muted
        calls.append(argv)
        if argv[1] == "get-volume":
            return f"Volume: {level:.2f}" + (" [MUTED]" if muted else "")
        if argv[1] == "set-volume":
            level = float(argv[-1])
        elif argv[1] == "set-mute":
            assert argv[-1] in ("0", "1")
            muted = argv[-1] == "1"
        return ""

    audio = AudioManager(runner=runner, wpctl="/usr/bin/wpctl", pactl="")
    audio.pactl = None
    assert (await audio.execute("set_volume", 0.25))["completion_confirmed"]
    assert (await audio.execute("mute"))["muted"]
    assert (await audio.execute("mute"))["changed"] is False
    assert (await audio.execute("unmute"))["muted"] is False
    assert not any("toggle" in argv for argv in calls)
    for level in [True, 2, float("inf")]:
        with pytest.raises(BridgeError):
            await audio.execute("set_volume", level)
