#!/usr/bin/env bash
# Copy the Bridge status mock to the TV and run it inside the Plasma session.
#
#   ./launch.sh [start [scenario]]   copy and start, optionally on one fixture id
#   ./launch.sh stop         close the mock
#   ./launch.sh screenshot   save a full-screen capture to ./screenshots/
#   ./launch.sh logs         show the mock's journal
#
# The mock runs as a transient systemd user unit, so it inherits the session's
# WAYLAND_DISPLAY and never touches bigscreen-bridge.service or its config.
set -euo pipefail

HOST="${BIGSCREEN_HOST:-mediac@192.168.30.153}"
DEST=".local/share/bigscreen-status-mock"
UNIT="bigscreen-status-mock"
HERE="$(cd "$(dirname "$0")" && pwd)"

remote() { ssh -o BatchMode=yes "$HOST" "$@"; }

case "${1:-start}" in
start)
    # Only the app and its fixtures; no tools, screenshots or macOS metadata.
    COPYFILE_DISABLE=1 tar --no-xattrs -C "$HERE" --exclude ./tools --exclude ./screenshots --exclude '._*' \
        -cf - main.qml components fixtures | remote "rm -rf ~/$DEST && mkdir -p ~/$DEST && tar -C ~/$DEST -xf -"
    remote "systemctl --user stop $UNIT 2>/dev/null; systemctl --user reset-failed $UNIT 2>/dev/null; \
        systemd-run --user --quiet --collect --unit=$UNIT \
            --setenv=QML_XHR_ALLOW_FILE_READ=1 \
            qml6 \$HOME/$DEST/main.qml -- ${2:-}"
    echo "Started $UNIT on $HOST"
    ;;
stop)
    remote "systemctl --user stop $UNIT 2>/dev/null || true"
    echo "Stopped $UNIT"
    ;;
screenshot)
    name="${2:-$(date +%H%M%S)}"
    mkdir -p "$HERE/screenshots"
    remote "systemd-run --user --quiet --collect --wait spectacle --background --nonotify --fullscreen \
        --output /tmp/$UNIT-shot.png >/dev/null && cat /tmp/$UNIT-shot.png && rm -f /tmp/$UNIT-shot.png" \
        > "$HERE/screenshots/$name.png"
    echo "screenshots/$name.png"
    ;;
logs)
    remote "journalctl --user -u $UNIT --no-pager -n ${2:-50}"
    ;;
*)
    echo "usage: $0 [start [scenario]|stop|screenshot [name]|logs [lines]]" >&2
    exit 2
    ;;
esac
