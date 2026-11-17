#!/usr/bin/env python3
"""TV check: native catalogue, authenticated browse/open, singleton and player state."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import uuid

import aiohttp
from dbus_next import Message, MessageType
from dbus_next.aio import MessageBus

BUS = "io.github.jclsn.Flimmer.Control"
PATH = "/io/github/jclsn/Flimmer/Control"
APP = "io.github.jclsn.Flimmer"
PROFILE = None
CAPTURE_PATH = None


async def request(bus, command, **kwargs):
    request_id = str(uuid.uuid4())
    reply = await asyncio.wait_for(bus.call(Message(destination=BUS, path=PATH, interface=BUS,
        member="Execute", signature="s", body=[json.dumps(dict(id=request_id, command=command, **kwargs))])), 5)
    if reply.message_type == MessageType.ERROR:
        raise RuntimeError(reply.error_name)
    response = json.loads(reply.body[0])
    assert response['id'] == request_id
    return response


def ui_processes():
    result = set()
    for directory in Path('/proc').iterdir():
        if not directory.name.isdigit():
            continue
        try:
            argv = (directory / 'cmdline').read_bytes().split(b'\0')
            if argv and argv[0].rsplit(b'/', 1)[-1] == b'flimmer':
                result.add(int(directory.name))
        except OSError:
            pass
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


async def launch():
    args = ['flatpak', 'run']
    app_args = []
    if PROFILE:
        args.append('--command=env')
        for name, folder in [('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data'), ('XDG_CACHE_HOME', 'cache')]:
            app_args.append(name + '=' + str(Path(PROFILE) / folder))
        app_args.append('/app/bin/flimmer')
    output = open(Path(PROFILE) / 'application.log', 'ab') if PROFILE else asyncio.subprocess.DEVNULL
    try:
        return await asyncio.create_subprocess_exec(*args, APP, *app_args, env=await desktop_environment(),
            stdout=output, stderr=output)
    finally:
        if PROFILE:
            output.close()


async def main(restart, require_playback=False):
    if restart:
        processes = ui_processes()
        assert len(processes) <= 1, 'Multiple pre-existing Flimmer GUIs'
        for pid in processes:
            os.kill(pid, signal.SIGTERM)
        async with asyncio.timeout(10):
            while ui_processes():
                await asyncio.sleep(0.1)
        await launch()
    bus = await MessageBus().connect()
    try:
        async with asyncio.timeout(40):
            while True:
                try:
                    state = await request(bus, 'get_state')
                    if state['ok'] and state['result']['ready']:
                        break
                except RuntimeError:
                    pass
                await asyncio.sleep(0.3)
        print('PASS real Flimmer control endpoint ready')
        before = ui_processes()
        duplicate = await launch()
        assert await asyncio.wait_for(duplicate.wait(), 15) == 0
        assert len(before) == 1 and before == ui_processes()
        print('PASS duplicate launch reuses the existing GUI')
        bad = await request(bus, 'select_channel', channel_id='https://provider/stream')
        assert not bad['ok']
        print('PASS arbitrary URL selection rejected')

        config = json.loads((Path.home()/'.config/bigscreen-bridge/config.json').read_text())
        async with aiohttp.ClientSession(headers={'Authorization': 'Bearer ' + config['token']}) as session:
            async with session.ws_connect('http://127.0.0.1:8765/ws') as ws:
                await ws.receive_json()
                async def command(command, **kwargs):
                    request_id = str(uuid.uuid4())
                    await ws.send_json(dict(id=request_id, command=command, **kwargs))
                    async with asyncio.timeout(45):
                        while True:
                            reply = await ws.receive_json()
                            if reply.get('id') == request_id:
                                assert reply['ok'], reply.get('error', {}).get('code')
                                return reply['result']
                await command('focus', app='flimmer')
                async with asyncio.timeout(30):
                    while True:
                        page = await command('browse', app='flimmer', offset=0, limit=50)
                        if page['items']:
                            break
                        await asyncio.sleep(1)
                print('PASS authenticated channel browse; channel count:', page['total'])
                if page.get('next_offset') is not None:
                    second = await command('browse', app='flimmer', offset=page['next_offset'], limit=50)
                    assert not ({item['id'] for item in page['items']} & {item['id'] for item in second['items']})
                    print('PASS real catalogue pagination without duplicate channel IDs')
                if require_playback:
                    for _ in range(20):
                        item = (await command('browse', app='flimmer', offset=0, limit=1))['items'][0]
                        if item['current']['title'] and item['next']['title']:
                            break
                        await asyncio.sleep(0.25)
                    assert item['current']['title'] == 'Bridge test current'
                    assert item['next']['title'] == 'Bridge test next'
                    print('PASS actual XMLTV current and next programme reporting')
                current = state['result']['media'].get('channel', {}).get('id')
                channel_id = current or page['items'][0]['id']
                selected = await command('open', app='flimmer', content_type='channel', content_id=channel_id)
                assert selected['delivery'] == 'player_accepted'
                print('PASS authenticated channel selection acknowledged by real QML player')
                actual = None
                for _ in range(40):
                    state = (await request(bus, 'get_state'))['result']
                    actual = state['media']['state']
                    if actual in ('playing', 'error'):
                        break
                    await asyncio.sleep(0.5)
                if actual == 'playing':
                    await command('pause', app='flimmer')
                    paused = (await request(bus, 'get_state'))['result']
                    assert paused['media']['state'] == 'paused'
                    await command('play', app='flimmer')
                    await asyncio.sleep(1)
                    resumed = (await request(bus, 'get_state'))['result']
                    assert resumed['media']['state'] == 'playing'
                    print('PASS real IPTV playback, pause and timeshift resume')
                    if CAPTURE_PATH:
                        capture = await asyncio.create_subprocess_exec('spectacle', '-b', '-n', '-o', str(CAPTURE_PATH),
                            env=await desktop_environment(), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                        assert await asyncio.wait_for(capture.wait(), 15) == 0
                else:
                    print('UNVERIFIED IPTV stream playback; reported state:', actual)
                    assert not require_playback, 'Synthetic IPTV stream did not play'
                assert before == ui_processes()
                await command('focus', app='couchside')
    finally:
        bus.disconnect()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restart-ui', action='store_true')
    asyncio.run(main(parser.parse_args().restart_ui))
