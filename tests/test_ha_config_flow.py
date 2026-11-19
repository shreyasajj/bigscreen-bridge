"""Config flow paths not covered by test_homeassistant.py: discovery, unreachable or
misbehaving bridges, bad addresses and reauthentication, each ending in the right form error."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from custom_components.bigscreen_bridge.config_flow import ConfigFlow

DEAD = "http://127.0.0.1:9"    # nothing listens on the discard port


def flow(hass):
    f = ConfigFlow()
    f.hass = hass
    f.context = {}
    f.async_set_unique_id = AsyncMock(side_effect=lambda device_id: f.context.update(unique_id=device_id))
    f._abort_if_unique_id_configured = Mock()
    f.async_abort = Mock(side_effect=lambda **kwargs: {"type": "abort", **kwargs})
    f.async_create_entry = Mock(side_effect=lambda **kwargs: {"type": "create_entry", **kwargs})
    f.async_show_form = Mock(side_effect=lambda **kwargs: {"type": "form", **kwargs})
    f.async_update_reload_and_abort = Mock(side_effect=lambda entry, **kwargs: {"type": "abort", **kwargs})
    return f


def announcement(http, **properties):
    url = http.make_url("")
    props = {"device_id": "test-device", "protocol": "1", "name": "TV"} | properties
    return SimpleNamespace(host=url.host, port=url.port, properties={k: v for k, v in props.items() if v is not None})


@pytest.mark.parametrize("properties", [{"device_id": None}, {"protocol": "2"}])
async def test_zeroconf_ignores_other_protocols(coordinator, bridge, properties):
    _, http = bridge
    f = flow(coordinator.hass)
    assert (await f.async_step_zeroconf(announcement(http, **properties)))["reason"] == "not_supported"


async def test_zeroconf_ignores_a_bad_port(coordinator, bridge):
    _, http = bridge
    f = flow(coordinator.hass)
    found = announcement(http)
    found.port = 99999
    assert (await f.async_step_zeroconf(found))["reason"] == "not_supported"


async def test_zeroconf_then_pair_creates_the_entry(coordinator, bridge):
    _, http = bridge
    f = flow(coordinator.hass)
    form = await f.async_step_zeroconf(announcement(http, name="Living room"))
    assert form["step_id"] == "pair" and f.context["title_placeholders"] == {"name": "Living room"}
    f._abort_if_unique_id_configured.assert_called_once_with(updates={"url": str(http.make_url("")).rstrip("/")})
    f.context["unique_id"] = "test-device"
    created = await f.async_step_pair({"code": "a" * 48})
    assert created["type"] == "create_entry" and created["data"]["token"] == "a" * 48


async def test_zeroconf_ipv6_host_is_bracketed(coordinator):
    f = flow(coordinator.hass)
    found = SimpleNamespace(host="fe80::1", port=8765, properties={"device_id": "d", "protocol": "1"})
    await f.async_step_zeroconf(found)
    assert f._found["url"] == "http://[fe80::1]:8765"


@pytest.mark.parametrize("code,error", [("b" * 48, "invalid_auth"), ("a" * 48, "wrong_device")])
async def test_pair_step_errors(coordinator, bridge, code, error):
    _, http = bridge
    f = flow(coordinator.hass)
    f._found = {"url": str(http.make_url("")).rstrip("/"), "name": "TV"}
    f.context["unique_id"] = "another-device"
    result = await f.async_step_pair({"code": code})
    assert result["errors"]["base"] == error
    f.async_create_entry.assert_not_called()


async def test_pair_step_bridge_gone(coordinator):
    f = flow(coordinator.hass)
    f._found = {"url": DEAD, "name": "TV"}
    assert (await f.async_step_pair({"code": "a" * 48}))["errors"]["base"] == "cannot_connect"


@pytest.mark.parametrize("url,error", [("ftp://tv", "invalid_url"), ("http://tv/path", "invalid_url"),
                                       (DEAD, "cannot_connect")])
async def test_user_step_address_errors(coordinator, url, error):
    f = flow(coordinator.hass)
    assert (await f.async_step_user({"url": url, "code": "a" * 48}))["errors"]["base"] == error
    f.async_create_entry.assert_not_called()


async def test_user_step_rate_limited_pairing(coordinator, bridge, tmp_path):
    from bigscreen_bridge.pairing import Pairing
    b, http = bridge
    b.pairing = Pairing(tmp_path / "clients.json")
    f = flow(coordinator.hass)
    url = str(http.make_url("")).rstrip("/")
    errors = [(await f.async_step_user({"url": url, "code": "2222-2222"}))["errors"]["base"] for _ in range(21)]
    assert errors[0] == "invalid_auth" and errors[-1] == "too_many_attempts"


async def test_reauth_accepts_a_new_token_for_the_same_device(coordinator):
    f = flow(coordinator.hass)
    f._get_reauth_entry = Mock(return_value=coordinator.entry)
    assert (await f.async_step_reauth({}))["step_id"] == "reauth_confirm"
    result = await f.async_step_reauth_confirm({"code": "a" * 48})
    assert result == {"type": "abort", "data_updates": {"token": "a" * 48}}


@pytest.mark.parametrize("url,code,error", [(None, "b" * 48, "invalid_auth"), (DEAD, "a" * 48, "cannot_connect")])
async def test_reauth_errors(coordinator, url, code, error):
    f = flow(coordinator.hass)
    entry = SimpleNamespace(data={"url": url or coordinator.entry.data["url"]}, unique_id="test-device")
    f._get_reauth_entry = Mock(return_value=entry)
    assert (await f.async_step_reauth_confirm({"code": code}))["errors"]["base"] == error
    f.async_update_reload_and_abort.assert_not_called()
