# Bigscreen Bridge protocol v1

Authenticated local WebSocket at `/ws`, default port 8765. Clients send an HTTP
`Authorization: Bearer <token>` header; tokens are never accepted in query strings.
No browser Origin is permitted. Use only on a trusted LAN or behind TLS/VPN.
`/health` exposes only service health, without identities or windows.

On connect the bridge sends `{ "type": "state", "protocol": 1, "device_id": "...",
"name": "...", "active_app": null, "apps": [...] }`. The device UUID is persisted
independently of hostname/IP. State updates replace the whole snapshot. Window
captions are not exposed. Apps declare `id`, `name`, `installed`, `running`,
`capabilities`, and `state`; UI focus is not evidence of media playback.

Requests: `{ "id": "unique UUID", "command": "focus", "app": "stremio" }`.
Commands initially supported: `get_state`, `browse`, `focus`.
`browse` returns installed application entries, each playable with typed media
content type `app` and ID equal to the app's fixed identifier.
Response: `{ "type": "result", "id": "same ID", "ok": true, "result": {...} }`
or `ok:false, error:{code,message}`. IDs must be nonempty strings <=128 chars;
within a WebSocket session a bounded response cache makes retries idempotent.
Do not retry a command with a new ID after a disconnect unless it is safe to repeat.
Only one request executes at a time per connection; mutation lock serializes
focus/launch across all clients.

Errors include invalid_request, unsupported, unknown_app, not_installed,
window_manager_unavailable, launch_failed, readiness_timeout, focus_failed.
Unsupported content or command is rejected before any launch or focus. Focus
without content preserves media state; focus with content requires a negotiated
adapter hook. Bridge startup waits for an authoritative KWin snapshot before
lifecycle decisions. Launch uses a fixed Flatpak ID, never a client command;
a requested app must acquire a matching window within the readiness deadline.
An acknowledgement confirms window activation was requested and KWin then
reported that window active, not that media began playing.

Initially planned adapter content types (Couchside is now implemented below): couchside track/album/artist/
playlist/search/now_playing; stremio movie and series episode (Stremio video ID);
flimmer channel (source-qualified persistent ID); vacuumtube youtube_video,
youtube_playlist, search. Each adapter must validate IDs, check readiness,
return explicit errors and declare only verified capabilities. Additional
commands: play/pause/stop/seek/volume/next/previous only when supported. Position
includes UTC observation timestamp; duration uses seconds, volume 0..1.
Couchside declares MA ownership and does not create another playback controller.
Optional MQTT (`mqtt.py`) mirrors this API: retained `availability` and `state`
under `<base_topic>/<device_id>/`, and, only with `"commands": true`, the same JSON
requests on `command` answered on `response`. Existing Stremio messages remain
unchanged. No MQTT Discovery media player is introduced.

Discovery: `_bigscreen-bridge._tcp` via Avahi with TXT `device_id`, `name`,
`protocol=1`. The token is never announced.

`browse` with `app` lists that app's library: `{"id","command":"browse","app",
"path"?, "offset"?, "limit"?}` (`limit` 1-50, `path` a section name matching
`[a-z_]{1,32}`). Each item has `id`, `name` and either `path` (a section to browse
next) or `content_type` plus `media_content_type`, the typed ID to pass to `open`.
`next_offset` is null on the last page. Apps list `"browse": true` in the snapshot
when a library is available.

Security: constant-time token check, >=32-character generated secret, bounded
frames, bounded per-session result cache, no arbitrary launch arguments/URLs,
no remote eval, user-private config, no secrets in logs. Local D-Bus trusts the
logged-in Unix user; sandboxed app hooks need narrowly scoped bus permissions.

## Adapter contract (shared by every application hook)

Every app exposes one local request/response channel carrying strict JSON:
`{"id", "command", ...}` in, `{"id", "ok", "result" | "error": {"code", "message"}}`
out, with the request ID echoed. Two transports exist: `Execute(s) -> s` on a
session-bus name the sandboxed app owns (Couchside, Stremio, Flimmer), and one JSON
line per connection on a user-private Unix socket (VacuumTube). `get_state` returns
at least `protocol: 1`, `ready`, and `content_types`; apps with a player add `media`
and `capabilities`. In the bridge this is `hooks.py` (`Hook`, `DBusHook`,
`SocketHook`); an app module adds only its identity, validation and navigation call,
and `server.py` routes every app through one `ADAPTERS` table.

A hook may offer a read-only library with `"browse": true` in its state and a
`browse` command taking optional `path`, `offset` and `limit`, returning
`{"items": [{"id", "name", "content_type" | null, "path" | null}], "next_offset"}`.
The bridge rejects any page with an item it would not also agree to open. Couchside
(Music Assistant library sections) and Stremio (stored library) implement it;
Flimmer keeps its earlier `list_channels` command; VacuumTube has none.

A hook without its own signal can be followed through the app's MPRIS player
(`MprisRegistry.follow`): VacuumTube's Chromium player announces changes, and the
socket is then read for the authoritative state.

D-Bus hooks should emit a data-free `StateChanged` signal on their control interface
whenever the state they publish really changes (not for ordinary playback progress).
The bridge then re-reads `get_state`; it also watches the name's owner, so start and
exit are seen immediately. A hook that has never emitted the signal is polled every 2
seconds, so unpatched or older builds keep working. Everything is fully re-read every
10 seconds as a safety net.

Snapshots are sent to clients only when something other than the playback clock
changed: `updated_at` is ignored, and while playing, `position` only counts when it
departs from real time (a seek or a stall). Clients interpolate from `position` and
`updated_at`. Transport capabilities are listed only while the app reports playing,
paused or buffering; volume capabilities are listed whenever the app negotiates them.

System commands (no `app` field) act on the device: `set_volume` (`volume_level`
0..1), `mute`, `unmute`, `screen_on`, `screen_off`, `lock_screen`, `get_power_state`,
and the session commands `pause_active`, `pause_all`, `resume_active`,
`resume_previously_paused`, `stop_active`, `stop_all`. In Home Assistant the device
player maps volume/mute and turn on/off to the first five; app players never do.

## Couchside UI extension (implemented phase 5)

The `open` command accepts `app: "couchside"`, `content_type` (without prefix), and
`content_id`. Allowed types: `now_playing`, `search`, `track`, `album`, `artist`,
`playlist`. Music item IDs must be Music Assistant URIs with the corresponding
kind, for example `library://album/123`. Search accepts up to 512 characters;
Now Playing accepts an empty ID or `now_playing`.

```json
{"id":"request-uuid","command":"open","app":"couchside","content_type":"album","content_id":"library://album/123"}
```

This navigates the existing Couchside UI and does not start playback. The UI
resolves exact music items through its authenticated MA client and acknowledges
QML navigation. ID resolution, MA disconnection, invalid types and navigation
failures return explicit errors. HA `play_media` uses the prefixed media content
types `couchside_now_playing`, `couchside_search`, `couchside_track`, etc.
Playback remains through the existing MA integration; no duplicate playback entity
or transport features are added. Snapshot attributes identify MA as playback
owner and expose the local MA player ID, never MA credentials.

Couchside's application IPC is session D-Bus
`io.github.shreyasajj.Couchside.Control`, object
`/io/github/shreyasajj/Couchside/Control`, `Execute(s) → s`. Its strict JSON
protocol supports `get_state`, `focus`, `navigate`; no shell/eval/player commands.
The app's D-Bus name lease enforces one patched UI instance. Sendspin has its own
independent supervisor and lock. The installed Flatpak grants only the one UI bus
name in addition to the existing Sendspin MPRIS permission.

The bridge probes this endpoint; `couchside_control: true` in private config
records installation of a verified patched app and enables cold launch even with
a background Sendspin Flatpak present. Do not enable that flag for an unpatched
Couchside. KWin snapshots exclude deleted windows and are refreshed before focus;
the control endpoint is also checked to distinguish a closing UI window from a
usable instance. A request waits for UI and, when needed, MA readiness.

`view` is the latest bridge-acknowledged navigation, not a mirror of later manual
remote navigation. Music library browsing and media metadata mirroring remain
separate future work. Other applications still reject media content requests.

## Stremio extension (implemented phase 6)

`open` accepts `app: "stremio"`, content type `movie`, `series`, or `episode`.
Movie/series content IDs are bounded Stremio-compatible strings. Episode content
IDs are objects containing exactly `series_id` and `video_id`. Allowed ID
characters are alphanumerics plus colon, dot, underscore and hyphen (max 256).
`resume` is an optional boolean; it is rejected for other apps.

```json
{"id":"movie-request","command":"open","app":"stremio","content_type":"movie","content_id":"tt0133093"}
{"id":"episode-request","command":"open","app":"stremio","content_type":"episode","content_id":{"series_id":"tt0944947","video_id":"tt0944947:1:2"}}
{"id":"pause-request","command":"pause","app":"stremio"}
{"id":"seek-request","command":"seek","app":"stremio","position":120}
```

Open returns `delivery: "detail"`; stored resume can return `stream_selection`.
Neither reports playback started. Real playback state is observed separately.
Missing titles/episodes and missing matching resume records return errors.
Requests do not accept direct stream URLs, paths, addon configuration or scripts.

Play/pause/stop/seek require the installed native control hook. Volume and next/
previous are unsupported. App snapshots expose media state and capabilities;
transport controls are advertised only while an actual player is open, and seek
requires known duration. UTC `updated_at` anchors position interpolation.

The native endpoint is `io.github.shreyasajj.StremioTV.Control`, object
`/io/github/shreyasajj/StremioTV/Control`, method `Execute(s) → s`. It owns the app's
single-instance lease and waits for renderer acknowledgements. Stremio's legacy
MQTT messages are independent and retain their previous schema.

HA uses `stremio_movie`, `stremio_series`, and `stremio_episode`. Episode media IDs
are JSON text with the two fields above; `extra: {resume: true}` requests saved
progress. A dedicated Stremio entity exposes the same verified controls while the
main entity follows the active app. Neither provides unsupported volume features.

## Flimmer channel control

A verified Flimmer hook adds `flimmer_channel` to its advertised content types.
WebSocket commands use `app: flimmer`. Browse with
`{"id":"iptv-page","command":"browse","app":"flimmer","offset":0,"limit":50}`.
The result contains `items` with opaque `id`, `name`, `number`, `current` and `next`,
plus `total`, `loading` and nullable `next_offset`. Programme times are Unix seconds.
Use the returned ID with `command: open`, `content_type: channel`, and
`content_id: channel-<64 hex digits>`. Resume arguments are unsupported for live TV.

Selection acknowledges `delivery: player_accepted`; watch pushed media state for
actual playback. A loading catalogue is retried with the same request ID for up to
20 seconds. Unknown or ambiguous IDs fail explicitly. Play/pause/stop flags are
negotiated from the actual player state; volume and seek are not advertised.
Provider artwork and stream URLs are excluded. The native app endpoint is
`io.github.jclsn.Flimmer.Control.Execute` at `/io/github/jclsn/Flimmer/Control`.

## VacuumTube / YouTube (phase 8)

Use `app: vacuumtube` and `content_type: video|playlist|search` with `open`.
In Home Assistant the corresponding media types are `youtube_video`,
`youtube_playlist`, and `youtube_search`. Video IDs contain 11 URL-safe characters;
playlist IDs contain 10–128; search text is bounded to 200 characters without
control characters. Arbitrary URLs, script and resume arguments are rejected.

An open response reports `video_selected`, `playlist_selected`, or `search_opened`
after the native application observes the requested view/content. The native
listener alone is insufficient evidence. Startup transitions can discard links,
so the app retries the same fixed content request for a bounded 18 seconds.
Selection acknowledgements do not guarantee successful network playback.

Media includes real video ID, title, fixed-host thumbnail, state, position,
duration and UTC observation time. Navigation `view` includes semantic kind and,
when applicable, the actual search query or playlist ID. Capabilities negotiate
play/pause/stop/absolute seek, plus `volume_set` (`volume_level`, 0–1) and
`volume_mute` (`muted`, boolean). Volume/mute use the app's existing authoritative
controller. `volume` is its configured level and `output_volume` is read from the
player; mute is represented separately. Music Assistant remains authoritative for
Couchside and gains no duplicate playback/volume controls.

The app's native transport is one bounded JSON line per private Unix-socket
connection at `$XDG_RUNTIME_DIR/bigscreen-bridge/vacuumtube.sock`, directory 0700,
socket 0600, both owned by the current user. The KDE bridge verifies ownership,
permissions and socket type before connecting. This transport is local to the
Unix user; external HA access continues through the authenticated WebSocket API.

## Diagnostics

`{"id": "uuid", "command": "diagnostics"}` returns the bridge's own health record
(`bigscreen_bridge/diagnostics.py`). It takes no arguments (any other key is
`invalid_request`) and changes nothing. It is not part of the pushed `state`
snapshot or the MQTT `state` topic, so entities do not change on every request or
log line. Times are UTC ISO 8601 with milliseconds and `Z`; compute ages against
`generated_at`, not the client's clock.

```json
{"schema": 1, "generated_at": "…Z",
 "service":   {"version", "started_at", "port", "health": "ok" | "degraded"},
 "websocket": {"clients", "last_connected_at", "last_disconnected_at", "last_disconnect_reason"},
 "discovery": {"enabled", "announced", "error", "service_type", "instance"},
 "mqtt":      {"configured", "connected", "broker": "host:port", "tls", "commands_enabled",
               "last_connected_at", "last_error", "failures", "retry_seconds"},
 "kwin":      {"ready", "windows", "last_report_at"},
 "hooks": {"<app>": {"transport": "dbus" | "socket", "endpoint", "enabled", "reachable", "ready",
                     "mode": "pushed" | "polled", "poll_reason", "signal_source", "signals_received",
                     "last_signal_at", "last_read_at", "last_read_ok", "consecutive_failures",
                     "last_error": {"code", "message", "at"} | null}},
 "mpris":    [{"bus_name", "identity", "desktop", "app", "state", "role", "superseded_by"}],
 "commands": [{"id", "command", "app", "transport", "started_at", "finished_at", "ok",
               "error_code", "duration_ms"}],
 "warnings": [{"at", "level", "logger", "message", "count"}]}
```

- `last_disconnect_reason`: `normal`, `heartbeat_timeout`, `send_timeout`,
  `connection_lost` or `unexpected_message` (a non-text frame).
- `mqtt.last_error` is the exception class name; `failures` counts attempts since
  the last successful connection.
- `poll_reason` (only while `mode` is `polled`): `not_running` (no endpoint, no
  window), `not_ready` (a window but no answering endpoint, or VacuumTube before
  Leanback is ready), or `never_signalled` (answers, but never signalled). `signal_source` is `StateChanged` or `mpris`.
- Couchside adds `music_assistant_ready`; Flimmer adds `catalogue_loading`,
  `catalogue_loading_since` and `channels` (the last `list_channels` total, `null`
  until a page was listed).
- `commands` keeps the last 50 requests from every transport; a retry answered from
  the cache is not listed again, and a request still running has `ok: null`.
  `command` and `app` are only known names (otherwise `unknown`).
- `warnings` keeps the last 50 distinct `bigscreen_bridge.*` log messages at INFO
  and above, with repeats folded into `count`.

Never included: the token, MQTT credentials, content IDs, URIs, search text, volume
levels, window captions or application payloads. Error messages are the bridge's
own fixed strings; when an app reports an error only its code is kept (or `error`
if the code is not a plain identifier), with the message "`<App>` reported an error".

## Local read-only status (session bus)

For screens on the TV itself, which should not need the token, the bridge exports a
second interface beside the KWin receiver on its own name:

| | |
| --- | --- |
| Name | `org.bigscreen.Bridge` |
| Path | `/org/bigscreen/Bridge` |
| Interface | `org.bigscreen.Bridge.Status` |
| `Status() → s` | JSON `{"snapshot": <the state snapshot>, "diagnostics": <as above>}` |
| `StatusChanged()` | no arguments; sent when either changes, at most about twice a second |

It has no other methods or properties and nothing on it can change the TV, the apps
or the bridge. Listen for `StatusChanged` and re-read `Status()`; also re-read now
and then, because a signal can be missed while the bridge restarts. The session bus
is private to the logged-in user, who can already read the bridge's config and
token, so this does not widen access. The older `org.bigscreen.Bridge` interface at
the same path is the KWin script callback and is not meant for clients.

```sh
busctl --user call org.bigscreen.Bridge /org/bigscreen/Bridge org.bigscreen.Bridge.Status Status
```
