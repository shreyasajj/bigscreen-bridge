# Added phases — power, global media, persistent receiver and wake-to-play

These extend the core work; they do not replace phases 9/10. User requirements
were supplied on 2026-11-16 in the conversation and attachment
`/Users/shre/.codex/attachments/12d96437-268f-4159-b808-f5333f56e51a/Pasted text.txt`.

## Repository map

| Area | Files | Verification gate |
|---|---|---|
| Universal links (in progress before new prompt) | `bigscreen_bridge/{deep_links,open_link,config}.py`, server, HA player, installer, tests/docs | Same link through CLI, KDE handler and actual HA; reject malformed/cross-app links before side effects. |
| KDE power/display | New `bigscreen_bridge/power.py`, server, protocol/config and tests | Introspect actual Plasma 6/PowerDevil/KScreen/logind; advertise authorized capabilities; brief reversible DPMS test with guaranteed restore. |
| Global sessions and pause ledger | New `bigscreen_bridge/{sessions,mpris,audio}.py`, adapters/server/tests | Track sessions independently of focus; canonical player identities; explicit pause only; record confirmed bridge-caused pauses and preserve manual pauses. |
| Persistent receiver health | New `bigscreen_bridge/receiver.py`, existing Couchside service/client hooks as necessary | Reuse existing Sendspin autostart/singleton; independently verify process health and actual MA availability; avoid duplicating credentials. |
| Priority policy | Session coordinator + configurable policies, HA sensors/options | Serialized/debounced transitions; video overrides only the associated local music player; default no automatic music resume. |
| Smart sequences | Power/session coordinator, event contract | Pause/stop acknowledgements, explicit save confirmations only, flush/publish intent before accepted power request. Respect authorization/inhibitors. |
| HA entities/actions | New `button.py`, `binary_sensor.py`, `sensor.py`, `services.py`, options flow/strings | Capability-driven entities; distinguish PC/display/physical TV; verify actual HA services and dashboard feature schemas. |
| Wake/music/CEC orchestration | New HA wake/command store helpers, options flow/services | Persist request on HA side; configured local MA entity only; use existing HA CEC entities/actions; verified WOL only; bounded stages and explicit confirmation/errors. |

## Initial machine audit

SSH user has no sudo. `kscreen-doctor`, `ethtool`, `pactl`, `wpctl`, and
`cec-client` are present; presence does not establish working capability. PowerDevil
exports Plasma session methods. A physical Ethernet MAC was read, but neither WOL
configuration nor firmware wake behavior has yet been verified. No new power or
wake command has been executed at this point.

## Safety and truthfulness

- Existing authenticated API and typed adapters remain the command boundary.
- No arbitrary executable paths, shell commands or client-supplied D-Bus methods.
- System actions need logind/Polkit capability checks and must honor inhibitors.
- Acceptance is acknowledged before a sleep/poweroff disconnect; completion is a
  separate observed event/state, never inferred from an invocation alone.
- Screen DPMS is distinct from physical-TV CEC and PC suspend/shutdown.
- Wake is not advertised from merely finding a MAC address or ethtool flag.
- HA uses the existing Music Assistant and CEC integrations; no competing CEC
  controller and no duplicate authoritative music entity/credentials.
- Actual destructive power transitions require a recoverable test path. If wake
  cannot be verified, implement and test the safe failure/acceptance paths and
  document the hardware limitation instead of taking the machine offline blindly.
