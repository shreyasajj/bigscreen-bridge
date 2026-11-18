# End-to-end and component testing in Docker CI

Status: approved 2026-11-18.

## Intent

Twice in one night the bridge passed every test and still broke on the TV. A fake
KWin didn't number scripts like the real one, and a fake LedFx didn't drop effects
like the real one. The user wants:
- an **integration** test in CI that runs the real system in Docker: Plasma Bigscreen,
  the real apps, the bridge and Home Assistant, end to end in both directions;
- **component** tests with mocks that cover both good and bad paths, and press every
  control.

## Decisions made with the user

- **Integration tests: good path only, real apps in Docker.** They prove the whole
  chain works.
  - **Home Assistant → bridge → app:** each command reaches the app and does what it
    should.
  - **App → bridge → Home Assistant:** what happens in an app shows up on the HA entity.
- **Component tests: mocks, good and bad paths.** No real apps are needed; every
  module's errors are covered here.
- **Buttons:** every Home Assistant control, and every remote key on the Bridge Status
  screen. The apps' own on-screen buttons are out of scope; they belong to each app's
  own tests.
- **Apps come from released Flatpaks, as on the TV:**
  - **Couchside:** its Flatpak repo, which publishes every merge to `main`.
  - **Stremio TV:** a release with Flatpak bundles, once #36 is merged and released.
  - **VacuumTube:** a release with a Flatpak bundle, once #1 (merged) is released. The
    fork's release workflow needs a Flatpak job first.
  - **Flimmer:** left out for now, because the user hasn't decided how it is released.
    It's covered by component tests with mocks, and the stack keeps a place for it.

## Approaches considered

- **A. One privileged "TV" container (chosen).** It runs a real Plasma Bigscreen
  session on a headless KWin, with real Flatpak apps and the bridge installed by
  `install.sh`. Home Assistant, Music Assistant and a media server run as separate
  containers. It's the closest to the TV, and it tests the Flatpak IDs, sandbox and
  bus names the bridge depends on.
- **B. Native app builds without Flatpak.** Rejected: the bus names, sandbox and window
  identities would differ from the TV, which is the class of difference that hid
  tonight's bugs.
- **Building the apps from their branches in the pipeline.** Rejected for released
  builds: it's slower, and it tests builds the TV doesn't run.

## Design

### Layers

| Layer | Runs | Paths | Where |
| --- | --- | --- | --- |
| Unit | functions and classes, with stand-ins | good and bad | `ci.yml`, every PR, under a minute |
| Component | each module against mocks of its neighbours, including every HA control and every status-screen key | good and bad | `ci.yml`, every PR |
| Integration | the real stack in Docker | good only | `integration.yml`: nightly, on release tags, on demand; not a required check |

### Component tests (mocks)

- **Coverage:** `pytest-cov` measures the bridge and the integration. CI shows the
  numbers, and every module gets tests for its error paths until its uncovered lines
  are only unreachable defensive code. No fixed percentage gate, at the builder's
  discretion.
- **Every Home Assistant control:** each entity action on every platform runs against
  the in-process bridge with stand-in hooks and lights:
  - media player: play, pause, stop, seek, volume, mute, source, `play_media` for every
    content type, `browse_media`, turn on and off;
  - lights, the switch, scenes;
  - Couchside forwarded to Music Assistant.

  Each action is checked for the right command, and for the right error when the bridge
  refuses it.
- **Every Bridge Status key:** a Qt Quick Test (`qmltestrunner`) loads `main.qml`
  against:
  - fixtures, for ◀ ▶ scenarios;
  - a stand-in `org.bigscreen.Bridge` Status service on a private session bus
    (dbus-next), for live mode.

  It presses ▲ ▼, OK (data sources), OK on Pairing (`StartPairing` called), Back
  (closes the panel, then the window) and ◀ ▶. It also checks the bad cases: no bridge
  ("Bridge not running"), a bridge without Status ("no Status interface"), and pausing
  in the background. This runs in a Qt container image in CI.

### Integration stack (`tests/integration/`, docker compose)

- **`tv` (privileged):**
  - KDE neon with the Plasma Bigscreen session on `kwin_wayland --virtual` (software
    rendering), a session bus, PipeWire (dummy sink) and Flatpak.
  - The apps are installed `--user` from Flathub runtimes and their releases. Runtimes
    and apps are cached between runs.
  - The bridge is installed by `install.sh` from the commit under test, built as a
    local release archive.
- **`homeassistant`:** the official image at the version the TV's HA runs (2026.11).
  The test onboards it through its API and adds the Music Assistant integration and
  this integration. Pairing uses the code read from the bridge's `Status()`, so
  pairing is tested too.
- **`music-assistant`:** the official server image, with a local-files provider
  holding two short generated tracks. Couchside connects to it.
- **`media`:** serves Stremio's existing mock addon and a short video.

### Integration scenarios (good path only)

For each app (Couchside, Stremio TV, VacuumTube):

1. **Launch from HA:** `select_source` or `play_media app`. The app's window appears
   and the bridge reports it running and ready.
2. **Deep link from HA:**
   - Couchside: open an album, which plays through Music Assistant.
   - Stremio: open the mock movie.
   - VacuumTube: open a fixed public video ID.
3. **Control from HA:** play, pause, seek where supported; Couchside goes through
   Music Assistant.
4. **App to HA:** an action inside the app, without HA (a key press into the app, or
   the media ending), changes the HA entity's state, title or position within 5 s,
   through the app's `StateChanged` signal.
5. **Focus from HA without content** keeps the app's state, and no second instance
   starts.

There are also bridge-level scenarios:
- the Bridge Status screen shows LIVE;
- the status entities in HA carry the right values;
- `bigscreen-bridge-update --check` runs against a local release.

LedFx and HyperHDR in the stack are a later phase, not in this design.

### Prerequisites

1. **Stremio TV:** fix the end-to-end CI failure. Its test image starts from an
   unpinned `debian:trixie`, and it has failed on `main` since 2026-11-18 without a
   code change, probably from a new Qt WebEngine. Then merge #36 and tag a release
   with Flatpak bundles.
2. **VacuumTube fork:** add a release job that builds the bridge `app.asar` over the
   Flathub base into `VacuumTube-bridge-x86_64.flatpak`, as `flatpak/install-bridge.sh`
   does locally. Then publish a release.

### Order

0. **Spike:** prove the hardest part on a GitHub runner first. That's a Plasma
   Bigscreen session on a headless KWin in a privileged container, with one real
   Flatpak window and the bridge reporting it. If it can't be made to work, come back
   to the user before building more.
1. Component tests and coverage: Python, then the QML key tests.
2. The two prerequisites.
3. The integration stack with Home Assistant and the bridge, and pairing.
4. Apps, one at a time: Stremio, then Couchside with Music Assistant, then VacuumTube.

## Constraints

- **Nothing touches the real TV.**
- **The integration workflow never blocks merges.** It reports, and a failure opens or
  updates one issue.
- **No secrets.** Home Assistant and the bridge are created fresh each run.
- **Runner limits:** 14 GB of disk, so runtimes are cached and pruned. The target is
  under 45 minutes per run.

## Non-goals

- The apps' own on-screen buttons.
- Real LED hardware, GPU playback, and audio output quality.
- Flimmer in the integration stack until its release question is settled.

## Risks

- **Headless Bigscreen in a container is unproven,** hence the spike first.
- **VacuumTube loads the real YouTube TV site,** so its scenarios need the network and
  may be flaky. Those steps get one retry, and their failures are reported separately.
- **Software rendering is slow.** Timeouts are generous, but state checks still poll
  rather than sleep.

## Left to the builder

The base image tags, compose layout, fixture media generation, the QML test harness
details, and the exact polling helpers.
