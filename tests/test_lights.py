"""LedFx and HyperHDR, read and controlled over their local APIs."""
import asyncio
import json

import pytest
from aiohttp import web
from bigscreen_bridge.adapters import BridgeError
from bigscreen_bridge.lights import COMMANDS, Lights


def answer(body):
    """An async aiohttp handler returning `body()` as JSON."""
    async def handler(_request):
        return web.json_response(body())
    return handler


GRADIENT = "linear-gradient(90deg, #ff0000 0.00%, #0000ff 100.00%)"


class FakeLedFx:
    def __init__(self):
        self.paused = False
        self.puts = []
        self.virtuals = {
            "tv-backlight": {"config": {"name": "TV backlight"}, "active": True,
                             "effect": {"type": "pitchSpectrum", "name": "Pitch Spectrum",
                                        "config": {"brightness": 0.8, "gradient": GRADIENT}}},
            "lepre": {"config": {"name": "Lepre"}, "active": False, "effect": {}},
            "desk": {"config": {"name": "Desk"}, "active": True,
                     "effect": {"type": "fire", "name": "Fire", "config": {"brightness": 1.0}}}}
        self.scenes = {"blade-scene": {"name": "Blade Scene"}}

    def app(self):
        app = web.Application()
        app.router.add_get("/api/info", answer(lambda: {"version": "2.1.9"}))
        app.router.add_get("/api/virtuals", answer(lambda:
            {"status": "success", "paused": self.paused, "virtuals": self.virtuals}))
        app.router.add_put("/api/virtuals", self.toggle)
        app.router.add_get("/api/devices", answer(lambda: {"devices": {
            "tv": {"online": True, "config": {"name": "TV"}}, "wall": {"online": False, "config": {"name": "Wall"}}}}))
        app.router.add_get("/api/scenes", answer(lambda: {"scenes": self.scenes}))
        app.router.add_put("/api/scenes", self.record)
        app.router.add_get("/api/schema", answer(lambda: {"effects": {
            "pitchSpectrum": {"name": "Pitch Spectrum", "schema": {"properties": {"brightness": {}, "gradient": {}}}},
            "singleColor": {"name": "Single Color", "schema": {"properties": {"brightness": {}, "color": {}}}},
            "fire": {"name": "Fire", "schema": {"properties": {"brightness": {}}}}}}))
        app.router.add_put("/api/virtuals/{id}", self.record)
        app.router.add_post("/api/virtuals/{id}/effects", self.record)
        app.router.add_put("/api/virtuals/{id}/effects", self.update_effect)
        return app

    async def toggle(self, request):
        self.puts.append(("PUT", request.path, None))
        self.paused = not self.paused
        return web.json_response({"status": "success", "paused": self.paused})

    async def update_effect(self, request):
        """Like LedFx: without the running effect's type, the PUT replaces the effect, so refuse it here."""
        body = await request.json()
        running = (self.virtuals[request.match_info["id"]].get("effect") or {}).get("type")
        if body.get("type") != running:
            return web.json_response({"status": "failed", "reason": "type does not match"}, status=400)
        self.puts.append((request.method, request.path, body))
        return web.json_response({"status": "success"})

    async def record(self, request):
        self.puts.append((request.method, request.path, await request.json()))
        return web.json_response({"status": "success"})


SERVERINFO = {"success": True, "info": {
    "components": [{"name": "ALL", "enabled": True}, {"name": "LEDDEVICE", "enabled": True}],
    "grabbers": {"active": "V4L2:USB capture"}, "lastError": "",
    "effects": [{"name": "Rainbow swirl"}, {"name": "Candle"}],
    "instance": [{"instance": 0, "friendly_name": "Tv backlight", "running": True},
                 {"instance": 1, "friendly_name": "Right wall", "running": False}]}}


class FakeHyperHdrSocket:
    """HyperHDR's JSON server: one JSON object per line, answered by tan."""

    def __init__(self):
        self.received = []

    async def handle(self, reader, writer):
        while line := await reader.readline():
            request = json.loads(line)
            self.received.append(request)
            writer.write(json.dumps({"command": request["command"], "success": True,
                                     "tan": request.get("tan")}).encode() + b"\n")
            await writer.drain()
        writer.close()


@pytest.fixture
async def lights(aiohttp_server):
    ledfx = FakeLedFx()
    ledfx_server = await aiohttp_server(ledfx.app())
    hyper_app = web.Application()
    hyper_app.router.add_post("/json-rpc", answer(lambda: SERVERINFO))
    hyper_server = await aiohttp_server(hyper_app)
    socket = FakeHyperHdrSocket()
    tcp = await asyncio.start_server(socket.handle, "127.0.0.1", 0)
    port = tcp.sockets[0].getsockname()[1]
    units = {"ledfx.service": "active", "hyperhdr@tv.service": "active"}

    async def unit_state(_bus, unit):
        return units[unit]

    module = Lights({"ledfx_url": str(ledfx_server.make_url("")), "hyperhdr_url": str(hyper_server.make_url("")),
                     "hyperhdr_socket": f"127.0.0.1:{port}"}, user="tv", unit_state=unit_state)
    yield module, ledfx, socket, units, ledfx_server
    await module.close()
    tcp.close()


async def test_state_describes_both(lights):
    module, *_ = lights
    assert await module.refresh() is True
    ledfx, hyper = module.state["ledfx"], module.state["hyperhdr"]
    assert ledfx["available"] and ledfx["service"] == "active" and ledfx["version"] == "2.1.9"
    assert ledfx["paused"] is False and ledfx["devices"] == {"total": 2, "online": 1, "offline": ["Wall"]}
    assert ledfx["scenes"] == [{"id": "blade-scene", "name": "Blade Scene", "active": False}]
    tv = next(v for v in ledfx["virtuals"] if v["id"] == "tv-backlight")
    assert tv == {"id": "tv-backlight", "name": "TV backlight", "active": True, "effect": "pitchSpectrum",
                  "effect_name": "Pitch Spectrum", "brightness": 0.8, "color": None}
    assert {"type": "fire", "name": "Fire"} in ledfx["effects"]
    assert hyper["available"] and hyper["output"] is True and hyper["grabber"] == "V4L2:USB capture"
    assert hyper["effects"] == ["Rainbow swirl", "Candle"]
    assert hyper["instances"] == [{"id": 0, "name": "Tv backlight", "running": True},
                                  {"id": 1, "name": "Right wall", "running": False}]
    assert await module.refresh() is False   # nothing changed
    assert set(COMMANDS) == {"ledfx_pause", "ledfx_light", "ledfx_scene", "hyperhdr_light"}


async def test_one_service_down_leaves_the_other_working(lights):
    module, _, _, units, ledfx_server = lights
    await ledfx_server.close()
    units["ledfx.service"] = "inactive"
    await module.refresh()
    assert module.state["ledfx"]["available"] is False and module.state["ledfx"]["service"] == "inactive"
    assert module.state["ledfx"]["reason"] == "service_inactive"
    assert module.state["hyperhdr"]["available"] is True
    with pytest.raises(BridgeError) as error:
        await module.execute("ledfx_pause", {"paused": True})
    assert error.value.code == "service_unavailable"


async def test_pause_only_toggles_when_it_differs(lights):
    module, ledfx, *_ = lights
    await module.refresh()
    await module.execute("ledfx_pause", {"paused": False})
    assert ledfx.puts == []
    await module.execute("ledfx_pause", {"paused": True})
    assert ledfx.paused is True and len(ledfx.puts) == 1
    assert module.state["ledfx"]["paused"] is True


async def test_light_on_effect_brightness_and_colour(lights):
    module, ledfx, *_ = lights
    await module.refresh()
    await module.execute("ledfx_light", {"virtual": "lepre", "on": True})
    assert ledfx.puts[-1] == ("PUT", "/api/virtuals/lepre", {"active": True})
    await module.execute("ledfx_light", {"virtual": "tv-backlight", "brightness": 0.5})
    assert ledfx.puts[-1] == ("PUT", "/api/virtuals/tv-backlight/effects",
                              {"type": "pitchSpectrum", "config": {"brightness": 0.5}})
    await module.execute("ledfx_light", {"virtual": "tv-backlight", "color": "#00ff00"})   # gradient effect
    assert ledfx.puts[-1][2] == {"type": "pitchSpectrum",
                                 "config": {"gradient": "linear-gradient(90deg, #00ff00 0.00%, #00ff00 100.00%)"}}
    await module.execute("ledfx_light", {"virtual": "desk", "color": "#123456"})   # neither color nor gradient
    assert ledfx.puts[-1] == ("POST", "/api/virtuals/desk/effects",
                              {"type": "singleColor", "config": {"color": "#123456", "brightness": 1.0}})
    await module.execute("ledfx_light", {"virtual": "tv-backlight", "effect": "fire"})
    assert ledfx.puts[-1] == ("POST", "/api/virtuals/tv-backlight/effects",
                              {"type": "fire", "config": {"brightness": 0.8}})
    await module.execute("ledfx_scene", {"scene": "blade-scene", "active": True})
    assert ledfx.puts[-1] == ("PUT", "/api/scenes", {"id": "blade-scene", "action": "activate"})


@pytest.mark.parametrize(("command", "request_"), [
    ("ledfx_light", {"virtual": "nope", "on": True}),
    ("ledfx_light", {"virtual": "tv-backlight", "effect": "unknown"}),
    ("ledfx_light", {"virtual": "tv-backlight", "brightness": 1.5}),
    ("ledfx_light", {"virtual": "tv-backlight", "color": "red"}),
    ("ledfx_light", {"virtual": "tv-backlight"}),
    ("ledfx_light", {"virtual": "lepre", "brightness": 0.5}),           # no effect running
    ("ledfx_scene", {"scene": "nope", "active": True}),
    ("ledfx_pause", {"paused": "yes"}),
    ("hyperhdr_light", {"instance": 7, "on": True}),
    ("hyperhdr_light", {"instance": 0, "effect": "Unknown"}),
    ("hyperhdr_light", {"instance": 0, "color": [256, 0, 0]}),
    ("hyperhdr_light", {"instance": True, "on": True}),
])
async def test_bad_requests_send_nothing(lights, command, request_):
    module, ledfx, socket, *_ = lights
    await module.refresh()
    with pytest.raises(BridgeError) as error:
        await module.execute(command, request_)
    assert error.value.code in ("invalid_request", "unsupported")
    assert ledfx.puts == [] and socket.received == []


async def test_hyperhdr_switches_instance_before_each_command(lights):
    module, _, socket, *_ = lights
    await module.refresh()
    await module.execute("hyperhdr_light", {"instance": 1, "on": True})
    await module.execute("hyperhdr_light", {"instance": 1, "color": [255, 0, 0], "brightness": 0.5})
    await module.execute("hyperhdr_light", {"instance": 0, "effect": "Candle"})
    await module.execute("hyperhdr_light", {"instance": 0, "effect": "Video"})
    sent = [{k: v for k, v in r.items() if k != "tan"} for r in socket.received]
    assert sent == [
        {"command": "instance", "subcommand": "startInstance", "instance": 1},
        {"command": "instance", "subcommand": "switchTo", "instance": 1},
        {"command": "color", "color": [255, 0, 0], "priority": 50, "origin": "Bigscreen Bridge"},
        {"command": "adjustment", "adjustment": {"scaleOutput": 0.5}},
        {"command": "instance", "subcommand": "switchTo", "instance": 0},
        {"command": "effect", "effect": {"name": "Candle"}, "priority": 50, "origin": "Bigscreen Bridge"},
        {"command": "instance", "subcommand": "switchTo", "instance": 0},
        {"command": "clear", "priority": 50}]


async def test_disabled_module_reports_nothing():
    module = Lights({"lights": False})
    assert await module.refresh() is False
    assert module.state == {}
    await module.close()


class StubLights:
    enabled = True

    def __init__(self):
        self.state = {"ledfx": {"available": True, "paused": False}, "hyperhdr": {"available": False}}
        self.calls = []

    async def execute(self, command, request):
        self.calls.append((command, request))
        return {"ok": command}


async def test_bridge_reports_lights_and_routes_their_commands(bridge, recorder):
    b, http = bridge
    b.diagnostics = recorder
    b.lights = StubLights()
    assert b.snapshot()["lights"] == b.lights.state
    result = await b.execute({"id": "1", "command": "ledfx_light", "virtual": "tv", "brightness": 0.5})
    assert result == {"ok": "ledfx_light"}
    assert b.lights.calls == [("ledfx_light", {"virtual": "tv", "brightness": 0.5})]
    with pytest.raises(BridgeError) as error:
        await b.execute({"id": "2", "command": "ledfx_pause", "paused": True, "extra": 1})
    assert error.value.code == "invalid_request"
    async with http.ws_connect("/ws", headers={"Authorization": "Bearer " + "a" * 48}) as ws:
        await ws.receive_json()
        await ws.send_json({"id": "3" * 8, "command": "hyperhdr_light", "instance": 0, "on": True})
        while (reply := await ws.receive_json()).get("id") != "3" * 8:
            pass
        assert reply["ok"]
    recorded = recorder.to_dict(b)["commands"][-1]
    assert recorded["command"] == "hyperhdr_light" and "instance" not in json.dumps(recorded)


async def test_without_the_lights_module_the_commands_say_so(bridge):
    b, _ = bridge
    assert "lights" not in b.snapshot()
    with pytest.raises(BridgeError) as error:
        await b.execute({"id": "1", "command": "ledfx_pause", "paused": True})
    assert error.value.code == "unsupported"


def keys(value, path=""):
    """Every key path in `value`, with list entries collapsed to `[]`."""
    if isinstance(value, dict):
        return set().union(set(), *({path + "." + k} | keys(v, path + "." + k) for k, v in value.items()))
    if isinstance(value, list):
        return set().union(set(), *(keys(v, path + "[]") for v in value))
    return set()


async def test_status_screen_fixtures_have_the_real_shape(lights):
    """The fixtures' hand-written lights match what Lights.refresh() really produces, key for key."""
    import importlib.util
    from pathlib import Path
    module, *_ = lights
    await module.refresh()
    spec = importlib.util.spec_from_file_location(
        "make_fixtures", Path(__file__).parents[1] / "bigscreen-status" / "tools" / "make_fixtures.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    assert keys(tool.LIGHTS) == keys(module.state)
