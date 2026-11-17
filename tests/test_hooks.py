"""The shared adapter contract: change announcements, polling fallback and uniform routing."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from dbus_next import MessageType
from homeassistant.components.media_player import MediaPlayerEntityFeature, MediaPlayerState
from homeassistant.exceptions import HomeAssistantError

from bigscreen_bridge.adapters import APPS, BridgeError
from bigscreen_bridge.flimmer import FlimmerHook
from bigscreen_bridge.server import ADAPTERS
from bigscreen_bridge.sessions import SessionController
from bigscreen_bridge.stremio import BUS_NAME, OBJECT_PATH, StremioHook
from custom_components.bigscreen_bridge.media_player import BigscreenPlayer


def signal(**fields):
    defaults = dict(message_type=MessageType.SIGNAL, sender=":1.5", interface=BUS_NAME,
                    path=OBJECT_PATH, member="StateChanged", body=[])
    return SimpleNamespace(**{**defaults, **fields})


async def test_app_announcement_replaces_polling():
    hook = StremioHook(SimpleNamespace(call=AsyncMock(), add_message_handler=Mock()), True)
    hook.on_change = Mock()
    await hook.subscribe()
    rules = [call.args[0].body[0] for call in hook.bus.call.await_args_list]
    assert any("member='StateChanged'" in rule and f"sender='{BUS_NAME}'" in rule for rule in rules)
    hook.request = AsyncMock(return_value={"protocol": 1, "content_types": ["movie"], "media": {}, "ready": True})
    await hook.refresh()
    assert hook.stale  # never announced anything, so it still has to be polled
    hook._signal(signal())
    assert hook.pushed and hook.stale
    hook.on_change.assert_called_once()
    await hook.refresh()
    assert not hook.stale and hook.content_types == ["movie"]


async def test_unrelated_signals_and_owner_changes():
    hook = StremioHook(None, True)
    hook.on_change = Mock()
    for other in (signal(member="Other"), signal(path="/elsewhere"), signal(interface="org.example"),
                  signal(message_type=MessageType.METHOD_CALL)):
        hook._signal(other)
    hook.on_change.assert_not_called()
    hook.dirty = False
    hook._signal(signal(sender="org.freedesktop.DBus", interface="org.freedesktop.DBus",
                        member="NameOwnerChanged", body=[BUS_NAME, ":1.5", ""]))
    assert hook.dirty and not hook.pushed
    hook.on_change.assert_called_once()


async def test_observer_rereads_only_sources_that_changed():
    quiet, noisy, legacy = (SimpleNamespace(state=None, refresh=AsyncMock(), stale=stale)
                            for stale in (False, True, True))
    del legacy.stale  # hooks without the attribute are always polled
    registry = SimpleNamespace(players={}, overflow=False, dirty=False, refresh=AsyncMock())
    bridge = SimpleNamespace(lock=asyncio.Lock(), stremio=quiet, flimmer=noisy, vacuumtube=legacy)
    sessions = SessionController(bridge, registry)
    await sessions.observe(full=False)
    quiet.refresh.assert_not_awaited()
    registry.refresh.assert_not_awaited()
    noisy.refresh.assert_awaited_once()
    legacy.refresh.assert_awaited_once()
    await sessions.observe()
    quiet.refresh.assert_awaited_once()
    registry.refresh.assert_awaited_once()


@pytest.mark.parametrize("app", list(ADAPTERS))
async def test_every_adapter_rejects_unknown_content_before_focus(bridge, app):
    b, _ = bridge
    setattr(b, app, SimpleNamespace(enabled=True, state={"ready": True}, content_types=list(ADAPTERS[app].kinds),
                                    refresh=AsyncMock(), open=AsyncMock(), navigate=AsyncMock()))
    for request in (dict(content_type="nonsense", content_id="x"), dict(content_type=None, content_id=None)):
        with pytest.raises(BridgeError):
            await b.execute(dict(id="bad", command="open", app=app, **request))
    assert not b.wm.focused


async def test_idle_player_advertises_volume_but_not_transport(bridge):
    b, _ = bridge
    b.vacuumtube = SimpleNamespace(enabled=True, content_types=["video"], refresh=AsyncMock(), state={
        "ready": True, "capabilities": ["play", "pause", "seek", "volume_set"], "media": {"state": "idle"}})
    app = next(a for a in b.snapshot()["apps"] if a["id"] == "vacuumtube")
    assert app["capabilities"] == ["focus", "volume_set"]


async def test_flimmer_hook_shares_the_base_control_path():
    native = FlimmerHook(None, True)
    native.request = AsyncMock(side_effect=[
        {"protocol": 1, "ready": True, "content_types": ["channel"], "media": {}, "capabilities": ["pause"]},
        {"delivery": "player_accepted"},
        BridgeError("not_ready", "gone")])
    assert await native.control("one", "pause") == {"delivery": "player_accepted"}
    assert native.state is None and native.enabled
    native.request = AsyncMock(return_value={"protocol": 1, "ready": True, "content_types": ["channel"],
                                             "media": {}, "capabilities": []})
    with pytest.raises(BridgeError, match="does not support"):
        await native.control("two", "pause")


async def test_device_player_owns_system_volume_and_display(bridge):
    b, _ = bridge
    b.wm.windows = [{"id": "youtube", "desktop": APPS[3].flatpak, "active": True}]
    b.vacuumtube = SimpleNamespace(enabled=True, content_types=["video"], refresh=AsyncMock(), state={
        "ready": True, "capabilities": ["pause", "volume_set"],
        "media": {"state": "playing", "volume": 0.9, "muted": False}})
    data = b.snapshot()
    data["audio"] = {"available": True, "capabilities": ["mute", "unmute", "set_volume"], "volume": 0.4, "muted": True}
    data["power"] = {"capabilities": ["screen_off", "screen_on"], "display": {"display_on": True}}
    coordinator = SimpleNamespace(data=data, last_update_success=True, config_entry=None,
                                  async_contexts=lambda: (), command=AsyncMock())
    device, app = BigscreenPlayer(coordinator), BigscreenPlayer(coordinator, "vacuumtube")
    assert device.volume_level == 0.4 and device.is_volume_muted is True and app.volume_level == 0.9
    wanted = (MediaPlayerEntityFeature.VOLUME_SET | MediaPlayerEntityFeature.VOLUME_MUTE
              | MediaPlayerEntityFeature.TURN_OFF | MediaPlayerEntityFeature.TURN_ON)
    assert device.supported_features & wanted == wanted
    assert not app.supported_features & MediaPlayerEntityFeature.TURN_OFF
    await device.async_set_volume_level(0.2)
    coordinator.command.assert_awaited_with("set_volume", volume_level=0.2)
    await device.async_mute_volume(False)
    coordinator.command.assert_awaited_with("unmute")
    await device.async_turn_off()
    coordinator.command.assert_awaited_with("screen_off")
    await app.async_set_volume_level(0.3)
    coordinator.command.assert_awaited_with("volume_set", app="vacuumtube", volume_level=0.3)
    with pytest.raises(HomeAssistantError):
        await app.async_turn_off()
    assert device.state == MediaPlayerState.PLAYING
    data["power"]["display"]["display_on"] = False
    assert device.state == MediaPlayerState.OFF and app.state == MediaPlayerState.PLAYING
    data["audio"] = {"available": False, "capabilities": []}
    assert not device.supported_features & MediaPlayerEntityFeature.VOLUME_SET


def test_position_ticks_are_not_state_changes():
    from bigscreen_bridge.server import significant

    def snap(position, stamp, state="playing", **extra):
        media = {"state": state, "position": position, "updated_at": stamp, "title": "T", **extra}
        return {"apps": [{"id": "stremio", "media": media}], "sessions": [{"id": "app:stremio", "media": dict(media)}]}

    base = significant(snap(10, "2026-11-16T12:00:10Z"))
    assert base == significant(snap(12, "2026-11-16T12:00:12.000Z"))            # normal progress
    assert base != significant(snap(60, "2026-11-16T12:00:12Z"))                # a seek
    assert base != significant(snap(12, "2026-11-16T12:00:12Z", title="Next"))  # new content
    assert base != significant(snap(12, "2026-11-16T12:00:12Z", state="paused"))
    idle = significant(snap(0, "2026-11-16T12:00:10Z", state="idle"))
    assert idle == significant(snap(0, "2026-11-16T12:00:12Z", state="idle"))   # timestamp churn
    assert significant(snap(5, None)) == significant(snap(7, None))             # MPRIS: no anchor
    original = snap(10, "2026-11-16T12:00:10Z")
    significant(original)
    assert original["apps"][0]["media"]["updated_at"]                           # input untouched


async def test_vacuumtube_follows_its_chromium_mpris_player():
    from bigscreen_bridge import mpris as mpris_module
    from bigscreen_bridge.mpris import PATH, PLAYER, MprisRegistry
    from bigscreen_bridge.vacuumtube import VacuumTubeHook

    hook = VacuumTubeHook(True, "/nonexistent/vacuumtube.sock")
    hook.on_change = Mock()
    registry = MprisRegistry(None)
    registry.follow("vacuumtube", hook)
    record = {"id": "mpris::1.9", "owner": ":1.9", "app": "vacuumtube", "state": "playing", "available": True}
    registry._read = AsyncMock(return_value=record)
    original, mpris_module.call = mpris_module.call, AsyncMock(return_value=[["org.mpris.MediaPlayer2.chromium.instance3"]])
    try:
        await registry.refresh()
        assert hook.pushed and hook.dirty  # the player appeared: re-read, then stop polling
        hook.state, hook.dirty = {"ready": True}, False
        assert not hook.stale
        for interface, member, body in (("org.freedesktop.DBus.Properties", "PropertiesChanged", [PLAYER, {}, []]),
                                        (PLAYER, "Seeked", [5000000])):
            hook.dirty = False
            registry._signal(signal(sender=":1.9", path=PATH, interface=interface, member=member, body=body))
            assert hook.stale
        hook.dirty, hook.settle = False, 0.01
        registry._signal(signal(sender=":1.9", path=PATH, interface=PLAYER, member="Seeked", body=[1]))
        hook.dirty = False
        await asyncio.sleep(0.05)
        assert hook.stale  # read again once the renderer has republished its state
        hook.dirty = False
        registry._signal(signal(sender=":1.77", path=PATH, interface=PLAYER, member="Seeked", body=[1]))
        assert not hook.stale  # another application's player is not ours
        hook.state = {"ready": False}
        assert hook.stale  # MPRIS cannot say when Leanback becomes ready
        mpris_module.call = AsyncMock(return_value=[[]])
        await registry.refresh()
        assert not hook.pushed and hook.stale  # player gone: back to polling
    finally:
        mpris_module.call = original
