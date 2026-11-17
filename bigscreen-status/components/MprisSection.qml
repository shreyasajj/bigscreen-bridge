// Every MPRIS player on the session bus and the app it was attributed to.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Card {
    id: root
    // Diagnostics list every player; the snapshot only those no native hook owns.
    readonly property var rows: Ui.diag.mpris
        || (Ui.snap.sessions || []).filter(s => String(s.id).startsWith("mpris:"))
             .map(s => ({bus_name: s.bus_name, identity: s.name, app: s.app, state: s.state, role: s.role, superseded_by: null}))

    heading: "MPRIS players"
    headingDetail: rows.length + " on the session bus"
    sources: "<b>Snapshot:</b> sessions[] holds only players no native hook owns<br>"
           + "<b>Diagnostics:</b> mpris[] lists every player seen, with superseded_by naming the hook that owns it"

    Repeater {
        model: root.rows
        delegate: RowLayout {
            required property var modelData
            Layout.fillWidth: true
            spacing: Ui.pad
            ColumnLayout {
                Layout.preferredWidth: Math.round(720 * Ui.scale)
                Layout.maximumWidth: Math.round(720 * Ui.scale)
                spacing: 0
                QQC2.Label { text: modelData.identity; font.pixelSize: Ui.body; font.weight: Font.DemiBold }
                QQC2.Label {
                    Layout.fillWidth: true
                    text: modelData.bus_name
                    elide: Text.ElideMiddle
                    font.pixelSize: Ui.small
                    color: Kirigami.Theme.disabledTextColor
                }
            }
            QQC2.Label {
                Layout.preferredWidth: Math.round(260 * Ui.scale)
                text: "→ " + (modelData.app ? Ui.appName(modelData.app) : "unattributed")
                font.pixelSize: Ui.body
            }
            Pill {
                text: modelData.state
                tone: modelData.state === "playing" ? Kirigami.Theme.positiveTextColor : Kirigami.Theme.disabledTextColor
            }
            QQC2.Label {
                Layout.fillWidth: true
                elide: Text.ElideRight
                font.pixelSize: Ui.small
                color: Kirigami.Theme.disabledTextColor
                text: modelData.role === "music_assistant" ? "Music Assistant owns playback"
                    : modelData.superseded_by ? "change signal only · control via " + modelData.superseded_by
                    : "used for session control"
            }
        }
    }
}
