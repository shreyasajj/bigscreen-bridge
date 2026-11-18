"""Explicit MPRIS player control with owner identity and native-adapter deduplication."""
import asyncio
import hashlib
import json

from dbus_next import MessageType

from .adapters import BridgeError
from .audio import identify_app
from .runtime_io import call, properties, unwrap

PATH = '/org/mpris/MediaPlayer2'
ROOT = 'org.mpris.MediaPlayer2'
PLAYER = ROOT + '.Player'
STATES = {'Playing': 'playing', 'Paused': 'paused', 'Stopped': 'idle'}


def media_state(props):
    metadata = props.get('Metadata') or {}
    if not isinstance(metadata, dict):
        metadata = {}
    title = metadata.get('xesam:title')
    track = metadata.get('mpris:trackid')
    identity = json.dumps([track[:2048] if isinstance(track, str) else '', title[:2048] if isinstance(title, str) else '',
                           metadata.get('mpris:length') if type(metadata.get('mpris:length')) is int else None])
    result = {'title': title[:2048] if isinstance(title, str) else '',
              'content_id': hashlib.sha256(identity.encode()).hexdigest()}
    for key, value in [('position', props.get('Position')), ('duration', metadata.get('mpris:length'))]:
        if type(value) is int and value >= 0:
            result[key] = value / 1000000
    # Generic MPRIS URLs can contain private files or provider credentials.
    return result


class MprisRegistry:
    def __init__(self, bus, changed=lambda *_: None):
        self.bus, self.changed = bus, changed
        self.players = {}
        self.owners = {}
        self.overflow = False
        self.dirty = True               # a full re-read is owed
        self.on_change = lambda: None
        self.followers = {}             # app id -> hook whose changes this player announces
        self.settling = {}              # app id -> timer for the follow-up read

    def follow(self, app, hook):
        """Let an app's own MPRIS player stand in for the change signal its hook lacks."""
        self.followers[app] = hook

    def _mark(self):
        self.dirty = True
        self.on_change()

    async def initialize(self):
        self.bus.add_message_handler(self._signal)
        for rule in ["type='signal',path='/org/mpris/MediaPlayer2',interface='org.freedesktop.DBus.Properties',member='PropertiesChanged'",
                     "type='signal',path='/org/mpris/MediaPlayer2',interface='org.mpris.MediaPlayer2.Player',member='Seeked'",
                     "type='signal',sender='org.freedesktop.DBus',interface='org.freedesktop.DBus',member='NameOwnerChanged',arg0namespace='org.mpris.MediaPlayer2'"]:
            await call(self.bus, 'org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'AddMatch', 's', [rule])
        await self.refresh()

    def _signal(self, message):
        if message.message_type != MessageType.SIGNAL:
            return
        if (message.path == PATH and message.sender in self.owners
                and (message.interface, message.member) in ((PLAYER, 'Seeked'), ('org.freedesktop.DBus.Properties', 'PropertiesChanged'))):
            record = self.players.get(self.owners[message.sender])
            follower = self.followers.get(record.get('app')) if record else None
            if follower:
                follower.announce()
                # The app's own state may trail its MPRIS player; read once more after it caught up.
                settle = getattr(follower, 'settle', 0)
                if settle:
                    pending = self.settling.pop(follower.app, None)
                    if pending:
                        pending.cancel()
                    self.settling[follower.app] = asyncio.get_running_loop().call_later(
                        settle, lambda: follower.announce(received=False))
        if (message.interface == 'org.freedesktop.DBus.Properties' and message.path == PATH
                and message.member == 'PropertiesChanged' and message.sender in self.owners
                and len(message.body) == 3 and message.body[0] == PLAYER):
            self._mark()
            changed = unwrap(message.body[1])
            if isinstance(changed, dict) and 'PlaybackStatus' in changed:
                key = self.owners[message.sender]
                record = self.players.get(key)
                if record:
                    previous = record['state']
                    record['state'] = STATES.get(changed['PlaybackStatus'], 'unknown')
                    self.changed(key, previous, record['state'])
        if (message.sender == 'org.freedesktop.DBus' and message.member == 'NameOwnerChanged'
                and len(message.body) == 3 and message.body[0].startswith(ROOT + '.')):
            self._mark()
        if (message.sender == 'org.freedesktop.DBus' and message.member == 'NameOwnerChanged'
                and len(message.body) == 3 and message.body[0].startswith(ROOT + '.') and not message.body[2]):
            owner = message.body[1]
            key = self.owners.get(owner)
            if key and key in self.players:
                self.players[key]['available'] = False
                self.changed(key, self.players[key]['state'], 'unavailable')

    async def _read(self, name):
        owner, pid, root, player = await asyncio.gather(
            call(self.bus, 'org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'GetNameOwner', 's', [name]),
            call(self.bus, 'org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'GetConnectionUnixProcessID', 's', [name]),
            properties(self.bus, name, PATH, ROOT), properties(self.bus, name, PATH, PLAYER), return_exceptions=True)
        if isinstance(owner, BaseException) or isinstance(player, BaseException):
            return None
        root = {} if isinstance(root, BaseException) else root
        pid = None if isinstance(pid, BaseException) else pid[0]
        owner = owner[0]
        identity = root.get('Identity', '')
        app = identify_app(root.get('DesktopEntry', ''), identity, pid)
        state = STATES.get(player.get('PlaybackStatus'), 'unknown')
        capabilities = []
        if player.get('CanControl') is True:
            if state == 'playing' and player.get('CanPause') is True:
                capabilities.append('pause')
            if state == 'paused' and player.get('CanPlay') is True:
                capabilities.append('play')
            if state in ('playing', 'paused'):
                capabilities.append('stop')
        return {'id': 'mpris:' + owner, 'bus_name': name, 'owner': owner, 'pid': pid,
                'app': app, 'name': identity[:256] if isinstance(identity, str) else 'Media player',
                'desktop': root.get('DesktopEntry', '')[:255] if isinstance(root.get('DesktopEntry'), str) else '', 'state': state, 'available': True,
                'role': 'music_assistant' if identity == 'Sendspin' else 'other',
                'capabilities': capabilities, 'media': media_state(player)}

    async def refresh(self):
        self.dirty = False
        try:
            names = (await call(self.bus, 'org.freedesktop.DBus', '/org/freedesktop/DBus',
                                'org.freedesktop.DBus', 'ListNames'))[0]
            names = sorted(name for name in names if name.startswith(ROOT + '.'))
            self.overflow = len(names) > 64
            results = await asyncio.gather(*(self._read(name) for name in names[:64]), return_exceptions=True)
            players = {item['id']: item for item in results if isinstance(item, dict)}
        except BridgeError:
            players = {}
        for key, previous in self.players.items():
            if key not in players:
                self.changed(key, previous['state'], 'unavailable')
        self.players = players
        self.owners = {record['owner']: key for key, record in players.items()}
        present = {record['app'] for record in players.values()}
        for app, hook in self.followers.items():
            # Without a player there is nothing to announce changes, so the hook is polled again.
            if hook.pushed != (app in present):
                hook.pushed = app in present
                hook.dirty = True
                hook.on_change()
        return list(players.values())

    async def control(self, key, command):
        # Re-read actual status before issuing an idempotent operation; never toggle.
        record = self.players.get(key)
        if not record:
            raise BridgeError('not_ready', 'MPRIS player is unavailable')
        if record.get('role') == 'music_assistant':
            raise BridgeError('music_assistant_required', 'Sendspin must be controlled through its associated Music Assistant entity')
        fresh = await self._read(record['bus_name'])
        if not fresh or fresh['id'] != key:
            raise BridgeError('not_ready', 'The underlying MPRIS player changed')
        desired = {'pause': 'paused', 'play': 'playing', 'stop': 'idle'}.get(command)
        if desired is None:
            raise BridgeError('unsupported', 'Unsupported MPRIS command')
        if fresh['state'] == desired:
            return {'changed': False, 'state': desired, 'completion_confirmed': True}
        if command not in fresh['capabilities']:
            raise BridgeError('unsupported', 'MPRIS player does not support this command')
        await call(self.bus, fresh['bus_name'], PATH, PLAYER, {'pause': 'Pause', 'play': 'Play', 'stop': 'Stop'}[command])
        for _ in range(15):
            observed = await self._read(fresh['bus_name'])
            if observed and observed['id'] == key and observed['state'] == desired:
                self.players[key] = observed
                return {'changed': True, 'state': desired, 'completion_confirmed': True}
            await asyncio.sleep(0.1)
        raise BridgeError('state_not_confirmed', 'MPRIS did not confirm the requested playback state')
