# The Bridge Status key tests: Plasma's QML modules from the TV image's `plasma` stage,
# plus Qt Quick Test and dbus-next for the stand-in bridge. Build the base first:
#   docker build --target plasma -t bigscreen-plasma tests/integration/tv
#   docker build -t bigscreen-status-test bigscreen-status/tests
#   docker run --rm -v "$PWD/bigscreen-status:/status:ro" bigscreen-status-test
ARG BASE=bigscreen-plasma
FROM ${BASE}
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      qt6-declarative-dev qml6-module-qttest python3 python3-dbus-next dbus \
 && rm -rf /var/lib/apt/lists/*
RUN useradd -m tester
USER tester
CMD ["sh", "/status/tests/run.sh"]
