# Status Entities and Lights Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (chosen: native, inline). Steps use `- [ ]`.

**Goal:** Show the bridge's own status, and LedFx and HyperHDR status with control, in Home Assistant through the bridge.

**Architecture:**
- **Bridge:** a new `lights.py` reads LedFx (HTTP) and HyperHDR (`serverinfo` over HTTP; commands over its JSON socket) every 5 s. It reads their systemd units' `ActiveState` over D-Bus, and exposes `snapshot["lights"]` plus four commands.
- **HA integration:** adds sensor, binary_sensor, switch, light and scene platforms on the TV device. Status entities poll the existing `diagnostics` command every 30 s; light entities follow the pushed snapshot.

**Tech Stack:** Python 3.12 asyncio, aiohttp (already a dependency), dbus-next, HA 2026.11 entity platforms.

**Spec:** `docs/superpowers/specs/2026-11-18-status-entities-and-lights-design.md`

## Global Constraints
- **Config keys** (all optional):

  | Key | Default |
  | --- | --- |
  | `ledfx_url` | `http://127.0.0.1:8888` |
  | `hyperhdr_url` | `http://127.0.0.1:8090` |
  | `hyperhdr_socket` | `127.0.0.1:19444` |
  | `ledfx_unit` | `ledfx.service` (user bus) |
  | `hyperhdr_unit` | `hyperhdr@<user>.service` (system bus) |
  | `lights` | `true`; `false` disables the module |

- **Timing:** poll every 5 s, 3 s timeout per request.
- **HyperHDR commands:** priority 50, origin `Bigscreen Bridge`. The special effect name is `Video` (clears priority 50).
- **Validation:** brightness is 0-1; colours are `#rrggbb` (LedFx) or `[r, g, b]` 0-255 (HyperHDR); effect names must be in the reported lists.
- **Diagnostics:** command names only, never values.
- **Isolation:** a failing light service never raises into the observe loop. Its section becomes `{"available": false, "reason": <fixed string>}`.

## Review Focus
1. **LedFx up but HyperHDR down** (or the reverse): one section is unavailable and the other keeps working. The test is in Task 1.
2. **Pause toggle when already in the wanted state:** no PUT is sent. The test is in Task 1.
3. **Colour on an effect with neither `color` nor `gradient`:** switches to `singleColor`. The test is in Task 1.
4. **HyperHDR instance index out of range, or an unknown effect:** `invalid_request`, nothing sent. The test is in Task 1.
5. **HA when the bridge reports no `lights`** (an older bridge): no light entities are created and nothing errors. The test is in Task 4.

---

### Task 1: `lights.py`
**Files:** create `bigscreen-bridge/src/bigscreen_bridge/lights.py` and `tests/test_lights.py`.

**Produces:**
- `class Lights(config, session_bus, system_bus, user)`
- `async refresh() -> bool` (True when the state changed)
- `state -> dict` (`{"ledfx": {...}, "hyperhdr": {...}}`)
- `async execute(command: str, request: dict) -> dict`
- `COMMANDS = ("ledfx_pause", "ledfx_light", "ledfx_scene", "hyperhdr_light")`

**Snapshot shapes:**
- **`ledfx`:** `available`, `reason`, `service`, `version`, `paused`, `effects` (list of `{type, name}`), `scenes` (`[{id, name, active}]`), `virtuals` (`[{id, name, active, effect, effect_name, brightness, color}]`), `devices` (`{total, online, offline: [names]}`).
- **`hyperhdr`:** `available`, `reason`, `service`, `output`, `grabber`, `last_error`, `effects` (list of names), `instances` (`[{id, name, running}]`).

- [ ] **Tests first**, against an aiohttp fake LedFx, an aiohttp fake HyperHDR JSON-RPC and an asyncio fake JSON socket (newline-delimited). Cover:
  - state shapes, and down services (Review Focus 1);
  - the pause no-op (2);
  - colour fallback to `singleColor` (3);
  - validation (4);
  - HyperHDR socket order: `switchTo`, then the command;
  - scene activate and deactivate.
- [ ] Run, and watch them fail.
- [ ] Implement. Then run `pytest tests/test_lights.py` and expect PASS.
- [ ] Commit `feat: Read and control LedFx and HyperHDR`.

### Task 2: Wire into the server
**Files:** modify `server.py`, `diagnostics.py` (COMMANDS) and `config.py` (if keys are checked); add tests to `tests/test_bridge.py` or `tests/test_lights.py`.

- [ ] **Test:** with `bridge.lights` set to a fake, `snapshot()["lights"]` is present. `execute` routes the four commands, and rejects extra keys with `invalid_request`. The diagnostics command ring records the command name.
- [ ] **Implement:**
  - `bridge.lights` is created in `serve()` unless `lights` is false.
  - `observe()` refreshes lights every `LIGHTS_POLL = 5`.
  - The commands then call `notify()`.
- [ ] Full suite, then commit `feat: Lights in the snapshot and four light commands`.

### Task 3: Bridge Status Lights card
**Files:** create `bigscreen-status/components/LightsSection.qml`; modify `main.qml` (sections) and `tools/make_fixtures.py` (a `lights` key in fixtures).

- [ ] **Card contents:**
  - LedFx: service, playing or paused, devices online, active virtual effects.
  - HyperHDR: service, output, grabber, instances.
- [ ] Regenerate the fixtures. `tests/test_status_fixtures.py` must pass.
- [ ] Commit `feat: Lights card on the status screen`.

### Task 4: HA integration, in `ha-bigscreen-bridge`
**Files:**
- create `diagnostics.py`, `sensor.py`, `binary_sensor.py`, `switch.py`, `light.py` and `scene.py`;
- modify `__init__.py` (platforms) and `coordinator.py` (diagnostics polling every 30 s into `coordinator.diagnostics`);
- tests go in the bridge repo's `tests/test_homeassistant.py`.

- [ ] **Tests:**
  - The status sensors read the right values from a real bridge's diagnostics.
  - The light entities map their state, and `turn_on` (brightness/rgb/effect) and `turn_off` send the right command.
  - The LedFx switch and scenes send theirs.
  - No `lights` in the snapshot means no light entities (Review Focus 5).
- [ ] Implement, run the full suite, commit `feat: Bridge status, LedFx and HyperHDR entities`. Bump `manifest.json` to 0.4.0.

### Task 5: Ship and verify on the TV
- [ ] Bump the bridge to 0.2.0. Open PRs, wait for green CI, merge, tag `v0.2.0` and integration `v0.4.0`.
- [ ] **Bridge restart:** record the PID, running apps and a screenshot first, and say so first. Then `bigscreen-bridge-update --now`, which exercises the updater for real.
- [ ] **Verify:**
  - `snapshot.lights` matches the LedFx and HyperHDR APIs.
  - `ledfx_pause` round trip, restored afterwards.
  - One LedFx virtual brightness change, restored.
  - The status screen card, via a screenshot.
