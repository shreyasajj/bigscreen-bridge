// LedFx and HyperHDR as the bridge reads them (snapshot.lights). Read-only here.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Card {
    id: root
    readonly property var lights: Ui.snap.lights || ({})
    readonly property var ledfx: lights.ledfx || ({})
    readonly property var hyper: lights.hyperhdr || ({})
    readonly property var playing: (ledfx.virtuals || []).filter(v => v.active && v.effect)
    readonly property var running: (hyper.instances || []).filter(i => i.running)

    function serviceTone(section) {
        return section.available ? Kirigami.Theme.positiveTextColor
             : section.service === "active" ? Kirigami.Theme.neutralTextColor : Kirigami.Theme.negativeTextColor
    }
    function serviceText(section) {
        if (section.available) return "Running"
        if (section.service === undefined) return "Not reported"
        return section.service === "active" ? "Not answering" : "Service " + section.service
    }

    heading: "Lights"
    headingDetail: root.lights.ledfx ? "LedFx · HyperHDR" : "not reported by this bridge"
    sources: "<b>Snapshot:</b> lights.ledfx (version, paused, virtuals[], devices, scenes[]) and "
           + "lights.hyperhdr (output, grabber, instances[], last_error), read every 5 s from their local APIs; "
           + "service state from systemd"

    // LedFx
    RowLayout {
        Layout.fillWidth: true
        spacing: Ui.pad
        QQC2.Label {
            Layout.preferredWidth: Math.round(260 * Ui.scale)
            text: "LedFx"; font.pixelSize: Ui.body; font.weight: Font.DemiBold
        }
        Pill { text: root.serviceText(root.ledfx); tone: root.serviceTone(root.ledfx) }
        Pill {
            visible: !!root.ledfx.available
            text: root.ledfx.paused ? "Paused" : "Playing"
            tone: root.ledfx.paused ? Kirigami.Theme.disabledTextColor : Kirigami.Theme.positiveTextColor
        }
        Pill {
            visible: !!root.ledfx.devices
            readonly property var d: root.ledfx.devices || ({})
            text: (d.online || 0) + "/" + (d.total || 0) + " devices online"
            tone: d.online === d.total ? Kirigami.Theme.positiveTextColor : Kirigami.Theme.negativeTextColor
        }
        QQC2.Label {
            Layout.fillWidth: true
            elide: Text.ElideRight
            font.pixelSize: Ui.small
            color: Kirigami.Theme.disabledTextColor
            text: !root.ledfx.available ? "" : (root.ledfx.devices && root.ledfx.devices.offline.length
                    ? "offline: " + root.ledfx.devices.offline.join(", ") + " · " : "")
                  + "v" + (root.ledfx.version || "?")
                  + ((root.ledfx.scenes || []).some(s => s.active)
                     ? " · scene " + root.ledfx.scenes.filter(s => s.active).map(s => s.name).join(", ") : "")
        }
    }
    Flow {
        Layout.fillWidth: true
        visible: root.playing.length > 0
        spacing: Ui.gap
        Repeater {
            model: root.playing.slice(0, 8)
            delegate: Pill {
                required property var modelData
                text: modelData.name + " · " + modelData.effect_name
                     + (modelData.brightness !== null && modelData.brightness !== undefined
                        ? " · " + Math.round(modelData.brightness * 100) + " %" : "")
                tone: Kirigami.Theme.highlightColor
            }
        }
        QQC2.Label {
            visible: root.playing.length > 8
            text: "+" + (root.playing.length - 8) + " more"
            font.pixelSize: Ui.small
            color: Kirigami.Theme.disabledTextColor
        }
    }

    // HyperHDR
    RowLayout {
        Layout.fillWidth: true
        spacing: Ui.pad
        QQC2.Label {
            Layout.preferredWidth: Math.round(260 * Ui.scale)
            text: "HyperHDR"; font.pixelSize: Ui.body; font.weight: Font.DemiBold
        }
        Pill { text: root.serviceText(root.hyper); tone: root.serviceTone(root.hyper) }
        Pill {
            visible: !!root.hyper.available
            text: root.hyper.output ? "Output on" : "Output off"
            tone: root.hyper.output ? Kirigami.Theme.positiveTextColor : Kirigami.Theme.disabledTextColor
        }
        Pill {
            visible: !!root.hyper.available
            text: root.running.length + "/" + (root.hyper.instances || []).length + " instances running"
            tone: root.running.length ? Kirigami.Theme.positiveTextColor : Kirigami.Theme.disabledTextColor
        }
        QQC2.Label {
            Layout.fillWidth: true
            elide: Text.ElideRight
            font.pixelSize: Ui.small
            color: Kirigami.Theme.disabledTextColor
            text: !root.hyper.available ? "" : (root.running.map(i => i.name).join(", ") || "none running")
                  + (root.hyper.grabber ? " · grabber " + root.hyper.grabber : "")
        }
    }
    QQC2.Label {
        Layout.fillWidth: true
        visible: !!root.hyper.last_error
        text: "Last error: " + (root.hyper.last_error || "")
        elide: Text.ElideRight
        font.pixelSize: Ui.small
        color: Kirigami.Theme.neutralTextColor
    }
}
