# Phase 5 — Couchside UI control and Music Assistant ownership

Date: 2026-11-16. Direct source implementation is in the user's canonical checkout
`/Users/shre/Documents/projects/couchside`, branch `ui-modernize`, starting from
`afd45818f60d76f7c0068d612d2a52e679fabddc`. Existing commits and unrelated untracked
files were preserved. Stremio and Flimmer working copies were inspected but not
modified in this phase.

## Implemented and installed

- A strict session-bus JSON endpoint with request IDs and acknowledgements.
- Primary UI ownership via a D-Bus name lease. Secondary patched launches request
  focus in that UI and exit; they do not initialize another controller or daemon.
- Now Playing, search and exact track/album/artist/playlist UI navigation.
- Exact item resolution through Couchside's existing authenticated MA client.
- A themed detail page with title, artist, artwork and explicit user actions.
- UI and MA readiness are distinct; content needing MA waits for its connection.
- Local Sendspin/MA identity mapping. A missing Sendspin ID cannot match an
  unrelated speaker. Credentials and private identity material are not exported.
- UI-only capabilities; Music Assistant retains all playback controls. No second
  music playback entity or duplicate playback feature flags are introduced in HA.
- The bridge's typed `open` request and HA `couchside_*` content types.
- A persisted opt-in for the verified patched Couchside, allowing UI launch while
  the separate Sendspin Flatpak is running.
- Fresh KWin window snapshots before lifecycle decisions, filtering deleted
  compositor windows and checking the IPC name across close/start races.

Couchside was built and installed as the existing user Flatpak from a separate TV
source copy of the canonical checkout. Existing TV `~/couchside` and `~/cs-exp`
trees were not overwritten. Only the experimental UI process was stopped during
the migration; its separate Sendspin helper was left running.

See [file-level implementation map](implementation-map.md#canonical-local-repositories-user-directed-source-change)
and the patch export under `patches/couchside/`. No source was committed or pushed.

## Verification

- **434 Couchside tests passed** against the canonical local repository, including
  new protocol, validation, real-controller search mapping, item navigation,
  missing-identity and disconnected-MA tests.
- **41 bridge/HA tests passed**, including native HA typed navigation, ownership,
  invalid IDs, background Sendspin launch, closing-window handling and MA readiness.
- Ruff passed for bridge, HA, scripts and new Couchside Python files. The D-Bus
  service's string signature annotations are intentionally excluded from F821.
- Live D-Bus requests opened Now Playing, empty/nonempty search and real library
  tracks, albums, artists and playlists on the actual installed UI.
- Malformed URI kinds and unsupported playback commands returned explicit errors.
- A secondary `flatpak run` returned success, preserved the same UI process and
  kept the last navigation. The normal focus path uses KWin for Wayland activation.
- The lifecycle smoke terminated only the identified UI, then requested Now Playing
  and search through the authenticated WebSocket API. A new single UI appeared;
  the original Sendspin helper and daemon PID/start-time pairs were unchanged.
- A TV screenshot of a resolved album page was captured and visually inspected;
  artwork, album title, artist and focused action button rendered successfully.
  The screenshot stays local in ignored `artifacts/couchside-album-tv.png`.

Live testing found and corrected a controller row-shape mistake in the new hook,
and a close/relaunch race where a compositor window outlived its control endpoint.
The automated suite includes regression coverage for both. Shell-launched GUI tests
explicitly inherit the desktop environment; SSH alone does not supply Wayland's
environment variables.

## Remaining limits

Sendspin was **Stopped** before these tests. Keeping its helper and daemon alive
is verified; uninterrupted audible music through UI closure is not. No tests
started or altered music playback. A separate playing-audio acceptance test is
still required for that claim.

`view` records the latest bridge navigation; manual remote navigation does not yet
update that field. The item page exposes the resolved item and existing MA play/
queue actions, not full album track lists or artist discographies. Music library
browsing and read-only media metadata on the main bridge entity remain future work.

The UI D-Bus endpoint trusts the logged-in Unix user. Flatpak permission is limited
to ownership of `io.github.shreyasajj.Couchside.Control`; no broad bus override was
added. Network access still uses the existing bridge bearer token and LAN/TLS/VPN
boundary. HA was tested against real core classes locally but is not installed or
tested on the actual HA host; configuration-folder access is still missing.

At the end of phase 5, Stremio, Flimmer and VacuumTube content hooks were unimplemented.
Stremio is now covered by [phase 6](verification-phase6.md). Re-audit the
user's local Stremio/Flimmer branches before editing: they differ from the original
upstream audit. Existing Stremio MQTT messages and configuration were not changed.

## Pull request publication

Application-side bridge changes are published in
[Couchside PR #19](https://github.com/shreyasajj/couchside/pull/19), ready for review
against `main`. An isolated `bigscreen-bridge` worktree cherry-picks the bridge
commit and adapts the new detail page to main's existing palette. The user's
ongoing `ui-modernize` worktree and unrelated changes are preserved. This PR branch
passes 403 tests; its palette adaptation has not been redeployed to the TV.
GitHub CI runs separately from the earlier live TV validation recorded above.
