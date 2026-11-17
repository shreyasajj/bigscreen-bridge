# Bridge status

A read-only, 10-foot "Bridge status" screen for Plasma Bigscreen. It shows the
Bigscreen Bridge, its four app hooks, MPRIS players, the display and volume, and
recent commands and warnings.

It reads the running bridge's `Status()` on the session bus
(`org.bigscreen.Bridge.Status`, see [docs/protocol.md](../docs/protocol.md)). That needs no
token and cannot change anything: the interface has one read method and one
signal. Nothing on this screen changes the TV, the apps or the bridge.

## Run it

Requirements on the TV: `qml6`, Kirigami, `org.kde.bigscreen` and Plasma's own
`org.kde.plasma.workspace.dbus` QML module (part of `plasma-workspace`). Plasma
Bigscreen on KDE neon has all four (checked on Qt 6.11.1, plasma-workspace 6.8.2);
nothing is built and nothing extra is installed. The bridge must include the
Status interface; an older bridge is reported as "Bridge has no Status interface".

```sh
./launch.sh install               # copy to the TV and add "Bridge Status" to the launcher
./launch.sh start                 # install, then start live
./launch.sh start 6               # live, starting on card 6 (0-7)
./launch.sh start --fixtures vacuumtube_down:4:sources   # fixture mode
./launch.sh screenshot name       # Spectacle capture to screenshots/name.png
./launch.sh logs                  # the screen's journal
./launch.sh stop                  # close it
./launch.sh uninstall             # stop it and remove its files and launcher entry
```

`BIGSCREEN_HOST` overrides the target (default `mediac@192.168.30.153`). Files go
to `~/.local/share/bigscreen-status`, the launcher entry to
`~/.local/share/applications/bigscreen-status.desktop`. On the TV, `run.sh` starts
`qml6 main.qml` as the transient user unit `bigscreen-status`; starting it again
replaces the running one. The unit inherits the Plasma session's `WAYLAND_DISPLAY`.
The bridge service is never touched.

| Remote | Action |
| --- | --- |
| ▲ ▼ | move between cards |
| OK | show which snapshot and diagnostics fields the card uses |
| Back | close the sources panel, then close the screen |
| ◀ ▶ | previous / next scenario (fixture mode only; nothing in live mode) |

### Live mode

`components/LiveSource.qml` calls `Status()` through
`org.kde.plasma.workspace.dbus` (`SessionBus.asyncCall`). It re-reads after every
`StatusChanged` signal, at once when the bridge's name appears, and every 10 s as
a safety net. While the bridge does not answer, the screen shows only the Bridge
card with "Bridge not running" (or "Bridge has no Status interface" for an older
bridge) and the D-Bus error name, never the last data, and keeps retrying. The
badge reads LIVE or NO BRIDGE; the header shows when the bridge last answered. Ages
are computed against the bridge's `generated_at`.

`SignalWatcher` only receives signals when `service` is a well-known name. With an
empty service nothing arrives (tested on the TV).

### Fixture mode

`--fixtures` reads `fixtures/*.json` instead of the bus, with ◀ ▶ switching
scenarios and a MOCK DATA badge. `QML_XHR_ALLOW_FILE_READ=1` (set by `run.sh`) lets
QML read the JSON.

## Scenarios

| File | What it shows |
| --- | --- |
| `healthy.json` | Every running hook pushes its changes; Stremio is playing and focused; Flimmer is not running. |
| `polled.json` | Stremio never sent `StateChanged`, so it is read every 2 s and the bridge logged that once. |
| `flimmer_loading.json` | Flimmer is ready but its catalogue is still loading; an `open` is still in progress. |
| `vacuumtube_down.json` | The window is open and Chromium's MPRIS player is playing, but the socket doesn't answer. `control_available` is false and the MPRIS player appears in `sessions`. |
| `display_off.json` | `pause_all` then `screen_off`; DPMS off, `paused_by_bridge` set. |
| `mqtt_down.json` | MQTT broker unreachable; the link retries every 5 s. |
| `ha_disconnected.json` | No WebSocket clients for three minutes (heartbeat timeout). |
| `command_burst.json` | Nine failed commands in 15 s; the bridge logged the burst at the fifth. |

`fixtures/index.json` sets the order, titles and one-line summaries.

## How the fixtures are made

`tools/make_fixtures.py` invents nothing. The top level of each fixture is the real
`Bridge.snapshot()` over fake KWin windows, app states (through the real hooks'
`accept()`) and MPRIS players. `diagnostics` is the real `Recorder.to_dict()` after
replaying a timed history (hook reads and signals, requests, MQTT and WebSocket
events, log lines) into the real recording hooks on a fake clock. Lines the bridge
logs from other modules (`MQTT link lost`, `WebSocket client closed`, `Request
failed`) are fed to the real log buffer in the format those modules use.
`tests/test_status_fixtures.py` fails if a fixture's shape drifts from the bridge.
Regenerate after bridge changes:

```sh
.venv/bin/python bigscreen-status/tools/make_fixtures.py
```

Two details come straight from the bridge and show on screen:

- Couchside has no `media` or `state`. Music Assistant owns playback, so its title
  comes from the Sendspin MPRIS player in `sessions`.
- An MPRIS player appears in `sessions` only while no native hook owns its app;
  `diagnostics.mpris[]` lists every player, with `superseded_by`.

## Diagnostics fields

The full schema is in [docs/protocol.md](../docs/protocol.md#diagnostics); the
recording is `bigscreen-bridge/src/bigscreen_bridge/diagnostics.py`. What the screen
takes from the snapshot: `available`, `active_app`, `apps[].installed`, `running`,
`hook_installed`, `control_available`, `state`, `media`, `view`, `playback_owner`,
`ma_player_id`, `power.display`, `audio.volume`, `audio.muted`, `audio.streams` and
`sessions[]`.

Where the built bridge differs from the mock's proposal:

| Field or line | What changed, and why |
| --- | --- |
| `hooks.*.mode` | `"polled"` when `Hook.polled` (never announced, or VacuumTube not ready), not `pushed and not stale`. `stale` is also true for the moment between a signal and the re-read it causes, which would have flashed every signalling hook to "polled". |
| `hooks.*.poll_reason` | `not_running` when the endpoint does not answer and no window matches; `not_ready` when it does not answer although a window matches, or (VacuumTube) before Leanback is ready; `never_signalled` when it answers but has never signalled. |
| `hooks.*.consecutive_failures` | A closed app fails every 2 s poll, so this counts into the thousands. The screen hides it when `poll_reason` is `not_running`. |
| `hooks.*.signals_received` | Counts only signals from the app (or its MPRIS player). VacuumTube's settle re-read after an MPRIS change is the bridge's own and is not counted. |
| `hooks.*.last_error.message` | The bridge's own message. When the app itself reported the error, its text is replaced by "`<App>` reported an error" and its code is kept only if it is a plain identifier. |
| `hooks.flimmer.channels` | `null` until a channel page has been listed (`list_channels` returns `total`); `get_state` has no count. |
| `mqtt.retry_seconds` | Recorded when the link is created (`MqttLink.retry`). `failures` counts attempts since the last successful connection. |
| `websocket.last_disconnect_reason` | Also `connection_lost` and `unexpected_message` (a non-text frame), besides `normal`, `heartbeat_timeout` and `send_timeout`. |
| `commands[].command`, `app` | Only known names; anything else is `unknown`, so client text never lands here. Requests that fail before they have a valid ID are listed with `id: null`. |
| "Home Assistant WebSocket client closed (…)" | Logged as "WebSocket client closed (…)": any authenticated client can connect, and the bridge cannot tell it is Home Assistant. |
| "Nine failed commands from one client in 15 s" | Logged once when a client reaches five failures in 15 s ("5 failed commands from one websocket client in 15 s"), then at most once a minute per client. |
| "Stremio has not emitted StateChanged; polling every 2 s" | Logged once per run of an endpoint that has answered for 30 s without signalling. An idle modern app that has had nothing to announce yet is logged too, which is accurate: it is being polled. |
| "VacuumTube control socket unavailable (not_ready)" | WARNING once per outage, only while VacuumTube's MPRIS player is present (the app is running); a closed VacuumTube logs nothing. |
| Couchside navigation | New INFO line per bridge-acknowledged navigation: "Couchside acknowledged navigation to `<kind>` (request `<first 8 of ID>`)". If Couchside changes view without this line, the bridge did not ask for it. |
