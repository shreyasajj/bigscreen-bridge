# Verification record — first milestone

Date: 2026-11-16. This records the initial milestone. See [phase 5](verification-phase5.md)
for Couchside implementation and [phase 6](verification-phase6.md) for current Stremio tests. All ten phases are not complete.

| Stage | Status | Verified evidence | Still required |
|---|---|---|---|
| 1 — audit | Complete for initial map | Four upstream checkouts inspected at pinned commits; TV Flatpak inventory, session-bus APIs, user services and network endpoints checked. | Deeper app-specific behavior tests during each adapter phase. |
| 2 — contract | Initial UI contract implemented | Authenticated snapshots, request IDs/results, capability declaration, app browsing, explicit unsupported errors. | Extend schema for real media, libraries, ownership and local app hooks. |
| 3 — bridge/KWin | Implemented and live tested | Installed/enabled user service at `192.168.30.153:8765`; real Wayland KWin identity/focus for all four apps; Flimmer cold launch and repeat focus; request acknowledgements; no added instances when focusing running apps; health reachable across LAN. | Reboot/session logout, KWin restart recovery and destructive fault tests. |
| 4 — HA player | Implemented; local HA core contract tests pass | Actual HA 2026.2.3 classes import and execute: native player features, app browsing, source selection, app play_media, config-flow auth/device ID, reauth identity rejection, disconnect availability and changed-device refusal. | Install into actual HA, confirm its version, test UI/restart/service calls there. Manual connection only; discovery not implemented. |
| 5 — Couchside/MA | Implemented in [phase 5](verification-phase5.md) | Source confirms MA controls, Sendspin identity matching and separate daemon supervisor. Live window focus only. | UI IPC/single instance/navigation, MA ownership mapping and uninterrupted audio tests. |
| 6 — Stremio | Planned | Source confirms MQTT topics/state/commands and Qt/libmpv architecture. Live window focus only. | New IPC, movie/episode/resume hooks, native metadata/commands, legacy MQTT regression. |
| 7 — Flimmer | Planned | Source confirms channel UUID/index APIs, now/next data and QML playback/timeshift. Live launch/focus only. | External control, stable M3U ID strategy, channel browsing/EPG/playback tests. |
| 8 — VacuumTube | Planned | Source confirms Electron single-instance/deeplink forwarding and Leanback readiness queue. Live focus only. | Safe IPC, actual video/playlist/search acceptance, metadata and supported player commands on Leanback. |
| 9 — full browsing/deep links | Planned | Installed app browsing and `app` IDs work. Unsupported content is rejected without launching/focusing. | Real libraries/channels and app content types; no media deep link is advertised yet. |
| 10 — end to end | Partial | Bridge unit/protocol tests and live TV smoke tests. | Real HA end to end, all media adapters, multiple physical devices and reboot matrix. |

## Test results

- `pytest -q`: **23 passed**, including tests against actual Home Assistant classes.
  Config flow rendering/entry registration is mocked; the protocol server and
  client connection are real aiohttp connections. This is not a running HA frontend.
- `ruff check bigscreen-bridge custom_components tests`: passed.
- Live TV smoke: auth failure without token, initial snapshot, app browsing,
  unsupported content rejection, focus for Couchside/Stremio/Flimmer/VacuumTube,
  KWin active-window confirmation and unchanged Flatpak instance counts.
- Separate live Flimmer test: absent window → launch → real window activation;
  second focus acknowledged. Flimmer remained running afterward.
- Final service check: `ActiveState=active`, `SubState=running`, `NRestarts=0` since
  final deployment. Config directory mode 0700; token config mode 0600.
- Bridge health returns HTTP 200 and protocol 1 from the workstation.
- HA HTTP endpoint at 8123 returns 200 from workstation; 8234 refuses connections.

Tests emit deprecation warnings from dbus-next's typing decorator and HA's aiohttp
application subclass. No test failures are suppressed.

## Live environment and boundaries

Multiple Flatpak entries were present before this work. Entry counts alone do not
prove duplicate UIs: later phases identified Couchside UI/Sendspin processes and
Stremio UI/QtWebEngine processes. Unrelated processes were left intact. Existing historical
Couchside unit failures and session-migration failure were left untouched.
The bridge uses one existing matching window; later app IPC hooks must enforce
single-instance UI ownership even across manual launcher invocations.

The first focus smoke began while another/non-target window had focus, and ended
on VacuumTube. Subsequent tests restored the initial target app, VacuumTube.
App-specific pause-on-blur behavior can occur during focus testing. No claim is
made that background playback continuity has been verified.

The bridge refuses to start a second Flatpak if an existing process has no usable
window; this deliberately returns a readiness error until app-specific hooks can
separate a background audio daemon from its UI. Existing window detection verifies
window readiness, not app library/authentication or media readiness.

Installed-app inventory is read at bridge startup. Restart after installing or
removing applications. KWin/session-bus loss now clears readiness and exits for
systemd restart, but restarting the compositor was not exercised on the live TV.
Authentication and browser-Origin rejection are tested; this is not an independent
security audit. Plain HTTP requires a trusted LAN or a TLS/VPN boundary.

At the initial milestone no source patches had been applied to the four installed apps;
phase 5 now installs patched Couchside. No MQTT settings,
HA config files or existing automations have been modified. MQTT authentication,
actual HA version/install path, and access to `/config` remain unknown.

## Next acceptance gate

Install the packaged custom integration into HA, connect it to the running bridge,
and test source selection/app browsing through native HA services. Then implement
Couchside UI IPC with MA as playback owner before proceeding through Stremio,
Flimmer and VacuumTube. File-level changes and remaining gates are in the
[implementation map](implementation-map.md).

API references: [Plasma 6 KWin scripting](https://develop.kde.org/docs/plasma/kwin/api/),
[HA media player entities](https://developers.home-assistant.io/docs/core/entity/media-player/).
