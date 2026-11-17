// One application: its hook, how the bridge follows it, and what it is playing.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Card {
    id: root
    property string appId: ""
    readonly property var app: Ui.app(appId)
    readonly property var hook: Ui.hook(appId)
    readonly property bool owner: app.playback_owner === "music_assistant"
    readonly property bool reachable: hook ? hook.reachable === true : app.control_available === true
    readonly property bool ready: owner ? (hook ? hook.ready === true : false) : app.control_available === true
    readonly property var media: app.media || null
    // MPRIS players are listed in the snapshot only when no native hook owns them.
    readonly property var player: (Ui.snap.sessions || []).find(s => s.app === appId && String(s.id).startsWith("mpris:")) || null
    readonly property color good: Kirigami.Theme.positiveTextColor
    readonly property color warn: Kirigami.Theme.neutralTextColor
    readonly property color bad: Kirigami.Theme.negativeTextColor
    readonly property color off: Kirigami.Theme.disabledTextColor

    heading: Ui.appName(appId)
    headingDetail: hook ? hook.endpoint : ""
    sources: "<b>Snapshot:</b> apps[" + appId + "].installed, running, "
           + (owner ? "playback_owner, ma_player_id, view; sessions[] (Sendspin)" : "hook_installed, control_available, state, media, view")
           + "<br><b>Diagnostics:</b> hooks." + appId + ".reachable, ready, mode, poll_reason, signals_received, "
           + "last_signal_at, last_read_at, last_read_ok, consecutive_failures, last_error"
           + (appId === "flimmer" ? ", catalogue_loading" : "")

    function stateIcon(state) {
        return ({playing: "media-playback-start", paused: "media-playback-pause", buffering: "view-refresh",
                 idle: "media-playback-stop", error: "dialog-error"})[state] || "media-playback-stop"
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: Ui.pad * 1.5

        // Left: lifecycle and how the bridge follows the hook.
        ColumnLayout {
            Layout.preferredWidth: Math.round(820 * Ui.scale)
            Layout.maximumWidth: Math.round(820 * Ui.scale)
            Layout.alignment: Qt.AlignTop
            spacing: Ui.gap

            Flow {
                Layout.fillWidth: true
                spacing: Ui.gap / 1.5
                Pill { text: "Installed"; tone: root.app.installed ? root.good : root.bad }
                Pill { text: "Window"; tone: root.app.running ? root.good : root.off }
                Pill { text: "Endpoint"; tone: root.reachable ? root.good : root.app.running ? root.bad : root.off }
                Pill { text: "Ready"; tone: root.ready ? root.good : root.app.running ? root.warn : root.off }
                Pill {
                    visible: Ui.snap.active_app === root.appId
                    text: "Focused"
                    tone: Kirigami.Theme.highlightColor
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Ui.gap
                Pill {
                    dot: false
                    text: !root.hook ? "—"
                        : root.hook.mode === "pushed" ? "Pushed · " + (root.hook.signal_source === "mpris" ? "MPRIS" : "StateChanged")
                        : "Polled · 2 s"
                    tone: !root.hook ? root.off : root.hook.mode === "pushed" ? root.good : root.warn
                }
                QQC2.Label {
                    Layout.fillWidth: true
                    visible: !!root.hook
                    font.pixelSize: Ui.small
                    elide: Text.ElideRight
                    color: Kirigami.Theme.disabledTextColor
                    text: !root.hook ? "" :
                        ({never_signalled: "never signalled · ", not_ready: "not ready · ", not_running: "not running · "}[root.hook.poll_reason] || "")
                        + "signal " + Ui.ago(root.hook.last_signal_at)
                        + "  ·  read " + Ui.ago(root.hook.last_read_at)
                        + (root.hook.last_read_ok === false && root.hook.consecutive_failures ? " (failed ×" + root.hook.consecutive_failures + ")" : "")
                }
            }

            RowLayout {
                spacing: Ui.gap
                QQC2.Label {
                    text: "Last error"
                    font.pixelSize: Ui.small
                    color: Kirigami.Theme.disabledTextColor
                }
                Pill {
                    visible: !!(root.hook && root.hook.last_error)
                    dot: false
                    text: root.hook && root.hook.last_error ? root.hook.last_error.code : ""
                    tone: root.bad
                }
                QQC2.Label {
                    font.pixelSize: Ui.small
                    color: Kirigami.Theme.disabledTextColor
                    text: root.hook && root.hook.last_error ? Ui.ago(root.hook.last_error.at) : "none"
                }
            }
        }

        // Right: playback.
        RowLayout {
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignTop
            spacing: Ui.gap

            readonly property string state: root.owner ? (root.player ? root.player.state : "idle")
                                          : root.media ? (root.media.state || "idle")
                                          : root.player ? root.player.state : ""

            Kirigami.Icon {
                Layout.alignment: Qt.AlignTop
                implicitWidth: Math.round(56 * Ui.scale)
                implicitHeight: implicitWidth
                visible: parent.state !== ""
                source: root.stateIcon(parent.state)
                color: parent.state === "playing" ? root.good : Kirigami.Theme.textColor
                isMask: true
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: Math.round(6 * Ui.scale)

                QQC2.Label {
                    id: titleLabel
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                    font.pixelSize: Ui.body
                    font.weight: Font.DemiBold
                    text: {
                        if (!root.app.running && !root.player) return "Not running"
                        if (root.owner) return root.player ? root.player.media.title : "Music Assistant idle"
                        if (root.appId === "flimmer" && root.media) {
                            const ch = root.media.channel
                            if (ch) return ch.number + "  " + ch.name
                            const h = root.hook
                            return h && h.catalogue_loading ? "Loading channel catalogue…" : "No channel selected"
                        }
                        if (root.media) return root.media.title || "Nothing loaded"
                        if (root.player) return root.player.media.title
                        return "No control endpoint"
                    }
                    color: (!root.app.running && !root.player) ? root.off : Kirigami.Theme.textColor
                }

                QQC2.Label {
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                    font.pixelSize: Ui.small
                    color: Kirigami.Theme.disabledTextColor
                    visible: text !== ""
                    text: {
                        const m = root.media, h = root.hook
                        if (root.owner)
                            return root.app.running || root.player ? "Played by Music Assistant · " + (root.app.ma_player_id || "no local player")
                                 + (root.app.view ? " · UI: " + root.app.view.type.replace("_", " ") : "") : ""
                        if (root.appId === "flimmer" && m) {
                            const ch = m.channel
                            if (ch) return "Now " + ch.current.title + " " + Ui.hhmm(ch.current.start) + "–" + Ui.hhmm(ch.current.stop)
                                         + "   ·   Next " + ch.next.title + " " + Ui.hhmm(ch.next.start)
                            return h && h.catalogue_loading ? "0 channels · loading for " + Ui.span(Ui.seconds(h.catalogue_loading_since)) : ""
                        }
                        if (m && m.episode) return m.episode
                        if (!m && root.player) return "From " + root.player.name + " MPRIS only — native hook not answering"
                        return ""
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Ui.gap
                    readonly property var src: root.media || (root.player ? root.player.media : null)
                    readonly property real pos: Ui.position(src && root.media ? root.media
                                               : src ? Object.assign({state: root.player.state}, src) : null)
                    visible: !!src && typeof src.duration === "number" && src.duration > 0
                    Bar {
                        Layout.fillWidth: true
                        value: parent.src && parent.src.duration ? parent.pos / parent.src.duration : 0
                    }
                    QQC2.Label {
                        font.pixelSize: Ui.small
                        font.features: {"tnum": 1}
                        text: parent.src ? Ui.clock(parent.pos) + " / " + Ui.clock(parent.src.duration) : ""
                    }
                }
            }
        }
    }
}
