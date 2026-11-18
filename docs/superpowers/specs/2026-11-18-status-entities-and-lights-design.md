# Bridge status and LED lights in Home Assistant

Status: approved 2026-11-18, including effect, colour, brightness and scene control.

## Intent

The user wants to see in Home Assistant what the top of the Bridge Status screen shows,
and the state of the TV's LED lighting (LedFx and HyperHDR), with basic control. All
of it goes through the bridge, so it arrives over the one pairing as part of one
device, with nothing else to set up in HA.

## Part 1: bridge status entities (HA only)

These are entities on the existing TV device, in HA's **Diagnostic** category. They
use the bridge's existing `diagnostics` command, polled every 30 s, plus the pushed
snapshot:

- **Bridge version** (sensor)
- **Started** (timestamp sensor)
- **Health** (binary sensor: problem when KWin isn't ready)
- **Discovery** (sensor: `announced`, `not_announced` or `disabled`)
- **MQTT** (sensor: `online`, `offline` or `not_configured`)
- **Display** (binary sensor)
- **Volume** (sensor, %)
- **Focused app** (sensor)
- **Windows** (sensor)
- **Warnings** (sensor: the count, with the latest message as an attribute)
- **Connected clients** (sensor: how many WebSocket clients are connected; kept at the user's request)

The "Home Assistant clients" tile is dropped, because from inside HA it's always 1.
**Download diagnostics** returns the full diagnostics, with no tokens.

## Part 2: lights through the bridge

### What the bridge reads

A `lights.py` monitor polls every 5 s over localhost HTTP. A service that doesn't
answer is checked through systemd on D-Bus.

**LedFx** (`ledfx_url`, default `http://127.0.0.1:8888`; user unit `ledfx.service`):
- `/api/info` gives the version.
- `/api/virtuals` gives `paused` and, per virtual, `active` and the effect name.
- `/api/devices` gives each device's `online` flag.

**HyperHDR** (`hyperhdr_url`, default `http://127.0.0.1:8090`; system unit
`hyperhdr@<user>.service`), all from `serverinfo` over `/json-rpc`:
- each instance's name and whether it is running;
- the `ALL` component, which is LED output on or off for instance 0;
- the active video grabber;
- the last error.

**Snapshot.** The results go into the snapshot as `lights.ledfx` and `lights.hyperhdr`,
along with whether each is available and its service state. Changes are pushed to
clients like any other state change.

### Commands

All of these are validated, acknowledged, and recorded in diagnostics by name only
(never values).

**LedFx** (HTTP):
- **`ledfx_pause {paused}`:** reads `paused`, then `PUT /api/virtuals` only if the
  state differs, because that call just toggles.
- **`ledfx_light {virtual, on?, effect?, brightness?, color?}`:**
  - `on` sends `PUT /api/virtuals/<id> {"active": on}`, which keeps the effect.
  - `effect` sends `POST /api/virtuals/<id>/effects {"type": effect}` and keeps the
    current brightness.
  - `brightness` (0-1) sets the running effect's `brightness` with
    `PUT …/effects {"config": {...}}`.
  - `color` (`#rrggbb`) depends on the running effect:
    - if its schema has `color`, that key is set;
    - else if it has `gradient`, the gradient becomes one solid colour;
    - otherwise the virtual switches to `singleColor` with that colour.
- **`ledfx_scene {scene, active}`:** `PUT /api/scenes` with `{"id": scene, "action":
  "activate"}` or `"deactivate"`.

**HyperHDR** (JSON socket on port 19444, one session per command: `instance switchTo`
first, then the command):
- **`hyperhdr_light {instance, on?, effect?, color?, brightness?}`:**
  - `on` sends `instance startInstance` or `stopInstance`.
  - `color` `[r, g, b]` sends `color` at priority 50, origin "Bigscreen Bridge".
  - `effect` sends `effect` at priority 50; the special effect `Video` sends `clear`
    for priority 50, which returns the instance to video capture.
  - `brightness` (0-1) sets `adjustment` `scaleOutput`.

Effect names, colours and brightness are checked against the known effect lists and
value ranges before anything is sent.

### In Home Assistant (TV device)

- **LedFx:**
  - one **light** per virtual: on/off, brightness, RGB colour, and the effect list
    (LedFx's effect types by display name);
  - switch **LedFx** (playing or paused);
  - one **scene** per LedFx scene, for activating it;
  - binary sensor **LedFx service**;
  - sensor **LedFx devices online** ("6/7"), with the offline names as an attribute.
- **HyperHDR:**
  - one **light** per instance: on/off (the instance running), brightness, RGB colour,
    and the effect list (HyperHDR's effects plus "Video");
  - binary sensor **HyperHDR service**;
  - binary sensor **HyperHDR output**;
  - sensor **HyperHDR grabber**, with the last error as an attribute.

If an app's service isn't installed, its entities aren't created. If it's installed
but stopped, its entities show as unavailable, except the service sensor.

### On the TV

Bridge Status gets a read-only **Lights** card.

## Constraints

- Localhost only, no credentials. HyperHDR's JSON API answers localhost without a
  token; the bridge never stores one.
- A failing light service never affects the rest of the bridge. Its section reports
  `available: false` and the reason.
- Polling stays light: two small local requests every 5 s.

## Not in scope

- Editing an effect's other settings, beyond brightness and colour.
- HyperHDR's other components (smoothing, HDR), and its per-instance LED layouts.
- Creating, renaming or deleting LedFx scenes.
- Coordinating LedFx and HyperHDR, which share the WLED strips.

## Left to the builder

The exact snapshot field names beyond the above, log wording, and test layout.
