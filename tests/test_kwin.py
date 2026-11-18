"""Loading scripts into KWin."""
from pathlib import Path
from types import SimpleNamespace

from dbus_next import MessageType

from bigscreen_bridge.kwin import KWin


class Bus:
    """Records messages; loadScript answers with a script number another script may already use."""

    def __init__(self):
        self.sent = []

    async def call(self, message):
        self.sent.append((message.path, message.member))
        body = [4] if message.member == "loadScript" else []
        return SimpleNamespace(message_type=MessageType.METHOD_RETURN, body=body, error_name=None)

    async def introspect(self, *args):
        raise AssertionError("a script's own object path must not be used")


async def test_scripts_are_started_through_scripting_not_their_own_path():
    kwin = KWin(Path("/tmp/monitor.js"))
    kwin.bus = Bus()
    await kwin.run(Path("/tmp/monitor.js"), "bigscreen-bridge-monitor")
    # KWin numbers a script by how many are loaded, so after an unload a new script can be
    # given the number, and /Scripting/ScriptN path, of one that is still running.
    assert kwin.bus.sent == [("/Scripting", "loadScript"), ("/Scripting", "start")]
