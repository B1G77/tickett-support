"""Tests for stage 1 extraction helpers."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import extract as E


def test_void_tags_do_not_swallow_the_body():
    """<meta>/<link> have no end tag; skipping them once lost the message."""
    html = (
        '<html><head><meta charset="utf-8">'
        '<link rel="stylesheet" href="x.css"></head>'
        "<body><p>Bom dia, nao recebi os meus bilhetes.</p></body></html>"
    )
    assert "nao recebi os meus bilhetes" in E.html_to_text(html)


def test_script_and_style_content_is_dropped():
    html = "<div>Ola<script>var x=1;</script><style>p{color:red}</style></div>"
    text = E.html_to_text(html)
    assert "Ola" in text
    assert "var x" not in text and "color:red" not in text


def test_accents_survive():
    assert "Confirmação de reserva" in E.html_to_text("<p>Confirmação de reserva</p>")


def test_block_tags_become_line_breaks():
    assert E.tidy(E.html_to_text("<p>uma</p><p>duas</p>")) == "uma\n\nduas"


def test_language_guess_needs_evidence():
    assert E.guess_language("ok")[0] == "unknown"
    lang, _ = E.guess_language(
        "Ola bom dia gostaria de saber sobre o reembolso do meu bilhete "
        "para o evento uma vez que nao recebi nada obrigado"
    )
    assert lang == "pt"


def test_emlx_length_prefix_is_honoured(tmp_path):
    msg = b"Subject: teste\r\n\r\ncorpo\r\n"
    f = tmp_path / "1.emlx"
    f.write_bytes(f"{len(msg)}\n".encode() + msg + b"<plist>trailer</plist>")
    assert E.read_emlx(f) == msg


def test_record_from_imap_bytes():
    raw = b"Subject: =?utf-8?q?Reembolso_concerto?=\r\nFrom: Ana <ana@x.pt>\r\nMessage-ID: <1@x>\r\n\r\nquero o reembolso"
    rec = E.record_from_bytes(raw, "imap:42")
    assert rec["id"] == "imap:42" and rec["subject"] == "Reembolso concerto"
    assert rec["from_domain"] == "x.pt" and "reembolso" in rec["body_text"]
