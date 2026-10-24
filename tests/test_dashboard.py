"""Tests for the local status page (no IMAP, no network)."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import dashboard as DB
import router as RT

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
OK_SYSTEM = {"on_battery": False}


def setup(tmp_path, log_lines=()):
    cfg = {"state_dir": tmp_path, "folders": {"F1": "1 Cancelamentos e Reembolsos", "F9": "9 A Rever"},
           "answered_folder": "10 Respondidos", "drafts": {"enabled": False}}
    st = RT.State(tmp_path / "router.db")
    d = RT.Decision("F1", False, "claude", "high", "refund")
    st.record("<a@x>", [], d, moved=True)
    st.record("<b@x>", [], RT.Decision("F9", False, "rule", "low", "no request text"), moved=True)
    (tmp_path / "router.log").write_text("".join(f"{l}\n" for l in log_lines), encoding="utf-8")
    return cfg


def test_heartbeat_counts_failures_in_a_row_and_resets_on_success(tmp_path):
    DB.heartbeat(tmp_path, False, "OSError: no network", NOW)
    DB.heartbeat(tmp_path, False, "OSError: no network", NOW)
    hb = json.loads((tmp_path / DB.HEARTBEAT).read_text())
    assert hb["failures_in_a_row"] == 2 and hb["failures"][-1][1] == "OSError: no network"
    DB.heartbeat(tmp_path, True, now=NOW)
    hb = json.loads((tmp_path / DB.HEARTBEAT).read_text())
    assert hb["failures_in_a_row"] == 0 and hb["last_ok"] == NOW.isoformat(timespec="seconds")


def test_collect_counts_filed_mail_digest_and_problems(tmp_path):
    t = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")   # real time: collect() uses now
    cfg = setup(tmp_path, [
        f"{t}  digest  summary saved in Drafts: Por responder: 239 · atrasadas (mais de 2 dias): 229",
        f"{t}  <c@x>  answered -> 10 Respondidos",
        f"{t}  <d@x>  left in Inbox, will retry (InternalServerError)",
        f"{t}  <e@x>  draft saved - nota: Ligar à Maria Silva sobre a encomenda 123456"])
    d = DB.collect(cfg, now=datetime.now(timezone.utc), system=OK_SYSTEM)
    assert (d["waiting"], d["overdue"]) == (239, 229)
    assert d["by_folder"] == {"1 Cancelamentos e Reembolsos": 1, "9 A Rever": 1}
    assert d["by_source"] == {"claude": 1, "rule": 1}
    assert d["problems_24h"] == [("left in Inbox, will retry (InternalServerError)", 1)]


def test_problem_kinds_carry_no_customer_text():
    assert DB._problem_kind("no draft - leak check") == "no draft"
    assert DB._problem_kind("reply sent, 91% of the draft kept") == "reply sent, N% of the draft kept"
    assert DB._problem_kind("not sent - leak check: Maria") == "not sent"


def test_page_has_counts_only_and_flags_battery_power(tmp_path):
    cfg = setup(tmp_path, [f"{NOW.isoformat()}  <secret-id@gmail.com>  left in Inbox, will retry (TimeoutError)"])
    DB.heartbeat(tmp_path, True, now=NOW)
    page = DB.render(DB.collect(cfg, now=NOW, system={"on_battery": True}), now=NOW)
    assert "secret-id" not in page and "gmail" not in page
    assert "na bateria" in page and "Backups" not in page


def test_router_turns_critical_when_the_last_cycle_is_old(tmp_path):
    cfg = setup(tmp_path)
    DB.heartbeat(tmp_path, True, now=NOW - timedelta(minutes=30))
    cs = DB.checks(DB.collect(cfg, now=NOW, system=OK_SYSTEM), NOW)
    assert cs[0] == ("critical", "Router", "último ciclo há 30 min")


def test_write_keeps_the_page_private(tmp_path):
    cfg = setup(tmp_path)
    DB.heartbeat(tmp_path, True)
    path = DB.write(cfg)
    assert path.exists() and oct(path.stat().st_mode & 0o777) == "0o600"


def test_the_page_can_live_in_a_folder_of_its_own(tmp_path):
    cfg = setup(tmp_path)
    cfg["dashboard_path"] = str(tmp_path / "www" / "index.html")
    DB.heartbeat(tmp_path, True)
    assert DB.write(cfg) == tmp_path / "www" / "index.html" and (tmp_path / "www" / "index.html").exists()


def test_the_events_document_check_reports_freshness():
    d = {"heartbeat": {}, "counts": {}, "problems_24h": [], "site": {"crawled_at": None}, "drafts_enabled": True,
         "system": OK_SYSTEM, "doc": {"fetched_at": (NOW - timedelta(minutes=10)).isoformat(), "general": 15,
                                      "with_info": 14, "without": 8}}
    doc = next(c for c in DB.checks(d, NOW) if c[1] == "Documento de eventos")
    assert doc == ("good", "Documento de eventos", "lido há 10 min: 15 respostas gerais, 14 eventos (8 ainda vazios)")
    assert not any(c[1] == "Energia" for c in DB.checks({**d, "system": {"on_battery": None}}, NOW))   # a server


def test_a_customer_summary_mentioning_an_error_is_not_a_problem(tmp_path):
    t = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
    cfg = setup(tmp_path, [
        f"{t}  <a@x>  3 Compra e Pagamento urgent=False via claude/high: Transaction failed at checkout, error page",
        f"{t}  <b@x>  2 Bilhetes não recebidos urgent=False via claude/high: tickets never arrived; asks for resend",
        f"{t}  site  crawl failed, keeping the previous facts (TimeoutError)"])
    d = DB.collect(cfg, now=datetime.now(timezone.utc), system=OK_SYSTEM)
    assert d["problems_24h"] == [("crawl failed, keeping the previous facts (TimeoutError)", 1)]
