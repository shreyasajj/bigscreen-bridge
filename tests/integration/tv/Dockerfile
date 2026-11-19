# A Plasma Bigscreen "TV" for integration tests: KDE neon packages on Ubuntu 24.04,
# systemd as PID 1 and a lingering user `tv` whose session is a headless KWin
# (`--virtual`, software rendering) running the Bigscreen shell.
# Run privileged: docker run --privileged --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw

# `plasma`: the KDE neon desktop libraries and shell, shared with the status-screen tests.
FROM ubuntu:24.04 AS plasma
ARG DEBIAN_FRONTEND=noninteractive
# The neon archive is often slow from runners: retry downloads rather than fail the build.
RUN echo 'Acquire::Retries "5"; Acquire::http::Timeout "60";' > /etc/apt/apt.conf.d/80retries \
 && apt-get update && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
 && curl -fsSL --retry 5 --retry-all-errors --connect-timeout 30 -o /tmp/neon.key https://archive.neon.kde.org/public.key \
 && gpg --dearmor -o /usr/share/keyrings/neon.gpg /tmp/neon.key && rm /tmp/neon.key \
 && echo "deb [signed-by=/usr/share/keyrings/neon.gpg] http://archive.neon.kde.org/user noble main" \
      > /etc/apt/sources.list.d/neon.list \
 && apt-get update && apt-get install -y --no-install-recommends \
      dbus dbus-user-session kwin-wayland plasma-workspace plasma-bigscreen xwayland \
      libgl1-mesa-dri libegl-mesa0 fonts-noto-core breeze breeze-icon-theme qml6-module-qtquick-controls \
 && rm -rf /var/lib/apt/lists/*

# `tv`: systemd, audio, Flatpak, and what install.sh needs.
FROM plasma AS tv
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      systemd systemd-sysv pipewire pipewire-pulse wireplumber flatpak bubblewrap \
      xdg-desktop-portal xdg-desktop-portal-kde python3 python3-venv jq \
 && rm -rf /var/lib/apt/lists/* \
 && systemctl mask getty@tty1.service systemd-networkd-wait-online.service
RUN userdel -r ubuntu 2>/dev/null; useradd -m -u 1000 -s /bin/bash tv \
 && (getent group render >/dev/null || groupadd -r render) && usermod -aG video,render tv \
 && mkdir -p /var/lib/systemd/linger && touch /var/lib/systemd/linger/tv
COPY session.sh /usr/local/bin/tv-session
COPY tv-session.service /etc/systemd/user/tv-session.service
RUN chmod 755 /usr/local/bin/tv-session && systemctl --global enable tv-session.service
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
