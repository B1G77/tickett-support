"""Tests for stage 5 sampling helpers."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import sample as S


def test_contact_form_without_a_question_is_content_free():
    form = {"subject": "Nova Entrada de Formulário [ORDER_REF] para Contato",
            "text": "You have a new website form submission:\nNome\n[NAME]\nE-mail\n[EMAIL]\n"
                    "Telefone\n[PHONE]\n---\nEsta mensagem foi enviada de [LINK]"}
    assert S.content_free(form)


def test_subject_only_question_is_not_content_free():
    case = {"subject": "Olá bom dia eu gostaria de saber quando o Jotape vem", "text": ""}
    assert not S.content_free(case)


def test_theme_tagging():
    assert set(S.themes_of("Quero o reembolso, o evento foi cancelado")) == {"reembolso", "cancelamento"}
