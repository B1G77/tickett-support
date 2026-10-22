"""Tests for the Markdown-to-HTML conversion of the management report."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import report as RP
import report_docx as RD


def test_escapes_html_and_marks_placeholders():
    out = RP.inline("<script>x</script> **negrito** {evento} [a calcular]")
    assert "<script>" not in out and "<strong>negrito</strong>" in out
    assert 'class="ph"' in out and 'class="todo"' in out


def test_tables_lists_quotes_and_reply_blocks():
    md = ("# Título\nSub\n\n## 4. Tema\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n- um\n- dois\n\n"
          "**Exemplos**\n> cliente\n\n**Resposta-tipo proposta**\n> Olá {nome}\n")
    out = RP.md_to_html(md)
    assert '<section class="cover">' in out and '<h2 class="topic">' in out
    assert "<td>1</td>" in out and "|---|" not in out and "<li>dois</li>" in out
    assert "<blockquote>cliente</blockquote>" in out and '<blockquote class="reply">' in out


def test_label_lines_keep_with_next():
    out = RP.md_to_html("# T\nS\n\n*A. Evento cancelado*\n\n**Exemplos (anonimizados)**\n")
    assert out.count('class="label"') == 2


def test_label_and_its_block_are_kept_together():
    out = RP.md_to_html("# T\nS\n\n*B. Ponto de situação*\n> Olá {nome}\n")
    assert '<div class="keep"><p class="label"><em>B. Ponto de situação</em></p><blockquote' in out


FRONT = {"doc_title": "T", "company": "LeBillet",
         "cover": {"doc_type": "R", "eyebrow": "E", "title": "Título", "standfirst": "S",
                   "prepared_by": ["A"], "prepared_for": ["B"], "date": ["C"]},
         "summary": {"heading": "Sumário", "kpis": [{"value": "1", "label": "L"}] * 3, "lede": "x",
                     "paragraphs": ["p"], "glance": ["**g**"], "scope_title": "s",
                     "scope": [{"title": "t", "desc": "d"}] * 3},
         "method": {"heading": "M", "intro": "i", "studies": [{"title": "t", "meta": "m", "desc": "d"}], "limits": "l"},
         "results": {"heading": "R", "figure_title": "F", "legend": ["a", "b"], "highlight_from": "2026-03",
                     "months": [["2025-01", 10], ["2026-04", 40]], "axis_note": "n", "table_title": "T",
                     "table_head": ["A", "B", "C"], "table_rows": [["x", "1", "2 %"]]},
         "findings": {"number": "13", "heading": "C", "items": [{"claim": "c", "evidence": "e"}],
                      "actions_title": "a", "actions_head": ["A", "B", "C"], "actions": [["x", "y", "z"]],
                      "signoff": ["s", "t"], "mark": "FIM"}}


def test_front_has_the_five_template_pages_in_order():
    out = RP.front_html(FRONT)
    assert out.count('class="pg"') == 5
    assert out.index("SUMÁRIO EXECUTIVO") < out.index("ABORDAGEM") < out.index("RESULTADOS") < out.index("CONCLUSÕES")
    assert 'class="muted"' in out and "<strong>g</strong>" in out


def test_docx_body_keeps_the_layout_blocks_and_the_chart():
    out = RD.docx_xml(FRONT, chart=True)
    assert out.count("<w:pageBreakBefore/>") == 2   # cover, summary, results
    assert "<w:drawing>" in out and "Título" in out
    for block in ("EM RESUMO", "Sumário", "Resultados", "x"):   # the box, both headings, a folder row
        assert block in out
    assert out.count("<w:tbl>") >= 4        # KPIs, the box, the three blocks, the folders


def test_the_chart_picture_is_not_titled_and_captioned_twice():
    f = dict(FRONT, results=dict(FRONT["results"], figure_title="TITULO DA FIGURA",
                                 axis_note="NOTA DO GRAFICO"))
    with_chart = RD.docx_xml(f, chart=True)      # the picture already carries both
    assert "TITULO DA FIGURA" not in with_chart and "NOTA DO GRAFICO" not in with_chart
    without = RD.docx_xml(f, chart=False)
    assert "TITULO DA FIGURA" in without and "NOTA DO GRAFICO" in without


def test_every_docx_table_declares_its_column_widths():
    out = RD.docx_xml(FRONT, chart=False)
    # Without a grid, Word and Pages collapse every column to one character wide.
    assert out.count("<w:tblGrid>") == out.count("<w:tbl>") > 0
    assert '<w:tblLayout w:type="fixed"/>' in out
    assert '<w:gridCol w:w="5638"/>' in out      # the folders table's first column


def test_docx_sets_the_template_faces_instead_of_word_defaults():
    out = RD.docx_xml(FRONT, chart=False)
    assert 'w:ascii="Georgia"' in out           # display type, standing in for Instrument Serif
    assert 'w:ascii="Helvetica Neue"' in out    # the body text, standing in for Inter


def test_docx_body_without_a_chart_image_still_builds():
    assert "<w:drawing>" not in RD.docx_xml(FRONT, chart=False)


def test_word_version_is_plain_html_with_both_tables():
    out = RP.word_html(FRONT)
    assert 'class="pg"' not in out and "<h1>Título</h1>" in out
    assert out.count("<table") == 2      # the folders, and the months that replace the chart
    assert "<td>x</td>" in out and "<td>2025-01</td>" in out


def test_results_section_number_follows_the_pages_that_are_there():
    short = dict(FRONT, results=dict(FRONT["results"], number="02"))
    assert ">02<" in RP.results(short)
    assert ">03<" in RP.results(FRONT)   # the long document keeps its own numbering


def test_short_front_is_only_cover_summary_and_results():
    out = RP.short_html(FRONT)
    assert out.count('class="pg"') == 3
    assert out.index("SUMÁRIO EXECUTIVO") < out.index("RESULTADOS")
    assert "ABORDAGEM" not in out and "CONCLUSÕES" not in out


def test_each_category_becomes_its_own_section_without_stray_tags():
    out = RP.topics_html("## 4. Cancelamentos\n\ntexto\n\n## 5. Bilhetes\n\n- um\n", FRONT)
    assert out.count('<section class="sec">') == 2 and ">04<" in out and ">05<" in out
    assert out.count("<div") == out.count("</div>")


def test_section_never_ends_with_an_orphan_block():
    out = RP.topics_html("## 5. Bilhetes\n\n**Notas**\n- a\n\n**Potencial:** alto.\n", FRONT)
    assert out.rstrip("</div>").count('class="keep"') >= 1
    assert '<p><strong>Potencial:</strong> alto.</p></div>' in out
