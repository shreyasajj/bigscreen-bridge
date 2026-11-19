"""Which version a release gets: from the tag, or the next pre-release for a security fix."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "release_version.py"
spec = importlib.util.spec_from_file_location("release_version", SCRIPT)
release_version = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_version)


def test_next_prerelease_after_latest_stable():
    assert release_version.next_prerelease(["v0.3.0", "v0.2.9"], "0.3.0") == "0.3.1rc1"
    assert release_version.next_prerelease(["v0.3.0", "v0.3.1rc1", "v0.3.1rc2"], "0.3.0") == "0.3.1rc3"
    assert (
        release_version.next_prerelease(["v0.3.0", "v0.4.0rc1", "junk", "v0.3.1rc9"], "0.3.0") == "0.3.1rc10"
    )


def test_first_prerelease_without_any_stable_uses_project_version():
    assert release_version.next_prerelease([], "0.3.0") == "0.3.0rc1"
    assert release_version.next_prerelease(["v0.3.0rc1"], "0.3.0") == "0.3.0rc2"


def pyproject(tmp_path, version):
    path = tmp_path / "pyproject.toml"
    path.write_text(f'[project]\nname = "bigscreen-bridge"\nversion = "{version}"\n')
    return path


def test_tag_must_match_project_version(tmp_path, capsys):
    path = pyproject(tmp_path, "0.2.0")
    assert release_version.main(["check-tag", "v0.3.0", "--pyproject", str(path)]) == 1
    assert "0.2.0" in capsys.readouterr().err
    assert release_version.main(["check-tag", "v0.2.0", "--pyproject", str(path)]) == 0
    assert release_version.main(["check-tag", "release-1", "--pyproject", str(path)]) == 1


def test_cli_prints_the_next_prerelease(tmp_path, capsys):
    tags = tmp_path / "tags"
    tags.write_text("v0.3.0\nv0.3.1rc1\n")
    assert (
        release_version.main(
            ["next-prerelease", "--pyproject", str(pyproject(tmp_path, "0.3.0")), "--tags-file", str(tags)]
        )
        == 0
    )
    assert capsys.readouterr().out.strip() == "0.3.1rc2"


@pytest.mark.parametrize("version", ["0.3.1rc2", "1.0.0"])
def test_stamp_rewrites_only_the_project_version(tmp_path, version):
    path = pyproject(tmp_path, "0.3.0")
    path.write_text(path.read_text() + '\n[tool.x]\nversion = "keep"\n')
    assert release_version.main(["stamp", version, "--pyproject", str(path)]) == 0
    text = path.read_text()
    assert f'version = "{version}"' in text and 'version = "keep"' in text
