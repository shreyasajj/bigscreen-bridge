import asyncio
from types import SimpleNamespace

import pytest

from bigscreen_bridge.adapters import BridgeError
from bigscreen_bridge.mpris import MprisRegistry
from bigscreen_bridge.sessions import SessionController


class Hook:
    def __init__(self, state='playing'):
        self.state = {'ready': True, 'media': {'state': state, 'content_id': 'one'},
                      'capabilities': ['play', 'pause', 'stop']}
        self.calls = []

    async def refresh(self):
        pass

    async def control(self, request_id, command):
        self.calls.append(command)
        self.state['media']['state'] = {'play': 'playing', 'pause': 'paused', 'stop': 'idle'}[command]


class Registry:
    overflow = False

    def __init__(self, players=None):
        self.players = players or {}
        self.calls = []

    async def refresh(self):
        pass

    async def control(self, key, command):
        self.calls.append((key, command))
        self.players[key]['state'] = {'play': 'playing', 'pause': 'paused', 'stop': 'idle'}[command]
        return {'changed': True, 'completion_confirmed': True}


def controller(hooks=None, players=None):
    bridge = SimpleNamespace(lock=asyncio.Lock(), **(hooks or {}))
    bridge.snapshot = lambda: {'active_app': 'vacuumtube'}
    return SessionController(bridge, Registry(players))


@pytest.mark.asyncio
async def test_repeated_pause_preserves_receipts_and_never_toggles_manual_pause():
    youtube, flimmer = Hook(), Hook('paused')
    sessions = controller({'vacuumtube': youtube, 'flimmer': flimmer})
    first = await sessions.execute('pause_all')
    assert first['completion_confirmed']
    assert first['paused_by_bridge'] == ['app:vacuumtube']
    await sessions.execute('pause_all')
    assert youtube.calls == ['pause']
    await sessions.execute('resume_previously_paused')
    assert youtube.calls == ['pause', 'play']
    assert flimmer.calls == []
    assert sessions.receipts == {}


@pytest.mark.asyncio
async def test_native_hook_deduplicates_mpris_and_sendspin_is_not_group_controlled():
    youtube = Hook()
    players = {'mpris::1': {'id': 'mpris::1', 'app': 'vacuumtube', 'state': 'playing', 'available': True},
               'mpris::2': {'id': 'mpris::2', 'app': 'couchside', 'state': 'playing',
                            'available': True, 'role': 'music_assistant'}}
    sessions = controller({'vacuumtube': youtube}, players)
    result = await sessions.execute('pause_all')
    assert result['status'] == 'failed'
    assert result['players'][1]['error']['code'] == 'music_assistant_required'
    assert youtube.calls == ['pause']
    assert sessions.mpris.calls == []


@pytest.mark.asyncio
async def test_changed_content_disappearance_and_manual_command_revoke_pause_ownership():
    hook = Hook()
    sessions = controller({'vacuumtube': hook})
    await sessions.execute('pause_all')
    hook.state['media']['content_id'] = 'two'
    await sessions.execute('resume_previously_paused')
    assert hook.calls == ['pause']
    hook.state['media']['state'] = 'playing'
    await sessions.execute('pause_all')
    sessions.invalidate_app('vacuumtube')
    await sessions.execute('resume_previously_paused')
    assert hook.calls == ['pause', 'pause']
    hook.state['media']['state'] = 'playing'
    await sessions.execute('pause_all')
    hook.state = None
    await sessions.execute('resume_previously_paused')
    assert not sessions.receipts


@pytest.mark.asyncio
async def test_observed_manual_transition_revokes_even_when_final_state_paused():
    hook = Hook()
    sessions = controller({'vacuumtube': hook})
    await sessions.execute('pause_all')
    sessions.transition('app:vacuumtube', 'paused', 'playing')
    sessions.transition('app:vacuumtube', 'playing', 'paused')
    await sessions.execute('resume_previously_paused')
    assert hook.calls == ['pause']


@pytest.mark.asyncio
async def test_low_level_sendspin_mpris_guard():
    registry = MprisRegistry(None)
    registry.players['music'] = {'role': 'music_assistant'}
    with pytest.raises(BridgeError, match='Music Assistant'):
        await registry.control('music', 'pause')


@pytest.mark.asyncio
async def test_inventory_overflow_prevents_safe_power_confirmation():
    sessions = controller()
    sessions.mpris.overflow = True
    result = await sessions.execute('pause_all')
    assert not result['completion_confirmed']
    assert not result['inventory_complete']
