import QtQuick
import org.kde.kirigami as Kirigami

Rectangle {
    property real value: 0      // 0..1
    property color tone: Kirigami.Theme.highlightColor
    implicitHeight: Math.max(4, Math.round(8 * Ui.scale))
    radius: height / 2
    color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.15)
    Rectangle {
        width: parent.width * Math.max(0, Math.min(1, parent.value || 0))
        height: parent.height
        radius: parent.radius
        color: parent.tone
    }
}
