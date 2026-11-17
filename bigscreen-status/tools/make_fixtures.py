"""Generate fixtures/*.json for the Bridge status screen's fixture mode.

Nothing here is invented. The top level of every fixture is the real
`Bridge.snapshot()` over fake KWin windows, app states (through the real hooks'
`accept()`) and MPRIS players. `diagnostics` is the real `Recorder.to_dict()` after
replaying a timed history of events (reads, signals, requests, log lines) into the
real recording hooks on a fake clock. Change the bridge, run this again.

Run from the repository root:
    .venv/bin/python bigscreen-status/tools/make_fixtures.py
"""
import asyncio
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import logging
import os
import uuid
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
os.environ["XDG_RUNTIME_DIR"] = "/run/user/1000"   # the TV's, so endpoints print as on the TV
sys.path.insert(0, str(ROOT / "bigscreen-bridge" / "src"))

from bigscreen_bridge.adapters import APPS  # noqa: E402
from bigscreen_bridge.adapters import BridgeError  # noqa: E402
from bigscreen_bridge.couchside import CouchsideHook, KINDS as COUCHSIDE_KINDS  # noqa: E402
from bigscreen_bridge.diagnostics import Recorder  # noqa: E402
from bigscreen_bridge.discovery import SERVICE_TYPE, service_record  # noqa: E402
from bigscreen_bridge.flimmer import FlimmerHook  # noqa: E402
from bigscreen_bridge.mpris import media_state  # noqa: E402
from bigscreen_bridge.mqtt import settings as mqtt_settings  # noqa: E402
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


# ------------------------------------------------------------- the replay ----

CONFIG = {"device_id": "3f2c9a1e-7b4d-4c55-9a0e-5d2f6b8c1a90", "name": "Living Room TV", "host": "0.0.0.0",
          "port": 8765, "token": "fixture-token-never-shown-" + "x" * 24,
          "mqtt": {"host": "mqtt.lan", "username": "fixture-user", "password": "fixture-password"}}
UPTIME = 36_540          # the bridge started ten hours before NOW
NOT_RUNNING = BridgeError("not_ready", "{label} control endpoint is unavailable")


class Clock:
    """Wall and monotonic time, both set as "seconds before NOW"."""
    def __init__(self):
        self.ago = UPTIME

    def __call__(self):
        return NOW - timedelta(seconds=self.ago)

    def monotonic(self):
        return 100_000 - self.ago


class World:
    """A fake bridge built from the real classes, plus a list of timed events to replay."""

    def __init__(self, windows, hooks, mpris, power, audio, paused_by_bridge=(), clients=1, installed=FLATPAK):
        self.clock = Clock()
        self.recorder = Recorder(clock=self.clock, monotonic=self.clock.monotonic)
        self.recorder.service["version"] = "0.1.0"   # the package version, pinned so fixtures stay stable
        wm = SimpleNamespace(windows=windows, ready=asyncio.Event())
        self.bridge = bridge = Bridge(CONFIG, wm, diagnostics=self.recorder)
        bridge.installed = {FLATPAK[app] for app in installed}
        bridge.clients = {object() for _ in range(clients)}
        for app, cls in (("couchside", CouchsideHook), ("stremio", StremioHook),
                         ("flimmer", FlimmerHook), ("vacuumtube", VacuumTubeHook)):
            hook = cls(None, True) if cls is not VacuumTubeHook else cls(True, "/run/user/1000/bigscreen-bridge/"
                                                                         "vacuumtube.sock")
            hook.recorder = self.recorder
            state = hooks.get(app)
            if state is not None:
                hook.state = hook.accept(copy.deepcopy(state))
                hook.content_types = [k for k in hook.kinds if k in hook.state["content_types"]]
            setattr(bridge, app, hook)
        bridge.power = SimpleNamespace(state=power)
        bridge.audio = SimpleNamespace(state=audio)
        # SessionController.refresh: native hooks first, then the MPRIS players they do not own.
        players, native = {}, set()
        for app in ("stremio", "flimmer", "vacuumtube"):
            state = getattr(bridge, app).state
            if state and state.get("ready"):
                native.add(app)
                media = state.get("media", {})
                players["app:" + app] = {"id": "app:" + app, "app": app, "state": media.get("state", "unknown"),
                                         "media": media, "capabilities": state.get("capabilities", []),
                                         "available": True, "role": "video"}
        for player in mpris:
            if player.get("app") not in native:
                players[player["id"]] = player
        registry = SimpleNamespace(players={p["id"]: p for p in mpris}, followers={"vacuumtube": bridge.vacuumtube})
        bridge.sessions = SimpleNamespace(players=players, mpris=registry,
                                          receipts={k: {} for k in paused_by_bridge})
        self.events = []
        self.baseline()

    def at(self, ago, fn, *args, **kwargs):
        self.events.append((ago, len(self.events), fn, args, kwargs))

    def hook(self, app):
        return getattr(self.bridge, app)

    # -- history every scenario shares ------------------------------------------

    def baseline(self):
        r = self.recorder
        self.at(UPTIME, r.started, 8765)
        self.at(UPTIME, r.discovery_result, service_record(CONFIG, "mediac"), SERVICE_TYPE)
        self.at(UPTIME, r.mqtt_configured, mqtt_settings(CONFIG), 5)
        self.at(UPTIME - 20, r.mqtt_connected)
        if self.bridge.clients:
            self.at(UPTIME - 40, r.ws_connected)
        self.at(42, r.kwin_report)

    def signals(self, app, count, last_ago, first_ago=UPTIME - 60):
        hook = self.hook(app)
        hook.pushed = count > 0
        step = (first_ago - last_ago) / max(1, count - 1)
        for n in range(count):
            self.at(first_ago - n * step if count > 1 else last_ago, self.recorder.hook_signal, hook)

    def reads(self, app, last_ago, every=10, since=UPTIME - 30, error=None, failures=0):
        """Successful reads every `every` s since `since`, then `failures` failed ones up to `last_ago`."""
        hook = self.hook(app)
        good_until = last_ago + failures * 2
        t = since
        while t > good_until:
            self.at(t, self.recorder.hook_read, hook)
            t -= every
        if error is None:
            self.at(last_ago, self.recorder.hook_read, hook)
            return
        exc = BridgeError(error.code, str(error).format(label=hook.label))
        for n in range(max(1, failures)):
            self.at(last_ago + (max(1, failures) - 1 - n) * 2, self.recorder.hook_read, hook, exc)

    def request(self, ago, ms, command, app=None, ok=True, code=None, transport="websocket", rid=None):
        rid = rid or str(uuid.uuid5(uuid.NAMESPACE_URL, f"{command}-{app}-{ago}"))
        data = {"command": command, **({"app": app} if app else {})}
        holder = {}
        self.at(ago + ms / 1000, lambda: holder.setdefault("entry", self.recorder.command_started(rid, data, transport)))
        if ok is not None:
            response = {"ok": ok, **({} if ok else {"error": {"code": code}})}
            self.at(ago, lambda: self.recorder.command_finished(holder["entry"], response, "client-1"))

    def log(self, ago, logger, level, message, *args):
        """A line another bridge module writes, exactly as it formats it."""
        def emit():
            record = logging.LogRecord(logger, level, __file__, 0, message, args, None)
            record.created = self.clock().timestamp()
            self.recorder.handler.handle(record)
        self.at(ago, emit)

    def run(self):
        self.recorder.install()   # the recorder's own lines (bursts, silence, outages) land in the buffer
        logging.getLogger("bigscreen_bridge").propagate = False
        try:
            for ago, _, fn, args, kwargs in sorted(self.events, key=lambda e: (-e[0], e[1])):
                self.clock.ago = ago
                fn(*args, **kwargs)
            self.clock.ago = 0
            self.bridge.wm.ready.set()
            return self.bridge.snapshot(), self.recorder.to_dict(self.bridge)
        finally:
            self.recorder.uninstall()
            logging.getLogger("bigscreen_bridge").propagate = True


# ------------------------------------------------------------------ scenarios --

HEALTHY_COMMANDS = [("pause", "vacuumtube", 412, 380), ("open", "stremio", 388, 2140),
                    ("set_volume", None, 120, 95), ("get_state", None, 31, 4)]


def healthy(**changes):
    world = dict(windows=[window("couchside"), window("stremio", True), window("vacuumtube")],
                 hooks={"couchside": couchside_state(), "stremio": stremio_state(), "vacuumtube": vacuumtube_state()},
                 mpris=[sendspin(), chromium()], power=power(), audio=audio())
    world.update(changes)
    return world


def healthy_history(w, commands=HEALTHY_COMMANDS, stremio_signals=True, vacuumtube_up=True):
    w.signals("couchside", 12, 388)
    w.reads("couchside", 6.0)
    if stremio_signals:
        w.signals("stremio", 58, 11)
    w.reads("stremio", 1.0, every=2 if not stremio_signals else 10)
    # Not running all day: every 2 s poll since start-up failed, exactly as the bridge counts it.
    w.reads("flimmer", 6.0, since=0, error=NOT_RUNNING, failures=(UPTIME - 30) // 2)
    if vacuumtube_up:
        w.signals("vacuumtube", 133, 7.5)
        w.reads("vacuumtube", 6.3)
    for command, app, ago, ms in commands:
        w.request(ago, ms, command, app)


def scenarios():
    w = World(**healthy())
    healthy_history(w)
    yield "healthy", "All healthy", "Every hook answers and announces its own changes.", w

    w = World(**healthy())
    healthy_history(w, stremio_signals=False)
    yield ("polled", "One app polled", "Stremio is an older build that never signals, so it is read every 2 s.", w)

    w = World(**healthy(windows=[window("couchside"), window("stremio"), window("vacuumtube"), window("flimmer", True)],
                        hooks={"couchside": couchside_state(), "stremio": stremio_state("paused"),
                               "vacuumtube": vacuumtube_state(), "flimmer": flimmer_state(loading=True)}))
    healthy_history(w, commands=HEALTHY_COMMANDS[:3])
    w.signals("flimmer", 2, 7.1, first_ago=7.4)
    w.reads("flimmer", 0.4, every=0.5, since=7.4)
    w.request(9.6, 410, "pause", "stremio")
    w.request(7.6, 1830, "focus", "flimmer")
    w.request(0, 7400, "open", "flimmer", ok=None)
    w.at(7.6, w.recorder.kwin_report)
    yield ("flimmer_loading", "Flimmer catalogue loading",
           "Flimmer is ready but its channel list is still loading; the open request is retrying.", w)

    w = World(**healthy(windows=[window("couchside"), window("stremio"), window("vacuumtube", True)],
                        hooks={"couchside": couchside_state(), "stremio": stremio_state("paused")},
                        mpris=[sendspin(), chromium("playing")]))
    healthy_history(w, commands=HEALTHY_COMMANDS[:2], vacuumtube_up=False)
    w.signals("vacuumtube", 133, 2.4)   # Chromium's MPRIS player keeps announcing changes
    w.reads("vacuumtube", 0.9, error=BridgeError("not_ready", "{label} control endpoint is unavailable"),
            failures=37)
    w.request(52, 20_000, "open", "vacuumtube", ok=False, code="readiness_timeout")
    w.request(14, 6, "pause", "vacuumtube", ok=False, code="not_ready")
    yield ("vacuumtube_down", "VacuumTube socket unreachable",
           "The YouTube window is open and its Chromium player is playing, but the private socket does not answer.",
           w)

    w = World(**healthy(hooks={"couchside": couchside_state(), "stremio": stremio_state("paused"),
                               "vacuumtube": vacuumtube_state("paused")},
                        mpris=[sendspin("paused"), chromium()], power=power(display_on=False),
                        audio=audio(streams=("couchside",)), paused_by_bridge=["app:stremio"]))
    healthy_history(w, commands=HEALTHY_COMMANDS[:2] + [("pause_all", None, 64, 620), ("screen_off", None, 62, 1140)])
    yield ("display_off", "Display off", "Home Assistant paused everything and turned the screen off (DPMS).", w)

    w = World(**healthy())
    healthy_history(w)
    w.at(252, w.recorder.mqtt_connected)
    for n in range(50):
        ago = 250 - n * 5
        w.at(ago, w.recorder.mqtt_disconnected, type("MqttError", (Exception,), {})())
        w.log(ago, "bigscreen_bridge.mqtt", logging.WARNING, "MQTT link lost: %s", "MqttError")
    yield ("mqtt_down", "MQTT broker down", "The broker went away four minutes ago; the link retries every 5 s.", w)

    w = World(**healthy(), clients=0)
    w.at(14_000, w.recorder.ws_connected)
    w.at(192, w.recorder.ws_disconnected, "heartbeat_timeout")
    w.log(192, "bigscreen_bridge.server", logging.INFO, "WebSocket client closed (%s)", "heartbeat_timeout")
    healthy_history(w, commands=HEALTHY_COMMANDS[:3])
    yield ("ha_disconnected", "Home Assistant disconnected",
           "No WebSocket client has been connected for three minutes.", w)

    w = World(**healthy(hooks={"couchside": couchside_state(), "stremio": stremio_state("paused"),
                               "vacuumtube": vacuumtube_state()}))
    healthy_history(w, commands=[])
    burst = [("open", "stremio", 25_000, "command_timeout"), ("seek", "stremio", 3, "unsupported"),
             ("volume_set", "stremio", 1, "unsupported"), ("open", "vacuumtube", 2, "invalid_request"),
             ("focus", "flimmer", 25_000, "readiness_timeout"), ("open_uri", None, 1, "invalid_request"),
             ("open", "couchside", 25_000, "not_ready"), ("play", "stremio", 4, "internal_error"),
             ("pause", "vacuumtube", 1, "invalid_request")]
    for i, (command, app, ms, code) in enumerate(burst):
        ago = round((len(burst) - i) * 1.4, 1)
        w.request(ago, ms, command, app, ok=False, code=code, rid=str(uuid.uuid5(uuid.NAMESPACE_URL, f"burst-{i}")))
    w.at(12.6, w.recorder.hook_error, w.hook("stremio"),
         BridgeError("command_timeout", "Stremio did not acknowledge the command"))
    w.at(4.2, w.recorder.hook_error, w.hook("couchside"),
         BridgeError("not_ready", "Couchside UI control did not become ready"))
    w.log(2.8, "bigscreen_bridge.server", logging.ERROR, "Request failed")
    yield ("command_burst", "Burst of failed commands",
           "An automation fired a run of invalid or unanswerable requests.", w)


def main():
    OUT.mkdir(exist_ok=True)
    index = []
    for key, title, summary, world in scenarios():
        snapshot, diagnostics = world.run()
        # The top level is exactly Bridge.snapshot(); `diagnostics` exactly Recorder.to_dict().
        fixture = {**snapshot, "diagnostics": diagnostics}
        text = json.dumps(fixture, indent=2, ensure_ascii=False)
        assert CONFIG["token"] not in text and CONFIG["mqtt"]["password"] not in text
        (OUT / f"{key}.json").write_text(text + "\n")
        index.append({"id": key, "title": title, "summary": summary, "file": f"{key}.json"})
    (OUT / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    print(f"wrote {len(index)} fixtures to {OUT}")


if __name__ == "__main__":
    main()
