#!/usr/bin/env python3
"""Build the management report PDF on the pen.dev template.

Inputs in data/report/: frente.json (cover, summary, method, results, findings -
the template's fixed pages) and conteudo.md (one "## N. Name" section per email
category). Fixed pages are laid out like the template (794 x 1123 px, no print
margin); the category sections flow across pages with a print margin. The PDFs
are merged in reading order and every page after the cover gets the footer and
page number (scripts/pdfmerge.swift), because WebKit cannot repeat footers.

--resumo builds the short version for management from frente-resumo.json:
the same template, but only cover, executive summary and results.
--word writes the same text as an .rtf that Pages and Word open and edit.
RTF keeps the tables but not the chart, so the months become a table.

Usage: report.py [--html-only] [--resumo | --word]
"""
from __future__ import annotations

import html
import json
import re
import subprocess
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "report"
FRONT = DATA / "frente.json"
SRC = DATA / "conteudo.md"
FRONT_HTML = DATA / "_frente.html"
TOPICS_HTML = DATA / "_categorias.html"
OUT_PDF = DATA / "LeBillet-tipos-de-email.pdf"
RESUMO_FRONT = DATA / "frente-resumo.json"
RESUMO_HTML = DATA / "_resumo.html"
RESUMO_PDF = DATA / "LeBillet-resumo.pdf"
WORD_HTML = DATA / "_resumo-editavel.html"
WORD_RTF = DATA / "LeBillet-resumo-editavel.rtf"
BIN = Path.home() / "lebillet-data" / "bin"
FLOW_MARGIN_PT = "40"
FONTS = ('<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif&'
         'family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">')

CSS = """
:root{--ink:#101418;--muted:#6B7280;--line:#E3E2DC;--accent:#0F5BFF;--accent2:#7FA0FF;--soft:#F1F0EB;--good:#1F7A4D;--pale:#C6D3F6}
*{box-sizing:border-box}html{-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{margin:0;font-family:Inter,system-ui,sans-serif;color:var(--ink);background:#fff}
.serif{font-family:"Instrument Serif",Georgia,serif;font-weight:400}
.pg{width:794px;height:1122px;position:relative;overflow:hidden;display:flex;flex-direction:column;break-after:page}
.pg:last-child{break-after:auto}
.band{height:14px;background:var(--accent);flex-shrink:0}
.brand{margin:72px 72px 0;padding-bottom:16px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center}
.logo{display:flex;align-items:center;gap:9px;font-weight:700;font-size:15px}.logo img{width:30px;height:30px;border-radius:7px}
.cap,.lbl{font-size:9px;font-weight:600;letter-spacing:1.5px;color:var(--muted)}.cap{font-size:10px}
.titleblock{flex:1;display:flex;flex-direction:column;justify-content:center;gap:28px;padding:0 72px}
.eyebrow{font-size:11px;font-weight:600;letter-spacing:2px;color:var(--accent)}
.cover-title{font-size:66px;line-height:69px}.divider{width:88px;height:3px;background:var(--ink)}
.standfirst{font-size:15px;line-height:25px;color:var(--muted);width:520px}
.meta3{display:flex;gap:56px;padding:0 72px 72px}.meta3>div{flex:1}.lbl{margin-bottom:6px}.val{font-size:13px;line-height:19px;font-weight:500}
.head{display:flex;justify-content:space-between;padding:56px 72px 14px;font-size:9.5px;letter-spacing:1.3px}
.head .t{color:var(--muted);font-weight:500}.head .s{font-weight:600}.rule{height:1px;background:var(--ink)}
.content{padding:40px 72px 0;display:flex;flex-direction:column;gap:30px;flex:1}
.h{font-size:38px;line-height:44px;margin-top:12px}
.kpis{display:flex;gap:24px}.kpi{flex:1}.kpi .bar{height:3px;background:var(--accent);margin-bottom:10px}
.kpi .v{font-size:46px;line-height:48px}.kpi .l{font-size:10px;line-height:14px;font-weight:600;letter-spacing:1.2px;color:var(--muted);margin-top:10px}
.cols{display:flex;gap:40px;border-top:1px solid var(--line);padding-top:28px}.narr{flex:1;display:flex;flex-direction:column;gap:16px}
.lede{font-size:14px;line-height:23px;font-weight:500}.p{font-size:12.5px;line-height:21px;color:var(--muted)}
.glance{width:236px;flex-shrink:0;background:var(--soft);border-radius:3px;padding:20px;display:flex;flex-direction:column;gap:14px}
.pt{display:flex;gap:10px;font-size:11.5px;line-height:17px}.pt i{width:3px;flex-shrink:0;background:var(--accent)}
.scope{border-top:1px solid var(--line);padding-top:28px}.sub{font-size:15px;font-weight:600;margin-bottom:18px}
.items{display:flex;gap:32px}.items>div{flex:1}.num{font-size:20px;color:var(--accent)}
.it{font-size:12.5px;line-height:18px;font-weight:600;margin:8px 0}.id{font-size:11.5px;line-height:18px;color:var(--muted)}
.studies{display:flex;flex-direction:column;gap:24px}
.study{display:flex;gap:24px;border-top:1px solid var(--line);padding-top:24px}.study .n{font-size:38px;line-height:38px;color:var(--accent);width:56px;flex-shrink:0}
.study .tt{font-size:16px;line-height:21px;font-weight:600}.study .m{font-size:11px;color:var(--accent);font-weight:500;margin:8px 0}
.box{background:var(--soft);border-radius:3px;padding:22px 24px}.box .lbl{color:var(--accent)}.box .b{font-size:11.5px;line-height:19px}
.ft{font-size:13.5px;line-height:19px;font-weight:600}.fighead{display:flex;justify-content:space-between;align-items:flex-end}
.legend{display:flex;gap:16px;font-size:10.5px;color:var(--muted)}.sw{display:inline-block;width:9px;height:9px;background:var(--accent);border-radius:1px;margin-right:6px}
.sw.muted{background:var(--pale)}
.plot{display:flex;align-items:flex-end;gap:5px;height:214px;margin-top:18px}
.col{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;gap:4px;height:100%}
.col b{display:block;width:100%;background:var(--accent);border-radius:2px 2px 0 0}.col b.muted{background:var(--pale)}
.col span{font-size:7.5px;color:var(--muted)}.col em{font-size:7.5px;font-style:normal}
.note{border-top:1px solid var(--line);padding-top:10px;margin-top:8px;font-size:10px;line-height:15px;color:var(--muted)}
.tblock{border-top:1px solid var(--line);padding-top:24px}
table.t{width:100%;border-collapse:collapse}.t th{font-size:9px;letter-spacing:1.2px;font-weight:600;text-align:left;padding:16px 8px 9px 0;border-bottom:1.5px solid var(--ink)}
.t td{font-size:12px;line-height:17px;padding:11px 8px 11px 0;border-bottom:1px solid var(--line);color:var(--muted)}.t td:first-child{color:var(--ink);font-weight:500}
.t th.r,.t td.r{text-align:right;padding-right:0}.t td.r:last-child{color:var(--ink);font-weight:600}
.finds{display:flex;flex-direction:column;gap:20px}
.find{display:flex;gap:20px;border-top:1px solid var(--line);padding-top:20px}.find .n{font-size:30px;line-height:30px;color:var(--accent2);width:48px;flex-shrink:0}
.find .c{font-size:14px;line-height:20px;font-weight:600}.find .e{font-size:12px;line-height:20px;color:var(--muted);margin-top:7px}
.sign{background:var(--ink);border-radius:3px;padding:24px;display:flex;justify-content:space-between;align-items:center;color:#fff}
.sign .a{font-size:13px;font-weight:600}.sign .b{font-size:11.5px;color:#9AA3AE;margin-top:6px}.sign .mk{font-size:9px;letter-spacing:1.4px;color:var(--accent2);font-weight:600}
.flow{padding:0 72px}.sec{break-before:page}.sec:first-child{break-before:auto}
.sec .head{padding:10px 0 14px}.sec .rule{margin-bottom:34px}.sec .h{margin:12px 0 20px}
.flow h3{font-size:14px;margin:18px 0 6px}.flow p{font-size:12px;line-height:20px;margin:6px 0}
.flow ul,.flow ol{padding-left:18px;margin:6px 0}.flow li{font-size:12px;line-height:19px;margin:3px 0}
.flow .label{font-size:12px;margin:16px 0 6px}
.flow blockquote{margin:8px 0;padding:10px 14px;background:var(--soft);border-left:3px solid var(--accent);font-size:11.5px;line-height:18px;break-inside:avoid}
.flow blockquote.reply{background:#fff;border:1px solid var(--line);border-left:3px solid var(--good);font-size:11px}
.flow table{width:100%;border-collapse:collapse;font-size:11px}.flow th{text-align:left;border-bottom:1.5px solid var(--ink);padding:8px 6px;font-size:9px}
.flow td{border-bottom:1px solid var(--line);padding:8px 6px;vertical-align:top}
.keep{break-inside:avoid}.ph{color:var(--accent);font-weight:600}.todo{background:#FFF1E6;color:#9A3412;padding:0 4px;border-radius:2px;font-weight:600}
"""

_INLINE = [(re.compile(r"\*\*(.+?)\*\*"), r"<strong>\1</strong>"),
           (re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])"), r"<em>\1</em>"),
           (re.compile(r"\[a calcular\]"), r'<span class="todo">[a calcular]</span>'),
           (re.compile(r"\{([^{}]{1,60})\}"), r'<span class="ph">{\1}</span>')]


def inline(text: str) -> str:
    out = html.escape(text, quote=False)
    for rx, rep in _INLINE:
        out = rx.sub(rep, out)
    return out


def md_to_html(md: str) -> str:
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    title_done = False
    reply_next = False
    while i < len(lines):
        line = lines[i].rstrip()
        if not line:
            i += 1
            continue
        if line.startswith("# ") and not title_done:
            sub = lines[i + 1].strip() if i + 1 < len(lines) else ""
            logo = '<img src="logo.png" alt="">' if (DATA / "logo.png").exists() else ""
            out.append(f'<section class="cover">{logo}<h1>{inline(line[2:])}</h1><p>{inline(sub)}</p></section><div class="page">')
            title_done = True
            i += 2
            continue
        if line.startswith("## "):
            topic = re.match(r"## (\d+)\.", line) and int(re.match(r"## (\d+)\.", line).group(1)) >= 4
            out.append(f'<h2 class="{"topic" if topic else ""}">{inline(line[3:])}</h2>')
        elif line.startswith("### "):
            out.append(f"<h3>{inline(line[4:])}</h3>")
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            body = [r for r in rows[1:] if not all(re.fullmatch(r":?-+:?", c) for c in r)]
            out.append("<table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in rows[0]) + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body)
                       + "</tbody></table>")
            continue
        elif line.startswith(">"):
            quote = []
            while i < len(lines) and lines[i].startswith(">"):
                quote.append(inline(lines[i][1:].strip()))
                i += 1
            cls = ' class="reply"' if reply_next else ""
            out.append(f"<blockquote{cls}>" + "<br>".join(quote) + "</blockquote>")
            continue
        elif re.match(r"(\d+\.|-) ", line):
            ordered = line[0].isdigit()
            items = []
            while i < len(lines) and re.match(r"(\d+\.|-) ", lines[i]):
                items.append(f"<li>{inline(re.sub(r'^(\d+\.|-) ', '', lines[i]))}</li>")
                i += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>{''.join(items)}</{tag}>")
            continue
        elif re.fullmatch(r"\*{1,2}[^*].*?\*{1,2}", line):
            out.append(f'<p class="label">{inline(line)}</p>')
        else:
            out.append(f"<p>{inline(line)}</p>")
        # Quotes that follow a "Resposta-tipo" heading are reply templates, not customer examples.
        if line.startswith(("**Resposta", "*A.", "*B.", "*C.", "*D.")):
            reply_next = True
        elif line.startswith(("**Exemplo", "**O que é", "**Casos", "**Como", "**Notas", "**Informação", "## ", "### ")):
            reply_next = False
        i += 1
    if title_done:
        out.append("</div>")
    return "\n".join(keep_with_next(out))


def keep_with_next(blocks: list[str]) -> list[str]:
    """WebKit ignores break-after:avoid when printing, but honours
    break-inside:avoid, so a label and the block it introduces are wrapped
    together and can never be split across pages."""
    merged: list[str] = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        nxt = blocks[i + 1] if i + 1 < len(blocks) else ""
        after = blocks[i + 2] if i + 2 < len(blocks) else ""
        if b.startswith('<p class="label">') and nxt.startswith(("<blockquote", "<ul>", "<ol>", "<table>")):
            merged.append(f'<div class="keep">{b}{nxt}</div>')
            i += 2
            continue
        # "Respostas-tipo propostas" followed by "A. ..." and its template: keep all three together
        if b.startswith('<p class="label">') and nxt.startswith('<p class="label">') and after.startswith("<blockquote"):
            merged.append(f'<div class="keep">{b}{nxt}{after}</div>')
            i += 3
            continue
        merged.append(b)
        i += 1
    return merged


def e(text) -> str:
    return html.escape(str(text), quote=False)


def page_head(doc_title: str, section: str) -> str:
    return (f'<div class="head"><span class="t">{e(doc_title).upper()}</span>'
            f'<span class="s">{e(section)}</span></div><div class="rule"></div>')


def fixed_page(f: dict, section: str, num: str, heading: str, body: str) -> str:
    return (f'<div class="pg">{page_head(f["doc_title"], section)}<div class="content"><div>'
            f'<div class="eyebrow">{num}</div><div class="serif h">{e(heading)}</div></div>{body}</div></div>')


def cover(f: dict) -> str:
    c = f["cover"]
    logo = '<img src="logo.png" alt="">' if (DATA / "logo.png").exists() else ""
    meta = "".join(f'<div><div class="lbl">{e(label)}</div><div class="val">{"<br>".join(map(e, value))}</div></div>'
                   for label, value in (("PREPARADO POR", c["prepared_by"]), ("PREPARADO PARA", c["prepared_for"]),
                                        ("DATA", c["date"])))
    return (f'<div class="pg"><div class="band"></div><div class="brand"><div class="logo">{logo}{e(f["company"])}</div>'
            f'<div class="cap">{e(c["doc_type"])}</div></div><div class="titleblock">'
            f'<div class="eyebrow">{e(c["eyebrow"])}</div><div class="serif cover-title">{e(c["title"])}</div>'
            f'<div class="divider"></div><div class="standfirst">{e(c["standfirst"])}</div></div>'
            f'<div class="meta3">{meta}</div></div>')


def summary(f: dict) -> str:
    s = f["summary"]
    kpis = "".join(f'<div class="kpi"><div class="bar"></div><div class="serif v">{e(k["value"])}</div>'
                   f'<div class="l">{e(k["label"])}</div></div>' for k in s["kpis"])
    paras = "".join(f'<div class="p">{inline(p)}</div>' for p in s["paragraphs"])
    glance = "".join(f'<div class="pt"><i></i><div>{inline(g)}</div></div>' for g in s["glance"])
    items = "".join(f'<div><div class="serif num">{n}</div><div class="it">{e(i["title"])}</div>'
                    f'<div class="id">{e(i["desc"])}</div></div>' for n, i in zip(("I", "II", "III"), s["scope"]))
    body = (f'<div class="kpis">{kpis}</div><div class="cols"><div class="narr"><div class="lede">{inline(s["lede"])}</div>'
            f'{paras}</div><div class="glance"><div class="lbl">EM RESUMO</div>{glance}</div></div>'
            f'<div class="scope"><div class="sub">{e(s["scope_title"])}</div><div class="items">{items}</div></div>')
    return fixed_page(f, "SUMÁRIO EXECUTIVO", "01", s["heading"], body)


def method(f: dict) -> str:
    m = f["method"]
    studies = "".join(f'<div class="study"><div class="serif n">{i:02d}</div><div><div class="tt">{e(s["title"])}</div>'
                      f'<div class="m">{e(s["meta"])}</div><div class="p">{inline(s["desc"])}</div></div></div>'
                      for i, s in enumerate(m["studies"], 1))
    body = (f'<div class="p" style="font-size:13.5px;line-height:23px;width:560px">{inline(m["intro"])}</div>'
            f'<div class="studies">{studies}</div><div class="box"><div class="lbl">FONTES DE DADOS E LIMITAÇÕES</div>'
            f'<div class="b">{inline(m["limits"])}</div></div>')
    return fixed_page(f, "ABORDAGEM E MÉTODO", "02", m["heading"], body)


def table(head: list, rows: list) -> str:
    th = "".join(f'<th class="{"r" if i and len(head) > 2 and i > 0 else ""}">{e(h)}</th>' for i, h in enumerate(head))
    tr = "".join("<tr>" + "".join(f'<td class="{"r" if i else ""}">{inline(c)}</td>' for i, c in enumerate(row)) + "</tr>"
                 for row in rows)
    return f'<table class="t"><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table>'


def results(f: dict) -> str:
    r = f["results"]
    top = max(v for _, v in r["months"])
    cols = "".join(f'<div class="col"><em>{v}</em><b class="{"" if m >= r["highlight_from"] else "muted"}" '
                   f'style="height:{max(2, round(170 * v / top))}px"></b><span>{m[5:]}/{m[2:4]}</span></div>'
                   for m, v in r["months"])
    legend = (f'<div class="legend"><span><i class="sw muted"></i>{e(r["legend"][0])}</span>'
              f'<span><i class="sw"></i>{e(r["legend"][1])}</span></div>')
    body = (f'<div><div class="fighead"><div><div class="lbl" style="color:var(--accent)">FIGURA 1</div>'
            f'<div class="ft">{e(r["figure_title"])}</div></div>{legend}</div><div class="plot">{cols}</div>'
            f'<div class="note">{e(r["axis_note"])}</div></div>'
            f'<div class="tblock"><div class="ft">{e(r["table_title"])}</div>{table(r["table_head"], r["table_rows"])}</div>')
    return fixed_page(f, "RESULTADOS", r.get("number", "03"), r["heading"], body)


def findings(f: dict) -> str:
    x = f["findings"]
    items = "".join(f'<div class="find"><div class="serif n">{i:02d}</div><div><div class="c">{inline(it["claim"])}</div>'
                    f'<div class="e">{inline(it["evidence"])}</div></div></div>' for i, it in enumerate(x["items"], 1))
    body = (f'<div class="finds">{items}</div><div class="tblock"><div class="sub" style="margin:0">{e(x["actions_title"])}</div>'
            f'{table(x["actions_head"], x["actions"])}</div><div class="sign"><div><div class="a">{e(x["signoff"][0])}</div>'
            f'<div class="b">{e(x["signoff"][1])}</div></div><div class="mk">{e(x["mark"])}</div></div>')
    return fixed_page(f, "CONCLUSÕES E PRÓXIMOS PASSOS", x["number"], x["heading"], body)


def front_html(f: dict) -> str:
    """Five fixed pages, in template order: cover, summary, method, results, findings."""
    return cover(f) + summary(f) + method(f) + results(f) + findings(f)


def keep_last(section_html: str) -> str:
    """Bind a section's last block to the one before it, so a section never
    ends with a single orphaned line on an otherwise empty page."""
    blocks = section_html.split("\n")
    if len(blocks) >= 2:
        blocks[-2:] = [f'<div class="keep">{blocks[-2]}{blocks[-1]}</div>']
    return "\n".join(blocks)


def short_html(f: dict) -> str:
    """The short version: cover, summary and results, without method, findings or categories."""
    return cover(f) + summary(f) + results(f)


def plain_table(head: list, rows: list) -> str:
    """A table with no classes, for the editable copy: the RTF converter drops CSS."""
    th = "".join(f"<th>{e(h)}</th>" for h in head)
    tr = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row) + "</tr>" for row in rows)
    return f'<table border="1"><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table>'


def word_html(f: dict) -> str:
    """The short report as plain HTML, to convert into an editable .rtf. No page frames
    and no styling; the chart becomes a table of months, which RTF can carry."""
    c, s, r = f["cover"], f["summary"], f["results"]
    kpis = "".join(f'<p><b>{e(k["value"])}</b> — {e(k["label"])}</p>' for k in s["kpis"])
    paras = "".join(f"<p>{inline(p)}</p>" for p in s["paragraphs"])
    glance = "".join(f"<li>{inline(g)}</li>" for g in s["glance"])
    scope = "".join(f'<p><b>{e(i["title"])}</b> — {e(i["desc"])}</p>' for i in s["scope"])
    return (f'<h1>{e(c["title"])}</h1><p><i>{e(c["standfirst"])}</i></p>'
            f'<p>{e(" · ".join(c["prepared_by"] + c["prepared_for"] + c["date"]))}</p>'
            f'<h2>{e(s["heading"])}</h2>{kpis}<p>{inline(s["lede"])}</p>{paras}'
            f'<ul>{glance}</ul>'
            f'<h2>{e(s["scope_title"])}</h2>{scope}'
            f'<h2>{e(r["heading"])}</h2><p><b>{e(r["table_title"])}</b></p>'
            f'{plain_table(r["table_head"], r["table_rows"])}'
            f'<p><b>{e(r["figure_title"])}</b></p>'
            f'{plain_table(["MÊS", "EMAILS"], [[m, str(v)] for m, v in r["months"]])}'
            f'<p>{e(r["axis_note"])}</p>')


def topics_html(md: str, f: dict) -> str:
    out = []
    for chunk in re.split(r"(?m)^## ", md):
        if not chunk.strip():
            continue
        heading, _, body = chunk.partition("\n")
        m = re.match(r"(\d+)\.\s*(.+)", heading.strip())
        num, name = (f"{int(m.group(1)):02d}", m.group(2)) if m else ("", heading.strip())
        out.append(f'<section class="sec">{page_head(f["doc_title"], "TIPOS DE EMAIL")}'
                   f'<div class="eyebrow">{num}</div><div class="serif h">{e(name)}</div>{keep_last(md_to_html(body))}</section>')
    return '<div class="flow">' + "".join(out) + "</div>"


def document(body: str) -> str:
    return (f'<!doctype html><html lang="pt"><head><meta charset="utf-8"><title>LeBillet · Tipos de email</title>'
            f"{FONTS}<style>{CSS}</style></head><body>{body}</body></html>")


def run(*cmd) -> None:
    subprocess.run([str(c) for c in cmd], check=True, timeout=180)


def build(html_only: bool = False) -> Path:
    f = json.loads(FRONT.read_text(encoding="utf-8"))
    FRONT_HTML.write_text(document(front_html(f)), encoding="utf-8")
    TOPICS_HTML.write_text(document(topics_html(SRC.read_text(encoding="utf-8"), f)), encoding="utf-8")
    if html_only:
        return FRONT_HTML
    front_pdf, topics_pdf = DATA / "_frente.pdf", DATA / "_categorias.pdf"
    run(BIN / "html2pdf", FRONT_HTML, front_pdf, "0")
    run(BIN / "html2pdf", TOPICS_HTML, topics_pdf, FLOW_MARGIN_PT)
    # Reading order: cover..results, the category sections, then findings.
    run(BIN / "pdfmerge", OUT_PDF, f'{f["company"]} — Confidencial', f"{front_pdf}:1-4", topics_pdf, f"{front_pdf}:5-5")
    return OUT_PDF


def build_short(html_only: bool = False) -> Path:
    f = json.loads(RESUMO_FRONT.read_text(encoding="utf-8"))
    RESUMO_HTML.write_text(document(short_html(f)), encoding="utf-8")
    if html_only:
        return RESUMO_HTML
    raw = DATA / "_resumo_raw.pdf"
    run(BIN / "html2pdf", RESUMO_HTML, raw, "0")
    run(BIN / "pdfmerge", RESUMO_PDF, f'{f["company"]} — Confidencial', f"{raw}:1-3")
    return RESUMO_PDF


def build_word(html_only: bool = False) -> Path:
    f = json.loads(RESUMO_FRONT.read_text(encoding="utf-8"))
    WORD_HTML.write_text(f'<!doctype html><html lang="pt"><head><meta charset="utf-8">'
                         f'<title>{e(f["doc_title"])}</title></head><body>{word_html(f)}</body></html>',
                         encoding="utf-8")
    if html_only:
        return WORD_HTML
    # -inputencoding so the accents survive; textutil assumes Latin-1 otherwise.
    run("textutil", "-convert", "rtf", "-inputencoding", "UTF-8", WORD_HTML, "-output", WORD_RTF)
    return WORD_RTF


def main() -> int:
    if "--docx" in sys.argv:
        if not RESUMO_FRONT.exists():
            print(f"missing {RESUMO_FRONT}", file=sys.stderr)
            return 1
        import report_docx
        print(report_docx.build_docx(), file=sys.stderr)
        return 0
    word = "--word" in sys.argv
    if word or "--resumo" in sys.argv:
        if not RESUMO_FRONT.exists():
            print(f"missing {RESUMO_FRONT}", file=sys.stderr)
            return 1
        print((build_word if word else build_short)("--html-only" in sys.argv), file=sys.stderr)
        return 0
    for need in (FRONT, SRC):
        if not need.exists():
            print(f"missing {need}", file=sys.stderr)
            return 1
    print(build("--html-only" in sys.argv), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
