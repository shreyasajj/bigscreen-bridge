#!/bin/sh
# scripts/install-tv.sh against a throwaway HOME: no systemd, no network, no pip.
# Run from the repository root: sh tests/test_install.sh
set -eu
repo=$(pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
real_python=$(command -v python3)
export HOME="$work/home" LOG="$work/calls.log"
mkdir -p "$HOME" "$work/bin"
: > "$LOG"

# Stubs on PATH. python3 only fakes `-m venv`; everything else is the real one.
cat > "$work/bin/python3" <<EOF
#!/bin/sh
if [ "\$1" = -m ] && [ "\$2" = venv ]; then
    mkdir -p "\$3/bin"
    printf '#!/bin/sh\necho "pip \$*" >> "$LOG"\n' > "\$3/bin/pip"
    printf '#!/bin/sh\nPYTHONPATH="$repo/bigscreen-bridge/src" exec "$real_python" "\$@"\n' > "\$3/bin/python"
    chmod +x "\$3/bin/pip" "\$3/bin/python"
    exit 0
fi
exec "$real_python" "\$@"
EOF
printf '#!/bin/sh\necho "systemctl $*" >> "%s"\n' "$LOG" > "$work/bin/systemctl"
printf '#!/bin/sh\nexit 0\n' > "$work/bin/update-desktop-database"
chmod +x "$work/bin/"*
export PATH="$work/bin:$PATH"

fail() { echo "FAIL: $*"; exit 1; }
share="$HOME/.local/share/bigscreen-bridge"
config="$HOME/.config/bigscreen-bridge/config.json"
version=$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml | head -n 1)

# An existing install from before releases: a real venv directory and a config.
mkdir -p "$share/venv/bin" "$(dirname "$config")"
echo old > "$share/venv/marker"
echo '{"device_id": "kept", "token": "kept-token", "port": 8765}' > "$config"
echo '{"clients": []}' > "$(dirname "$config")/clients.json"

sh scripts/install-tv.sh > "$work/out.log" 2>&1 || { cat "$work/out.log"; fail "install-tv.sh exited non-zero"; }

[ -L "$share/venv" ] || fail "venv is not a symlink"
[ "$(readlink "$share/venv")" = "$share/venvs/$version" ] || fail "venv points at $(readlink "$share/venv")"
[ "$(cat "$share/venvs/previous/marker")" = old ] || fail "the old venv was not kept as venvs/previous"
"$real_python" - "$config" <<'PY' || fail "config not merged"
import json, sys
c = json.load(open(sys.argv[1]))
assert c["device_id"] == "kept" and c["token"] == "kept-token", c
assert c["update"] == {"channel": "stable", "auto": True}, c["update"]
PY
[ -f "$(dirname "$config")/clients.json" ] || fail "clients.json lost"
[ "$(stat -f %Lp "$config" 2>/dev/null || stat -c %a "$config")" = 600 ] || fail "config is not 0600"
[ -f "$HOME/.local/share/bigscreen-status/main.qml" ] || fail "status screen not installed"
desktop="$HOME/.local/share/applications/bigscreen-status.desktop"
grep -q "$HOME/.local/share/bigscreen-status" "$desktop" || fail "status launcher not written"
! grep -q @DIR@ "$desktop" || fail "@DIR@ left in the launcher"
[ -f "$HOME/.config/systemd/user/bigscreen-bridge-update.timer" ] || fail "timer not installed"
grep -q "enable --now bigscreen-bridge-update.timer" "$LOG" || fail "timer not enabled"
grep -q "restart bigscreen-bridge.service" "$LOG" || fail "bridge not restarted"

# Following pre-releases, without automatic updates: settings change, timer goes.
: > "$LOG"
BIGSCREEN_UPDATE_CHANNEL=prerelease BIGSCREEN_AUTO_UPDATE=0 sh scripts/install-tv.sh > "$work/out.log" 2>&1 \
    || { cat "$work/out.log"; fail "second install exited non-zero"; }
"$real_python" -c "import json,sys; u=json.load(open(sys.argv[1]))['update']; assert u == {'channel': 'prerelease', 'auto': False}, u" "$config" \
    || fail "flags not written"
grep -q "disable --now bigscreen-bridge-update.timer" "$LOG" || fail "timer not disabled"

# Older venvs are pruned: the current one and one other stay.
mkdir -p "$share/venvs/0.0.1" "$share/venvs/0.0.2"
sh scripts/install-tv.sh > "$work/out.log" 2>&1 || { cat "$work/out.log"; fail "third install exited non-zero"; }
count=$(find "$share/venvs" -mindepth 1 -maxdepth 1 | wc -l | tr -d ' ')
[ "$count" = 2 ] || fail "$count venvs kept, expected 2"
[ -d "$share/venvs/$version" ] || fail "current venv pruned"
"$real_python" -c "import json,sys; u=json.load(open(sys.argv[1]))['update']; assert u == {'channel': 'prerelease', 'auto': False}, u" "$config" \
    || fail "a plain re-run changed the update settings"

echo "install-tv.sh: all checks passed"

# install.sh against a fake release served from file:// URLs.
fake="$work/release"
mkdir -p "$fake/stage/bigscreen-bridge-9.9.9"
tar -cf - --exclude ./.git --exclude ./.venv --exclude ./.superpowers . \
    | tar -xf - -C "$fake/stage/bigscreen-bridge-9.9.9"
sed -i.bak 's/^version = ".*"$/version = "9.9.9"/' "$fake/stage/bigscreen-bridge-9.9.9/pyproject.toml"
tar -czf "$fake/bigscreen-bridge-9.9.9.tar.gz" -C "$fake/stage" bigscreen-bridge-9.9.9
(cd "$fake" && sha256sum bigscreen-bridge-9.9.9.tar.gz > SHA256SUMS)
cat > "$fake/latest" <<EOF
{"tag_name": "v9.9.9", "draft": false, "prerelease": false, "assets": [
 {"name": "bigscreen-bridge-9.9.9.tar.gz", "browser_download_url": "file://$fake/bigscreen-bridge-9.9.9.tar.gz"},
 {"name": "SHA256SUMS", "browser_download_url": "file://$fake/SHA256SUMS"}]}
EOF
BIGSCREEN_RELEASES_API="file://$fake" sh install.sh > "$work/out.log" 2>&1 || { cat "$work/out.log"; fail "install.sh exited non-zero"; }
[ "$(readlink "$share/venv")" = "$share/venvs/9.9.9" ] || fail "install.sh did not install 9.9.9"
[ -f "$share/releases/9.9.9/scripts/install-tv.sh" ] || fail "release not unpacked"
"$real_python" -c "import json,sys; c=json.load(open(sys.argv[1])); assert c['token'] == 'kept-token' and c['update']['channel'] == 'prerelease', c" "$config" \
    || fail "install.sh changed the config"

# A tampered archive installs nothing.
echo junk >> "$fake/bigscreen-bridge-9.9.9.tar.gz"
rm -rf "$share/releases/9.9.9"
if BIGSCREEN_RELEASES_API="file://$fake" sh install.sh > "$work/out.log" 2>&1; then fail "a bad checksum was accepted"; fi
grep -q "Checksum mismatch" "$work/out.log" || fail "no checksum message"
[ ! -e "$share/releases/9.9.9" ] || fail "a bad archive was unpacked"

echo "install.sh: all checks passed"
