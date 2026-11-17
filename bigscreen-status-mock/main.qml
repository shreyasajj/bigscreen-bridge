// Bridge status — a read-only mock for Plasma Bigscreen, fed from fixtures/*.json.
// Remote: ◀ ▶ scenario · ▲ ▼ move · OK show data sources · Back close.
import QtQuick
import QtQuick.Layouts
import QtQuick.Window
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import "components"

QQC2.ApplicationWindow {
    id: window
    title: "Bridge status (mock)"
    visible: true
    visibility: Window.FullScreen
    width: 1920
    height: 1080
    color: Kirigami.Theme.backgroundColor

    property var scenarios: []
    property int current: 0
    property real loadedAt: Date.now()
    property real generatedMs: 0
    property string loadError: ""
    property int startCard: 0
    property bool startSources: false

    // Started from a systemd unit, the window has no activation token; ask KWin for focus
    // so the remote's keys reach the mock rather than the app underneath.
    onVisibleChanged: if (visible) requestActivate()
    onActiveChanged: console.info("mock window active:", active)

    readonly property var sections: [
        {file: "OverviewSection.qml"},
        {file: "AppSection.qml", app: "couchside"},
        {file: "AppSection.qml", app: "stremio"},
        {file: "AppSection.qml", app: "flimmer"},
        {file: "AppSection.qml", app: "vacuumtube"},
        {file: "MprisSection.qml"},
        {file: "CommandsSection.qml"},
        {file: "WarningsSection.qml"}
    ]

    Binding { target: Ui; property: "scale"; value: Math.max(0.6, window.height / 1080) }

    // Local files need QML_XHR_ALLOW_FILE_READ=1; the launch script sets it.
    function readJson(name) {
        const xhr = new XMLHttpRequest()
        xhr.open("GET", Qt.resolvedUrl("fixtures/" + name), false)
        xhr.send()
        if (!xhr.responseText)
            throw new Error("cannot read fixtures/" + name + " (is QML_XHR_ALLOW_FILE_READ=1 set?)")
        return JSON.parse(xhr.responseText)
    }

    function load(i) {
        if (!scenarios.length) return
        current = (i + scenarios.length) % scenarios.length
        try {
            const data = readJson(scenarios[current].file)
            generatedMs = Date.parse((data.diagnostics || {}).generated_at) || Date.now()
            loadedAt = Date.now()
            Ui.nowMs = generatedMs
            Ui.fixture = data
            loadError = ""
        } catch (e) {
            loadError = String(e)
        }
    }

    Component.onCompleted: {
        try {
            scenarios = readJson("index.json")
            // `qml6 main.qml -- <scenario>[:<card>[:sources]]` picks the starting view (for screenshots);
            // the remote takes over from there.
            const wanted = String(Qt.application.arguments[Qt.application.arguments.length - 1]).split(":")
            load(Math.max(0, scenarios.findIndex(s => s.id === wanted[0])))
            startCard = Math.max(0, Math.min(sections.length - 1, parseInt(wanted[1]) || 0))
            startSources = wanted[2] === "sources"
        } catch (e) {
            loadError = String(e)
        }
    }

    Timer {
        // Apply the starting card once the list has built its delegates.
        interval: 300
        running: true
        onTriggered: {
            window.requestActivate()
            list.currentIndex = window.startCard
            list.positionViewAtIndex(window.startCard, ListView.Contain)
            if (window.startSources && list.currentItem && list.currentItem.item)
                list.currentItem.item.expanded = true
            console.info("active:", window.active, "focus:", window.activeFocusItem)
        }
    }

    Timer {
        interval: 500
        running: true
        repeat: true
        onTriggered: Ui.nowMs = window.generatedMs + (Date.now() - window.loadedAt)
    }

    FocusScope {
        id: page
        anchors.fill: parent
        focus: true

        Keys.onPressed: event => {
            switch (event.key) {
            case Qt.Key_Left:
                window.load(window.current - 1); event.accepted = true; break
            case Qt.Key_Right:
                window.load(window.current + 1); event.accepted = true; break
            case Qt.Key_Escape:
            case Qt.Key_Back:
            case Qt.Key_Backspace: {
                // Back first folds an open "data sources" panel, then closes the mock.
                const card = list.currentItem && list.currentItem.item
                if (card && card.expanded) card.expanded = false
                else Qt.quit()
                event.accepted = true
                break
            }
            }
        }

        ColumnLayout {
            anchors.fill: parent
            anchors.leftMargin: Ui.pad * 2.5
            anchors.rightMargin: Ui.pad * 2.5
            anchors.topMargin: Ui.pad * 1.5
            anchors.bottomMargin: Ui.pad
            spacing: Ui.gap

            // Header: title, device, scenario switcher.
            RowLayout {
                Layout.fillWidth: true
                spacing: Ui.pad

                ColumnLayout {
                    spacing: 0
                    QQC2.Label {
                        text: "Bridge status"
                        font.pixelSize: Ui.title
                        font.weight: Font.Bold
                    }
                    QQC2.Label {
                        text: (Ui.snap.name || "") + "  ·  protocol " + (Ui.snap.protocol || "?")
                        font.pixelSize: Ui.small
                        color: Kirigami.Theme.disabledTextColor
                    }
                }

                Pill {
                    Layout.alignment: Qt.AlignTop
                    Layout.topMargin: Ui.gap
                    text: "MOCK DATA"
                    tone: Kirigami.Theme.neutralTextColor
                }

                Item { Layout.fillWidth: true }

                ColumnLayout {
                    spacing: Math.round(6 * Ui.scale)
                    RowLayout {
                        Layout.alignment: Qt.AlignRight
                        spacing: Ui.gap
                        Kirigami.Icon {
                            source: "go-previous"
                            implicitWidth: Ui.heading; implicitHeight: Ui.heading
                        }
                        QQC2.Label {
                            text: window.scenarios.length ? window.scenarios[window.current].title : "—"
                            font.pixelSize: Ui.heading
                            font.weight: Font.DemiBold
                        }
                        Kirigami.Icon {
                            source: "go-next"
                            implicitWidth: Ui.heading; implicitHeight: Ui.heading
                        }
                    }
                    RowLayout {
                        Layout.alignment: Qt.AlignRight
                        spacing: Math.round(8 * Ui.scale)
                        QQC2.Label {
                            text: (window.current + 1) + " / " + window.scenarios.length
                            font.pixelSize: Ui.small
                            color: Kirigami.Theme.disabledTextColor
                        }
                        Repeater {
                            model: window.scenarios.length
                            Rectangle {
                                required property int index
                                implicitWidth: Math.round(12 * Ui.scale); implicitHeight: implicitWidth
                                radius: width / 2
                                color: index === window.current ? Kirigami.Theme.highlightColor
                                                                : Kirigami.Theme.disabledTextColor
                                opacity: index === window.current ? 1 : 0.5
                            }
                        }
                    }
                }
            }

            QQC2.Label {
                Layout.fillWidth: true
                text: window.loadError || (window.scenarios.length ? window.scenarios[window.current].summary : "")
                color: window.loadError ? Kirigami.Theme.negativeTextColor : Kirigami.Theme.textColor
                font.pixelSize: Ui.body
                elide: Text.ElideRight
            }

            ListView {
                id: list
                Layout.fillWidth: true
                Layout.fillHeight: true
                focus: true
                clip: true
                spacing: Ui.gap * 1.25
                model: window.sections
                keyNavigationEnabled: true
                highlightMoveDuration: Kirigami.Units.longDuration
                highlightRangeMode: ListView.ApplyRange
                preferredHighlightBegin: Ui.pad
                preferredHighlightEnd: height - Ui.pad * 2
                topMargin: Ui.focusWidth * 2
                bottomMargin: Ui.focusWidth * 2
                leftMargin: Ui.focusWidth * 2
                rightMargin: Ui.focusWidth * 2

                delegate: Loader {
                    required property var modelData
                    required property int index
                    width: ListView.view.width - ListView.view.leftMargin - ListView.view.rightMargin
                    Component.onCompleted: setSource(Qt.resolvedUrl("components/" + modelData.file),
                                                     modelData.app ? {appId: modelData.app} : {})
                }
            }

            // Remote hints.
            RowLayout {
                Layout.fillWidth: true
                spacing: Ui.pad * 1.5
                Repeater {
                    model: [["go-previous", "go-next", "Scenario"], ["go-up", "go-down", "Move"],
                            ["", "", "OK  Data sources"], ["", "", "Back  Close"]]
                    RowLayout {
                        required property var modelData
                        spacing: Math.round(6 * Ui.scale)
                        Kirigami.Icon { visible: modelData[0] !== ""; source: modelData[0]; implicitWidth: Ui.body; implicitHeight: Ui.body }
                        Kirigami.Icon { visible: modelData[1] !== ""; source: modelData[1]; implicitWidth: Ui.body; implicitHeight: Ui.body }
                        QQC2.Label { text: modelData[2]; font.pixelSize: Ui.small; color: Kirigami.Theme.disabledTextColor }
                    }
                }
                Item { Layout.fillWidth: true }
                QQC2.Label {
                    text: "Read-only · fixture " + (window.scenarios.length ? window.scenarios[window.current].file : "")
                    font.pixelSize: Ui.small
                    color: Kirigami.Theme.disabledTextColor
                }
            }
        }
    }
}
