#!/usr/bin/env python3
"""Regenerate patches/ from the app branches named in config/repositories.json.

The branches are the source of truth; the patch files are a reviewable export and
must never be edited by hand. Run from anywhere: `python3 scripts/export-patches.py`.
Pass --check to fail when a committed patch no longer matches its branch.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    check = "--check" in sys.argv[1:]
    stale = []
    for name, repo in json.loads((ROOT / "config/repositories.json").read_text())["repositories"].items():
        revisions = f"{repo['base_revision']}..{repo['development_branch']}"
        diff = subprocess.run(
            ["git", "-C", repo["path"], "diff", "--binary", revisions], check=True, capture_output=True
        ).stdout
        target = ROOT / repo["patch"]
        if check:
            if target.read_bytes() != diff:
                stale.append(name)
        else:
            target.write_bytes(diff)
            print(f"{name}: {revisions} -> {repo['patch']} ({diff.count(b'diff --git')} files)")
    if stale:
        sys.exit("Stale patches, re-run without --check: " + ", ".join(stale))


if __name__ == "__main__":
    main()
