"""Token-free, read-only status for local screens, on the bridge's own session-bus name.

`org.bigscreen.Bridge.Status` sits beside the KWin receiver at `/org/bigscreen/Bridge`.
It has one method and one signal, and neither changes the TV, an app or the bridge:

  Status() -> s     JSON {"snapshot": <Bridge.snapshot()>, "diagnostics": <diagnostics>,
                          "pairing": {"code", "reason", "expires_at"} | null}
  StatusChanged()   no data; at most about twice a second; re-read Status() after it

`org.bigscreen.Bridge.Pairing.StartPairing()` is separate and is the only call
that changes anything: it replaces the pairing code shown on the TV.

The session bus is private to the logged-in user, who can already read the bridge's
config and token, so this exposes nothing beyond that boundary.
"""

import asyncio
import logging
import time

from dbus_next.service import ServiceInterface, method, signal

LOG = logging.getLogger(__name__)
INTERFACE = "org.bigscreen.Bridge.Status"
PATH = "/org/bigscreen/Bridge"
PAIRING_INTERFACE = "org.bigscreen.Bridge.Pairing"


class StatusInterface(ServiceInterface):
    def __init__(self, read):
        super().__init__(INTERFACE)
        self.read = read

    @method()
    def Status(self) -> "s":
        return self.read()

    @signal()
    def StatusChanged(self):
        return None


class PairingInterface(ServiceInterface):
    """The one write on the session bus: ask for a new pairing code to show on the TV.

    It cannot pair anything itself; a client still has to type the code shown on screen.
    """

    def __init__(self, start):
        super().__init__(PAIRING_INTERFACE)
        self.start = start

    @method()
    def StartPairing(self):
        self.start()


class StatusSignal:
    """Coalesce change notifications into at most one signal per `interval`."""

    def __init__(self, emit, interval=0.5, monotonic=time.monotonic):
        self.emit, self.interval, self.monotonic = emit, interval, monotonic
        self.last = None
        self.pending = None

    def __call__(self):
        if self.pending is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # nothing to schedule on outside the bridge's loop
        delay = 0 if self.last is None else max(0, self.last + self.interval - self.monotonic())
        # Never emit from inside the code that reported the change.
        self.pending = loop.call_later(delay, self._fire)

    def _fire(self):
        self.pending = None
        self.last = self.monotonic()
        try:
            self.emit()
        except Exception:
            LOG.debug("StatusChanged emission failed", exc_info=True)
