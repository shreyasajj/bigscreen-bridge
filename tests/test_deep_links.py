import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bigscreen_bridge.adapters import APPS, BridgeError
from bigscreen_bridge.config import load_config
from bigscreen_bridge.deep_links import parse_uri
from bigscreen_bridge.open_link import desktop_entry, open_uri
from custom_components.bigscreen_bridge.media_player import BigscreenPlayer


@pytest.mark.parametrize('uri,expected', [
    ('bigscreen://couchside', {'command': 'focus', 'app': 'couchside'}),
    ('bigscreen://flimmer/', {'command': 'focus', 'app': 'flimmer'}),
    ('bigscreen://couchside/now_playing', {'command': 'open', 'app': 'couchside', 'content_type': 'now_playing', 'content_id': ''}),
    ('bigscreen://couchside/album/library%3A%2F%2Falbum%2F123', {'command': 'open', 'app': 'couchside', 'content_type': 'album', 'content_id': 'library://album/123'}),
    ('bigscreen://stremio/movie/tt0133093', {'command': 'open', 'app': 'stremio', 'content_type': 'movie', 'content_id': 'tt0133093'}),
    ('bigscreen://stremio/episode/tt0944947/tt0944947%3A1%3A2?resume=true', {'command': 'open', 'app': 'stremio', 'content_type': 'episode', 'content_id': {'series_id': 'tt0944947', 'video_id': 'tt0944947:1:2'}, 'resume': True}),
    ('bigscreen://vacuumtube/video/M7lc1UVf-VE', {'command': 'open', 'app': 'vacuumtube', 'content_type': 'video', 'content_id': 'M7lc1UVf-VE'}),
    ('bigscreen://vacuumtube/search/KDE%20Bigscreen', {'command': 'open', 'app': 'vacuumtube', 'content_type': 'search', 'content_id': 'KDE Bigscreen'}),
    ('bigscreen://flimmer/channel/channel-' + 'a'*64, {'command': 'open', 'app': 'flimmer', 'content_type': 'channel', 'content_id': 'channel-' + 'a'*64}),
])
def test_shared_uri_contract(uri, expected):
    assert parse_uri(uri) == expected


@pytest.mark.parametrize('uri', [
    None, True, '', 'http://example.org', 'file:///tmp/private', 'bigscreen://unknown',
    'bigscreen://user:password@vacuumtube/video/M7lc1UVf-VE', 'bigscreen://vacuumtube:8765',
    'bigscreen://vacuumtube/video/%FF', 'bigscreen://vacuumtube/video/%xx',
    'bigscreen://vacuumtube//video/M7lc1UVf-VE', 'bigscreen://vacuumtube/search/%00',
    'bigscreen://vacuumtube/video/https%3A%2F%2Fexample.org', 'bigscreen://couchside#fragment',
    'bigscreen://couchside?resume=true', 'bigscreen://stremio/movie/tt1?url=https://example.org',
    'bigscreen://stremio/movie/tt1?resume=true&resume=false', 'bigscreen://stremio/movie/tt1?resume=True',
    'bigscreen://couchside/album/library://album/1', 'bigscreen://vacuumtube/search/' + 'x'*9000,
])
def test_invalid_links(uri):
    with pytest.raises(BridgeError):
        parse_uri(uri)


async def test_dispatch_and_app_scope_before_focus(bridge):
    b, _ = bridge
    b.wm.windows = [{'id': 'music', 'desktop': APPS[0].flatpak, 'active': True}]
    assert await b.execute(dict(id='focus', command='open_uri', uri='bigscreen://couchside')) == {'app': 'couchside'}
    b.wm.focused.clear()
    with pytest.raises(BridgeError):
        await b.execute(dict(id='wrong-app', command='open_uri', uri='bigscreen://couchside', app='vacuumtube'))
    with pytest.raises(BridgeError):
        await b.execute(dict(id='unsafe', command='open_uri', uri='http://example.org'))
    assert not b.wm.focused


async def test_native_ha_uri_forwards_an_explicit_app_scope(bridge):
    b, _ = bridge
    coordinator = SimpleNamespace(data=b.snapshot(), last_update_success=True, config_entry=None,
                                  async_contexts=lambda: (), command=AsyncMock())
    main, youtube = BigscreenPlayer(coordinator), BigscreenPlayer(coordinator, 'vacuumtube')
    await main.async_play_media('bigscreen_uri', 'bigscreen://couchside')
    coordinator.command.assert_awaited_with('open_uri', uri='bigscreen://couchside')
    await youtube.async_play_media('bigscreen_uri', 'bigscreen://vacuumtube/video/M7lc1UVf-VE')
    coordinator.command.assert_awaited_with('open_uri', uri='bigscreen://vacuumtube/video/M7lc1UVf-VE', app='vacuumtube')


def private_config(tmp_path, **extra):
    tmp_path.chmod(0o700)
    path = tmp_path/'config.json'
    path.write_text(json.dumps({'token': 'a'*48, 'device_id': 'test-device', **extra}))
    path.chmod(0o600)
    return path


def test_private_configuration_rejects_readable_files_and_symlinks(tmp_path):
    path = private_config(tmp_path)
    assert load_config(path)['device_id'] == 'test-device'
    path.chmod(0o644)
    with pytest.raises(ValueError, match='private'):
        load_config(path)
    path.chmod(0o600)
    link = tmp_path/'linked.json'
    link.symlink_to(path)
    with pytest.raises(ValueError):
        load_config(link)
    tmp_path.chmod(0o755)
    with pytest.raises(ValueError):
        load_config(path)


@pytest.mark.parametrize('port', [0, 65536, True, '8765'])
def test_private_configuration_port_validation(tmp_path, port):
    with pytest.raises(ValueError, match='port'):
        load_config(private_config(tmp_path, port=port))


async def test_uri_client_uses_real_authenticated_websocket(bridge, tmp_path):
    b, client = bridge
    b.wm.windows = [{'id': 'music', 'desktop': APPS[0].flatpak, 'active': True}]
    path = private_config(tmp_path, host='127.0.0.1', port=client.server.port)
    assert await open_uri('bigscreen://couchside', path) == {'app': 'couchside'}
    assert b.wm.focused == ['music']


def test_desktop_entry_quotes_program_and_keeps_uri_as_one_field():
    entry = desktop_entry('/home/me/a path/bin/open')
    assert len(entry.splitlines()) == 8
    assert 'MimeType=x-scheme-handler/bigscreen;' in entry
    assert 'Exec="/home/me/a path/bin/open" --notify %u' in entry
    assert '%%u' in desktop_entry('/home/me/percent%u/open')
