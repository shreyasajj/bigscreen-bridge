"""`bigscreen-bridge-update`: install a newer GitHub release, never during playback.

Run daily by `bigscreen-bridge-update.timer`. Stable releases only, unless
`config.json` has `"update": {"channel": "prerelease"}`; `"auto": false` only
reports. Before installing it reads the bridge's `Status()` on the session bus and
waits while anything is playing (every 10 minutes, for up to 4 hours). After the
install restarts the bridge it waits for `/health`; if that never comes it points
`venv` back at the previous version and restarts again. Stdlib only, apart from
dbus-next, which the bridge already depends on. The token is never read or logged.
"""

import argparse
import asyncio
import contextlib
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = "shreyasajj/bigscreen-bridge"
RELEASES = f"https://api.github.com/repos/{REPO}/releases?per_page=30"
WAIT_SECONDS = 600
MAX_WAITS = 24  # 24 x 10 minutes = 4 hours
HEALTH_SECONDS = 60
ROOT = Path.home() / ".local/share/bigscreen-bridge"
VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(?:rc(\d+))?")


class UpdateError(Exception):
    pass


def parse_version(text):
    """`0.3.1` -> (0, 3, 1, 1, 0); `0.3.1rc2` -> (0, 3, 1, 0, 2): a pre-release sorts before its final."""
    match = VERSION.fullmatch(text or "")
    if not match:
        return None
    major, minor, patch, rc = match.groups()
    return int(major), int(minor), int(patch), 0 if rc else 1, int(rc or 0)


def pick_release(releases, channel, installed):
    """The newest published release on the channel that is newer than `installed`."""
    current = parse_version(installed) or (-1,)
    best = None
    for release in releases:
        version = parse_version(release.get("tag_name"))
        if (
            version is None
            or release.get("draft")
            or (release.get("prerelease") and channel != "prerelease")
            or version <= current
        ):
            continue
        if best is None or version > best[0]:
            best = (version, release)
    return best and best[1]


def verify_and_extract(archive, sums, name, dest):
    """Check `archive` against its `SHA256SUMS` line, then unpack it (without its top directory) to `dest`."""
    wanted = next(
        (
            line.split()[0]
            for line in sums.splitlines()
            if len(line.split()) == 2 and line.split()[1].lstrip("*") == name
        ),
        None,
    )
    actual = hashlib.sha256(Path(archive).read_bytes()).hexdigest()
    if wanted is None or wanted.lower() != actual:
        raise UpdateError(f"Checksum mismatch for {name}")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=dest.parent, prefix=".unpack-"))
    try:
        with tarfile.open(archive) as tar:
            for member in tar.getmembers():
                parts = Path(member.name).parts
                if (
                    member.name.startswith("/")
                    or ".." in parts
                    or not (member.isfile() or member.isdir())
                    or len(parts) < 1
                ):
                    raise UpdateError(f"Refusing unsafe archive member in {name}")
                member.name = str(Path(*parts[1:])) if len(parts) > 1 else "."
            tar.extractall(staging, filter="data")
        if dest.exists():
            shutil.rmtree(dest)
        staging.rename(dest)
    except (tarfile.TarError, OSError) as exc:
        raise UpdateError(f"Could not unpack {name}: {type(exc).__name__}") from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if dest.parent.exists() and not any(dest.parent.iterdir()):
            dest.parent.rmdir()
    return dest


def is_playing(status):
    snapshot = status.get("snapshot") or {}
    return any(
        entry.get("state") == "playing"
        for entry in (snapshot.get("apps") or []) + (snapshot.get("sessions") or [])
    )


def _settings(config_path):
    try:
        update = json.loads(Path(config_path).read_text()).get("update") or {}
    except (OSError, ValueError):
        update = {}
    return {
        "channel": "prerelease" if update.get("channel") == "prerelease" else "stable",
        "auto": update.get("auto", True) is not False,
    }


def _relink(root, target):
    link = Path(root) / "venv"
    temporary = link.with_name(".venv-rollback")
    if temporary.is_symlink():
        temporary.unlink()
    temporary.symlink_to(target)
    os.replace(temporary, link)


def run(
    config_path,
    *,
    installed,
    root,
    fetch,
    read_status,
    install,
    health,
    restart,
    sleep,
    log,
    check=False,
    now=False,
):
    settings = _settings(config_path)
    try:
        release = pick_release(fetch(), settings["channel"], installed)
    except UpdateError as exc:
        log(f"Update check failed: {exc}")
        return 0
    if release is None:
        log(f"Up to date ({installed}, {settings['channel']} channel)")
        return 0
    version = release["tag_name"].lstrip("v")
    state = Path(root) / "update-state.json"
    try:
        failed = json.loads(state.read_text()).get("failed")
    except (OSError, ValueError, AttributeError):
        failed = None
    if failed == version:
        log(f"Skipping {version}: it failed before; waiting for a newer release")
        return 0
    if check or not settings["auto"]:
        reason = "check only" if check else "automatic updates are off"
        log(f"Update available: {installed} -> {version} ({reason})")
        return 0
    if not now:
        for attempt in range(MAX_WAITS + 1):
            try:
                playing = is_playing(read_status())
            except Exception:  # no bridge on the bus: nothing to interrupt
                playing = False
            if not playing:
                break
            if attempt == MAX_WAITS:
                hours = MAX_WAITS * WAIT_SECONDS // 3600
                log(f"Update {version} postponed: still playing after {hours} hours")
                return 0
            sleep(WAIT_SECONDS)
    link = Path(root) / "venv"
    previous = os.readlink(link) if link.is_symlink() else None
    try:
        install(release)
        if health():
            state.unlink(missing_ok=True)
            log(f"Updated {installed} -> {version}")
            return 0
        problem = "the bridge did not become healthy"
    except UpdateError as exc:
        problem = str(exc)
    except Exception as exc:  # anything else after the install has begun must still roll back
        problem = f"unexpected {type(exc).__name__}"
    with contextlib.suppress(OSError):
        state.write_text(json.dumps({"failed": version}))
    if previous and (not link.is_symlink() or os.readlink(link) != previous):
        _relink(root, previous)
        restart()
        log(f"Update to {version} failed ({problem}); rolled back to {installed}")
    else:
        log(f"Update to {version} failed ({problem}); nothing was changed")
    return 1


# -- the real side effects ---------------------------------------------------------


def _get(url, accept="application/vnd.github+json"):
    request = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "bigscreen-bridge-update"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            raise UpdateError("GitHub rate limit reached; trying again next time") from exc
        raise UpdateError(f"GitHub answered HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError(f"GitHub unreachable ({type(exc).__name__})") from exc


def fetch_releases():
    try:
        releases = json.loads(_get(RELEASES))
    except ValueError as exc:
        raise UpdateError("Unreadable release list") from exc
    return releases if isinstance(releases, list) else []


def read_status():
    from dbus_next import BusType
    from dbus_next.aio import MessageBus

    async def call():
        bus = await MessageBus(bus_type=BusType.SESSION).connect()
        try:
            node = await bus.introspect("org.bigscreen.Bridge", "/org/bigscreen/Bridge")
            proxy = bus.get_proxy_object("org.bigscreen.Bridge", "/org/bigscreen/Bridge", node)
            return json.loads(await proxy.get_interface("org.bigscreen.Bridge.Status").call_status())
        finally:
            bus.disconnect()

    return asyncio.run(asyncio.wait_for(call(), 10))


def make_install(root):
    def install(release):
        version = release["tag_name"].lstrip("v")
        assets = {a.get("name"): a.get("browser_download_url") for a in release.get("assets", [])}
        name = f"bigscreen-bridge-{version}.tar.gz"
        if name not in assets or "SHA256SUMS" not in assets:
            raise UpdateError(f"Release {version} has no {name} or SHA256SUMS")
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / name
            archive.write_bytes(_get(assets[name], "application/octet-stream"))
            sums = _get(assets["SHA256SUMS"], "application/octet-stream").decode()
            source = verify_and_extract(archive, sums, name, Path(root) / "releases" / version)
        result = subprocess.run(["sh", "scripts/install-tv.sh"], cwd=source, check=False)
        if result.returncode:
            raise UpdateError(f"install-tv.sh exited with {result.returncode}")

    return install


def health_url(config_path):
    """Where the bridge answers: its configured host, or loopback when it listens on every address."""
    try:
        config = json.loads(Path(config_path).read_text())
        host, port = config.get("host") or "0.0.0.0", int(config.get("port", 8765))
    except (OSError, ValueError, AttributeError):
        host, port = "0.0.0.0", 8765
    host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
    return f"http://{f'[{host}]' if ':' in host else host}:{port}/health"


def make_health(config_path):
    def health():
        url = health_url(config_path)
        deadline = time.monotonic() + HEALTH_SECONDS
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=3) as response:
                    body = json.loads(response.read())
                    if isinstance(body, dict) and body.get("ok"):
                        return True
            except Exception:  # not up yet, or an odd reply: keep waiting until the deadline
                pass
            time.sleep(2)
        return False

    return health


def restart():
    subprocess.run(["systemctl", "--user", "restart", "bigscreen-bridge.service"], check=False)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="bigscreen-bridge-update", description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=Path.home() / ".config/bigscreen-bridge/config.json")
    parser.add_argument("--check", action="store_true", help="report what would happen; change nothing")
    parser.add_argument("--now", action="store_true", help="do not wait for playback to stop")
    args = parser.parse_args(argv)
    try:
        installed = importlib.metadata.version("bigscreen-bridge")
    except importlib.metadata.PackageNotFoundError:
        installed = "unknown"
    return run(
        args.config,
        installed=installed,
        root=ROOT,
        fetch=fetch_releases,
        read_status=read_status,
        install=make_install(ROOT),
        health=make_health(args.config),
        restart=restart,
        sleep=time.sleep,
        log=lambda line: print(line, flush=True),
        check=args.check,
        now=args.now,
    )


if __name__ == "__main__":
    raise SystemExit(main())
