// Fixture mode: ◀ ▶ step through the recorded scenarios.
import QtQuick
import QtTest

KeysCase {
    name: "Fixtures"
    args: ["--fixtures", "healthy"]

    function init() {
        openScreen()
        verify(win.scenarios.length > 1)
    }

    function test_right_and_left_cycle_and_wrap() {
        compare(win.current, 0)
        press(Qt.Key_Right)
        compare(win.current, 1)
        press(Qt.Key_Left, 2)
        compare(win.current, win.scenarios.length - 1)
        press(Qt.Key_Right)
        compare(win.current, 0)
    }

    function test_every_scenario_loads() {
        for (let i = 0; i < win.scenarios.length; i++) {
            compare(win.loadError, "", win.scenarios[win.current].id)
            press(Qt.Key_Right)
        }
        compare(win.current, 0)
    }

    function test_pairing_is_read_only_in_fixtures() {
        press(Qt.Key_Down)
        press(Qt.Key_Return)
        verify(card().expanded, "without a bridge, OK on Pairing shows its sources")
    }
}
