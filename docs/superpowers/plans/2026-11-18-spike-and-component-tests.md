# Spike and Component Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove that a headless Plasma Bigscreen "TV" runs in a GitHub container (the
spike). Then cover every bridge and Home Assistant module's good and bad paths with
mock-based component tests, including every HA control and every Bridge Status key, with
coverage shown in CI.

**Architecture:**
- **The spike:**
  - a KDE neon Docker image (`tests/integration/tv/`) that starts a session bus,
    `kwin_wayland --virtual` and the Bigscreen shell;
  - it installs the bridge with `install.sh` from a locally built release, then opens
    one Flatpak (Couchside);
  - a workflow runs it on a GitHub runner.
- **Python component tests:** added to the existing pytest suite in `tests/`, using the
  existing `bridge`/`coordinator` fixtures and fake buses.
- **QML key tests:** a Qt Quick Test runs inside the same KDE neon base image, against a
  stand-in Status service written with dbus-next.

**Tech Stack:** Python 3.12/3.14, pytest, pytest-asyncio, pytest-aiohttp, pytest-cov,
dbus-next, Home Assistant 2026.11 core classes, Qt 6 Quick Test (`qmltestrunner`), KDE
neon, Docker, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-18-docker-ci-testing-design.md`

**Later plans (not this one):**
- the app prerequisites: the Stremio e2e fix and release, and the VacuumTube Flatpak
  release job;
- the integration stack with HA, Music Assistant, the media server, and each app.

Both are planned after the spike reports, because the stack builds on the image this
plan's Task 1 makes.

## Global Constraints

- **Nothing touches the real TV.** No SSH to `192.168.30.153`, and no `launch.sh`.
- **Never record** the token, MQTT credentials, content IDs, URIs, search text, volume
  levels, window captions or app payloads. Test fixtures use obvious fakes (`"a" * 48`,
  `tt0000000`).
- **Don't install system packages on the Mac.** Docker images and the project `.venv`
  are fine.
- **Work on the `docker-ci-testing` branch;** never commit to `main`.
- **Don't claim something works unless you ran it.** The spike counts only if it runs
  green on a GitHub runner.
- **Integration workflows never block merges.** `integration.yml` is not a required
  check.
- **No fixed coverage percentage gate.** Each module is covered until what's missing is
  only unreachable defensive code.
- **The bridge runs on Python 3.12.** HA tests run on 3.14. The 3.12 CI job runs only the
  files that don't import `custom_components`.

## Review Focus

1. **A test that only tests its mock.** In a bad-path test, the assertion must be about
   what the real code does with the failure (the error code, the entity's availability,
   the state it keeps), not that the fake was called.
2. **`client.py` shows 0% even though the coordinator tests use it.** The likely cause is
   that it's measured under a different import path, so fix the measurement before
   writing tests that duplicate existing ones.
3. **Back on the Status screen calls `Qt.quit()`.** In `qmltestrunner` that can end the
   test run itself, so a passing run must have actually run every test function (count
   them).
4. **Bad-path HA calls must surface `HomeAssistantError`,** not a raw
   `BridgeClientError` or `KeyError`, and must leave the entity's previous state in
   place.
5. **The spike must report the Couchside window through the bridge,** not just
   "something launched". Its check reads the bridge's own window list.

---

### Task 1: Spike — headless Bigscreen TV container on a GitHub runner

This is a gate. If after a real effort the session or the Flatpak window can't be made to
work on `ubuntu-24.04` runners, stop, ledger what failed, and report to the user before
building anything else. Tasks 2–6 don't depend on it, but the next plan does.

**Files:**
- Create: `tests/integration/tv/Dockerfile`: KDE neon user edition base, plus
  `plasma-bigscreen`, `flatpak`, `pipewire`, `wireplumber`, `dbus-x11`,
  `python3-venv`, `jq`, `curl`. A non-root user `tv` with UID 1000.
- Create: `tests/integration/tv/session.sh`: started as `tv`. It starts the session bus
  and `XDG_RUNTIME_DIR`, then PipeWire with a null sink. Then it runs `kwin_wayland
  --virtual --width 1920 --height 1080` with `QT_QUICK_BACKEND=software` and
  `LIBGL_ALWAYS_SOFTWARE=1`, then the Bigscreen shell (`plasmashell -p
  org.kde.plasma.mycroft.bigscreen`, or whatever neon's Bigscreen session file
  `Exec`s). It writes `/run/tv-ready` once KWin answers `org.kde.KWin` on the session
  bus.
- Create: `tests/integration/build-release.sh`: builds `dist/` the way `release.yml` does
  (`git archive`, stamped `pyproject.toml`, `SHA256SUMS`, `install.sh`, `releases.json`
  in the shape `BIGSCREEN_RELEASES_API` expects). The URLs are `file://`.
- Create: `tests/integration/spike.sh`. On the host, it:
  1. builds the image;
  2. runs it with `--privileged`;
  3. waits for `/run/tv-ready`;
  4. installs the bridge via `install.sh` with `BIGSCREEN_RELEASES_API` pointing at the
     local release;
  5. adds the Couchside Flatpak remote and installs the app `--user`;
  6. opens it with `flatpak run`.

  It succeeds only when the bridge's own window list, from a `busctl --user call
  org.bigscreen.Bridge /org/bigscreen/Bridge org.bigscreen.Bridge.Status Status` piped
  through `jq`, contains Couchside's Flatpak desktop ID. Then it reports `SPIKE OK`. On
  failure it dumps the `journalctl --user` tails and the container logs.
- Create: `.github/workflows/integration.yml`:
  - Triggers: `workflow_dispatch`, `schedule` (`0 4 * * *`), `push` to tags `v*`, and
    `pull_request` with `paths: [tests/integration/**, .github/workflows/integration.yml]`.
    The path-filtered PR trigger lets this branch run it before the file reaches `main`.
  - One job runs `sh tests/integration/spike.sh`.
  - Caches the Flatpak runtimes with `actions/cache`, keyed on the Couchside runtime
    version.

**Interfaces:**
- Produces: the image `bigscreen-tv:test` and the `session.sh` readiness contract
  (`/run/tv-ready`), which the next plan's compose stack and Task 6's QML job build on.
  Task 6 uses the same Dockerfile up to the `plasma` stage, so give it a named stage:
  `FROM … AS plasma`.

- [ ] **Step 1:** Write `build-release.sh`. Check it locally:
  `sh tests/integration/build-release.sh && ls dist/`.
  Expected: `bigscreen-bridge-0.2.2.tar.gz install.sh SHA256SUMS releases.json`, and
  `cd dist && shasum -a 256 -c SHA256SUMS` passes.
- [ ] **Step 2:** Write the Dockerfile and `session.sh`. Build them locally, accepting
  amd64 emulation on the Mac:
  `docker build --platform linux/amd64 -t bigscreen-tv:test tests/integration/tv`.
  Expected: the build succeeds. Running the session locally is optional, because
  emulated KWin may be too slow. The runner is the judge.
- [ ] **Step 3:** Write `spike.sh` and `integration.yml`, then commit and push the branch.
  Open a draft PR titled "Component tests and Docker integration spike" so the
  path-filtered trigger runs.

  Run: `gh pr checks --watch` (or `gh run watch`).
  Expected: the integration job prints `SPIKE OK`.
- [ ] **Step 4:** Iterate on the runner until `SPIKE OK`, or until the session or the
  Flatpak window clearly can't work. In that case stop and report, with the evidence:
  the last log tails, and what was tried.
- [ ] **Step 5:** Commit:
  `git commit -m "test: Headless Bigscreen TV container spike"`.
  Ledger the job's run URL and how long it took.

---

### Task 2: Coverage in CI, and HA client and setup paths

**Files:**
- Modify: `pyproject.toml`:
  - add `pytest-cov>=6` to `test`;
  - add `[tool.coverage.run] source = ["bigscreen_bridge", "custom_components.bigscreen_bridge"]`
    and `[tool.coverage.report] show_missing = true, skip_covered = true`.
- Modify: `.github/workflows/ci.yml`:
  - the 3.14 job runs `pytest -q --cov --cov-report=term --cov-report=xml`, then writes
    the `coverage report` table to `$GITHUB_STEP_SUMMARY`;
  - the 3.12 job adds `--cov` the same way, for its subset.
- Create: `tests/test_ha_client.py`.
- Modify: `tests/test_homeassistant.py`, for `__init__` setup, reload and unload.

**Interfaces:**
- Consumes: the `bridge` fixture (`tests/conftest.py`), returning `(Bridge, aiohttp
  TestClient)`, and the `coordinator` fixture in `test_homeassistant.py`.
- Produces: `--cov` runs that later tasks read to find their gaps.
  Command: `.venv/bin/python -m pytest -q --cov --cov-report=term-missing`.

- [ ] **Step 1:** Add the coverage config and run it.

  Run: `.venv/bin/python -m pytest -q --cov --cov-report=term-missing 2>&1 | tail -45`.
  Expected: 274 passed.
  - If `client.py` is still at 0%, find out why. Compare
    `custom_components.bigscreen_bridge.client.__file__` with the reported path, fix the
    measurement (the `source` / `pythonpath` / import order in `conftest.py`), and
    re-run. Expected: `client.py` above 0%.
  - Ledger the per-file baseline.
- [ ] **Step 2: Write the failing or uncovered client tests** in `tests/test_ha_client.py`,
  against the real bridge app from the `bridge` fixture unless a test says otherwise:
  - `test_pairing_code_normalises_and_rejects`: `pairing_code(" 123 456 ")` normalises
    to the bridge's format, and letters raise `ValueError`.
  - `test_validate_url_rejects_paths_credentials_and_queries`: one parametrized case
    each, plus a valid `http://host:8765` that passes.
  - `test_pair_wrong_code_is_auth_error`: `pair(session, url, "000000")` raises
    `BridgeAuthError`, with the bridge's error code.
  - `test_pair_unexpected_reply`: a stub aiohttp app returning `200 text/plain` raises
    `BridgeClientError("Unexpected pairing reply")`.
  - `test_connect_bad_token_is_auth_error`, and
    `test_connect_unsupported_protocol`: a stub WS server announcing protocol 99 raises
    `"Unsupported bridge protocol"`.
  - `test_command_while_disconnected`: after `close()`, `command("play")` raises
    `"Bridge is disconnected"`.
  - `test_command_error_carries_code`: an unknown app command gives
    `BridgeClientError.code == "unsupported"`.
  - `test_malformed_frame_disconnects`: the stub server sends `[]`, then
    `on_disconnect` is called once and pending commands fail rather than hang (a
    `wait_for` of 2 s).
- [ ] **Step 3:** Run `.venv/bin/python -m pytest -q tests/test_ha_client.py`.
  Expected: any failing test reveals a real defect. Fix it in
  `../ha-bigscreen-bridge/custom_components/bigscreen_bridge/client.py` on a same-named
  `docker-ci-testing` branch in that repo (CI picks same-named branches), then re-run.
  Expected: all pass.
- [ ] **Step 4:** In `test_homeassistant.py`, add
  `test_setup_entry_forwards_platforms_and_unload_closes`. Use a real `HomeAssistant`
  plus a `MockConfigEntry`-like `SimpleNamespace`, and patch
  `hass.config_entries.async_forward_entry_setups` and `async_unload_platforms` with
  `AsyncMock`. Assert that the platform list matches `__init__.PLATFORMS`, that unload
  closes the coordinator's client, and that a setup whose first refresh raises
  `BridgeAuthError` surfaces `ConfigEntryAuthFailed`.
- [ ] **Step 5:** Run the full suite with `--cov`. Expected: all pass, and `client.py` and
  `__init__.py` now show only unreachable lines missing. Commit (bridge repo, and the
  integration repo if it changed):
  `git commit -m "test: Coverage in CI; HA client and setup error paths"`.

---

### Task 3: Every Home Assistant control, good and refused

**Files:**
- Create: `tests/test_ha_controls.py`. Move nothing out of `test_homeassistant.py`;
  import its `coordinator` fixture by moving that fixture into `tests/conftest.py`,
  guarded so the 3.12 job still collects (only define it when `homeassistant` imports).

**Interfaces:**
- Consumes: `coordinator`, `bridge` and `RecordingLights` (move it to `conftest.py`
  as well), plus the `music_assistant(hass, monkeypatch, player_id, state)` helper
  (move it to `conftest.py`).

- [ ] **Step 1: Write the media-player matrix.** One parametrized test,
  `test_every_media_player_control`, over (player ∈ active, couchside, stremio, flimmer,
  vacuumtube) × (action ∈ play, pause, stop, seek(30), volume_set(0.4), volume_mute(True),
  next, previous, select_source, turn_on, turn_off, play_media for every content type the
  bridge advertises for that app, and `browse_media` root plus one child).

  For each pair:
  - if the feature flag is in `supported_features`, call it and assert the bridge
    received the expected command (via the hooks' recorded calls or `b.wm.focused`);
  - if not, assert that `async_<action>` raises `HomeAssistantError` or
    `NotImplementedError`, and that no command reached the bridge.

  The expected command table is written in the test as a dict, not derived from the
  code under test.
- [ ] **Step 2: Write the refused paths:**
  - `test_refused_command_raises_and_keeps_state`: make the bridge hook raise
    `BridgeError("unsupported", …)`. The HA call raises `HomeAssistantError` with that
    message, and `player.state` and `media_title` are unchanged.
  - `test_controls_while_bridge_down`: close the client. Every action raises
    `HomeAssistantError`, and `available` is False.
  - `test_couchside_forward_when_ma_player_unavailable`: MA in state `unavailable`
    means no forwarding, and `play` raises `HomeAssistantError` rather than calling
    `media_player.media_play`.
- [ ] **Step 3: Write the lights, switch and scene matrix:**
  - `test_every_light_control`: on, off, brightness 1 and 255, `rgb_color`, `effect`
    (every listed effect, plus `Video` for HyperHDR), on each LedFx virtual and
    HyperHDR instance in `RecordingLights`. Assert the exact recorded `execute(command,
    request)`.
  - `test_light_controls_refused`: `RecordingLights` raising `invalid_request` and
    `unsupported` gives `HomeAssistantError`.
  - `test_switch_and_scene_controls`: pause switch on and off; scene activate; and the
    same with the service stopped (the entity is unavailable, and the call raises).
- [ ] **Step 4:** Run `.venv/bin/python -m pytest -q tests/test_ha_controls.py --cov`.
  Expected: every parametrized case runs. Fix any real defect in the integration repo,
  same branch as Task 2, with this test as the RED. Then the full suite passes.
  `media_player.py`, `light.py`, `switch.py` and `scene.py` miss only unreachable lines.
- [ ] **Step 5:** Commit:
  `git commit -m "test: Press every Home Assistant control, good and refused"`.

---

### Task 4: Bridge D-Bus edges — KWin, MPRIS, runtime_io

**Files:**
- Modify: `tests/test_kwin.py`, `tests/test_sessions.py` (or create `tests/test_mpris.py`).
- Create: `tests/test_runtime_io.py`.

**Interfaces:**
- Consumes: the fake-bus pattern in `tests/test_kwin.py` (it records
  `(path, member)` and returns canned bodies).

- [ ] **Step 1: Write the tests:**
  - **kwin:**
    - `test_monitor_restarts_when_kwin_reappears`: `owner_changed` with a new owner
      reruns the monitor script;
    - `test_report_windows_ignores_malformed_payload`: `ReportWindows("not json")`
      keeps the previous windows and logs;
    - `test_refresh_times_out_without_reply`: `refresh()` returns within its timeout
      and keeps the last windows;
    - `test_focus_unknown_window_reports_not_found`: `FocusResult(id, False)` makes
      `focus()` raise `BridgeError` with code `not_found`;
    - `test_close_unloads_scripts`.
  - **mpris:**
    - `test_media_state_maps_every_status`: `Playing`, `Paused`, `Stopped` and a
      missing one;
    - `test_registry_follows_name_owner_changes`: a player appears, then vanishes, and
      `changed` fires both times;
    - `test_properties_changed_updates_position`;
    - `test_control_on_missing_player_raises`;
    - `test_read_tolerates_dbus_error`: the player is skipped, not crashed.
  - **runtime_io:**
    - `test_unwrap_nested_variants`;
    - `test_call_raises_bridge_error_on_dbus_error`;
    - `test_call_times_out`;
    - `test_run_nonzero_exit_and_timeout`: real `/bin/sh -c 'exit 3'` and
      `sleep 10` with `timeout=0.1`;
    - `test_desktop_environment_unknown`.
- [ ] **Step 2:** Run them. Expected: new tests either pass (the behaviour already exists)
  or fail by exposing a defect. For each defect, fix the minimal code in the module and
  ledger it.
- [ ] **Step 3:** Run the full suite with `--cov`. Expected: all pass; `kwin.py`,
  `mpris.py` and `runtime_io.py` miss only unreachable lines.
- [ ] **Step 4:** Commit:
  `git commit -m "test: KWin, MPRIS and D-Bus helper error paths"`.

---

### Task 5: Bridge service edges — audio, power, open_link, update runner, server startup

**Files:**
- Modify: `tests/test_power_audio.py`, `tests/test_deep_links.py` (`open_link`),
  `tests/test_update.py`, `tests/test_bridge.py`.

**Interfaces:**
- Consumes: the `runner=` injection of `AudioManager` and `PowerManager`; the existing
  `test_update.py` fakes; and `server.serve(...)` / `main()`.

- [ ] **Step 1: Write the tests:**
  - **audio:**
    - `test_process_app_id_from_flatpak_cgroup` and `…_missing_proc`;
    - `test_wpctl_missing_falls_back_to_pactl`;
    - `test_refresh_when_both_tools_fail_reports_unavailable`;
    - `test_execute_rejects_out_of_range_level` (−0.1, 1.5).
  - **power:**
    - `test_parse_dpms_unknown`;
    - `test_blocked_inhibitors_filters_operation`;
    - `test_display_command_failure_raises`;
    - `test_check_system_denied`;
    - `test_lock_without_screensaver`.
  - **open_link:**
    - `test_main_opens_valid_uri` (stubbed `open_uri`, exit 0);
    - `test_main_rejects_unknown_scheme` (exit non-zero and nothing called);
    - `test_open_uri_bridge_unreachable` (a message and exit non-zero).
  - **update:** `test_main_check_reports_newer` and `test_main_check_current`,
    `test_run_installs_then_health_ok`, `test_run_health_fails_rolls_back`,
    `test_run_skips_failed_version` and `test_run_waits_while_playing`. Use the existing
    fakes for the installer, health and sleep. These cover `update.py` lines 170–285.
  - **server:**
    - `test_serve_starts_and_stops_cleanly`: run `serve()` with every D-Bus and
      network dependency stubbed via its parameters or monkeypatch, then cancel. Assert
      that Lights, the recorder and the KWin monitor are closed.
    - `test_main_bad_config_exits_with_message`.
- [ ] **Step 2:** Run them, and fix any defect they expose (ledger each one).
- [ ] **Step 3:** Run the full suite with `--cov`. Expected: all pass, and no bridge module
  misses anything but unreachable defensive lines. Ledger the final per-file table.
- [ ] **Step 4:** Commit:
  `git commit -m "test: Audio, power, open-link, updater and startup error paths"`.

---

### Task 6: Every Bridge Status key, in a Qt Quick Test

**Files:**
- Create: `bigscreen-status/tests/tst_keys.qml`: `TestCase` cases that create
  `main.qml` through `Qt.createComponent`, wait for the window to be active, and press
  keys with `keyClick`.
- Create: `bigscreen-status/tests/fake_status.py`: a dbus-next session service that
  owns `org.bigscreen.Bridge` with `Status()` (returning `fixtures/healthy.json`
  wrapped as `{snapshot, diagnostics, pairing}`), `StatusChanged`, and
  `Pairing.StartPairing()`. It appends `StartPairing` to a file named by `$CALLS`. Flags:
  `--no-status` (owns the name but exports no Status interface) and `--absent`
  (doesn't start).
- Create: `bigscreen-status/tests/run.sh`: starts `dbus-run-session`, then the fake
  (per mode), then
  `QT_QPA_PLATFORM=offscreen QML_XHR_ALLOW_FILE_READ=1 qmltestrunner -input tst_keys.qml`.
  It runs once per mode (`live`, `no-status`, `absent`, `fixtures`), with the mode passed
  to QML through `BIGSCREEN_STATUS_TEST_MODE`. It fails if the reported total of passed
  tests differs from the expected count for that mode.
- Create: `bigscreen-status/tests/Dockerfile`: `FROM` the Task 1 image's `plasma`
  stage, plus `qml6-module-qttest` / `qt6-declarative-dev-tools` and
  `python3-dbus-next`.
- Modify: `.github/workflows/ci.yml`: a `status-screen` job builds that image (with a
  cached layer) and runs `run.sh`. It's a required check like the others, since it
  doesn't depend on apps.
- Modify: `bigscreen-status/main.qml` only if a test proves a defect, or if `Qt.quit()`
  can't be observed. In that case route Back through a `function close()` that calls
  `Qt.quit()`, so the test can spy on a `closing` signal. Ledger it as a ruling.

**Interfaces:**
- Consumes: `main.qml`'s `ourArgs` contract. In live mode there are no args; fixture
  mode uses `-- --fixtures healthy`. If `Qt.application.arguments` can't carry these
  under `qmltestrunner`, add an env fallback (`BIGSCREEN_STATUS_ARGS`) read in `ourArgs`.
  Ledger it.
- Consumes: the `plasma` stage from Task 1.

- [ ] **Step 1: Write the harness proof.** `tst_keys.qml` has one case,
  `test_window_loads`, that asserts the window exists and `sections.length === 10`. Run
  `run.sh` in the image locally (`docker build --platform linux/amd64 …`) or on CI.
  Expected: 1 passed. If `Qt.quit()` or the arguments block it, apply the rulings above.
- [ ] **Step 2: Write the key tests:**
  - **live:**
    - `test_live_pill`: it shows `LIVE`;
    - `test_down_up_move_cards`: ▼ ×3 gives `list.currentIndex === 3`, then ▲ gives 2;
    - `test_ok_opens_sources`: OK sets `currentItem.item.expanded`;
    - `test_back_closes_sources_then_window`: the first Back gives `expanded` false and
      the window still open; the second Back fires the quit or close;
    - `test_ok_on_pairing_calls_start_pairing`: move to card 1, press OK, and `$CALLS`
      contains `StartPairing` (read it with XHR);
    - `test_left_right_ignored_live`: `current` is unchanged;
    - `test_background_pauses_reads`: set the window inactive (another `Window` takes
      activation). The fake's `Status` call count doesn't grow over 1.5 s with
      `StatusChanged` emitted. Reactivating triggers one read within 1 s.
  - **absent:** `test_no_bridge`: it shows `NO BRIDGE`, only one section, and ▼ stays
    at 0.
  - **no-status:** `test_no_status_interface`: `Ui.linkError` names the D-Bus
    unknown-interface error, and the pill shows `NO BRIDGE`.
  - **fixtures:** `test_left_right_cycle_scenarios`: ▶ advances `current` and wraps at
    the end, ◀ wraps to the last, and `loadError` is empty for every scenario.
- [ ] **Step 3:** Run `run.sh` for all four modes. Expected: each mode reports its full
  count passed. Fix any defect in the QML with the failing test as the RED.
- [ ] **Step 4:** Add the `status-screen` CI job, push, and run
  `gh pr checks --watch`. Expected: `status-screen` passes on the runner.
- [ ] **Step 5:** Commit:
  `git commit -m "test: Press every Bridge Status key against a stand-in bridge"`.

---

## After the last task

- Final whole-branch review; mark the draft PR ready; the CI checks pass.
- Report the before and after coverage tables, the spike result with its run URL and
  duration, and every ruling.
- Then write the next plan: the prerequisites and the integration stack. If the spike
  failed, ask the user how to proceed first.
