"""Short-lived pairing codes shown on the TV, exchanged for a token per client.

The TV shows an 8-character code (on the Bridge status screen, read through the
session bus). A client sends it to `POST /pair` and receives its own bearer token.
Only a SHA-256 hash of each token is stored, in a user-private `clients.json` next
to the config. The config's original `token` keeps working.

A code lives 5 minutes and allows 5 wrong attempts. While no client has paired, a
code is kept available all the time; afterwards one is made only on request
(`StartPairing()` on the session bus). Redeeming a code ends it at once. Codes and
tokens never reach logs, diagnostics, WebSocket replies other than `/pair`, or MQTT.
"""
import asyncio
from collections import deque
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import secrets
import time
from datetime import datetime, timezone

from .adapters import BridgeError
from .diagnostics import utc

LOG = logging.getLogger(__name__)

ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"     # no 0/O, 1/I/L or U: readable from a sofa
CODE_LENGTH = 8
LIFETIME = 300
MAX_FAILURES = 5
ATTEMPTS_PER_MINUTE = 20
MAX_CLIENTS = 32
NAME = re.compile(r"[^\x00-\x1f\x7f]{1,64}")


def normalize(code):
    """What a person typed, without spaces, dashes or case."""
    if not isinstance(code, str) or len(code) > 32:
        return None
    code = re.sub(r"[\s-]", "", code).upper()
    return code if len(code) == CODE_LENGTH and all(c in ALPHABET for c in code) else None


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Pairing:
    def __init__(self, path: Path, changed=lambda: None, clock=time.time, monotonic=time.monotonic):
        self.path = Path(path)
        self.changed, self.clock, self.monotonic = changed, clock, monotonic
        self.loaded = None
        self.clients = self._load()
        self.code = None
        self.expires = 0.0
        self.reason = None
        self.failures = 0
        self.attempts = deque()
        self.last_paired = None
        self.timer = None

    # -- storage ----------------------------------------------------------------

    def _stamp(self):
        try:
            info = self.path.stat()
            return info.st_mtime_ns, info.st_size
        except OSError:
            return None

    def _reload(self):
        """Pick up `bigscreen-bridge-clients revoke` without a restart."""
        if self._stamp() != self.loaded:
            self.clients = self._load()

    def _load(self):
        self.loaded = self._stamp()
        try:
            data = json.loads(self.path.read_text())
            clients = data.get("clients", []) if isinstance(data, dict) else []
            return [c for c in clients if isinstance(c, dict) and isinstance(c.get("hash"), str)
                    and len(c["hash"]) == 64][:MAX_CLIENTS]
        except FileNotFoundError:
            return []
        except (OSError, ValueError):
            LOG.warning("Paired clients file is unreadable; only the configured token is accepted")
            return []

    def _save(self):
        temporary = self.path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump({"clients": self.clients}, stream, indent=2)
        os.replace(temporary, self.path)
        self.loaded = self._stamp()

    # -- codes ------------------------------------------------------------------

    @property
    def automatic(self):
        """Nothing has paired yet, so a code is kept on screen."""
        return not self.clients

    def _expired(self):
        return self.code is not None and self.monotonic() >= self.expires

    def _issue(self, reason):
        self.code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
        self.expires = self.monotonic() + LIFETIME
        self.reason, self.failures = reason, 0
        self._schedule()
        LOG.info("Pairing code issued (%s), valid %d min", reason, LIFETIME // 60)
        self.changed()

    def _end(self):
        self.code, self.reason, self.failures = None, None, 0
        if self.timer:
            self.timer.cancel()
            self.timer = None

    def _schedule(self):
        """Replace or drop the code when it runs out, even if nobody is looking."""
        if self.timer:
            self.timer.cancel()
        try:
            self.timer = asyncio.get_running_loop().call_later(LIFETIME + 0.05, self.refresh)
        except RuntimeError:
            self.timer = None

    def refresh(self):
        """Expire the code; keep one available while nothing has paired."""
        self._reload()
        if self._expired():
            self._end()
            self.changed()
        if self.code is None and self.automatic:
            self._issue("unpaired")

    def start(self):
        """A person asked for a new code on the TV."""
        self._end()
        self._issue("requested")

    def public(self):
        """For the TV screen only (`Status()` on the session bus)."""
        self.refresh()
        if self.code is None:
            return None
        return {"code": self.code[:4] + "-" + self.code[4:], "reason": self.reason,
                "expires_at": utc(datetime.fromtimestamp(self.clock() + max(0.0, self.expires - self.monotonic()),
                                                         timezone.utc))}

    def summary(self):
        self._reload()
        return self._summary()

    def _summary(self):
        """For diagnostics: never the code itself."""
        active = self.code is not None and not self._expired()
        return {"active": active, "reason": self.reason if active else None, "paired_clients": len(self.clients),
                "last_paired_at": self.last_paired and self.last_paired["at"],
                "last_paired_name": self.last_paired and self.last_paired["name"]}

    # -- redeeming and checking -------------------------------------------------

    def redeem(self, code, name):
        now = self.monotonic()
        while self.attempts and now - self.attempts[0] > 60:
            self.attempts.popleft()
        if len(self.attempts) >= ATTEMPTS_PER_MINUTE:
            raise BridgeError("rate_limited", "Too many pairing attempts; wait a minute")
        self.attempts.append(now)
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise BridgeError("invalid_request", "A client name of 1-64 printable characters is required")
        self.refresh()
        wanted = normalize(code)
        if self.code is None or wanted is None or not hmac.compare_digest(wanted, self.code):
            if self.code is not None:
                self.failures += 1
                if self.failures >= MAX_FAILURES:
                    LOG.warning("Pairing code withdrawn after %d wrong attempts", self.failures)
                    self._end()
                    self.refresh()   # a fresh code if nothing has paired yet
                    self.changed()
                else:
                    LOG.warning("Rejected a pairing attempt (%d left)", MAX_FAILURES - self.failures)
            raise BridgeError("invalid_code", "The pairing code is wrong or has expired")
        if len(self.clients) >= MAX_CLIENTS:
            raise BridgeError("too_many_clients", "Too many paired clients")
        token = secrets.token_urlsafe(32)
        at = utc(datetime.fromtimestamp(self.clock(), timezone.utc))
        self.clients.append({"id": secrets.token_hex(8), "name": name, "hash": digest(token), "created_at": at})
        self._save()
        self.last_paired = {"name": name, "at": at}
        self._end()
        LOG.info("Paired a new client: %s", name)
        self.changed()
        return token

    def accepts(self, token):
        if not isinstance(token, str) or not token:
            return False
        self._reload()
        wanted = digest(token)
        # Compare against every entry so the time taken does not say which one matched.
        return any([hmac.compare_digest(wanted, client["hash"]) for client in self.clients])


def main(argv=None):
    """`bigscreen-bridge-clients list` / `revoke <id>`: manage paired clients on the TV."""
    import argparse
    parser = argparse.ArgumentParser(prog="bigscreen-bridge-clients")
    parser.add_argument("--config-dir", type=Path, default=Path.home() / ".config" / "bigscreen-bridge")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="paired clients (never their tokens)")
    revoke = commands.add_parser("revoke", help="remove one client; its token stops working at once")
    revoke.add_argument("id")
    args = parser.parse_args(argv)
    pairing = Pairing(args.config_dir / "clients.json")
    if args.command == "list":
        for client in pairing.clients:
            print(f"{client.get('id', '?')}  {client.get('created_at', '?')}  {client.get('name', '')}")
        if not pairing.clients:
            print("No paired clients; the TV shows a pairing code.")
        return 0
    kept = [c for c in pairing.clients if c.get("id") != args.id]
    if len(kept) == len(pairing.clients):
        print(f"No client {args.id}")
        return 1
    pairing.clients = kept
    pairing._save()
    print(f"Revoked {args.id}")
    return 0
