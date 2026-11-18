// Bridge status: a 10-foot status screen for Plasma Bigscreen; its only write is a new pairing code.
// Live (default): reads org.bigscreen.Bridge.Status on the session bus.
// Fixtures:       `-- --fixtures [scenario[:card[:sources]]]` reads fixtures/*.json instead.
// Remote: ▲ ▼ move · OK show data sources · Back close · ◀ ▶ scenario (fixtures only).
import QtQuick
import QtQuick.Layouts
import QtQuick.Window
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import "components"

QQC2.ApplicationWindow {
    id: window
    title: "Bridge status"
    visible: true
    visibility: Window.FullScreen
    width: 1920
    height: 1080
    color: Kirigami.Theme.backgroundColor

    // Everything after `--` on the qml6 command line is ours; decided before anything loads.
    readonly property var ourArgs: {
        const args = Qt.application.arguments
        return args.indexOf("--") >= 0 ? args.slice(args.indexOf("--") + 1) : []
    }
    readonly property bool live: ourArgs[0] !== "--fixtures"
    property var scenarios: []
    property int current: 0
    property real loadedAt: Date.now()
    property real generatedMs: 0
    property string loadError: ""
    property int startCard: 0
    property bool startSources: false
    // Live only: "connecting" until the first answer, then "live" or "unreachable".
    property string link: "connecting"
    property real lastReadMs: 0
    // Behind another app nothing here is seen: the clock stops and the bridge is not
    // read. Coming back redraws at once with a fresh read (KWin reports the switch
    // through the application state: Active in front, Inactive behind another window).
    readonly property bool foreground: Qt.application.state === Qt.ApplicationActive
    onForegroundChanged: {
        console.info("Bridge status", foreground ? "in front: resuming" : "in the background: paused")
        if (foreground) tick()
    }

    function tick() {
        Ui.nowMs = window.generatedMs + (Date.now() - window.loadedAt)
        Ui.wallMs = Date.now()
    }

    // Started from a systemd unit, the window has no activation token; ask KWin for focus
    // so the remote's keys reach this window rather than the app underneath.
    onVisibleChanged: if (visible) requestActivate()

    readonly property var allSections: [
        {file: "OverviewSection.qml"},
        {file: "PairingSection.qml"},
        {file: "AppSection.qml", app: "couchside"},
        {file: "AppSection.qml", app: "stremio"},
        {file: "AppSection.qml", app: "flimmer"},
        {file: "AppSection.qml", app: "vacuumtube"},
        {file: "MprisSection.qml"},
        {file: "CommandsSection.qml"},
        {file: "WarningsSection.qml"}
    ]
    // Without a bridge there is nothing current to show beyond the Bridge card.
    readonly property var sections: Ui.offline ? allSections.slice(0, 1) : allSections

    Binding { target: Ui; property: "scale"; value: Math.max(0.6, window.height / 1080) }
    Binding { target: Ui; property: "offline"; value: window.live && window.link !== "live" }
    Binding { target: Ui; property: "link"; value: window.live ? window.link : "fixture" }

    function show(data) {
        generatedMs = Date.parse((data.diagnostics || {}).generated_at) || Date.now()
        loadedAt = Date.now()
        Ui.nowMs = generatedMs
        Ui.fixture = data
    }

    // Fixture mode only. Local files need QML_XHR_ALLOW_FILE_READ=1; the launch script sets it.
    function readJson(name) {
        const xhr = new XMLHttpRequest()
        xhr.open("GET", Qt.resolvedUrl("fixtures/" + name), false)
        xhr.send()
        if (!xhr.responseText)
            throw new Error("cannot read fixtures/" + name + " (is QML_XHR_ALLOW_FILE_READ=1 set?)")
        return JSON.parse(xhr.responseText)
    }

    function load(i) {
        if (live || !scenarios.length) return
        current = (i + scenarios.length) % scenarios.length
        try {
            show(readJson(scenarios[current].file))
            loadError = ""
        } catch (e) {
            loadError = String(e)
        }
    }

    Component.onCompleted: {
        const ours = ourArgs
        const view = String((live ? ours[0] : ours[1]) || "").split(":")
        if (live) view.unshift("")
        startCard = Math.max(0, Math.min(allSections.length - 1, parseInt(view[1]) || 0))
        startSources = view[2] === "sources"
        if (live) return
        try {
            scenarios = readJson("index.json")
            load(Math.max(0, scenarios.findIndex(s => s.id === view[0])))
        } catch (e) {
            loadError = String(e)
        }
    }

    Loader {
        active: window.live
        source: "components/LiveSource.qml"
        onLoaded: {
            item.paused = Qt.binding(() => !window.foreground)
            item.loaded.connect(status => {
                window.link = "live"
                Ui.linkError = ""
                window.lastReadMs = Date.now()
                window.loadError = ""
                window.show(Object.assign({}, status.snapshot,
                                          {diagnostics: status.diagnostics, pairing: status.pairing}))
            })
            Ui.startPairing = () => item.startPairing()
            item.failed.connect(reason => {
                // Never leave the last answer on screen as if it were current.
                window.link = "unreachable"
                window.loadError = reason
                Ui.linkError = reason
                Ui.fixture = ({})
            })
        }
    }

    Timer {
        // Apply the starting card once the list has built its delegates.
        interval: 300
        running: true
        onTriggered: {
            window.requestActivate()
            list.currentIndex = Math.min(window.startCard, list.count - 1)
            list.positionViewAtIndex(list.currentIndex, ListView.Contain)
            if (window.startSources && list.currentItem && list.currentItem.item)
                list.currentItem.item.expanded = true
        }
    }

    Timer {
        // Ages and the pairing countdown; only while someone can see them.
        interval: 500
        running: window.foreground
        repeat: true
        onTriggered: window.tick()
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
                // Back first folds an open "data sources" panel, then closes the screen.
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

            // Header: title, device, and either the live link or the scenario switcher.
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
                        text: Ui.offline ? "bigscreen-bridge.service"
                                         : (Ui.snap.name || "") + "  ·  protocol " + (Ui.snap.protocol || "?")
                        font.pixelSize: Ui.small
                        color: Kirigami.Theme.disabledTextColor
                    }
                }

                Pill {
                    Layout.alignment: Qt.AlignTop
                    Layout.topMargin: Ui.gap
                    text: !window.live ? "MOCK DATA" : window.link === "live" ? "LIVE" : "NO BRIDGE"
                    tone: !window.live ? Kirigami.Theme.neutralTextColor
                        : window.link === "live" ? Kirigami.Theme.positiveTextColor : Kirigami.Theme.negativeTextColor
                }

                Item { Layout.fillWidth: true }

                // Live: when the bridge last answered.
                ColumnLayout {
                    visible: window.live
                    spacing: Math.round(6 * Ui.scale)
                    QQC2.Label {
                        Layout.alignment: Qt.AlignRight
                        text: window.link === "live" ? "Updated " + Ui.span((Ui.wallMs - window.lastReadMs) / 1000) + " ago"
                            : window.link === "connecting" ? "Connecting…" : "Retrying every 10 s"
                        font.pixelSize: Ui.heading
                        font.weight: Font.DemiBold
                    }
                    QQC2.Label {
                        Layout.alignment: Qt.AlignRight
                        text: "org.bigscreen.Bridge.Status"
                        font.pixelSize: Ui.small
                        color: Kirigami.Theme.disabledTextColor
                    }
                }

                // Fixtures: the scenario switcher.
                ColumnLayout {
                    visible: !window.live
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
                visible: text !== ""
                text: window.live ? "" : window.loadError
                    || (window.scenarios.length ? window.scenarios[window.current].summary : "")
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
                    model: (window.live ? [] : [["go-previous", "go-next", "Scenario"]])
                           .concat([["go-up", "go-down", "Move"], ["", "", "OK  Data sources"], ["", "", "Back  Close"]])
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
                    text: window.live ? "Session bus, no token · only the pairing code can change"
                        : "Read-only · fixture " + (window.scenarios.length ? window.scenarios[window.current].file : "")
                    font.pixelSize: Ui.small
                    color: Kirigami.Theme.disabledTextColor
                }
            }
        }
    }
}
