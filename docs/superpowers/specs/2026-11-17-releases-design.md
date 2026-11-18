# Releases, one-command install and auto-update

Status: approved in conversation, 2026-11-17.

## Intent

Installing the bridge on a TV should be one command, and a TV should keep itself
current without anyone logging in. Tagging a version should publish it. A
Dependabot security fix should reach TVs that opt in, as a pre-release, with no
human step.

## Scope

- `bigscreen-bridge` (made public for this, so TVs need no GitHub token).
- `ha-bigscreen-bridge`: a release-on-tag workflow. HACS already offers updates
  from GitHub releases, so nothing has to run inside Home Assistant.
- Not the four apps. Couchside already auto-merges Dependabot and publishes a
  signed Flatpak on every merge; the others keep their own release paths.

## Approaches considered

- **Public repo, releases from GitHub (picked).** `curl | sh` install, updates need
  no secrets.
- Private repo with a read-only token stored on every TV: one more secret per TV
  to rotate. Rejected by the user.

## Design

### CI (`.github/workflows/ci.yml`, bridge)

- Python 3.12, the TV's version: the bridge tests without `test_homeassistant.py`,
  plus ruff.
- Python 3.14 with the `ha-test` extra: the full suite. It checks out
  `ha-bigscreen-bridge` next to the bridge. It uses the HA branch with the same
  name when one exists, so stacked pairs such as `bridge-diagnostics` with
  `pairing-code` test together; otherwise it uses `main`.
- shellcheck on the shell scripts.
- Callable through `workflow_call`, so a release re-runs exactly these checks.

### Releases (`release.yml`, bridge)

- **A pushed tag `vX.Y.Z`** must equal `pyproject.toml`'s version, or the release
  fails. The checks run, then a GitHub release is published with generated notes
  and three assets:
  - `bigscreen-bridge-X.Y.Z.tar.gz` (`git archive` of the tag)
  - `install.sh`
  - `SHA256SUMS`
- A tag with a pre-release suffix (`v0.3.1rc1`) is published as a pre-release.
- **`workflow_dispatch` with `prerelease: true`** (used by the security flow) picks
  the next version itself: the next patch after the latest stable release,
  `rcN`, where N is one more than the existing pre-releases of that patch. It
  stamps that version into the archive's `pyproject.toml`, tags the commit and
  publishes a pre-release. Nothing is committed to `main`.

### Dependabot security fixes (bridge)

- `dependabot.yml` has **no pip version updates**, so every pip PR from Dependabot
  is a security update. GitHub Actions get weekly version updates.
- `dependabot-security.yml` runs on `workflow_run` when CI completes successfully
  for a pull request. The PR must be:
  - authored by `dependabot[bot]`
  - from a `dependabot/pip/` branch
  - open, with every check green

  Such a PR is squash-merged, then `release.yml` is dispatched with
  `prerelease: true`. A `workflow_run` and a `workflow_dispatch` both start even
  though GitHub's built-in token does the merge, which a plain push or tag would
  not.
- GitHub Actions bumps auto-merge on green CI, with no release: nothing shipped
  changes (my call).
- Repository settings: Dependabot alerts and security updates on.

### Install (`install.sh`, bridge)

```sh
curl -fsSL https://github.com/shreyasajj/bigscreen-bridge/releases/latest/download/install.sh | sh
# options: --prerelease (follow pre-releases), --version X.Y.Z, --no-auto-update
```

The installer:
1. Downloads the release archive and `SHA256SUMS`, and verifies the checksum.
2. Unpacks to `~/.local/share/bigscreen-bridge/releases/<version>`.
3. Runs that release's `scripts/install-tv.sh`.

`install-tv.sh` (also used from a checkout) changes as follows:
- **Venvs:** builds `venvs/<version>` and points the `venv` symlink at it, so
  `ExecStart` is unchanged. A pre-existing real `venv` directory is migrated. The
  last two venvs are kept for rollback.
- **Status screen:** installs the Bridge Status screen and its launcher entry
  locally, instead of only from a Mac with `launch.sh`.
- **Updater:** installs `bigscreen-bridge-update.service` and its timer, and
  writes `update` settings into `config.json` (`{"channel": "stable", "auto": true}`
  by default) without touching other keys.

### Auto-update (`bigscreen-bridge-update`, bridge)

- **Timer:** daily at 03:00 local time, `RandomizedDelaySec=1h`, `Persistent=true`.
- **Command:** stdlib Python in the package, so it runs from the venv.
  - Lists releases from the GitHub API, skipping drafts, and pre-releases unless
    the channel is `prerelease`.
  - Compares versions (`X.Y.Z` and `X.Y.ZrcN`) with the installed package
    version.
  - Does nothing when the installed version is current.
- **Never during playback:** before installing it reads `Status()` on the session
  bus. While any app's `state` is `playing` it waits 10 minutes and checks again,
  for up to 4 hours, then gives up until the next day.
- **Install:** the same steps as `install.sh`, which restarts the bridge.
- **Rollback:** after the restart it expects `/health` within 60 seconds. Without
  it, it repoints `venv` at the previous version, restarts, and logs a warning.
- **Other modes:** `--check` reports what it would do and changes nothing; `--now`
  skips the playback wait. Every run logs one line to the journal.

### Home Assistant integration (`ha-bigscreen-bridge`)

- `release.yml` on tag `vX.Y.Z`: `manifest.json` `version` must equal the tag.
  hassfest and HACS validation run, then a GitHub release is published with
  generated notes; it is a pre-release if the tag has a suffix. HACS picks it up
  from there.
- `dependabot.yml`: GitHub Actions only. The integration has no Python
  requirements.

## Constraints and non-goals

- **Trust:** archives are trusted over HTTPS from github.com. The checksum catches
  a broken download; it isn't a signature. Signing is a possible later step.
- **Restarts:** an update briefly drops Home Assistant's connection. This is why
  updates happen at night and never during playback.
- **Releases are versions, not builds of `main`:** nothing is released without a
  tag or a security merge.
- **No sudo:** the TV setup still needs no root.

## Left to the builder

Exact log wording, version-comparison edge cases beyond `X.Y.Z[rcN]`, and test
layout.
