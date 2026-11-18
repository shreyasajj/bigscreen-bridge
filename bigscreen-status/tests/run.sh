#!/bin/sh
# Run the Bridge Status key tests, each file on its own private session bus:
#   tst_live.qml, tst_live_back.qml  against the stand-in bridge (fake_status.py)
#   tst_nostatus.qml                 against a stand-in without the Status interface
#   tst_absent.qml                   with no bridge at all
#   tst_fixtures.qml                 fixture mode (no bus needed)
# A file passes only if every one of its test functions ran and passed.
# Needs qmltestrunner, Plasma's QML modules and python3-dbus-next (see Dockerfile).
set -eu
here=$(cd "$(dirname "$0")" && pwd)
export QT_QPA_PLATFORM=offscreen QML_XHR_ALLOW_FILE_READ=1 QT_QUICK_BACKEND=software
calls=/tmp/bigscreen-status-calls
runner=$(command -v qmltestrunner6 || command -v qmltestrunner || echo /usr/lib/qt6/bin/qmltestrunner)
status=0

run() { # test file, stand-in mode: live | no-status | none
    file=$1 mode=$2
    : > "$calls"
    rm -f "$calls.ready"
    expected=$(( $(grep -c 'function test_' "$here/$file") + 2 ))   # plus initTestCase/cleanupTestCase
    # shellcheck disable=SC2016 # the inner script gets its values as arguments
    out=$(dbus-run-session -- sh -c '
        here=$1 file=$2 mode=$3 calls=$4 runner=$5
        if [ "$mode" != none ]; then
            flag=; [ "$mode" = no-status ] && flag=--no-status
            python3 "$here/fake_status.py" --fixture "$here/../fixtures/healthy.json" --calls "$calls" $flag &
            i=0; until [ -e "$calls.ready" ]; do i=$((i + 1)); [ $i -lt 100 ] || exit 3; sleep 0.1; done
        fi
        "$runner" -input "$here/$file" 2>&1
    ' sh "$here" "$file" "$mode" "$calls" "$runner") || true
    echo "$out" | grep -E '^(PASS|FAIL|SKIP|XFAIL|QWARN|Totals)|^   ' || true
    passed=$(echo "$out" | sed -n 's/^Totals: \([0-9]*\) passed, \([0-9]*\) failed.*/\1 \2/p' | tail -n 1)
    if [ "${passed% *}" = "$expected" ] && [ "${passed#* }" = 0 ]; then
        echo "ok   $file ($expected)"
    else
        echo "FAIL $file: expected $expected passed, got '${passed:-no totals}'"
        echo "$out" | tail -n 40
        status=1
    fi
}

run tst_live.qml live
run tst_live_back.qml live
run tst_nostatus.qml no-status
run tst_absent.qml none
run tst_fixtures.qml none
exit "$status"
