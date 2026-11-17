# Phase 6 — native Stremio TV control

Date: 2026-11-16. Canonical source:
`/Users/shre/Documents/projects/stremio-tv`, new local branch `bigscreen-bridge`
from `605547b575ba2c20f791e96f8089ae7ff19a076d`. The old local `main` and unrelated
`.vscode` files were preserved. No commit, push or PR was made.

## Implemented

- A native Qt DBus endpoint with fixed-schema JSON requests, request IDs,
  acknowledgements, bounded messages/cache and duplicate-ID protection.
- A D-Bus service-name lease before window creation; second launches focus the
  existing UI and exit. Wayland activation from HA goes through KWin.
- Movie and series metadata navigation; exact episode selection opens the right
  season and focuses that episode. Content resolves before changing the player.
- Stored resume via the existing resume/stream selection flow. The result
  distinguishes `detail` and `stream_selection` from actual playback.
- State/metadata from the existing Qt/libmpv-backed player: title, episode, poster,
  content/video IDs, position, duration, playback state and UTC timestamp.
- Play, pause, stop and absolute seek routed to the existing player handler.
- Renderer readiness, bounded acknowledgements and cancellation checks that
  prevent a late metadata lookup from navigating after cancellation.
- A dedicated native HA Stremio entity plus the main active-app player. Transport
  flags depend on supported capabilities and actual player state; seek requires
  duration. No volume, next or previous flags are exposed. Couchside remains UI-only.
- Typed Stremio `play_media` and the existing WebSocket transport. No MQTT
  Discovery media player is created.

The legacy MQTT topics, normalized payload fields, retained availability,
discovery sensors/buttons/image and existing player commands remain intact.
No actual broker credentials or addon settings were extracted or overwritten for testing.

## Files changed

In Stremio: new `native/bridgecontrol.{h,cpp}`, `ui/js/bridge-protocol.js`,
`ui/js/bridgecontrol.js`, protocol tests, build helper and documentation. Updates
cover `native/{main.cpp,Main.qml,bridge.js,CMakeLists.txt}`, the Flatpak manifest,
`ui/js/{main,homeassistant,player,streams}.js`, `ui/js/screens/detail.js`, and the
native end-to-end suite. A router refresh now preserves whether its page became
hidden while awaiting metadata, fixing an observed race during remote navigation.

In the bridge: new `stremio.py`, protocol/control dispatch, polling and lifecycle
integration, native HA Stremio entity/metadata/features, tests and TV smoke/deploy
scripts. See the [implementation map](implementation-map.md).

## Test results

- **6 Node protocol tests passed**: types/IDs, exact episode navigation, stored
  resume behavior, invalid commands, no-player errors, cancelled lookup safety.
- **92 native Linux checks passed** using real Qt Quick/WebEngine/libmpv, a private
  D-Bus session, a headless Wayland compositor, sample media and a real local
  Mosquitto broker. Tests cover actual pause/play/absolute seek/stop, resume to
  saved position, state metadata, cached ID replay, exact episode focus,
  second-process exit and player preservation. Existing MQTT state payload keys,
  discovery, playback commands and offline last will still pass.
- **58 bridge/HA tests passed** against actual HA core classes, including typed
  episode/resume calls, metadata/timestamps, per-app flags, active-source switching,
  invalid content, unsupported controls, and prior Couchside regression coverage.
- Ruff and relevant Node/shell syntax checks passed.

A native test run exposed a router refresh making the old page visible again;
this was corrected and the native suite then passed. Another run exposed a mock
profile reload synchronization issue; the test now waits for the new renderer
context before driving the mock UI. No test failures are suppressed.

## Live TV verification

Installed user Flatpak commit:
`7dfce97d105e1809a67bc858567430ad3fd93892956b7791fa1338f1134e19dc`.
Previous commit recorded for rollback:
`965476880561e39a6dce8638f24c918b49940878ce8ae5c1ac08faf4225409b1`.
The bridge service reports active/running with zero restarts after final deployment.

- Real installed-addon metadata opened a movie, series and exact series episode.
- A second `flatpak run` exited successfully and preserved the same GUI PID.
- Authenticated WebSocket navigation and KWin focus passed on the TV.
- After deliberately terminating only the verified GUI, a WebSocket episode
  request launched one new UI, waited for its renderer, and selected that episode.
- No-player pause returned `not_playing`; idle snapshots expose no play/pause/seek
  flags. Navigation did not start any production streams.
- A real screenshot shows the series page, correct season and E2 focus. It was
  visually inspected and is kept in ignored `artifacts/stremio-episode-tv.png`.
- Private `stremio_control: true` is persisted only after verifying the installed
  endpoint; future cold starts can distinguish the app's control lease from
  compositor closing animations.

Inventory correction: additional Flatpak entries can be browser subprocesses.
Stremio's extra entry belongs to QtWebEngine; singleton tests inspect the actual
`stremio-tv-native` GUI executable, rather than interpreting entry counts as UIs.

## Build and remaining limits

The TV build reuses its installed FFmpeg/mpv/Mosquitto libraries with matching
pinned public headers, Qt 6.10 SDK and BaseApp development files. The
[official BaseApp manifest](https://raw.githubusercontent.com/flathub/io.qt.qtwebengine.BaseApp/branch/6.10/io.qt.qtwebengine.BaseApp.json)
explains why its development files disappear from installed application packages.
The canonical source includes a user-only incremental build helper; equivalent
build/export/install commands were executed during this phase. Full production
packaging remains the original YAML manifest. No sudo was used.

Actual play/pause/seek/stop and saved resume playback were verified with sample
media in the Linux test container. Production streams and saved watch progress on
the TV were not exercised or changed. An `open`/`resume` acknowledgement is not a
promise of autoplay: addon stream selection may require the remote. IDs outside
the documented bounded Stremio ID format return errors. Library browsing remains
phase 9 work.

The updated HA install archive is prepared and locally tested, but actual HA
installation/end-to-end UI validation still needs access to its configuration
folder. The TV bridge and both application hooks are installed. IPTV and
VacuumTube control remain later phases; their source repositories are ready.
Security checks are functional validation, not an independent penetration test.

## Pull request publication

Application changes are committed as `eb5fdfc` and published in
[Stremio TV PR #35](https://github.com/shreyasajj/stremio-tv/pull/35), ready for review
against `main`. The CI entry point now starts a private session bus when needed,
and the workflow runs the six protocol tests. Its 92 native checks were rerun
successfully through that CI entry point before publication. Unrelated `.vscode`
files remain untracked. GitHub CI and Flatpak builds run on the PR independently.
