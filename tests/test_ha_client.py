"""The integration's protocol client against stand-in bridges that misbehave.

The happy paths run against the real bridge in test_bridge.py and test_homeassistant.py;
these cover what the client does when a bridge, or something pretending to be one, does
not answer the way the protocol says.
"""
import asyncio
import importlib.util
from pathlib import Path

import aiohttp
from aiohttp import web
import pytest

spec = importlib.util.spec_from_file_location(
    "ha_client_under_test",
    Path(__file__).parents[2] / "ha-bigscreen-bridge/custom_components/bigscreen_bridge/client.py")
client_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client_module)
BridgeClient = client_module.BridgeClient
BridgeClientError = client_module.BridgeClientError
BridgeAuthError = client_module.BridgeAuthError

STATE = {"type": "state", "protocol": 1, "device_id": "fake-device", "apps": []}


@pytest.mark.parametrize("typed,expected", [
    (" 23ab cd45 ", "23ABCD45"), ("23AB-CD45", "23ABCD45"),
    ("a" * 48, None), ("0000 0000", None), ("", None), (None, None)])
def test_pairing_code_normalises_and_rejects(typed, expected):
    assert client_module.pairing_code(typed) == expected


@pytest.mark.parametrize("url", ["ftp://tv", "http://", "http://tv/#x", "https://u@tv"])
def test_validate_url_rejects_other_shapes(url):
    with pytest.raises(ValueError):
        client_module.validate_url(url)


def test_validate_url_keeps_a_plain_address():
    assert client_module.validate_url("http://tv.local:8765/") == "http://tv.local:8765"


async def stub(aiohttp_server, handler, path="/pair", method="POST"):
    app = web.Application()
    app.router.add_route(method, path, handler)
    server = await aiohttp_server(app)
    return str(server.make_url("")).rstrip("/")


def reply(status, body=None, text=None):
    async def handler(_request):
        if text is not None:
            return web.Response(status=status, text=text)
        return web.json_response(body, status=status)
    return handler


@pytest.mark.parametrize("status,error,code", [
    (403, BridgeAuthError, "invalid_code"), (429, BridgeAuthError, "rate_limited"),
    (500, BridgeClientError, "internal")])
async def test_pair_refusals_carry_the_bridge_error(aiohttp_server, status, error, code):
    url = await stub(aiohttp_server, reply(status, {"error": {"code": code, "message": "No."}}))
    async with aiohttp.ClientSession() as session:
        with pytest.raises(error) as raised:
            await client_module.pair(session, url, "23ABCD45")
    assert raised.value.code == code
    assert str(raised.value) == "No."


async def test_pair_refusal_without_details_still_explains(aiohttp_server):
    url = await stub(aiohttp_server, reply(403, ["not", "an", "object"]))
    async with aiohttp.ClientSession() as session:
        with pytest.raises(BridgeAuthError, match="Pairing was refused"):
            await client_module.pair(session, url, "23ABCD45")


async def test_pair_ok_without_a_token_is_a_failure(aiohttp_server):
    url = await stub(aiohttp_server, reply(200, {"token": 7}))
    async with aiohttp.ClientSession() as session:
        with pytest.raises(BridgeClientError, match="Pairing failed") as raised:
            await client_module.pair(session, url, "23ABCD45")
    assert type(raised.value) is BridgeClientError


async def test_pair_unexpected_reply(aiohttp_server):
    url = await stub(aiohttp_server, reply(200, text="<html>router login</html>"))
    async with aiohttp.ClientSession() as session:
        with pytest.raises(BridgeClientError, match="Unexpected pairing reply"):
            await client_module.pair(session, url, "23ABCD45")


def socket(*frames, then=None):
    """A WebSocket endpoint that sends `frames` (dicts as JSON, bytes as binary), then waits."""
    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        for frame in frames:
            if isinstance(frame, bytes):
                await ws.send_bytes(frame)
            elif isinstance(frame, str):
                await ws.send_str(frame)
            else:
                await ws.send_json(frame)
        if then:
            await then(ws)
        else:
            async for _ in ws:
                pass
        return ws
    return handler


async def test_connect_bad_token_is_auth_error(bridge):
    _, http = bridge
    async with aiohttp.ClientSession() as session:
        client = BridgeClient(session, str(http.make_url("")), "b" * 48)
        with pytest.raises(BridgeAuthError):
            await client.connect()


async def test_handshake_failure_that_is_not_auth(aiohttp_server):
    url = await stub(aiohttp_server, reply(500, {}), path="/ws", method="GET")
    async with aiohttp.ClientSession() as session:
        with pytest.raises(BridgeClientError, match="handshake failed") as raised:
            await BridgeClient(session, url, "a" * 48).connect()
    assert type(raised.value) is BridgeClientError


@pytest.mark.parametrize("first", [dict(STATE, protocol=99), dict(STATE, type="hello"),
                                   dict(STATE, device_id=5), dict(STATE, apps={})])
async def test_connect_unsupported_protocol_closes(aiohttp_server, first):
    url = await stub(aiohttp_server, socket(first), path="/ws", method="GET")
    async with aiohttp.ClientSession() as session:
        client = BridgeClient(session, url, "a" * 48)
        with pytest.raises(BridgeClientError, match="Unsupported bridge protocol"):
            await client.connect()
        assert client.ws.closed
        assert client.reader is None


async def test_command_while_disconnected():
    async with aiohttp.ClientSession() as session:
        client = BridgeClient(session, "http://tv", "a" * 48)
        with pytest.raises(BridgeClientError, match="disconnected"):
            await client.command("play")


async def test_command_error_carries_code(bridge):
    _, http = bridge
    async with aiohttp.ClientSession() as session:
        client = BridgeClient(session, str(http.make_url("")), "a" * 48)
        await client.connect()
        with pytest.raises(BridgeClientError) as raised:
            await client.command("no_such_command")
        assert raised.value.code
        await client.close()


@pytest.mark.parametrize("bad", ["[]", "not json", b"\x00binary"])
async def test_malformed_frame_disconnects_and_fails_pending(aiohttp_server, bad):
    command_seen = asyncio.Event()

    async def after(ws):
        await ws.receive()          # the client's command; never answered
        command_seen.set()
        await (ws.send_bytes(bad) if isinstance(bad, bytes) else ws.send_str(bad))
        async for _ in ws:
            pass

    url = await stub(aiohttp_server, socket(STATE, then=after), path="/ws", method="GET")
    disconnected = []
    async with aiohttp.ClientSession() as session:
        client = BridgeClient(session, url, "a" * 48, on_disconnect=lambda: disconnected.append(1))
        await client.connect()
        with pytest.raises(BridgeClientError, match="Bridge disconnected"):
            await asyncio.wait_for(client.command("play"), 2)
        await asyncio.wait_for(client.reader, 2)
        assert command_seen.is_set()
        assert disconnected == [1]
        assert client.pending == {}
        await client.close()


async def test_state_pushes_reach_the_callback(aiohttp_server):
    pushed = dict(STATE, active_app="stremio")
    url = await stub(aiohttp_server, socket(STATE, pushed), path="/ws", method="GET")
    states = []
    async with aiohttp.ClientSession() as session:
        client = BridgeClient(session, url, "a" * 48, on_state=states.append)
        await client.connect()
        async with asyncio.timeout(2):
            while not states:
                await asyncio.sleep(0.01)
        assert client.state["active_app"] == "stremio"
        await client.close()
