// Pairing: the code Home Assistant needs, shown until something pairs. OK asks for a new one.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Card {
    id: root
    readonly property var code: Ui.pairing
    readonly property var summary: Ui.diag.pairing || ({})
    readonly property real secondsLeft: code ? Math.max(0, (Date.parse(code.expires_at) - Ui.nowMs) / 1000) : 0
    readonly property bool showing: !!code && secondsLeft > 0

    heading: "Pairing"
    headingDetail: (summary.paired_clients || 0) + ((summary.paired_clients === 1) ? " device paired" : " devices paired")
    // Live mode only: fixtures cannot change anything.
    activate: Ui.startPairing ? function() { Ui.startPairing() } : null

    // A code to type into Home Assistant.
    RowLayout {
        Layout.fillWidth: true
        visible: root.showing
        spacing: Ui.pad * 2

        QQC2.Label {
            text: root.code ? root.code.code : ""
            font.pixelSize: Math.round(110 * Ui.scale)
            font.weight: Font.Bold
            font.family: "monospace"
            font.letterSpacing: Math.round(8 * Ui.scale)
            color: Kirigami.Theme.highlightColor
        }
        ColumnLayout {
            Layout.fillWidth: true
            spacing: Math.round(6 * Ui.scale)
            QQC2.Label {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: "In Home Assistant: Settings → Devices & services → Bigscreen Bridge, then enter this code."
                font.pixelSize: Ui.body
            }
            QQC2.Label {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: (root.code && root.code.reason === "unpaired" ? "Nothing has paired yet.  " : "")
                    + "Expires in " + Ui.clock(root.secondsLeft) + " · works once · each device gets its own token"
                font.pixelSize: Ui.small
                color: Kirigami.Theme.disabledTextColor
            }
        }
    }

    // No code on screen.
    RowLayout {
        Layout.fillWidth: true
        visible: !root.showing
        spacing: Ui.pad
        QQC2.Label {
            Layout.fillWidth: true
            text: root.code ? "Getting a new code…"
                : root.summary.last_paired_name
                  ? "Last paired: " + root.summary.last_paired_name + ", " + Ui.ago(root.summary.last_paired_at)
                  : "No pairing code active"
            font.pixelSize: Ui.body
        }
        QQC2.Label {
            text: Ui.startPairing ? "OK  New pairing code" : "Read-only fixture"
            font.pixelSize: Ui.body
            font.weight: Font.DemiBold
            color: Ui.startPairing ? Kirigami.Theme.highlightColor : Kirigami.Theme.disabledTextColor
        }
    }
}
