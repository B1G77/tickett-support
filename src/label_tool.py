#!/usr/bin/env python3
"""Build a local, offline labelling page for the blind test set.

Picks a seeded subset of the held-out test set (content-free cases excluded:
they get a fixed route, not a classifier decision) and writes one
self-contained HTML file. Labels are kept in the browser's localStorage and
exported as CSV. Nothing is uploaded anywhere. The page never shows an AI
suggestion, so the labels stay blind.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
TEST = DATA / "05_test_set.jsonl"
TAXONOMY = DATA / "06_taxonomy_v2.json"
OUT_HTML = DATA / "07_label_tool.html"
OUT_IDS = DATA / "07_labelling_sample.json"
SEED = 20260911
N = 150
TEXT_CAP = 4000


def embed_json(obj) -> str:
    """JSON that is safe inside a <script> block."""
    return json.dumps(obj, ensure_ascii=True).replace("</", "<\\/")


def pick(cases: list[dict], n: int, seed: int) -> list[dict]:
    pool = sorted((c for c in cases if not c.get("content_free")), key=lambda c: c["case_id"])
    return random.Random(seed).sample(pool, min(n, len(pool)))


PAGE = """<!doctype html><html lang="pt"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>LeBillet · Rotulagem</title>
<style>
:root{--bg:#f7f6f3;--card:#fff;--ink:#1d1d1f;--mut:#6b6b70;--line:#e3e1dc;--acc:#1f5eff;--sel:#e8efff;--urg:#c62828}
@media (prefers-color-scheme:dark){:root{--bg:#161618;--card:#202023;--ink:#ececef;--mut:#a0a0a8;--line:#333338;--acc:#7aa2ff;--sel:#243152;--urg:#ff6b6b}}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,system-ui,sans-serif}
.wrap{max-width:1150px;margin:0 auto;padding:16px;display:grid;grid-template-columns:1fr 320px;gap:16px}
header{grid-column:1/-1;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.bar{flex:1;min-width:120px;height:6px;background:var(--line);border-radius:3px;overflow:hidden}.bar i{display:block;height:100%;background:var(--acc)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}
#text{white-space:pre-wrap;max-height:65vh;overflow:auto;margin-top:8px;overflow-wrap:anywhere}
.meta{color:var(--mut);font-size:13px}
.f{display:flex;width:100%;text-align:left;gap:8px;padding:8px 10px;margin:4px 0;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);cursor:pointer;font:inherit}
.f.on{background:var(--sel);border-color:var(--acc);font-weight:600}
.k{font-weight:700;color:var(--acc);width:1.2em}
.btn{padding:6px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);cursor:pointer;font:inherit}
.urg{width:100%;margin-top:8px}.urg.on{background:var(--urg);color:#fff;border-color:var(--urg)}
details p{margin:6px 0;font-size:13px}
@media (max-width:820px){.wrap{grid-template-columns:1fr}}
</style></head><body><div class="wrap">
<header><strong>Rotulagem · conjunto de teste</strong><span id="count" class="meta"></span>
<div class="bar"><i id="prog"></i></div>
<button class="btn" id="prev">← Anterior</button><button class="btn" id="next">Seguinte →</button>
<button class="btn" id="dl">Descarregar CSV</button></header>
<main class="card"><div class="meta" id="meta"></div><h3 id="subj"></h3><div id="text"></div></main>
<aside class="card"><div id="folders"></div><button class="btn urg" id="urg">🚩 Urgente (U)</button>
<p class="meta">Teclas 1–9 escolhem a pasta e avançam · U marca urgente · ← → navegam.
Escolha a pasta pela <b>ação necessária</b>. Em caso de dúvida, use a regra de precedência nas definições.</p>
<details><summary>Definições e precedência</summary><div id="defs"></div></details></aside></div>
<script>
const CASES=__DATA__, TAX=__TAX__, KEY="lebillet-test-labels-v2";
const F=TAX.folders.slice().sort((a,b)=>a.id.localeCompare(b.id));
let L={}, i=0;
try{L=JSON.parse(localStorage.getItem(KEY)||"{}");i=Math.min(+(localStorage.getItem(KEY+":i")||0),CASES.length-1)}catch(e){}
const $=id=>document.getElementById(id);
function save(){try{localStorage.setItem(KEY,JSON.stringify(L));localStorage.setItem(KEY+":i",i)}catch(e){}}
function render(){
  const c=CASES[i], l=L[c.case_id]||{};
  const done=CASES.filter(x=>L[x.case_id]&&L[x.case_id].folder).length;
  $("count").textContent=`${i+1} / ${CASES.length} · rotulados ${done}`;
  $("prog").style.width=(100*done/CASES.length)+"%";
  $("meta").textContent=`${c.case_id} · ${(c.languages||[]).join(",")||"?"} · ${c.n_messages} msg`+(c.attachment_hints.length?` · anexos: ${c.attachment_hints.join(", ")}`:"");
  $("subj").textContent=c.subject||"(sem assunto)";
  $("text").textContent=c.text||"(sem texto)";
  $("folders").replaceChildren(...F.map((f,k)=>{const b=document.createElement("button");
    b.className="f"+(l.folder===f.id?" on":"");const s=document.createElement("span");s.className="k";s.textContent=k+1;
    b.append(s,document.createTextNode(f.name));b.onclick=()=>pick(k);return b}));
  $("urg").className="btn urg"+(l.urgent?" on":"");
}
function pick(k){const id=CASES[i].case_id;L[id]=Object.assign({},L[id],{folder:F[k].id});save();if(i<CASES.length-1)i++;render()}
function urg(){const id=CASES[i].case_id;L[id]=Object.assign({},L[id],{urgent:!(L[id]||{}).urgent});save();render()}
function go(d){i=Math.max(0,Math.min(CASES.length-1,i+d));save();render()}
function csv(){const q=s=>'"'+String(s).replace(/"/g,'""')+'"';
  const rows=[["case_id","folder_id","folder","urgent"]];
  for(const c of CASES){const l=L[c.case_id];if(l&&l.folder)rows.push([c.case_id,l.folder,F.find(f=>f.id===l.folder).name,l.urgent?1:0])}
  const a=document.createElement("a");a.href=URL.createObjectURL(new Blob(["\\ufeff"+rows.map(r=>r.map(q).join(",")).join("\\n")],{type:"text/csv"}));
  a.download="test_labels.csv";a.click()}
$("prev").onclick=()=>go(-1);$("next").onclick=()=>go(1);$("urg").onclick=urg;$("dl").onclick=csv;
document.addEventListener("keydown",e=>{if(e.metaKey||e.ctrlKey)return;
  if(/^[1-9]$/.test(e.key))pick(+e.key-1);else if(e.key==="u"||e.key==="U")urg();
  else if(e.key==="ArrowRight")go(1);else if(e.key==="ArrowLeft")go(-1)});
$("defs").replaceChildren(...[...F].sort((a,b)=>a.precedence-b.precedence).map(f=>{const p=document.createElement("p");
  p.textContent=`${f.precedence}. ${f.name} — ${f.definition}`+(f.excludes?` (Exclui: ${f.excludes})`:"");return p}),
  Object.assign(document.createElement("p"),{textContent:"🚩 Urgente — "+TAX.flags[0].definition}));
render();
</script></body></html>
"""


def main() -> int:
    for need in (TEST, TAXONOMY):
        if not need.exists():
            print(f"missing {need}", file=sys.stderr)
            return 1
    cases = [json.loads(line) for line in TEST.open(encoding="utf-8")]
    chosen = pick(cases, N, SEED)
    payload = [{"case_id": c["case_id"], "subject": c["subject"], "text": c["text"][:TEXT_CAP],
                "languages": c["languages"], "n_messages": c["n_messages"],
                "attachment_hints": c["attachment_hints"]} for c in chosen]
    tax = json.loads(TAXONOMY.read_text(encoding="utf-8"))
    OUT_HTML.write_text(PAGE.replace("__DATA__", embed_json(payload)).replace("__TAX__", embed_json(tax)),
                        encoding="utf-8")
    OUT_IDS.write_text(json.dumps([c["case_id"] for c in chosen]), encoding="utf-8")
    print(f"{len(chosen)} cases -> {OUT_HTML}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
