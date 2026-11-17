"""Generate fixtures/*.json for the Bridge status mock.

The `snapshot` half of every fixture comes from the real `Bridge.snapshot()` and the
real hooks' `accept()`, fed with fake KWin windows and fake app states, so the shape
cannot drift from the bridge. Everything the bridge does not record today goes under
`diagnostics`, which is invented for this mock (see ../README.md).

Run from the repository root:
    .venv/bin/python bigscreen-status-mock/tools/make_fixtures.py
"""
import asyncio
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import uuid
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bigscreen-bridge" / "src"))

from bigscreen_bridge.adapters import APPS  # noqa: E402
from bigscreen_bridge.couchside import CouchsideHook, KINDS as COUCHSIDE_KINDS  # noqa: E402
from bigscreen_bridge.flimmer import FlimmerHook  # noqa: E402
from bigscreen_bridge.mpris import media_state  # noqa: E402
from bigscreen_bridge.server import Bridge  # noqa: E402
from bigscreen_bridge.stremio import StremioHook  # noqa: E402
from bigscreen_bridge.vacuumtube import VacuumTubeHook  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "fixtures"
NOW = datetime(2026, 11, 17, 16, 42, 0, tzinfo=timezone.utc)
FLATPAK = {app.id: app.flatpak for app in APPS}
VIDEO = "M7lc1UVf-VE"
CHANNEL = "channel-" + hashlib.sha256(b"tvheadend:8f3c1d2e-news").hexdigest()


def ts(seconds_ago):
    return (NOW - timedelta(seconds=seconds_ago)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def unix(seconds_from_now):
    return round(NOW.timestamp() + seconds_from_now)


# ---------------------------------------------------------------- app states --

def couchside_state():
    return {"protocol": 1, "ready": True, "content_types": list(COUCHSIDE_KINDS), "browse": True,
            "music_assistant_ready": True, "playback_owner": "music_assistant",
            "ma_player_id": "sendspin-living-room", "view": {"type": "now_playing", "id": ""}}


def stremio_state(state="playing", position=1834):
    return {"protocol": 1, "ready": True, "browse": True, "content_types": ["movie", "series", "episode"],
            "capabilities": ["play", "pause", "stop", "seek"],
            "media": {"state": state, "title": "Game of Thrones", "type": "series",
                      "episode": "S01E02 — The Kingsroad", "position": position, "duration": 3360,
                      "poster": "https://images.metahub.space/poster/medium/tt0944947/img",
                      "updated_at": ts(0.6), "content_id": "tt0944947:1:2"}}


def vacuumtube_state(state="paused"):
    caps = ["play" if state == "paused" else "pause", "stop", "seek", "volume_set", "volume_mute"]
    return {"protocol": 1, "ready": True, "content_types": ["video", "playlist", "search"], "capabilities": caps,
            "view": {"kind": "watch", "video_id": VIDEO},
            "media": {"video_id": VIDEO, "title": "YouTube Developers Live: Embedded Web Player Customization",
                      "state": state, "position": 312.4, "duration": 1287, "updated_at": ts(0.8),
                      "thumbnail": f"https://i.ytimg.com/vi/{VIDEO}/hqdefault.jpg",
                      "volume": 0.8, "muted": False, "output_volume": 0.8}}


def flimmer_state(loading=False, state="playing"):
    media = {"state": "idle" if loading else state, "updated_at": ts(0.4)}
    if not loading:
        media["channel"] = {"id": CHANNEL, "name": "Das Erste HD", "number": 1,
                            "current": {"title": "Tagesschau", "start": unix(-720), "stop": unix(180)},
                            "next": {"title": "Wetter vor acht", "start": unix(180), "stop": unix(480)}}
    caps = [] if loading else ["stop", "pause" if state == "playing" else "play"]
    return {"protocol": 1, "ready": True, "content_types": ["channel"], "catalogue_loading": loading,
            "capabilities": caps, "media": media}


# ------------------------------------------------------------------- MPRIS --

def sendspin(state="playing"):
    props = {"PlaybackStatus": state.capitalize(), "Position": 96_000_000,
             "Metadata": {"xesam:title": "Clair de Lune", "mpris:trackid": "/sendspin/track/42",
                          "mpris:length": 302_000_000}}
    return {"id": "mpris::1.41", "bus_name": "org.mpris.MediaPlayer2.Sendspin", "owner": ":1.41", "pid": 2210,
            "app": "couchside", "name": "Sendspin", "desktop": "org.sendspin.cli",
            "state": state if state != "stopped" else "idle", "available": True, "role": "music_assistant",
            "capabilities": [], "media": media_state(props)}


def chromium(state="paused"):
    props = {"PlaybackStatus": state.capitalize(), "Position": 312_400_000,
             "Metadata": {"xesam:title": "YouTube Developers Live: Embedded Web Player Customization",
                          "mpris:trackid": "/org/chromium/MediaPlayer2/TrackList/Track1",
                          "mpris:length": 1_287_000_000}}
    caps = ["pause", "stop"] if state == "playing" else ["play", "stop"]
    return {"id": "mpris::1.77", "bus_name": "org.mpris.MediaPlayer2.chromium.instance5120", "owner": ":1.77",
            "pid": 5120, "app": "vacuumtube", "name": "Chromium", "desktop": "", "state": state,
            "available": True, "role": "other", "capabilities": caps, "media": media_state(props)}


# ------------------------------------------------------------- snapshot -----

def build_snapshot(world):
    """Run the bridge's own snapshot code over a fake world."""
    async def run():
        wm = SimpleNamespace(windows=world["windows"], ready=asyncio.Event())
        if world.get("kwin_ready", True):
            wm.ready.set()
        bridge = Bridge({"device_id": "3f2c9a1e-7b4d-4c55-9a0e-5d2f6b8c1a90", "name": "Living Room TV"}, wm)
        bridge.installed = {FLATPAK[app] for app in world.get("installed", FLATPAK)}
        for app, cls in (("couchside", CouchsideHook), ("stremio", StremioHook),
                         ("flimmer", FlimmerHook), ("vacuumtube", VacuumTubeHook)):
            hook = cls(None, True) if cls is not VacuumTubeHook else cls(True)
            state = world["hooks"].get(app)
            if state is not None:
                hook.state = hook.accept(copy.deepcopy(state))
                hook.content_types = [k for k in hook.kinds if k in hook.state["content_types"]]
            setattr(bridge, app, hook)
        bridge.power = SimpleNamespace(state=world["power"])
        bridge.audio = SimpleNamespace(state=world["audio"])
        # SessionController.refresh: native hooks first, then MPRIS players they do not own.
        players, native = {}, set()
        for app in ("stremio", "flimmer", "vacuumtube"):
            state = getattr(bridge, app).state
            if state and state.get("ready"):
                native.add(app)
                media = state.get("media", {})
                players["app:" + app] = {"id": "app:" + app, "app": app, "state": media.get("state", "unknown"),
                                         "media": media, "capabilities": state.get("capabilities", []),
                                         "available": True, "role": "video"}
        for player in world["mpris"]:
            if player.get("app") not in native:
                players[player["id"]] = player
        bridge.sessions = SimpleNamespace(players=players, receipts={k: {} for k in world.get("paused_by_bridge", [])})
        return bridge.snapshot(), native
    return asyncio.run(run())


def power(display_on=True):
    mode = "on" if display_on else "off"
    return {"display": {"supported": True, "outputs": {"HDMI-A-1": mode}, "display_on": display_on},
            "authorization": {"suspend": "yes", "shutdown": "yes", "restart": "yes"},
            "capabilities": ["screen_off", "screen_on", "lock_screen"], "locked": False, "system_state": "awake",
            "network": [{"interface": "enp3s0", "mac": "02:00:5e:10:00:01", "hardware_wakeup": "enabled",
                         "wol_verified_suspend": False, "wol_verified_poweroff": False}],
            "blocked": {"suspend": [], "shutdown": [], "restart": []}}


def audio(volume=0.42, muted=False, streams=("couchside", "stremio")):
    rows = {"couchside": {"index": 61, "pid": 2210, "app": "couchside", "serial": "118", "role": "sendspin",
                          "stream_active": True, "producing_audio": None, "muted": False, "volume": 1.0},
            "stremio": {"index": 74, "pid": 4388, "app": "stremio", "serial": "131", "role": None,
                        "stream_active": True, "producing_audio": None, "muted": False, "volume": 1.0},
            "vacuumtube": {"index": 80, "pid": 5120, "app": "vacuumtube", "serial": "140", "role": None,
                           "stream_active": True, "producing_audio": None, "muted": False, "volume": 0.8}}
    return {"available": True, "volume": volume, "raw_volume": volume, "muted": muted,
            "capabilities": ["mute", "unmute", "set_volume"], "streams": [rows[s] for s in streams]}


def window(app, active=False):
    return {"id": "{" + hashlib.md5(app.encode()).hexdigest()[:8] + "-kwin}", "desktop": FLATPAK[app],
            "resource": FLATPAK[app], "class": FLATPAK[app], "active": active}


# ----------------------------------------------------------- diagnostics ----

ENDPOINTS = {"couchside": ("dbus", "io.github.shreyasajj.Couchside.Control", "StateChanged"),
             "stremio": ("dbus", "io.github.shreyasajj.StremioTV.Control", "StateChanged"),
             "flimmer": ("dbus", "io.github.jclsn.Flimmer.Control", "StateChanged"),
             "vacuumtube": ("socket", "$XDG_RUNTIME_DIR/bigscreen-bridge/vacuumtube.sock", "mpris")}


def hook_diag(app, *, reachable=True, ready=True, pushed=True, signals=40, signal_ago=3.2, read_ago=3.1,
              failures=0, error=None, **extra):
    transport, endpoint, source = ENDPOINTS[app]
    entry = {"transport": transport, "endpoint": endpoint, "enabled": True, "reachable": reachable,
             "ready": ready, "mode": "pushed" if pushed else "polled",
             "poll_reason": None if pushed else ("never_signalled" if not signals else "not_ready"),
             "signal_source": source, "signals_received": signals,
             "last_signal_at": ts(signal_ago) if signals else None,
             "last_read_at": ts(read_ago), "last_read_ok": reachable,
             "consecutive_failures": failures, "last_error": error}
    entry.update(extra)
    return entry


def error(code, message, ago):
    return {"code": code, "message": message, "at": ts(ago)}


def cmd(rid, command, app, ago, ms, ok=True, code=None, transport="websocket"):
    return {"id": rid, "command": command, "app": app, "transport": transport,
            "started_at": ts(ago + ms / 1000), "finished_at": None if ok is None else ts(ago),
            "ok": ok, "error_code": code, "duration_ms": ms}


def warn(ago, message, level="WARNING", logger="bigscreen_bridge.server", count=1):
    return {"at": ts(ago), "level": level, "logger": logger, "message": message, "count": count}


def mpris_diag(players, native):
    return [{"bus_name": p["bus_name"], "identity": p["name"], "desktop": p["desktop"] or None, "app": p["app"],
             "state": p["state"], "role": p["role"],
             "superseded_by": ("app:" + p["app"]) if p["app"] in native else None} for p in players]


def diagnostics(native, mpris, *, hooks, clients=1, mqtt=None, commands=(), warnings=(), kwin=None,
                ws_last_disconnect=None):
    return {
        "schema": 1,
        "generated_at": ts(0),
        "service": {"version": "0.1.0", "started_at": ts(36_540), "health": "ok", "port": 8765},
        "websocket": {"clients": clients, "last_connected_at": ts(36_500 if clients else 14_000),
                      "last_disconnected_at": ws_last_disconnect and ts(ws_last_disconnect),
                      "last_disconnect_reason": "heartbeat_timeout" if ws_last_disconnect else None},
        "discovery": {"enabled": True, "announced": True, "service_type": "_bigscreen-bridge._tcp",
                      "instance": "Living Room TV (3f2c9a1e)", "error": None},
        "mqtt": mqtt or {"configured": True, "connected": True, "broker": "mqtt.lan:1883", "tls": False,
                         "commands_enabled": False, "last_connected_at": ts(36_520), "last_error": None,
                         "retry_seconds": 5, "failures": 0},
        "kwin": kwin or {"ready": True, "windows": 3, "last_report_at": ts(42)},
        "hooks": hooks,
        "mpris": mpris_diag(mpris, native),
        "commands": list(commands),
        "warnings": list(warnings),
    }


HEALTHY_COMMANDS = [
    cmd("a41f0c2e-9a7d-4d4e-8e8f-1c0b8a5f3e11", "pause", "vacuumtube", 412, 380),
    cmd("7c2e9b10-3f5a-4b8c-a1d2-e3f4a5b6c7d8", "open", "stremio", 388, 2140),
    cmd("e5d4c3b2-a190-4f8e-9d7c-6b5a4f3e2d1c", "set_volume", None, 120, 95),
    cmd("1b2c3d4e-5f60-4718-8293-a4b5c6d7e8f9", "get_state", None, 31, 4),
]


def scenario_healthy():
    world = {"windows": [window("couchside"), window("stremio", True), window("vacuumtube")],
             "hooks": {"couchside": couchside_state(), "stremio": stremio_state(), "vacuumtube": vacuumtube_state()},
             "mpris": [sendspin(), chromium()], "power": power(), "audio": audio()}
    hooks = {"couchside": hook_diag("couchside", signals=12, signal_ago=388, read_ago=6.0, music_assistant_ready=True),
             "stremio": hook_diag("stremio", signals=58, signal_ago=11, read_ago=1.0),
             "flimmer": hook_diag("flimmer", reachable=False, ready=False, signals=0, read_ago=6.0,
                                  error=error("not_ready", "Flimmer control endpoint is unavailable", 6.0),
                                  pushed=False, catalogue_loading=False),
             "vacuumtube": hook_diag("vacuumtube", signals=133, signal_ago=7.5, read_ago=6.3)}
    hooks["flimmer"]["poll_reason"] = "not_running"
    return world, dict(hooks=hooks, commands=HEALTHY_COMMANDS)


def scenarios():
    yield "healthy", "All healthy", "Every hook answers and announces its own changes.", *scenario_healthy()

    world, extra = scenario_healthy()
    extra["hooks"]["stremio"] = hook_diag("stremio", pushed=False, signals=0, read_ago=1.3)
    extra["warnings"] = [warn(36_400, "Stremio has not emitted StateChanged; polling every 2 s",
                              level="INFO", logger="bigscreen_bridge.hooks")]
    yield ("polled", "One app polled", "Stremio is an older build that never signals, so it is read every 2 s.",
           world, extra)

    world, extra = scenario_healthy()
    world["windows"] = [window("couchside"), window("stremio"), window("vacuumtube"), window("flimmer", True)]
    world["hooks"]["flimmer"] = flimmer_state(loading=True)
    world["hooks"]["stremio"] = stremio_state("paused")
    extra["hooks"]["flimmer"] = hook_diag("flimmer", signals=2, signal_ago=7.1, read_ago=0.4,
                                          catalogue_loading=True, catalogue_loading_since=ts(7.4), channels=0)
    extra["commands"] = HEALTHY_COMMANDS[:3] + [
        cmd("0f9e8d7c-6b5a-4493-8271-605f4e3d2c1b", "pause", "stremio", 9.6, 410),
        cmd("c0ffee00-1234-4cde-8f00-aa11bb22cc33", "focus", "flimmer", 7.6, 1830),
        cmd("5ca1ab1e-0000-4abc-9def-0123456789ab", "open", "flimmer", 0, 7400, ok=None)]
    extra["kwin"] = {"ready": True, "windows": 4, "last_report_at": ts(7.6)}
    yield ("flimmer_loading", "Flimmer catalogue loading",
           "Flimmer is ready but its channel list is still loading; the open request is retrying.", world, extra)

    world, extra = scenario_healthy()
    world["windows"] = [window("couchside"), window("stremio"), window("vacuumtube", True)]
    world["hooks"]["stremio"] = stremio_state("paused")
    del world["hooks"]["vacuumtube"]
    world["mpris"] = [sendspin(), chromium("playing")]
    extra["hooks"]["vacuumtube"] = hook_diag(
        "vacuumtube", reachable=False, ready=False, pushed=False, signals=133, signal_ago=2.4, read_ago=0.9,
        failures=37, error=error("not_ready", "VacuumTube control endpoint is unavailable", 0.9))
    extra["commands"] = HEALTHY_COMMANDS[:2] + [
        cmd("9d8c7b6a-5f4e-4d3c-b2a1-0f9e8d7c6b5a", "open", "vacuumtube", 52, 20_000, ok=False,
            code="readiness_timeout"),
        cmd("8e7d6c5b-4a39-4281-9706-f5e4d3c2b1a0", "pause", "vacuumtube", 14, 6, ok=False, code="not_ready")]
    extra["warnings"] = [warn(74, "VacuumTube control socket unavailable (not_ready)", logger="bigscreen_bridge.hooks",
                              count=37)]
    yield ("vacuumtube_down", "VacuumTube socket unreachable",
           "The YouTube window is open and its Chromium player is playing, but the private socket does not answer.",
           world, extra)

    world, extra = scenario_healthy()
    world["hooks"]["stremio"] = stremio_state("paused")
    world["hooks"]["vacuumtube"] = vacuumtube_state("paused")
    world["mpris"] = [sendspin("paused"), chromium()]
    world["power"] = power(display_on=False)
    world["audio"] = audio(streams=("couchside",))
    world["paused_by_bridge"] = ["app:stremio"]
    extra["commands"] = HEALTHY_COMMANDS[:2] + [
        cmd("2468ace0-1357-49bd-8f00-13579bdf2468", "pause_all", None, 64, 620),
        cmd("13579bdf-2468-4ace-9f11-2468ace01357", "screen_off", None, 62, 1140)]
    yield ("display_off", "Display off", "Home Assistant paused everything and turned the screen off (DPMS).",
           world, extra)

    world, extra = scenario_healthy()
    extra["mqtt"] = {"configured": True, "connected": False, "broker": "mqtt.lan:1883", "tls": False,
                     "commands_enabled": False, "last_connected_at": ts(252), "last_error": "MqttError",
                     "retry_seconds": 5, "failures": 50}
    extra["warnings"] = [warn(2, "MQTT link lost: MqttError", logger="bigscreen_bridge.mqtt", count=50)]
    yield ("mqtt_down", "MQTT broker down", "The broker went away four minutes ago; the link retries every 5 s.",
           world, extra)

    world, extra = scenario_healthy()
    extra["clients"] = 0
    extra["ws_last_disconnect"] = 192
    extra["commands"] = HEALTHY_COMMANDS[:3]
    extra["warnings"] = [warn(192, "Home Assistant WebSocket client closed (heartbeat_timeout)",
                              level="INFO")]
    yield ("ha_disconnected", "Home Assistant disconnected",
           "No WebSocket client has been connected for three minutes.", world, extra)

    world, extra = scenario_healthy()
    world["hooks"]["stremio"] = stremio_state("paused")
    burst = [("open", "stremio", 25_000, "command_timeout"), ("seek", "stremio", 3, "unsupported"),
             ("volume_set", "stremio", 1, "unsupported"), ("open", "vacuumtube", 2, "invalid_request"),
             ("focus", "flimmer", 25_000, "readiness_timeout"), ("open_uri", None, 1, "invalid_request"),
             ("open", "couchside", 25_000, "not_ready"), ("play", "stremio", 4, "internal_error"),
             ("pause", "vacuumtube", 1, "invalid_request")]
    extra["commands"] = [cmd(str(uuid.uuid5(uuid.NAMESPACE_URL, f"burst-{i}")), c, a,
                             (len(burst) - i) * 1.4, ms, ok=False, code=code)
                         for i, (c, a, ms, code) in enumerate(burst)]
    extra["hooks"]["stremio"]["last_error"] = error("command_timeout", "Stremio did not acknowledge the command", 12.6)
    extra["hooks"]["couchside"]["last_error"] = error("not_ready", "Couchside UI control did not become ready", 4.2)
    extra["warnings"] = [warn(2.8, "Request failed", level="ERROR"),
                         warn(4.0, "Nine failed commands from one client in 15 s", count=9)]
    yield ("command_burst", "Burst of failed commands",
           "An automation fired a run of invalid or unanswerable requests.", world, extra)


def main():
    OUT.mkdir(exist_ok=True)
    index = []
    for key, title, summary, world, extra in scenarios():
        snapshot, native = build_snapshot(world)
        # Top level is exactly what Bridge.snapshot() returns; everything invented is under diagnostics.
        fixture = {**snapshot, "diagnostics": diagnostics(native, world["mpris"], **extra)}
        (OUT / f"{key}.json").write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n")
        index.append({"id": key, "title": title, "summary": summary, "file": f"{key}.json"})
    (OUT / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    print(f"wrote {len(index)} fixtures to {OUT}")


if __name__ == "__main__":
    main()
