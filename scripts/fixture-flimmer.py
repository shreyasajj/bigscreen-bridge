#!/usr/bin/env python3
"""Real TV player test using a synthetic local IPTV feed and a separate profile."""
import asyncio
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import uuid

import aiohttp
from pathlib import Path
import signal
import subprocess
import tempfile
import threading

spec = importlib.util.spec_from_file_location('smoke_flimmer', Path(__file__).with_name('smoke-flimmer.py'))
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


async def main():
    base = Path.home()/'.var/app/io.github.jclsn.Flimmer/data/bigscreen-bridge-tests'
    base.mkdir(parents=True, exist_ok=True)
    profile = Path(tempfile.mkdtemp(prefix='iptv-', dir=base))
    media = profile/'fixture.ts'
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                    'smptebars=size=640x360:rate=25', '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
                    '-t', '60', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', '-f', 'mpegts', str(media)], check=True)
    processes = set()
    requests = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            requests[self.path] = requests.get(self.path, 0) + 1
            if self.path == '/stream.ts':
                self.send_response(200)
                self.send_header('Content-Type', 'video/mp2t')
                self.end_headers()
                proc = subprocess.Popen(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-re',
                    '-stream_loop', '-1', '-i', str(media), '-c', 'copy', '-f', 'mpegts', 'pipe:1'],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                processes.add(proc)
                try:
                    while data := proc.stdout.read(188 * 32):
                        self.wfile.write(data)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    proc.terminate()
                    proc.wait()
                    processes.discard(proc)
                return
            if self.path == '/channels.m3u':
                text = '#EXTM3U\n' + ''.join(
                    f'#EXTINF:-1 tvg-id="fixture-{i}" tvg-chno="{i + 1}",Bridge test {i + 1}\n'
                    f'http://127.0.0.1:{self.server.server_port}/stream.ts\n' for i in range(55))
            elif self.path == '/guide.xml':
                now = datetime.now(timezone.utc)
                def stamp(value):
                    return value.strftime('%Y%m%d%H%M%S +0000')
                programmes = ''.join(
                    f'<programme channel="fixture-{i}" start="{stamp(now - timedelta(minutes=5))}" '
                    f'stop="{stamp(now + timedelta(minutes=25))}"><title>Bridge test current</title></programme>'
                    f'<programme channel="fixture-{i}" start="{stamp(now + timedelta(minutes=25))}" '
                    f'stop="{stamp(now + timedelta(minutes=55))}"><title>Bridge test next</title></programme>'
                    for i in range(55))
                text = '<?xml version="1.0"?><tv>' + programmes + '</tv>'
            else:
                self.send_error(404)
                return
            data = text.encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    config = profile/'config/flimmer/flimmer.conf'
    config.parent.mkdir(parents=True)
    config.write_text(f'''[sources]
size=1
1\\id=src-fixture
1\\index=0
1\\type=m3u
1\\enabled=true
1\\name=Bridge test fixture
1\\fields\\playlistUrl=http://127.0.0.1:{server.server_port}/channels.m3u
1\\fields\\xmltvUrl=http://127.0.0.1:{server.server_port}/guide.xml

[playback]
buffer=0
stopInBackground=false
confirmQuit=false
''')
    try:
        smoke.PROFILE = profile
        smoke.CAPTURE_PATH = profile / "playback.png"
        await smoke.main(True, require_playback=True)
    finally:
        for pid in smoke.ui_processes():
            import os
            os.kill(pid, signal.SIGTERM)
        for _ in range(30):
            if not smoke.ui_processes():
                break
            await asyncio.sleep(0.1)
        smoke.PROFILE = None
        # Let the user service own the normal GUI, so it survives the SSH test session.
        settings = json.loads((Path.home()/'.config/bigscreen-bridge/config.json').read_text())
        async with aiohttp.ClientSession(headers={'Authorization': 'Bearer ' + settings['token']}) as session:
            async with session.ws_connect('http://127.0.0.1:8765/ws') as ws:
                await ws.receive_json()
                for app in ('flimmer', 'couchside'):
                    request_id = str(uuid.uuid4())
                    await ws.send_json(dict(id=request_id, command='focus', app=app))
                    async with asyncio.timeout(45):
                        while True:
                            reply = await ws.receive_json()
                            if reply.get('id') == request_id:
                                assert reply['ok'], reply.get('error', {}).get('code')
                                break
                    if app == 'flimmer':
                        local_bus = await smoke.MessageBus().connect()
                        try:
                            state = (await smoke.request(local_bus, 'get_state'))['result']
                        finally:
                            local_bus.disconnect()
                        assert state['ready'] and len(smoke.ui_processes()) == 1
                        print('PASS bridge cold launch restores the normal Flimmer profile')
        server.shutdown()
        for proc in list(processes):
            proc.terminate()
        print("Fixture profile:", profile)
        print('Fixture requests:', requests)
        print('Restored the normal Flimmer profile; synthetic fixture profile kept for inspection')


if __name__ == '__main__':
    asyncio.run(main())
