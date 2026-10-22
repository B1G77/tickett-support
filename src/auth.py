"""Dashboard accounts, sessions and login lockout (stdlib only).

Two fixed accounts (admin, agent) with scrypt-hashed passwords stored in accounts.json,
in-memory sessions that expire after idling, and a per-key failed-login lockout.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Callable

ROLES = {"admin": "admin", "agent": "agent"}
MIN_PASSWORD = 10
_SCRYPT = dict(n=2**14, r=8, p=1, dklen=32)


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)


class Accounts:
    """accounts.json: {"admin": {"salt": hex, "hash": hex}, ...}, mode 0600."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            return {}

    def set_password(self, user: str, password: str) -> None:
        if user not in ROLES:
            raise ValueError(f"unknown user: {user!r}")
        if len(password) < MIN_PASSWORD:
            raise ValueError(f"password must be at least {MIN_PASSWORD} characters")
        data = self._load()
        salt = os.urandom(16)
        data[user] = {"salt": salt.hex(), "hash": _hash(password, salt).hex()}
        tmp = self.path.with_name(self.path.name + ".tmp")
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:   # never readable
            f.write(json.dumps(data, indent=2))
        tmp.chmod(0o600)   # a .tmp left from before keeps its old mode
        tmp.replace(self.path)

    def check(self, user: str, password: str) -> bool:
        rec = self._load().get(user)
        if not rec:
            return False
        got = _hash(password, bytes.fromhex(rec["salt"]))
        return hmac.compare_digest(got, bytes.fromhex(rec["hash"]))


class Sessions:
    """In-memory sessions; each use refreshes the idle timer."""

    def __init__(self, idle_seconds: int = 8 * 3600, now: Callable[[], float] = time.time):
        self.idle = idle_seconds
        self.now = now
        self._s: dict[str, tuple[str, float]] = {}   # token -> (user, last seen)

    def create(self, user: str) -> str:
        token = secrets.token_urlsafe(32)
        self._s[token] = (user, self.now())
        return token

    def get(self, token: str) -> str | None:
        rec = self._s.get(token)
        if not rec:
            return None
        user, seen = rec
        if self.now() - seen > self.idle:
            self._s.pop(token, None)   # another request may have dropped it meanwhile
            return None
        self._s[token] = (user, self.now())
        return user

    def drop(self, token: str) -> None:
        self._s.pop(token, None)


class Lockout:
    """Lock a key (e.g. client address) for lock_seconds after max_tries failures."""

    def __init__(self, max_tries: int = 5, lock_seconds: int = 900, now: Callable[[], float] = time.time):
        self.max, self.lock, self.now = max_tries, lock_seconds, now
        self._f: dict[str, tuple[int, float]] = {}   # key -> (failures, locked until)

    def locked(self, key: str) -> bool:
        return self._f.get(key, (0, 0.0))[1] > self.now()

    def failed(self, key: str) -> None:
        count, until = self._f.get(key, (0, 0.0))
        if until and until <= self.now():   # an old lock ran out: start counting afresh
            count, until = 0, 0.0
        count += 1
        if count >= self.max:
            until = self.now() + self.lock
        self._f[key] = (count, until)

    def succeeded(self, key: str) -> None:
        self._f.pop(key, None)
