#!/bin/sh
# Run on KDE as the logged-in user, from the repository root. No sudo needed.
set -eu
install_root="$HOME/.local/share/bigscreen-bridge"
config_root="$HOME/.config/bigscreen-bridge"
mkdir -p "$install_root" "$config_root" "$HOME/.config/systemd/user"
chmod 700 "$config_root"
python3 -m venv "$install_root/venv"
"$install_root/venv/bin/pip" install .
cp bigscreen-bridge/kwin/monitor.js "$install_root/monitor.js"
python3 - "$config_root/config.json" "$install_root/monitor.js" <<'PY'
import json, os, secrets, socket, sys, uuid
from pathlib import Path
path = Path(sys.argv[1])
if not path.exists():
    config = dict(device_id=str(uuid.uuid4()), name=socket.gethostname(),
                  token=secrets.token_urlsafe(48), host='0.0.0.0', port=8765,
                  monitor_path=sys.argv[2])
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(config, stream, indent=2)
PY
chmod 600 "$config_root/config.json"
"$install_root/venv/bin/python" - "$install_root/venv/bin/bigscreen-bridge-open" <<'PYLINK'
from pathlib import Path
import sys
from bigscreen_bridge.open_link import desktop_entry
root = Path.home()/'.local/share/applications'
root.mkdir(parents=True, exist_ok=True)
(root/'bigscreen-bridge-link.desktop').write_text(desktop_entry(sys.argv[1]))
PYLINK
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$HOME/.local/share/applications"
fi
cp systemd/bigscreen-bridge.service "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable bigscreen-bridge.service
systemctl --user restart bigscreen-bridge.service
printf '%s\n' "Bridge installed. Pair using token in $config_root/config.json (keep it private)."
