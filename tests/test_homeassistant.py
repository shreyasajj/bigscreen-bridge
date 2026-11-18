"""Tests against real HA core classes; no running HA installation required."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest
from homeassistant.components.media_player import MediaPlayerEntityFeature, MediaPlayerState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.bigscreen_bridge.coordinator import BridgeCoordinator
from custom_components.bigscreen_bridge.media_player import BigscreenPlayer

@pytest.fixture
async def coordinator(bridge, tmp_path, monkeypatch):
    _, http = bridge
    hass = HomeAssistant(str(tmp_path))
    async with aiohttp.ClientSession() as session:
        monkeypatch.setattr("custom_components.bigscreen_bridge.coordinator.async_get_clientsession",
                            lambda _: session)
        monkeypatch.setattr("custom_components.bigscreen_bridge.config_flow.async_get_clientsession",
                            lambda _: session)
        entry = SimpleNamespace(data={"url": str(http.make_url("")), "token": "a" * 48},
                                unique_id="test-device", async_on_unload=Mock())
        coordinator = BridgeCoordinator(hass, entry)
        coordinator.async_set_updated_data(await coordinator._async_update_data())
        yield coordinator
        await coordinator.async_close()
        await coordinator.async_shutdown()


async def test_native_player_browse_sources_and_unsupported_features(coordinator, bridge):
    b, _ = bridge
    player = BigscreenPlayer(coordinator)
    assert player.unique_id == "test-device_active"
    assert player.available
    assert player.state == MediaPlayerState.OFF
    assert player.source_list == ["Couchside", "Stremio TV", "Flimmer", "VacuumTube"]
    assert not player.supported_features & (MediaPlayerEntityFeature.PLAY | MediaPlayerEntityFeature.PAUSE
                                           | MediaPlayerEntityFeature.SEEK | MediaPlayerEntityFeature.VOLUME_SET)
    library = await player.async_browse_media()
    assert len(library.children) == 4
    assert all(item.can_play and item.media_content_type == "app" for item in library.children)
    b.wm.windows = [{"id": "live", "desktop": "io.github.shreyasajj.StremioTV", "active": True}]
    await player.async_select_source("Stremio TV")
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    assert player.source == "Stremio TV"
    assert player.state == MediaPlayerState.IDLE
    await player.async_play_media("app", "stremio")
    assert b.wm.focused == ["live", "live"]
    with pytest.raises(HomeAssistantError):
        await player.async_play_media("movie", "tt0133093")
    with pytest.raises(HomeAssistantError):
        await player.async_select_source("Unknown")
    with pytest.raises(HomeAssistantError):
        await player.async_browse_media("channel", "unknown")


async def test_disconnect_unavailable_and_identity_change(coordinator, bridge):
    b, _ = bridge
    player = BigscreenPlayer(coordinator)
    coordinator._disconnected()
    assert not player.available
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    assert player.available
    await coordinator.client.close()
    b.config["device_id"] = "another-device"
    with pytest.raises(UpdateFailed, match="identity changed"):
        await coordinator._async_update_data()


async def test_auth_rejection_translates_to_ha_reauth(coordinator):
    await coordinator.client.close()
    coordinator.client.token = "wrong"
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_command_error_translates_to_ha_error(coordinator):
    with pytest.raises(HomeAssistantError, match="No verified playback controller"):
        await coordinator.command("pause")


@pytest.mark.parametrize("token,error", [("wrong", "invalid_auth"), ("a" * 48, None)])
async def test_config_flow_checks_live_bridge_identity(coordinator, token, error):
    from unittest.mock import AsyncMock
    from custom_components.bigscreen_bridge.config_flow import ConfigFlow
    flow = ConfigFlow()
    flow.hass = coordinator.hass
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = Mock()
    flow.async_create_entry = Mock(return_value={"type": "create_entry"})
    flow.async_show_form = Mock(side_effect=lambda **kwargs: kwargs)
    result = await flow.async_step_user({"url": coordinator.entry.data["url"], "code": token})
    if error:
        assert result["errors"]["base"] == error
        flow.async_set_unique_id.assert_not_called()
    else:
        flow.async_set_unique_id.assert_awaited_once_with("test-device")
        flow.async_create_entry.assert_called_once()


async def test_config_flow_pairs_with_the_code_shown_on_the_tv(coordinator, bridge, tmp_path):
    from custom_components.bigscreen_bridge.config_flow import ConfigFlow
    from bigscreen_bridge.pairing import Pairing
    b, _ = bridge
    b.pairing = Pairing(tmp_path / "clients.json")
    shown = b.status()["pairing"]["code"]
    flow = ConfigFlow()
    flow.hass = coordinator.hass
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = Mock()
    flow.async_create_entry = Mock(side_effect=lambda **kwargs: kwargs)
    flow.async_show_form = Mock(side_effect=lambda **kwargs: kwargs)
    url = coordinator.entry.data["url"]
    wrong = await flow.async_step_user({"url": url, "code": "2222-2222"})
    assert wrong["errors"]["base"] == "invalid_auth"
    created = await flow.async_step_user({"url": url, "code": shown.lower().replace("-", " ")})
    token = created["data"]["token"]
    assert created["data"] == {"url": url, "token": token} and b.pairing.accepts(token)
    assert b.status()["pairing"] is None   # paired: the TV stops showing a code
    assert list(wrong["data_schema"].schema) == ["url", "code"]


async def test_reauth_rejects_replacement_device(coordinator, bridge):
    from custom_components.bigscreen_bridge.config_flow import ConfigFlow
    b, _ = bridge
    flow = ConfigFlow()
    flow.hass = coordinator.hass
    flow._get_reauth_entry = Mock(return_value=coordinator.entry)
    flow.async_show_form = Mock(side_effect=lambda **kwargs: kwargs)
    flow.async_update_reload_and_abort = Mock(return_value={"type": "abort"})
    b.config["device_id"] = "replacement-device"
    result = await flow.async_step_reauth_confirm({"code": "a" * 48})
    assert result["errors"]["base"] == "wrong_device"
    flow.async_update_reload_and_abort.assert_not_called()


async def test_couchside_typed_navigation_preserves_ma_control(coordinator, bridge):
    b, _ = bridge
    b.wm.windows = [{"id": "music", "desktop": "io.github.shreyasajj.Couchside"}]
    b.couchside = SimpleNamespace(enabled=True, state={"ma_player_id": "local-ma-player"}, refresh=AsyncMock(),
        navigate=AsyncMock(return_value={"view": {"type": "now_playing", "id": ""}}))
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    player = BigscreenPlayer(coordinator)
    await player.async_play_media("couchside_now_playing", "")
    assert b.couchside.navigate.await_args.args[1:] == ("now_playing", "")
    assert player.extra_state_attributes["music_assistant_player_id"] == "local-ma-player"
    assert not player.supported_features & MediaPlayerEntityFeature.PLAY
    with pytest.raises(HomeAssistantError, match="advertise"):
        await player.async_play_media("couchside_radio", "library://radio/1")


MUSIC = (MediaPlayerEntityFeature.PLAY | MediaPlayerEntityFeature.PAUSE | MediaPlayerEntityFeature.STOP
         | MediaPlayerEntityFeature.NEXT_TRACK | MediaPlayerEntityFeature.PREVIOUS_TRACK
         | MediaPlayerEntityFeature.SEEK | MediaPlayerEntityFeature.PLAY_MEDIA)


async def music_assistant(hass, monkeypatch, player_id="local-ma-player", state="playing"):
    """A Music Assistant player entity: its state and services, and the registry lookup that finds it."""
    from homeassistant.util import dt as dt_util
    entity_id = "media_player.tv_music"
    monkeypatch.setattr("custom_components.bigscreen_bridge.media_player.music_assistant_entity_id",
                        lambda _hass, wanted: entity_id if wanted == player_id else None)
    hass.states.async_set(entity_id, state, {"supported_features": int(MUSIC), "media_title": "Be Mine",
                                             "media_artist": "Ofenbach", "media_album_name": "Be Mine",
                                             "media_duration": 180, "media_position": 42,
                                             "media_position_updated_at": dt_util.utcnow(),
                                             "entity_picture": "/api/media_player_proxy/media_player.tv_music?token=t"})
    calls = []
    for service in ("media_play", "media_pause", "media_stop", "media_next_track", "media_previous_track",
                    "media_seek", "play_media"):
        hass.services.async_register("media_player", service,
                                     lambda call: calls.append((call.service, dict(call.data))))
    return entity_id, calls


async def couchside_active(coordinator, bridge):
    b, _ = bridge
    b.wm.windows = [{"id": "music", "desktop": "io.github.shreyasajj.Couchside", "active": True}]
    b.couchside = SimpleNamespace(enabled=True, state={"ma_player_id": "local-ma-player"}, refresh=AsyncMock(),
        navigate=AsyncMock(return_value={"view": {"type": "now_playing", "id": ""}}))
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    player = BigscreenPlayer(coordinator)
    player.hass = coordinator.hass
    return player


async def test_couchside_controls_are_forwarded_to_music_assistant(coordinator, bridge, monkeypatch):
    player = await couchside_active(coordinator, bridge)
    entity_id, calls = await music_assistant(coordinator.hass, monkeypatch)
    assert player.supported_features & MUSIC == MUSIC
    assert player.state == MediaPlayerState.PLAYING
    assert (player.media_title, player.media_artist, player.media_album_name) == ("Be Mine", "Ofenbach", "Be Mine")
    assert (player.media_position, player.media_duration) == (42, 180)
    assert player.extra_state_attributes["music_assistant_entity_id"] == entity_id
    assert player.entity_picture == "/api/media_player_proxy/media_player.tv_music?token=t"
    await player.async_media_pause()
    await player.async_media_play()
    await player.async_media_next_track()
    await player.async_media_previous_track()
    await player.async_media_seek(30)
    await player.async_media_stop()
    assert [c[0] for c in calls] == ["media_pause", "media_play", "media_next_track", "media_previous_track",
                                     "media_seek", "media_stop"]
    assert all(entity_id in str(c[1]["entity_id"]) for c in calls)
    assert calls[4][1]["seek_position"] == 30


async def test_picking_couchside_music_plays_it_and_opens_now_playing(coordinator, bridge, monkeypatch):
    b, _ = bridge
    player = await couchside_active(coordinator, bridge)
    entity_id, calls = await music_assistant(coordinator.hass, monkeypatch)
    await player.async_play_media("couchside_track", "library://track/7")
    assert calls == [("play_media", {"entity_id": entity_id, "media_content_id": "library://track/7",
                                     "media_content_type": "music"})]
    assert b.couchside.navigate.await_args.args[1:] == ("now_playing", "")
    await player.async_play_media("couchside_search", "ofenbach")      # search still only navigates
    assert len(calls) == 1 and b.couchside.navigate.await_args.args[1:] == ("search", "ofenbach")


async def test_without_a_usable_music_assistant_player_couchside_only_navigates(coordinator, bridge, monkeypatch):
    b, _ = bridge
    player = await couchside_active(coordinator, bridge)
    monkeypatch.setattr("custom_components.bigscreen_bridge.media_player.music_assistant_entity_id",
                        lambda _hass, _player: None)                                # no MA integration
    assert not player.supported_features & MediaPlayerEntityFeature.PLAY
    entity_id, calls = await music_assistant(coordinator.hass, monkeypatch, state="unavailable")
    assert not player.supported_features & MediaPlayerEntityFeature.PLAY
    with pytest.raises(HomeAssistantError):
        await player.async_media_pause()
    await player.async_play_media("couchside_track", "library://track/7")
    assert calls == [] and b.couchside.navigate.await_args.args[1:] == ("track", "library://track/7")


async def test_another_active_app_is_not_forwarded(coordinator, bridge, monkeypatch):
    b, _ = bridge
    player = await couchside_active(coordinator, bridge)
    await music_assistant(coordinator.hass, monkeypatch)
    b.wm.windows = [{"id": "live", "desktop": "io.github.shreyasajj.StremioTV", "active": True}]
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    assert player.media_title != "Be Mine"
    assert not player.supported_features & MediaPlayerEntityFeature.NEXT_TRACK


async def test_music_assistant_changes_show_on_the_tv_player_at_once(coordinator, bridge, monkeypatch):
    player = await couchside_active(coordinator, bridge)
    entity_id, _ = await music_assistant(coordinator.hass, monkeypatch)
    written = []
    player.async_write_ha_state = lambda: written.append(player.state)
    monkeypatch.setattr(coordinator.config_entry, "pref_disable_polling", False, raising=False)
    await player.async_added_to_hass()
    coordinator.hass.states.async_set(entity_id, "paused", {"supported_features": int(MUSIC)})
    coordinator.hass.states.async_set("media_player.something_else", "playing")
    await coordinator.hass.async_block_till_done()
    assert written == [MediaPlayerState.PAUSED]
    await player.async_will_remove_from_hass()
