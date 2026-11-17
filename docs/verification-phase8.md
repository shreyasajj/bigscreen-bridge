# Phase 8 verification — VacuumTube and native HA runtime

Canonical fork: `/Users/shre/Documents/projects/VacuumTube`, local branch
`bigscreen-bridge`, commit `f5a5449`, based on upstream
`0c7df3a1e2411b430e416a0b8f3c872bb1f8fb4d`. This phase committed locally;
no additional PR was published. The earlier Couchside and Stremio PRs both have
passing checks, including Stremio's x86_64 and ARM Flatpak builds.

## Implemented and tested in the real Leanback application

- Private Linux Unix socket, fixed schemas, request IDs, bounded frames,
  owner/mode checks, bounded replay/pending state, serialized mutations.
- Existing Electron per-profile single-instance lock, existing-window focus,
  renderer/frame trust checks, reload/loss invalidation and freshness watchdog.
- Video and playlist navigation through the actual `h5vcc` listener.
- Search through the actual Leanback command resolver, with confirmation of the
  public query in its search request. A normal results URL did not work and is not
  used by the runtime search path.
- Bounded retry of startup links that Leanback can discard while its initial
  route settles. Success requires observing the requested content/view, not just
  calling a listener; failure returns `navigation_failed`.
- Real video ID, title, position, duration, state and thumbnail. The title field
  from `getVideoData()` was empty, so the implementation observes public title/ID
  fields in responses Leanback already parses, with a bounded read-only cache.
- Actual pause, resume, stop and absolute seek through the existing player APIs.
- Volume/mute through VacuumTube's existing authoritative controller. A raw
  player volume write would be overwritten by its 100 ms update loop. Tests
  checked both controller state and actual player output volume, and restored
  the previous volume and mute values.

Video used: `M7lc1UVf-VE` (the actual reported title was “YouTube Developers Live:
Embedded Web Player Customization”). Playlist: `PLBCF2DAC6FFB574DE`. Search:
`KDE Bigscreen`. These were exercised against the actual YouTube TV page, not a
mock renderer. The native smoke test used a separate portable guest profile.

The authenticated WebSocket/KWin test verified preserved GUI PID on focus,
video selection, replay acknowledgement, pushed metadata, playback/seek/volume/
mute, confirmed search, and **cold-launch playlist delivery to a single normal-
profile Electron instance**. Test playback was stopped and Couchside focus restored.

## Native Home Assistant verification

`scripts/smoke-homeassistant.py` booted actual Home Assistant **2026.2.3** with
matching frontend **20260128.6**, a private temporary configuration and loopback
HTTP port 18123, connected to the real TV bridge. It verified:

- UI configuration-flow backend rejects an invalid token and creates a valid entry.
- Duplicate pairing aborts for the existing device UUID.
- Four real native MediaPlayerEntity registrations: active app, Stremio, Flimmer,
  YouTube. No duplicate Couchside/Music Assistant playback entity.
- Actual HA `select_source`, typed `play_media`, pause/play/seek, volume/mute,
  `browse_media` response serialization and typed search service calls.
- Pushed title, ID, duration, playback and navigation attributes.
- Unavailability followed by automatic reconnection after restarting only the
  bridge user service; the native application kept playing the same video.
- Integration unload closes the connection; test playback stopped, Couchside
  focus restored, temporary HA configuration deleted.

This is **not an installation into the user's HA instance**. Access to that
instance's configuration folder is still unavailable. Browser rendering of the HA
configuration form was not tested; the actual flow backend and services were.

## Tests, build and security

**37 Node tests pass**; **96 bridge/HA tests pass**; Ruff and whitespace checks
pass. Node tests cover fixed schemas, bounded private socket transport, symlink
rejection, renderer trust, replay, pending serialization, stale state, player
capabilities, public metadata matching, delayed navigation and volume validation.

The Linux development archive reuses the installed matching app version and
production dependencies, adding no runtime dependency. The deploy helper was run
successfully against the TV. A user-level Flatpak from the local
`bigscreen-vacuumtube` repository shadows the system installation, which remains
available for rollback. SDK/runtime: freedesktop 26.08. Tested installed commit:
`95294e65d597fa5055bcefec761c8835a3121ff00e61b2eca836c3a22762a2e6`.
Original system commit:
`4613b89bb9fe66e69087f2719bb0df23149d9544f6345456b2f5f771f6f42dd3`.

Private configuration backups are under
`~/.cache/bigscreen-backups/vacuumtube-phase8-*`. No cookies, account tokens,
response bodies or stream URLs are exported by the control interface. The new
Flatpak grant exposes only the private runtime socket directory; it does not
expose the external bridge's bearer-token configuration. No upstream update or
release was published.

## Remaining scope

YouTube library/subscription/playlist enumeration, next/previous/repeat, and other
unimplemented player features remain unadvertised. The tested Leanback interfaces
can change on YouTube's side; freshness and navigation confirmation fail explicitly
when they stop working. This phase did not test every account, ad, live stream or
possible embedded-frame layout.

Flimmer provider artwork remains omitted for credential safety, and its original
Stalker source still needs its own working configuration. Optional bridge MQTT
transport, universal link parsing and broader multi-device/restart validation are
later work. Stremio's existing MQTT implementation remains unchanged.
