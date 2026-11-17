# Repository implementation map

Audit date: 2026-11-16. Source inspection is distinguished from live verification.
Audit checkouts live in ignored `upstream/`. Published application PRs and later
canonical source changes are recorded in the phase sections and verification docs.

## Audited revisions

| Repository | Revision | Evidence and existing capabilities | Files to change in later phases |
|---|---|---|---|
| [Couchside](https://github.com/shreyasajj/couchside) | `722e614c70fd643d3a123c4529ea9ac5922d8e11` | `core/connection.py` constructs MusicAssistantClient; `core/playback.py` matches the local Sendspin identity against MA player/output protocol IDs, reads metadata and controls MA players/queues. `ui/controller.py` has search/playUri/library/queue slots. `ui/qml/Main.qml` owns navigation. `sendspin/runner.py` has a separately supervised daemon and single-instance file lock. The UI entry point has no equivalent lock. | `src/couchside/ui/app.py`, `ui/controller.py`, `ui/qml/Main.qml`; new `ui/bridge_control.py`; Flatpak manifest and tests. Add single-instance UI endpoint, navigation signals and explicit readiness. Preserve independent Sendspin lifetime. |
| [Stremio TV](https://github.com/shreyasajj/stremio-tv) | `605547b575ba2c20f791e96f8089ae7ff19a076d` | `native/homeassistant.cpp/.h` uses libmosquitto, retained `stremio_tv/<box>/{state,availability}`, and command topic; existing discovery creates sensors/buttons/image, not a media player. State includes title/type/episode/position/duration/progress/poster/imdb_id/updated_at. Commands are play/pause/toggle/stop/relative seek. Qt WebChannel joins web UI and libmpv. `native/main.cpp` has no single-instance guard. | `native/main.cpp`, new `native/bridgecontrol.{h,cpp}`, `native/CMakeLists.txt`, `native/Main.qml`, `native/bridge.js`, `ui/js/homeassistant.js`, `ui/js/main.js`, `ui/js/screens/detail.js`, `ui/js/player.js`, Flatpak manifest, tests. Reuse existing playback and stored progress; add typed movie/episode navigation and acknowledged IPC without altering legacy MQTT topics. |
| [Flimmer](https://github.com/jclsn/flimmer) | `48dad7da9aa2c5bacc1734b7962d045f25abe86d` | `src/channelmodel.h` exposes get/indexOfUuid/uuidAt, now/next EPG roles; `src/epgstore.h` stores programmes. `qml/Main.qml` owns watch(uuid), `PlayerView.qml` owns play/pause/timeshift. `src/m3u.cpp` uses tvg-id or channel name, with order-dependent # suffixes for duplicates. `src/main.cpp` lacks a single-instance control endpoint. | `src/main.cpp`, new `src/bridgecontrol.{h,cpp}`, `CMakeLists.txt`, `qml/Main.qml`, `qml/PlayerView.qml`, `src/m3u.cpp`, channel/EPG tests and Flatpak manifest. Expose TVH UUIDs with source namespace; persist M3U IDs and handle ambiguous duplicates explicitly. Never expose credential-bearing stream URLs to HA. |
| [VacuumTube](https://github.com/shy1132/VacuumTube) | `0c7df3a1e2411b430e416a0b8f3c872bb1f8fb4d` | `src/index.js` already requests Electron single-instance lock and forwards deeplink to running window. Supports --deeplink and youtu.be normalization. `src/preload/modules/h5vcc/index.js` queues links until Leanback registers its listener. This is stronger evidence than assuming loadURL routes content. Metadata/player command support remains unverified against live Leanback. | `src/index.js`, new `src/bridge-control.js`, `src/preload/index.js`, new preload control/telemetry module, Flatpak manifest and node tests. Fixed-schema IPC only; no remote JavaScript or arbitrary URL execution. Gate video/playlist/search and metadata independently after live tests. |

All Couchside relative paths above are beneath `src/couchside/` where abbreviated.

## New bridge repository

| Phase | Files | Acceptance gate |
|---|---|---|
| 1 — audit | this map, `docs/verification.md` | Pin revisions; inspect real source; inventory live installations. |
| 2 — contract | `docs/protocol.md` | Define auth, IDs, acknowledgements, capability negotiation, lifecycle and errors. |
| 3 — KDE bridge | `bigscreen-bridge/src/bigscreen_bridge/{server,kwin,adapters}.py`, `kwin/monitor.js`, `systemd/bigscreen-bridge.service`, `scripts/install-tv.sh` | Authenticated WebSocket; KWin Wayland identity/focus; launch only after authoritative window snapshot; readiness timeout; reject unsupported content before launch. |
| 4 — native HA player | `custom_components/bigscreen_bridge/{__init__,config_flow,coordinator,media_player,client}.py`, manifest/strings | UI connect, stable device ID, active app/source list, push updates, availability/reconnect, browse installed apps; no unsupported transport flags. Install to actual HA and test there. |
| 5 — Couchside/MA | adapter + Couchside files above | UI-only Couchside surface; existing MA player remains authoritative; test background audio and running-instance navigation. |
| 6 — Stremio | adapter + Stremio files above | Real movie/episode selection, explicit resume policy, metadata/libmpv commands; original MQTT tests still pass. |
| 7 — IPTV | adapter + Flimmer files above | Channel browse/select with persistent IDs, now/next EPG and real playback state. |
| 8 — YouTube | adapter + VacuumTube files above | Live Leanback tests for each link type and telemetry; unsupported features stay disabled. |
| 9 — universal links | adapter validators, HA browse/play_media | Validate typed IDs; no shell commands/URLs supplied by clients; library pagination and stale-ID errors. |
| 10 — full validation | `tests/`, install docs, automation examples | Reconnect/reboot/multiple devices, launch/focus/content readiness matrix, token rejection, sandbox restrictions, real HA service calls for each supported feature. |

## Deployment facts

SSH `mediac@192.168.30.153` works without sudo. Host is media-ser, user lingering enabled; user systemd is degraded (cause not yet audited). Python3, qdbus6, kpackagetool6 available; aiohttp/dbus-next/pytest initially absent. All four target Flatpaks installed. KWin exposes Plasma 6 Scripting loadScript/start/unloadScript methods on the session bus.

HA port 8234 refuses connections from workstation and TV. Port 8123 responds from TV; installation access/authentication is not established. MQTT credentials and current Stremio box are not yet inspected. Existing failures are five historical Couchside app units plus session-migration.service; they were left untouched. Do not infer application interoperability from installation alone.

## Canonical local repositories (user-directed source change)

The user identified these working copies for direct edits and installation:

| App | Local repository and starting revision | Existing work preserved |
|---|---|---|
| Couchside | `/Users/shre/Documents/projects/couchside`, branch `ui-modernize`, `afd45818f60d76f7c0068d612d2a52e679fabddc` | Two local commits ahead of origin; unrelated untracked HTML and support.js untouched. This phase's hook is applied directly here. |
| Flimmer | `/Users/shre/Documents/projects/plasma-tv`, branch `fix/pr7-review`, `a78b99966e0586ea09f40b727a0b9a01f84e67b4` | Existing source abstractions and source-qualified channel ID code differ from the initial upstream audit. Re-audit these before phase 7; this phase makes no changes. |
| Stremio TV | `/Users/shre/Documents/projects/stremio-tv`, branch `main`, `b2e331645a7672051d9c017365e685e6f50517a1` | Starts 16 commits behind its tracked origin; untracked .vscode preserved. Reconcile upstream MQTT changes before phase 6 deployment; this phase makes no changes. |


The fourth canonical checkout is now `/Users/shre/Documents/projects/VacuumTube`,
created with `gh repo fork shy1132/VacuumTube --clone`. Its `origin` is
[shreyasajj/VacuumTube](https://github.com/shreyasajj/VacuumTube), and `upstream`
is the original repository. Local branch `bigscreen-bridge` starts at
`0c7df3a1e2411b430e416a0b8f3c872bb1f8fb4d`. The **23 existing Node tests pass**.
The fork's `docs/bigscreen-bridge.md` records the patch scope, build path and
unverified Leanback capabilities. No runtime patch, Linux build or TV installation
was performed during fork setup; the development branch has not been pushed.

All four source locations are recorded in `config/repositories.json`. These are
development checkout locations, not the private bridge runtime configuration.
The local Flimmer checkout's actual origin is `jclsn/plasma-tv`, distinct from the
initial `jclsn/flimmer` audit URL; inspect its current packaging before deployment.

Couchside phase 5 files: new `src/couchside/core/bridge_control.py`,
`src/couchside/ui/bridge_control.py`, `src/couchside/ui/qml/ContentPage.qml`,
`tests/test_bridge_control.py`, `tests/test_bridge_ui.py`; updates to `ui/app.py`,
`ui/qml/Main.qml`, `ui/qml/LibraryPage.qml`, Flatpak manifest and README.
No commit, push or branch switch was made. A reference patch based on the local
Couchside revision is saved under `patches/couchside/`.

Bridge/HA phase 5 files: new `bigscreen-bridge/src/bigscreen_bridge/couchside.py`,
`tests/test_couchside.py`, Couchside deployment/smoke scripts; updates to
`server.py`, `kwin.py`, `kwin/monitor.js`, HA `media_player.py`, tests and docs.
The deployment copies the canonical checkout to a separate TV build source path,
leaving `~/couchside` and the previous `~/cs-exp` sources intact.

## Phase 6 — Stremio TV

Canonical branch `bigscreen-bridge` starts from current origin/main
`605547b575ba2c20f791e96f8089ae7ff19a076d`, without changing the old local `main`.

App changes: new `native/bridgecontrol.{h,cpp}` and
`ui/js/{bridge-protocol,bridgecontrol}.js`; updates to `native/main.cpp`,
`native/Main.qml`, `native/bridge.js`, CMake and Flatpak manifest,
`ui/js/{main,homeassistant,player,streams}.js`, `ui/js/screens/detail.js`, native
end-to-end tests and new protocol tests. Added a local incremental Flatpak build
helper and interface documentation.

Bridge changes: `bigscreen-bridge/src/bigscreen_bridge/stremio.py`, updates to
`server.py`, HA `media_player.py` (active + dedicated Stremio entities), tests,
TV smoke/deploy scripts, protocol docs and automation examples.

Verified: 6 protocol tests, 92 native Linux checks including existing MQTT
regressions, 58 bridge/HA tests; real TV movie/series/episode navigation,
single-instance launch, authenticated WebSocket focus and cold launch. Playback
commands and resume were tested on real libmpv in the isolated Linux container.
See [phase 6 verification](verification-phase6.md) for exact limits and commits.

## Phase 7 — Flimmer

The canonical `plasma-tv` checkout now has a local `bigscreen-bridge` branch.
App changes: new `src/bridgecontrol.{h,cpp}` and `src/channelidentity.{h,cpp}`;
updates to `src/main.cpp`, `qml/Main.qml`, channel model/EPG cache/M3U parser,
source identity prefixing, CMake, tests and Flatpak permissions; new user-only
build helper and local control documentation. Legacy UI/guide channel keys remain
unchanged; automation identities are separate and persisted in cache schema 5.

Bridge/HA changes: new `bigscreen-bridge/src/bigscreen_bridge/flimmer.py`, updates
to `server.py` and `media_player.py`, new `tests/test_flimmer.py`, deploy/smoke and
synthetic IPTV fixture scripts. Verified: 18 Qt suites, 75 root tests, real TV
single-instance launch, authenticated paginated browsing, XMLTV EPG, MPEG-TS
playback/pause/timeshift resume. See [phase 7 verification](verification-phase7.md)
for provider, artwork and real HA installation limitations.

## Phase 8 — VacuumTube / YouTube

Canonical checkout: `/Users/shre/Documents/projects/VacuumTube`, development
branch `bigscreen-bridge`, based on `0c7df3a1e2411b430e416a0b8f3c872bb1f8fb4d`.

App changes: new `src/{bridge-control,bridge-electron,bridge-protocol}.js`,
`src/preload/modules/bigscreen-bridge.js` and preload utilities
`bigscreen-{runtime,player,metadata,navigation}.js`; updates to `src/index.js`,
`h5vcc/index.js`, the volume controller and command resolver. New Node tests,
archive build helper, Flatpak user-install script and interface documentation.
No runtime dependency was added; the development installer reuses the matching
installed Linux Electron binary and production dependencies.

Bridge/HA changes: new `bigscreen_bridge/vacuumtube.py`, extended server/player
capabilities, typed `youtube_video`, `youtube_playlist`, `youtube_search`, native
YouTube media player, volume/mute services, deploy/smoke scripts and an isolated
real Home Assistant smoke test. See [phase 8 verification](verification-phase8.md).
