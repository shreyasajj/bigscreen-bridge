#!/bin/sh
# The TV's graphical session, as a user unit of `tv`: KWin on a virtual 1080p output,
# the Bigscreen shell inside it, and the Wayland environment handed to the user manager
# so units it starts (the bridge, apps) reach the same display.
# Ready when $XDG_RUNTIME_DIR/tv-ready exists.
set -eu
rm -f "$XDG_RUNTIME_DIR/tv-ready"
# Bigscreen's own session environment. It sources /etc/profile, which is not -u clean.
set +u
# shellcheck disable=SC1091
. plasma-bigscreen-common-env
set -u
export QT_QUICK_BACKEND=software LIBGL_ALWAYS_SOFTWARE=1 XDG_SESSION_TYPE=wayland \
       XDG_CURRENT_DESKTOP=KDE KDE_FULL_SESSION=true WAYLAND_DISPLAY=wayland-0
# The runner has no GPU. KWin's virtual backend needs a render device and crashes when a
# window is drawn without one, so KWin runs windowed inside Xvfb with software OpenGL.
Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp &
for _ in $(seq 50); do [ -S /tmp/.X11-unix/X99 ] && break; sleep 0.1; done
DISPLAY=:99 kwin_wayland --x11-display :99 --width 1920 --height 1080 --socket wayland-0 --no-lockscreen \
    --xwayland > "$XDG_RUNTIME_DIR/kwin.log" 2>&1 &
kwin=$!
for _ in $(seq 120); do
    busctl --user status org.kde.KWin >/dev/null 2>&1 && [ -S "$XDG_RUNTIME_DIR/wayland-0" ] && break
    kill -0 "$kwin" || { echo "kwin_wayland exited" >&2; exit 1; }
    sleep 1
done
busctl --user status org.kde.KWin >/dev/null
systemctl --user import-environment WAYLAND_DISPLAY XDG_SESSION_TYPE XDG_CURRENT_DESKTOP KDE_FULL_SESSION \
    QT_QUICK_BACKEND LIBGL_ALWAYS_SOFTWARE PLASMA_PLATFORM QT_FILE_SELECTORS PLASMA_DEFAULT_SHELL
plasmashell -p org.kde.plasma.bigscreen --no-respawn &
touch "$XDG_RUNTIME_DIR/tv-ready"
echo "TV session ready on $WAYLAND_DISPLAY"
wait "$kwin"
