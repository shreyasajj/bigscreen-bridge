"""Press every Home Assistant control against the in-process bridge, and check what reaches it.

The bridge's command entry point is replaced by a recorder, so these tests check the
whole integration side: entity → coordinator → WebSocket → the bridge's `handle`. The
snapshot advertises every capability, so each control is offered. Every action is then
checked two ways:
- **Offered:** it sends exactly the command in the tables below.
- **Not offered for that player:** it raises HomeAssistantError and sends nothing.

The tables are written out by hand; they are the contract, not a copy of the code.
"""
import copy
import json

import pytest
from bigscreen_bridge.adapters import BridgeError
from custom_components.bigscreen_bridge.media_player import BigscreenPlayer
from ha_helpers import RecordingLights, by_key, music_assistant
from homeassistant.components.media_player import MediaPlayerEntityFeature as F
from homeassistant.exceptions import HomeAssistantError

MEDIA = {"state": "playing", "title": "Test title", "position": 10, "duration": 100}
SNAPSHOT = {
    "type": "state", "protocol": 1, "device_id": "test-device", "name": "TV", "active_app": "stremio",
    "available": True,
    "audio": {"capabilities": ["set_volume", "mute"], "volume": 0.5, "muted": False},
    "power": {"capabilities": ["screen_on", "screen_off"], "display": "on"},
    "apps": [
        {"id": "couchside", "name": "Couchside", "installed": True, "running": True, "capabilities": ["focus"],
         "state": "idle", "browse": False, "playback_owner": "music_assistant", "ma_player_id": "local-ma-player",
         "content_types": ["couchside_now_playing", "couchside_search", "couchside_track", "couchside_album",
                           "couchside_artist", "couchside_playlist"]},
        {"id": "stremio", "name": "Stremio TV", "installed": True, "running": True, "hook_installed": True,
         "control_available": True, "state": "playing", "media": MEDIA, "browse": True,
         "capabilities": ["focus", "play", "pause", "stop", "seek", "volume_set", "volume_mute"],
         "content_types": ["stremio_movie", "stremio_series", "stremio_episode"]},
        {"id": "flimmer", "name": "Flimmer", "installed": True, "running": True, "hook_installed": True,
         "control_available": True, "state": "playing", "media": dict(MEDIA, channel="Test channel"),
         "browse": True, "capabilities": ["focus", "play", "pause", "stop", "volume_set", "volume_mute"],
         "content_types": ["flimmer_channel"]},
        {"id": "vacuumtube", "name": "VacuumTube", "installed": True, "running": True, "hook_installed": True,
         "control_available": True, "state": "playing", "media": MEDIA, "browse": False,
         "capabilities": ["focus", "play", "pause", "seek", "volume_set", "volume_mute"],
         "content_types": ["youtube_video", "youtube_playlist", "youtube_search"]},
    ]}
EPISODE = json.dumps({"series_id": "tt0000000", "video_id": "tt0000000:1:1"})
REFUSED = HomeAssistantError

# (action, arguments) → what the main player sends with Stremio in front. REFUSED: nothing is sent.
MAIN = {
    ("media_play", ()): {"command": "play", "app": "stremio"},
    ("media_pause", ()): {"command": "pause", "app": "stremio"},
    ("media_stop", ()): {"command": "stop", "app": "stremio"},
    ("media_seek", (30,)): {"command": "seek", "app": "stremio", "position": 30},
    ("set_volume_level", (0.4,)): {"command": "set_volume", "volume_level": 0.4},
    ("mute_volume", (True,)): {"command": "mute"},
    ("mute_volume", (False,)): {"command": "unmute"},
    ("media_next_track", ()): REFUSED,
    ("media_previous_track", ()): REFUSED,
    ("select_source", ("VacuumTube",)): {"command": "focus", "app": "vacuumtube"},
    ("select_source", ("Not an app",)): REFUSED,
    ("turn_on", ()): {"command": "screen_on"},
    ("turn_off", ()): {"command": "screen_off"},
    ("play_media", ("app", "couchside")): {"command": "focus", "app": "couchside"},
    ("play_media", ("app", "nope")): REFUSED,
    ("play_media", ("bigscreen_uri", "bigscreen://stremio")): {"command": "open_uri", "uri": "bigscreen://stremio"},
    ("play_media", ("stremio_movie", "tt0000000")):
        {"command": "open", "app": "stremio", "content_type": "movie", "content_id": "tt0000000", "resume": False},
    ("play_media", ("stremio_series", "tt0000000")):
        {"command": "open", "app": "stremio", "content_type": "series", "content_id": "tt0000000", "resume": False},
    ("play_media", ("stremio_episode", EPISODE)):
        {"command": "open", "app": "stremio", "content_type": "episode", "resume": False,
         "content_id": {"series_id": "tt0000000", "video_id": "tt0000000:1:1"}},
    ("play_media", ("stremio_episode", "not json")): REFUSED,
    ("play_media", ("flimmer_channel", "m3u:0000")):
        {"command": "open", "app": "flimmer", "content_type": "channel", "content_id": "m3u:0000"},
    ("play_media", ("youtube_video", "aaaaaaaaaaa")):
        {"command": "open", "app": "vacuumtube", "content_type": "video", "content_id": "aaaaaaaaaaa"},
    ("play_media", ("youtube_playlist", "PL0000")):
        {"command": "open", "app": "vacuumtube", "content_type": "playlist", "content_id": "PL0000"},
    ("play_media", ("youtube_search", "test search")):
        {"command": "open", "app": "vacuumtube", "content_type": "search", "content_id": "test search"},
    ("play_media", ("couchside_album", "library://album/1")):
        {"command": "open", "app": "couchside", "content_type": "album", "content_id": "library://album/1"},
    ("play_media", ("couchside_now_playing", "")):
        {"command": "open", "app": "couchside", "content_type": "now_playing", "content_id": ""},
    ("play_media", ("couchside_radio", "x")): REFUSED,
    ("play_media", ("music", "x")): REFUSED,
}


def app_table(app, content):
    """What an app's own player sends. `content` maps its own media types to the expected `open`."""
    table = {
        ("media_play", ()): {"command": "play", "app": app},
        ("media_pause", ()): {"command": "pause", "app": app},
        ("media_stop", ()): {"command": "stop", "app": app},
        ("media_seek", (30,)): {"command": "seek", "app": app, "position": 30},
        ("set_volume_level", (0.4,)): {"command": "volume_set", "app": app, "volume_level": 0.4},
        ("mute_volume", (True,)): {"command": "volume_mute", "app": app, "muted": True},
        ("media_next_track", ()): REFUSED,
        ("media_previous_track", ()): REFUSED,
        ("select_source", ("Couchside",)): REFUSED,
        ("turn_on", ()): {"command": "focus", "app": app},
        ("turn_off", ()): REFUSED,
        ("play_media", ("app", app)): {"command": "focus", "app": app},
        ("play_media", ("app", "couchside")): REFUSED,
        ("play_media", ("couchside_album", "library://album/1")): REFUSED,
        ("play_media", ("bigscreen_uri", "bigscreen://x")): {"command": "open_uri", "uri": "bigscreen://x", "app": app},
    }
    table.update(content)
    return table


APPS = {
    "stremio": app_table("stremio", {
        ("play_media", ("stremio_movie", "tt0000000")): MAIN[("play_media", ("stremio_movie", "tt0000000"))],
        ("play_media", ("youtube_video", "aaaaaaaaaaa")): REFUSED}),
    "flimmer": app_table("flimmer", {
        ("media_seek", (30,)): {"command": "seek", "app": "flimmer", "position": 30},
        ("play_media", ("flimmer_channel", "m3u:0000")): MAIN[("play_media", ("flimmer_channel", "m3u:0000"))],
        ("play_media", ("stremio_movie", "tt0000000")): REFUSED}),
    "vacuumtube": app_table("vacuumtube", {
        ("play_media", ("youtube_video", "aaaaaaaaaaa")): MAIN[("play_media", ("youtube_video", "aaaaaaaaaaa"))],
        ("play_media", ("flimmer_channel", "m3u:0000")): REFUSED}),
}

# The feature flag that offers each action; an action that is offered must have its flag,
# and a refused playback action must not.
FEATURE = {"media_play": F.PLAY, "media_pause": F.PAUSE, "media_stop": F.STOP, "media_seek": F.SEEK,
           "set_volume_level": F.VOLUME_SET, "mute_volume": F.VOLUME_MUTE, "media_next_track": F.NEXT_TRACK,
           "media_previous_track": F.PREVIOUS_TRACK, "select_source": F.SELECT_SOURCE, "turn_on": F.TURN_ON,
           "turn_off": F.TURN_OFF, "play_media": F.PLAY_MEDIA}
# Not advertised by that app (Flimmer has no seek, VacuumTube no stop), so the control is
# hidden; a direct service call still goes to the bridge, which decides.
NOT_OFFERED = {"flimmer": {"media_seek"}, "vacuumtube": {"media_stop"}}


class Recorder:
    """Stands in for the bridge's command execution; everything above it is real."""

    def __init__(self):
        self.calls = []
        self.refuse = None
        self.answer = None      # a fixed reply instead of the defaults below

    async def __call__(self, request):
        sent = {k: v for k, v in request.items() if k != "id"}
        self.calls.append(sent)
        if self.refuse:
            raise self.refuse
        if self.answer is not None:
            return self.answer
        if sent["command"] == "browse":
            if "app" not in sent:
                return {"items": [{"id": a["id"], "title": a["name"]} for a in SNAPSHOT["apps"]]}
            return {"items": [{"name": "Item", "id": "id-1", "media_content_type": sent["app"] + "_movie"},
                              {"name": "Folder", "path": "folder"}], "next_offset": 50}
        return {}


@pytest.fixture
async def wired(coordinator, bridge):
    b, _ = bridge
    recorder = Recorder()
    b.execute = recorder
    coordinator.async_set_updated_data(copy.deepcopy(SNAPSHOT))
    return coordinator, recorder


def cases(table):
    return [pytest.param(action, args, expected, id=f"{action}{list(args)}") for (action, args), expected in table.items()]


async def press(player, recorder, action, args, expected):
    recorder.calls.clear()
    call = getattr(player, "async_" + action)(*args)
    if expected is REFUSED:
        with pytest.raises(HomeAssistantError):
            await call
        assert recorder.calls == []
    else:
        await call
        assert recorder.calls == [expected]


@pytest.mark.parametrize("action,args,expected", cases(MAIN))
async def test_every_main_player_control(wired, action, args, expected):
    coordinator, recorder = wired
    player = BigscreenPlayer(coordinator)
    if expected is not REFUSED:
        assert player.supported_features & FEATURE[action]
    elif action in ("media_next_track", "media_previous_track"):
        assert not player.supported_features & FEATURE[action]
    await press(player, recorder, action, args, expected)


@pytest.mark.parametrize("app,action,args", [(app, *key) for app in APPS for key in app_table(app, {})])
async def test_every_app_player_control(wired, app, action, args):
    coordinator, recorder = wired
    player = BigscreenPlayer(coordinator, app)
    expected = APPS[app][(action, args)]
    flag = FEATURE[action]
    if action in NOT_OFFERED.get(app, ()):
        assert not player.supported_features & flag
        recorder.calls.clear()
        # Not offered in the UI; a direct call still only does what the bridge allows.
        await getattr(player, "async_" + action)(*args)
        assert recorder.calls == [expected]
        return
    if expected is not REFUSED and action != "turn_on":
        assert player.supported_features & flag, (app, action)
    if action in ("media_next_track", "media_previous_track", "turn_off", "select_source"):
        assert not player.supported_features & flag
    await press(player, recorder, action, args, expected)


@pytest.mark.parametrize("app,content", [(app, key) for app, table in APPS.items() for key in table
                                         if key not in app_table(app, {})])
async def test_every_app_player_content_type(wired, app, content):
    coordinator, recorder = wired
    await press(BigscreenPlayer(coordinator, app), recorder, *content, APPS[app][content])


@pytest.mark.parametrize("front", ["flimmer", "vacuumtube"])
async def test_main_player_controls_follow_the_app_in_front(wired, front):
    coordinator, recorder = wired
    coordinator.data["active_app"] = front
    player = BigscreenPlayer(coordinator)
    for action in ("media_play", "media_pause"):
        await press(player, recorder, action, (), {"command": action.removeprefix("media_"), "app": front})


async def test_couchside_without_music_assistant_has_no_transport(wired):
    coordinator, recorder = wired
    coordinator.data["active_app"] = "couchside"
    player = BigscreenPlayer(coordinator)
    assert not player.supported_features & (F.PLAY | F.PAUSE | F.SEEK | F.NEXT_TRACK)
    for action, args in (("media_play", ()), ("media_pause", ()), ("media_seek", (5,)), ("media_next_track", ())):
        await press(player, recorder, action, args, REFUSED)


async def test_couchside_controls_go_to_music_assistant_not_the_bridge(wired, monkeypatch):
    coordinator, recorder = wired
    coordinator.data["active_app"] = "couchside"
    player = BigscreenPlayer(coordinator)
    player.hass = coordinator.hass
    entity_id, calls = await music_assistant(coordinator.hass, monkeypatch)
    for action, args, service, data in (
            ("media_play", (), "media_play", {}), ("media_pause", (), "media_pause", {}),
            ("media_stop", (), "media_stop", {}), ("media_next_track", (), "media_next_track", {}),
            ("media_previous_track", (), "media_previous_track", {}),
            ("media_seek", (12,), "media_seek", {"seek_position": 12})):
        calls.clear()
        await getattr(player, "async_" + action)(*args)
        assert calls == [(service, {"entity_id": entity_id, **data})]
        assert recorder.calls == []
    calls.clear()
    recorder.calls.clear()
    await player.async_play_media("couchside_album", "library://album/1")
    assert calls == [("play_media", {"entity_id": entity_id, "media_content_id": "library://album/1",
                                     "media_content_type": "music"})]
    assert recorder.calls == [{"command": "open", "app": "couchside", "content_type": "now_playing", "content_id": ""}]


async def test_couchside_forward_stops_when_music_assistant_is_unavailable(wired, monkeypatch):
    coordinator, recorder = wired
    coordinator.data["active_app"] = "couchside"
    player = BigscreenPlayer(coordinator)
    player.hass = coordinator.hass
    _, calls = await music_assistant(coordinator.hass, monkeypatch, state="unavailable")
    assert not player.supported_features & F.PLAY
    await press(player, recorder, "media_play", (), REFUSED)
    assert calls == []


async def test_browse_root_and_an_app_library(wired):
    coordinator, recorder = wired
    player = BigscreenPlayer(coordinator)
    root = await player.async_browse_media()
    assert [c.media_content_id for c in root.children] == ["couchside", "stremio", "flimmer", "vacuumtube"]
    assert recorder.calls == [{"command": "browse"}]
    recorder.calls.clear()
    page = await player.async_browse_media("stremio_library", "stremio")
    assert recorder.calls == [{"command": "browse", "app": "stremio", "offset": 0, "limit": 50}]
    assert [(c.title, c.can_play) for c in page.children] == [("Item", True), ("Folder", False), ("More", False)]
    assert page.children[2].media_content_id == "stremio:50"
    recorder.calls.clear()
    await player.async_browse_media("stremio_library", "stremio/folder:50")
    assert recorder.calls == [{"command": "browse", "app": "stremio", "offset": 50, "limit": 50, "path": "folder"}]


async def test_app_player_browses_its_own_library_and_no_other(wired):
    coordinator, recorder = wired
    player = BigscreenPlayer(coordinator, "flimmer")
    page = await player.async_browse_media()
    assert recorder.calls == [{"command": "browse", "app": "flimmer", "offset": 0, "limit": 50}]
    assert page.title == "Flimmer channels"
    recorder.calls.clear()
    for content_id in ("stremio", "vacuumtube", "../etc"):
        with pytest.raises(HomeAssistantError):
            await player.async_browse_media("x", content_id)
    assert recorder.calls == []


@pytest.mark.parametrize("code", ["unsupported", "not_ready", "invalid_request", "launch_failed"])
async def test_refused_command_raises_and_keeps_state(wired, code):
    coordinator, recorder = wired
    player = BigscreenPlayer(coordinator, "stremio")
    before = (player.state, player.media_title, player.media_position)
    recorder.refuse = BridgeError(code, "The bridge said no")
    for action, args in (("media_pause", ()), ("media_seek", (5,)), ("play_media", ("stremio_movie", "tt0000000"))):
        with pytest.raises(HomeAssistantError, match="The bridge said no"):
            await getattr(player, "async_" + action)(*args)
    assert (player.state, player.media_title, player.media_position) == before


async def test_controls_while_the_bridge_is_down(wired):
    coordinator, recorder = wired
    await coordinator.client.close()
    coordinator._disconnected()
    main, app = BigscreenPlayer(coordinator), BigscreenPlayer(coordinator, "stremio")
    assert not main.available and not app.available
    for player in (main, app):
        for action, args in (("media_play", ()), ("turn_on", ()), ("play_media", ("app", "stremio"))):
            with pytest.raises(HomeAssistantError):
                await getattr(player, "async_" + action)(*args)
    assert recorder.calls == []


# --- LED lights, the LedFx pause switch and scenes ---------------------------------------

@pytest.fixture
async def lit(coordinator, bridge):
    b, _ = bridge
    b.lights = RecordingLights()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    return coordinator, b.lights


LIGHT_CASES = [
    ("ledfx_tv-backlight", {}, ("ledfx_light", {"virtual": "tv-backlight", "on": True})),
    ("ledfx_tv-backlight", {"brightness": 1}, ("ledfx_light", {"virtual": "tv-backlight", "on": True, "brightness": 0.004})),
    ("ledfx_tv-backlight", {"brightness": 255}, ("ledfx_light", {"virtual": "tv-backlight", "on": True, "brightness": 1.0})),
    ("ledfx_tv-backlight", {"rgb_color": (1, 2, 3)}, ("ledfx_light", {"virtual": "tv-backlight", "on": True, "color": "#010203"})),
    ("ledfx_tv-backlight", {"effect": "Single Color"},
     ("ledfx_light", {"virtual": "tv-backlight", "on": True, "effect": "singleColor"})),
    ("ledfx_desk", {}, ("ledfx_light", {"virtual": "desk", "on": True})),
    ("hyperhdr_1", {}, ("hyperhdr_light", {"instance": 1, "on": True})),
    ("hyperhdr_1", {"brightness": 128}, ("hyperhdr_light", {"instance": 1, "on": True, "brightness": 0.502})),
    ("hyperhdr_1", {"effect": "Video"}, ("hyperhdr_light", {"instance": 1, "on": True, "effect": "Video"})),
    ("hyperhdr_0", {"rgb_color": (0, 0, 255)}, ("hyperhdr_light", {"instance": 0, "on": True, "color": [0, 0, 255]})),
]


@pytest.mark.parametrize("key,kwargs,expected", LIGHT_CASES)
async def test_every_light_turn_on(lit, key, kwargs, expected):
    from custom_components.bigscreen_bridge import light
    coordinator, lights = lit
    await by_key(light.entities(coordinator))[key].async_turn_on(**kwargs)
    assert lights.calls == [expected]


@pytest.mark.parametrize("key,expected", [("ledfx_tv-backlight", ("ledfx_light", {"virtual": "tv-backlight", "on": False})),
                                          ("hyperhdr_0", ("hyperhdr_light", {"instance": 0, "on": False}))])
async def test_every_light_turn_off(lit, key, expected):
    from custom_components.bigscreen_bridge import light
    coordinator, lights = lit
    await by_key(light.entities(coordinator))[key].async_turn_off()
    assert lights.calls == [expected]


async def test_every_effect_listed_can_be_picked(lit):
    from custom_components.bigscreen_bridge import light
    coordinator, lights = lit
    for entity in light.entities(coordinator):
        for effect in entity.effect_list or []:
            lights.calls.clear()
            await entity.async_turn_on(effect=effect)
            assert len(lights.calls) == 1 and lights.calls[0][1]["on"] is True, (entity.key, effect)


@pytest.mark.parametrize("code", ["invalid_request", "unsupported", "unavailable"])
async def test_light_controls_refused(lit, code):
    from custom_components.bigscreen_bridge import light, scene, switch
    coordinator, lights = lit

    async def refuse(command, request):
        raise BridgeError(code, "Lights said no")

    lights.execute = refuse
    entities = (by_key(light.entities(coordinator))["ledfx_tv-backlight"].async_turn_on(),
                by_key(light.entities(coordinator))["hyperhdr_0"].async_turn_off(),
                by_key(switch.entities(coordinator))["ledfx_playing"].async_turn_on(),
                by_key(scene.entities(coordinator))["ledfx_scene_blade-scene"].async_activate())
    for call in entities:
        with pytest.raises(HomeAssistantError, match="Lights said no"):
            await call


async def test_switch_and_scene_controls(lit):
    from custom_components.bigscreen_bridge import scene, switch
    coordinator, lights = lit
    playing = by_key(switch.entities(coordinator))["ledfx_playing"]
    await playing.async_turn_on()
    await playing.async_turn_off()
    scenes = scene.entities(coordinator)
    for entity in scenes:
        await entity.async_activate()
    assert lights.calls == [("ledfx_pause", {"paused": False}), ("ledfx_pause", {"paused": True})] + [
        ("ledfx_scene", {"scene": entity.key.removeprefix("ledfx_scene_"), "active": True}) for entity in scenes]


async def test_light_switch_and_scene_unavailable_without_ledfx(lit):
    from custom_components.bigscreen_bridge import light, scene, switch
    coordinator, lights = lit
    lights.state["ledfx"] = {"available": False, "reason": "service_inactive", "service": "inactive"}
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    entities = (*[e for e in light.entities(coordinator) if e.key.startswith("ledfx_")],
                *switch.entities(coordinator), *scene.entities(coordinator))
    assert entities and not any(e.available for e in entities)


# --- What each platform adds, and states the controls depend on ---------------------------

@pytest.mark.parametrize("platform,expected", [
    ("media_player", {"test-device_active", "test-device_stremio", "test-device_flimmer", "test-device_vacuumtube"}),
    ("light", {"test-device_ledfx_tv-backlight", "test-device_ledfx_desk", "test-device_hyperhdr_0",
               "test-device_hyperhdr_1"}),
    ("switch", {"test-device_ledfx_playing"}),
    ("scene", {"test-device_ledfx_scene_blade-scene"}),
    ("binary_sensor", {"test-device_health", "test-device_display", "test-device_ledfx_service",
                       "test-device_hyperhdr_service", "test-device_hyperhdr_output"}),
])
async def test_each_platform_adds_its_entities(wired, bridge, platform, expected):
    import importlib
    from types import SimpleNamespace
    coordinator, _ = wired
    b, _ = bridge
    b.lights = RecordingLights()
    coordinator.data["lights"] = copy.deepcopy(b.lights.state)
    module = importlib.import_module(f"custom_components.bigscreen_bridge.{platform}")
    added = []
    await module.async_setup_entry(coordinator.hass, SimpleNamespace(runtime_data=coordinator), added.extend)
    assert {e.unique_id for e in added} >= expected
    if platform != "media_player":
        assert {e.unique_id for e in added} == expected


async def test_app_players_only_for_installed_apps(wired):
    from types import SimpleNamespace

    from custom_components.bigscreen_bridge import media_player
    coordinator, _ = wired
    for app in coordinator.data["apps"]:
        app["installed"] = app["id"] == "flimmer"
    added = []
    await media_player.async_setup_entry(coordinator.hass, SimpleNamespace(runtime_data=coordinator), added.extend)
    assert [e.unique_id for e in added] == ["test-device_active", "test-device_flimmer"]


async def test_no_ledfx_means_no_scenes_or_led_sensors(wired):
    from custom_components.bigscreen_bridge import binary_sensor, scene, sensor
    coordinator, _ = wired
    assert scene.entities(coordinator) == []
    assert "ledfx_devices" not in by_key(sensor.entities(coordinator))
    assert "ledfx_service" not in by_key(binary_sensor.entities(coordinator))


@pytest.mark.parametrize("media_type,media_id,app", [
    ("youtube_video", "aaaaaaaaaaa", "vacuumtube"), ("flimmer_channel", "m3u:0000", "flimmer"),
    ("stremio_movie", "tt0000000", "stremio"), ("couchside_album", "library://album/1", "couchside")])
async def test_deep_links_an_app_does_not_advertise_are_refused(wired, media_type, media_id, app):
    coordinator, recorder = wired
    next(a for a in coordinator.data["apps"] if a["id"] == app)["content_types"] = []
    await press(BigscreenPlayer(coordinator), recorder, "play_media", (media_type, media_id), REFUSED)


async def test_flimmer_catalogue_still_loading(wired):
    coordinator, recorder = wired
    recorder.answer = {"items": [], "loading": True}
    with pytest.raises(HomeAssistantError, match="loading"):
        await BigscreenPlayer(coordinator, "flimmer").async_browse_media()
    assert recorder.calls == [{"command": "browse", "app": "flimmer", "offset": 0, "limit": 50}]


async def test_unknown_led_effect_is_refused(lit):
    from custom_components.bigscreen_bridge import light
    coordinator, lights = lit
    with pytest.raises(HomeAssistantError, match="Unknown LedFx effect"):
        await by_key(light.entities(coordinator))["ledfx_tv-backlight"].async_turn_on(effect="Not an effect")
    assert lights.calls == []


@pytest.mark.parametrize("media,running,display,expected", [
    ({"state": "buffering"}, True, True, "buffering"), ({"state": "paused"}, True, True, "paused"),
    ({}, True, True, "idle"), ({}, False, True, "off"), ({"state": "playing"}, True, False, "off")])
async def test_app_player_states(wired, media, running, display, expected):
    coordinator, _ = wired
    app = next(a for a in coordinator.data["apps"] if a["id"] == "stremio")
    app.update(media=media, running=running)
    coordinator.data["power"]["display"] = {"display_on": display}
    player = BigscreenPlayer(coordinator, "stremio" if display else None)
    assert player.state == expected


async def test_series_metadata_and_display_sensor(wired):
    from custom_components.bigscreen_bridge import binary_sensor, sensor
    coordinator, _ = wired
    app = next(a for a in coordinator.data["apps"] if a["id"] == "stremio")
    app["media"] = dict(MEDIA, type="series", title="Show", season=1, episode=2)
    player = BigscreenPlayer(coordinator, "stremio")
    assert player.media_series_title == "Show"
    coordinator.data["power"]["display"] = {"display_on": True}
    coordinator.data["audio"].update(available=True, volume=0.42)
    assert by_key(binary_sensor.entities(coordinator))["display"].is_on is True
    assert by_key(sensor.entities(coordinator))["volume"].native_value == 42


# --- the coordinator's periodic diagnostics read -----------------------------------------

async def test_diagnostics_are_reread_on_the_slow_interval(wired):
    coordinator, recorder = wired
    recorder.answer = {"service": {"version": "9.9.9"}}
    coordinator.diagnostics_at = 0
    await coordinator._async_update_data()
    assert {"command": "diagnostics"} in recorder.calls and coordinator.diagnostics["service"]["version"] == "9.9.9"


async def test_an_older_bridge_without_diagnostics_has_none(wired):
    coordinator, recorder = wired
    coordinator.diagnostics = {"stale": True}
    recorder.refuse = BridgeError("unsupported", "Unknown command")
    await coordinator.refresh_diagnostics()
    assert coordinator.diagnostics is None


async def test_a_failed_diagnostics_read_keeps_the_last_one(wired):
    coordinator, recorder = wired
    coordinator.diagnostics = {"service": {"version": "0.2.2"}}
    recorder.refuse = BridgeError("internal_error", "Bridge operation failed")
    await coordinator.refresh_diagnostics()
    assert coordinator.diagnostics == {"service": {"version": "0.2.2"}}


async def test_a_failed_state_read_marks_the_update_failed(wired):
    from homeassistant.helpers.update_coordinator import UpdateFailed
    coordinator, recorder = wired
    recorder.refuse = BridgeError("internal_error", "Bridge operation failed")
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
