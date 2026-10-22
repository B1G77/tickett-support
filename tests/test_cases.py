"""Tests for stage 3 case building."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import cases as K


def test_forward_keeps_the_customers_words():
    body = ("Segue o email do cliente.\n\n"
            "---------- Forwarded message ---------\n"
            "De: Cliente <c@x.pt>\nDate: 1 Mai 2026\nSubject: Bilhetes\nTo: camara@y.pt\n\n"
            "Nao recebi os bilhetes para o festival.")
    out = K.strip_quotes(body, K.is_forward("FW: Bilhetes", body))
    assert "Nao recebi os bilhetes" in out
    assert "Segue o email" in out
    assert "Date:" not in out and "c@x.pt" not in out


def test_reply_cuts_at_wrapped_gmail_attribution():
    body = ("Obrigada, ja recebi.\n\n"
            "Em sex., 20 de dez. de 2024 às 10:16, Fulano de Tal <\n"
            "fulano@x.pt> escreveu:\n\n> texto antigo do suporte")
    out = K.strip_quotes(body, False)
    assert out == "Obrigada, ja recebi."


def test_reply_cuts_at_outlook_header_block():
    body = "Ok, aguardo.\n\nDe: Suporte <s@lebillet.eu>\nEnviado: 3 Mai\nPara: eu\n\ntexto velho"
    assert K.strip_quotes(body, False) == "Ok, aguardo."


def test_inline_quote_lines_are_dropped_not_cut():
    body = "> pergunta do suporte\nresposta minha\n> outra\nmais resposta"
    out = K.strip_quotes(body, False)
    assert "resposta minha" in out and "mais resposta" in out and "pergunta" not in out


def test_device_footer_removed():
    assert K.strip_quotes("Quero reembolso.\nEnviado do meu iPhone", False) == "Quero reembolso."


def test_stable_case_id_is_deterministic():
    a = K.stable_case_id({"message_id": "<abc@x>", "id": "1"})
    assert a == K.stable_case_id({"message_id": "<abc@x>", "id": "999"})
    assert a.startswith("C") and len(a) == 11


def test_subject_normalisation_strips_stacked_prefixes():
    assert K.normalise_subject("RE: Fwd: ENC: Bilhetes  Crato") == "bilhetes crato"


def test_german_and_dutch_device_footers():
    assert K.strip_quotes("Obrigado\nGesendet von Yahoo Mail für iPhone", False) == "Obrigado"
    assert K.strip_quotes("Ok\nVerstuurd vanaf mijn iPad", False) == "Ok"
