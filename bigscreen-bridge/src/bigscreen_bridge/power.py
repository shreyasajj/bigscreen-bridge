"""Observed KDE display and authorized logind operations, kept distinct from TV CEC."""
import asyncio
import re
import shutil
import time
from pathlib import Path

from dbus_next import MessageType

from .adapters import BridgeError
from .runtime_io import call, desktop_environment, properties, run

LOGIN = 'org.freedesktop.login1'
LOGIN_PATH = '/org/freedesktop/login1'
MANAGER = 'org.freedesktop.login1.Manager'
SCREEN = 'org.freedesktop.ScreenSaver'
SCREEN_PATH = '/ScreenSaver'
METHODS = {'suspend': ('CanSuspend', 'Suspend', 'sleep'),
           'shutdown': ('CanPowerOff', 'PowerOff', 'shutdown'),
           'restart': ('CanReboot', 'Reboot', 'shutdown')}
_DPMS = re.compile(r'^dpms mode for screen (.{1,128}): (on|off)$', re.MULTILINE)


def parse_dpms(text):
    modes = dict(_DPMS.findall(text))
    return {'supported': bool(modes), 'outputs': modes,
            'display_on': any(value == 'on' for value in modes.values()) if modes else None}


def blocked_inhibitors(rows, operation):
    return [{'what': row[0][:256], 'who': row[1][:256], 'why': row[2][:512], 'mode': row[3]}
            for row in rows if len(row) >= 4 and row[3] == 'block' and operation in row[0].split(':')]


def network_inventory(root=Path('/sys/class/net')):
    result = []
    for path in sorted(root.iterdir()) if root.exists() else []:
        if path.name == 'lo':
            continue
        try:
            mac = (path/'address').read_text().strip()
            if not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', mac):
                continue
            wake = path/'device/power/wakeup'
            result.append({'interface': path.name, 'mac': mac,
                           'hardware_wakeup': wake.read_text().strip() if wake.exists() else 'unknown',
                           'wol_verified_suspend': False, 'wol_verified_poweroff': False})
        except OSError:
            continue
    return result


class PowerManager:
    def __init__(self, session_bus, system_bus, config=None, runner=run, display_command=None,
                 changed=lambda: None, delay=asyncio.sleep, clock=time.monotonic):
        self.session_bus, self.system_bus = session_bus, system_bus
        self.config = config or {}
        allowed = self.config.get('allowed_system_actions', [])
        if not isinstance(allowed, list) or any(action not in METHODS for action in allowed):
            raise ValueError('allowed_system_actions must be a list of suspend, shutdown and restart')
        self.logind_owner = None
        self.runner = runner
        self.display_command = display_command or shutil.which('kscreen-doctor')
        self.changed, self.delay, self.clock = changed, delay, clock
        self.env = None
        self.inhibitors = []
        self.state = {'display': {'supported': False, 'display_on': None, 'outputs': {}},
                      'authorization': {}, 'capabilities': [], 'locked': None,
                      'system_state': 'awake', 'network': network_inventory()}
        self.on_prepare = lambda *_: None

    async def initialize(self):
        self.env = await desktop_environment(self.session_bus)
        if self.system_bus:
            self.system_bus.add_message_handler(self._signal)
            await call(self.system_bus, 'org.freedesktop.DBus', '/org/freedesktop/DBus',
                'org.freedesktop.DBus', 'AddMatch', 's',
                ["type='signal',sender='org.freedesktop.login1',path='/org/freedesktop/login1',interface='org.freedesktop.login1.Manager'"])
        await self.refresh()

    def _signal(self, message):
        if (message.message_type != MessageType.SIGNAL or message.sender != self.logind_owner
                or message.interface != MANAGER or message.path != LOGIN_PATH
                or message.member not in ('PrepareForSleep', 'PrepareForShutdown')
                or len(message.body) != 1 or not isinstance(message.body[0], bool)):
            return
        value = message.body[0]
        self.state['system_state'] = ('preparing_sleep' if message.member == 'PrepareForSleep' else 'preparing_shutdown') if value else 'awake'
        self.on_prepare(message.member, value)
        self.changed()

    async def _display(self):
        if not self.display_command:
            return {'supported': False, 'display_on': None, 'outputs': {}}
        try:
            return parse_dpms(await self.runner(self.display_command, '--dpms', 'show', env=self.env))
        except (BridgeError, OSError):
            return {'supported': False, 'display_on': None, 'outputs': {}}

    async def refresh(self):
        self.state['display'] = await self._display()
        authorization = {}
        try:
            self.logind_owner = (await call(self.system_bus, 'org.freedesktop.DBus', '/org/freedesktop/DBus',
                'org.freedesktop.DBus', 'GetNameOwner', 's', [LOGIN]))[0]
            for action, (can, _, _) in METHODS.items():
                authorization[action] = (await call(self.system_bus, LOGIN, LOGIN_PATH, MANAGER, can))[0]
            self.inhibitors = (await call(self.system_bus, LOGIN, LOGIN_PATH, MANAGER, 'ListInhibitors'))[0]
            flags = await properties(self.system_bus, LOGIN, LOGIN_PATH, MANAGER)
            self.state['system_state'] = ('preparing_shutdown' if flags.get('PreparingForShutdown') else
                                          'preparing_sleep' if flags.get('PreparingForSleep') else 'awake')
        except BridgeError:
            self.inhibitors = []
            authorization = {}
            self.state['system_state'] = 'unknown'
        try:
            self.state['locked'] = (await call(self.session_bus, SCREEN, SCREEN_PATH, SCREEN, 'GetActive'))[0]
        except BridgeError:
            self.state['locked'] = None
        self.state['authorization'] = authorization
        capabilities = ['screen_off', 'screen_on'] if self.state['display']['supported'] else []
        if isinstance(self.state['locked'], bool):
            capabilities.append('lock_screen')
        allowed = self.config.get('allowed_system_actions', [])
        self.state['blocked'] = {action: blocked_inhibitors(self.inhibitors, kind)[:32]
                                 for action, (_, _, kind) in METHODS.items()}
        for action in METHODS:
            if (action in allowed and authorization.get(action) == 'yes'
                    and not self.state['blocked'][action] and self.state['system_state'] == 'awake'):
                capabilities.append(action)
        self.state['capabilities'] = capabilities
        return self.state

    async def check_system(self, action):
        if action not in METHODS:
            raise BridgeError('unsupported', 'Unsupported system power action')
        await self.refresh()
        if action not in self.config.get('allowed_system_actions', []):
            raise BridgeError('not_authorized', 'System power actions are disabled in bridge configuration')
        authorization = self.state['authorization'].get(action)
        if authorization == 'challenge':
            raise BridgeError('authorization_required', 'Polkit requires authorization for this system action')
        if authorization != 'yes':
            raise BridgeError('unsupported', 'The system does not authorize this action')
        if self.state['blocked'][action]:
            raise BridgeError('inhibited', 'A system inhibitor blocks this action')
        if self.state['system_state'] != 'awake':
            raise BridgeError('busy', 'A system power operation is already in progress')

    async def submit_system(self, action):
        await self.check_system(action)
        await call(self.system_bus, LOGIN, LOGIN_PATH, MANAGER, METHODS[action][1], 'b', [False], timeout=20)
        return {'action': action, 'status': 'submitted', 'completion_confirmed': False}

    async def display(self, enabled):
        self.state['display'] = await self._display()
        if not self.state['display']['supported']:
            raise BridgeError('unsupported', 'Wayland display power control is unavailable')
        desired = 'on' if enabled else 'off'
        changed = any(mode != desired for mode in self.state['display']['outputs'].values())
        if changed:
            await self.runner(self.display_command, '--dpms', desired, env=self.env)
        deadline = self.clock() + 3
        while True:
            self.state['display'] = await self._display()
            values = self.state['display']['outputs'].values()
            confirmed = bool(values) and all(mode == desired for mode in values)
            if confirmed or self.clock() >= deadline:
                break
            await self.delay(0.2)
        self.changed()
        return {'action': 'screen_on' if enabled else 'screen_off', 'changed': changed,
                'status': 'completed' if confirmed else 'accepted', 'completion_confirmed': confirmed,
                'display_on': self.state['display']['display_on'], 'physical_tv_power': 'unknown'}

    async def lock(self):
        if self.state['locked'] is None:
            raise BridgeError('unsupported', 'Screen locking is unavailable')
        await call(self.session_bus, SCREEN, SCREEN_PATH, SCREEN, 'Lock')
        self.state['locked'] = (await call(self.session_bus, SCREEN, SCREEN_PATH, SCREEN, 'GetActive'))[0]
        self.changed()
        return {'action': 'lock_screen', 'status': 'completed' if self.state['locked'] else 'accepted',
                'completion_confirmed': self.state['locked'] is True}
