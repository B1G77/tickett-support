"""Tests for stage 2 triage. Synthetic messages only."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import triage as T


def rec(subject="", body="", domain="gmail.com", **kw):
    return {"subject": subject, "body_text": body, "from_domain": domain,
            "from_email": f"x@{domain}", "to": ["atendimento@lebillet.eu"], **kw}


def test_scam_content_is_phishing_from_any_domain():
    assert T.classify(rec("Copyright Infringement Notice", "", "ss.cnu.edu.vn"))[0] == "phishing"
    assert T.classify(rec("META VERIFIED - Parabéns", "", "kstvet.ac.ke"))[0] == "phishing"
    assert T.classify(rec("Transfer Record Confirmation", "", "outlook.com"))[0] == "phishing"


def test_genuine_customers_with_risky_words_stay_customers():
    sig = "Obrigado\nJoao\nCopyright © 2026 Empresa Lda. All rights reserved."
    assert T.classify(rec("Reembolso", sig))[0] == "customer"
    assert T.classify(rec("Payment", "I paid by wire transfer yesterday, order 123"))[0] == "customer"
    assert T.classify(rec("Site", "your page is not loading on my phone"))[0] == "customer"


def test_partner_forward_is_rescued():
    r = rec("FW: Bilhetes", "---------- Forwarded message ---------\nnao recebi", "cm-crato.pt")
    assert T.classify(r) == ("customer", "forwarded to support by a partner")


def test_staff_reply_to_customer():
    r = rec("Re: Pedido", "ok", "lebillet.com.br", to=["cliente@gmail.com"], from_email="suporte@lebillet.com.br")
    assert T.classify(r)[0] == "staff_reply"


def test_fake_einvoice_family_but_not_real_invoice_requests():
    scam = rec("Uw huidige factuur is bevestigd",
               "Geachte klant, De invoice for order nr. VF-1, gekoppeld aan atendimento@lebillet.eu, is verwerkt.",
               "yonpoint.com")
    assert T.classify(scam)[0] == "phishing"
    real = rec("Factuur", "Goedendag, kunt u mij een factuur sturen voor bestelling 123?", "live.nl")
    assert T.classify(real)[0] == "customer"


def test_only_strong_scam_signals_are_safe_to_purge():
    reason = lambda r: T.classify(r)[1]
    assert T.safe_to_purge(reason(rec("Copyright Infringement Notice", "", "ss.cnu.edu.vn")))
    assert T.safe_to_purge(reason(rec("ΜеtаΜаѕk Security Advisory", "", "x.site")))
    assert not T.safe_to_purge(reason(rec("Pagamento", "Segue o transfer record do pagamento")))
    assert not T.safe_to_purge(reason(rec("Bilhetes", "Não recebi os bilhetes", "empresa.pt", dmarc_fail=True)))


def test_homoglyph_and_new_scam_families():
    assert T.classify(rec("ΜеtаΜаѕk Security Advisory", "", "x.site"))[0] == "phishing"
    assert T.classify(rec("Get a head start: OpenAI Advertising Beta now open"))[0] == "phishing"
    assert T.classify(rec("[Limited Time Offer] Premium Timepieces - From $250"))[0] == "phishing"
    assert T.classify(rec("Билеты Calema", "Здравствуйте, где мои билеты?"))[0] == "customer"


def test_newsletter_signups_from_the_site_are_not_customers():
    rec = {"from_domain": "calemamusic.com", "subject": "Nova Entrada de Formulário #1639 para Newsletter - CALEMA",
           "body_text": "You have a new website form submission:\nEmail\nx@y.pt"}
    assert T.classify(rec)[0] == "vendor"
    rec["subject"] = "Nova Entrada de Formulário #1640 para Contato"
    assert T.classify(rec)[0] == "customer"
