import asyncio
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.exceptions import HomeAssistantError

from bigscreen_bridge.adapters import APPS, BridgeError
from bigscreen_bridge.vacuumtube import VacuumTubeHook, validate_content
from custom_components.bigscreen_bridge.media_player import BigscreenPlayer

VIDEO = 'M7lc1UVf-VE'


@pytest.mark.parametrize('kind,value', [('video', VIDEO), ('playlist', 'PLBCF2DAC6FFB574DE'), ('search', 'KDE Bigscreen')])
def test_content_contract(kind, value):
    assert validate_content(kind, value) == (kind, value)


@pytest.mark.parametrize('kind,value', [('video', 'https://youtube.com/watch'), ('video', True),
                                       ('playlist', '../secret'), ('search', '\nunsafe'), ('search', ' '), ('unknown', VIDEO)])
def test_invalid_content(kind, value):
    with pytest.raises(BridgeError):
        validate_content(kind, value)


def hook():
    return SimpleNamespace(enabled=True, content_types=['video'], refresh=AsyncMock(),
        state={'ready': True, 'capabilities': ['pause', 'seek'], 'media': {'state': 'playing', 'type': 'video',
               'title': 'YouTube test', 'content_id': VIDEO, 'video_id': VIDEO, 'duration': 100, 'position': 5,
               'poster': f'https://i.ytimg.com/vi/{VIDEO}/hqdefault.jpg', 'updated_at': '2026-11-16T18:00:00Z'}},
        open=AsyncMock(return_value={'delivery': 'video_selected'}),
        control=AsyncMock(return_value={'delivery': 'player_called'}))


async def test_bridge_and_native_youtube_entity(bridge):
    b, _ = bridge
    b.vacuumtube = hook()
    b.wm.windows = [{'id': 'youtube', 'desktop': APPS[3].flatpak, 'active': True}]
    coordinator = SimpleNamespace(data=b.snapshot(), last_update_success=True, config_entry=None,
                                  async_contexts=lambda: (), command=AsyncMock())
    player = BigscreenPlayer(coordinator, 'vacuumtube')
    assert player.name == 'YouTube' and player.media_title == 'YouTube test'
    assert player.media_content_type == 'video' and player.media_content_id == VIDEO
    assert player.media_duration == 100 and player.media_position == 5
    assert player.media_image_url.endswith('/hqdefault.jpg')
    assert player.supported_features & MediaPlayerEntityFeature.SEEK
    assert not player.supported_features & MediaPlayerEntityFeature.VOLUME_SET
    await player.async_play_media('youtube_video', VIDEO)
    coordinator.command.assert_awaited_with('open', app='vacuumtube', content_type='video', content_id=VIDEO)
    await b.execute(dict(id='open', command='open', app='vacuumtube', content_type='video', content_id=VIDEO))
    b.vacuumtube.open.assert_awaited_once_with('open', 'video', VIDEO)
    await b.execute(dict(id='seek', command='seek', app='vacuumtube', position=12))
    b.vacuumtube.control.assert_awaited_once_with('seek', 'seek', position=12)
    with pytest.raises(HomeAssistantError):
        await player.async_play_media('flimmer_channel', 'channel-' + 'a'*64)


async def test_unadvertised_youtube_content_does_not_focus(bridge):
    b, _ = bridge
    b.vacuumtube = hook()
    for extra in ({'content_id': 'https://example.org'}, {'content_type': 'search', 'content_id': 'cats'}, {'resume': False}):
        request = dict(id='bad', command='open', app='vacuumtube', content_type='video', content_id=VIDEO)
        request.update(extra)
        with pytest.raises(BridgeError):
            await b.execute(request)
    b.vacuumtube.open.assert_not_awaited()
    assert not b.wm.focused


async def test_private_socket_round_trip_and_metadata():
    # macOS pytest paths exceed AF_UNIX's address limit.
    directory = tempfile.TemporaryDirectory(prefix='vt-', dir='/tmp')
    tmp_path = Path(directory.name)
    tmp_path.chmod(0o700)
    socket_path = tmp_path/'vacuumtube.sock'

    async def serve(reader, writer):
        request = json.loads(await reader.readline())
        response = {'id': request['id'], 'ok': True, 'result': {'protocol': 1, 'ready': True,
                    'content_types': ['video'], 'capabilities': ['pause'],
                    'media': {'state': 'playing', 'video_id': VIDEO, 'title': 'Actual player',
                              'thumbnail': 'file:///private'}}}
        writer.write(json.dumps(response).encode() + b'\n')
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(serve, path=socket_path)
    socket_path.chmod(0o600)
    try:
        native = VacuumTubeHook(socket_path=socket_path)
        await native.refresh()
        assert native.enabled and native.state['ready']
        assert native.state['media']['content_id'] == VIDEO
        assert native.state['media']['poster'] == f'https://i.ytimg.com/vi/{VIDEO}/hqdefault.jpg'
        socket_path.chmod(0o666)
        with pytest.raises(BridgeError, match='not private'):
            await native.request('get_state')
    finally:
        server.close()
        await server.wait_closed()
        directory.cleanup()


async def test_missing_and_symlink_socket_are_unavailable(tmp_path):
    tmp_path.chmod(0o700)
    native = VacuumTubeHook(socket_path=tmp_path/'missing.sock')
    await native.refresh()
    assert native.state is None
    target = tmp_path/'regular'
    target.write_text('never treat files as a control endpoint')
    os.symlink(target, tmp_path/'link.sock')
    native.socket_path = tmp_path/'link.sock'
    with pytest.raises(BridgeError):
        await native.request('get_state')


@pytest.mark.parametrize('value', [-1, 2, True, '0.5', float('inf'), float('nan'), 10**400])
def test_invalid_volume(value):
    from bigscreen_bridge.vacuumtube import validate_volume
    with pytest.raises(BridgeError):
        validate_volume(value)


async def test_youtube_volume_uses_only_advertised_controller(bridge):
    b, _ = bridge
    b.vacuumtube = hook()
    b.vacuumtube.state['capabilities'] += ['volume_set', 'volume_mute']
    b.vacuumtube.state['media'].update(volume=0.5, muted=True)
    b.wm.windows = [{'id': 'youtube', 'desktop': APPS[3].flatpak, 'active': True}]
    coordinator = SimpleNamespace(data=b.snapshot(), last_update_success=True, config_entry=None,
                                  async_contexts=lambda: (), command=AsyncMock())
    player = BigscreenPlayer(coordinator, 'vacuumtube')
    assert player.volume_level == 0.5 and player.is_volume_muted is True
    assert player.supported_features & MediaPlayerEntityFeature.VOLUME_SET
    assert player.supported_features & MediaPlayerEntityFeature.VOLUME_MUTE
    await player.async_set_volume_level(0.25)
    coordinator.command.assert_awaited_with('volume_set', app='vacuumtube', volume_level=0.25)
    await player.async_mute_volume(False)
    coordinator.command.assert_awaited_with('volume_mute', app='vacuumtube', muted=False)
    await b.execute(dict(id='level', command='volume_set', app='vacuumtube', volume_level=0.25))
    b.vacuumtube.control.assert_awaited_with('level', 'volume_set', volume_level=0.25)
    for request in [dict(id='bad', command='volume_mute', app='vacuumtube', muted=1),
                    dict(id='bad2', command='volume_set', app='vacuumtube', volume_level=True)]:
        with pytest.raises(BridgeError):
            await b.execute(request)
