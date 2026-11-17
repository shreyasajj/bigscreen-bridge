// One focus stop on the page. Enter shows where each value comes from.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import org.kde.bigscreen as Bigscreen

Item {
    id: card
    property string heading: ""
    property string headingDetail: ""
    property string sources: ""
    property bool expanded: false
    // When set, OK runs this instead of showing the data sources.
    property var activate: null
    default property alias content: body.data

    focus: true
    implicitHeight: column.implicitHeight + 2 * Ui.pad

    // Keep a card whose sources panel just opened entirely on screen, once its layout has grown.
    // The parent is the list's Loader.
    onExpandedChanged: if (expanded) reveal.restart()
    Timer {
        id: reveal
        interval: 60
        onTriggered: {
            const loader = card.parent, view = loader && loader.ListView.view
            if (!view) return
            const bottom = loader.y + loader.height + view.bottomMargin + Ui.gap - view.height
            if (view.contentY < bottom) view.contentY = bottom
        }
    }

    Keys.onPressed: event => {
        if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter || event.key === Qt.Key_Select) {
            if (card.activate) card.activate()
            else card.expanded = !card.expanded
            event.accepted = true
        }
    }

    Bigscreen.DelegateBackground {
        anchors.fill: parent
        control: card
        highlighted: card.activeFocus
        alternateBackgroundColor: true
    }

    Rectangle {
        anchors.fill: parent
        anchors.margins: -Ui.focusWidth
        radius: Kirigami.Units.cornerRadius + Ui.focusWidth
        color: "transparent"
        border.width: Ui.focusWidth
        border.color: Kirigami.Theme.highlightColor
        visible: card.activeFocus
    }

    ColumnLayout {
        id: column
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: Ui.pad }
        spacing: Ui.gap

        RowLayout {
            visible: card.heading !== ""
            Layout.fillWidth: true
            spacing: Ui.gap
            QQC2.Label {
                text: card.heading
                font.pixelSize: Ui.heading
                font.weight: Font.DemiBold
            }
            QQC2.Label {
                Layout.fillWidth: true
                text: card.headingDetail
                font.pixelSize: Ui.small
                color: Kirigami.Theme.disabledTextColor
                elide: Text.ElideRight
            }
        }

        ColumnLayout {
            id: body
            Layout.fillWidth: true
            spacing: Ui.gap
        }

        QQC2.Label {
            visible: card.expanded
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: card.sources
            textFormat: Text.StyledText
            font.pixelSize: Ui.small
            color: Kirigami.Theme.disabledTextColor
        }
    }
}
