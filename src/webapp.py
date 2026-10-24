#!/usr/bin/env python3
"""The support dashboard: a reply draft for every open email, to send, edit, have rewritten or close
without a reply, and the health of the stack. Stdlib only.

It listens on 127.0.0.1 only; a reverse proxy in front puts it on the network.
Everyone logs in (two accounts, admin and agent; see auth.py) and
gets a session cookie; /api/admin/* is for the admin only. The React build in web/dist is served
to anyone, the API only to a session. Every request must name an expected Host (no DNS
rebinding); every POST needs the page's own header and a JSON body from the same origin (no
cross-site requests); the page may only load its own files (Content-Security-Policy).

Usage: webapp.py                        (serves until stopped)
       webapp.py --set-password admin   (or agent: set that account's password)
"""
from __future__ import annotations

import getpass
import json
import sys
import threading
import traceback
from datetime import date, datetime, timedelta
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import actions as AC
import auth
import autosend as AS
import drafts as DR
import metrics
import router as RT
from api import health_json, state_json

DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
                 ".woff2": "font/woff2", ".woff": "font/woff", ".ico": "image/x-icon"}
ACTION_HEADER = "X-Tickett-Dashboard"
COOKIE = "tickett_session"
MAX_BODY = 16384
MAX_PERIOD_DAYS = 400
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                               "img-src 'self' data:; font-src 'self'; base-uri 'none'; form-action 'none'; "
                               "frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}
ERR_LOGIN = {"error": "Utilizador ou palavra-passe errados."}
ERR_LOCKED = {"error": "Demasiadas tentativas. Tente daqui a 15 minutos."}
ERR_SESSION = {"error": "Sessão terminada."}


class App:
    """Shared state for the request handlers: config, accounts and sessions, and one action per draft at a time."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.web = cfg.get("webapp", {})
        self.port = int(self.web.get("port", 8787))
        self.hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}", *self.web.get("hosts", [])}
        # the page's own addresses: local, and https on the configured host names
        self.origins = {f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}",
                        *(f"https://{h}" for h in self.web.get("hosts", []))}
        self.dist = Path(self.web.get("dist") or DIST)
        self.secure_cookie = bool(self.web.get("secure_cookie"))
        self.trusted_proxy = bool(self.web.get("trusted_proxy"))
        self.accounts = auth.Accounts(Path(cfg["state_dir"]) / "accounts.json")
        self.sessions = auth.Sessions()
        self.lockout = auth.Lockout()
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self.log = RT.Log(Path(cfg["state_dir"]) / "router.log")
        self._drafter = self._redactor = None

    def state(self):
        return RT.State(Path(self.cfg["state_dir"]) / "router.db")

    def lock_for(self, message_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(message_id, threading.Lock())

    def drafter(self):
        if self._drafter is None:
            RT.load_secret_env()
            from drafter import Drafter
            self._drafter, self._redactor = Drafter(), RT.build_redactor()
        return self._drafter, self._redactor

    def allowed(self, headers, client: str) -> str:
        """"" when the request may proceed, else why not."""
        if client not in ("127.0.0.1", "::1"):
            return "not from this machine"
        if headers.get("Host", "") not in self.hosts:
            return "unexpected host"
        return ""

    def login(self, username: str, password: str, forwarded: str) -> tuple[int, dict, str]:
        """(status, body, session token or "") for a login attempt."""
        name = username if username in auth.ROLES else "?"   # never log what a stranger typed
        # the proxy appends the address it saw: the right-most entry is the only one the client cannot forge
        key = (forwarded.split(",")[-1].strip() if self.trusted_proxy and forwarded else name)[:64]
        if self.lockout.locked(key):
            return 429, ERR_LOCKED, ""
        try:
            ok = username in auth.ROLES and self.accounts.check(username, password)
        except Exception:   # unreadable or corrupt accounts file: same answer as a wrong password
            traceback.print_exc()
            ok = False
        if not ok:
            self.lockout.failed(key)
            self.log("dashboard", f"login failed {name}")
            if self.lockout.locked(key):
                self.log("dashboard", f"locked {name}")
            return 401, ERR_LOGIN, ""
        self.lockout.succeeded(key)
        self.log("dashboard", f"login {username}")
        return 200, {"user": username, "role": auth.ROLES[username]}, self.sessions.create(username)

    def act(self, action: str, body: dict) -> dict:
        cid, version = str(body.get("id", "")), str(body.get("version", ""))
        st = self.state()
        with self.lock_for(cid):
            if action == "send":
                return {"message": AC.send(self.cfg, st, cid, version, self.log)}
            if action == "rewrite":
                drafter, redactor = self.drafter()
                return {"message": AC.rewrite(self.cfg, st, cid, version, str(body.get("comment", "")), self.log,
                                              drafter, redactor)}
            if action == "answered":
                return {"message": AC.answered_by_hand(self.cfg, st, cid, version, self.log)}
            if action == "to":
                message, new_version = AC.set_to(self.cfg, st, cid, version, str(body.get("to", ""))[:320], self.log)
                return {"message": message, "version": new_version}
            if action == "edit":
                message, new_version = AC.edit(self.cfg, st, cid, version, str(body.get("body", "")), self.log)
                return {"message": message, "version": new_version}
        raise LookupError("unknown action")

    def static(self, path: str) -> tuple[int, bytes, str]:
        """A file of the build, else index.html (client-side routes); 404 for anything outside it."""
        root = self.dist.resolve()
        rel = unquote(path).lstrip("/")
        if "\0" in rel:
            return 404, b"", ""
        target = (root / rel).resolve()
        if target != root and root not in target.parents:
            return 404, b"", ""
        if not target.is_file():
            target = root / "index.html"
        if not target.is_file():
            return 503, b"", ""
        return 200, target.read_bytes(), CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "tickett-dashboard"
        sys_version = ""

        def log_message(self, fmt, *args):   # no request log: URLs carry nothing useful, keep stderr quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in {**SECURITY_HEADERS, **(extra or {})}.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj, extra: dict | None = None) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", extra)

        def _gate(self) -> bool:
            why = app.allowed(self.headers, self.client_address[0])
            if why:
                self._json(403, {"error": "Sem acesso."})
                return False
            return True

        def _token(self) -> str:
            try:
                return SimpleCookie(self.headers.get("Cookie", ""))[COOKIE].value
            except Exception:   # no cookie, or a malformed one
                return ""

        def _user(self) -> str | None:
            token = self._token()
            return app.sessions.get(token) if token else None

        def _cookie(self, value: str, max_age: int | None = None) -> dict:
            c = f"{COOKIE}={value}; HttpOnly; SameSite=Strict; Path=/" + ("; Secure" if app.secure_cookie else "")
            return {"Set-Cookie": c + (f"; Max-Age={max_age}" if max_age is not None else "")}

        def _read(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                raise ValueError("Pedido demasiado grande.")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Pedido inválido.")
            return body

        def do_GET(self):
            if not self._gate():
                return
            path = self.path.split("?", 1)[0]
            if path.startswith("/api/"):
                user = self._user()
                if not user:
                    self._json(401, ERR_SESSION)
                elif path == "/api/me":
                    self._json(200, {"user": user, "role": auth.ROLES[user]})
                elif path == "/api/state":
                    try:
                        self._json(200, state_json(app.cfg, app.state()))
                    except Exception:
                        traceback.print_exc()
                        self._json(500, {"error": "Não foi possível ler o estado."})
                elif path.startswith("/api/admin/"):
                    if auth.ROLES[user] != "admin":
                        self._json(403, {"error": "Sem acesso."})
                    elif path == "/api/admin/metrics":
                        q = parse_qs(urlsplit(self.path).query)
                        try:
                            end = date.fromisoformat(q["to"][0]) if "to" in q else datetime.now().astimezone().date()
                            start = date.fromisoformat(q["from"][0]) if "from" in q else end - timedelta(days=29)
                        except (ValueError, OverflowError):
                            self._json(400, {"error": "Datas inválidas."})
                            return
                        if abs((end - start).days) + 1 > MAX_PERIOD_DAYS:
                            self._json(400, {"error": f"Escolha um período de até {MAX_PERIOD_DAYS} dias."})
                            return
                        try:
                            self._json(200, metrics.report(app.cfg, app.state(), start, end,
                                                           datetime.now().astimezone()))
                        except Exception:
                            traceback.print_exc()
                            self._json(500, {"error": "Não foi possível calcular os números."})
                    elif path == "/api/admin/autosend":
                        try:
                            self._json(200, AS.status(app.cfg, app.state()))
                        except Exception:
                            traceback.print_exc()
                            self._json(500, {"error": "Não foi possível ler o envio automático."})
                    elif path == "/api/admin/health":
                        try:
                            self._json(200, health_json(app.cfg, app.state()))
                        except Exception:
                            traceback.print_exc()
                            self._json(500, {"error": "Não foi possível ler a saúde do sistema."})
                    else:
                        self._json(404, {"error": "Não existe."})
                else:
                    self._json(404, {"error": "Não existe."})
                return
            code, body, ctype = app.static(path)
            if code == 200:
                self._send(200, body, ctype)
            elif code == 503:
                self._json(503, {"error": "A aplicação ainda não foi compilada."})
            else:
                self._json(404, {"error": "Não existe."})

        def do_POST(self):
            if not self._gate():
                return
            origin = self.headers.get("Origin")
            if self.headers.get(ACTION_HEADER) != "1" or "application/json" not in self.headers.get("Content-Type", "") \
                    or (origin is not None and origin not in app.origins):
                self._json(403, {"error": "Pedido recusado."})
                return
            action = self.path.split("?", 1)[0].removeprefix("/api/")
            try:
                if action == "login":
                    body = self._read()
                    code, out, token = app.login(str(body.get("username", "")), str(body.get("password", "")),
                                                 self.headers.get("X-Forwarded-For", ""))
                    self._json(code, out, self._cookie(token) if token else None)
                    return
                if not self._user():
                    self._json(401, ERR_SESSION)
                    return
                if action == "logout":
                    app.sessions.drop(self._token())
                    self._json(200, {}, self._cookie("", max_age=0))
                    return
                if action == "admin/autosend":
                    if auth.ROLES[self._user()] != "admin":
                        self._json(403, {"error": "Sem acesso."})
                        return
                    st = app.state()
                    saved = AS.save_settings(st, self._read())
                    app.log("autosend", json.dumps(saved))
                    self._json(200, AS.status(app.cfg, st))
                    return
                if action not in ("send", "rewrite", "edit", "answered", "to"):
                    self._json(404, {"error": "Não existe."})
                    return
                self._json(200, app.act(action, self._read()))
            except AC.Stale as exc:
                self._json(409, {"error": str(exc)})
            except (ValueError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc) or "Pedido inválido."})
            except Exception as exc:
                traceback.print_exc()
                self._json(500, {"error": f"Falhou ({type(exc).__name__}). Antes de tentar outra vez, veja em Enviados "
                                          "se a resposta já saiu."})
    return Handler


def set_password(user: str) -> int:
    cfg = RT.load_config()
    if user not in auth.ROLES:
        print(f"unknown account {user!r}; use admin or agent", file=sys.stderr)
        return 2
    pw = getpass.getpass(f"New password for {user}: ")
    if pw != getpass.getpass("Again: "):
        print("the two passwords differ; nothing changed", file=sys.stderr)
        return 1
    try:
        auth.Accounts(Path(cfg["state_dir"]) / "accounts.json").set_password(user, pw)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"password set for {user}", file=sys.stderr)
    return 0


def main() -> int:
    if sys.argv[1:2] == ["--set-password"]:
        return set_password(sys.argv[2]) if len(sys.argv) == 3 else 2
    cfg = RT.load_config()
    app = App(cfg)
    try:
        DR.adopt_handoffs(app.state())   # also before the router's first cycle
        AC.fill_missing(cfg, app.state())
    except Exception:
        traceback.print_exc()
    server = ThreadingHTTPServer(("127.0.0.1", app.port), make_handler(app))
    print(f"dashboard on http://127.0.0.1:{app.port}/", file=sys.stderr, flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
