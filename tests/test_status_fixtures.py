"""The status screen's fixtures must have exactly the shape the real bridge produces."""
import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from bigscreen_bridge.adapters import BridgeError
from bigscreen_bridge.couchside import CouchsideHook
from bigscreen_bridge.diagnostics import Recorder
from bigscreen_bridge.flimmer import FlimmerHook
from bigscreen_bridge.server import Bridge
from bigscreen_bridge.stremio import StremioHook
from bigscreen_bridge.vacuumtube import VacuumTubeHook

FIXTURES = Path(__file__).resolve().parents[1] / "bigscreen-status" / "fixtures"
INDEX = json.loads((FIXTURES / "index.json").read_text())


def reference():
    """A bridge with every module present, and diagnostics with one of each list entry."""
    wm = SimpleNamespace(windows=[], ready=asyncio.Event())
    recorder = Recorder()
    bridge = Bridge({"device_id": "d", "name": "TV"}, wm, diagnostics=recorder)
    bridge.couchside, bridge.stremio, bridge.flimmer = (cls(None, True) for cls in
                                                        (CouchsideHook, StremioHook, FlimmerHook))
    bridge.vacuumtube = VacuumTubeHook(True)
    bridge.power, bridge.audio = SimpleNamespace(state={}), SimpleNamespace(state={})
    player = {"bus_name": "org.mpris.MediaPlayer2.x", "name": "X", "desktop": "", "app": None,
              "state": "idle", "role": "other"}
    bridge.sessions = SimpleNamespace(players={}, receipts={},
                                      mpris=SimpleNamespace(players={"mpris::1.1": player}, followers={}))
    for hook in (bridge.couchside, bridge.stremio, bridge.flimmer, bridge.vacuumtube):
        recorder.hook_error(hook, BridgeError("not_ready", "unavailable"))
    recorder.command_finished(recorder.command_started("r", {"command": "get_state"}, "websocket"), {"ok": True})
    recorder.handler.handle(logging.LogRecord("bigscreen_bridge.x", logging.INFO, "", 0, "line", (), None))
    return bridge.snapshot(), recorder.to_dict(bridge)


def keys(value, path="diagnostics"):
    """Every dict key path in `value`, with list entries collapsed to `[]`."""
    if isinstance(value, dict):
        found = set()
        for key, item in value.items():
            found |= {path + "." + key} | keys(item, path + "." + key)
        return found
    if isinstance(value, list):
        return set().union(*(keys(item, path + "[]") for item in value)) if value else set()
    return set()


SNAPSHOT, DIAGNOSTICS = reference()


def test_index_lists_every_fixture():
    assert sorted(entry["file"] for entry in INDEX) == sorted(p.name for p in FIXTURES.glob("*.json")
                                                              if p.name != "index.json")


@pytest.mark.parametrize("entry", INDEX, ids=[entry["id"] for entry in INDEX])
def test_fixture_matches_the_real_bridge(entry):
    fixture = json.loads((FIXTURES / entry["file"]).read_text())
    diagnostics = fixture.pop("diagnostics")
    assert set(fixture) == set(SNAPSHOT)
    # Within diagnostics, nothing the real module does not produce, and every section it does.
    assert keys(diagnostics) <= keys(DIAGNOSTICS)
    assert set(diagnostics) == set(DIAGNOSTICS)
    for section in ("service", "websocket", "discovery", "mqtt", "kwin"):
        assert set(diagnostics[section]) == set(DIAGNOSTICS[section])
    assert set(diagnostics["hooks"]) == set(DIAGNOSTICS["hooks"])
    for app, hook in diagnostics["hooks"].items():
        assert set(hook) == set(DIAGNOSTICS["hooks"][app]), app
    for section in ("mpris", "commands", "warnings"):
        for row in diagnostics[section]:
            assert set(row) == set(DIAGNOSTICS[section][0]), section
