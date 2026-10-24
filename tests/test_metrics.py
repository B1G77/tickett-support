"""Tests for the admin numbers: rows seeded straight into router.db, fixed dates, local noon times."""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import drafts as DR
import ledger as L
import metrics as M
import router as RT

CFG = {"folders": {"F1": "1 Cancelamentos e Reembolsos", "F2": "2 Bilhetes não recebidos", "F9": "9 A Rever"},
       "answered_folder": "10 Respondidos", "handoff_folder": "0 Por Responder", "overdue_days": 2,
       "pricing": {"usd_to_eur": 0.5, "per_million_usd": {"m1": {"in": 10, "out": 20, "cache_read": 1,
                                                                 "cache_write": 5}}}}


def at(day: str, hour: float = 12) -> str:
    """Local `day` at the given hour, as ISO UTC."""
    d = datetime.fromisoformat(day) + timedelta(hours=hour)
    return d.astimezone().astimezone(timezone.utc).isoformat(timespec="seconds")


NOW = datetime.fromisoformat(at("2026-03-20"))


def new_state(tmp_path):
    state = RT.State(tmp_path / "router.db")
    L.init(state)
    DR.init(state)
    return state


def reply(state, mid, folder, received, answered=None, event="", now_in=None):
    """A ledger row, and the router's decision for it: the email is in `now_in` (default: `folder`)."""
    state.db.execute("INSERT INTO replies (message_id, folder, received_at, answered_at, event) VALUES (?,?,?,?,?)",
                     (mid, folder, received, answered, event))
    state.db.execute("INSERT INTO decisions (message_id, folder) VALUES (?,?)", (mid, now_in or folder))
    state.db.commit()


def run(state, start="2026-03-10", end="2026-03-16"):
    return M.report(CFG, state, date.fromisoformat(start), date.fromisoformat(end), NOW)


def test_overview_counts_received_and_answered_in_the_period(tmp_path):
    s = new_state(tmp_path)
    reply(s, "a", "F1", at("2026-03-11"), at("2026-03-12"))
    reply(s, "b", "F2", at("2026-03-09"), at("2026-03-10"))   # received before, answered inside
    reply(s, "c", "F2", at("2026-03-16"))                     # unanswered
    reply(s, "d", "F2", at("2026-03-17"), at("2026-03-17"))   # after
    s.db.execute("INSERT INTO handoffs (message_id, created_at) VALUES ('h1', ?)", (at("2026-03-15"),))
    s.db.execute("INSERT INTO handoffs (message_id, created_at, answered_at) VALUES ('h2', ?, ?)",
                 (at("2026-03-15"), at("2026-03-16")))
    s.db.execute("INSERT OR REPLACE INTO meta VALUES ('ledger_since', '2026-03-01')")
    s.db.commit()
    r = run(s)
    assert r["overview"]["received"] == 2 and r["overview"]["answered"] == 2
    assert r["overview"]["waiting_person"] == 1
    assert r["overview"]["oldest_waiting_hours"] == 96.0   # c: 16th noon to 20th noon
    assert r["complete_since"] == "2026-03-01"


def test_per_day_lists_every_day_with_zeros(tmp_path):
    s = new_state(tmp_path)
    reply(s, "a", "F1", at("2026-03-11"), at("2026-03-13"))
    days = run(s)["per_day"]
    assert [d["day"] for d in days] == [f"2026-03-{n}" for n in range(10, 17)]
    assert days[1] == {"day": "2026-03-11", "received": 1, "answered": 0}
    assert days[3] == {"day": "2026-03-13", "received": 0, "answered": 1}
    assert days[0] == {"day": "2026-03-10", "received": 0, "answered": 0}


def test_reply_time_median_and_within_limit(tmp_path):
    s = new_state(tmp_path)
    reply(s, "a", "F1", at("2026-03-10"), at("2026-03-10", 15))      # 3 h
    reply(s, "b", "F1", at("2026-03-11"), at("2026-03-11", 20))      # 8 h
    reply(s, "c", "F1", at("2026-03-12"), at("2026-03-16"))          # 96 h, late
    reply(s, "d", "F1", at("2026-03-13"))                            # open, 7 days old: late
    reply(s, "e", "F1", at("2026-03-19"))                            # outside period
    rt = run(s)["reply_time"]
    assert rt["median_hours"] == 8.0 and rt["limit_days"] == 2
    assert rt["within_limit_pct"] == 50.0   # 2 of (3 answered + 1 late open)
    assert sum(w["n"] for w in rt["weekly"]) == 4
    assert all(w["week"].startswith("2026-W") for w in rt["weekly"])


def test_only_mail_still_in_an_open_folder_counts_as_waiting(tmp_path):
    s = new_state(tmp_path)
    reply(s, "open", "F1", at("2026-03-15"))                    # still in F1: waiting, 5 days
    reply(s, "handed", "F1", at("2026-03-14"), now_in="P")      # handed to a person: still waiting
    reply(s, "moved", "F1", at("2026-03-10"), now_in="A")       # moved to the answered folder by hand
    reply(s, "thanks", "F2", at("2026-03-11"), now_in="F8")     # a thank-you follow-up moved to folder 8
    reply(s, "gone", "F2", at("2026-03-12"))                    # the draft found the email gone
    s.db.execute("INSERT INTO drafts (message_id, folder, closed_at, closed_how) VALUES ('gone', 'F2', ?, 'gone')",
                 (at("2026-03-13"),))
    s.db.execute("INSERT INTO replies (message_id, folder, received_at) VALUES ('nodecision', 'F1', ?)",
                 (at("2026-03-09"),))                            # no decision at all: not known to wait
    reply(s, "quick", "F1", at("2026-03-16"), at("2026-03-16", 15))   # answered in 3 h
    r = run(s)
    assert r["overview"]["oldest_waiting_hours"] == 6 * 24.0   # "handed", 14th noon to 20th noon
    assert r["reply_time"]["within_limit_pct"] == 33.3          # 1 of (1 answered + "open" and "handed" late)
    assert sum(w["n"] for w in r["reply_time"]["weekly"]) == 6   # received in the period (not "nodecision")


def test_quality_splits_unchanged_edited_rewritten_handed_off(tmp_path):
    s = new_state(tmp_path)
    rows = [("1", "F1", "sent", 0, 0, "x", "x"), ("2", "F1", "sent", 0, 1, "y", "x"),
            ("3", "F1", "sent", 2, 0, "z", "x"), ("4", "F1", "person", 0, 0, "x", "x"),
            ("5", "F2", "answered", 0, 0, "x", "x"), ("6", "F2", "sent", 0, 0, "x", "x")]
    for mid, folder, how, rew, ed, body, first in rows:
        s.db.execute("INSERT INTO drafts (message_id, folder, body, first_body, rewrites, edited, created_at, "
                     "closed_at, closed_how) VALUES (?,?,?,?,?,?,?,?,?)",
                     (mid, folder, body, first, rew, ed, at("2026-03-10"), at("2026-03-12"), how))
    s.db.execute("INSERT INTO drafts (message_id, folder, body, first_body, closed_at, closed_how) "
                 "VALUES ('out','F1','x','x',?, 'sent')", (at("2026-03-30"),))
    s.db.commit()
    q = run(s)["quality"]
    assert q == [{"folder": "F1", "name": "1 Cancelamentos e Reembolsos", "drafts": 4, "unchanged": 1,
                  "edited": 1, "rewritten": 1, "handed_off": 1},
                 {"folder": "F2", "name": "2 Bilhetes não recebidos", "drafts": 2, "unchanged": 1,
                  "edited": 0, "rewritten": 0, "handed_off": 0}]


def test_quality_leaves_out_drafts_sent_automatically(tmp_path):
    s = new_state(tmp_path)
    for mid, how in (("1", "sent"), ("2", "auto")):
        s.db.execute("INSERT INTO drafts (message_id, folder, body, first_body, closed_at, closed_how) "
                     "VALUES (?,'F2','x','x',?,?)", (mid, at("2026-03-12"), how))
    s.db.commit()
    assert [(q["drafts"], q["unchanged"]) for q in run(s)["quality"]] == [(1, 1)]


def test_cost_per_month_in_euros(tmp_path):
    s = new_state(tmp_path)
    for day, model in (("2026-02-27", "m1"), ("2026-03-05", "m1"), ("2026-03-12", "zzz"), ("2026-04-02", "m1")):
        s.db.execute("INSERT INTO ai_usage VALUES (?,?,?,?,?,?,?)", (at(day), "draft", model, 1_000_000, 0, 0, 0))
    s.db.commit()
    c = run(s, "2026-02-20", "2026-03-31")["cost"]
    assert c["period_eur"] == 10.0 and c["unknown_models"] == ["zzz"]
    assert c["monthly"] == [{"month": "2026-02", "eur": 5.0}, {"month": "2026-03", "eur": 5.0}]


def test_the_report_has_no_events_section(tmp_path):
    s = new_state(tmp_path)
    reply(s, "m1", "F1", at("2026-03-11"), event="Concerto Exemplo")
    assert "events" not in run(s)


def test_an_empty_period_gives_zeros(tmp_path):
    r = run(new_state(tmp_path))
    assert r["overview"] == {"received": 0, "answered": 0, "waiting_person": 0, "oldest_waiting_hours": None}
    assert r["reply_time"]["median_hours"] is None and r["reply_time"]["within_limit_pct"] is None
    assert r["reply_time"]["weekly"] == [] and r["categories"] == [] and r["quality"] == []
    assert r["cost"]["period_eur"] == 0 and len(r["per_day"]) == 7


def test_from_after_to_is_swapped(tmp_path):
    s = new_state(tmp_path)
    reply(s, "a", "F1", at("2026-03-11"))
    assert run(s, "2026-03-16", "2026-03-10") == run(s, "2026-03-10", "2026-03-16")


def test_old_shop_and_partner_history_shows_under_a_rever(tmp_path):
    import answered as A
    assert "F6" not in M.OPEN_FOLDERS and "F7" not in M.OPEN_FOLDERS and "F9" in M.OPEN_FOLDERS
    assert "F6" not in L.COUNTED and "F7" not in L.COUNTED and "F6" not in A.OPEN_FOLDERS and "F7" not in A.OPEN_FOLDERS
    s = new_state(tmp_path)
    reply(s, "a", "F6", at("2026-03-11"))
    reply(s, "b", "F7", at("2026-03-12"))
    reply(s, "c", "F9", at("2026-03-12"))
    for mid, folder in (("1", "F6"), ("2", "F7")):
        s.db.execute("INSERT INTO drafts (message_id, folder, body, first_body, closed_at, closed_how) "
                     "VALUES (?,?,'x','x',?,'sent')", (mid, folder, at("2026-03-12")))
    s.db.commit()
    r = run(s)
    assert r["categories"] == [{"folder": "F9", "name": "9 A Rever", "n": 3}]
    assert [(q["folder"], q["drafts"]) for q in r["quality"]] == [("F9", 2)]
