#!/bin/sh
# Build the source archive locally; reuse the installed matching Linux Electron runtime.
set -eu
source_root="${1:-../VacuumTube}"
tv_host="${BIGSCREEN_TV:-mediac@192.168.30.153}"
[ -f "$source_root/scripts/build-bridge-asar.cjs" ] || exit 1
source_root=$(CDPATH='' cd -- "$source_root" && pwd)
local_stage=$(mktemp -d "${TMPDIR:-/tmp}/bigscreen-vacuumtube-XXXXXX")
trap 'rm -rf "$local_stage"' EXIT HUP INT TERM
baseline_root=$(ssh "$tv_host" 'flatpak info --show-location rocks.shy.VacuumTube')
scp -q "$tv_host:$baseline_root/files/bin/vacuumtube/resources/app.asar" "$local_stage/installed.asar"
if [ ! -x "$source_root/node_modules/.bin/asar" ]; then
    (cd "$source_root" && npm ci --ignore-scripts)
fi
node "$source_root/scripts/build-bridge-asar.cjs" "$local_stage/installed.asar" "$local_stage/app.asar"
ssh "$tv_host" 'mkdir -p ~/.local/share/bigscreen-bridge/app-sources/VacuumTube/flatpak'
scp -q "$local_stage/app.asar" "$tv_host:.cache/vacuumtube-bridge-app.asar"
rsync -az --exclude .git --exclude node_modules --exclude dist "$source_root/" \
    "$tv_host:.local/share/bigscreen-bridge/app-sources/VacuumTube/"
ssh "$tv_host" 'sh ~/.local/share/bigscreen-bridge/app-sources/VacuumTube/flatpak/install-bridge.sh ~/.cache/vacuumtube-bridge-app.asar'
