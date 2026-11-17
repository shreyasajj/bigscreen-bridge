"""Announce the bridge on the local network through the system Avahi daemon.

Only the address and a stable device identity are published; pairing still needs
the private token. Nothing is announced while the bridge listens on loopback.
"""
import ipaddress
import logging

from .adapters import BridgeError
from .runtime_io import call

LOG = logging.getLogger(__name__)
SERVICE_TYPE = "_bigscreen-bridge._tcp"
AVAHI = "org.freedesktop.Avahi"


def is_loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def service_record(config, hostname):
    """Instance name and TXT entries; `None` when the bridge is not reachable from the network."""
    if is_loopback(config.get("host", "127.0.0.1")) or config.get("discovery", True) is False:
        return None
    name = str(config.get("name") or hostname)[:40]
    text = {"device_id": config["device_id"], "name": name, "protocol": "1"}
    # Instance names must be unique on the link; the device ID keeps two TVs apart.
    return {"instance": f"{name} ({config['device_id'][:8]})", "port": config.get("port", 8765),
            "txt": [f"{key}={value}".encode() for key, value in text.items()]}


async def advertise(system_bus, config, hostname):
    """Register the service; it disappears by itself when the bus connection closes."""
    record = service_record(config, hostname)
    if record is None:
        return None
    try:
        group = (await call(system_bus, AVAHI, "/", AVAHI + ".Server", "EntryGroupNew"))[0]
        # interface -1 and protocol -1: every interface, IPv4 and IPv6; empty host and domain: ours.
        await call(system_bus, AVAHI, group, AVAHI + ".EntryGroup", "AddService", "iiussssqaay",
                   [-1, -1, 0, record["instance"], SERVICE_TYPE, "", "", record["port"], record["txt"]])
        await call(system_bus, AVAHI, group, AVAHI + ".EntryGroup", "Commit")
    except BridgeError as exc:
        LOG.warning("Network discovery is unavailable: %s", exc)
        return None
    LOG.info("Announced %s on the local network", SERVICE_TYPE)
    return group
