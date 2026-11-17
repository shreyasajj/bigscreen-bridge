from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.media_player import MediaPlayerEntityFeature, MediaPlayerState
from homeassistant.exceptions import HomeAssistantError

from bigscreen_bridge.adapters import APPS, BridgeError
from bigscreen_bridge.stremio import validate_content, validate_seek
from custom_components.bigscreen_bridge.media_player import BigscreenPlayer

@pytest.mark.parametrize(
    "kind,value",
    [
        ("movie", "tt0133093"),
        ("series", "kitsu:42"),
        ("episode", {"series_id": "tt0944947", "video_id": "tt0944947:1:2"}),
    ],
)
def test_content_contract(kind, value):
    assert validate_content(kind, value) == (kind, value)


@pytest.mark.parametrize(
    "kind,value",
    [
        ("movie", "https://example.org"),
        ("movie", "tt1/../a"),
        ("episode", {"series_id": "tt1", "video_id": "tt1:1:2", "url": "file:///tmp/movie"}),
        ("episode", "tt1:1:2"),
        ("unknown", "tt1"),
    ],
)
def test_invalid_content_contract(kind, value):
    with pytest.raises(BridgeError):
        validate_content(kind, value)


@pytest.mark.parametrize("position", [-1, float("nan"), float("inf"), True, "5", 10**400])
def test_invalid_seek(position):
    with pytest.raises(BridgeError):
        validate_seek(position)


def hook():
    return SimpleNamespace(
        enabled=True,
        content_types=["movie", "series", "episode"],
        refresh=AsyncMock(),
        state={
            "ready": True,
            "capabilities": ["play", "pause", "stop", "seek"],
            "media": {
                "state": "playing",
                "title": "Episode",
                "type": "series",
                "episode": "S02E03 — Title",
                "position": 42,
                "duration": 120,
                "poster": "https://example.org/poster.jpg",
                "updated_at": "2026-11-16T12:00:00Z",
                "content_id": "tt123",
            },
        },
        open=AsyncMock(return_value={"delivery": "detail", "type": "movie", "id": "tt123"}),
        control=AsyncMock(return_value={"command": "pause"}),
    )


async def test_stremio_snapshot_and_controls(bridge):
    b, _ = bridge
    b.stremio = hook()
    b.wm.windows = [{"id": "stremio", "desktop": APPS[1].flatpak, "active": True}]
    state = b.snapshot()
    app = next(a for a in state["apps"] if a["id"] == "stremio")
    assert app["state"] == "playing" and app["media"]["title"] == "Episode"
    assert app["capabilities"] == ["focus", "play", "pause", "stop", "seek"]
    await b.execute({"id": "pause", "command": "pause", "app": "stremio"})
    b.stremio.control.assert_awaited_once_with("pause", "pause")
    result = await b.execute(
        {"id": "movie", "command": "open", "app": "stremio", "content_type": "movie", "content_id": "tt123"}
    )
    assert result["delivery"] == "detail"
    b.stremio.open.assert_awaited_once_with("movie", "movie", "tt123", False)
    b.stremio.state["media"] = {"state": "idle"}
    assert next(a for a in b.snapshot()["apps"] if a["id"] == "stremio")["capabilities"] == ["focus"]


async def test_stremio_bad_content_and_seek_never_touch_player(bridge):
    b, _ = bridge
    b.stremio = hook()
    for req in [
        dict(id="one", command="open", app="stremio", content_type="movie", content_id="https://evil"),
        dict(id="two", command="seek", app="stremio", position=-1),
    ]:
        with pytest.raises(BridgeError):
            await b.execute(req)
    b.stremio.control.assert_not_awaited()
    b.stremio.open.assert_not_awaited()
    assert not b.wm.focused


async def test_native_entity_metadata_flags_and_owner_switch(bridge):
    b, _ = bridge
    b.stremio = hook()
    b.wm.windows = [{"id": "stremio", "desktop": APPS[1].flatpak, "active": True}]
    coordinator = SimpleNamespace(
        data=b.snapshot(),
        last_update_success=True,
        config_entry=None,
        async_contexts=lambda: (),
        command=AsyncMock(),
    )
    active = BigscreenPlayer(coordinator)
    app = BigscreenPlayer(coordinator, "stremio")
    assert active.state == app.state == MediaPlayerState.PLAYING
    assert app.unique_id == "test-device_stremio" and app.media_position == 42 and app.media_duration == 120
    assert (
        app.media_season == 2
        and app.media_episode == 3
        and app.media_image_url == "https://example.org/poster.jpg"
    )
    assert app.media_position_updated_at.utcoffset().total_seconds() == 0
    assert app.supported_features & MediaPlayerEntityFeature.SEEK
    assert not app.supported_features & MediaPlayerEntityFeature.VOLUME_SET
    await app.async_media_pause()
    coordinator.command.assert_awaited_with("pause", app="stremio")
    await app.async_play_media(
        "stremio_episode", '{"series_id":"tt123","video_id":"tt123:2:3"}', extra={"resume": True}
    )
    coordinator.command.assert_awaited_with(
        "open",
        app="stremio",
        content_type="episode",
        content_id={"series_id": "tt123", "video_id": "tt123:2:3"},
        resume=True,
    )
    coordinator.data["active_app"] = "couchside"
    assert not active.supported_features & MediaPlayerEntityFeature.PLAY
    assert app.supported_features & MediaPlayerEntityFeature.PLAY
    assert app.source == "Stremio TV" and app.source_list is None
    with pytest.raises(HomeAssistantError):
        await active.async_media_pause()
    with pytest.raises(HomeAssistantError):
        await app.async_play_media("couchside_search", "Music")
