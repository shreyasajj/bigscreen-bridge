"""Pairing codes shown on the TV and the per-client tokens they are exchanged for."""
import json
import logging
import os
import stat

import pytest
from bigscreen_bridge.adapters import BridgeError
from bigscreen_bridge.pairing import ALPHABET, LIFETIME, MAX_FAILURES, Pairing, normalize
from bigscreen_bridge.status import PAIRING_INTERFACE, PairingInterface

AUTH = {"Authorization": "Bearer " + "a" * 48}


@pytest.fixture
def pairing(tmp_path, clock):
    return Pairing(tmp_path / "clients.json", clock=lambda: clock().timestamp(), monotonic=clock.monotonic)


def test_codes_are_readable_and_typing_is_forgiving():
    assert not set("0O1IlU") & set(ALPHABET)
    assert normalize("7kq4-mxd2") == normalize(" 7KQ4 MXD2 ") == "7KQ4MXD2"
    for bad in ("7KQ4-MXD", "7KQ4-MXD0", "OOOOOOOO", None, 12345678, "x" * 40):
        assert normalize(bad) is None


def test_a_code_stays_available_until_something_pairs(pairing, clock):
    shown = pairing.public()
    assert shown["reason"] == "unpaired" and len(shown["code"]) == 9 and shown["code"][4] == "-"
    assert shown["expires_at"] == "2026-11-17T16:47:00.123Z"
    clock.tick(LIFETIME + 1)
    renewed = pairing.public()
    assert renewed["code"] != shown["code"] and renewed["reason"] == "unpaired"
    token = pairing.redeem(renewed["code"], "Home Assistant")
    assert pairing.public() is None   # paired: the code is gone and none replaces it
    assert pairing.accepts(token) and not pairing.accepts(token + "x") and not pairing.accepts("")
    pairing.start()
    assert pairing.public()["reason"] == "requested"
    clock.tick(LIFETIME + 1)
    assert pairing.public() is None   # a requested code is not renewed by itself


def test_each_pairing_gets_its_own_token_and_only_its_hash_is_stored(pairing, tmp_path):
    first = pairing.redeem(pairing.public()["code"], "Home Assistant")
    pairing.start()
    second = pairing.redeem(pairing.public()["code"].lower(), "Second HA")
    assert first != second and pairing.accepts(first) and pairing.accepts(second)
    path = tmp_path / "clients.json"
    text = path.read_text()
    assert first not in text and second not in text
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    reloaded = Pairing(path)
    assert reloaded.accepts(first) and reloaded.accepts(second) and not reloaded.automatic
    assert [c["name"] for c in json.loads(text)["clients"]] == ["Home Assistant", "Second HA"]


def test_wrong_codes_are_limited(pairing, clock):
    code = pairing.public()["code"]
    for _ in range(MAX_FAILURES - 1):
        with pytest.raises(BridgeError, match="wrong or has expired"):
            pairing.redeem("2222-2222", "x")
    with pytest.raises(BridgeError):
        pairing.redeem("2222-2222", "x")
    # Withdrawn after five misses; while unpaired a new one takes its place.
    assert pairing.public()["code"] != code
    with pytest.raises(BridgeError) as error:
        pairing.redeem(code, "x")
    assert error.value.code == "invalid_code"
    for _ in range(20):
        with pytest.raises(BridgeError):
            pairing.redeem("bad", "x")
    with pytest.raises(BridgeError) as error:
        pairing.redeem(pairing.public()["code"], "x")
    assert error.value.code == "rate_limited"
    clock.tick(61)
    with pytest.raises(BridgeError) as error:
        pairing.redeem(pairing.public()["code"], "\x00bad name")
    assert error.value.code == "invalid_request"


async def test_pair_endpoint_issues_a_token_the_websocket_accepts(bridge, pairing, recorder, caplog):
    b, http = bridge
    b.diagnostics = recorder
    b.pairing = pairing
    caplog.set_level(logging.INFO, logger="bigscreen_bridge")
    code = pairing.public()["code"]
    assert (await http.post("/pair", json={"code": "2222-2222", "name": "HA"})).status == 403
    assert (await http.post("/pair", json={"code": code, "name": "HA"},
                            headers={"Origin": "http://evil"})).status == 400
    assert (await http.post("/pair", json={"code": code, "name": "HA", "extra": 1})).status == 400
    assert (await http.post("/pair", data=b"not json")).status == 400
    reply = await http.post("/pair", json={"code": code, "name": "Home Assistant"})
    body = await reply.json()
    assert reply.status == 200 and body["device_id"] == "test-device" and body["name"] == "TV"
    async with http.ws_connect("/ws", headers={"Authorization": "Bearer " + body["token"]}) as ws:
        assert (await ws.receive_json())["type"] == "state"
    async with http.ws_connect("/ws", headers=AUTH) as ws:   # the configured token still works
        assert (await ws.receive_json())["type"] == "state"
    assert (await http.post("/pair", json={"code": code, "name": "HA"})).status == 403   # used up
    diagnostics = json.dumps(b.diagnostics.to_dict(b))
    assert code.replace("-", "") not in diagnostics and code not in diagnostics
    assert body["token"] not in diagnostics and body["token"] not in caplog.text
    assert code not in caplog.text and code.replace("-", "") not in caplog.text
    summary = b.diagnostics.to_dict(b)["pairing"]
    assert summary["paired_clients"] == 1 and summary["last_paired_name"] == "Home Assistant"
    assert not summary["active"]
    assert b.status()["pairing"] is None


async def test_without_pairing_the_endpoint_says_so(bridge):
    _, http = bridge
    assert (await http.post("/pair", json={"code": "2222-2222", "name": "HA"})).status == 404


def test_the_only_session_bus_write_starts_pairing(pairing):
    calls = []
    interface = PairingInterface(lambda: calls.append(1))
    introspection = interface.introspect()
    assert introspection.name == PAIRING_INTERFACE
    assert [(m.name, m.in_args, m.out_args) for m in introspection.methods] == [("StartPairing", [], [])]
    assert not introspection.signals and not introspection.properties
    PairingInterface._get_methods(interface)[0].fn(interface)
    assert calls == [1]


def test_revoking_a_client_takes_effect_without_a_restart(pairing, tmp_path, capsys):
    from bigscreen_bridge.pairing import main
    token = pairing.redeem(pairing.public()["code"], "Old HA")
    client_id = pairing.clients[0]["id"]
    assert main(["--config-dir", str(tmp_path), "list"]) == 0
    listed = capsys.readouterr().out
    assert client_id in listed and "Old HA" in listed and token not in listed
    assert main(["--config-dir", str(tmp_path), "revoke", "nope"]) == 1
    assert main(["--config-dir", str(tmp_path), "revoke", client_id]) == 0
    assert not pairing.accepts(token)            # the running bridge notices the file change
    assert pairing.public()["reason"] == "unpaired"   # nothing paired again: a code returns
