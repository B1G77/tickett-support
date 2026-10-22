#!/usr/bin/env python3
"""Label the whole archive in-session (Claude reading, no API): show a batch
of redacted cases, then save the codes typed for it.

Code per case:  <folder 1-9><sub-intent a-f>[!][=N|P|A]
    1b!   Cancelamentos e Reembolsos / refund-status chaser / urgent
    8a    Não-cliente / scam
The reply decision - N no reply, P standard reply (the terms answer it),
A reply with action - follows from the sub-intent unless overridden with =X.

Usage: session_label.py --show N | --save N "codes ..." | --summary
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
REDACTED = DATA / "04_redacted.jsonl"
CASES = DATA / "03_cases.jsonl"
OUT = DATA / "10_session_labels.jsonl"
BATCH = 100

SUBS = {
    "1": {"a": "evento cancelado", "b": "ponto de situação do reembolso", "c": "evento adiado ou alterado",
          "d": "desistência do cliente", "e": "reclamação sobre o evento"},
    "2": {"a": "bilhete não recebido", "b": "email errado na compra", "c": "não abre / formato / PDF",
          "d": "bilhete errado", "e": "perdeu o bilhete / pede reenvio"},
    "3": {"a": "erro no pagamento, valor debitado", "b": "pagamento pendente / referência MB",
          "c": "cobrança duplicada / compra a mais", "d": "fatura / NIF", "e": "não consegue comprar"},
    "4": {"a": "mudar data ou sessão", "b": "mudar lugares ou zona", "c": "mudar titular / transferir",
          "d": "mudar tipo de bilhete"},
    "5": {"a": "disponibilidade, preço, como comprar", "b": "idade e crianças",
          "c": "local, horário, estacionamento, transporte", "d": "VIP e conteúdo do bilhete", "e": "acessibilidade"},
    "6": {"a": "estado ou entrega da encomenda", "b": "morada, tamanho ou alteração", "c": "cancelar ou reembolsar encomenda"},
    "7": {"a": "promotor: vendas, controlo, pagamentos", "b": "parceria ou prestador de serviços"},
    "8": {"a": "burla / phishing", "b": "spam / publicidade", "c": "notificação automática", "d": "candidatura / administração"},
    "9": {"a": "vazio ou ilegível", "b": "aviso formal sem conteúdo", "c": "comprou noutra plataforma",
          "d": "agradecimento / encerramento"},
}
NO_REPLY = {"8a", "8b", "8c", "8d", "9d"}
STANDARD = {"1a", "1c", "1d", "2c", "2e", "3b", "3e", "4a", "4b", "4c", "4d",
            "5a", "5b", "5c", "5d", "5e", "9c"}
_CODE = re.compile(r"^([1-9])([a-f])?(!)?(?:=([NPA]))?$")


def reply_for(folder: str, sub: str) -> str:
    key = folder + sub
    if key in NO_REPLY or (folder == "8" and not sub):
        return "N"
    return "P" if key in STANDARD else "A"


def parse(code: str) -> dict:
    m = _CODE.match(code)
    if not m or (m.group(2) and m.group(2) not in SUBS[m.group(1)]):
        raise ValueError(f"bad code {code!r}")
    folder, sub = m.group(1), m.group(2) or ""
    return {"folder": f"F{folder}", "sub": sub, "urgent": bool(m.group(3)),
            "reply": m.group(4) or reply_for(folder, sub)}


def load(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def ordered() -> list[dict]:
    first = {c["case_id"]: c["first_date"] for c in load(CASES)}
    return sorted(load(REDACTED), key=lambda r: (first.get(r["case_id"], ""), r["case_id"]))


def saved() -> dict:
    return {r["case_id"]: r for r in load(OUT)} if OUT.exists() else {}


def show(n: int) -> None:
    rows = ordered()[(n - 1) * BATCH: n * BATCH]
    for i, r in enumerate(rows, 1):
        text = " ".join(r["text"].split())[:380]
        print(f"{i}|{','.join(r['languages']) or '?'}|{r['n_messages']}m|{' '.join(r['subject'].split())[:80]} :: {text}")
    print(f"-- batch {n}: {len(rows)} cases (of {len(ordered())}; {-(-len(ordered()) // BATCH)} batches)")


def save(n: int, codes: str) -> None:
    rows = ordered()[(n - 1) * BATCH: n * BATCH]
    parts = codes.split()
    if len(parts) != len(rows):
        sys.exit(f"batch {n} has {len(rows)} cases but {len(parts)} codes were given")
    labels = saved()
    for r, code in zip(rows, parts):
        labels[r["case_id"]] = {"case_id": r["case_id"], "batch": n, "code": code, **parse(code)}
    with OUT.open("w", encoding="utf-8") as f:
        for rec in labels.values():
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    got = Counter(parse(c)["folder"] for c in parts)
    print(f"saved batch {n}: {dict(sorted(got.items()))} | total labelled {len(labels)}")


def summary() -> None:
    labels = saved()
    total = len(labels)
    print(f"labelled: {total} of {len(ordered())}")
    for key in ("folder", "reply"):
        print(f"  {key}: {dict(sorted(Counter(v[key] for v in labels.values()).items()))}")
    print(f"  urgent: {sum(v['urgent'] for v in labels.values())}")
    old = DATA / "06_labels.json"
    if old.exists():
        prev = {cid: v["v2"] for b in json.loads(old.read_text()).values() for cid, v in b.items()}
        both = [c for c in prev if c in labels]
        same = sum(prev[c] == labels[c]["folder"] for c in both)
        if both:
            print(f"  self-consistency with the earlier 360 discovery labels: {same}/{len(both)}"
                  f" = {100 * same / len(both):.0f}%")


def main() -> int:
    global REDACTED, CASES, OUT
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--show", type=int)
    g.add_argument("--save", type=int)
    g.add_argument("--summary", action="store_true")
    ap.add_argument("codes", nargs="?", default="")
    ap.add_argument("--set", choices=["archive", "left"], default="archive",
                    help="left: the INBOX leftovers from inbox_leftover.py --fetch")
    a = ap.parse_args()
    if a.set == "left":
        REDACTED, CASES, OUT = (DATA / "11_left_redacted.jsonl", DATA / "11_left_cases.jsonl",
                                DATA / "11_left_labels.jsonl")
    if a.show:
        show(a.show)
    elif a.save:
        save(a.save, a.codes)
    else:
        summary()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
