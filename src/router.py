#!/usr/bin/env python3
"""Stage 8 - file new mail from the atendimento@ inbox into its folder.

One pass, run every couple of minutes by launchd:
  1. log in over IMAPS (password from the macOS Keychain, never a file)
  2. make sure the router's folders exist
  3. for each message in INBOX:
       rules first    phishing / spam / platform / vendor / internal -> 8 Não-cliente
                      a short thank-you asking nothing more -> 10 Respondidos
       conversation   a reply in an already-filed thread -> that thread's folder
       Claude         redacted text only; low confidence, a failed leak check
                      or no request text -> 9 A Rever
     then red-flag it if urgent, MOVE it, and log the decision in SQLite
  4. phishing older than the retention period: 8 Não-cliente -> Trash

Messages are read with BODY.PEEK, so nothing gets marked as read. An email
you drag back into the Inbox is left alone from then on.

Usage:  router.py --once [--dry-run]  |  --status  |  --undo HOURS
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import answered as A
import autosend as AS
import cases as K
import classify as C
import dashboard as DB
import drafts as DR
import extract as E
import ledger as LG
import redact as R
import sample as S
import triage as T
from imap_util import Mailbox

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "router.json"
SECRETS = Path.home() / ".config" / "claude-secrets" / "claude.env"
NOT_CUSTOMER = {"spam", "social", "vendor", "internal"}
ANSWERED = "A"   # key of cfg["answered_folder"] next to F1-F9
HANDOFF = "P"    # key of cfg["handoff_folder"]: mail a person must answer
_THANKS = re.compile(r"obrigad|agrade[çc]|thank|\bthx\b|merci|gracias|danke|bedankt|dank je|grazie", re.I)
_NOT_DONE = re.compile(r"\?|\b(mas|but|mais|pero|aber|maar|ainda|still|toujours|todav[íi]a|noch|nog|however|"
                       r"queria|quero|gostaria|preciso|podem|poderiam|want|need|would like|please|voudrais|"
                       r"pouvez|besoin|quisiera|necesito|m[öo]chte|brauche|wil graag|graag)\b|"
                       # still waiting, or sending something that needs handling
                       r"aguard|esper|previs|necessit|wait|attend|segue|anexo|attach|joint|comprovativo|"
                       # a question without a question mark, a problem, or details we asked for
                       r"\b(pode|posso|como|saber|savoir|souhait|o[uù]|where|how|provide|n[ãa]o|not|pas|nunca|"
                       r"never)\b|anulad|cancel|@", re.I)
MAX_FAILURES = 3
_URGENT = re.compile(
    r"tribunal|advogad|jur[íi]dic|a[çc][ãa]o judicial|livro de reclama|portal da queixa|\bdeco\b|"
    r"chargeback|test.?achats|mise en demeure|avocat|plainte|poursuite|lawyer|legal action|"
    r"\bcourt\b|pol[íi]cia|\bpolice\b|fraud|\bscam\b|arnaque|escroquerie|burla|abogado|denuncia|"
    r"anwalt|klage|advocaat|rechtbank|consumentenbond|"
    r"reclama[çc][ãa]o formal|queixa formal|formal complaint|"
    # data-protection requests carry a one-month legal deadline
    r"\brgpd\b|\bgdpr\b|(apagar|eliminar|remover) (os )?(meus )?dados|delete my (data|account)|"
    r"supprimer mes donn[ée]es|borrar mis datos", re.I)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_config(path: Path = CONFIG) -> dict:
    """config/router.json, with the server's own settings (ROUTER_LOCAL_CONFIG) laid over it:
    top-level values replace, nested objects such as "imap" are merged key by key."""
    cfg = json.loads(path.read_text(encoding="utf-8"))
    local = os.environ.get("ROUTER_LOCAL_CONFIG")
    if local and Path(local).exists():
        for k, v in json.loads(Path(local).read_text(encoding="utf-8")).items():
            cfg[k] = {**cfg[k], **v} if isinstance(v, dict) and isinstance(cfg.get(k), dict) else v
    cfg["state_dir"] = Path(os.path.expanduser(cfg["state_dir"]))
    return cfg


def keychain_password(service: str, account: str) -> str:
    """The mailbox password: IMAP_PASSWORD on the server (systemd hands it over from a root-only
    file), the macOS Keychain on the Mac."""
    if os.environ.get("IMAP_PASSWORD"):
        return os.environ["IMAP_PASSWORD"]
    res = subprocess.run(["security", "find-generic-password", "-s", service, "-a", account, "-w"],
                         capture_output=True, text=True)
    if res.returncode != 0:
        sys.exit(f"No Keychain password for {account}. Add it once with:\n"
                 f"  security add-generic-password -s {service} -a {account} -w")
    return res.stdout.rstrip("\n")


def load_secret_env(path: Path = SECRETS) -> None:
    """launchd does not read ~/.zshrc, so pick the API key up from the secrets file."""
    if os.environ.get("ANTHROPIC_API_KEY") or not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().removeprefix("export ").partition("=")
        if sep and key == "ANTHROPIC_API_KEY":
            os.environ[key] = value.strip().strip("\"'")


def no_request_text(case: dict) -> bool:
    """Nothing to classify: an empty email, or a contact form with only name,
    e-mail and phone. Stricter than the discovery filter on purpose -
    "Não recebi o bilhete" is short but a real request."""
    rest = S._TEMPLATE.sub(" ", case["subject"] + " " + case["text"])
    return len(re.findall(r"[A-Za-zÀ-ÿ]{2,}", rest)) < 2


def urgent_hint(text: str) -> bool:
    return bool(_URGENT.search(text or ""))


def closing(text: str) -> bool:
    """A short thank-you or 'received, all good' that asks for nothing more."""
    return bool(_THANKS.search(text)) and not _NOT_DONE.search(text) and len(text.split()) <= 15


def new_body(rec: dict) -> str:
    body = rec.get("body_text", "")
    return K.strip_quotes(body, K.is_forward(rec.get("subject", ""), body))


def new_text(rec: dict) -> str:
    """Subject plus what the sender wrote this time, without the quoted history."""
    return rec.get("subject", "") + "\n" + new_body(rec)


def refs(rec: dict) -> list[str]:
    return [r for r in list(rec.get("references") or []) + [rec.get("in_reply_to", "")] if r]


@dataclass
class Decision:
    folder: str
    urgent: bool
    source: str            # rule | thread | claude | fallback
    confidence: str
    reason: str
    phishing: bool = False
    suggested: str = ""
    model: str = ""


class State:
    COLS = ("message_id", "folder", "urgent", "source", "confidence", "reason", "suggested",
            "model", "phishing", "decided_at", "moved_at")

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS decisions (
              message_id TEXT PRIMARY KEY, folder TEXT, urgent INT, source TEXT, confidence TEXT,
              reason TEXT, suggested TEXT, model TEXT, phishing INT, decided_at TEXT, moved_at TEXT,
              undone_at TEXT, purged_at TEXT, override INT DEFAULT 0, failures INT DEFAULT 0);
            CREATE TABLE IF NOT EXISTS threads (ref TEXT PRIMARY KEY, folder TEXT);
            CREATE TABLE IF NOT EXISTS inbox_seen (message_id TEXT PRIMARY KEY, seen_at TEXT);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
            UPDATE decisions SET folder='F9' WHERE folder IN ('F6', 'F7');
            UPDATE threads SET folder='F9' WHERE folder IN ('F6', 'F7');""")

    def first_sight(self, mid: str) -> bool:
        """True the first time an unmatched Inbox message is seen, so it is logged
        and red-flagged once; a flag the user removes is not put back."""
        cur = self.db.execute("INSERT OR IGNORE INTO inbox_seen VALUES (?,?)", (mid, now()))
        self.db.commit()
        return cur.rowcount == 1

    def get(self, mid: str) -> dict | None:
        cur = self.db.execute("SELECT * FROM decisions WHERE message_id=?", (mid,))
        row = cur.fetchone()
        return dict(zip([c[0] for c in cur.description], row)) if row else None

    def thread_folder(self, references: list[str]) -> str | None:
        for r in references:
            row = self.db.execute("SELECT folder FROM threads WHERE ref=?", (r,)).fetchone()
            if row:
                return row[0]
        return None

    def record(self, mid: str, references: list[str], d: Decision, moved: bool) -> None:
        t = now()
        vals = (mid, d.folder, int(d.urgent), d.source, d.confidence, d.reason, d.suggested,
                d.model, int(d.phishing), t, t if moved else None)
        sets = ", ".join(f"{c}=excluded.{c}" for c in self.COLS[1:])
        self.db.execute(f"INSERT INTO decisions ({','.join(self.COLS)}) VALUES ({','.join('?' * len(vals))}) "
                        f"ON CONFLICT(message_id) DO UPDATE SET {sets}", vals)
        # a scam must never pull a real conversation into Não-cliente, and a thank-you
        # must not make the customer's next question land in Respondidos
        if d.folder not in ("F8", ANSWERED):
            self.db.execute("INSERT OR REPLACE INTO threads VALUES (?,?)", (mid, d.folder))
            self.db.executemany("INSERT OR IGNORE INTO threads VALUES (?,?)", [(r, d.folder) for r in references])
        self.db.commit()

    def fail(self, mid: str) -> int:
        self.db.execute("INSERT INTO decisions (message_id, failures) VALUES (?, 1) "
                        "ON CONFLICT(message_id) DO UPDATE SET failures = failures + 1", (mid,))
        self.db.commit()
        return self.db.execute("SELECT failures FROM decisions WHERE message_id=?", (mid,)).fetchone()[0]

    def mark(self, mid: str, **cols) -> None:
        self.db.execute(f"UPDATE decisions SET {', '.join(f'{k}=?' for k in cols)} WHERE message_id=?",
                        (*cols.values(), mid))
        self.db.commit()


def rule_decision(rec: dict, rules: dict | None, urgent: bool) -> Decision | None:
    """Free rules for when there is no API key - only the high-precision ones."""
    if not rules:
        return None
    text = (rec.get("subject", "") + "\n" + rec.get("body_text", "")).lower()
    if re.search(rules["cancelled_events"], text) and re.search(rules["refund_words"], text):
        return Decision("F1", urgent, "rule", "high", "refund or cancellation of a known cancelled event")
    if re.search(rules["merch_words"], text):
        return Decision("F9", urgent, "rule", "high", "merchandise order")
    return None


def decide(rec: dict, state: State, redactor, classifier, rules: dict | None = None) -> Decision | None:
    """Folder for one message, or None to leave it in the Inbox (rules-only mode)."""
    bucket, why = T.classify(rec)
    if bucket == "phishing":
        inherited = C.current_folder(state.thread_folder(refs(rec)))
        if inherited:   # never bin a reply in a known conversation; a person will see it there
            return Decision(inherited, False, "thread", "high", f"reply in an already-filed conversation ({why})")
        return Decision("F8", False, "rule", "high", why, phishing=T.safe_to_purge(why))
    if bucket in NOT_CUSTOMER:
        return Decision("F8", False, "rule", "high", why)
    urgent = urgent_hint(new_text(rec))
    if bucket == "customer" and not urgent and closing(new_body(rec)):
        return Decision(ANSWERED, False, "rule", "high", "thank-you or closing, nothing to answer")
    inherited = C.current_folder(state.thread_folder(refs(rec)))
    if inherited:
        return Decision(inherited, urgent, "thread", "high", "reply in an already-filed conversation")
    if classifier is None:   # no API key yet: nothing leaves the Mac, unmatched mail stays put
        return rule_decision(rec, rules, urgent)

    text = new_body(rec)
    if bucket == "staff_reply":   # Brazil team copy: the customer is the recipient
        names, addrs = [], [a for a in rec.get("to", []) if a.rpartition("@")[2] not in K.INTERNAL_DOMAINS]
    else:
        names, addrs = [rec.get("from_name", "")], [rec.get("from_email", "")]
    subject, text, _, leaks = redactor.redact(rec.get("subject", ""), text, names, addrs)
    case = {"subject": subject, "text": text, "languages": [rec.get("lang_guess", "")],
            "n_messages": 1, "attachment_hints": R.attachment_hints([rec])}
    if leaks:
        return Decision("F9", urgent, "rule", "low", "not sent - leak check: " + ",".join(leaks))
    if no_request_text(case):
        return Decision("F9", urgent, "rule", "low", "no request text")
    out = classifier.classify(case)
    LG.note_usage(state, "classify", out.get("model", ""), out.get("usage", {}))
    folder = C.current_folder(out["folder"]) if out.get("confidence") in ("high", "medium") else "F9"
    urgent = bool(out.get("urgent")) or urgent
    if folder == "F8" and urgent:   # a legal letter must not be buried with the scams; a person checks it
        folder = "F9"
    return Decision(folder, urgent, "claude", out.get("confidence", "low"),
                    out.get("reason", ""), suggested=out["folder"], model=out.get("model", ""))


class Log:
    def __init__(self, path: Path):
        self.path = path

    def __call__(self, mid: str, msg: str) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(f"{now()}  {mid[:60]}  {msg}\n")


def connect(cfg: dict) -> Mailbox:
    i = cfg["imap"]
    return Mailbox.connect(i["host"], i["port"], i["user"], keychain_password(i["keychain_service"], i["user"]))


def build_redactor():
    from redactor import build
    return build()


def build_classifier():
    """The Claude classifier, or None when no API key is configured (rules-only mode)."""
    load_secret_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    from classify import Classifier
    return Classifier()


def build_drafter(cfg: dict, classifier, log: Log):
    """The reply drafter, or None while drafts are switched off, there is no API key, or its
    own setup fails (missing rulebook, bad events.json) - filing must not stop for that."""
    if classifier is None or not cfg.get("drafts", {}).get("enabled"):
        return None
    from drafter import Drafter
    try:
        return Drafter()
    except Exception as exc:
        log("drafter", f"drafter unavailable ({type(exc).__name__}) - filing without drafts")
        return None


def purge_phishing(box: Mailbox, state: State, cfg: dict, folders: dict, log: Log) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=cfg["phishing_retention_days"])).isoformat()
    rows = state.db.execute("SELECT message_id FROM decisions WHERE phishing=1 AND moved_at < ? "
                            "AND undone_at IS NULL AND purged_at IS NULL AND override=0", (cutoff,)).fetchall()
    trash = box.trash() if rows else None
    if not trash:
        return
    box.select(folders["F8"])
    for (mid,) in rows:
        for uid in box.find(mid):
            box.move(uid, trash)
        state.mark(mid, purged_at=now())
        log(mid, "phishing older than retention -> Trash")


def run_once(cfg: dict, dry_run: bool = False) -> int:
    sd = cfg["state_dir"]
    sd.mkdir(parents=True, exist_ok=True)
    lock = open(sd / "router.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0   # the previous run is still going
    state, log = State(sd / "router.db"), Log(sd / "router.log")
    box = connect(cfg)
    try:
        moved = file_inbox(box, state, log, cfg, dry_run)
        if not dry_run:
            try:
                DB.save_counts(sd, box)
            except Exception as exc:   # the status page must never stop the filing
                log("dashboard", f"folder counts failed ({type(exc).__name__})")
        return moved
    finally:
        box.logout()   # also after a failed run, so no half-dead connection is left open


def folder_names(cfg: dict) -> dict[str, str]:
    extra = {HANDOFF: cfg["handoff_folder"]} if cfg.get("handoff_folder") else {}
    return {**cfg["folders"], ANSWERED: cfg["answered_folder"], **extra}


def file_inbox(box: Mailbox, state: State, log: Log, cfg: dict, dry_run: bool) -> int:
    names = folder_names(cfg)
    folders = box.ensure_folders(names)
    if not dry_run:   # first, so mail already answered by hand is not drafted
        A.sync(box, state, cfg, folders, log)
    box.select(Mailbox.INBOX)
    classifier = build_classifier()
    drafter = build_drafter(cfg, classifier, log)
    drafts_folder = A._special(box, "\\Drafts", "Drafts") if drafter else ""
    redactor = None
    moved = 0
    for uid in box.uids(since=cfg.get("route_since")):
        raw = box.fetch(uid)
        rec = E.record_from_bytes(raw, f"imap:{uid.decode() if isinstance(uid, bytes) else uid}")
        mid = rec.get("message_id") or "sha1:" + hashlib.sha1(raw).hexdigest()
        prev = state.get(mid)
        if prev and prev["moved_at"]:
            if not prev["override"]:
                state.mark(mid, override=1)
                log(mid, "moved back to the Inbox by hand - leaving it there")
            continue
        if classifier is not None and redactor is None:
            redactor = build_redactor()
        try:
            d = decide(rec, state, redactor, classifier, cfg.get("rules"))
        except Exception as exc:   # API or network trouble: keep it in the Inbox and retry
            if state.fail(mid) < MAX_FAILURES:
                log(mid, f"left in Inbox, will retry ({type(exc).__name__})")
                continue
            d = Decision("F9", urgent_hint(rec.get("subject", "")), "fallback", "low",
                         f"classification failed {MAX_FAILURES}x ({type(exc).__name__})")
        if d is None:
            if dry_run or state.first_sight(mid):
                urgent = urgent_hint(new_text(rec))
                if urgent and not dry_run:
                    box.flag(uid)
                log(mid, f"left in Inbox (rules-only mode, no rule matched) urgent={urgent}")
            continue
        log(mid, f"{'DRY ' if dry_run else ''}{names[d.folder]} urgent={d.urgent} "
                 f"via {d.source}/{d.confidence}: {d.reason}")
        if dry_run:
            continue
        out, drafting = None, False
        if drafter is not None:
            bucket, why = T.classify(rec)
            drafting = DR.qualifies(d, bucket, state, mid)   # legal and relayed mail too, flagged as such
            out = DR.prepare(rec, mid, d, drafter, redactor, cfg, log, why) if drafting else None
        if d.urgent:
            box.flag(uid)   # flags travel with the message when it moves
        box.move(uid, folders[d.folder])
        state.record(mid, refs(rec), d, moved=True)
        LG.note_received(state, mid, rec, d.folder, LG.event_names(), log)
        moved += 1
        if out is not None and DR.save(box, drafts_folder, rec, mid, d, state, out, drafter, cfg, log):
            AS.maybe_send(cfg, state, box, rec, mid, d, log)
        elif drafting and not DR.has_draft(state, mid):   # the card still shows, with an empty reply to write
            DR.save_empty(state, rec, mid, d, log, why)
    if not dry_run:
        A.daily_draft(box, state, cfg, folders, log)
        purge_phishing(box, state, cfg, folders, log)
        DR.maybe_crawl(state, log)
        DR.maybe_refresh_doc(state, log)
        LG.after_cycle(state, cfg, log)
        AS.send_due(cfg, state, box, log)
    return moved


def loop(cfg: dict, run=None, sleep=time.sleep, cycles: int | None = None) -> None:
    """Run forever under launchd KeepAlive. launchd's StartInterval kept being
    deferred after the Mac slept; a resident loop just carries on after wake.
    A failed run (no network after wake, server hiccup) is logged and retried."""
    run = run or run_once
    n = 0
    while cycles is None or n < cycles:
        ok, err = True, ""
        try:
            moved = run(cfg)
            if moved:
                print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} filed {moved} emails",
                      file=sys.stderr, flush=True)
        except Exception as exc:
            ok, err = False, f"{type(exc).__name__}: {exc}"
            traceback.print_exc(file=sys.stderr)
        try:
            DB.heartbeat(cfg["state_dir"], ok, err)
            DB.write(cfg)
        except Exception:   # the status page must never stop the router
            traceback.print_exc(file=sys.stderr)
        n += 1
        sleep(cfg.get("poll_seconds", 120))


def status(cfg: dict) -> None:
    box = connect(cfg)
    wanted = {"MOVE", "UIDPLUS", "QUOTA", "IDLE", "SPECIAL-USE", "NAMESPACE"}
    print("capabilities:", " ".join(sorted(box.caps & wanted)) or "(none of interest)")
    print(f"namespace prefix {box.prefix!r}, delimiter {box.delim!r}")
    for name, n in box.counts():
        print(f"{n:>7}  {name}")
    print("quota:", box.quota())
    box.logout()
    db = cfg["state_dir"] / "router.db"
    if db.exists():
        rows = State(db).db.execute("SELECT folder, COUNT(*) FROM decisions WHERE moved_at IS NOT NULL "
                                    "GROUP BY folder ORDER BY folder").fetchall()
        print("filed by the router:", {folder_names(cfg).get(f, f): n for f, n in rows})


def undo(cfg: dict, hours: float) -> None:
    state = State(cfg["state_dir"] / "router.db")
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    rows = state.db.execute("SELECT message_id, folder, urgent FROM decisions WHERE moved_at >= ? "
                            "AND undone_at IS NULL", (since,)).fetchall()
    box = connect(cfg)
    folders = box.ensure_folders(folder_names(cfg))
    back = 0
    for mid, fid, urg in rows:
        box.select(folders[fid])
        for uid in box.find(mid):
            if urg:
                box.unflag(uid)
            box.move(uid, Mailbox.INBOX)
            back += 1
        state.mark(mid, undone_at=now(), override=1)
    box.logout()
    print(f"moved {back} emails back to the Inbox; the router will leave them there")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--once", action="store_true", help="file everything currently in the Inbox")
    g.add_argument("--loop", action="store_true", help="run --once every poll_seconds, forever (launchd)")
    g.add_argument("--status", action="store_true", help="show folders, counts, quota, capabilities")
    g.add_argument("--undo", type=float, metavar="HOURS", help="move back what was filed in the last HOURS")
    g.add_argument("--open", action="store_true", help="list mail since route_since still waiting for a reply")
    g.add_argument("--drafts", action="store_true", help="how the reply drafts are doing")
    ap.add_argument("--dry-run", action="store_true", help="with --once: log decisions, move nothing")
    args = ap.parse_args()
    cfg = load_config()
    if args.status:
        status(cfg)
    elif args.undo is not None:
        undo(cfg, args.undo)
    elif args.open:
        box = connect(cfg)
        rows = A.open_rows(box, cfg, box.ensure_folders(folder_names(cfg)))
        box.logout()
        print(A.format_digest(rows, datetime.now(timezone.utc), cfg.get("overdue_days", 2)))
    elif args.drafts:
        print(DR.report(State(cfg["state_dir"] / "router.db")))
    elif args.loop:
        loop(cfg)
    else:
        print(f"filed {run_once(cfg, args.dry_run)} emails", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
