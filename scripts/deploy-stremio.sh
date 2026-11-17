#!/bin/sh
# Sync the canonical checkout to a separate TV build path, then build/install.
set -eu
source_root="${1:-../stremio-tv}"
tv_host="${BIGSCREEN_TV:-mediac@192.168.30.153}"
[ -f "$source_root/native/packaging/build-bridge-flatpak.sh" ] || {
    printf '%s\n' 'Pass the patched Stremio TV checkout directory' >&2
    exit 1
}
ssh "$tv_host" 'mkdir -p ~/.local/share/bigscreen-bridge/app-sources/stremio-tv'
rsync -az --exclude .git --exclude .vscode --exclude node_modules --exclude native/build \
    --exclude test/shots --exclude dist --exclude .flatpak-builder \
    "$source_root/" "$tv_host:.local/share/bigscreen-bridge/app-sources/stremio-tv/"
ssh "$tv_host" 'sh ~/.local/share/bigscreen-bridge/app-sources/stremio-tv/native/packaging/build-bridge-flatpak.sh --install'
printf '%s\n' 'Installation finished. Restart only the Stremio GUI to load the new build.'
