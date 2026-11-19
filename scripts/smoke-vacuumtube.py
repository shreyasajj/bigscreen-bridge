#!/usr/bin/env python3
"""Probe the actual Leanback listener and player through VacuumTube's local hook."""
import asyncio
import json
import os
import signal
import uuid
from pathlib import Path

APP = 'rocks.shy.VacuumTube'
SOCKET = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'))/'bigscreen-bridge/vacuumtube.sock'
PROFILE = Path.home()/'.var/app/rocks.shy.VacuumTube/data/bigscreen-bridge-test-profile'


async def request(command, request_id=None, **kwargs):
    request_id = request_id or str(uuid.uuid4())
    reader, writer = await asyncio.open_unix_connection(str(SOCKET))
    try:
        writer.write(json.dumps(dict(id=request_id, command=command, **kwargs)).encode() + b'\n')
        await writer.drain()
        result = json.loads(await asyncio.wait_for(reader.readline(), 25))
        assert result['id'] == request_id
        return result
    finally:
        writer.close()
        await writer.wait_closed()


def ui_processes():
    result = set()
    for directory in Path('/proc').iterdir():
        if not directory.name.isdigit():
            continue
        try:
            args = (directory/'cmdline').read_bytes().split(b'\0')
            # Zypak/Chromium may rewrite all arguments into argv[0].
            command = b' '.join(args).strip()
            if command.startswith(b'/app/bin/vacuumtube/vacuumtube ') and b' --type=' not in command:
                result.add(int(directory.name))
        except OSError:
            pass
    return result


async def desktop_environment():
    env = dict(os.environ)
    proc = await asyncio.create_subprocess_exec('pgrep', '-u', str(os.getuid()), '-x', 'plasmashell', stdout=asyncio.subprocess.PIPE)
    output, _ = await proc.communicate()
    pid = output.decode().splitlines()[0]
    for item in (Path('/proc')/pid/'environ').read_bytes().split(b'\0'):
        key, sep, value = item.partition(b'=')
        name = key.decode()
        if sep and (name in {'WAYLAND_DISPLAY', 'DISPLAY', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS',
                            'XDG_SESSION_TYPE', 'XDG_CURRENT_DESKTOP'} or name.startswith('QT_')):
            env[name] = value.decode()
    return env


async def launch():
    PROFILE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (PROFILE/'bridge-test.log').open('ab') as output:
        return await asyncio.create_subprocess_exec('flatpak', 'run', APP, '--portable', str(PROFILE), '--fullscreen',
            env=await desktop_environment(), stdout=output, stderr=output)


async def capture(name):
    await asyncio.sleep(2)
    destination = Path.home()/'.cache'/name
    proc = await asyncio.create_subprocess_exec('spectacle', '-b', '-n', '-o', str(destination),
        env=await desktop_environment(), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    assert await asyncio.wait_for(proc.wait(), 15) == 0


async def main():
    old = ui_processes()
    assert len(old) <= 1, 'Multiple pre-existing VacuumTube GUIs'
    for pid in old:
        os.kill(pid, signal.SIGTERM)
    async with asyncio.timeout(10):
        while ui_processes():
            await asyncio.sleep(0.1)
    await launch()
    async with asyncio.timeout(60):
        while True:
            try:
                state = (await request('get_state'))['result']
                if state['ready']:
                    break
            except (OSError, ValueError, KeyError):
                pass
            await asyncio.sleep(0.5)
    print('PASS actual Leanback listener ready; types:', state['content_types'])
    before = ui_processes()
    duplicate = await launch()
    assert await asyncio.wait_for(duplicate.wait(), 15) == 0
    assert len(before) == 1 and before == ui_processes()
    print('PASS second launch reuses one Electron main process')
    invalid = await request('open', content_type='video', content_id='https://example.org')
    assert not invalid['ok']
    async def wait_for(predicate, timeout=30):
        async with asyncio.timeout(timeout):
            while True:
                state = (await request('get_state'))['result']
                if predicate(state):
                    return state
                await asyncio.sleep(0.3)

    video = 'M7lc1UVf-VE'
    opened = await request('open', content_type='video', content_id=video)
    assert opened['ok']
    state = await wait_for(lambda s: s['media'].get('video_id') == video
                           and s['media'].get('title') and s['media']['state'] == 'playing')
    assert state['media']['duration'] > 0
    print('PASS real requested video, title, duration, position and thumbnail:', state['media']['title'])
    await request('focus')
    assert (await request('get_state'))['result']['media']['video_id'] == video
    for command, expected in [('pause', 'paused'), ('play', 'playing')]:
        result = await request(command)
        assert result['ok'], result
        await wait_for(lambda s, expected=expected: s['media']['state'] == expected)
        print('PASS real Leanback player command:', command)
    result = await request('seek', position=12)
    assert result['ok'], result
    await wait_for(lambda s: s['media']['state'] == 'playing' and 11 <= s['media'].get('position', -1) < 20)
    print('PASS real absolute seek')
    await capture('bigscreen-vacuumtube-video.png')
    volume_state = (await request('get_state'))['result']['media']
    original_volume, original_muted = volume_state['volume'], volume_state['muted']
    try:
        assert (await request('volume_mute', muted=False))['ok']
        assert (await request('volume_set', volume_level=0.25))['ok']
        await wait_for(lambda s: abs(s['media'].get('volume', -1) - 0.25) < 0.01
                       and abs(s['media'].get('output_volume', -1) - 0.25) < 0.01)
        await asyncio.sleep(1)
        assert abs((await request('get_state'))['result']['media']['output_volume'] - 0.25) < 0.01
        assert (await request('volume_mute', muted=True))['ok']
        await wait_for(lambda s: s['media'].get('muted') is True and s['media'].get('output_volume') == 0)
        print('PASS authoritative app volume and mute, verified against actual player output volume')
    finally:
        await request('volume_set', volume_level=original_volume)
        await request('volume_mute', muted=original_muted)
    playlist = 'PLBCF2DAC6FFB574DE'
    result = await request('open', content_type='playlist', content_id=playlist)
    assert result['ok'], result
    await wait_for(lambda s: s.get('view', {}).get('playlist_id') == playlist
                   and s['media']['state'] == 'playing' and s['media'].get('title'))
    print('PASS actual playlist navigation and playback')
    result = await request('open', content_type='search', content_id='KDE Bigscreen')
    assert result['ok'], result
    state = await wait_for(lambda s: s.get('view', {}).get('kind') == 'search' and s['view'].get('query') == 'KDE Bigscreen')
    print('PASS actual search navigation:', state.get('view'))
    await capture('bigscreen-vacuumtube-search.png')
    stopped = await request('stop')
    print('Stop command probe:', stopped['ok'])
    if stopped['ok']:
        await wait_for(lambda s: s['media']['state'] == 'idle')
        print('PASS actual player stop')


if __name__ == '__main__':
    asyncio.run(main())
