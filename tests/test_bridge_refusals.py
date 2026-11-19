"""The bridge's refusals: every command it turns down, and why, without touching an app.

The app hooks run against a scripted app that answers each protocol command from a table,
so the hooks' own validation runs. The bridge-level checks run on the in-process bridge.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bigscreen_bridge import server
from bigscreen_bridge.adapters import APPS, BridgeError
from bigscreen_bridge.couchside import CouchsideHook
from bigscreen_bridge.flimmer import FlimmerHook
from bigscreen_bridge.stremio import StremioHook
from bigscreen_bridge.vacuumtube import VacuumTubeHook


async def refused(coroutine, code):
    with pytest.raises(BridgeError) as raised:
        await coroutine
    assert raised.value.code == code, raised.value
    return raised.value


# --- system, audio and power commands ----------------------------------------------------


@pytest.mark.parametrize(
    "request_",
    [
        {"command": "set_volume", "volume_level": 0.5, "app": "stremio"},
        {"command": "mute", "volume_level": 0.5},
        {"command": "pause_all", "app": "x"},
    ],
)
async def test_system_commands_refuse_extra_arguments(bridge, request_):
    b, _ = bridge
    await refused(b.execute({"id": "r", **request_}), "invalid_request")


@pytest.mark.parametrize(
    "command", ["pause_all", "screen_off", "lock_screen", "get_power_state", "mute", "set_volume"]
)
async def test_system_commands_without_their_module(bridge, command):
    b, _ = bridge
    request = {"id": "r", "command": command, **({"volume_level": 0.5} if command == "set_volume" else {})}
    await refused(b.execute(request), "unsupported")


async def test_system_commands_reach_their_module_and_notify(bridge):
    b, _ = bridge
    notified = []
    b.notify = AsyncMock(side_effect=lambda: notified.append(1))
    b.sessions = SimpleNamespace(
        execute=AsyncMock(return_value={"paused": []}), invalidate_app=lambda app: None
    )
    b.audio = SimpleNamespace(execute=AsyncMock(return_value={"volume": 0.5}))
    b.power = SimpleNamespace(
        refresh=AsyncMock(return_value={"display": {}}),
        lock=AsyncMock(return_value={}),
        display=AsyncMock(return_value={}),
    )
    assert await b.execute({"id": "1", "command": "pause_all"}) == {"paused": []}
    assert await b.execute({"id": "2", "command": "set_volume", "volume_level": 0.5}) == {"volume": 0.5}
    await b.execute({"id": "3", "command": "get_power_state"})
    await b.execute({"id": "4", "command": "lock_screen"})
    await b.execute({"id": "5", "command": "screen_on"})
    b.audio.execute.assert_awaited_once_with("set_volume", 0.5)
    b.power.display.assert_awaited_once_with(True)
    assert len(notified) == 5


# --- focus and launching -----------------------------------------------------------------


async def test_focus_refusals(bridge):
    b, _ = bridge
    await refused(b.focus("nope"), "unknown_app")
    b.installed = set()
    await refused(b.focus("stremio"), "not_installed")
    b.installed = {a.flatpak for a in APPS}
    b.wm.ready.clear()
    await refused(b.focus("stremio"), "window_manager_unavailable")


@pytest.fixture
def quick_launch(monkeypatch):
    real_timeout = asyncio.timeout
    monkeypatch.setattr(server.asyncio, "timeout", lambda delay: real_timeout(min(delay, 0.2)))
    running = []

    async def process(*argv):
        return "\n".join(running)

    monkeypatch.setattr(server, "process", process)
    return running


async def test_an_app_that_never_shows_a_window(bridge, quick_launch):
    b, _ = bridge
    launched = []

    async def launcher(*argv):
        launched.append(argv)

    b.launcher = launcher
    await refused(b.focus("couchside"), "readiness_timeout")
    assert launched == [("flatpak", "run", "io.github.shreyasajj.Couchside")]


async def test_a_failed_launch_is_reported(bridge, quick_launch):
    b, _ = bridge

    async def launcher(*argv):
        raise BridgeError("launch_failed", "Application exited unsuccessfully")

    b.launcher = launcher
    await refused(b.focus("couchside"), "launch_failed")


async def test_a_running_app_without_a_window_is_not_launched_twice(bridge, quick_launch):
    b, _ = bridge
    quick_launch.append("io.github.shreyasajj.Couchside")
    b.launcher = AsyncMock()
    await refused(b.focus("couchside"), "readiness_timeout")
    b.launcher.assert_not_awaited()


async def test_window_without_control_endpoint(bridge, quick_launch):
    b, _ = bridge
    b.wm.windows = [{"id": "w", "desktop": "io.github.shreyasajj.StremioTV"}]
    b.stremio = SimpleNamespace(enabled=True, state=None, refresh=AsyncMock(), content_types=[])
    b.launcher = AsyncMock()
    error = await refused(b.focus("stremio"), "readiness_timeout")
    assert "control endpoint" in str(error)


# --- opening content and player commands ------------------------------------------------


async def test_open_refusals_before_anything_moves(bridge):
    b, _ = bridge
    b.stremio = SimpleNamespace(
        enabled=True, state={"ready": True}, refresh=AsyncMock(), content_types=["movie"], open=AsyncMock()
    )
    movie = {
        "id": "r",
        "command": "open",
        "app": "stremio",
        "content_type": "movie",
        "content_id": "tt0000000",
    }
    await refused(b.open_content({**movie, "app": "nope"}), "unsupported")
    await refused(
        b.open_content(
            {
                **movie,
                "app": "couchside",
                "content_type": "album",
                "content_id": "library://album/1",
                "resume": True,
            }
        ),
        "unsupported",
    )
    await refused(b.open_content({**movie, "content_type": "series"}), "unsupported")
    b.installed = set()
    await refused(b.open_content(movie), "not_installed")
    b.installed = {a.flatpak for a in APPS}
    b.wm.ready.clear()
    await refused(b.open_content(movie), "window_manager_unavailable")
    b.stremio.open.assert_not_awaited()
    assert b.wm.focused == []


async def test_a_hook_refusal_is_recorded_and_raised(bridge):
    b, _ = bridge
    b.wm.windows = [{"id": "w", "desktop": "io.github.shreyasajj.StremioTV", "active": True}]
    b.stremio = SimpleNamespace(
        enabled=True,
        state={"ready": True},
        refresh=AsyncMock(),
        content_types=["movie"],
        open=AsyncMock(side_effect=BridgeError("not_found", "No such title")),
        control=AsyncMock(side_effect=BridgeError("not_ready", "Nothing loaded")),
    )
    errors = []
    b.diagnostics.hook_error = lambda hook, exc: errors.append(exc.code)
    await refused(
        b.open_content(
            {
                "id": "r",
                "command": "open",
                "app": "stremio",
                "content_type": "movie",
                "content_id": "tt0000000",
            }
        ),
        "not_found",
    )
    await refused(b.player_command({"id": "r", "command": "pause", "app": "stremio"}), "not_ready")
    assert errors == ["not_found", "not_ready"]


@pytest.mark.parametrize(
    "request_,code",
    [
        ({"command": "pause", "app": "couchside"}, "unsupported"),
        ({"command": "rewind", "app": "stremio"}, "unsupported"),
        ({"command": "seek", "app": "stremio", "position": -1}, "invalid_request"),
        ({"command": "volume_set", "app": "vacuumtube", "volume_level": 2}, "invalid_request"),
        ({"command": "volume_mute", "app": "vacuumtube", "muted": "yes"}, "invalid_request"),
    ],
)
async def test_player_command_refusals(bridge, request_, code):
    b, _ = bridge
    invalidated = []
    b.sessions = SimpleNamespace(invalidate_app=invalidated.append)
    for app in ("stremio", "vacuumtube"):
        setattr(b, app, SimpleNamespace(enabled=True, state={"ready": True}, control=AsyncMock()))
    await refused(b.player_command({"id": "r", **request_}), code)
    assert invalidated == [request_["app"]]
    b.stremio.control.assert_not_awaited()
    b.vacuumtube.control.assert_not_awaited()


async def test_volume_mute_reaches_the_app(bridge):
    b, _ = bridge
    b.vacuumtube = SimpleNamespace(enabled=True, state={"ready": True}, control=AsyncMock(return_value={}))
    await b.player_command({"id": "r", "command": "volume_mute", "app": "vacuumtube", "muted": True})
    b.vacuumtube.control.assert_awaited_once_with("r", "volume_mute", muted=True)


# --- each app hook against a scripted app ------------------------------------------------


def scripted(cls, answers, *args):
    """`cls` with its transport replaced by an app that answers `answers[command]`."""

    class Scripted(cls):
        async def transport(self, payload):
            request = json.loads(payload)
            answer = answers[request["command"]]
            if isinstance(answer, BaseException):
                raise answer
            if callable(answer):
                answer = answer(request)
            return json.dumps({"id": request["id"], **answer})

    hook = Scripted(*args)
    hook.recorder = SimpleNamespace(hook_read=lambda *a: None, hook_signal=lambda *a: None)
    return hook


def ok(result):
    return {"ok": True, "result": result}


STATES = {
    StremioHook: {"protocol": 1, "ready": True, "content_types": ["movie"], "capabilities": [], "media": {}},
    VacuumTubeHook: {
        "protocol": 1,
        "ready": True,
        "content_types": ["video"],
        "capabilities": [],
        "media": {},
        "view": "home",
    },
    FlimmerHook: {
        "protocol": 1,
        "ready": True,
        "content_types": ["channel"],
        "capabilities": [],
        "media": {},
    },
    CouchsideHook: {
        "protocol": 1,
        "ready": True,
        "music_assistant_ready": True,
        "playback_owner": "music_assistant",
        "content_types": ["now_playing", "album"],
        "view": {"type": "home", "id": ""},
        "ma_player_id": "p",
    },
}


def make(cls, answers):
    return scripted(cls, answers, None, True) if cls is not VacuumTubeHook else scripted(cls, answers, True)


@pytest.mark.parametrize(
    "cls,broken",
    [
        (StremioHook, {"media": "nope"}),
        (VacuumTubeHook, {"capabilities": "nope"}),
        (FlimmerHook, {"content_types": ["movie"]}),
        (CouchsideHook, {"playback_owner": "couchside"}),
    ],
)
async def test_a_hook_refuses_an_unknown_protocol(cls, broken):
    hook = make(cls, {"get_state": ok({**STATES[cls], **broken})})
    await hook.refresh()
    assert hook.state is None and hook.enabled


@pytest.mark.parametrize(
    "cls,kind,value",
    [
        (StremioHook, "series", "tt0000000"),
        (VacuumTubeHook, "playlist", "PL0000000000000000"),
        (CouchsideHook, "artist", "library://artist/1"),
    ],
)
async def test_open_a_kind_the_running_app_does_not_offer(cls, kind, value, monkeypatch):
    from bigscreen_bridge import hooks

    real_timeout = asyncio.timeout
    monkeypatch.setattr(hooks.asyncio, "timeout", lambda delay: real_timeout(min(delay, 0.3)))
    hook = make(cls, {"get_state": ok(STATES[cls]), "open": ok({"view": {"type": kind, "id": value}})})
    await hook.refresh()
    code = "readiness_timeout" if cls is VacuumTubeHook else "unsupported"
    deliver = hook.navigate if cls is CouchsideHook else hook.open
    await refused(deliver("r", kind, value), code)


@pytest.mark.parametrize(
    "cls,kind,value", [(StremioHook, "movie", "tt0000000"), (VacuumTubeHook, "video", "aaaaaaaaaaa")]
)
async def test_open_an_offered_kind_asks_the_app_and_rereads(cls, kind, value):
    reads = []
    hook = make(
        cls, {"get_state": lambda r: reads.append(1) or ok(STATES[cls]), "open": ok({"opened": True})}
    )
    await hook.refresh()
    assert await hook.open("r", kind, value) == {"opened": True}
    assert len(reads) >= 3


async def test_an_app_answering_with_another_request_id(monkeypatch):
    hook = make(StremioHook, {"get_state": lambda r: {"id": "someone-else", "ok": True, "result": {}}})
    hook.transport = AsyncMock(return_value=json.dumps({"id": "someone-else", "ok": True, "result": {}}))
    await refused(hook.request("get_state"), "invalid_response")


async def test_an_app_that_never_answers():
    hook = make(StremioHook, {"get_state": TimeoutError()})
    await refused(hook.request("get_state"), hook.timeout_code)


async def test_an_app_that_hangs_runs_out_the_commands_own_deadline():
    hangs = asyncio.Event()

    async def hang(_request):
        await hangs.wait()

    hook = make(StremioHook, {})
    hook.transport = hang
    hook.timeouts = {"open": 0.05}
    async with asyncio.timeout(2):  # the hook's deadline, not this one, must end it
        await refused(hook.request("open"), hook.timeout_code)


def test_now_playing_takes_no_item():
    from bigscreen_bridge.couchside import validate_content

    with pytest.raises(BridgeError) as raised:
        validate_content("now_playing", "library://track/1")
    assert raised.value.code == "invalid_request"
