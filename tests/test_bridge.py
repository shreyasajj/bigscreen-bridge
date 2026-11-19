import asyncio
import importlib.util
from pathlib import Path

import aiohttp
import pytest
from bigscreen_bridge.adapters import APPS, BridgeError

# Load the transport client without importing Home Assistant's integration package.
spec = importlib.util.spec_from_file_location("bridge_client", Path(__file__).parents[2]
    / "ha-bigscreen-bridge/custom_components/bigscreen_bridge/client.py")
client_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client_module)


async def result(ws):
    while True:
        data = await ws.receive_json()
        if data["type"] == "result":
            return data


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"},
    {"Authorization": "Bearer " + "a" * 48, "Origin": "http://untrusted"}])
async def test_auth_and_browser_origins(bridge, headers):
    _, http = bridge
    with pytest.raises(aiohttp.WSServerHandshakeError) as exc:
        await http.ws_connect("/ws", headers=headers)
    assert exc.value.status == 401


async def test_health_is_sanitized(bridge):
    _, http = bridge
    response = await http.get("/health")
    assert await response.json() == {"ok": True, "protocol": 1}


async def test_invalid_json_id_and_unsupported_content(bridge):
    b, http = bridge
    async with http.ws_connect("/ws", headers={"Authorization": "Bearer " + "a" * 48}) as ws:
        initial = await ws.receive_json()
        assert initial["active_app"] is None
        for payload in ["{", "[]", '{"command":"focus"}',
                        '{"id":4,"command":"focus"}']:
            await ws.send_str(payload)
            assert (await result(ws))["error"]["code"] == "invalid_request"
        await ws.send_json({"id": "unsupported", "command": "focus", "app": "flimmer",
                            "content": {"channel": "123"}})
        assert (await result(ws))["error"]["code"] == "unsupported"
        assert not b.wm.focused


async def test_existing_window_focus_idempotency_and_identity(bridge):
    b, http = bridge
    b.wm.windows = [{"id": "existing", "desktop": "io.github.shreyasajj.StremioTV.desktop"}]
    async with http.ws_connect("/ws", headers={"Authorization": "Bearer " + "a" * 48}) as ws:
        await ws.receive_json()
        payload = {"id": "same", "command": "focus", "app": "stremio"}
        await ws.send_json(payload)
        ack = await result(ws)
        assert ack["ok"] and ack["id"] == "same"
        await ws.send_json(payload)
        assert await result(ws) == ack
        assert b.wm.focused == ["existing"]
        assert b.snapshot()["active_app"] == "stremio"
        await ws.send_json({**payload, "app": "flimmer"})
        assert (await result(ws))["error"]["code"] == "invalid_request"
        assert b.wm.focused == ["existing"]


async def test_no_playback_capabilities_and_browse(bridge):
    b, _ = bridge
    assert all(a["capabilities"] == ["focus"] for a in b.snapshot()["apps"])
    assert len((await b.execute({"command": "browse"}))["items"]) == 4
    assert not APPS[1].matches({"class": "com.stremio.Stremio"})
    with pytest.raises(BridgeError) as exc:
        await b.execute({"command": "play", "app": "stremio"})
    assert exc.value.code == "unsupported"


async def test_no_launch_without_window_manager(bridge):
    b, _ = bridge
    b.wm.ready.clear()
    with pytest.raises(BridgeError) as exc:
        await b.focus("couchside")
    assert exc.value.code == "window_manager_unavailable"


async def test_no_duplicate_when_process_has_no_window(bridge, monkeypatch):
    b, _ = bridge

    async def running(*argv):
        return APPS[0].flatpak

    monkeypatch.setattr("bigscreen_bridge.server.process", running)
    with pytest.raises(BridgeError, match="no duplicate"):
        await b.focus("couchside")
    assert not b.launch_tasks


async def test_concurrent_launch_waits_for_readiness_once(bridge, monkeypatch):
    b, _ = bridge
    launches = []

    async def processes(*argv):
        return ""

    async def launch(*argv):
        launches.append(argv)
        await asyncio.sleep(0.01)
        b.wm.windows = [{"id": "new", "desktop": APPS[2].flatpak}]

    monkeypatch.setattr("bigscreen_bridge.server.process", processes)
    b.launcher = launch
    await asyncio.gather(b.focus("flimmer"), b.focus("flimmer"))
    assert launches == [("flatpak", "run", APPS[2].flatpak)]
    assert b.wm.focused == ["new", "new"]


async def test_client_commands_push_disconnect_and_reconnect(bridge):
    b, http = bridge
    states = []
    disconnected = asyncio.Event()
    async with aiohttp.ClientSession() as session:
        client = client_module.BridgeClient(session, str(http.make_url("")), "a" * 48,
                                           states.append, disconnected.set)
        initial = await client.connect()
        assert initial["device_id"] == "test-device"
        assert len((await client.command("browse"))["items"]) == 4
        b.wm.windows = [{"id": "new", "desktop": APPS[3].flatpak, "active": True}]
        await b.notify()
        async with asyncio.timeout(1):
            while not states:
                await asyncio.sleep(0.01)
        assert states[-1]["active_app"] == "vacuumtube"
        await next(iter(b.clients)).close()
        await asyncio.wait_for(disconnected.wait(), 1)
        await client.close()
        client.on_disconnect = disconnected.set
        assert (await client.connect())["active_app"] == "vacuumtube"
        await client.close()


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://user:pass@tv", "http://tv/ws",
                                 "http://tv?token=x", "http://tv:99999"])
def test_reject_unsafe_bridge_addresses(url):
    with pytest.raises(ValueError):
        client_module.validate_url(url)
