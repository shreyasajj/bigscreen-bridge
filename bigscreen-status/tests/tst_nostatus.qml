// A bridge that owns the name but has no Status interface (an older bridge).
import QtQuick
import QtTest
import "../components"

KeysCase {
    name: "NoStatusInterface"

    function test_a_bridge_without_status_is_reported() {
        openScreen()
        tryCompare(win, "link", "unreachable", 15000)
        verify(/Unknown(Interface|Method|Object)/.test(Ui.linkError), Ui.linkError)
        compare(win.sections.length, 1)
    }
}
