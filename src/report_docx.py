#!/usr/bin/env python3
"""The short management report as a .docx, laid out as close to the PDF as Word allows.

textutil cannot do this: its docx writer drops every table and embeds no image,
whatever the input. So the WordprocessingML is written here by hand - one zip with
document.xml, the two relationship parts and the chart, cropped out of the built PDF
(Word cannot draw the bar chart, so it travels as a picture).

Built from the same frente-resumo.json as the PDF. Usage: report.py --docx
"""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import report as R

CHART_PNG = R.DATA / "_chart.png"
CHART_DIR = R.DATA / "_chartpages"
DOCX_OUT = R.DATA / "LeBillet-resumo.docx"

INK, MUTED, ACCENT, SOFT, LINE = "101418", "6B7280", "0F5BFF", "F1F0EB", "E3E2DC"
# Word has neither Inter nor Instrument Serif: the nearest faces already installed.
SANS, SERIF = "Helvetica Neue", "Georgia"
# A4 (11906 twips) less 2 cm margins on each side.
CONTENT_W = 9638
CHART_W, CHART_H = 850, 372          # pixels of the cropped figure
EMU_W = 5_760_720                    # 6.3 in, the width that fits the text column
EMU_H = round(EMU_W * CHART_H / CHART_W)

CONTENT_TYPES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                 '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                 '<Default Extension="xml" ContentType="application/xml"/>'
                 '<Default Extension="png" ContentType="image/png"/>'
                 '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
                 'officedocument.wordprocessingml.document.main+xml"/></Types>')
RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="word/document.xml"/></Relationships>')
IMG_REL = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
           '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
           'relationships/image" Target="media/chart.png"/></Relationships>')
NO_REL = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')


def runs(text: str, size: int, color: str = INK, caps: bool = False, font: str = SANS) -> str:
    """Runs for one string, switching to bold inside **...** as the JSON marks emphasis."""
    out = []
    for i, part in enumerate(text.split("**")):
        if not part:
            continue
        pr = f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}"/>'
        pr += ("<w:b/>" if i % 2 else "") + ("<w:caps/>" if caps else "")
        pr += f'<w:color w:val="{color}"/>' + ('<w:spacing w:val="30"/>' if caps else "")
        out.append(f'<w:r><w:rPr>{pr}<w:sz w:val="{size}"/></w:rPr>'
                   f'<w:t xml:space="preserve">{R.e(part)}</w:t></w:r>')
    return "".join(out)


def par(text: str = "", size: int = 21, color: str = INK, after: int = 120,
        bold: bool = False, caps: bool = False, rule: bool = False, font: str = SANS) -> str:
    bdr = f'<w:pBdr><w:bottom w:val="single" w:sz="6" w:color="{LINE}"/></w:pBdr>' if rule else ""
    # Even a spacer carries a run: a paragraph with none draws as a stray glyph in some readers.
    body = (runs(f"**{text}**" if bold else text, size, color, caps, font) if text
            else f'<w:r><w:rPr><w:rFonts w:ascii="{font}" w:hAnsi="{font}"/>'
                 f'<w:sz w:val="{size}"/></w:rPr><w:t xml:space="preserve"></w:t></w:r>')
    return f'<w:p><w:pPr>{bdr}<w:spacing w:after="{after}"/></w:pPr>{body}</w:p>'


def cell(content: str, width: int, shade: str = "", top_accent: bool = False) -> str:
    bdr = (f'<w:tcBorders><w:top w:val="single" w:sz="18" w:color="{ACCENT}"/></w:tcBorders>'
           if top_accent else "")
    shd = f'<w:shd w:val="clear" w:color="auto" w:fill="{shade}"/>' if shade else ""
    return (f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{bdr}{shd}'
            f'<w:tcMar><w:top w:w="120" w:type="dxa"/><w:left w:w="120" w:type="dxa"/>'
            f'<w:bottom w:w="120" w:type="dxa"/><w:right w:w="120" w:type="dxa"/></w:tcMar>'
            f'</w:tcPr>{content or par()}</w:tc>')


def table(rows: list[str], widths: list[int], inside_rule: bool = False) -> str:
    """`widths` is required: without a tblGrid, Word and Pages collapse every
    column to one character wide and the document runs to ten pages."""
    edges = ("top", "left", "bottom", "right", "insideV")
    bdr = "".join(f'<w:{s} w:val="none" w:sz="0" w:color="auto"/>' for s in edges)
    bdr += (f'<w:insideH w:val="single" w:sz="4" w:color="{LINE}"/>' if inside_rule
            else '<w:insideH w:val="none" w:sz="0" w:color="auto"/>')
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
    return (f'<w:tbl><w:tblPr><w:tblW w:w="{sum(widths)}" w:type="dxa"/>'
            f'<w:tblBorders>{bdr}</w:tblBorders><w:tblLayout w:type="fixed"/></w:tblPr>'
            f"<w:tblGrid>{grid}</w:tblGrid>"
            + "".join(f"<w:tr>{r}</w:tr>" for r in rows) + "</w:tbl>")


def head_line(doc_title: str, section: str, brk: bool = False) -> str:
    """The running head of the template: document on the left, section on the right.
    One tabbed paragraph, so the page break can hang on it: a standalone break
    paragraph draws as a stray glyph."""
    return ('<w:p><w:pPr>' + ("<w:pageBreakBefore/>" if brk else "")
            + f'<w:pBdr><w:bottom w:val="single" w:sz="8" w:color="{INK}"/></w:pBdr>'
            + f'<w:tabs><w:tab w:val="right" w:pos="{CONTENT_W}"/></w:tabs>'
            + '<w:spacing w:after="240"/></w:pPr>'
            + runs(doc_title, 17, MUTED, caps=True)
            + "<w:r><w:tab/></w:r>"
            + runs(f"**{section}**", 17, INK, caps=True) + "</w:p>")


def image_par() -> str:
    return ('<w:p><w:pPr><w:spacing w:after="120"/></w:pPr><w:r><w:drawing>'
            f'<wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{EMU_W}" cy="{EMU_H}"/>'
            '<wp:docPr id="1" name="Figura 1"/><a:graphic>'
            '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic>'
            '<pic:nvPicPr><pic:cNvPr id="1" name="chart.png"/><pic:cNvPicPr/></pic:nvPicPr>'
            '<pic:blipFill><a:blip r:embed="rId1"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{EMU_W}" cy="{EMU_H}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData>'
            '</a:graphic></wp:inline></w:drawing></w:r></w:p>')


def cover(c: dict) -> str:
    meta = [("PREPARADO POR", c["prepared_by"]), ("PREPARADO PARA", c["prepared_for"]), ("DATA", c["date"])]
    cells = "".join(cell(par(label, 16, MUTED, 60, caps=True)
                         + "".join(par(v, 21, INK, 40, bold=True) for v in values), 3212)
                    for label, values in meta)
    return (par(c["doc_type"], 16, MUTED, 600, caps=True)
            + par(c["eyebrow"], 18, ACCENT, 160, bold=True, caps=True)
            + par(c["title"], 68, INK, 200, font=SERIF)
            + par("", 10, after=200, rule=True)
            + par(c["standfirst"], 23, MUTED, 700)
            + table([cells], [3212, 3212, 3214]))


def summary(s: dict, doc_title: str) -> str:
    kpis = "".join(cell(par(k["value"], 52, INK, 60, font=SERIF) + par(k["label"], 15, MUTED, 0, caps=True),
                        3212, top_accent=True) for k in s["kpis"])
    narrative = par(s["lede"], 25, INK, 200, bold=True) + "".join(par(p, 21, MUTED, 160) for p in s["paragraphs"])
    box = par("EM RESUMO", 15, MUTED, 140, caps=True) + "".join(par(g, 20, INK, 140) for g in s["glance"])
    blocks = "".join(cell(par(n, 30, ACCENT, 80, font=SERIF) + par(i["title"], 21, INK, 80, bold=True)
                          + par(i["desc"], 20, MUTED, 0), 3212)
                     for n, i in zip(("I", "II", "III"), s["scope"]))
    return (head_line(doc_title, "Sumário executivo", brk=True)
            + par("01", 18, ACCENT, 100, bold=True) + par(s["heading"], 56, INK, 240, font=SERIF)
            + table([kpis], [3212, 3212, 3214]) + par("", 10, after=180)
            + table([cell(narrative, 6000) + cell(box, 3638, shade=SOFT)], [6000, 3638])
            + par("", 10, after=180, rule=True)
            + par(s["scope_title"], 26, INK, 200, bold=True) + table([blocks], [3212, 3212, 3214]))


def results(r: dict, doc_title: str, chart: bool) -> str:
    head = "".join(cell(par(h, 16, MUTED, 0, caps=True), w)
                   for h, w in zip(r["table_head"], (5638, 2000, 2000)))
    rows = [head] + [cell(par(row[0], 21, INK, 0), 5638) + cell(par(row[1], 21, MUTED, 0), 2000)
                     + cell(par(row[2], 21, INK, 0, bold=True), 2000) for row in r["table_rows"]]
    # The cropped picture already shows the title, the legend and the caption, so the
    # text versions would print them a second time. Without it, they carry the figure.
    figure = (par("FIGURA 1", 15, ACCENT, 80, caps=True)
              + (image_par() + par("", 10, after=220) if chart
                 else par(r["figure_title"], 23, INK, 140, bold=True) + par(r["axis_note"], 18, MUTED, 220)))
    return (head_line(doc_title, "Resultados", brk=True)
            + par(r.get("number", "02"), 18, ACCENT, 100, bold=True)
            + par(r["heading"], 56, INK, 240, font=SERIF)
            + figure + par(r["table_title"], 23, INK, 140, bold=True)
            + table(rows, [5638, 2000, 2000], inside_rule=True))


def docx_xml(f: dict, chart: bool = True) -> str:
    body = (cover(f["cover"])
            + summary(f["summary"], f["doc_title"])
            + results(f["results"], f["doc_title"], chart))
    sect = ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
            '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/></w:sectPr>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
            'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f"<w:body>{body}{sect}</w:body></w:document>")


def make_chart() -> Path | None:
    """The chart as a picture, cut out of page 3 of the built PDF."""
    pdf = R.build_short()
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    R.run(R.BIN / "pdf2png", pdf, CHART_DIR, 1000)
    page = CHART_DIR / "page-03.png"
    if not page.exists():
        return None
    shutil.copy(page, CHART_PNG)
    R.run("sips", "-c", CHART_H, CHART_W, "--cropOffset", 298, 80, CHART_PNG, "--out", CHART_PNG)
    return CHART_PNG


def build_docx() -> Path:
    f = json.loads(R.RESUMO_FRONT.read_text(encoding="utf-8"))
    chart = make_chart()
    with zipfile.ZipFile(DOCX_OUT, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS)
        z.writestr("word/_rels/document.xml.rels", IMG_REL if chart else NO_REL)
        z.writestr("word/document.xml", docx_xml(f, chart=chart is not None))
        if chart:
            z.write(chart, "word/media/chart.png")
    return DOCX_OUT
