"""Tests for the events document reader (no network). The fixture is made up in the Doc's export format."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import events_doc as D

MD = "\n".join([
    "# **Informação para o apoio ao cliente**",
    "Texto de introdução.",
    "# **Parte 1: Perguntas gerais**",
    "## **Bilhetes não recebidos**",
    "| Podemos enviar o bilhete em PDF? | Não, o bilhete vai por email. |",
    "| :---- | :---- |",
    "| **O bilhete pode ser mostrado no telemóvel?** | **Sim.** |",
    "| **Pergunta sem resposta?** |  |",
    "# **Parte 2: Eventos**",
    "## **Concerto Exemplo**",
    "| Data(s) e local | 2026-05-01 21:00 Sala Exemplo \\- Lisboa |",
    "| :---- | :---- |",
    "| **Estado** | cancelado |",
    "| **Quem trata do reembolso** | promotor |",
    "| **Como é feito o reembolso** | Contactar o promotor\x0bEmpresa Exemplo (exemplo@exemplo.pt). |",
    "| **Informação prática** | bilheteira no local, estacionamento, acessibilidade, regras de entrada |",
    "## **Festa Vazia**",
    "| Data(s) e local |  |",
    "| :---- | :---- |",
    "| **Estado** | ativo / adiado / cancelado |",
    "| **Quem trata do reembolso** | Tickett / promotor |",
    "## **Novo evento**",
    "| Data(s) e local |  |",
    "# **Outras informações**",
    "Espaço para tudo o que ajude a responder aos clientes e não caiba nas partes acima.",
    "Os reembolsos de taxas de serviço não são feitos.",
])


def test_general_answers_and_events_are_read_and_example_text_counts_as_empty():
    doc = D.parse(MD)
    assert doc["general"] == [
        {"topic": "Bilhetes não recebidos", "question": "Podemos enviar o bilhete em PDF?",
         "answer": "Não, o bilhete vai por email."},
        {"topic": "Bilhetes não recebidos", "question": "O bilhete pode ser mostrado no telemóvel?", "answer": "Sim."}]
    names = [e["name"] for e in doc["events"]]
    assert names == ["Concerto Exemplo", "Festa Vazia"]   # the template block is dropped
    ev = doc["events"][0]["fields"]
    assert ev["Estado"] == "cancelado" and ev["Data(s) e local"] == "2026-05-01 21:00 Sala Exemplo - Lisboa"
    assert ev["Como é feito o reembolso"] == "Contactar o promotor Empresa Exemplo (exemplo@exemplo.pt)."
    assert "Informação prática" not in ev and doc["events"][1]["fields"] == {}
    assert doc["other"] == "Os reembolsos de taxas de serviço não são feitos."


def test_prompt_lists_answers_and_names_events_without_information():
    text = D.as_prompt(D.parse(MD))
    assert "Podemos enviar o bilhete em PDF? -> Não" in text and "Pergunta sem resposta" not in text
    assert "## Festa Vazia: sem informação no documento" in text and "Estado: cancelado" in text


def test_a_page_without_the_two_parts_is_rejected():
    try:
        D.parse("<html>Sign in</html>")
        raise AssertionError("should have failed")
    except ValueError:
        pass


def test_refresh_keeps_the_last_good_copy(tmp_path):
    D.refresh(tmp_path, get=lambda url: MD)
    before = (tmp_path / "doc.json").read_text()
    assert json.loads(before)["fetched_at"]
    for bad in (lambda url: "<html>Sign in</html>", lambda url: (_ for _ in ()).throw(OSError("offline"))):
        try:
            D.refresh(tmp_path, get=bad)
        except (ValueError, OSError):
            pass
    assert (tmp_path / "doc.json").read_text() == before and D.load(tmp_path)["events"]


def ev(name, **fields):
    return {"name": name, "fields": fields}


def test_diff_names_added_removed_and_changed_events():
    old = {"general": [{"answer": "a"}], "events": [ev("Concerto Exemplo", Estado="ativo"), ev("Festa Vazia"),
                                                      ev("Peca Exemplo", Estado="ativo")]}
    new = {"general": [{"answer": "b"}], "events": [ev("Concerto Exemplo", Estado="cancelado"), ev("Festa Vazia"),
                                                      ev("Teatro Novo", Estado="ativo")]}
    assert D.diff(old, new) == [{"kind": "alterado", "name": "Concerto Exemplo"},
                                {"kind": "novo", "name": "Teatro Novo"},
                                {"kind": "removido", "name": "Peca Exemplo"},
                                {"kind": "alterado", "name": "Perguntas gerais"}]


def test_diff_of_a_first_read_is_empty():
    assert D.diff({}, {"general": [], "events": [ev("Concerto Exemplo", Estado="ativo")]}) == []


def test_export_url_needs_no_login_parameters():
    assert D.export_url("abc") == "https://docs.google.com/document/d/abc/export?format=md"
