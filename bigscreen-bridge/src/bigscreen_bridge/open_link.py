"""KDE URI handler: validate once, then send a request to the existing user service."""
import argparse
import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import aiohttp
from yarl import URL

from .adapters import BridgeError
from .config import load_config
from .deep_links import parse_uri


def desktop_entry(executable):
    # Desktop string escaping is applied before Exec argument unquoting.
    escaped = ''.join('\\' + char if char in '\\"`$' else char for char in executable)
    escaped = escaped.replace('\\', '\\\\').replace('%', '%%')
    return ('[Desktop Entry]\nType=Application\nName=Bigscreen Bridge Link\nNoDisplay=true\n'
            'Terminal=false\nIcon=video-display\nMimeType=x-scheme-handler/bigscreen;\n'
            'Exec="' + escaped + '" --notify %u\n')


async def open_uri(uri, config_path):
    parse_uri(uri)
    config = load_config(config_path)
    host = config.get('host', '127.0.0.1')
    host = {'0.0.0.0': '127.0.0.1', '::': '::1'}.get(host, host)
    url = URL.build(scheme='http', host=host, port=config.get('port', 8765), path='/ws')
    async with aiohttp.ClientSession(headers={'Authorization': 'Bearer ' + config['token']}) as session:
        # A simple user service can be active before KWin/HTTP startup completes.
        async with asyncio.timeout(8):
            while True:
                try:
                    ws = await session.ws_connect(url)
                    break
                except aiohttp.ClientConnectorError:
                    await asyncio.sleep(0.2)
                except aiohttp.WSServerHandshakeError as exc:
                    if exc.status == 401:
                        raise BridgeError('invalid_auth', 'Bridge rejected the configured token') from exc
                    raise
        async with ws:
            async with asyncio.timeout(75):
                await ws.receive_json()
                request_id = str(uuid.uuid4())
                await ws.send_json({'id': request_id, 'command': 'open_uri', 'uri': uri})
                async for message in ws:
                    if message.type != aiohttp.WSMsgType.TEXT:
                        break
                    response = json.loads(message.data)
                    if response.get('id') != request_id:
                        continue
                    if not response.get('ok'):
                        raise BridgeError(response['error']['code'], response['error']['message'])
                    return response['result']
    raise BridgeError('disconnected', 'Bridge disconnected before acknowledging the link')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('uri')
    parser.add_argument('--config', type=Path, default=Path.home()/'.config/bigscreen-bridge/config.json')
    parser.add_argument('--notify', action='store_true', help='Show a desktop notification if delivery fails')
    args = parser.parse_args()
    try:
        result = asyncio.run(open_uri(args.uri, args.config))
        print(json.dumps(result))
    except (BridgeError, ValueError, OSError, aiohttp.ClientError, TimeoutError) as exc:
        message = str(exc) if isinstance(exc, (BridgeError, ValueError)) else 'Cannot reach the Bigscreen user service'
        print(message, file=sys.stderr)
        if args.notify and shutil.which('notify-send'):
            subprocess.run(['notify-send', '--app-name=Bigscreen Bridge', 'Bigscreen link failed', message], check=False)
        raise SystemExit(1) from None
