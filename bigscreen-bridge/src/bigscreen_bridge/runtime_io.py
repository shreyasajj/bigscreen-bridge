"""Bounded system I/O for fixed bridge operations, never client-supplied commands."""
import asyncio
import os

from dbus_next import Message, MessageType, Variant

from .adapters import BridgeError


def unwrap(value):
    if isinstance(value, Variant):
        return unwrap(value.value)
    if isinstance(value, dict):
        return {key: unwrap(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [unwrap(item) for item in value]
    return value


async def call(bus, destination, path, interface, member, signature='', body=None, timeout=5):
    if bus is None:
        raise BridgeError('service_unavailable', 'Required system service is unavailable')
    try:
        reply = await asyncio.wait_for(bus.call(Message(destination=destination, path=path,
            interface=interface, member=member, signature=signature, body=body or [])), timeout)
    except (OSError, EOFError, TimeoutError, ConnectionError) as exc:
        raise BridgeError('service_unavailable', 'System service did not respond') from exc
    if reply is None or reply.message_type == MessageType.ERROR:
        name = reply.error_name if reply else ''
        if any(word in name for word in ('AccessDenied', 'NotAuthorized', 'InteractiveAuthorization', 'AuthFailed')):
            raise BridgeError('authorization_required', 'The system did not authorize this operation')
        raise BridgeError('system_error', 'System operation failed: ' + (name or 'no_response'))
    return unwrap(reply.body)


async def properties(bus, destination, path, interface):
    return (await call(bus, destination, path, 'org.freedesktop.DBus.Properties', 'GetAll', 's', [interface]))[0]


async def run(*argv, env=None, timeout=5):
    # Callers construct fixed argument lists; no network field selects an executable.
    process = await asyncio.create_subprocess_exec(*argv, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout)
    except TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise BridgeError('command_timeout', 'System operation timed out') from exc
    if process.returncode or len(stdout) > 2 * 1024 * 1024:
        raise BridgeError('system_error', 'System operation failed')
    return stdout.decode(errors='replace')


async def desktop_environment(bus):
    env = dict(os.environ)
    try:
        values = (await call(bus, 'org.freedesktop.systemd1', '/org/freedesktop/systemd1',
            'org.freedesktop.DBus.Properties', 'Get', 'ss',
            ['org.freedesktop.systemd1.Manager', 'Environment']))[0]
        allowed = {'WAYLAND_DISPLAY', 'DISPLAY', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS',
                   'XDG_SESSION_TYPE', 'XDG_CURRENT_DESKTOP', 'QT_QPA_PLATFORM'}
        for item in values:
            key, separator, value = item.partition('=')
            if separator and key in allowed:
                env[key] = value
    except BridgeError:
        pass
    return env
