// Recent bridge warnings, newest first.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Card {
    id: root
    readonly property var rows: (Ui.diag.warnings || []).slice().reverse()

    heading: "Recent warnings"
    headingDetail: rows.length ? "" : "none"
    sources: "<b>Diagnostics only:</b> warnings[] — at, level, logger, message, count. The last 50 distinct "
           + "bigscreen_bridge log lines at INFO and above; the full journal is journalctl --user -u bigscreen-bridge."

    QQC2.Label {
        visible: root.rows.length === 0
        text: "No warnings"
        font.pixelSize: Ui.body
        color: Kirigami.Theme.disabledTextColor
    }

    Repeater {
        model: root.rows
        delegate: RowLayout {
            required property var modelData
            Layout.fillWidth: true
            spacing: Ui.pad
            QQC2.Label {
                Layout.preferredWidth: Math.round(170 * Ui.scale)
                text: Ui.ago(modelData.at)
                font.pixelSize: Ui.small
                color: Kirigami.Theme.disabledTextColor
            }
            Pill {
                dot: false
                text: modelData.level
                tone: modelData.level === "ERROR" ? Kirigami.Theme.negativeTextColor
                    : modelData.level === "WARNING" ? Kirigami.Theme.neutralTextColor : Kirigami.Theme.disabledTextColor
            }
            QQC2.Label {
                Layout.fillWidth: true
                elide: Text.ElideRight
                font.pixelSize: Ui.body
                text: modelData.message + (modelData.count > 1 ? "   ×" + modelData.count : "")
            }
            QQC2.Label {
                text: modelData.logger.replace("bigscreen_bridge.", "")
                font.pixelSize: Ui.small
                color: Kirigami.Theme.disabledTextColor
            }
        }
    }
}
