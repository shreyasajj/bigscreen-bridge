import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bigscreen_bridge.adapters import APPS, BridgeError
from bigscreen_bridge.flimmer import FlimmerHook, validate_channel, validate_page
from custom_components.bigscreen_bridge.media_player import BigscreenPlayer
from dbus_next import MessageType
from homeassistant.components.media_player import MediaPlayerEntityFeature, MediaPlayerState
from homeassistant.exceptions import HomeAssistantError

CHANNEL = "channel-" + "a" * 64


def hook():
    return SimpleNamespace(
        enabled=True,
        refresh=AsyncMock(),
        state={
            "protocol": 1,
            "ready": True,
            "capabilities": ["pause", "stop"],
            "media": {
                "state": "playing",
                "channel": {
                    "id": CHANNEL,
                    "name": "News",
                    "current": {"title": "Current programme"},
                    "next": {"title": "Next programme"},
                },
            },
        },
        open=AsyncMock(return_value={"delivery": "player_accepted"}),
        control=AsyncMock(return_value={"delivery": "player_accepted"}),
        browse=AsyncMock(
            return_value={"items": [{"id": CHANNEL, "name": "News"}], "total": 1, "next_offset": None}
        ),
    )


@pytest.mark.parametrize("value", [None, True, "https://provider/secret", "a", "channel-" + "x" * 64])
def test_invalid_channel(value):
    with pytest.raises(BridgeError):
        validate_channel(value)
    assert validate_channel(CHANNEL) == CHANNEL


@pytest.mark.parametrize("offset,limit", [(True, 1), (-1, 1), (0, 51), (0, 0), (0, False), (0.5, 2)])
def test_invalid_pages(offset, limit):
    with pytest.raises(BridgeError):
        validate_page(offset, limit)


async def test_channel_open_browse_and_flags(bridge):
    b, _ = bridge
    b.flimmer = hook()
    b.wm.windows = [{"id": "iptv", "desktop": APPS[2].flatpak, "active": True}]
    app = next(a for a in b.snapshot()["apps"] if a["id"] == "flimmer")
    assert app["content_types"] == ["flimmer_channel"]
    assert app["capabilities"] == ["focus", "pause", "stop"]
    await b.execute(
        dict(id="open", command="open", app="flimmer", content_type="channel", content_id=CHANNEL)
    )
    b.flimmer.open.assert_awaited_once_with("open", CHANNEL)
    await b.execute(dict(id="pause", command="pause", app="flimmer"))
    b.flimmer.control.assert_awaited_once_with("pause", "pause")
    result = await b.execute(dict(id="browse", command="browse", app="flimmer", offset=50, limit=25))
    assert result["items"][0]["id"] == CHANNEL
    b.flimmer.browse.assert_awaited_once_with(50, 25)
    with pytest.raises(BridgeError):
        await b.execute(dict(id="seek", command="seek", app="flimmer", position=0))


async def test_invalid_links_never_focus(bridge):
    b, _ = bridge
    b.flimmer = hook()
    for extra in ({"content_id": "https://provider/secret"}, {"resume": True}, {"content_type": "movie"}):
        request = dict(id="bad", command="open", app="flimmer", content_type="channel", content_id=CHANNEL)
        request.update(extra)
        with pytest.raises(BridgeError):
            await b.execute(request)
    assert not b.wm.focused
    b.flimmer.open.assert_not_awaited()


async def test_native_entity_channel_metadata_and_paginated_browse(bridge):
    b, _ = bridge
    b.flimmer = hook()
    b.wm.windows = [{"id": "iptv", "desktop": APPS[2].flatpak, "active": True}]
    coordinator = SimpleNamespace(
        data=b.snapshot(),
        last_update_success=True,
        config_entry=None,
        async_contexts=lambda: (),
        command=AsyncMock(),
    )
    active, app = BigscreenPlayer(coordinator), BigscreenPlayer(coordinator, "flimmer")
    assert active.state == app.state == MediaPlayerState.PLAYING
    assert app.media_title == "Current programme" and app.media_channel == "News"
    assert app.media_content_id == CHANNEL and app.media_content_type == "channel"
    assert app.media_duration is None and app.media_position is None and app.media_image_url is None
    assert app.supported_features & MediaPlayerEntityFeature.BROWSE_MEDIA
    assert app.supported_features & MediaPlayerEntityFeature.PAUSE
    assert not app.supported_features & (MediaPlayerEntityFeature.SEEK | MediaPlayerEntityFeature.VOLUME_SET)
    await app.async_play_media("flimmer_channel", CHANNEL)
    coordinator.command.assert_awaited_with("open", app="flimmer", content_type="channel", content_id=CHANNEL)
    await app.async_media_pause()
    coordinator.command.assert_awaited_with("pause", app="flimmer")
    coordinator.command.return_value = {"items": [{"id": CHANNEL, "name": "News"}], "next_offset": 50}
    page = await app.async_browse_media()
    assert page.children[0].media_content_type == "flimmer_channel"
    assert page.children[1].media_content_id == "flimmer:50"
    await active.async_browse_media("flimmer_channels", "flimmer:50")
    coordinator.command.assert_awaited_with("browse", app="flimmer", offset=50, limit=50)
    with pytest.raises(HomeAssistantError):
        await app.async_play_media("stremio_movie", "tt123")
    with pytest.raises(HomeAssistantError):
        await BigscreenPlayer(coordinator, "stremio").async_play_media("flimmer_channel", CHANNEL)
    coordinator.data["active_app"] = "couchside"
    assert not active.supported_features & MediaPlayerEntityFeature.PAUSE


async def test_dbus_response_contract_and_disconnection():
    bus = SimpleNamespace(call=AsyncMock())
    native = FlimmerHook(bus)
    bus.call.return_value = SimpleNamespace(
        message_type=MessageType.METHOD_RETURN, body=[json.dumps({"id": "one", "ok": True, "result": {}})]
    )
    assert await native.request("get_state", "one") == {}
    sent = bus.call.call_args[0][0]
    assert sent.destination == "io.github.jclsn.Flimmer.Control"
    with pytest.raises(BridgeError, match="Invalid Flimmer"):
        await native.request("get_state", "mismatched")
    bus.call.return_value = SimpleNamespace(message_type=MessageType.ERROR)
    await native.refresh()
    assert native.state is None


async def test_invalid_catalogue_is_rejected():
    native = FlimmerHook(None, True)
    native.refresh = AsyncMock()
    native.state = {"ready": True}
    native.request = AsyncMock(
        return_value={"items": [{"id": "https://provider/password", "name": "Bad"}], "next_offset": None}
    )
    with pytest.raises(BridgeError, match="Invalid Flimmer channel catalogue"):
        await native.browse()
    native.request.return_value = {"items": [{"id": CHANNEL, "name": "News"}], "next_offset": 0}
    with pytest.raises(BridgeError):
        await native.browse()


async def test_channel_open_waits_for_loading_catalogue():
    native = FlimmerHook(None, True)
    native.wait_ready = AsyncMock()
    native.refresh = AsyncMock()
    native.request = AsyncMock(
        side_effect=[BridgeError("not_ready", "Loading"), {"delivery": "player_accepted"}]
    )
    assert await native.open("cold", CHANNEL) == {"delivery": "player_accepted"}
    assert native.request.await_count == 2
    native.request.assert_awaited_with("select_channel", "cold", channel_id=CHANNEL)
