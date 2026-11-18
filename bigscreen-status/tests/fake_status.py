"""Stand-in for the bridge's session-bus status service, for the Bridge Status key tests.

Owns org.bigscreen.Bridge and serves a fixture through Status(), announces StatusChanged
every 300 ms, and logs each Status and StartPairing call to --calls, one per line.
With --no-status it owns the name but exports only Pairing, like a bridge from before
the Status interface.
"""
import argparse
import asyncio
import json
from pathlib import Path

from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method, signal

PATH = "/org/bigscreen/Bridge"


class Status(ServiceInterface):
    def __init__(self, payload, log):
        super().__init__("org.bigscreen.Bridge.Status")
        self.payload, self.log = payload, log

    @method()
    def Status(self) -> "s":  # noqa: F821
        self.log("Status")
        return self.payload

    @signal()
    def StatusChanged(self):
        return None


class Pairing(ServiceInterface):
    def __init__(self, log):
        super().__init__("org.bigscreen.Bridge.Pairing")
        self.log = log

    @method()
    def StartPairing(self):
        self.log("StartPairing")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--calls", type=Path, required=True)
    parser.add_argument("--no-status", action="store_true")
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text())
    payload = json.dumps({"snapshot": {k: v for k, v in fixture.items() if k not in ("diagnostics", "pairing")},
                          "diagnostics": fixture.get("diagnostics"), "pairing": fixture.get("pairing")})

    def log(line):
        with args.calls.open("a") as calls:
            calls.write(line + "\n")

    bus = await MessageBus().connect()
    status = Status(payload, log)
    if not args.no_status:
        bus.export(PATH, status)
    bus.export(PATH, Pairing(log))
    await bus.request_name("org.bigscreen.Bridge")
    Path(str(args.calls) + ".ready").touch()
    while True:
        await asyncio.sleep(0.3)
        if not args.no_status:
            status.StatusChanged()


asyncio.run(main())
