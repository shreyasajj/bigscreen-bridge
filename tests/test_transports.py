"""Library browsing, network discovery and the optional MQTT transport."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from bigscreen_bridge import discovery
from bigscreen_bridge.adapters import BridgeError
from bigscreen_bridge.couchside import CouchsideHook
from bigscreen_bridge.mqtt import MqttLink, settings
from bigscreen_bridge.stremio import StremioHook
from custom_components.bigscreen_bridge.media_player import BigscreenPlayer
from homeassistant.components.media_player import MediaPlayerEntityFeature

ALBUM = "library://album/12"


def hook(cls, state, page):
    instance = cls(None, True)
    instance.refresh = AsyncMock()
    instance.state = state
    instance.request = AsyncMock(return_value=page)
    return instance


def couchside(page):
    return hook(
        CouchsideHook, {"ready": True, "browse": True, "content_types": list(CouchsideHook.kinds)}, page
    )


async def test_hook_browse_validates_every_listed_item():
    sections = {
        "items": [{"id": "album", "name": "Albums", "path": "album", "content_type": None}],
        "next_offset": None,
    }
    music = couchside(sections)
    assert (await music.browse())["items"][0]["path"] == "album"
    music.request.assert_awaited_once_with("browse", offset=0, limit=50)
    music.request = AsyncMock(
        return_value={
            "items": [
                {"id": ALBUM, "name": "Album", "content_type": "album", "path": None, "subtitle": "ignored"}
            ],
            "next_offset": 50,
        }
    )
    page = await music.browse("album")
    assert page == {
        "items": [{"id": ALBUM, "name": "Album", "content_type": "album", "path": None}],
        "next_offset": 50,
    }
    for bad in (
        {"id": "https://example.org/x", "name": "URL", "content_type": "album", "path": None},
        {"id": ALBUM, "name": "Both", "content_type": "album", "path": "album"},
        {"id": ALBUM, "name": "Neither", "content_type": None, "path": None},
        {"id": ALBUM, "name": "Kind", "content_type": "radio", "path": None},
        {"id": "x", "name": "Section", "content_type": None, "path": "../etc"},
    ):
        music.request = AsyncMock(return_value={"items": [bad], "next_offset": None})
        with pytest.raises(BridgeError) as error:
            await music.browse("album")
        assert error.value.code == "invalid_response"
    music.request = AsyncMock(return_value={"items": [], "next_offset": 0})
    with pytest.raises(BridgeError):
        await music.browse("album")  # a continuation must move forward


async def test_hook_browse_needs_a_ready_app_that_offers_it():
    music = couchside({"items": []})
    for path, limit in (("Album", 50), ("album", 51)):
        with pytest.raises(BridgeError) as error:
            await music.browse(path, 0, limit)
        assert error.value.code == "invalid_request"
    music.state = {"ready": True}
    with pytest.raises(BridgeError) as error:
        await music.browse()
    assert error.value.code == "unsupported"
    music.state = None
    with pytest.raises(BridgeError) as error:
        await music.browse()
    assert error.value.code == "not_ready"
    music.request.assert_not_awaited()


async def test_bridge_browse_returns_typed_ids_and_reports_the_capability(bridge):
    b, _ = bridge
    b.stremio = hook(
        StremioHook,
        {
            "protocol": 1,
            "ready": True,
            "browse": True,
            "content_types": ["movie", "series"],
            "capabilities": [],
            "media": {"state": "idle"},
        },
        {
            "items": [{"id": "tt0133093", "name": "The Matrix", "content_type": "movie", "path": None}],
            "next_offset": None,
        },
    )
    b.couchside = couchside(
        {
            "items": [{"id": "album", "name": "Albums", "path": "album", "content_type": None}],
            "next_offset": None,
        }
    )
    apps = {app["id"]: app for app in b.snapshot()["apps"]}
    assert apps["stremio"]["browse"] and apps["couchside"]["browse"] and "browse" not in apps["vacuumtube"]
    movies = await b.execute({"id": "1", "command": "browse", "app": "stremio"})
    assert movies["items"][0]["media_content_type"] == "stremio_movie"
    music = await b.execute(
        {"id": "2", "command": "browse", "app": "couchside", "path": "album", "limit": 10}
    )
    assert music["items"][0]["media_content_type"] is None
    b.couchside.request.assert_awaited_once_with("browse", offset=0, limit=10, path="album")
    for request in ({"app": "vacuumtube"}, {"app": "nothing"}):
        with pytest.raises(BridgeError) as error:
            await b.execute({"id": "3", "command": "browse", **request})
        assert error.value.code == "unsupported"
    with pytest.raises(BridgeError):
        await b.execute({"id": "4", "command": "browse", "path": "album"})


async def test_home_assistant_browses_sections_items_and_pages():
    apps = [
        {"id": "couchside", "name": "Couchside", "installed": True, "browse": True},
        {"id": "stremio", "name": "Stremio TV", "installed": True, "browse": True, "hook_installed": True},
        {"id": "vacuumtube", "name": "VacuumTube", "installed": True},
    ]
    pages = {
        None: {
            "items": [{"id": "album", "name": "Albums", "path": "album", "media_content_type": None}],
            "next_offset": None,
        },
        "album": {
            "items": [{"id": ALBUM, "name": "Album", "path": None, "media_content_type": "couchside_album"}],
            "next_offset": 50,
        },
    }

    async def command(name, **kwargs):
        if "app" not in kwargs:
            return {"items": [{"id": app["id"], "title": app["name"]} for app in apps]}
        return pages[kwargs.get("path")]

    coordinator = SimpleNamespace(
        data={"device_id": "d", "name": "TV", "apps": apps, "active_app": None},
        command=AsyncMock(side_effect=command),
        last_update_success=True,
    )
    player = BigscreenPlayer(coordinator)
    root = await player.async_browse_media()
    assert {child.media_content_id: child.can_expand for child in root.children} == {
        "couchside": True,
        "stremio": True,
        "vacuumtube": False,
    }
    sections = await player.async_browse_media("app", "couchside")
    assert sections.children[0].media_content_id == "couchside/album" and sections.children[0].can_expand
    albums = await player.async_browse_media("couchside_library", "couchside/album")
    item, more = albums.children
    assert (item.media_content_type, item.media_content_id, item.media_class, item.can_play) == (
        "couchside_album",
        ALBUM,
        "album",
        True,
    )
    assert more.media_content_id == "couchside/album:50"
    await player.async_browse_media("couchside_library", "couchside/album:50")
    coordinator.command.assert_awaited_with("browse", app="couchside", offset=50, limit=50, path="album")
    movies = BigscreenPlayer(coordinator, "stremio")
    assert movies.supported_features & MediaPlayerEntityFeature.BROWSE_MEDIA
    pages[None] = {"items": [], "next_offset": None}
    assert (await movies.async_browse_media()).title == "Stremio TV library"
    from homeassistant.exceptions import HomeAssistantError

    for player_, target in ((movies, "couchside"), (player, "vacuumtube"), (player, "couchside/../x")):
        with pytest.raises(HomeAssistantError):
            await player_.async_browse_media("x", target)


def test_discovery_publishes_identity_but_never_on_loopback():
    config = {
        "device_id": "0123456789abcdef",
        "name": "Living room",
        "host": "0.0.0.0",
        "port": 8765,
        "token": "s" * 48,
    }
    record = discovery.service_record(config, "tv")
    assert record["instance"] == "Living room (01234567)" and record["port"] == 8765
    assert set(record["txt"]) == {b"device_id=0123456789abcdef", b"name=Living room", b"protocol=1"}
    assert not any(b"s" * 48 in entry for entry in record["txt"])
    for change in ({"host": "127.0.0.1"}, {"host": "::1"}, {"host": "localhost"}, {"discovery": False}):
        assert discovery.service_record({**config, **change}, "tv") is None
    assert discovery.service_record({"device_id": "x" * 8, "host": "0.0.0.0"}, "tv")["instance"].startswith(
        "tv ("
    )


async def test_discovery_registers_with_avahi_and_survives_its_absence(monkeypatch):
    config = {"device_id": "0123456789abcdef", "name": "TV", "host": "0.0.0.0"}
    call = AsyncMock(side_effect=[["/Client1/EntryGroup1"], [], []])
    monkeypatch.setattr(discovery, "call", call)
    assert await discovery.advertise("bus", config, "tv") == "/Client1/EntryGroup1"
    add = call.await_args_list[1].args
    assert (
        add[4:6] == ("AddService", "iiussssqaay")
        and add[6][4] == "_bigscreen-bridge._tcp"
        and add[6][7] == 8765
    )
    assert call.await_args_list[2].args[4] == "Commit"
    monkeypatch.setattr(discovery, "call", AsyncMock(side_effect=BridgeError("system_error", "no avahi")))
    assert await discovery.advertise("bus", config, "tv") is None
    monkeypatch.setattr(discovery, "call", call := AsyncMock())
    assert await discovery.advertise("bus", {**config, "host": "127.0.0.1"}, "tv") is None
    call.assert_not_awaited()


def test_mqtt_settings_are_optional_and_validated():
    base = {"device_id": "tv-1", "token": "a" * 48}
    assert settings(base) is None
    options = settings({**base, "mqtt": {"host": "broker.local"}})
    assert options["base"] == "bigscreen/tv-1" and options["port"] == 1883 and options["commands"] is False
    for bad in (
        {"host": ""},
        {"host": "b", "port": 0},
        {"host": "b", "base_topic": "a/#"},
        {"host": "b", "base_topic": "/a"},
        {"host": "b", "commands": "yes"},
        {"host": "b", "password": 5},
        ["b"],
    ):
        with pytest.raises(ValueError):
            settings({**base, "mqtt": bad})
    with pytest.raises(ValueError):
        settings({"device_id": "tv/+", "mqtt": {"host": "b"}})


class Broker:
    """Stands in for one aiomqtt session."""

    def __init__(self, incoming=(), hold=None):
        self.published, self.subscribed, self.incoming, self.hold = [], [], list(incoming), hold

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, retain))

    async def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)

    @property
    def messages(self):
        async def stream():
            for payload in self.incoming:
                yield SimpleNamespace(payload=payload)
            if self.hold:
                await self.hold.wait()

        return stream()


async def test_mqtt_mirrors_state_and_acknowledges_commands(bridge):
    b, _ = bridge
    options = settings({**b.config, "mqtt": {"host": "broker", "commands": True}})
    request = json.dumps({"id": "r1", "command": "get_state"}).encode()
    broker = Broker([request, request, b"not json", b"x" * 20000])
    await MqttLink(b, options, connect=lambda _: broker).session()
    base = "bigscreen/test-device"
    assert broker.subscribed == [base + "/command"]
    assert broker.published[0] == (base + "/availability", "online", True)
    topic, state, retained = broker.published[1]
    assert topic == base + "/state" and retained and json.loads(state)["device_id"] == "test-device"
    assert "a" * 48 not in state
    replies = [json.loads(payload) for topic, payload, _ in broker.published if topic == base + "/response"]
    assert [reply["ok"] for reply in replies] == [True, True, False, False]
    assert replies[0] == replies[1] and replies[0]["id"] == "r1"
    assert replies[3]["error"]["message"] == "Request too large"


async def test_mqtt_is_read_only_by_default_and_follows_state_changes(bridge):
    b, _ = bridge
    hold = asyncio.Event()
    broker = Broker(hold=hold)
    link = MqttLink(b, settings({**b.config, "mqtt": {"host": "broker"}}), connect=lambda _: broker, retry=0)
    task = asyncio.create_task(link.run())
    for _ in range(20):
        await asyncio.sleep(0)
    assert broker.subscribed == [] and len(broker.published) == 2
    await b.notify()
    assert broker.published[-1][0].endswith("/state") and len(broker.published) == 3
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert broker.published[-1][1] == "offline" and b.publishers == []


async def test_a_failing_publisher_never_blocks_notifications(bridge):
    b, _ = bridge
    b.publishers.append(AsyncMock(side_effect=RuntimeError("broker down")))
    await b.notify()


async def test_zeroconf_discovery_only_asks_for_the_token(bridge, monkeypatch):
    import aiohttp
    from custom_components.bigscreen_bridge.config_flow import ConfigFlow

    _, http = bridge
    async with aiohttp.ClientSession() as session:
        monkeypatch.setattr(
            "custom_components.bigscreen_bridge.config_flow.async_get_clientsession", lambda _: session
        )
        flow = ConfigFlow()
        flow.hass = None
        flow.context = {}

        async def set_unique_id(value):
            flow.context["unique_id"] = value

        flow.async_set_unique_id = set_unique_id
        flow._abort_if_unique_id_configured = Mock()
        flow.async_show_form = Mock(side_effect=lambda **kwargs: kwargs)
        flow.async_create_entry = Mock(side_effect=lambda **kwargs: kwargs)
        flow.async_abort = Mock(side_effect=lambda **kwargs: kwargs)
        found = SimpleNamespace(
            host=http.host,
            port=http.port,
            properties={"device_id": "test-device", "name": "TV", "protocol": "1"},
        )
        form = await flow.async_step_zeroconf(found)
        url = f"http://{http.host}:{http.port}"
        flow._abort_if_unique_id_configured.assert_called_once_with(updates={"url": url})
        assert form["step_id"] == "pair" and list(form["data_schema"].schema) == ["code"]
        assert (await flow.async_step_pair({"code": "b" * 48}))["errors"] == {"base": "invalid_auth"}
        created = await flow.async_step_pair({"code": "a" * 48})
        assert created == {"title": "TV", "data": {"url": url, "token": "a" * 48}}
        flow.context["unique_id"] = "another-device"
        assert (await flow.async_step_pair({"code": "a" * 48}))["errors"] == {"base": "wrong_device"}
        for properties in ({"name": "TV", "protocol": "1"}, {"device_id": "x", "protocol": "2"}):
            aborted = await flow.async_step_zeroconf(SimpleNamespace(host="h", port=1, properties=properties))
            assert aborted == {"reason": "not_supported"}
