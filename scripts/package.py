#!/usr/bin/env python3
"""Package the custom integration, excluding caches and private configuration."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parents[1]
target = root / "dist/bigscreen_bridge-0.1.0.zip"
target.parent.mkdir(exist_ok=True)
with ZipFile(target, "w", compression=ZIP_DEFLATED) as archive:
    for path in sorted((root / "custom_components/bigscreen_bridge").rglob("*")):
        if path.is_file() and path.suffix in {".py", ".json"} and "__pycache__" not in path.parts:
            archive.write(path, path.relative_to(root))
print(target)
