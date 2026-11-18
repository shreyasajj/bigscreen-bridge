// Recent requests, newest first.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Card {
    id: root
    readonly property var rows: (Ui.diag.commands || []).slice().reverse().slice(0, 9)
    readonly property int failures: rows.filter(r => r.ok === false).length

    heading: "Recent commands"
    headingDetail: rows.length ? rows.length + " shown · " + failures + " failed" : ""
    sources: "<b>Diagnostics only:</b> commands[] — id, command, app, transport, started_at, finished_at, ok, error_code, "
           + "duration_ms. The last 50 requests from Bridge.handle(); content IDs, URIs and search text are never kept."

    QQC2.Label {
        visible: root.rows.length === 0
        text: "No commands since the bridge started"
        font.pixelSize: Ui.body
        color: Kirigami.Theme.disabledTextColor
    }

    Repeater {
        model: root.rows
        delegate: RowLayout {
            required property var modelData
            readonly property bool running: modelData.ok === null
            Layout.fillWidth: true
            spacing: Ui.pad
            QQC2.Label {
                Layout.preferredWidth: Math.round(170 * Ui.scale)
                text: running ? "now" : Ui.ago(modelData.finished_at)
                font.pixelSize: Ui.small
                color: Kirigami.Theme.disabledTextColor
            }
            QQC2.Label {
                Layout.preferredWidth: Math.round(170 * Ui.scale)
                text: modelData.id.slice(0, 8)
                font.family: "monospace"
                font.pixelSize: Ui.small
                color: Kirigami.Theme.disabledTextColor
            }
            QQC2.Label {
                Layout.preferredWidth: Math.round(260 * Ui.scale)
                text: modelData.command
                font.pixelSize: Ui.body
                font.weight: Font.DemiBold
            }
            QQC2.Label {
                Layout.preferredWidth: Math.round(260 * Ui.scale)
                text: modelData.app ? Ui.appName(modelData.app) : "system"
                font.pixelSize: Ui.body
                color: modelData.app ? Kirigami.Theme.textColor : Kirigami.Theme.disabledTextColor
            }
            Item {
                Layout.preferredWidth: Math.round(330 * Ui.scale)
                implicitHeight: resultPill.implicitHeight
                Pill {
                    id: resultPill
                    dot: false
                    text: running ? "running…" : modelData.ok ? "OK" : modelData.error_code
                    tone: running ? Kirigami.Theme.neutralTextColor
                        : modelData.ok ? Kirigami.Theme.positiveTextColor : Kirigami.Theme.negativeTextColor
                }
            }
            QQC2.Label {
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignRight
                font.pixelSize: Ui.small
                font.features: {"tnum": 1}
                text: running ? Ui.span(Ui.seconds(modelData.started_at)) : Ui.millis(modelData.duration_ms)
                color: Kirigami.Theme.disabledTextColor
            }
        }
    }
}
