"""Optional MQTT mirror of the WebSocket API for automations that already speak MQTT.

Topics under `<base_topic>/<device_id>/`:
  availability  retained `online` / `offline` (also the broker's last will)
  state         retained snapshot, the same object WebSocket clients receive
  command       JSON requests, accepted only when `commands` is enabled
  response      one acknowledgement per request, carrying the request ID

This never creates Home Assistant entities (no MQTT Discovery) and is separate
from Stremio TV's own MQTT link. Anyone allowed to publish on the broker can send
commands, so command handling is opt-in and the broker is the trust boundary.
"""

import asyncio
import contextlib
import json
import logging
import re
import ssl
from collections import OrderedDict

from .diagnostics import NULL

LOG = logging.getLogger(__name__)
_TOPIC = re.compile(r"[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*")


def settings(config):
    """Validated MQTT options, or `None` when the transport is not configured."""
    raw = config.get("mqtt")
    if not raw:
        return None
    base = raw.get("base_topic", "bigscreen") if isinstance(raw, dict) else None
    port = raw.get("port", 1883) if isinstance(raw, dict) else None
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("host"), str)
        or not raw["host"]
        or type(port) is not int
        or not 1 <= port <= 65535
        or not isinstance(base, str)
        or not _TOPIC.fullmatch(base)
        or not _TOPIC.fullmatch(config["device_id"])
        or any(not isinstance(raw.get(key, ""), str) for key in ("username", "password"))
        or any(not isinstance(raw.get(key, False), bool) for key in ("tls", "commands"))
    ):
        raise ValueError("Invalid MQTT settings")
    return {
        "host": raw["host"],
        "port": port,
        "username": raw.get("username") or None,
        "password": raw.get("password") or None,
        "tls": raw.get("tls", False),
        "commands": raw.get("commands", False),
        "base": f"{base}/{config['device_id']}",
    }


def connect(options):
    """A broker session; imported lazily so the dependency stays optional."""
    import aiomqtt

    return aiomqtt.Client(
        hostname=options["host"],
        port=options["port"],
        username=options["username"],
        password=options["password"],
        tls_context=ssl.create_default_context() if options["tls"] else None,
        will=aiomqtt.Will(options["base"] + "/availability", "offline", qos=1, retain=True),
    )


class MqttLink:
    def __init__(self, bridge, options, connect=connect, retry=5):
        self.bridge, self.options, self.connect, self.retry = bridge, options, connect, retry
        self.base = options["base"]
        self.client = None
        self.cache = OrderedDict()
        self.recorder = getattr(bridge, "diagnostics", NULL)
        self.recorder.mqtt_configured(options, retry)

    async def publish_state(self):
        if self.client:
            await self.client.publish(
                self.base + "/state", json.dumps(self.bridge.snapshot()), qos=1, retain=True
            )

    async def _command(self, payload):
        if len(payload) > 16384:
            response = {
                "type": "result",
                "id": None,
                "ok": False,
                "error": {"code": "invalid_request", "message": "Request too large"},
            }
        else:
            response = await self.bridge.handle(payload, self.cache, transport="mqtt")
        await self.client.publish(self.base + "/response", json.dumps(response), qos=1)

    async def session(self):
        async with self.connect(self.options) as client:
            self.client = client
            try:
                await client.publish(self.base + "/availability", "online", qos=1, retain=True)
                self.recorder.mqtt_connected()
                await self.publish_state()
                if self.options["commands"]:
                    await client.subscribe(self.base + "/command", qos=1)
                async for message in client.messages:
                    payload = message.payload
                    await self._command(
                        payload.decode(errors="replace") if isinstance(payload, bytes) else str(payload)
                    )
            except asyncio.CancelledError:
                # A clean stop says so itself instead of waiting for the broker's last will.
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(
                        client.publish(self.base + "/availability", "offline", qos=1, retain=True), 2
                    )
                raise
            finally:
                self.client = None
                self.recorder.mqtt_disconnected()

    async def run(self):
        self.bridge.publishers.append(self.publish_state)
        try:
            while True:
                try:
                    await self.session()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # broker errors must never take the bridge down
                    self.recorder.mqtt_disconnected(exc)
                    LOG.warning("MQTT link lost: %s", type(exc).__name__)
                await asyncio.sleep(self.retry)
        finally:
            self.bridge.publishers.remove(self.publish_state)
