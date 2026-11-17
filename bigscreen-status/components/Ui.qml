// Shared sizes, the current data (live or fixture) and the screen's clock.
pragma Singleton
import QtQuick

QtObject {
    // 1.0 at 1080p; set by main.qml from the window height.
    property real scale: 1
    readonly property int title: Math.round(46 * scale)
    readonly property int heading: Math.round(33 * scale)
    readonly property int body: Math.round(27 * scale)
    readonly property int small: Math.round(22 * scale)
    readonly property int pad: Math.round(26 * scale)
    readonly property int gap: Math.round(16 * scale)
    readonly property int focusWidth: Math.max(3, Math.round(5 * scale))

    // Bridge.snapshot() with the bridge's diagnostics under "diagnostics", from Status() or a fixture.
    property var fixture: ({})
    readonly property var snap: fixture
    readonly property var diag: fixture.diagnostics || ({})

    // "live", "connecting", "unreachable" or "fixture"; offline hides everything but the Bridge card.
    property string link: "fixture"
    property bool offline: false
    property string linkError: ""     // the D-Bus error name of the last failed read
    // The name answers but the Status interface is missing: an older bridge is running.
    readonly property bool outdated: /UnknownInterface|UnknownMethod|UnknownObject/.test(linkError)

    // The data's own generated_at advanced by real seconds since it arrived, so ages tick.
    property real nowMs: 0
    property real wallMs: Date.now()

    readonly property var appNames: ({couchside: "Couchside", stremio: "Stremio TV",
                                      flimmer: "Flimmer", vacuumtube: "VacuumTube"})

    function appName(id) {
        return id ? (appNames[id] || id) : "—"
    }

    function app(id) {
        return (snap.apps || []).find(a => a.id === id) || ({})
    }

    function hook(id) {
        return (diag.hooks || {})[id] || null
    }

    function seconds(iso) {
        return iso ? (nowMs - Date.parse(iso)) / 1000 : NaN
    }

    function span(s) {
        if (isNaN(s)) return "never"
        s = Math.max(0, s)
        if (s < 10) return s.toFixed(1) + " s"
        if (s < 90) return Math.round(s) + " s"
        if (s < 5400) return Math.round(s / 60) + " min"
        const h = Math.floor(s / 3600)
        return h + " h " + Math.round((s - h * 3600) / 60) + " min"
    }

    function ago(iso) {
        return iso ? span(seconds(iso)) + " ago" : "never"
    }

    function clock(s) {
        if (typeof s !== "number" || isNaN(s)) return "--:--"
        s = Math.max(0, Math.floor(s))
        const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, sec = s % 60
        const mm = (h ? String(m).padStart(2, "0") : String(m)) + ":" + String(sec).padStart(2, "0")
        return h ? h + ":" + mm : mm
    }

    function millis(ms) {
        if (typeof ms !== "number") return ""
        return ms < 1000 ? ms + " ms" : (ms / 1000).toFixed(1) + " s"
    }

    // Playback position advanced from its UTC anchor, as bridge clients interpolate it.
    function position(media) {
        if (!media || typeof media.position !== "number") return NaN
        let p = media.position
        if (media.state === "playing" && media.updated_at)
            p += seconds(media.updated_at)
        return typeof media.duration === "number" ? Math.min(p, media.duration) : p
    }

    function hhmm(unixSeconds) {
        return Qt.formatTime(new Date(unixSeconds * 1000), "hh:mm")
    }
}
