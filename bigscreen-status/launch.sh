#!/usr/bin/env bash
# Install and drive the Bridge status screen on the TV over SSH.
#
#   ./launch.sh install              copy to the TV and add it to the Bigscreen launcher
#   ./launch.sh start [args]         install, then start (live; see run.sh for args)
#   ./launch.sh start --fixtures [scenario[:card[:sources]]]
#   ./launch.sh stop                 close it
#   ./launch.sh screenshot [name]    Spectacle capture to ./screenshots/<name>.png
#   ./launch.sh logs [lines]         the screen's journal
#   ./launch.sh uninstall            stop, remove the files and the launcher entry
#
# The screen runs as a transient systemd user unit, so it inherits the session's
# WAYLAND_DISPLAY. It only reads the bridge; it never touches bigscreen-bridge.service.
set -euo pipefail

HOST="${BIGSCREEN_HOST:-mediac@192.168.30.153}"
DEST=".local/share/bigscreen-status"
DESKTOP=".local/share/applications/bigscreen-status.desktop"
UNIT="bigscreen-status"
HERE="$(cd "$(dirname "$0")" && pwd)"

remote() { ssh -o BatchMode=yes "$HOST" "$@"; }

install() {
    # Only the app, its fixtures and launcher entry; no tools, screenshots or macOS metadata.
    COPYFILE_DISABLE=1 tar --no-xattrs -C "$HERE" --exclude ./tools --exclude ./screenshots --exclude '._*' \
        -cf - main.qml run.sh bigscreen-status.desktop components fixtures \
        | remote "rm -rf ~/$DEST && mkdir -p ~/$DEST ~/.local/share/applications && tar -C ~/$DEST -xf - \
            && sed \"s|@DIR@|\$HOME/$DEST|\" ~/$DEST/bigscreen-status.desktop > ~/$DESKTOP \
            && rm ~/$DEST/bigscreen-status.desktop \
            && (command -v update-desktop-database >/dev/null && update-desktop-database ~/.local/share/applications || true)"
    echo "Installed to $HOST:~/$DEST (launcher: Bridge Status)"
}

case "${1:-start}" in
install)
    install
    ;;
start)
    install
    shift || true
    args=""
    if [ $# -gt 0 ]; then args="$(printf '%q ' "$@")"; fi
    remote "\$HOME/$DEST/run.sh $args"
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
uninstall)
    remote "systemctl --user stop $UNIT 2>/dev/null || true; rm -rf ~/$DEST ~/$DESKTOP; \
        (command -v update-desktop-database >/dev/null && update-desktop-database ~/.local/share/applications || true)"
    echo "Removed $UNIT from $HOST"
    ;;
*)
    echo "usage: $0 install|start [args]|stop|screenshot [name]|logs [lines]|uninstall" >&2
    exit 2
    ;;
esac
