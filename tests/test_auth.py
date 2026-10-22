"""Tests for auth.py: the two dashboard accounts, idle-expiring sessions and the login lockout."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import auth as A


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_a_set_password_checks_and_a_wrong_one_fails(tmp_path):
    path = tmp_path / "accounts.json"
    acc = A.Accounts(path)
    acc.set_password("admin", "correct horse 1")
    assert acc.check("admin", "correct horse 1")
    assert not acc.check("admin", "wrong")
    assert not acc.check("nobody", "correct horse 1")
    assert (path.stat().st_mode & 0o777) == 0o600
    assert "correct horse 1" not in path.read_text()
    assert A.Accounts(path).check("admin", "correct horse 1")   # survives a reload


def test_the_temporary_accounts_file_is_created_private(tmp_path):
    opened, real = [], A.os.open
    A.os.open = lambda path, flags, mode=0o777: (opened.append((Path(path).name, flags, mode)), real(path, flags, mode))[1]
    try:
        A.Accounts(tmp_path / "accounts.json").set_password("admin", "correct horse 1")
    finally:
        A.os.open = real
    assert opened == [("accounts.json.tmp", A.os.O_WRONLY | A.os.O_CREAT | A.os.O_TRUNC, 0o600)]


def test_unknown_users_and_short_passwords_are_refused(tmp_path):
    acc = A.Accounts(tmp_path / "accounts.json")
    for user, pw in (("boss", "long enough pw"), ("agent", "short")):
        try:
            acc.set_password(user, pw)
        except ValueError:
            continue
        raise AssertionError(f"{user!r}/{pw!r} was accepted")


def test_a_session_lives_while_used_and_expires_after_idle():
    clock = Clock()
    s = A.Sessions(now=clock)
    token = s.create("admin")
    clock.t = 7 * 3600
    assert s.get(token) == "admin"
    clock.t = 7 * 3600 + 8 * 3600 + 1
    assert s.get(token) is None
    assert s.get(token) is None   # removed
    token2 = s.create("agent")
    s.drop(token2)
    assert s.get(token2) is None


def test_five_wrong_tries_lock_for_fifteen_minutes():
    clock = Clock()
    lk = A.Lockout(now=clock)
    for _ in range(4):
        lk.failed("1.2.3.4")
    assert not lk.locked("1.2.3.4")
    lk.failed("1.2.3.4")
    assert lk.locked("1.2.3.4")
    clock.t = 899
    assert lk.locked("1.2.3.4")
    clock.t = 901
    assert not lk.locked("1.2.3.4")
    for _ in range(4):
        lk.failed("k")
    lk.succeeded("k")
    lk.failed("k")
    assert not lk.locked("k")
