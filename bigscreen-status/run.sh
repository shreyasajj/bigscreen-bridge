#!/bin/sh
# Start the Bridge status screen inside the Plasma session as its own user unit.
# One instance: starting it again replaces the running one (and brings it to the front).
#   run.sh                         live, from org.bigscreen.Bridge.Status
#   run.sh 2:sources               live, starting on card 2 with its data sources open
#   run.sh --fixtures [scenario[:card[:sources]]]   fixture mode
here="$(dirname "$(readlink -f "$0")")"
systemctl --user stop bigscreen-status 2>/dev/null
systemctl --user reset-failed bigscreen-status 2>/dev/null
# Fixture mode reads JSON with XMLHttpRequest, which needs file reads enabled.
exec systemd-run --user --quiet --collect --unit=bigscreen-status --description="Bridge status screen" \
    --setenv=QML_XHR_ALLOW_FILE_READ=1 qml6 "$here/main.qml" -- "$@"
