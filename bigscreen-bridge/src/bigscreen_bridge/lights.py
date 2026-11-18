"""LedFx and HyperHDR on the TV: their state and a few controls, over their local APIs.

LedFx answers HTTP (`ledfx_url`). HyperHDR's state comes from `serverinfo` over HTTP
JSON-RPC (`hyperhdr_url`); its commands go over its JSON socket (`hyperhdr_socket`),
because a command for one instance needs `instance switchTo` earlier in the same
session. Whether each service runs comes from systemd over D-Bus: LedFx as a user
unit, HyperHDR as a system unit. Everything is localhost and needs no credentials. A
service that is down only marks its own section unavailable.
"""
import asyncio
import itertools
import json
import logging
import re

import aiohttp

from .adapters import BridgeError
from .runtime_io import call, properties

LOG = logging.getLogger(__name__)

COMMANDS = ("ledfx_pause", "ledfx_light", "ledfx_scene", "hyperhdr_light")
TIMEOUT = 3
PRIORITY = 50
ORIGIN = "Bigscreen Bridge"
VIDEO = "Video"       # HyperHDR "effect" that clears our colour or effect and returns to capture
HEX = re.compile(r"#[0-9a-fA-F]{6}")
SYSTEMD = "org.freedesktop.systemd1"


async def systemd_state(bus, unit):
    """`active`, `inactive`, `failed`, … or `unknown`."""
    try:
        path = (await call(bus, SYSTEMD, "/org/freedesktop/systemd1", SYSTEMD + ".Manager", "LoadUnit", "s", [unit]))[0]
        return (await properties(bus, SYSTEMD, path, SYSTEMD + ".Unit")).get("ActiveState") or "unknown"
    except (BridgeError, IndexError, TypeError, AttributeError):
        return "unknown"


def _number(value, low, high):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high


class Lights:
    def __init__(self, config, session_bus=None, system_bus=None, user=None, unit_state=systemd_state):
        self.enabled = config.get("lights", True) is not False
        self.ledfx_url = config.get("ledfx_url", "http://127.0.0.1:8888").rstrip("/")
        self.hyperhdr_url = config.get("hyperhdr_url", "http://127.0.0.1:8090").rstrip("/")
        host, _, port = config.get("hyperhdr_socket", "127.0.0.1:19444").rpartition(":")
        self.socket_address = (host or "127.0.0.1", int(port or 19444))
        self.ledfx_unit = config.get("ledfx_unit", "ledfx.service")
        self.hyperhdr_unit = config.get("hyperhdr_unit", f"hyperhdr@{user}.service" if user else "hyperhdr.service")
        self.session_bus, self.system_bus, self.unit_state = session_bus, system_bus, unit_state
        self.state = {}
        self.http = None
        self.schema = None          # LedFx effect types: {type: {"name", "keys"}}
        self.tans = itertools.count(1)

    async def close(self):
        if self.http:
            await self.http.close()

    def _client(self):
        if self.http is None:
            self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT))
        return self.http

    # -- reading ------------------------------------------------------------------

    async def refresh(self):
        """Re-read both; True when anything a client would show changed."""
        if not self.enabled:
            return False
        before = self.state
        ledfx, hyperhdr = await asyncio.gather(self._read_ledfx(), self._read_hyperhdr())
        self.state = {"ledfx": ledfx, "hyperhdr": hyperhdr}
        return self.state != before

    async def _ledfx_get(self, path):
        async with self._client().get(self.ledfx_url + path) as response:
            response.raise_for_status()
            return await response.json(content_type=None)

    async def _read_ledfx(self):
        service = await self.unit_state(self.session_bus, self.ledfx_unit)
        try:
            info, virtuals, devices, scenes = await asyncio.gather(
                self._ledfx_get("/api/info"), self._ledfx_get("/api/virtuals"),
                self._ledfx_get("/api/devices"), self._ledfx_get("/api/scenes"))
            if self.schema is None:
                effects = (await self._ledfx_get("/api/schema")).get("effects") or {}
                self.schema = {kind: {"name": (spec.get("name") or kind),
                                      "keys": set(((spec.get("schema") or spec).get("properties") or {}))}
                               for kind, spec in effects.items() if isinstance(spec, dict)}
        except (aiohttp.ClientError, TimeoutError, ValueError, AttributeError):
            self.schema = None
            return {"available": False, "service": service,
                    "reason": "service_inactive" if service in ("inactive", "failed") else "not_reachable"}
        rows = []
        for vid, virtual in (virtuals.get("virtuals") or {}).items():
            effect = virtual.get("effect") or {}
            config = effect.get("config") or {}
            rows.append({"id": vid, "name": (virtual.get("config") or {}).get("name") or vid,
                         "active": bool(virtual.get("active")), "effect": effect.get("type"),
                         "effect_name": effect.get("name"), "brightness": config.get("brightness"),
                         "color": config.get("color") if HEX.fullmatch(str(config.get("color", ""))) else None})
        found = devices.get("devices") or {}
        offline = sorted((d.get("config") or {}).get("name") or did for did, d in found.items() if not d.get("online"))
        return {"available": True, "reason": None, "service": service, "version": info.get("version"),
                "paused": bool(virtuals.get("paused")), "virtuals": rows,
                "devices": {"total": len(found), "online": len(found) - len(offline), "offline": offline},
                "scenes": [{"id": sid, "name": s.get("name") or sid, "active": bool(s.get("active"))}
                           for sid, s in (scenes.get("scenes") or {}).items()],
                "effects": sorted(({"type": k, "name": v["name"]} for k, v in self.schema.items()),
                                  key=lambda e: e["name"].lower())}

    async def _read_hyperhdr(self):
        service = await self.unit_state(self.system_bus, self.hyperhdr_unit)
        try:
            async with self._client().post(self.hyperhdr_url + "/json-rpc",
                                           json={"command": "serverinfo", "tan": next(self.tans)}) as response:
                response.raise_for_status()
                reply = await response.json(content_type=None)
            info = reply["info"]
        except (aiohttp.ClientError, TimeoutError, ValueError, KeyError, TypeError):
            return {"available": False, "service": service,
                    "reason": "service_inactive" if service in ("inactive", "failed") else "not_reachable"}
        components = {c.get("name"): bool(c.get("enabled")) for c in info.get("components") or []}
        return {"available": True, "reason": None, "service": service, "output": components.get("ALL"),
                "grabber": (info.get("grabbers") or {}).get("active") or None,
                "last_error": info.get("lastError") or None,
                "effects": [e.get("name") for e in info.get("effects") or [] if e.get("name")],
                "instances": [{"id": i.get("instance"), "name": i.get("friendly_name") or f"Instance {i.get('instance')}",
                               "running": bool(i.get("running"))} for i in info.get("instance") or []]}

    # -- commands -------------------------------------------------------------------

    def _section(self, name, label):
        section = self.state.get(name) or {}
        if not section.get("available"):
            raise BridgeError("service_unavailable", f"{label} is not available")
        return section

    async def execute(self, command, request):
        if command == "ledfx_pause":
            result = await self._ledfx_pause(request)
        elif command == "ledfx_light":
            result = await self._ledfx_light(request)
        elif command == "ledfx_scene":
            result = await self._ledfx_scene(request)
        elif command == "hyperhdr_light":
            result = await self._hyperhdr_light(request)
        else:
            raise BridgeError("unsupported", "Unknown lights command")
        await self.refresh()
        return result

    async def _ledfx_send(self, method, path, body=None):
        try:
            async with self._client().request(method, self.ledfx_url + path, json=body) as response:
                response.raise_for_status()
                return await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise BridgeError("service_unavailable", "LedFx did not accept the change") from exc

    async def _ledfx_pause(self, request):
        paused = request.get("paused")
        if not isinstance(paused, bool):
            raise BridgeError("invalid_request", "paused must be true or false")
        self._section("ledfx", "LedFx")
        try:
            current = bool((await self._ledfx_get("/api/virtuals")).get("paused"))
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise BridgeError("service_unavailable", "LedFx did not answer") from exc
        if current != paused:              # LedFx only offers a toggle
            await self._ledfx_send("PUT", "/api/virtuals")
        return {"paused": paused}

    async def _ledfx_light(self, request):
        section = self._section("ledfx", "LedFx")
        virtual = next((v for v in section["virtuals"] if v["id"] == request.get("virtual")), None)
        if virtual is None:
            raise BridgeError("invalid_request", "Unknown LedFx virtual")
        on, effect, brightness, color = (request.get(k) for k in ("on", "effect", "brightness", "color"))
        if on is None and effect is None and brightness is None and color is None:
            raise BridgeError("invalid_request", "Nothing to change")
        if on is not None and not isinstance(on, bool):
            raise BridgeError("invalid_request", "on must be true or false")
        if effect is not None and effect not in (self.schema or {}):
            raise BridgeError("invalid_request", "Unknown LedFx effect")
        if brightness is not None and not _number(brightness, 0, 1):
            raise BridgeError("invalid_request", "brightness must be between 0 and 1")
        if color is not None and not (isinstance(color, str) and HEX.fullmatch(color)):
            raise BridgeError("invalid_request", "color must be #rrggbb")
        if brightness is not None and effect is None and virtual["effect"] is None:
            raise BridgeError("unsupported", "This virtual has no effect running")
        base = f"/api/virtuals/{virtual['id']}"
        # LedFx updates the running effect only when `type` names it; without it, a PUT
        # replaces the effect with nothing (and saves that). Every PUT below carries it.
        current = effect or virtual["effect"]
        level = brightness if brightness is not None else virtual["brightness"]
        if effect is not None:
            await self._ledfx_send("POST", base + "/effects",
                                   {"type": effect, "config": {} if level is None else {"brightness": level}})
        if color is not None:
            keys = (self.schema or {}).get(current, {}).get("keys", set())
            if "color" in keys:
                await self._ledfx_send("PUT", base + "/effects", {"type": current, "config": {"color": color}})
            elif "gradient" in keys:
                solid = f"linear-gradient(90deg, {color} 0.00%, {color} 100.00%)"
                await self._ledfx_send("PUT", base + "/effects", {"type": current, "config": {"gradient": solid}})
            else:
                config = {"color": color, **({} if level is None else {"brightness": level})}
                await self._ledfx_send("POST", base + "/effects", {"type": "singleColor", "config": config})
        if brightness is not None and effect is None:
            await self._ledfx_send("PUT", base + "/effects", {"type": current, "config": {"brightness": brightness}})
        if on is not None:
            await self._ledfx_send("PUT", base, {"active": on})
        return {"virtual": virtual["id"]}

    async def _ledfx_scene(self, request):
        section = self._section("ledfx", "LedFx")
        if request.get("scene") not in {s["id"] for s in section["scenes"]} or not isinstance(request.get("active"), bool):
            raise BridgeError("invalid_request", "A known scene and active true or false are required")
        action = "activate" if request["active"] else "deactivate"
        await self._ledfx_send("PUT", "/api/scenes", {"id": request["scene"], "action": action})
        return {"scene": request["scene"], "active": request["active"]}

    async def _hyperhdr_light(self, request):
        section = self._section("hyperhdr", "HyperHDR")
        instance = request.get("instance")
        if isinstance(instance, bool) or instance not in {i["id"] for i in section["instances"]}:
            raise BridgeError("invalid_request", "Unknown HyperHDR instance")
        on, effect, brightness, color = (request.get(k) for k in ("on", "effect", "brightness", "color"))
        if on is None and effect is None and brightness is None and color is None:
            raise BridgeError("invalid_request", "Nothing to change")
        if on is not None and not isinstance(on, bool):
            raise BridgeError("invalid_request", "on must be true or false")
        if effect is not None and effect != VIDEO and effect not in section["effects"]:
            raise BridgeError("invalid_request", "Unknown HyperHDR effect")
        if brightness is not None and not _number(brightness, 0, 1):
            raise BridgeError("invalid_request", "brightness must be between 0 and 1")
        if color is not None and not (isinstance(color, list) and len(color) == 3
                                      and all(isinstance(c, int) and not isinstance(c, bool) and 0 <= c <= 255
                                              for c in color)):
            raise BridgeError("invalid_request", "color must be [r, g, b] from 0 to 255")
        messages = []
        if on is not None:
            messages.append({"command": "instance", "subcommand": "startInstance" if on else "stopInstance",
                             "instance": instance})
        steps = []
        if color is not None:
            steps.append({"command": "color", "color": color, "priority": PRIORITY, "origin": ORIGIN})
        if effect == VIDEO:
            steps.append({"command": "clear", "priority": PRIORITY})
        elif effect is not None:
            steps.append({"command": "effect", "effect": {"name": effect}, "priority": PRIORITY, "origin": ORIGIN})
        if brightness is not None:
            steps.append({"command": "adjustment", "adjustment": {"scaleOutput": brightness}})
        if steps:
            messages += [{"command": "instance", "subcommand": "switchTo", "instance": instance}, *steps]
        await self._hyperhdr_session(messages)
        return {"instance": instance}

    async def _hyperhdr_session(self, messages):
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(*self.socket_address), TIMEOUT)
        except (OSError, TimeoutError) as exc:
            raise BridgeError("service_unavailable", "HyperHDR's JSON server did not answer") from exc
        try:
            for message in messages:
                tan = next(self.tans)
                writer.write(json.dumps({**message, "tan": tan}).encode() + b"\n")
                await writer.drain()
                while True:
                    line = await asyncio.wait_for(reader.readline(), TIMEOUT)
                    if not line:
                        raise BridgeError("service_unavailable", "HyperHDR closed the connection")
                    reply = json.loads(line)
                    if reply.get("tan") == tan:
                        break
                if not reply.get("success"):
                    raise BridgeError("system_error", "HyperHDR refused the change")
        except (OSError, TimeoutError, ValueError) as exc:
            raise BridgeError("service_unavailable", "HyperHDR did not answer") from exc
        finally:
            writer.close()
