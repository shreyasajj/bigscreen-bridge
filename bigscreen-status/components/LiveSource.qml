// Reads the running bridge's read-only Status() on the session bus; no token, no writes.
// Re-reads on StatusChanged and every 10 s; a failed read reports the bridge as unreachable.
// Uses Plasma's own D-Bus QML module (plasma-workspace), so nothing has to be built.
import QtQuick
import org.kde.plasma.workspace.dbus as DBus

Item {
    id: source

    readonly property string service: "org.bigscreen.Bridge"
    readonly property string objectPath: "/org/bigscreen/Bridge"
    readonly property string iface: "org.bigscreen.Bridge.Status"

    signal loaded(var status)
    signal failed(string reason)

    property bool busy: false
    property bool again: false

    // The reply's string sits inside wrapper objects ({value: {value: "..."}}).
    function unwrap(v) {
        while (v !== null && typeof v === "object" && "value" in v)
            v = v.value
        return v
    }

    function read() {
        if (busy) { again = true; return }
        busy = true
        DBus.SessionBus.asyncCall({service: service, path: objectPath, iface: iface, member: "Status", arguments: []},
            reply => {
                finish()
                let status
                try {
                    status = JSON.parse(unwrap(reply))
                } catch (e) {
                    source.failed("Unreadable status reply")
                    return
                }
                source.loaded(status)
            },
            reply => {
                finish()
                const error = reply && reply.error
                source.failed(error && error.name ? error.name : "No reply")
            })
    }

    function finish() {
        busy = false
        if (again) { again = false; coalesce.restart() }
    }

    DBus.SignalWatcher {
        busType: DBus.BusType.Session
        service: source.service       // a well-known name is required for the match to work
        path: source.objectPath
        iface: source.iface
        function dbusStatusChanged() { coalesce.restart() }
    }

    // Read as soon as the bridge (re)appears instead of waiting for the next tick.
    DBus.DBusServiceWatcher {
        busType: DBus.BusType.Session
        watchedService: source.service
        onRegisteredChanged: coalesce.restart()
    }

    // Signals come at most twice a second; read once per burst.
    Timer { id: coalesce; interval: 150; onTriggered: source.read() }
    // Safety net, and the retry while the bridge is not running.
    Timer { interval: 10000; running: true; repeat: true; onTriggered: source.read() }

    Component.onCompleted: read()
}
