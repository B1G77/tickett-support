"""Tests for the dashboard web server: real HTTP on a free local port (no IMAP, no SMTP, no API)."""
import json
import socket
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import actions as AC
import api
import auth
import autosend as AS
import drafts as DR
import router as RT
import webapp as W


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PASSWORDS = {"admin": "admin-test-pass", "agent": "agent-test-pass"}


def start(tmp_path, hosts=(), **web):
    port = free_port()
    dist = tmp_path / "web" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Apoio Tickett</title>")
    accounts = auth.Accounts(tmp_path / "accounts.json")
    for user, pw in PASSWORDS.items():
        accounts.set_password(user, pw)
    cfg = {"state_dir": tmp_path, "folders": {"F1": "1 Cancelamentos e Reembolsos"}, "answered_folder": "10 Respondidos",
           "handoff_folder": "0 Por Responder", "imap": {"user": "support@tickett.com"}, "drafts": {"enabled": True},
           "webapp": {"port": port, "hosts": list(hosts), "dist": str(dist), **web}}
    RT.State(tmp_path / "router.db")
    app = W.App(cfg)
    server = W.ThreadingHTTPServer(("127.0.0.1", port), W.make_handler(app))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port, app


def call(port, path, method="GET", body=None, headers=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


ACT = {"Content-Type": "application/json", "X-Tickett-Dashboard": "1"}


def login(port, user="admin", pw=None):
    """Log in and return the Cookie header value for later requests."""
    code, headers, _ = call(port, "/api/login", "POST", {"username": user, "password": pw or PASSWORDS[user]}, ACT)
    assert code == 200
    return headers["Set-Cookie"].split(";", 1)[0]


def test_without_login_the_api_answers_401_and_the_page_still_loads(tmp_path):
    server, port, _ = start(tmp_path)
    try:
        code, headers, body = call(port, "/")
        assert code == 200 and b"Apoio Tickett" in body
        assert headers["Content-Security-Policy"] == (
            "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; "
            "font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        assert headers["X-Frame-Options"] == "DENY"
        for path in ("/api/state", "/api/me", "/api/admin/metrics"):
            code, _, body = call(port, path)
            assert code == 401 and json.loads(body) == {"error": "Sessão terminada."}
        assert call(port, "/api/send", "POST", {"id": "x", "version": "v"}, ACT)[0] == 401
    finally:
        server.shutdown()


def test_login_sets_an_httponly_strict_cookie_and_me_returns_the_role(tmp_path):
    server, port, _ = start(tmp_path)
    try:
        code, headers, body = call(port, "/api/login", "POST", {"username": "agent", "password": PASSWORDS["agent"]}, ACT)
        assert code == 200 and json.loads(body) == {"user": "agent", "role": "agent"}
        cookie = headers["Set-Cookie"]
        assert cookie.startswith("tickett_session=") and "HttpOnly" in cookie and "SameSite=Strict" in cookie
        assert "Path=/" in cookie and "Secure" not in cookie
        c = cookie.split(";", 1)[0]
        assert json.loads(call(port, "/api/me", headers={"Cookie": c})[2]) == {"user": "agent", "role": "agent"}
        state = json.loads(call(port, "/api/state", headers={"Cookie": c})[2])
        assert state["drafts"] == [] and state["mailbox"] == "support@tickett.com"
        assert state["health"]["overall"] in ("ok", "warn", "bad")
        # logout drops the session and clears the cookie
        code, headers, _ = call(port, "/api/logout", "POST", {}, {**ACT, "Cookie": c})
        assert code == 200 and "Max-Age=0" in headers["Set-Cookie"]
        assert call(port, "/api/me", headers={"Cookie": c})[0] == 401
        log = (tmp_path / "router.log").read_text()
        assert "login agent" in log and PASSWORDS["agent"] not in log
    finally:
        server.shutdown()


def test_health_lists_doc_changes_for_the_admin(tmp_path):
    import ledger as LG
    server, port, app = start(tmp_path)
    try:
        app.cfg["site_dir"] = str(tmp_path / "site")   # never the real data folder
        st = app.state()
        LG.init(st)
        for i in range(55):
            st.db.execute("INSERT INTO doc_changes VALUES (?,?,?)",
                          (f"2026-10-{i // 10 + 1:02d}T10:0{i % 10}:00+00:00", "novo", f"Concerto {i}"))
        st.db.commit()
        agent, admin = login(port, "agent"), login(port, "admin")
        assert call(port, "/api/admin/health", headers={"Cookie": agent})[0] == 403
        code, _, body = call(port, "/api/admin/health", headers={"Cookie": admin})
        h = json.loads(body)
        assert code == 200 and h["checks"] and all({"status", "label", "detail"} <= set(c) for c in h["checks"])
        assert len(h["doc"]["changes"]) == 50 and h["doc"]["changes"][0]["name"] == "Concerto 54"
        assert h["doc"]["fetched_at"] == "" and set(h["doc"]) == {"fetched_at", "changes"}
    finally:
        server.shutdown()


def test_the_cookie_is_secure_when_configured(tmp_path):
    server, port, _ = start(tmp_path, secure_cookie=True)
    try:
        _, headers, _ = call(port, "/api/login", "POST", {"username": "admin", "password": PASSWORDS["admin"]}, ACT)
        assert "; Secure" in headers["Set-Cookie"]
    finally:
        server.shutdown()


def test_login_needs_the_page_header_json_and_a_known_host(tmp_path):
    server, port, _ = start(tmp_path)
    body = {"username": "admin", "password": PASSWORDS["admin"]}
    try:
        assert call(port, "/api/login", "POST", body, {"Content-Type": "application/json"})[0] == 403
        assert call(port, "/api/login", "POST", body, {**ACT, "Origin": "https://evil.example"})[0] == 403
        assert call(port, "/api/login", "POST", body, {**ACT, "Host": "evil.example"})[0] == 403
    finally:
        server.shutdown()


def test_a_wrong_password_is_401_and_the_sixth_try_is_429(tmp_path):
    server, port, _ = start(tmp_path)
    bad = {"username": "agent", "password": "not-the-password"}
    try:
        for _ in range(5):
            code, _, body = call(port, "/api/login", "POST", bad, ACT)
            assert code == 401 and json.loads(body) == {"error": "Utilizador ou palavra-passe errados."}
        code, _, body = call(port, "/api/login", "POST", {"username": "agent", "password": PASSWORDS["agent"]}, ACT)
        assert code == 429 and json.loads(body) == {"error": "Demasiadas tentativas. Tente daqui a 15 minutos."}
        log = (tmp_path / "router.log").read_text()
        assert "login failed agent" in log and "locked agent" in log and "not-the-password" not in log
        assert call(port, "/api/login", "POST", {"username": "admin", "password": PASSWORDS["admin"]}, ACT)[0] == 200
    finally:
        server.shutdown()


def test_a_corrupt_accounts_file_is_a_plain_401(tmp_path):
    server, port, _ = start(tmp_path)
    try:
        (tmp_path / "accounts.json").write_text("{not json")
        code, _, body = call(port, "/api/login", "POST", {"username": "admin", "password": "whatever-long"}, ACT)
        assert code == 401 and json.loads(body) == {"error": "Utilizador ou palavra-passe errados."}
    finally:
        server.shutdown()


def test_the_agent_cannot_read_admin_routes(tmp_path):
    server, port, _ = start(tmp_path)
    try:
        agent, admin = login(port, "agent"), login(port, "admin")
        assert call(port, "/api/admin/metrics", headers={"Cookie": agent})[0] == 403
        code, _, body = call(port, "/api/admin/metrics", headers={"Cookie": admin})
        assert code == 200 and "overview" in json.loads(body)
    finally:
        server.shutdown()


def test_an_idle_session_expires_after_eight_hours(tmp_path):
    server, port, app = start(tmp_path)
    clock = [1000.0]
    app.sessions.now = lambda: clock[0]
    try:
        c = login(port)
        clock[0] += 8 * 3600 - 60
        assert call(port, "/api/me", headers={"Cookie": c})[0] == 200   # use refreshes the timer
        clock[0] += 8 * 3600 + 60
        assert call(port, "/api/me", headers={"Cookie": c})[0] == 401
    finally:
        server.shutdown()


def test_unknown_paths_fall_back_to_index_and_traversal_is_404(tmp_path):
    server, port, _ = start(tmp_path)
    dist = tmp_path / "web" / "dist"
    (dist / "app.js").write_text("console.log(1)")
    (tmp_path / "web" / "secret.txt").write_text("nope")
    try:
        code, _, body = call(port, "/painel")
        assert code == 200 and b"Apoio Tickett" in body
        code, headers, _ = call(port, "/app.js")
        assert code == 200 and headers["Content-Type"].startswith("text/javascript")
        assert call(port, "/../config/router.json")[0] == 404
        assert call(port, "/%2e%2e/secret.txt")[0] == 404
        assert call(port, "/api/nope")[0] == 401
    finally:
        server.shutdown()


def test_a_missing_build_answers_503(tmp_path):
    server, port, _ = start(tmp_path)
    (tmp_path / "web" / "dist" / "index.html").unlink()
    try:
        code, _, body = call(port, "/")
        assert code == 503 and json.loads(body) == {"error": "A aplicação ainda não foi compilada."}
    finally:
        server.shutdown()


def test_an_unexpected_host_is_refused(tmp_path):
    server, port, _ = start(tmp_path)
    try:
        assert call(port, "/api/state", headers={"Host": "evil.example"})[0] == 403
        assert call(port, "/", headers={"Host": "evil.example"})[0] == 403
    finally:
        server.shutdown()


def test_actions_need_the_page_header_json_and_the_own_origin(tmp_path):
    server, port, _ = start(tmp_path, hosts=["tickett-router.tail1.ts.net"])
    body = {"id": "x", "version": "v"}
    c = {"Cookie": login(port)}
    try:
        assert call(port, "/api/send", "POST", body, {"Content-Type": "application/json", **c})[0] == 403
        assert call(port, "/api/send", "POST", body, {**ACT, **c, "Origin": "https://evil.example"})[0] == 403
        assert call(port, "/api/send", "POST", body, {**ACT, **c, "Origin": "https://tickett-router.tail1.ts.net"})[0] == 409
        assert call(port, "/api/send", "POST", body, {**ACT, **c})[0] == 409   # passes the checks, then: no such draft
        assert call(port, "/api/delete-everything", "POST", body, {**ACT, **c})[0] == 404
    finally:
        server.shutdown()


def test_the_server_only_listens_on_this_machine():
    assert "127.0.0.1" in Path(W.__file__).read_text().split("def main", 1)[1]


def test_the_card_shows_the_reply_to_address_and_flags_a_difference(tmp_path):
    server, port, app = start(tmp_path)
    server.shutdown()
    st = app.state()
    DR.init(st)
    for mid, reply_to in (("<a@x>", "Ana <ana.s@gmail.com>"), ("<b@x>", ""), ("<c@x>", "ANA@gmail.com"),
                          ("<d@x>", "ana@gmail.com, outro@gmail.com")):
        st.db.execute("INSERT INTO drafts (message_id, folder, body, created_at, from_email, reply_to) "
                      "VALUES (?, 'F1', 'x', ?, 'ana@gmail.com', ?)", (mid, mid, reply_to))
    st.db.execute("UPDATE drafts SET rewrites=2, edited=1 WHERE message_id='<a@x>'")
    st.db.commit()
    cards = {c["version"]: c for c in api.state_json(app.cfg, st)["drafts"]}
    assert cards["<a@x>"]["to"] == "ana.s@gmail.com" and cards["<a@x>"]["to_differs"] is True
    assert cards["<a@x>"]["rewrites"] == 2 and cards["<a@x>"]["edited"] is True
    assert cards["<b@x>"]["to"] == "ana@gmail.com" and cards["<b@x>"]["to_differs"] is False
    assert cards["<b@x>"]["edited"] is False
    assert cards["<c@x>"]["to_differs"] is False   # same address, other case
    assert cards["<d@x>"]["to"] == "ana@gmail.com, outro@gmail.com" and cards["<d@x>"]["to_differs"] is True


def test_edit_is_an_action_route_that_needs_a_session_and_a_current_version(tmp_path):
    server, port, _ = start(tmp_path)
    body = {"id": "x", "version": "v", "body": "texto"}
    try:
        assert call(port, "/api/edit", "POST", body, ACT)[0] == 401
        assert call(port, "/api/edit", "POST", body, {**ACT, "Cookie": login(port)})[0] == 409
    finally:
        server.shutdown()


def test_search_is_gone(tmp_path):
    server, port, _ = start(tmp_path)
    try:
        assert call(port, "/api/search?q=rita", headers={"Cookie": login(port, "agent")})[0] == 404
        assert not hasattr(api, "search_json")
    finally:
        server.shutdown()


def test_a_card_shows_when_it_goes_out_automatically(tmp_path):
    from datetime import datetime, timedelta, timezone
    server, port, app = start(tmp_path)
    server.shutdown()
    st = app.state()
    DR.init(st)
    soon = datetime.now(timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=1)
    later = soon + timedelta(days=3)
    for mid, folder, at in (("<a@x>", "F2", soon.isoformat()), ("<b@x>", "F2", later.isoformat()),
                            ("<c@x>", "F2", None), ("<d@x>", "F3", soon.isoformat())):
        st.db.execute("INSERT INTO drafts (message_id, folder, body, created_at, from_email, auto_after) "
                      "VALUES (?, ?, 'x', ?, 'ana@gmail.com', ?)", (mid, folder, mid, at))
    st.db.commit()
    AS.save_settings(st, {"enabled": True, "daily_limit": 20, "categories": ["F2"]})
    cards = {c["version"]: c for c in api.state_json(app.cfg, st)["drafts"]}
    local = soon.astimezone()
    today = local.date() == datetime.now().astimezone().date()
    assert cards["<a@x>"]["auto_at"] == local.strftime("%H:%M" if today else "%d/%m %H:%M")
    assert cards["<b@x>"]["auto_at"] == later.astimezone().strftime("%d/%m %H:%M")
    assert cards["<c@x>"]["auto_at"] == ""
    assert cards["<d@x>"]["auto_at"] == ""   # its category is off: no promise on the card
    AS.save_settings(st, {"enabled": False, "daily_limit": 20, "categories": ["F2"]})
    assert all(c["auto_at"] == "" for c in api.state_json(app.cfg, st)["drafts"])


def test_answered_is_an_action_route_and_people_cards_have_an_id(tmp_path):
    server, port, app = start(tmp_path)
    try:
        st = app.state()
        DR.init(st)
        st.db.execute("INSERT INTO handoffs (message_id, created_at) VALUES ('<h@x>', 'now')")
        st.db.commit()
        assert api.state_json(app.cfg, st)["people"][0]["id"] == AC.card_id("<h@x>")
        assert call(port, "/api/answered", "POST", {"id": "x"}, ACT)[0] == 401
        assert call(port, "/api/answered", "POST", {"id": "x"}, {**ACT, "Cookie": login(port, "agent")})[0] == 409
    finally:
        server.shutdown()


def test_admin_metrics_takes_dates_and_rejects_bad_ones(tmp_path):
    server, port, _ = start(tmp_path)
    try:
        cookie = {"Cookie": login(port)}
        for q in ("from=nope", "to=2026-13-01"):
            code, _, body = call(port, f"/api/admin/metrics?{q}", headers=cookie)
            assert code == 400 and json.loads(body) == {"error": "Datas inválidas."}
        code, _, body = call(port, "/api/admin/metrics", headers=cookie)
        assert code == 200 and len(json.loads(body)["per_day"]) == 30
        code, _, body = call(port, "/api/admin/metrics?from=2026-03-01&to=2026-03-03", headers=cookie)
        assert code == 200 and len(json.loads(body)["per_day"]) == 3
        code, _, body = call(port, "/api/admin/metrics?to=0001-01-05", headers=cookie)   # 29 days before: overflow
        assert code == 400 and json.loads(body) == {"error": "Datas inválidas."}
        for q in ("from=2025-01-01&to=2026-02-05", "from=9999-12-31&to=0001-01-01"):   # 401 days, and far more
            code, _, body = call(port, f"/api/admin/metrics?{q}", headers=cookie)
            assert code == 400 and json.loads(body) == {"error": "Escolha um período de até 400 dias."}
        code, _, body = call(port, "/api/admin/metrics?from=2025-01-01&to=2026-02-04", headers=cookie)   # 400 days
        assert code == 200 and len(json.loads(body)["per_day"]) == 400
    finally:
        server.shutdown()


def test_behind_the_proxy_the_lockout_keys_on_the_right_most_forwarded_address(tmp_path):
    server, port, app = start(tmp_path, trusted_proxy=True)
    server.shutdown()
    for i in range(5):   # the client can put anything at the left of X-Forwarded-For; the proxy appends the real one
        assert app.login("agent", "not-the-password", f"10.9.9.{i}, 100.64.0.7")[0] == 401
    assert app.login("agent", PASSWORDS["agent"], "1.1.1.1, 100.64.0.7")[0] == 429
    assert app.login("agent", PASSWORDS["agent"], "100.64.0.8")[0] == 200


def test_the_lockout_keeps_one_key_for_all_unknown_usernames(tmp_path):
    server, port, app = start(tmp_path)
    server.shutdown()
    for name in ("root", "x" * 60, "administrator"):
        app.login(name, "whatever-long", "")
    assert set(app.lockout._f) == {"?"}
    assert app.login("admin", PASSWORDS["admin"], "")[0] == 200


def test_admin_autosend_reads_and_saves_the_settings(tmp_path):
    server, port, _ = start(tmp_path)
    try:
        agent, admin = login(port, "agent"), login(port, "admin")
        on = {"enabled": True, "daily_limit": 5, "categories": ["F2"], "delay_minutes": 60}
        assert call(port, "/api/admin/autosend", headers={"Cookie": agent})[0] == 403
        assert call(port, "/api/admin/autosend", "POST", on, {**ACT, "Cookie": agent})[0] == 403
        assert call(port, "/api/admin/autosend", "POST", on, {"Content-Type": "application/json", "Cookie": admin})[0] == 403
        assert call(port, "/api/admin/autosend", "POST", on,
                    {**ACT, "Cookie": admin, "Origin": "https://evil.example"})[0] == 403
        assert call(port, "/api/admin/autosend", "POST", on, ACT)[0] == 401
        code, _, body = call(port, "/api/admin/autosend", headers={"Cookie": admin})
        assert code == 200 and json.loads(body) == {"settings": {"enabled": False, "daily_limit": 20, "categories": [],
                                                                 "delay_minutes": 0},
                                                    "sent_today": 0, "readiness": []}
        code, _, body = call(port, "/api/admin/autosend", "POST", {**on, "categories": ["F1"]}, {**ACT, "Cookie": admin})
        assert code == 400 and json.loads(body) == {"error": "Categoria não permitida."}
        code, _, body = call(port, "/api/admin/autosend", "POST", {**on, "delay_minutes": 45}, {**ACT, "Cookie": admin})
        assert code == 400 and json.loads(body) == {"error": "Atraso não permitido."}
        code, _, body = call(port, "/api/admin/autosend", "POST", on, {**ACT, "Cookie": admin})
        assert code == 200 and json.loads(body)["settings"] == on
        assert json.loads(call(port, "/api/admin/autosend", headers={"Cookie": admin})[2])["settings"] == on
        log = (tmp_path / "router.log").read_text()
        assert 'autosend  {"enabled": true, "daily_limit": 5, "categories": ["F2"], "delay_minutes": 60}' in log and "F1" not in log
    finally:
        server.shutdown()


def test_the_installer_builds_the_web_app():
    script = (Path(__file__).resolve().parent.parent / "scripts" / "router-install.sh").read_text()
    assert "npm run build" in script and 'web/dist/" "$APP/web/dist/"' in script
