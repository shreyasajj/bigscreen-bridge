#!/bin/sh
# Spike: does a headless Plasma Bigscreen TV work in a container on this machine?
# Builds the TV image, installs the bridge from the working tree with install.sh,
# installs and opens Couchside, and passes only when the bridge itself reports
# Couchside running. Prints SPIKE OK on success. Run from the repository root.
set -eu
name=bigscreen-tv-spike
image=bigscreen-tv:test
here=$(cd "$(dirname "$0")" && pwd)
started=$(date +%s)

as_tv() { docker exec -u tv -e XDG_RUNTIME_DIR=/run/user/1000 \
    -e DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus -w /home/tv "$name" sh -c "$1"; }
step() { echo "== $* ($(( $(date +%s) - started ))s)"; }
wait_for() { # seconds, description, command
    i=0
    until as_tv "$3" >/dev/null 2>&1; do
        i=$((i + 2)); [ "$i" -lt "$1" ] || { echo "Timed out: $2" >&2; return 1; }
        sleep 2
    done
}
dump() {
    echo "== diagnostics"
    docker exec "$name" systemctl --no-pager --failed || true
    docker exec "$name" journalctl --no-pager -n 60 || true
    as_tv 'systemctl --user --no-pager status tv-session bigscreen-bridge; journalctl --user --no-pager -n 150' || true
}
cleanup() { docker rm -f "$name" >/dev/null 2>&1 || true; }
trap 'status=$?; [ "$status" -eq 0 ] || dump; cleanup' EXIT

step "build image"
docker build -q -t "$image" --target tv "$here/tv"
sh "$here/build-release.sh" /opt/release >/dev/null

step "start the TV"
cleanup
docker run -d --name "$name" --privileged --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
    --tmpfs /run --tmpfs /run/lock -v "$(pwd)/dist:/opt/release:ro" "$image" >/dev/null
wait_for 120 "user session bus" 'busctl --user status >/dev/null'
wait_for 180 "KWin and the Bigscreen shell" \
    "test -e /run/user/1000/tv-ready || { systemctl --user is-failed -q tv-session && exit 0; false; }"
if as_tv "systemctl --user is-failed -q tv-session"; then echo "The TV session failed" >&2; exit 1; fi
sleep 10
as_tv 'busctl --user list --no-pager | grep -E "org.kde.(KWin|plasmashell) "' \
    || { echo "KWin or the Bigscreen shell did not stay up" >&2; exit 1; }

step "install the bridge with install.sh"
as_tv 'BIGSCREEN_RELEASES_API=file:///opt/release/releases sh /opt/release/install.sh --no-auto-update'
wait_for 60 "bridge Status on the session bus" \
    'busctl --user call org.bigscreen.Bridge /org/bigscreen/Bridge org.bigscreen.Bridge.Status Status'

step "install Couchside"
as_tv 'flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo'
# GitHub Pages, which hosts the Couchside repo, rate-limits the many small object fetches
# (503, 429). Flatpak keeps what it already fetched, so back off and continue.
delay=30
for attempt in 1 2 3 4 5 6; do
    as_tv 'flatpak install --user -y --noninteractive https://shreyasajj.github.io/couchside/couchside.flatpakref' && break
    [ "$attempt" -lt 6 ] || exit 1
    echo "Couchside download failed (attempt $attempt); retrying in ${delay}s"; sleep "$delay"
    delay=$((delay * 2))
done

step "open Couchside"
as_tv 'systemd-run --user --collect --unit=couchside flatpak run io.github.shreyasajj.Couchside'
status='busctl --user --json=short call org.bigscreen.Bridge /org/bigscreen/Bridge org.bigscreen.Bridge.Status Status | jq -r ".data[0]"'
if ! wait_for 180 "the bridge to report Couchside running" \
        "$status | jq -e '.snapshot.apps[] | select(.id == \"couchside\") | .installed and .running'"; then
    as_tv "$status | jq '.snapshot.apps, .snapshot.available'" || true
    exit 1
fi
as_tv "$status | jq -c '.snapshot.apps[] | {id, installed, running, state}'"
step "SPIKE OK"
