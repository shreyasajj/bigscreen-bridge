"""The diagnostics command and the token-free, read-only Status interface on the session bus."""
import asyncio
import json
from unittest.mock import Mock

from bigscreen_bridge.mqtt import MqttLink, settings
from bigscreen_bridge.status import INTERFACE, StatusInterface, StatusSignal

from test_diagnostics import AUTH, Broker, wire


async def test_diagnostics_command_over_websocket(bridge, recorder):
    b, http = bridge
    wire(b, recorder)
    async with http.ws_connect("/ws", headers=AUTH) as ws:
        state = await ws.receive_json()
        assert "diagnostics" not in state
        await ws.send_json({"id": "d1", "command": "diagnostics"})
        reply = await ws.receive_json()
        assert reply["ok"] and reply["id"] == "d1"
        assert set(reply["result"]) == {"schema", "generated_at", "service", "websocket", "discovery", "mqtt",
                                        "kwin", "pairing", "hooks", "mpris", "commands", "warnings"}
        assert reply["result"]["websocket"]["clients"] == 1
        await ws.send_json({"id": "d2", "command": "diagnostics", "app": "stremio"})
        reply = await ws.receive_json()
        assert not reply["ok"] and reply["error"]["code"] == "invalid_request"
    assert "diagnostics" not in b.snapshot()


async def test_mqtt_state_topic_carries_no_diagnostics(bridge, recorder):
    b, _ = bridge
    wire(b, recorder)
    broker = Broker()
    broker.payloads = []

    async def publish(topic, payload, qos=0, retain=False):
        broker.payloads.append((topic, payload))
    broker.publish = publish
    await MqttLink(b, settings({**b.config, "mqtt": {"host": "broker"}}), connect=lambda _: broker).session()
    state = next(payload for topic, payload in broker.payloads if topic.endswith("/state"))
    assert "diagnostics" not in json.loads(state)


class FakeBus:
    """Collects what dbus-next would broadcast for an exported interface."""

    def __init__(self):
        self.signals = []

    def _interface_signal_notify(self, interface, name, member, signature, body, fds):
        self.signals.append((name, member, signature, body))


async def test_status_interface_is_read_only_and_signals_without_data(bridge, recorder):
    b, _ = bridge
    wire(b, recorder)
    status = StatusInterface(lambda: json.dumps(b.status()))
    bus = FakeBus()
    StatusInterface._add_bus(status, bus)
    introspection = status.introspect()
    assert introspection.name == INTERFACE == "org.bigscreen.Bridge.Status"
    assert [m.name for m in introspection.methods] == ["Status"]
    assert [a.signature for a in introspection.methods[0].in_args] == []
    assert [a.signature for a in introspection.methods[0].out_args] == ["s"]
    assert [s.name for s in introspection.signals] == ["StatusChanged"] and not introspection.signals[0].args
    assert not introspection.properties
    status_method = StatusInterface._get_methods(status)[0]   # what the bus invokes for a call
    data = json.loads(status_method.fn(status))
    assert set(data) == {"snapshot", "diagnostics", "pairing"} and data["snapshot"] == b.snapshot()
    assert "a" * 48 not in json.dumps(data)
    status.StatusChanged()
    assert bus.signals == [(INTERFACE, "StatusChanged", "", [])]


async def test_status_changed_is_rate_limited(clock):
    emit = Mock()
    throttle = StatusSignal(emit, interval=0.05)
    for _ in range(10):
        throttle()
    await asyncio.sleep(0.01)
    assert emit.call_count == 1   # the first change goes out at once
    for _ in range(10):
        throttle()
    await asyncio.sleep(0.01)
    assert emit.call_count == 1   # the rest wait for the interval and are coalesced
    await asyncio.sleep(0.06)
    assert emit.call_count == 2
    StatusSignal(emit)()  # outside a running loop it does nothing rather than raise


async def test_bridge_changes_reach_the_status_signal(bridge, recorder):
    b, _ = bridge
    wire(b, recorder)
    seen = Mock()
    recorder.listeners.append(seen)
    await b.notify()
    recorder.kwin_report()
    assert seen.call_count == 2
