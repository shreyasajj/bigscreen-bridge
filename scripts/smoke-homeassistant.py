#!/usr/bin/env python3
"""Boot isolated Home Assistant and exercise the integration against the real TV.

Uses a temporary config directory and loopback HTTP port. The user's HA installation
is never modified. The TV bridge token stays in memory or the private temp config.
"""
import asyncio
import json
import logging
from pathlib import Path
import shutil
import subprocess
import tempfile

from homeassistant import bootstrap, loader
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.json import json_bytes

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = 'bigscreen_bridge'


async def main():
    completed = await asyncio.to_thread(subprocess.run,
        ['ssh', '-o', 'BatchMode=yes', 'mediac@192.168.30.153', 'cat ~/.config/bigscreen-bridge/config.json'],
        capture_output=True, text=True, check=True)
    settings = json.loads(completed.stdout)
    credentials = {'url': 'http://192.168.30.153:8765', 'token': settings['token']}
    with tempfile.TemporaryDirectory(prefix='bigscreen-ha-', dir='/tmp') as directory:
        shutil.copytree(ROOT/'custom_components/bigscreen_bridge', Path(directory)/'custom_components/bigscreen_bridge')
        hass = HomeAssistant(directory)
        loader.async_setup(hass)
        hass.config.skip_pip = True
        try:
            configured = await bootstrap.async_from_config_dict({
                'homeassistant': {'name': 'Bigscreen verification', 'latitude': 0, 'longitude': 0,
                                  'elevation': 0, 'time_zone': 'UTC', 'unit_system': 'metric'},
                'http': {'server_host': '127.0.0.1', 'server_port': 18123},
            }, hass)
            assert configured is hass, 'Isolated HA bootstrap failed'
            await hass.async_start()
            bad = await hass.config_entries.flow.async_init(DOMAIN, context={'source': 'user'},
                data={**credentials, 'token': 'invalid-test-token'})
            assert bad['type'] == 'form' and bad['errors']['base'] == 'invalid_auth'
            hass.config_entries.flow.async_abort(bad['flow_id'])
            print('PASS real HA configuration flow rejects an invalid bridge token', flush=True)
            created = await hass.config_entries.flow.async_init(DOMAIN, context={'source': 'user'}, data=credentials)
            assert created['type'] == 'create_entry', 'Config flow did not create an entry'
            entry = created['result']
            await hass.async_block_till_done()
            assert entry.state == ConfigEntryState.LOADED, f'Entry setup failed: {entry.state}'
            registry = er.async_get(hass)
            entities = {item.unique_id: item.entity_id for item in er.async_entries_for_config_entry(registry, entry.entry_id)}
            prefix = settings['device_id']
            assert set(entities) == {prefix + suffix for suffix in ['_active', '_stremio', '_flimmer', '_vacuumtube']}
            print('PASS real HA loads four native media players; Couchside playback is not duplicated', flush=True)
            duplicate = await hass.config_entries.flow.async_init(DOMAIN, context={'source': 'user'}, data=credentials)
            assert duplicate['type'] == 'abort' and duplicate['reason'] == 'already_configured'
            print('PASS real HA rejects duplicate pairing of the same device', flush=True)
            active = entities[prefix + '_active']
            youtube = entities[prefix + '_vacuumtube']

            async def service(name, entity_id, **data):
                return await hass.services.async_call('media_player', name, {'entity_id': entity_id, **data}, blocking=True)

            async def wait_state(predicate, timeout=35):
                async with asyncio.timeout(timeout):
                    while True:
                        state = hass.states.get(youtube)
                        if state and predicate(state):
                            return state
                        await asyncio.sleep(0.3)

            await service('select_source', active, source='VacuumTube')
            await service('play_media', youtube, media_content_type='bigscreen_uri', media_content_id='bigscreen://vacuumtube/video/M7lc1UVf-VE')
            state = await wait_state(lambda s: s.state == 'playing' and bool(s.attributes.get('media_title')))
            assert state.attributes['media_content_id'] == 'M7lc1UVf-VE'
            assert state.attributes['media_duration'] > 0
            print('PASS real HA play_media, source selection, title, ID, duration and playback state', flush=True)
            await service('media_pause', youtube)
            await wait_state(lambda s: s.state == 'paused')
            await service('media_play', youtube)
            await wait_state(lambda s: s.state == 'playing')
            await service('media_seek', youtube, seek_position=12)
            await wait_state(lambda s: s.state == 'playing' and 11 <= s.attributes.get('media_position', -1) < 20)
            print('PASS real HA pause, play and seek service calls control the TV', flush=True)
            original = hass.states.get(youtube)
            level, muted = original.attributes.get('volume_level'), original.attributes.get('is_volume_muted')
            if level is not None and muted is not None:
                try:
                    await service('volume_set', youtube, volume_level=0.25)
                    await wait_state(lambda s: abs(s.attributes.get('volume_level', -1) - 0.25) < 0.01)
                    await service('volume_mute', youtube, is_volume_muted=True)
                    await wait_state(lambda s: s.attributes.get('is_volume_muted') is True)
                    print('PASS real HA volume and mute services', flush=True)
                finally:
                    await service('volume_set', youtube, volume_level=level)
                    await service('volume_mute', youtube, is_volume_muted=muted)
            else:
                raise AssertionError('YouTube volume controller did not appear in real HA')
            browse = await hass.services.async_call('media_player', 'browse_media', {'entity_id': active},
                                                    blocking=True, return_response=True)
            serialized = json.loads(json_bytes(browse))
            assert {item['media_content_id'] for item in serialized[active]['children']} == {'couchside', 'stremio', 'flimmer', 'vacuumtube'}
            print('PASS real HA browse_media service serializes the app catalogue', flush=True)
            # Restart the bridge service only; the native application keeps playing.
            disconnected = []
            def changed(event):
                state = event.data.get('new_state')
                if event.data.get('entity_id') == youtube and state and state.state == 'unavailable':
                    disconnected.append(True)
            remove_listener = hass.bus.async_listen('state_changed', changed)
            try:
                await asyncio.to_thread(subprocess.run, ['ssh', '-o', 'BatchMode=yes', 'mediac@192.168.30.153',
                    'systemctl --user restart bigscreen-bridge.service'], capture_output=True, check=True)
                await wait_state(lambda s: bool(disconnected) and s.state == 'playing'
                                 and s.attributes.get('media_content_id') == 'M7lc1UVf-VE', timeout=45)
                print('PASS real HA availability transition and automatic reconnect after bridge restart', flush=True)
            finally:
                remove_listener()
            await service('play_media', youtube, media_content_type='youtube_search', media_content_id='KDE Bigscreen')
            await wait_state(lambda s: s.attributes.get('youtube_view', {}).get('query') == 'KDE Bigscreen')
            print('PASS real HA typed search and pushed navigation state', flush=True)
            await service('media_stop', youtube)
            await wait_state(lambda s: s.state == 'idle')
            await service('select_source', active, source='Couchside')
            assert await hass.config_entries.async_unload(entry.entry_id)
            print('PASS integration unload closes the bridge connection; Couchside focus restored', flush=True)
        finally:
            await hass.async_stop()


if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
