// Back on a folded card closes the screen. Alone in its file: the screen calls Qt.quit().
import QtQuick
import QtTest

KeysCase {
    name: "LiveBack"

    function test_back_closes_the_screen() {
        openScreen()
        tryCompare(win, "link", "live", 5000)
        const spy = createTemporaryObject(spyComponent, win.contentItem, {target: win})
        verify(!card().expanded)
        press(Qt.Key_Escape)
        compare(spy.count, 1)
    }

    Component {
        id: spyComponent
        SignalSpy { signalName: "closeRequested" }
    }
}
