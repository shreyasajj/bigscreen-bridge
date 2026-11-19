"""The self-updater: which release to take, when, and what happens if it fails."""
import hashlib
import io
import json
import tarfile

import pytest
from bigscreen_bridge.update import (
    UpdateError,
    is_playing,
    parse_version,
    pick_release,
    run,
    verify_and_extract,
)

STABLE = {"tag_name": "v0.3.1", "prerelease": False, "draft": False}


def test_versions_order_prereleases_before_final():
    assert parse_version("v0.3.1rc1") < parse_version("0.3.1rc2") < parse_version("0.3.1") < parse_version("0.3.2rc1")
    assert parse_version("0.3") is None and parse_version("latest") is None and parse_version("0.3.1-x") is None


def test_stable_channel_skips_prereleases_and_drafts():
    releases = [{"tag_name": "v0.4.0rc1", "prerelease": True, "draft": False},
                {"tag_name": "v0.3.2", "prerelease": False, "draft": True},
                STABLE, {"tag_name": "nightly", "prerelease": False, "draft": False}]
    assert pick_release(releases, "stable", "0.3.0")["tag_name"] == "v0.3.1"
    assert pick_release(releases, "prerelease", "0.3.0")["tag_name"] == "v0.4.0rc1"
    assert pick_release(releases, "stable", "0.3.1") is None
    assert pick_release(releases, "stable", "unknown")["tag_name"] == "v0.3.1"   # a dev install takes any release


def archive(tmp_path, members):
    path = tmp_path / "bigscreen-bridge-0.3.1.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path, f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"


def test_a_good_archive_is_unpacked_without_its_top_directory(tmp_path):
    path, sums = archive(tmp_path, {"bigscreen-bridge-0.3.1/scripts/install-tv.sh": b"echo hi\n"})
    dest = verify_and_extract(path, sums, path.name, tmp_path / "releases" / "0.3.1")
    assert (dest / "scripts" / "install-tv.sh").read_bytes() == b"echo hi\n"


@pytest.mark.parametrize("members", [{"bigscreen-bridge-0.3.1/../evil": b"x"}, {"/etc/evil": b"x"}])
def test_traversal_is_refused(tmp_path, members):
    path, sums = archive(tmp_path, members)
    with pytest.raises(UpdateError):
        verify_and_extract(path, sums, path.name, tmp_path / "releases" / "0.3.1")
    assert not (tmp_path / "releases").exists() and not (tmp_path / "evil").exists()


def test_a_wrong_or_missing_checksum_is_refused(tmp_path):
    path, _ = archive(tmp_path, {"bigscreen-bridge-0.3.1/a": b"x"})
    for sums in ("0" * 64 + f"  {path.name}\n", "0" * 64 + "  other.tar.gz\n"):
        with pytest.raises(UpdateError):
            verify_and_extract(path, sums, path.name, tmp_path / "releases" / "0.3.1")
    assert not (tmp_path / "releases").exists()


def test_playing_is_read_from_apps_and_sessions():
    assert is_playing({"snapshot": {"apps": [{"state": "playing"}], "sessions": []}})
    assert is_playing({"snapshot": {"apps": [], "sessions": [{"state": "playing"}]}})
    assert not is_playing({"snapshot": {"apps": [{"state": "paused"}]}})
    assert not is_playing({})


class Tv:
    """Everything the updater touches, recorded."""

    def __init__(self, tmp_path, update=None, playing=False, healthy=True, releases=(STABLE,)):
        self.root = tmp_path / "share"
        (self.root / "venvs" / "0.3.0").mkdir(parents=True)
        (self.root / "venv").symlink_to(self.root / "venvs" / "0.3.0")
        self.config = tmp_path / "config.json"
        self.config.write_text(json.dumps({"token": "secret-token", **({"update": update} if update else {})}))
        self.playing, self.healthy = playing, healthy
        self.releases = releases if isinstance(releases, Exception) else list(releases)
        self.installed, self.sleeps, self.lines, self.restarts = [], [], [], 0

    def fetch(self):
        if isinstance(self.releases, Exception):
            raise self.releases
        return self.releases

    def read_status(self):
        if isinstance(self.playing, Exception):
            raise self.playing
        return {"snapshot": {"apps": [{"state": "playing" if self.playing else "idle"}]}}

    def install(self, release):
        (self.root / "venvs" / "0.3.1").mkdir()
        (self.root / "venv").unlink()
        (self.root / "venv").symlink_to(self.root / "venvs" / "0.3.1")
        self.installed.append(release["tag_name"])

    def restart(self):
        self.restarts += 1

    def run(self, **kwargs):
        return run(self.config, installed="0.3.0", root=self.root, fetch=self.fetch, read_status=self.read_status,
                   install=self.install, health=lambda: self.healthy, restart=self.restart,
                   sleep=self.sleeps.append, log=self.lines.append, **kwargs)


def test_installs_a_newer_release_when_nothing_plays(tmp_path):
    tv = Tv(tmp_path)
    assert tv.run() == 0
    assert tv.installed == ["v0.3.1"] and tv.sleeps == [] and tv.restarts == 0
    assert any("0.3.0" in line and "0.3.1" in line for line in tv.lines)
    assert not any("secret-token" in line for line in tv.lines)


def test_waits_for_playback_then_gives_up(tmp_path):
    tv = Tv(tmp_path, playing=True)
    assert tv.run() == 0
    assert tv.sleeps == [600] * 24 and tv.installed == []


def test_now_skips_the_playback_wait(tmp_path):
    tv = Tv(tmp_path, playing=True)
    assert tv.run(now=True) == 0 and tv.installed == ["v0.3.1"]


def test_unreachable_bus_counts_as_not_playing(tmp_path):
    tv = Tv(tmp_path, playing=RuntimeError("no bridge on the bus"))
    assert tv.run() == 0 and tv.installed == ["v0.3.1"]


def test_offline_or_rate_limited_changes_nothing(tmp_path):
    tv = Tv(tmp_path, releases=UpdateError("GitHub rate limit reached"))
    assert tv.run() == 0
    assert tv.installed == [] and len(tv.lines) == 1 and "rate limit" in tv.lines[0]


def test_failed_health_rolls_back(tmp_path):
    tv = Tv(tmp_path, healthy=False)
    assert tv.run() == 1
    assert (tv.root / "venv").resolve() == (tv.root / "venvs" / "0.3.0").resolve() and tv.restarts == 1


def test_a_failed_install_rolls_back_too(tmp_path):
    tv = Tv(tmp_path)
    def broken(release):
        Tv.install(tv, release)
        raise UpdateError("install-tv.sh failed")
    tv.install = broken
    assert tv.run() == 1
    assert (tv.root / "venv").resolve() == (tv.root / "venvs" / "0.3.0").resolve() and tv.restarts == 1


def test_prerelease_channel_from_config(tmp_path):
    tv = Tv(tmp_path, update={"channel": "prerelease"},
            releases=[STABLE, {"tag_name": "v0.3.2rc1", "prerelease": True, "draft": False}])
    tv.install = lambda release: tv.installed.append(release["tag_name"])
    assert tv.run() == 0 and tv.installed == ["v0.3.2rc1"]


def test_auto_false_and_check_mode_only_report(tmp_path):
    tv = Tv(tmp_path, update={"auto": False})
    assert tv.run() == 0 and tv.installed == [] and "0.3.1" in tv.lines[-1]
    tv = Tv(tmp_path / "b")
    assert tv.run(check=True) == 0 and tv.installed == [] and "0.3.1" in tv.lines[-1]


def test_up_to_date_says_so(tmp_path):
    tv = Tv(tmp_path, releases=[])
    assert tv.run() == 0 and tv.installed == [] and "up to date" in tv.lines[-1].lower()


def test_an_unexpected_failure_after_install_still_rolls_back(tmp_path):
    tv = Tv(tmp_path)
    def broken_health():
        raise AttributeError("'list' object has no attribute 'get'")
    assert run(tv.config, installed="0.3.0", root=tv.root, fetch=tv.fetch, read_status=tv.read_status,
               install=tv.install, health=broken_health, restart=tv.restart, sleep=tv.sleeps.append,
               log=tv.lines.append) == 1
    assert (tv.root / "venv").resolve() == (tv.root / "venvs" / "0.3.0").resolve() and tv.restarts == 1


def test_a_release_that_failed_is_skipped_until_a_newer_one(tmp_path):
    tv = Tv(tmp_path, healthy=False)
    assert tv.run() == 1 and tv.installed == ["v0.3.1"]
    tv.healthy = True
    assert tv.run() == 0 and tv.installed == ["v0.3.1"]          # not retried
    assert "0.3.1" in tv.lines[-1] and "failed before" in tv.lines[-1]
    tv.releases.append({"tag_name": "v0.3.2", "prerelease": False, "draft": False})
    tv.install = lambda release: tv.installed.append(release["tag_name"])
    assert tv.run() == 0 and tv.installed == ["v0.3.1", "v0.3.2"]


@pytest.mark.parametrize(("host", "expected"), [("0.0.0.0", "127.0.0.1"), ("192.168.30.153", "192.168.30.153"),
                                                 (None, "127.0.0.1"), ("::", "::1")])
def test_health_asks_the_address_the_bridge_listens_on(tmp_path, host, expected):
    from bigscreen_bridge.update import health_url
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"port": 9000, **({"host": host} if host else {})}))
    wanted = f"[{expected}]" if ":" in expected else expected
    assert health_url(config) == f"http://{wanted}:9000/health"
