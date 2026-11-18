// Shared by the tst_*.qml key tests: opens the real main.qml in a chosen mode and gives
// the tests its card list, the focused card and the stand-in bridge's call log.
import QtQuick
import QtQuick.Window
import QtTest

TestCase {
    id: root
    when: windowShown
    property var args: []
    property var win: null
    property var cards: null
    // Written by fake_status.py, one line per Status / StartPairing call.
    readonly property string callsFile: "/tmp/bigscreen-status-calls"

    function openScreen() {
        const component = Qt.createComponent(Qt.resolvedUrl("../main.qml"))
        compare(component.status, Component.Ready, component.errorString())
        win = component.createObject(null, {ourArgs: args})
        verify(win, "main.qml did not open")
        win.requestActivate()
        tryVerify(() => win.active, 5000, "the screen never became the active window")
        cards = findChild(win.contentItem, "cards")
        verify(cards, "no card list")
        wait(500)   // the start timer places the first card after 300 ms
    }

    function cleanup() {
        if (win) {
            win.destroy()
            win = null
        }
    }

    function card() {
        return cards.currentItem && cards.currentItem.item
    }

    function press(key, times) {
        for (let i = 0; i < (times || 1); i++)
            keyClick(key)
    }

    function calls(what) {
        const xhr = new XMLHttpRequest()
        xhr.open("GET", "file://" + callsFile, false)
        xhr.send()
        return xhr.responseText.split("\n").filter(line => line === what).length
    }
}
