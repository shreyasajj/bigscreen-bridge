"""Shared Home Assistant test helpers: the coordinator against the in-process bridge,
a stand-in Music Assistant player, and recording LED lights."""
from types import SimpleNamespace
from unittest.mock import Mock

import aiohttp
import pytest
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.core import HomeAssistant

from custom_components.bigscreen_bridge.coordinator import BridgeCoordinator


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


LIGHT_STATE = {
    "ledfx": {"available": True, "reason": None, "service": "active", "version": "2.1.9", "paused": False,
              "virtuals": [{"id": "tv-backlight", "name": "TV backlight", "active": True, "effect": "pitchSpectrum",
                            "effect_name": "Pitch Spectrum", "brightness": 0.8, "color": None},
                           {"id": "desk", "name": "Desk", "active": False, "effect": "singleColor",
                            "effect_name": "Single Color", "brightness": 1.0, "color": "#ff8000"}],
              "devices": {"total": 7, "online": 6, "offline": ["right-contoller"]},
              "scenes": [{"id": "blade-scene", "name": "Blade Scene", "active": False}],
              "effects": [{"type": "fire", "name": "Fire"}, {"type": "pitchSpectrum", "name": "Pitch Spectrum"},
                          {"type": "singleColor", "name": "Single Color"}]},
    "hyperhdr": {"available": True, "reason": None, "service": "active", "output": False,
                 "grabber": "V4L2:USB capture", "last_error": "VIDIOC_QBUF error",
                 "effects": ["Candle", "Rainbow swirl"],
                 "instances": [{"id": 0, "name": "Tv backlight", "running": True},
                               {"id": 1, "name": "Right wall", "running": False}]}}


class RecordingLights:
    enabled = True

    def __init__(self):
        import copy
        self.state = copy.deepcopy(LIGHT_STATE)
        self.calls = []

    async def execute(self, command, request):
        self.calls.append((command, request))
        return {}


def by_key(entities):
    return {entity.key: entity for entity in entities}
