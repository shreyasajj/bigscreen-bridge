# Bigscreen Bridge

Phased KDE Plasma 6 / Home Assistant integration. Includes an **app launcher and window controller**, Couchside UI deep links,
and a native Home Assistant media player.
Stremio now supports native playback controls and metadata; Couchside navigation
keeps Music Assistant in charge of playback.

- [Repository implementation map](docs/implementation-map.md): pinned upstream source audit, exact files, phases and gates.
- [Protocol contract](docs/protocol.md): authenticated WebSocket, request IDs and capability rules.
- [Verification record](docs/verification.md): tested behavior and remaining work.

## Current behavior

The user service monitors Plasma 6 KWin windows over the session bus and publishes
active app changes over an authenticated WebSocket. It matches Flatpak desktop IDs,
focuses existing windows, and uses fresh KWin snapshots and verified application
control endpoints for launch decisions. Unpatched apps are not launched when
a Flatpak process is already present. Patched Couchside can start its UI alongside
its independent Sendspin background process. Launch waits for an actual KWin window;
focus waits for KWin to confirm activation. Unsupported content fails before any
app launch. Requests carry IDs and acknowledgements.

Home Assistant connects through a UI config flow and creates one native
`MediaPlayerEntity` for the active app plus a dedicated Stremio player when installed. The player supports source selection,
`play_media` with content type `app`, Couchside UI content types, and browsing
installed applications.
Stremio playback state and metadata come from the native player. Its play, pause,
stop and seek flags are enabled only when supported; other apps report UI activity
as idle/off. No volume controls or duplicate Couchside playback entity are created;
Music Assistant remains the music playback controller.

## Install the KDE bridge

Requirements: Plasma 6 graphical user session, Flatpak, Python >=3.12 with venv
and pip, network access to install Python dependencies. No root access required.
Run from this repository root **on the TV as its desktop user**:

```sh
./scripts/install-tv.sh
systemctl --user status bigscreen-bridge
curl http://127.0.0.1:8765/health
```

Configuration is private at `~/.config/bigscreen-bridge/config.json`. It contains a
persistent device UUID, generated token, name, bind address, port and monitor path.
Installer preserves existing config and restarts the user service on upgrades.
The service starts with the graphical session. User lingering alone cannot provide
a working KWin session before login.

The installer binds port 8765 to LAN interfaces; restrict it to your trusted LAN.
Bearer tokens traverse HTTP in cleartext: use TLS via a reverse proxy, a VPN, or SSH
tunneling on untrusted networks. Change `host` to `127.0.0.1` if using a local proxy.
Browser-origin connections are rejected. Never put the token in a URL or automation.
After changing config: `systemctl --user restart bigscreen-bridge`.

The bridge runs outside Flatpak and does not require broad sandbox overrides.
Future app hooks will need narrow, per-app D-Bus permissions. No overrides have
been applied to your installed applications.

Run live smoke tests with the private token read locally, never printed:

```sh
~/.local/share/bigscreen-bridge/venv/bin/python scripts/smoke-tv.py
# Also activate each existing target window (may affect app pause-on-blur behavior):
~/.local/share/bigscreen-bridge/venv/bin/python scripts/smoke-tv.py --focus
```

The focus test restores the initial app if it belongs to the four target apps.
If another app initially has focus, the last tested target remains focused.

Uninstall the bridge without touching app data:

```sh
systemctl --user disable --now bigscreen-bridge
rm ~/.config/systemd/user/bigscreen-bridge.service
systemctl --user daemon-reload
```

The config and installation stay in place until explicitly removed. The persistent
KWin monitor is unloaded during normal service shutdown; restarting the bridge also
replaces an old monitor after an unexpected crash.

## Install the Home Assistant integration

Copy the whole `custom_components/bigscreen_bridge` directory into your HA
configuration directory, or extract `dist/bigscreen_bridge-0.1.0.zip` there. The
result must be `/config/custom_components/bigscreen_bridge/manifest.json`.
Restart HA after installing the custom integration. Then use **Settings → Devices
& services → Add integration → Bigscreen Bridge**. Enter
`http://192.168.30.153:8765` and the private token from the TV config. An expired
or changed token can be updated through HA's reauthentication flow.

Port 8765 is the bridge address, not Home Assistant's port. During initial checks,
HA responded at `http://192.168.30.3:8123`; the supplied port 8234 refused
connections. Access to that HA installation has not yet been established.

Each bridge's stored UUID identifies its HA device independently of its IP address.
Configure each TV separately. A changed UUID is treated as another device.
The first version supports manual UI connection; zeroconf discovery, app-specific
entities for the other apps, library browsing, and IPTV/YouTube deep links remain later phases.

## Examples that work in this milestone

Use the entity ID assigned by your HA installation:

```yaml
action: media_player.select_source
target:
  entity_id: media_player.media_ser
data:
  source: Stremio TV
```

```yaml
action: media_player.play_media
target:
  entity_id: media_player.media_ser
data:
  media_content_type: app
  media_content_id: flimmer
```

Valid app IDs: `couchside`, `stremio`, `flimmer`, `vacuumtube`.
See [automation examples](examples/automations.yaml).
Channel and YouTube deep links are **not yet enabled**; Stremio navigation is implemented below.
Music Assistant URI navigation is supported through the Couchside extension below. The [contract](docs/protocol.md) records their planned typed interfaces.
Existing Stremio MQTT configuration and messages are untouched; the bridge has no
MQTT transport in this milestone.

## Develop and test

```sh
python3.14 -m venv .venv
.venv/bin/pip install -e '.[test,ha-test]'
.venv/bin/pytest -q
.venv/bin/ruff check bigscreen-bridge custom_components tests
```

HA contract tests use Home Assistant 2026.2.3, the version available from the package
index during this milestone. This does not establish compatibility with the actual
HA host, whose version is unknown. On Python 3.12/Linux, bridge-only testing can use
`.[test]` with `pytest --ignore=tests/test_homeassistant.py`.
Ignored `upstream/` checkouts are source audit references, not installed application
changes. Do not mistake source inspection or simulated protocol tests for live
media playback validation. Application patches will be added in their own phases.

## Couchside phase 5

Implemented directly in `/Users/shre/Documents/projects/couchside` on the existing
`ui-modernize` branch and installed on the TV. Rebuild from that source with:

```sh
./scripts/deploy-couchside.sh /Users/shre/Documents/projects/couchside
```

The patched app is single-instance and exposes a narrow, UI-only D-Bus endpoint.
The bridge can open Now Playing, search or a resolved track, album, artist or
playlist in that instance. If closed, it can launch the UI even while the separate
Sendspin process remains active. `couchside_control: true` is enabled on this TV
only after installation; do not set it for an unpatched app.

HA typed navigation examples (replace URI with a real MA item from your library):

```yaml
action: media_player.play_media
target:
  entity_id: media_player.media_ser
data:
  media_content_type: couchside_album
  media_content_id: library://album/123
```

Use `couchside_now_playing` with `media_content_id: ""`, or `couchside_search` with
a query. Navigation does not play media. Use the existing Music Assistant entity
for playback; the bridge exposes its local MA player ID without adding another
transport controller. IPTV and YouTube content types remain unsupported.

See [phase 5 verification](docs/verification-phase5.md) for live results and limits.


## Canonical app working copies

Source locations are recorded in [config/repositories.json](config/repositories.json).
VacuumTube is now forked at [shreyasajj/VacuumTube](https://github.com/shreyasajj/VacuumTube)
and cloned to `/Users/shre/Documents/projects/VacuumTube`, with a local
`bigscreen-bridge` branch. Its `docs/bigscreen-bridge.md` contains the application
patch plan and Linux build commands. Fork setup passed the 23 existing Node tests;
the VacuumTube bridge hook and Leanback playback tests remain phase 8 work.


## Stremio phase 6

Implemented in `/Users/shre/Documents/projects/stremio-tv` on its local
`bigscreen-bridge` branch and installed on the TV. Native controls include
play/pause/stop and absolute seek, with title, episode, artwork, position, duration
and observed playback state. The main entity follows the active app; a dedicated
Stremio entity remains useful when another app has focus. Unsupported volume and
next/previous controls are not advertised. Existing Stremio MQTT messages remain
unchanged.

```yaml
action: media_player.play_media
target:
  entity_id: media_player.media_ser_stremio_tv
data:
  media_content_type: stremio_episode
  media_content_id: '{"series_id":"tt0944947","video_id":"tt0944947:1:2"}'
```

Use `stremio_movie` or `stremio_series` with a Stremio ID. `extra: {resume: true}`
requests matching stored progress. Detail navigation does not start a stream;
resume can open the existing stream picker and reports that explicitly. Use the
HA entity IDs assigned by your installation.

The user's canonical source can be rebuilt and installed with
`./scripts/deploy-stremio.sh /Users/shre/Documents/projects/stremio-tv`.
This uses an existing 0.8.1 user Flatpak as the media-library baseline, not sudo.
See [phase 6 validation and limits](docs/verification-phase6.md). The HA archive
still needs installation in the actual HA configuration folder.

Flimmer phase 7 adds a native IPTV player and paginated channel browsing. Build and
install the canonical checkout with `scripts/deploy-flimmer.sh ../plasma-tv`.
The bridge detects a running patched app; keep `flimmer_control: true` in its
private runtime configuration to retain cold-launch capability after app exit.
Use `flimmer_channel` in `play_media` with an ID returned by media browsing.

Run `scripts/smoke-flimmer.py` on the TV to test its actual configured source, or
`scripts/fixture-flimmer.py` for a synthetic local MPEG-TS/XMLTV test with an isolated
profile. The fixture restores the normal Flimmer profile and requires ffmpeg.
See [phase 7 verification](docs/verification-phase7.md) for tested capabilities
and remaining provider, artwork and HA installation limitations.

VacuumTube phase 8 adds a dedicated YouTube player with verified video/playlist/
search navigation, real Leanback metadata, playback, seek and shared app volume/
mute. Install the canonical fork with `sh scripts/deploy-vacuumtube.sh ../VacuumTube`.
Retain `vacuumtube_control: true` in the private bridge configuration for cold
launch; running patched instances are also detected automatically. Use typed
`youtube_video`, `youtube_playlist` or `youtube_search` in `play_media`.

`scripts/smoke-vacuumtube.py` tests the native hook in a separate portable profile;
`scripts/smoke-vacuumtube-bridge.py` exercises authenticated Wayland control and
cold launch into the normal profile. Test playback is stopped afterward.

For actual Home Assistant runtime validation, install the `ha-test` extra and run
`.venv/bin/python scripts/smoke-homeassistant.py`. It creates a private temporary HA
configuration, reads this TV's bridge credentials over SSH without printing them,
and checks configuration flow, native entities, real services, availability/
reconnect and unload. It uses loopback port 18123 and never modifies the user's HA
installation. See [phase 8 verification](docs/verification-phase8.md).
