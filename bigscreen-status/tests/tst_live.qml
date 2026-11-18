// Live mode against a stand-in bridge: every remote key.
import QtQuick
import QtTest

KeysCase {
    name: "Live"

    function init() {
        openScreen()
        tryCompare(win, "link", "live", 5000)
    }

    function test_all_cards_are_shown() {
        compare(win.sections.length, 10)
        compare(cards.count, 10)
    }

    function test_down_and_up_move_between_cards() {
        compare(cards.currentIndex, 0)
        press(Qt.Key_Down, 3)
        compare(cards.currentIndex, 3)
        press(Qt.Key_Up)
        compare(cards.currentIndex, 2)
    }

    function test_ok_opens_the_data_sources() {
        verify(!card().expanded)
        press(Qt.Key_Return)
        verify(card().expanded)
        press(Qt.Key_Return)
        verify(!card().expanded)
    }

    function test_ok_on_pairing_asks_the_bridge_for_a_new_code() {
        press(Qt.Key_Down)
        compare(cards.currentIndex, 1)
        const before = calls("StartPairing")
        press(Qt.Key_Return)
        tryVerify(() => calls("StartPairing") === before + 1, 3000, "StartPairing was not called")
        verify(!card().expanded, "Pairing runs its action instead of showing sources")
    }

    function test_left_and_right_do_nothing_live() {
        press(Qt.Key_Right)
        press(Qt.Key_Left)
        compare(win.current, 0)
        compare(win.link, "live")
    }

    function test_back_folds_open_sources_first() {
        const spy = createTemporaryObject(spyComponent, root, {target: win})
        press(Qt.Key_Return)
        verify(card().expanded)
        press(Qt.Key_Escape)
        verify(!card().expanded)
        compare(spy.count, 0, "Back with sources open must not close the screen")
    }

    function test_behind_another_app_it_stops_reading_and_reads_on_return() {
        tryVerify(() => calls("Status") > 0)
        win.hide()
        tryVerify(() => !win.foreground, 3000)
        const paused = calls("Status")
        wait(1500)   // the stand-in signals StatusChanged every 300 ms
        compare(calls("Status"), paused, "read while in the background")
        win.show()
        win.requestActivate()
        tryVerify(() => win.foreground, 3000)
        tryVerify(() => calls("Status") > paused, 2000, "no fresh read on return")
    }

    Component {
        id: spyComponent
        SignalSpy { signalName: "closeRequested" }
    }
}
