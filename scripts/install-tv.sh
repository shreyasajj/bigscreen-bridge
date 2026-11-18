#!/bin/sh
# Install or upgrade the bridge for the logged-in KDE user. No sudo needed.
# Runs from a checkout or an unpacked release (install.sh and the updater use it).
#
#   BIGSCREEN_UPDATE_CHANNEL=stable|prerelease   which releases the nightly updater takes
#   BIGSCREEN_AUTO_UPDATE=1|0                    turn the nightly updater on or off
# Unset, both keep what config.json already says (stable and on for a new install).
set -eu
cd "$(dirname "$0")/.."
version=$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml | head -n 1)
[ -n "$version" ] || { echo "No version in pyproject.toml" >&2; exit 1; }
install_root="$HOME/.local/share/bigscreen-bridge"
config_root="$HOME/.config/bigscreen-bridge"
units="$HOME/.config/systemd/user"
venvs="$install_root/venvs"
mkdir -p "$install_root" "$venvs" "$config_root" "$units"
chmod 700 "$config_root"

# A new venv for every install, under venvs/; `venv` points at the current one, so
# rolling back is a relink. The link moves only after pip has succeeded, so a failed
# install leaves the working venv in use. Scripts in a venv hold its absolute path,
# so a venv is never renamed once built.
if [ -d "$install_root/venv" ] && [ ! -L "$install_root/venv" ]; then
    rm -rf "$venvs/previous"
    mv "$install_root/venv" "$venvs/previous"
fi
previous=$(readlink "$install_root/venv" 2>/dev/null || true)
[ -n "$previous" ] || previous="$venvs/previous"
target="$venvs/$version-$(date +%Y%m%d%H%M%S)-$$"
python3 -m venv "$target"
if ! "$target/bin/pip" install --quiet ".[mqtt]"; then
    rm -rf "$target"
    echo "pip install failed; the bridge still runs the previous version" >&2
    exit 1
fi
ln -sfn "$target" "$install_root/venv"
# Keep the new venv and the one linked before it (the rollback target); drop the rest.
for old in "$venvs"/*; do
    [ -e "$old" ] || continue
    [ "$old" = "$target" ] || [ "$old" = "$previous" ] || rm -rf "$old"
done

cp bigscreen-bridge/kwin/monitor.js "$install_root/monitor.js"
auto=$(python3 - "$config_root/config.json" "$install_root/monitor.js" <<'PY'
import json, os, secrets, socket, sys, uuid
from pathlib import Path
path = Path(sys.argv[1])
if path.exists():
    config = json.loads(path.read_text())
else:
    config = dict(device_id=str(uuid.uuid4()), name=socket.gethostname(),
                  token=secrets.token_urlsafe(48), host='0.0.0.0', port=8765,
                  monitor_path=sys.argv[2])
update = config.setdefault('update', {})
channel, auto = os.environ.get('BIGSCREEN_UPDATE_CHANNEL'), os.environ.get('BIGSCREEN_AUTO_UPDATE')
if channel in ('stable', 'prerelease'):
    update['channel'] = channel
if auto in ('0', '1'):
    update['auto'] = auto == '1'
update.setdefault('channel', 'stable')
update.setdefault('auto', True)
temporary = path.with_suffix('.tmp')
fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, 'w') as stream:
    json.dump(config, stream, indent=2)
os.replace(temporary, path)
print('1' if update['auto'] else '0')
PY
)
chmod 600 "$config_root/config.json"

"$install_root/venv/bin/python" - "$install_root/venv/bin/bigscreen-bridge-open" <<'PYLINK'
from pathlib import Path
import sys
from bigscreen_bridge.open_link import desktop_entry
root = Path.home()/'.local/share/applications'
root.mkdir(parents=True, exist_ok=True)
(root/'bigscreen-bridge-link.desktop').write_text(desktop_entry(sys.argv[1]))
PYLINK

# The Bridge Status screen (read-only apart from asking for a new pairing code).
status_root="$HOME/.local/share/bigscreen-status"
rm -rf "$status_root"
mkdir -p "$status_root" "$HOME/.local/share/applications"
cp -R bigscreen-status/main.qml bigscreen-status/run.sh bigscreen-status/components \
    bigscreen-status/fixtures "$status_root/"
sed "s|@DIR@|$status_root|" bigscreen-status/bigscreen-status.desktop \
    > "$HOME/.local/share/applications/bigscreen-status.desktop"
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$HOME/.local/share/applications"
fi

cp systemd/bigscreen-bridge.service systemd/bigscreen-bridge-update.service \
    systemd/bigscreen-bridge-update.timer "$units/"
systemctl --user daemon-reload
systemctl --user enable bigscreen-bridge.service
if [ "$auto" = 1 ]; then
    systemctl --user enable --now bigscreen-bridge-update.timer
else
    systemctl --user disable --now bigscreen-bridge-update.timer || true
fi
systemctl --user restart bigscreen-bridge.service
printf '%s\n' "Bridge $version installed. To pair Home Assistant, open Bridge Status on the TV and type its code."
