#!/usr/bin/env python3
"""Package the custom integration, excluding caches and private configuration."""
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parents[1]
component = root / "custom_components/bigscreen_bridge"
version = json.loads((component / "manifest.json").read_text())["version"]
target = root / f"dist/bigscreen_bridge-{version}.zip"
target.parent.mkdir(exist_ok=True)
with ZipFile(target, "w", compression=ZIP_DEFLATED) as archive:
    for path in sorted(component.rglob("*")):
        if path.is_file() and path.suffix in {".py", ".json"} and "__pycache__" not in path.parts:
            archive.write(path, path.relative_to(root))
print(target)
