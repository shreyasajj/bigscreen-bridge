#!/bin/sh
# Build the canonical checkout separately from existing sources on the TV.
set -eu
source_root="${1:-../plasma-tv}"
tv_host="${BIGSCREEN_TV:-mediac@192.168.30.153}"
[ -f "$source_root/packaging/flatpak/build-bridge-flatpak.sh" ] || exit 1
ssh "$tv_host" 'mkdir -p ~/.local/share/bigscreen-bridge/app-sources/flimmer'
rsync -az --exclude .git --exclude .vscode --exclude build --exclude build-flatpak \
    --exclude .flatpak-builder --exclude repo --exclude stage --exclude flatpak_app \
    "$source_root/" "$tv_host:.local/share/bigscreen-bridge/app-sources/flimmer/"
ssh "$tv_host" 'sh ~/.local/share/bigscreen-bridge/app-sources/flimmer/packaging/flatpak/build-bridge-flatpak.sh --install'
printf '%s\n' 'Installed. Restart only the Flimmer GUI to load the control endpoint.'
