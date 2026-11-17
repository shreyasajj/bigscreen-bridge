"""Serialized explicit playback operations and receipts for bridge-owned pauses."""
import asyncio
import copy
import uuid

from .adapters import BridgeError


class SessionController:
    def __init__(self, bridge, mpris):
        self.bridge, self.mpris = bridge, mpris
        self.lock = asyncio.Lock()
        self.receipts = {}
        self.observed = {}
        self.pending = set()
        self.players = {}

    def transition(self, key, previous, current):
        # Signals invalidate ownership even when several transitions occur between polls.
        if key not in self.pending and previous != current:
            self.receipts.pop(key, None)

    async def observe(self, full=True):
        async with self.lock, self.bridge.lock:
            return await self.refresh(full)

    async def refresh(self, full=True):
        # Sources that announce their own changes are re-read only when they said so.
        jobs = [hook.refresh() for hook in self.hooks().values() if full or getattr(hook, 'stale', True)]
        if full or getattr(self.mpris, 'dirty', True):
            jobs.append(self.mpris.refresh())
        await asyncio.gather(*jobs)
        players = {}
        native = set()
        for app, hook in self.hooks().items():
            state = hook.state
            if not state or not state.get('ready'):
                continue
            native.add(app)
            media = state.get('media', {})
            players['app:' + app] = {'id': 'app:' + app, 'app': app, 'state': media.get('state', 'unknown'),
                                    'media': media, 'capabilities': state.get('capabilities', []),
                                    'available': True, 'role': 'video'}
        for key, player in self.mpris.players.items():
            # A native hook owns playback for its application; never also send MPRIS commands.
            if player.get('app') not in native:
                players[key] = player
        for key, receipt in list(self.receipts.items()):
            player = players.get(key)
            if (not player or not player.get('available') or player['state'] != 'paused'
                    or self.identity(player) != receipt['identity']):
                self.receipts.pop(key, None)
        self.players = copy.deepcopy(players)
        self.observed = {key: player['state'] for key, player in players.items()}
        return self.players

    def hooks(self):
        return {app: hook for app in ('stremio', 'flimmer', 'vacuumtube')
                if (hook := getattr(self.bridge, app, None)) is not None}

    @staticmethod
    def identity(player):
        media = player.get('media', {})
        return tuple(media.get(key) for key in ('content_id', 'video_id', 'channel_id', 'title'))

    def invalidate_app(self, app):
        for key, player in self.players.items():
            if player.get('app') == app:
                self.receipts.pop(key, None)

    async def _control(self, player, command):
        if player.get('role') == 'music_assistant':
            raise BridgeError('music_assistant_required', 'Associate the local Music Assistant entity before controlling music')
        key = player['id']
        if key.startswith('mpris:'):
            return await self.mpris.control(key, command)
        hook = self.hooks()[player['app']]
        desired = {'pause': 'paused', 'play': 'playing', 'stop': 'idle'}[command]
        if player['state'] == desired:
            return {'changed': False, 'completion_confirmed': True, 'state': desired}
        if command not in player['capabilities']:
            raise BridgeError('unsupported', 'The application does not support this operation')
        await hook.control(str(uuid.uuid4()), command)
        for _ in range(20):
            await hook.refresh()
            state = hook.state
            if state and state.get('ready') and state.get('media', {}).get('state') == desired:
                return {'changed': True, 'completion_confirmed': True, 'state': desired}
            await asyncio.sleep(0.1)
        raise BridgeError('state_not_confirmed', 'The application did not confirm playback state')

    async def execute(self, command):
        if command not in ('pause_active', 'pause_all', 'resume_active', 'resume_previously_paused', 'stop_active', 'stop_all'):
            raise BridgeError('unsupported', 'Unsupported global playback command')
        async with self.lock, self.bridge.lock:
            await self.refresh()
            operation = 'play' if command.startswith('resume') else command.split('_')[0]
            active = self.bridge.snapshot().get('active_app')
            targets = list(self.players.values())
            if command.endswith('_active'):
                targets = [player for player in targets if player.get('app') == active] if active else []
            if command == 'resume_previously_paused':
                targets = [player for player in targets if player['id'] in self.receipts]
            else:
                wanted = ('playing', 'paused') if operation == 'stop' else ('paused',) if operation == 'play' else ('playing',)
                targets = [player for player in targets if player['state'] in wanted]
            results = []
            for player in targets:
                key = player['id']
                self.pending.add(key)
                try:
                    result = await self._control(player, operation)
                    if operation == 'pause' and result.get('changed') and result.get('completion_confirmed'):
                        self.receipts[key] = {'identity': self.identity(player)}
                    elif operation != 'pause':
                        self.receipts.pop(key, None)
                    results.append({'player': key, 'ok': True, **result})
                except BridgeError as exc:
                    self.receipts.pop(key, None)
                    results.append({'player': key, 'ok': False, 'error': {'code': exc.code, 'message': str(exc)}})
                finally:
                    self.pending.discard(key)
            await self.refresh()
            complete = not self.mpris.overflow and all(row['ok'] and row.get('completion_confirmed') for row in results)
            return {'command': command, 'status': 'completed' if complete else 'failed',
                    'completion_confirmed': complete, 'players': results,
                    'paused_by_bridge': list(self.receipts), 'inventory_complete': not self.mpris.overflow}
