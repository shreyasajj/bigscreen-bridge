# Build Bigscreen Bridge — Native Home Assistant Integration

Build a complete KDE Plasma Bigscreen integration for Home Assistant based on these four applications:

- Music: https://github.com/shreyasajj/couchside
- Movies/TV: https://github.com/shreyasajj/stremio-tv
- IPTV: https://github.com/jclsn/flimmer
- YouTube: https://github.com/shy1132/VacuumTube

## Goal

Provide full two-way integration between Home Assistant and KDE neon Plasma Bigscreen, including native Home Assistant media players, application focus management, media metadata, remote playback control, and deep linking to content inside existing application instances.

## Required architecture

Create two main projects:

1. `bigscreen-bridge`: a KDE neon user service.
2. `custom_components/bigscreen_bridge`: a Home Assistant custom integration.

Prefer an authenticated local WebSocket API for structured communication, commands, state subscriptions and media browsing. Allow optional MQTT transport for compatibility with existing automations and Stremio TV's current MQTT implementation.

The Home Assistant integration must implement proper MediaPlayerEntity objects and should not rely on MQTT Discovery to create media players.

## Application-specific requirements

### Couchside

Couchside already integrates with Music Assistant and Sendspin.

Investigate its Music Assistant client and existing player/queue APIs.

Use the Music Assistant integration as the authoritative playback controller when available.

Implement a Couchside-specific hook for:

- Focus existing window.
- Launch Couchside when necessary.
- Open the Now Playing screen.
- Navigate to tracks, albums, artists and playlists.
- Open search.
- Keep music playback active when the Couchside interface is closed.
- Avoid duplicate Music Assistant playback entities.

### Stremio TV

Investigate existing MQTT Home Assistant integration.

Maintain its current MQTT messages for backward compatibility.

Add:

- Native media player representation.
- Open movie by Stremio-compatible ID.
- Open series and episode.
- Resume stored playback where supported.
- Focus an existing window without restarting.
- Report current title, episode, poster, playback position, duration and state.
- Integrate with the existing Qt/libmpv player.

### Flimmer

Investigate Qt/QML playback and channel-selection code.

Add a local application control hook:

- Get current channel.
- List channels and stable IDs.
- Select channel by ID.
- Report EPG current programme and next programme.
- Report playing/paused state.
- Reuse existing instance.
- Expose IPTV channels through Home Assistant media browsing.

Support TVHeadend channel IDs and M3U channels using an appropriate stable identifier strategy.

### VacuumTube

Investigate Electron main process, existing URL handling and renderer navigation.

Add a safe local IPC interface for:

- Open YouTube video ID.
- Open playlist.
- Open search where feasible.
- Focus running instance.
- Avoid launching duplicate instances.
- Report current video title, ID, position, duration, playback state and thumbnail.
- Provide supported player commands.

Do not assume renderer navigation or playback metadata extraction will work without testing against the actual YouTube Leanback implementation.

## Home Assistant integration

Create a UI configuration flow that discovers or connects to the KDE Bigscreen bridge.

Provide:

- A main media player for the active application.
- Individual app media players when meaningful.
- App selection through source selection.
- `play_media` with typed content IDs.
- `browse_media` for available app libraries and channels.
- Playback state updates.
- Track/video artwork.
- Position and duration.
- Volume and seek where supported.
- Launch, focus and deep-link actions.
- Device availability.
- Multiple-device support.

Do not expose unsupported media player features.

Avoid duplicate Couchside playback controls when the Music Assistant integration already owns the same player.

## App lifecycle

Every application must support these rules:

- Focus without content: preserve current state.
- Focus with content: navigate within the running instance.
- Not running: launch and deliver requested content after readiness.
- Already running: do not launch another instance unnecessarily.
- Unsupported deep link: report error rather than silently reopening the app.
- Commands must include request IDs and return acknowledgements.

## KDE integration

Use Plasma 6 KWin APIs to monitor active windows and activate existing windows under Wayland.

Implement reliable app identity matching, including Flatpak desktop IDs.

Use a systemd user service for the bridge.

Account for Flatpak sandbox restrictions and required D-Bus permissions.

## Deliverables

- Complete KDE bridge.
- Complete Home Assistant custom integration.
- Application-specific adapters.
- Necessary source-code changes or patches for the four applications.
- Configuration flow.
- Media browsing implementation.
- Security validation.
- Tests and install instructions.
- Example deep links.
- Example Home Assistant automations.

## Development sequence

1. Audit each application's actual code, APIs and capabilities.
2. Document an interface contract shared by all adapters.
3. Implement the KDE bridge and window manager.
4. Implement the Home Assistant integration and one working media player.
5. Integrate Couchside/Music Assistant.
6. Extend Stremio TV.
7. Add Flimmer channel control.
8. Add VacuumTube navigation.
9. Implement media browsing and universal deep links.
10. Complete end-to-end tests.

For every stage, specify which repository files need modification and which capabilities have been verified.

Start by producing a repository-by-repository implementation map. Do not invent existing APIs or claim that unimplemented features already work.