#!/usr/bin/env python3
"""TV smoke test: real metadata navigation and singleton reuse, no stream playback."""
import asyncio
import json
import os
from pathlib import Path
import uuid

from dbus_next import Message, MessageType
from dbus_next.aio import MessageBus

BUS = "io.github.shreyasajj.StremioTV.Control"
PATH = "/io/github/shreyasajj/StremioTV/Control"


async def request(bus, command, request_id=None, **kwargs):
    request_id = request_id or str(uuid.uuid4())
    reply = await asyncio.wait_for(bus.call(Message(destination=BUS, path=PATH, interface=BUS,
        member="Execute", signature="s", body=[json.dumps(dict(id=request_id, command=command, **kwargs))])), 25)
    if reply.message_type == MessageType.ERROR:
        raise RuntimeError(reply.error_name)
    result = json.loads(reply.body[0])
    assert result['id'] == request_id
    return result


async def desktop_environment():
    env = dict(os.environ)
    proc = await asyncio.create_subprocess_exec('pgrep', '-u', str(os.getuid()), '-x', 'plasmashell',
                                               stdout=asyncio.subprocess.PIPE)
    output, _ = await proc.communicate()
    pid = output.decode().splitlines()[0]
    for entry in (Path('/proc') / pid / 'environ').read_bytes().split(b'\0'):
        key, separator, value = entry.partition(b'=')
        name = key.decode()
        if separator and (name in {'WAYLAND_DISPLAY', 'DISPLAY', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS',
                                   'XDG_SESSION_TYPE', 'XDG_CURRENT_DESKTOP'} or name.startswith('QT_')):
            env[name] = value.decode()
    return env


def ui_processes():
    result = set()
    for directory in Path('/proc').iterdir():
        if not directory.name.isdigit():
            continue
        try:
            args = (directory / 'cmdline').read_bytes().split(b'\0')
            if args and args[0].rsplit(b'/', 1)[-1] == b'stremio-tv-native':
                result.add(int(directory.name))
        except OSError:
            pass
    return result


async def main():
    bus = await MessageBus().connect()
    try:
        async with asyncio.timeout(45):
            while True:
                try:
                    result = await request(bus, 'get_state')
                except RuntimeError:
                    await asyncio.sleep(0.5)
                    continue
                if result['ok'] and result['result']['ready']:
                    break
                await asyncio.sleep(0.5)
        for kind, content in [('movie', 'tt0133093'), ('series', 'tt0944947'),
                              ('episode', {'series_id':'tt0944947', 'video_id':'tt0944947:1:2'})]:
            result = await request(bus, 'open', content_type=kind, content_id=content)
            assert result['ok'], result
            assert result['result']['delivery'] == 'detail'
            print('PASS real Stremio metadata navigation:', kind)
        malformed = await request(bus, 'open', content_type='movie', content_id='https://example.org')
        assert not malformed['ok'] and malformed['error']['code'] == 'invalid_request'
        before = ui_processes()
        proc = await asyncio.create_subprocess_exec('flatpak', 'run', 'io.github.shreyasajj.StremioTV',
            env=await desktop_environment(), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        assert await asyncio.wait_for(proc.wait(), 15) == 0
        assert len(before) == 1 and ui_processes() == before
        print('PASS second launch reuses the same UI process')
        state = await request(bus, 'get_state')
        assert state['result']['media'].get('state') == 'idle', 'Navigation unexpectedly started playback'
        print('PASS navigation did not start playback; arbitrary URLs rejected')
    finally:
        bus.disconnect()


if __name__ == '__main__':
    asyncio.run(main())
