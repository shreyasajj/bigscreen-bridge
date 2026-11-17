// A labelled value in the overview.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Rectangle {
    id: tile
    property string label: ""
    property string value: ""
    property string detail: ""
    property color tone: Kirigami.Theme.textColor
    default property alias extra: column.data

    Layout.fillWidth: true
    Layout.preferredWidth: 1
    implicitHeight: column.implicitHeight + 2 * Ui.gap
    radius: Kirigami.Units.cornerRadius
    color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.05)

    Rectangle {
        anchors { left: parent.left; top: parent.top; bottom: parent.bottom; margins: Ui.gap * 0.6 }
        width: Math.max(3, Math.round(5 * Ui.scale))
        radius: width / 2
        color: tile.tone
    }

    ColumnLayout {
        id: column
        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter }
        anchors.leftMargin: Ui.gap * 1.6
        anchors.rightMargin: Ui.gap
        spacing: Math.round(4 * Ui.scale)
        QQC2.Label {
            text: tile.label
            font.pixelSize: Ui.small
            color: Kirigami.Theme.disabledTextColor
        }
        QQC2.Label {
            Layout.fillWidth: true
            text: tile.value
            font.pixelSize: Ui.heading
            font.weight: Font.DemiBold
            color: tile.tone
            elide: Text.ElideRight
        }
        QQC2.Label {
            Layout.fillWidth: true
            visible: tile.detail !== ""
            text: tile.detail
            font.pixelSize: Ui.small
            color: Kirigami.Theme.disabledTextColor
            elide: Text.ElideRight
        }
    }
}
