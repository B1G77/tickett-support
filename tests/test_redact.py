"""Tests for stage 4 redaction. Synthetic data only - never real emails."""
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import redact as R


def names(*tokens):
    alts = "|".join(R.accent_rx(t) for t in sorted(tokens, key=len, reverse=True))
    return re.compile(rf"(?<![\w\[])(?:{alts})(?![\w\]])", re.I)


def run(text, tokens=(), keep=()):
    keep_rx = re.compile(r"\b(?:" + "|".join(map(re.escape, keep)) + r")\b") if keep else None
    return R.redact(text, names(*tokens) if tokens else None, keep_rx, Counter())


def test_checksums():
    assert R.iban_ok("PT50 0002 0123 1234 5678 9015 4")
    assert not R.iban_ok("PT50 0002 0123 1234 5678 9015 5")
    assert R.luhn_ok("4111 1111 1111 1111") and not R.luhn_ok("4111 1111 1111 1112")


def test_structured_identifiers_are_replaced():
    out = run("Email joao.silva@gmail.com tel +351 912 345 678, NIF: 123456789, "
              "IBAN PT50 0002 0123 1234 5678 9015 4, cartao 4111 1111 1111 1111")
    for leaked in ("joao.silva", "912", "123456789", "PT50", "4111"):
        assert leaked not in out, leaked
    for tag in ("[EMAIL]", "[PHONE]", "[NIF]", "[IBAN]", "[CARD]"):
        assert tag in out, tag


def test_order_refs_keep_their_meaning():
    out = run("Pedido #900001 e pedido 900002")
    assert "900001" not in out and "900002" not in out
    assert out.count("[ORDER_REF]") == 2 and out.lower().startswith("pedido")


def test_order_link_is_a_credential():
    out = run("o meu bilhete https://lebillet.eu/order/aGVsbG8td29ybGQtdG9rZW4= nao abre")
    assert out == "o meu bilhete [ORDER_LINK] nao abre"


def test_unknown_hosts_are_not_revealed():
    assert run("ver https://joaosilva-fotos.com/x") == "ver [LINK]"
    assert run("ver https://www.dropbox.com/s/abc") == "ver [LINK:dropbox.com]"


def test_signal_survives():
    text = "Comprei 2 bilhetes por 45,00 € para 20.03.2026 as 21h30, paguei duas vezes."
    assert run(text) == text


def test_greeting_and_signoff_names():
    out = run("Olá João,\nquero o reembolso.\nCumprimentos,\nMaria da Silva")
    assert "João" not in out and "Maria" not in out and "Silva" not in out
    assert "quero o reembolso" in out
    assert run("Olá, Bom dia") == "Olá, Bom dia"


def test_header_names_redacted_accent_insensitive():
    out = run("A Joao comprou e a JOÃO pagou", tokens=("João",))
    assert "Joao" not in out and "JOÃO" not in out


def test_artist_protected_but_customer_namesake_is_not():
    out = run("Concerto Diogo Nogueira. Obrigado, Diogo", tokens=("Diogo",),
              keep=("Diogo Nogueira",))
    assert "Diogo Nogueira" in out and out.endswith("[NAME]")


def test_contact_form_fields():
    out = run("Nome\n\nBeatriz Quintela\n\nE-mail\n\nb@x.com\n\nTelefone\n\n912345678")
    assert "Beatriz" not in out and "b@x.com" not in out and "912345678" not in out


def test_leak_scan_fails_closed():
    assert "at_sign" in R.leak_scan("contacto joao arroba@", set(), None)
    assert "long_number" in R.leak_scan("ref 123456789012", set(), None)
    assert "header_name" in R.leak_scan("obrigado Joao", {"João"}, None)
    assert R.leak_scan("[NAME] pediu [ORDER_REF]", {"João"}, None) == []


# --- regressions found by the self-audit of redact-1 ----------------------

def test_greeting_does_not_eat_ordinary_words():
    text = "Bom dia, gostaria de saber se comprei bilhetes"
    assert run(text) == text
    assert run("Obrigado, aguardo resposta") == "Obrigado, aguardo resposta"


def test_organisation_display_names_are_not_people():
    assert not R.person_like("Câmara Municipal do Crato")
    assert not R.person_like("Festival Pé na Terra")
    assert R.person_like("Maria da Silva")


def test_brazilian_phone_tracking_and_arn():
    out = run("ligue (11) 9 1234-5678, encomenda DW123456789PT, ARN: 12345678901234567890123Acquirer")
    assert "1234-5678" not in out and "DW123456789PT" not in out
    assert not re.search(r"\d{7,}", out)
    assert "[PHONE]" in out and "[TRACKING]" in out


def test_social_handles():
    assert run("IG @joana.silva_ ok") == "IG [HANDLE] ok"


def test_at_as_word_is_not_a_leak():
    assert R.leak_scan("VANESSA DA MATA @ Cabaret Sauvage", set(), None) == []
    assert R.leak_scan("fala com @joana", set(), None) == ["at_sign"]


def test_known_people_and_first_name_rule():
    people = R.compile_people({"Kamila Rocha"})
    given = R.compile_given({"pedro"})
    out = R.redact("Falei com Kamila Rocha e com Pedro Albuquerque Neto.", None, None,
                   Counter(), people, given)
    assert "Kamila" not in out and "Albuquerque" not in out
    assert out == "Falei com [NAME] e com [NAME]."
    # a first name alone, or followed by lowercase, is left alone
    assert R.redact("o pedro chegou", None, None, Counter(), None, given) == "o pedro chegou"


# --- regressions found by reading redact-2 output by hand -----------------

def test_greeting_never_crosses_a_line_break():
    assert run("Olá,\n\nGostaria de saber") == "Olá,\n\nGostaria de saber"
    assert run("Bonjour,\n\nNous souhaitons") == "Bonjour,\n\nNous souhaitons"
    assert run("Bom dia Gostaria de saber") == "Bom dia Gostaria de saber"
    assert run("Olá João,\nok") == "Olá [NAME],\nok"


def test_address_display_names_give_no_tokens():
    assert R.name_tokens("maria@gmail.com") == set()
    assert "Com" not in R.name_tokens("Com Amor Silva")


def test_bracketed_and_trailing_surnames():
    assert "Hecquet" not in run("au nom de [Hecquet Pamela]")
    assert run("Maria de Souza pagou", tokens=("Maria",)) == "[NAME] pagou"


def test_staff_names_from_quoted_attributions():
    bodies = ["----- Mensagem de Fabia Antunes <atendimento@lebillet.eu> ---",
              "Em 3 Mai, Suporte LeBillet <suporte@lebillet.com.br> escreveu:",
              "From: Joana <joana@gmail.com>"]
    found = R.staff_from_attributions(bodies)
    assert "Fabia Antunes" in found
    assert not any("Joana" in n for n in found)


# --- regressions found by the final audit of redact-3 ---------------------

def test_keep_list_drops_staff_greetings_and_never_spans_lines():
    cases = [{"case_id": f"c{i}", "customer_addresses": [f"u{i}@x.pt"], "subject": "",
              "customer_text": "Olá Fabia, o concerto Diogo Nogueira\nBilhetes\nEstão"}
             for i in range(6)]
    keep = R.build_keep_list(cases, set(), {"Fabia"})
    assert "Diogo Nogueira" in keep
    assert not any("Fabia" in k or "\n" in k for k in keep)


def test_all_caps_first_name_before_redacted_surname():
    out = R.redact("Olá MANUELA SOUSA", R.compile_tokens({"Sousa"}), None, Counter(),
                   None, R.compile_given({"manuela"}))
    assert "MANUELA" not in out and "SOUSA" not in out


# --- regressions found in the before/after review of redact-4 -------------

def test_order_number_phrasings_keep_their_meaning():
    assert run("Order number: 900003") == "Order number [ORDER_REF]"
    assert run("Numéro de commande : 123456") == "Numéro de commande [ORDER_REF]"


def test_signature_name_does_not_swallow_next_line():
    out = R.redact("Thanks,\n\nLevi Tourinho\nCel: +31 (06) 2123-1679", None, None,
                   Counter(), None, R.compile_given({"levi"}))
    assert out == "Thanks,\n\n[NAME]\nCel: [PHONE]"


def test_all_caps_name_parts_beside_a_redacted_name():
    out = run("Cumprimentos SALDANHA Maria", tokens=("Maria",))
    assert "SALDANHA" not in out and out.endswith("[NAME]")


# --- regressions found reading discovery batches A-C (redact-6) -----------

def test_inline_name_fields_but_not_event_name_fields():
    out = run("Nome:Lucianne Barreto da Cunha\nE-mail: x@y.pt\nNome do Evento: O KANNALHA")
    assert "Lucianne" not in out and "Barreto" not in out
    assert "[EMAIL]" in out and "Nome do Evento: O KANNALHA" in out
    assert "Sameiro" not in run("* Nome completo: Maria do Sameiro Albergaria Pimentel de Sousa")


def test_more_self_introductions_and_signoffs():
    assert "Hellen" not in run("Bom dia, Daqui fala Hellen Monteiro, do clube.")
    assert "Rudolf" not in run("Recebi as entradas.\nAtt. Rudolf")
    assert "Bernardete" not in run("Os bilhetes foram 4 em meu nome, Bernardete Calado e estão pagos.")


def test_titles_with_lowercase_surnames_and_au_nom_de():
    assert "colombelle" not in run("Merci à vous Cordialement Mme colombelle")
    out = run("ma commande au nom de renard Mélanie commande faite le 6 juillet")
    assert "renard" not in out and "Mélanie" not in out
    assert run("Madame, Monsieur,") == "Madame, Monsieur,"


def test_relative_and_later_mentions_in_the_same_case():
    found, c = [], Counter()
    t = R.redact("Escrevo pelo meu irmão Marco Silva. O Marco não recebeu.", None, None, c, None, None, found)
    t = R.propagate_names(t, found, None, c)
    assert "Marco" not in t and "Silva" not in t and "não recebeu" in t


def test_propagation_is_case_sensitive_for_words_that_are_also_names():
    found, c = [], Counter()
    t = R.redact("Cumprimentos,\nRosa Lima\n\nquero o bilhete rosa", None, None, c, None, None, found)
    t = R.propagate_names(t, found, None, c)
    assert "Rosa Lima" not in t and "bilhete rosa" in t


def test_non_breaking_spaces_do_not_hide_surnames():
    assert run("Maria Oliveira pagou", tokens=("Maria",)) == "[NAME] pagou"
    assert run("Name: [NAME]") == "Name: [NAME]"


def test_redact_6_1_residuals():
    assert "Abbel" not in run("Podéis darme mais indicações?\n\nMuito agradecido\n\nAbbel")
    assert "Hellen" not in run("Boa noite,\n\nDaqui fala a Hellen, da organização do evento")
    out = run("não me respondem Maria Dupont\nChristophe 5 rue X", tokens=("Dupont",))
    assert "Christophe" not in out
    # but an ordinary sentence starting the next line is left alone
    assert run("Cumprimentos Ana Dupont\nObrigado pela ajuda", tokens=("Dupont",)).endswith("Obrigado pela ajuda")


def test_the_redactor_works_from_a_saved_lexicon_or_from_nothing(tmp_path):
    import redactor as RD
    msgs = [{"from_domain": "gmail.com", "from_name": "Rita Exemplo", "bucket": "customer", "body_text": ""},
            {"from_domain": "gmail.com", "from_name": "Rita Outra", "bucket": "customer", "body_text": ""}]
    full = RD.Redactor(msgs, [])
    lex = tmp_path / "redactor.json"
    RD.save_lexicon(full, lex)
    assert oct(lex.stat().st_mode & 0o777) == "0o600"
    missing = tmp_path / "none.jsonl"
    again = RD.build(lex, missing, missing)
    assert again.lexicon() == full.lexicon()
    text = "Falei com a Rita Exemplo ontem."
    assert full.redact("", text)[1] == again.redact("", text)[1]
    bare = RD.build(tmp_path / "absent.json", missing, missing)
    assert bare.people == [] and "x@example.com" not in bare.redact("", "escreva para x@example.com")[1]
