"""Tests against real HA core classes; no running HA installation required."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest
from homeassistant.components.media_player import MediaPlayerEntityFeature, MediaPlayerState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.bigscreen_bridge.media_player import BigscreenPlayer
from ha_helpers import MUSIC, RecordingLights, by_key, music_assistant

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


async def test_bridge_status_entities(coordinator, bridge, recorder):
    from custom_components.bigscreen_bridge import binary_sensor, sensor
    b, _ = bridge
    b.diagnostics = recorder
    recorder.started(8765)                       # as serve() does
    await coordinator.refresh_diagnostics()
    diagnostics = recorder.to_dict(b)
    sensors = by_key(sensor.entities(coordinator))
    assert sensors["version"].native_value == diagnostics["service"]["version"]
    assert sensors["started"].native_value.isoformat().startswith(diagnostics["service"]["started_at"][:19])
    assert sensors["clients"].native_value == 1
    assert sensors["discovery"].native_value in ("announced", "not_announced", "disabled")
    assert sensors["mqtt"].native_value == "not_configured"
    assert sensors["focused_app"].native_value == "Other window"
    assert sensors["windows"].native_value == 0
    assert sensors["warnings"].native_value == 0
    assert sensors["version"].entity_category.value == "diagnostic"
    binaries = by_key(binary_sensor.entities(coordinator))
    assert binaries["health"].is_on is (diagnostics["service"]["health"] != "ok")
    assert "ledfx_service" not in sensors and "ledfx_service" not in binaries   # this bridge has no lights


async def test_light_entities_and_their_commands(coordinator, bridge):
    from custom_components.bigscreen_bridge import binary_sensor, light, scene, sensor, switch
    b, _ = bridge
    b.lights = RecordingLights()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    lights = by_key(light.entities(coordinator))
    tv = lights["ledfx_tv-backlight"]
    assert tv.name == "LedFx TV backlight" and tv.is_on and tv.brightness == 204
    assert tv.effect == "Pitch Spectrum" and tv.effect_list == ["Fire", "Pitch Spectrum", "Single Color"]
    assert lights["ledfx_desk"].rgb_color == (255, 128, 0) and not lights["ledfx_desk"].is_on
    await tv.async_turn_on(brightness=128, rgb_color=(0, 255, 0), effect="Fire")
    await tv.async_turn_off()
    wall = lights["hyperhdr_1"]
    assert wall.name == "HyperHDR Right wall" and not wall.is_on and wall.effect_list[0] == "Video"
    await wall.async_turn_on(rgb_color=(255, 0, 0), brightness=255)
    await wall.async_turn_on(effect="Video")
    await wall.async_turn_off()
    playing = by_key(switch.entities(coordinator))["ledfx_playing"]
    assert playing.is_on
    await playing.async_turn_off()
    blade = by_key(scene.entities(coordinator))["ledfx_scene_blade-scene"]
    assert blade.name == "LedFx Blade Scene"
    await blade.async_activate()
    assert b.lights.calls == [
        ("ledfx_light", {"virtual": "tv-backlight", "on": True, "effect": "fire", "brightness": 0.502, "color": "#00ff00"}),
        ("ledfx_light", {"virtual": "tv-backlight", "on": False}),
        ("hyperhdr_light", {"instance": 1, "on": True, "color": [255, 0, 0], "brightness": 1.0}),
        ("hyperhdr_light", {"instance": 1, "on": True, "effect": "Video"}),
        ("hyperhdr_light", {"instance": 1, "on": False}),
        ("ledfx_pause", {"paused": True}),
        ("ledfx_scene", {"scene": "blade-scene", "active": True})]
    binaries = by_key(binary_sensor.entities(coordinator))
    assert binaries["ledfx_service"].is_on and binaries["hyperhdr_service"].is_on
    assert binaries["hyperhdr_output"].is_on is False
    sensors = by_key(sensor.entities(coordinator))
    assert sensors["ledfx_devices"].native_value == 6
    assert sensors["ledfx_devices"].extra_state_attributes == {"total": 7, "offline": ["right-contoller"]}
    assert sensors["hyperhdr_grabber"].native_value == "V4L2:USB capture"
    assert sensors["hyperhdr_grabber"].extra_state_attributes == {"last_error": "VIDIOC_QBUF error"}


async def test_a_stopped_light_service_makes_its_entities_unavailable(coordinator, bridge):
    from custom_components.bigscreen_bridge import binary_sensor, light
    b, _ = bridge
    b.lights = RecordingLights()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    lights = by_key(light.entities(coordinator))
    b.lights.state["ledfx"] = {"available": False, "reason": "service_inactive", "service": "inactive"}
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    assert not lights["ledfx_tv-backlight"].available and lights["hyperhdr_0"].available
    service = by_key(binary_sensor.entities(coordinator))["ledfx_service"]
    assert service.available and service.is_on is False


async def test_downloaded_diagnostics_never_hold_the_token(coordinator, bridge, recorder):
    from custom_components.bigscreen_bridge.diagnostics import async_get_config_entry_diagnostics
    b, _ = bridge
    b.diagnostics = recorder
    await coordinator.refresh_diagnostics()
    coordinator.entry.runtime_data = coordinator
    report = await async_get_config_entry_diagnostics(coordinator.hass, coordinator.entry)
    assert "a" * 48 not in str(report)
    assert report["bridge_diagnostics"]["service"]["version"] and report["state"]["device_id"] == "test-device"


def config_entry(http, token="a" * 48):
    from types import MappingProxyType
    from homeassistant.config_entries import ConfigEntry, ConfigEntryState
    entry = ConfigEntry(data={"url": str(http.make_url("")), "token": token}, discovery_keys=MappingProxyType({}),
                        domain="bigscreen_bridge", minor_version=1, options={}, source="user",
                        subentries_data=None, title="TV", unique_id="test-device", version=1,
                        state=ConfigEntryState.SETUP_IN_PROGRESS)
    return entry


@pytest.fixture
async def hass_with_entries(tmp_path, monkeypatch):
    hass = HomeAssistant(str(tmp_path))
    session = aiohttp.ClientSession()
    monkeypatch.setattr("custom_components.bigscreen_bridge.coordinator.async_get_clientsession", lambda _: session)
    hass.config_entries = SimpleNamespace(async_forward_entry_setups=AsyncMock(),
                                          async_unload_platforms=AsyncMock(return_value=True),
                                          async_reload=AsyncMock())
    yield hass
    await session.close()


async def test_setup_entry_forwards_every_platform_and_unload_closes(bridge, hass_with_entries):
    from custom_components.bigscreen_bridge import PLATFORMS, async_setup_entry, async_unload_entry
    from homeassistant.const import Platform
    _, http = bridge
    hass, entry = hass_with_entries, config_entry(http)
    assert await async_setup_entry(hass, entry)
    hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(entry, PLATFORMS)
    assert set(PLATFORMS) == {Platform.MEDIA_PLAYER, Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SWITCH,
                              Platform.LIGHT, Platform.SCENE}
    client = entry.runtime_data.client
    assert not client.ws.closed
    assert await async_unload_entry(hass, entry)
    assert client.ws.closed


async def test_setup_with_a_revoked_token_asks_for_reauth(bridge, hass_with_entries):
    from custom_components.bigscreen_bridge import async_setup_entry
    _, http = bridge
    with pytest.raises(ConfigEntryAuthFailed):
        await async_setup_entry(hass_with_entries, config_entry(http, token="b" * 48))
    hass_with_entries.config_entries.async_forward_entry_setups.assert_not_awaited()


async def test_setup_closes_the_connection_when_platforms_fail(bridge, hass_with_entries):
    from custom_components.bigscreen_bridge import async_setup_entry
    _, http = bridge
    entry = config_entry(http)
    hass_with_entries.config_entries.async_forward_entry_setups.side_effect = RuntimeError("platform broke")
    with pytest.raises(RuntimeError):
        await async_setup_entry(hass_with_entries, entry)
    assert entry.runtime_data.client.ws.closed


async def test_unload_refused_keeps_the_connection(bridge, hass_with_entries):
    from custom_components.bigscreen_bridge import async_setup_entry, async_unload_entry
    _, http = bridge
    entry = config_entry(http)
    await async_setup_entry(hass_with_entries, entry)
    hass_with_entries.config_entries.async_unload_platforms.return_value = False
    assert not await async_unload_entry(hass_with_entries, entry)
    assert not entry.runtime_data.client.ws.closed
    await entry.runtime_data.async_close()


async def test_options_change_reloads_the_entry(bridge, hass_with_entries):
    from custom_components.bigscreen_bridge import async_reload_entry
    _, http = bridge
    entry = config_entry(http)
    await async_reload_entry(hass_with_entries, entry)
    hass_with_entries.config_entries.async_reload.assert_awaited_once_with(entry.entry_id)
