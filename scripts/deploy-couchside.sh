#!/bin/sh
# Build/install from the user's canonical local repository, preserving the TV source trees.
set -eu
source_root="${1:-../couchside}"
tv_host="${BIGSCREEN_TV:-mediac@192.168.30.153}"
[ -f "$source_root/flatpak/io.github.shreyasajj.Couchside.yml" ] || {
    printf '%s\n' "Pass the Couchside source directory as the first argument" >&2
    exit 1
}
ssh "$tv_host" 'mkdir -p ~/.local/share/bigscreen-bridge/app-sources/couchside'
rsync -az --exclude .git --exclude .venv --exclude .superpowers --exclude __pycache__ \
    --exclude '*.egg-info' --exclude build-dir --exclude .flatpak-builder \
    --exclude 'Bigscreen Jukebox.dc.html' --exclude support.js \
    "$source_root/" "$tv_host:.local/share/bigscreen-bridge/app-sources/couchside/"
ssh "$tv_host" 'flatpak run \
    --filesystem="$HOME/.local/share/bigscreen-bridge/app-sources/couchside" \
    --filesystem="$HOME/.cache" org.flatpak.Builder --user --install --force-clean --ccache \
    --state-dir="$HOME/.cache/couchside-build/state" "$HOME/.cache/bigscreen-couchside-build" \
    "$HOME/.local/share/bigscreen-bridge/app-sources/couchside/flatpak/io.github.shreyasajj.Couchside.yml"'
printf '%s\n' 'Installed. Restart only the Couchside UI; keep couchside-sendspin running.'
