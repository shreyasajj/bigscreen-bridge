import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Rectangle {
    id: pill
    property string text: ""
    property color tone: Kirigami.Theme.disabledTextColor
    property bool dot: true

    implicitWidth: row.implicitWidth + 2 * Ui.gap
    implicitHeight: label.implicitHeight + Ui.gap * 0.7
    radius: height / 2
    color: Qt.rgba(tone.r, tone.g, tone.b, 0.16)
    border.width: Math.max(1, Math.round(2 * Ui.scale))
    border.color: Qt.rgba(tone.r, tone.g, tone.b, 0.6)

    RowLayout {
        id: row
        anchors.centerIn: parent
        spacing: Ui.gap / 2
        Rectangle {
            visible: pill.dot
            implicitWidth: Math.round(Ui.small * 0.55)
            implicitHeight: implicitWidth
            radius: width / 2
            color: pill.tone
        }
        QQC2.Label {
            id: label
            text: pill.text
            font.pixelSize: Ui.small
            color: Kirigami.Theme.textColor
        }
    }
}
