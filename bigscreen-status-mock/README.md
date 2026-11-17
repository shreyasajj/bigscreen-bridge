# Bridge status mock

A read-only, 10-foot "Bridge status" screen for Plasma Bigscreen. It shows the
Bigscreen Bridge, its four app hooks, MPRIS players, the display and volume, and
recent commands and warnings.

**It is a mock.** It reads `fixtures/*.json` and never connects to the bridge,
never reads its config, and never shows or logs the pairing token. Nothing on the
screen changes the TV.

## Run it

Requirements on the TV: `qml6`, Kirigami and `org.kde.bigscreen`. Plasma Bigscreen
on KDE neon already has all three (checked on Qt 6.11.1); nothing extra is installed.

```sh
./launch.sh                       # copy to the TV and start (scenario 1)
./launch.sh start vacuumtube_down # start on one scenario
./launch.sh start healthy:2:sources  # scenario, focused card (0-7), sources panel open
./launch.sh screenshot name       # Spectacle capture to screenshots/name.png
./launch.sh logs                  # the mock's journal
./launch.sh stop                  # close it
```

`BIGSCREEN_HOST` overrides the target (default `mediac@192.168.30.153`). The
script copies `main.qml`, `components/` and `fixtures/` to
`~/.local/share/bigscreen-status-mock` and runs
`qml6 main.qml` as a transient systemd user unit, `bigscreen-status-mock`. The
unit inherits the Plasma session's `WAYLAND_DISPLAY`, and
`QML_XHR_ALLOW_FILE_READ=1` lets QML read the JSON. The bridge service is never
touched.

| Remote | Action |
| --- | --- |
| ◀ ▶ | previous / next scenario |
| ▲ ▼ | move between cards |
| OK | show which snapshot and diagnostics fields the card uses |
| Back | close the sources panel, then close the mock |

## Scenarios

| File | What it shows |
| --- | --- |
| `healthy.json` | Every hook pushes its changes; Stremio is playing and focused. |
| `polled.json` | Stremio never sent `StateChanged`, so it is read every 2 s. The snapshot looks the same as healthy; only diagnostics show the difference. |
| `flimmer_loading.json` | Flimmer is ready but its catalogue is still loading; an `open` is still in progress. |
| `vacuumtube_down.json` | The window is open and Chromium's MPRIS player is playing, but the socket doesn't answer. `control_available` is false and the MPRIS player appears in `sessions`. |
| `display_off.json` | `pause_all` then `screen_off`; DPMS off, `paused_by_bridge` set. |
| `mqtt_down.json` | MQTT broker unreachable; the link retries every 5 s. |
| `ha_disconnected.json` | No WebSocket clients for three minutes. |
| `command_burst.json` | Nine failed commands in 15 s. |

`fixtures/index.json` sets the order, titles and one-line summaries.

## How the fixtures are made

`tools/make_fixtures.py` builds each fixture by running the real
`Bridge.snapshot()` and the real hooks' `accept()` over fake KWin windows, app
states and MPRIS players. The top level of every fixture is therefore exactly
what `snapshot()` returns. Regenerate after bridge changes:

```sh
.venv/bin/python bigscreen-status-mock/tools/make_fixtures.py
```

Two details come straight from that code and show on screen:

- Couchside has no `media` or `state`. Music Assistant owns playback, so its title
  comes from the Sendspin MPRIS player in `sessions`.
- An MPRIS player appears in `sessions` only while no native hook owns its app. VacuumTube's
  Chromium player is hidden when the socket answers and appears when it doesn't.

Everything else is under `"diagnostics"`. Today the bridge records none of it.

## Diagnostics fields

What the screen takes from the real snapshot: `available`, `active_app`,
`apps[].installed`, `running`, `hook_installed`, `control_available`, `state`,
`media`, `view`, `playback_owner`, `ma_player_id`, `power.display`,
`audio.volume`, `audio.muted`, `audio.streams` and `sessions[]`. `/health` gives
only `ok` (KWin ready) and `protocol`.

Everything below is invented for this mock. Each row says what the bridge would
have to record. File references are to `bigscreen-bridge/src/bigscreen_bridge/`.

### `diagnostics.schema`, `generated_at`

Format version and the UTC time the diagnostics were built. Clients compute every
age against `generated_at`, not their own clock.

### `diagnostics.service`

| Field | What would record it |
| --- | --- |
| `version` | `importlib.metadata.version("bigscreen-bridge")` at startup. |
| `started_at` | `serve()` stores the time once the window manager is ready. |
| `health` | The same test as `/health` (`wm.ready.is_set()`), as `"ok"` or `"degraded"`. |
| `port` | `config["port"]`. Not secret; the host is left out. |

### `diagnostics.websocket`

| Field | What would record it |
| --- | --- |
| `clients` | `len(bridge.clients)`; already in memory, never exposed. |
| `last_connected_at` | `Bridge.websocket()` after `self.clients.add(ws)`. |
| `last_disconnected_at`, `last_disconnect_reason` | The `finally` in `Bridge.websocket()` and the timeout path in `Bridge.send()`: `ws.close_code` mapped to `normal`, `heartbeat_timeout` or `send_timeout`. |

### `diagnostics.discovery`

| Field | What would record it |
| --- | --- |
| `enabled` | `service_record()` returned a record (not loopback, `discovery` not false). |
| `announced`, `error` | `advertise()` stores success, or the `BridgeError` text it now only logs. |
| `service_type`, `instance` | From `service_record()`. |

### `diagnostics.mqtt`

| Field | What would record it |
| --- | --- |
| `configured` | `mqtt_settings(config)` is not `None`. |
| `connected` | `MqttLink.client is not None` (set in `session()`). |
| `broker` | `host:port` from the settings. Username and password never appear. |
| `tls`, `commands_enabled` | From the settings. |
| `last_connected_at` | `session()` after publishing `online`. |
| `last_error`, `failures` | `run()`'s `except` (today it only logs `type(exc).__name__`); reset on connect. |
| `retry_seconds` | `MqttLink.retry`. |

### `diagnostics.kwin`

| Field | What would record it |
| --- | --- |
| `ready` | `wm.ready.is_set()`. |
| `windows` | `len(wm.windows)`. Only a count; captions stay private. |
| `last_report_at` | `Receiver.ReportWindows()` in `kwin.py`. |

### `diagnostics.hooks.<app>`

| Field | What would record it |
| --- | --- |
| `transport`, `endpoint` | Class constants: `DBusHook.bus_name`, `SocketHook.socket_path` (shown with `$XDG_RUNTIME_DIR` unexpanded). |
| `enabled` | `hook.enabled`. |
| `reachable` | `Hook.refresh()`: `get_state` answered (`hook.state is not None`). |
| `ready` | `hook.is_ready()`. For Couchside this is the only place it appears, because the snapshot omits `control_available` for owned apps. |
| `mode` | `"pushed"` if `hook.pushed and not hook.stale`, else `"polled"`. |
| `poll_reason` | `never_signalled` (`not hook.pushed`), `not_ready` (VacuumTube's `stale` override) or `not_running` (no window and no endpoint). |
| `signal_source` | `"StateChanged"` for `DBusHook`, `"mpris"` for hooks registered with `MprisRegistry.follow()`. |
| `signals_received`, `last_signal_at` | A counter and timestamp in `Hook.announce()`. |
| `last_read_at`, `last_read_ok`, `consecutive_failures` | `Hook.refresh()` on success and in its `except BridgeError`. |
| `last_error` (`code`, `message`, `at`) | The same `except`, plus failures in `open`, `navigate` and `control`. Messages are the bridge's own fixed strings, never app payloads. |
| `music_assistant_ready` (Couchside) | Copied from the hook's state. |
| `catalogue_loading`, `catalogue_loading_since`, `channels` (Flimmer) | `catalogue_loading` is already in Flimmer's `get_state` but dropped by `_describe()`. The time it first went true would be recorded in `refresh()`. `channels` would come from the last `list_channels` total. |

### `diagnostics.mpris[]`

Every player `MprisRegistry` last read, not only those in `sessions`.

| Field | What would record it |
| --- | --- |
| `bus_name`, `identity`, `desktop`, `app`, `state`, `role` | Copied from `MprisRegistry.players` (`_read()`). `pid` and `owner` are left out. |
| `superseded_by` | `"app:<id>"` when `SessionController.refresh()` hides the player because a native hook owns that app. |

### `diagnostics.commands[]`

A ring buffer of about 50 entries, kept in `Bridge.handle()`, which every
transport goes through.

| Field | What would record it |
| --- | --- |
| `id` | The request ID, cut to 128 characters as now. The screen shows 8. |
| `command`, `app` | From the validated request. |
| `transport` | `"websocket"` or `"mqtt"`, passed by the caller. |
| `started_at`, `finished_at`, `duration_ms` | Measured around `execute()`. While running, `finished_at` and `ok` are `null`. |
| `ok`, `error_code` | From the response. |

Content IDs, URIs, search text, volume levels and the token are deliberately not
logged. Cached retries are not logged twice.

### `diagnostics.warnings[]`

A logging handler (level `INFO` and above, logger `bigscreen_bridge.*`) attached
in `main()`. It keeps the last 50 records, folding repeats into `count`. Fields:
`at`, `level`, `logger`, `message`, `count`.

The fixtures use existing log lines where they exist: `MQTT link lost: %s`
(`mqtt.py`) and `Request failed` (`server.py`). Four lines are **proposed** and
not logged today:

- "Stremio has not emitted StateChanged; polling every 2 s"
- "VacuumTube control socket unavailable (not_ready)"
- "Home Assistant WebSocket client closed (heartbeat_timeout)"
- "Nine failed commands from one client in 15 s"

### How it would be exposed

A read-only `diagnostics` command on the existing authenticated WebSocket:

```json
{"id": "uuid", "command": "diagnostics"}
→ {"type": "result", "id": "uuid", "ok": true, "result": { ...the diagnostics object... }}
```

- It goes in `Bridge.execute()` next to `get_state`, takes no arguments and changes nothing.
- It is kept out of the pushed `state` snapshot, so Home Assistant entities don't
  change on every command or log line, and out of the MQTT mirror unless asked
  for. If it is ever added to MQTT, it gets its own `diagnostics` topic.
- `/health` stays as it is, with no identities.
- A real TV screen would need the token to make this call. It would have to read
  the bridge's private config, or the bridge would offer a separate local-only
  read endpoint (for example `Diagnostics() → s` on its session-bus name
  `org.bigscreen.Bridge`). Both need a decision; this mock does neither.

The bridge has not been changed.
