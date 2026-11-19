// No bridge on the session bus.
import QtQuick
import QtTest
import "../components"

KeysCase {
    name: "NoBridge"

    function test_no_bridge_shows_only_the_bridge_card() {
        openScreen()
        tryCompare(win, "link", "unreachable", 15000)
        verify(Ui.offline)
        compare(win.sections.length, 1)
        press(Qt.Key_Down, 2)
        compare(cards.currentIndex, 0)
        verify(Ui.linkError.length > 0)
    }
}
