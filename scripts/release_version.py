#!/usr/bin/env python3
"""Release versions for .github/workflows/release.yml.

  release_version.py check-tag vX.Y.Z --pyproject pyproject.toml     tag must equal the project version
  release_version.py next-prerelease --pyproject P --tags-file F     next rc after the latest stable tag
  release_version.py stamp X.Y.Z --pyproject P                       write the version into pyproject.toml
"""
import argparse
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bigscreen-bridge" / "src"))
from bigscreen_bridge.update import parse_version  # noqa: E402

PROJECT_VERSION = re.compile(r'^version = "([^"]*)"$', re.MULTILINE)


def project_version(path):
    match = PROJECT_VERSION.search(Path(path).read_text())
    if not match:
        raise SystemExit(f"No version in {path}")
    return match.group(1)


def next_prerelease(tags, project):
    """`rcN` of the patch after the newest stable tag; of the project version when nothing is stable yet."""
    versions = [v for v in map(parse_version, tags) if v]
    stable = [v for v in versions if v[3] == 1]
    if stable:
        major, minor, patch = max(stable)[:3]
        base = (major, minor, patch + 1)
    else:
        parsed = parse_version(project)
        if parsed is None:
            raise SystemExit(f"Project version {project!r} is not X.Y.Z")
        base = parsed[:3]
    taken = [v[4] for v in versions if v[:3] == base and v[3] == 0]
    return "%d.%d.%drc%d" % (*base, max(taken, default=0) + 1)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="release_version.py")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-tag")
    check.add_argument("tag")
    nxt = commands.add_parser("next-prerelease")
    nxt.add_argument("--tags-file", type=Path, required=True)
    stamp = commands.add_parser("stamp")
    stamp.add_argument("version")
    for command in (check, nxt, stamp):
        command.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    args = parser.parse_args(argv)
    if args.command == "check-tag":
        version = project_version(args.pyproject)
        if parse_version(args.tag) is None or args.tag.lstrip("v") != version:
            print(f"Tag {args.tag} does not match pyproject.toml version {version}", file=sys.stderr)
            return 1
        return 0
    if args.command == "next-prerelease":
        print(next_prerelease(args.tags_file.read_text().split(), project_version(args.pyproject)))
        return 0
    if parse_version(args.version) is None:
        print(f"{args.version} is not X.Y.Z or X.Y.ZrcN", file=sys.stderr)
        return 1
    text = args.pyproject.read_text()
    args.pyproject.write_text(PROJECT_VERSION.sub(f'version = "{args.version}"', text, count=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
