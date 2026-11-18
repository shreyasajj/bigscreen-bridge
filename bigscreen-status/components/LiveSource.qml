// Reads the running bridge's read-only Status() on the session bus; no token, no writes.
// Re-reads on StatusChanged and every 10 s; a failed read reports the bridge as unreachable.
// While paused (the screen is behind another app) it reads nothing and only notes that
// something changed; resuming reads once straight away.
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
    property bool paused: false
    onPausedChanged: if (!paused) read()

    // Signals, the bridge (re)appearing and the safety-net tick all come through here.
    function changed() {
        if (!paused) coalesce.restart()
    }

    // The reply's string sits inside wrapper objects ({value: {value: "..."}}).
    function unwrap(v) {
        while (v !== null && typeof v === "object" && "value" in v)
            v = v.value
        return v
    }

    function read() {
        if (paused) return
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

    // The one write: ask for a new pairing code, then show it.
    function startPairing() {
        DBus.SessionBus.asyncCall({service: service, path: objectPath, iface: "org.bigscreen.Bridge.Pairing",
                                   member: "StartPairing", arguments: []},
            reply => source.changed(),
            reply => source.failed(reply && reply.error && reply.error.name ? reply.error.name : "No reply"))
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
        function dbusStatusChanged() { source.changed() }
    }

    // Read as soon as the bridge (re)appears instead of waiting for the next tick.
    DBus.DBusServiceWatcher {
        busType: DBus.BusType.Session
        watchedService: source.service
        onRegisteredChanged: source.changed()
    }

    // Signals come at most twice a second; read once per burst.
    Timer { id: coalesce; interval: 150; onTriggered: source.read() }
    // Safety net, and the retry while the bridge is not running.
    Timer { interval: 10000; running: !source.paused; repeat: true; onTriggered: source.read() }

    Component.onCompleted: read()
}
