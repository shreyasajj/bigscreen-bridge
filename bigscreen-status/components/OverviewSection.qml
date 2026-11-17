// Bridge service, its links, and the TV itself.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Card {
    id: root
    readonly property var d: Ui.diag
    readonly property var service: d.service || ({})
    readonly property var ws: d.websocket || ({})
    readonly property var disc: d.discovery || ({})
    readonly property var mqtt: d.mqtt || ({})
    readonly property var kwin: d.kwin || ({})
    readonly property var display: ((Ui.snap.power || {}).display) || ({})
    readonly property var audio: Ui.snap.audio || ({})
    readonly property color good: Kirigami.Theme.positiveTextColor
    readonly property color warn: Kirigami.Theme.neutralTextColor
    readonly property color bad: Kirigami.Theme.negativeTextColor
    readonly property color off: Kirigami.Theme.disabledTextColor

    heading: "Bridge"
    headingDetail: Ui.offline ? "bigscreen-bridge.service"
                 : "bigscreen-bridge.service · " + (Ui.snap.name || "") + " · " + (Ui.snap.device_id || "").slice(0, 8)
    sources: "<b>Snapshot:</b> available, active_app, power.display, audio.volume, audio.muted<br>"
           + "<b>Diagnostics:</b> service.started_at, service.health, websocket.clients, websocket.last_disconnected_at, "
           + "discovery.announced, mqtt.connected, mqtt.last_error, kwin.windows"

    // Live mode without an answer: say so instead of showing anything stale.
    RowLayout {
        Layout.fillWidth: true
        visible: Ui.offline
        spacing: Ui.pad
        Kirigami.Icon {
            source: Ui.link === "connecting" ? "view-refresh" : "dialog-error"
            implicitWidth: Math.round(72 * Ui.scale)
            implicitHeight: implicitWidth
            color: Ui.link === "connecting" ? root.off : root.bad
            isMask: true
        }
        ColumnLayout {
            Layout.fillWidth: true
            spacing: Math.round(4 * Ui.scale)
            QQC2.Label {
                text: Ui.link === "connecting" ? "Connecting to the bridge…"
                    : Ui.outdated ? "Bridge has no Status interface" : "Bridge not running"
                font.pixelSize: Ui.heading
                font.weight: Font.DemiBold
                color: Ui.link === "connecting" ? Kirigami.Theme.textColor : root.bad
            }
            QQC2.Label {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: (Ui.outdated
                       ? "org.bigscreen.Bridge is running but is older than this screen; install the current bridge. "
                       : "org.bigscreen.Bridge did not answer on the session bus. ")
                    + "Retrying every 10 s, and at once when it starts. Check: systemctl --user status bigscreen-bridge"
                    + (Ui.linkError ? "  (" + Ui.linkError + ")" : "")
                font.pixelSize: Ui.small
                color: Kirigami.Theme.disabledTextColor
            }
        }
    }

    GridLayout {
        visible: !Ui.offline
        Layout.fillWidth: true
        columns: 6
        columnSpacing: Ui.gap
        rowSpacing: Ui.gap

        Tile {
            label: "Service"
            value: root.service.started_at ? "Running" : "Unknown"
            detail: root.service.started_at ? "up " + Ui.span(Ui.seconds(root.service.started_at)) : ""
            tone: root.service.started_at ? root.good : root.off
        }
        Tile {
            readonly property bool ok: Ui.snap.available === true && root.service.health === "ok"
            label: "Health"
            value: ok ? "OK" : "Degraded"
            detail: Ui.snap.available ? "KWin snapshot ready" : "No KWin snapshot"
            tone: ok ? root.good : root.bad
        }
        Tile {
            label: "Protocol"
            value: "v" + (Ui.snap.protocol || "?")
            detail: "bridge " + (root.service.version || "?")
        }
        Tile {
            readonly property int n: root.ws.clients || 0
            label: "Home Assistant"
            value: n === 0 ? "Disconnected" : n + (n === 1 ? " client" : " clients")
            detail: n === 0 ? "left " + Ui.ago(root.ws.last_disconnected_at)
                            : "for " + Ui.span(Ui.seconds(root.ws.last_connected_at))
            tone: n === 0 ? root.bad : root.good
        }
        Tile {
            label: "Discovery"
            value: !root.disc.enabled ? "Disabled" : root.disc.announced ? "Announced" : "Not announced"
            detail: root.disc.error || (root.disc.announced ? "mDNS · port " + (root.service.port || "") : "")
            tone: !root.disc.enabled ? root.off : root.disc.announced ? root.good : root.warn
        }
        Tile {
            label: "MQTT"
            value: !root.mqtt.configured ? "Not configured" : root.mqtt.connected ? "Online" : "Offline"
            detail: !root.mqtt.configured ? "" : root.mqtt.connected ? (root.mqtt.broker || "")
                  : (root.mqtt.last_error || "error") + " · retry " + root.mqtt.retry_seconds + " s · ×" + root.mqtt.failures
            tone: !root.mqtt.configured ? root.off : root.mqtt.connected ? root.good : root.bad
        }

        Tile {
            Layout.columnSpan: 2
            label: "Display"
            value: root.display.display_on === true ? "On" : root.display.display_on === false ? "Off (DPMS)" : "Unknown"
            detail: Object.keys(root.display.outputs || {}).map(k => k + " " + root.display.outputs[k]).join(" · ")
            tone: root.display.display_on === true ? root.good : root.display.display_on === false ? root.warn : root.off
        }
        Tile {
            Layout.columnSpan: 2
            label: "Volume"
            value: root.audio.available ? Math.round(root.audio.volume * 100) + " %" + (root.audio.muted ? " · muted" : "") : "Unavailable"
            detail: root.audio.available ? (root.audio.streams || []).length + " audio streams" : ""
            tone: !root.audio.available ? root.off : root.audio.muted ? root.warn : Kirigami.Theme.textColor
            Bar {
                Layout.fillWidth: true
                Layout.topMargin: Math.round(4 * Ui.scale)
                visible: root.audio.available === true
                value: root.audio.volume || 0
                tone: root.audio.muted ? root.off : Kirigami.Theme.highlightColor
            }
        }
        Tile {
            Layout.columnSpan: 2
            label: "Focused (KWin)"
            value: Ui.snap.active_app ? Ui.appName(Ui.snap.active_app) : "Other window"
            detail: (root.kwin.windows !== undefined ? root.kwin.windows + " windows · " : "")
                  + "reported " + Ui.ago(root.kwin.last_report_at)
            tone: Ui.snap.active_app ? Kirigami.Theme.highlightColor : root.off
        }
    }
}
