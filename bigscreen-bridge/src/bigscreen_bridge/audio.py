"""Local PipeWire/PulseAudio observation and explicit system audio commands."""
import json
import math
from pathlib import Path
import re
import shutil

from .adapters import APPS, BridgeError
from .runtime_io import run

_VOLUME = re.compile(r'^Volume: ([0-9]+(?:\.[0-9]+)?)( \[MUTED\])?\s*$')
_FLATPAK = re.compile(r'app-flatpak-([A-Za-z0-9_.-]+)-[0-9]+\.scope')


def process_app_id(pid, proc=Path('/proc')):
    try:
        match = _FLATPAK.search((proc/str(int(pid))/'cgroup').read_text())
        return match[1] if match else None
    except (OSError, ValueError, TypeError):
        return None


def identify_app(desktop='', identity='', pid=None):
    flatpak = process_app_id(pid) if pid is not None else None
    if identity == 'Sendspin' or desktop == 'org.sendspin.cli':
        return 'couchside'
    names = {str(value).lower().removesuffix('.desktop') for value in (desktop, identity, flatpak) if value}
    for app in APPS:
        if names & {value.lower() for value in (app.flatpak, app.name, *app.aliases)}:
            return app.id
    return None


def parse_volume(text):
    match = _VOLUME.fullmatch(text.strip())
    if not match:
        raise BridgeError('unsupported', 'Cannot read the default audio sink')
    raw = float(match[1])
    return {'available': True, 'volume': min(raw, 1.0), 'raw_volume': raw,
            'muted': bool(match[2]), 'capabilities': ['mute', 'unmute', 'set_volume']}


def parse_streams(rows):
    result = []
    if not isinstance(rows, list):
        return result
    for row in rows[:128]:
        if not isinstance(row, dict):
            continue
        props = row.get('properties') or {}
        if not isinstance(props, dict):
            continue
        pid = props.get('application.process.id')
        app = identify_app(props.get('flatpak.app-id') or props.get('application.id', ''), props.get('application.name', ''), pid)
        try:
            index = int(row['index'])
            pid = int(pid) if pid else None
            volumes = [value['value_percent'] for value in row.get('volume', {}).values()]
            levels = [float(value.rstrip('%')) / 100 for value in volumes if isinstance(value, str)]
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        result.append({'index': index, 'pid': pid, 'app': app,
                       'serial': str(props.get('object.serial', ''))[:64],
                       'role': 'sendspin' if props.get('application.id') == 'org.sendspin.cli' else None,
                       'stream_active': row.get('corked') is False and row.get('mute') is False,
                       # An uncorked stream can contain silence; no sample-level assertion.
                       'producing_audio': None, 'muted': row.get('mute'),
                       'volume': sum(levels) / len(levels) if levels else None})
    return result


class AudioManager:
    def __init__(self, runner=run, wpctl=None, pactl=None):
        self.runner = runner
        self.wpctl = wpctl or shutil.which('wpctl')
        self.pactl = pactl or shutil.which('pactl')
        self.state = {'available': False, 'capabilities': [], 'volume': None, 'muted': None, 'streams': []}

    async def refresh(self):
        try:
            if not self.wpctl:
                raise BridgeError('unsupported', 'PipeWire control is unavailable')
            state = parse_volume(await self.runner(self.wpctl, 'get-volume', '@DEFAULT_AUDIO_SINK@'))
        except (BridgeError, OSError):
            state = {'available': False, 'capabilities': [], 'volume': None, 'muted': None}
        try:
            rows = json.loads(await self.runner(self.pactl, '--format=json', 'list', 'sink-inputs')) if self.pactl else []
            state['streams'] = parse_streams(rows)
        except (BridgeError, OSError, ValueError):
            state['streams'] = []
        self.state = state
        return state

    async def execute(self, command, level=None):
        if command not in ('mute', 'unmute', 'set_volume'):
            raise BridgeError('unsupported', 'Unsupported global audio command')
        if command == 'set_volume':
            try:
                valid = not isinstance(level, bool) and isinstance(level, (int, float)) and math.isfinite(level) and 0 <= level <= 1
            except OverflowError:
                valid = False
            if not valid:
                raise BridgeError('invalid_request', 'Volume must be between zero and one')
        await self.refresh()
        if not self.state['available']:
            raise BridgeError('unsupported', 'System audio control is unavailable')
        if command == 'set_volume':
            changed = abs(self.state['volume'] - level) > 0.005
            if changed:
                await self.runner(self.wpctl, 'set-volume', '@DEFAULT_AUDIO_SINK@', f'{level:.4f}')
        else:
            desired = command == 'mute'
            changed = self.state['muted'] != desired
            if changed:
                await self.runner(self.wpctl, 'set-mute', '@DEFAULT_AUDIO_SINK@', '1' if desired else '0')
        await self.refresh()
        confirmed = (abs(self.state['volume'] - level) <= 0.01 if command == 'set_volume'
                     else self.state['muted'] == (command == 'mute'))
        return {'command': command, 'changed': changed, 'status': 'completed' if confirmed else 'accepted',
                'completion_confirmed': confirmed, 'volume': self.state['volume'], 'muted': self.state['muted']}
