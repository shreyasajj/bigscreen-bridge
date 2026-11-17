#!/usr/bin/env python3
"""Copy the integration into the HACS repository checkout; this project stays the source.

    python3 scripts/export-hacs.py [path-to-ha-bigscreen-bridge]   # copy
    python3 scripts/export-hacs.py --check [path]                  # exit 1 on drift
"""
import filecmp
from pathlib import Path
import shutil
import sys

root = Path(__file__).resolve().parents[1]
source = root / "custom_components/bigscreen_bridge"
arguments = [value for value in sys.argv[1:] if value != "--check"]
target = Path(arguments[0] if arguments else root.parent / "ha-bigscreen-bridge") / "custom_components/bigscreen_bridge"
files = sorted(path.relative_to(source) for path in source.rglob("*")
               if path.is_file() and path.suffix in {".py", ".json"} and "__pycache__" not in path.parts)
existing = sorted(path.relative_to(target) for path in target.rglob("*")
                  if path.is_file() and "__pycache__" not in path.parts) if target.exists() else []
drift = [str(name) for name in files if not (target / name).exists()
         or not filecmp.cmp(source / name, target / name, shallow=False)]
drift += [str(name) for name in existing if name not in files]
if "--check" in sys.argv:
    print("\n".join(drift) or "HACS repository matches")
    sys.exit(1 if drift else 0)
for name in existing:
    if name not in files:
        (target / name).unlink()
for name in files:
    (target / name).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / name, target / name)
print(f"{len(drift)} file(s) updated in {target}")
