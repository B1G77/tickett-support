"""Tests for the router's decision logic and state (no IMAP, no API)."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import drafter
import extract as E
import router as RT


class StubRedactor:
    def __init__(self, leaks=()):
        self.leaks = list(leaks)

    def redact(self, subject, text, names=(), addrs=()):
        return subject, text, {}, self.leaks


class StubClassifier:
    def __init__(self, **out):
        self.out = {"folder": "F2", "urgent": False, "confidence": "high", "reason": "stub", **out}
        self.calls = 0

    def classify(self, case):
        self.calls += 1
        return dict(self.out)


def msg(subject, body, sender="ana@gmail.com", headers=""):
    raw = (f"Subject: {subject}\r\nFrom: Ana <{sender}>\r\nContent-Type: text/plain; charset=utf-8\r\nMessage-ID: <{abs(hash(subject + body))}@x>\r\n"
           f"{headers}\r\n{body}").encode()
    return E.record_from_bytes(raw, "imap:1")


def state():
    return RT.State(Path(tempfile.mkdtemp()) / "router.db")


def test_phishing_goes_to_nao_cliente_without_calling_claude():
    c = StubClassifier()
    d = RT.decide(msg("Copyright Infringement Notice", "act now", "x@ss.cnu.edu.vn"), state(), StubRedactor(), c)
    assert (d.folder, d.phishing, c.calls) == ("F8", True, 0)


def test_weak_scam_signal_is_filed_but_never_purged():
    c = StubClassifier()
    d = RT.decide(msg("Pagamento", "Segue o transfer record do pagamento."), state(), StubRedactor(), c)
    assert (d.folder, d.phishing, c.calls) == ("F8", False, 0)


def test_scam_looking_reply_in_a_filed_conversation_stays_there():
    st = state()
    st.record("<orig@x>", [], RT.Decision("F1", False, "claude", "high", "refund"), moved=True)
    d = RT.decide(msg("Re: Reembolso", "Segue o transfer record do pagamento.",
                      headers="In-Reply-To: <orig@x>\r\n"), st, StubRedactor(), StubClassifier())
    assert (d.folder, d.source, d.phishing) == ("F1", "thread", False)


def test_follow_up_inherits_the_thread_folder_and_can_be_urgent():
    st, c = state(), StubClassifier()
    st.record("<orig@x>", [], RT.Decision("F1", False, "claude", "high", "refund"), moved=True)
    d = RT.decide(msg("Re: Reembolso", "Vou recorrer ao livro de reclamações.",
                      headers="In-Reply-To: <orig@x>\r\n"), st, StubRedactor(), c)
    assert (d.folder, d.source, d.urgent, c.calls) == ("F1", "thread", True, 0)


def test_low_confidence_and_leaks_go_to_a_rever():
    d = RT.decide(msg("Bilhetes", "Não recebi os bilhetes."), state(), StubRedactor(),
                  StubClassifier(folder="F2", confidence="low"))
    assert d.folder == "F9" and d.suggested == "F2"
    c = StubClassifier()
    d = RT.decide(msg("Bilhetes", "Não recebi os bilhetes."), state(), StubRedactor(["url"]), c)
    assert d.folder == "F9" and c.calls == 0


def test_confident_answer_is_used():
    d = RT.decide(msg("Bilhetes", "Paguei e não recebi os bilhetes."), state(), StubRedactor(),
                  StubClassifier(folder="F2", urgent=True))
    assert (d.folder, d.urgent, d.source) == ("F2", True, "claude")


def test_a_scam_reply_never_moves_a_conversation_to_nao_cliente():
    st = state()
    st.record("<scam@x>", ["<real@x>"], RT.Decision("F8", False, "rule", "high", "scam"), moved=True)
    assert st.thread_folder(["<real@x>"]) is None


def test_failures_are_counted():
    st = state()
    assert st.fail("<m@x>") == 1 and st.fail("<m@x>") == 2
    assert st.get("<m@x>")["moved_at"] is None


def test_no_request_text_is_strict():
    form = {"subject": "Nova Entrada de Formulário [ORDER_REF] para Contato",
            "text": "You have a new website form submission:\nNome\n[NAME]\nE-mail\n[EMAIL]\nTelefone\n[PHONE]"}
    assert RT.no_request_text(form)
    assert not RT.no_request_text({"subject": "", "text": "Não recebi o bilhete"})


def test_api_key_is_read_from_the_secrets_file(tmp_path):
    import os
    f = tmp_path / "claude.env"
    f.write_text('export TAVILY_API_KEY=x\nANTHROPIC_API_KEY="sk-test-123"\n')
    old = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        RT.load_secret_env(f)
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-test-123"
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)
        if old is not None:
            os.environ["ANTHROPIC_API_KEY"] = old


def test_build_drafter_returns_none_when_drafts_are_off_or_there_is_no_classifier():
    assert RT.build_drafter({"drafts": {"enabled": False}}, StubClassifier(), lambda *a: None) is None
    assert RT.build_drafter({"drafts": {"enabled": True}}, None, lambda *a: None) is None


def test_a_drafter_setup_failure_is_logged_and_filing_carries_on_without_it():
    class Boom:
        def __init__(self):
            raise OSError("no rulebook")
    orig, drafter.Drafter, logged = drafter.Drafter, Boom, []
    try:
        d = RT.build_drafter({"drafts": {"enabled": True}}, StubClassifier(), lambda mid, msg: logged.append(msg))
    finally:
        drafter.Drafter = orig
    assert d is None and logged == ["drafter unavailable (OSError) - filing without drafts"]


RULES = __import__("json").load(open(Path(__file__).resolve().parent.parent / "config" / "router.json"))["rules"]


def test_rules_only_mode_files_only_high_precision_cases():
    st = state()
    refund = RT.decide(msg("Reembolso", "O concerto do Diogo Nogueira foi cancelado, quero o reembolso."),
                       st, None, None, RULES)
    merch = RT.decide(msg("Commande", "Où est mon maillot Yanns ?"), st, None, None, RULES)
    other = RT.decide(msg("Dúvida", "A que horas abrem as portas?"), st, None, None, RULES)
    scam = RT.decide(msg("Copyright Infringement Notice", "x", "x@ss.cnu.edu.vn"), st, None, None, RULES)
    assert (refund.folder, refund.source) == ("F1", "rule")
    assert merch.folder == "F6" and other is None and scam.folder == "F8"


def test_data_protection_requests_are_urgent():
    assert RT.urgent_hint("Peço que apaguem os meus dados, ao abrigo do RGPD")
    assert RT.urgent_hint("Please delete my account and data")


def test_formal_complaints_are_urgent():
    assert RT.urgent_hint("Reclamação formal por alteração unilateral de local do evento")
    assert RT.urgent_hint("Apresento queixa formal") and RT.urgent_hint("This is a formal complaint")
    assert not RT.urgent_hint("Tenho uma reclamação sobre o horário")


def test_unmatched_inbox_mail_is_handled_once():
    st = state()
    assert st.first_sight("<m@x>") and not st.first_sight("<m@x>")


def test_loop_survives_a_failed_run_and_keeps_polling():
    calls, naps = [], []
    def run(cfg):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("no network after wake")
        return 0
    RT.loop({"poll_seconds": 7}, run=run, sleep=naps.append, cycles=3)
    assert len(calls) == 3 and naps == [7, 7, 7]


def test_urgent_is_legal_only():
    assert not RT.urgent_hint("Ainda não recebi o estorno da compra")
    assert not RT.urgent_hint("O evento é amanhã e não tenho o bilhete!")
    assert RT.urgent_hint("Vou pedir o chargeback ao banco")


def test_quoted_history_does_not_make_a_follow_up_urgent():
    st = state()
    st.record("<orig@x>", [], RT.Decision("F1", False, "claude", "high", "refund"), moved=True)
    body = "Então e o meu reembolso, quando chega?\n\nOn Mon, 1 Sep 2026, Ana wrote:\n> Vou recorrer ao livro de reclamações."
    d = RT.decide(msg("Re: Reembolso", body, headers="In-Reply-To: <orig@x>\r\n"), st, StubRedactor(), StubClassifier())
    assert d.folder == "F1" and not d.urgent


def test_legal_looking_nao_cliente_goes_to_a_rever_flagged():
    d = RT.decide(msg("Mise en demeure", "Mise en demeure et notification pré-contentieuse."), state(),
                  StubRedactor(), StubClassifier(folder="F8", urgent=True))
    assert (d.folder, d.urgent, d.suggested) == ("F9", True, "F8")


def test_thank_you_notes_count_as_answered():
    assert RT.closing("Muito obrigada, já recebi os bilhetes!")
    assert RT.closing("Merci beaucoup pour votre aide.")
    assert not RT.closing("Obrigado. Mas ainda não recebi o reembolso.")
    assert not RT.closing("Thank you, does the concert start at 8pm?")
    assert not RT.closing("Fico a aguardar resposta. Obrigado")
    assert not RT.closing("Segue o comprovativo de pagamento. Obrigada.")
    assert not RT.closing("Bonjour je souhaiterais savoir où en est ma commande. Merci")
    assert not RT.closing("Boa tarde, o e-mail é ana@gmail.com. Obrigada")
    assert not RT.closing("Boa tarde O concerto foi anulado Obrigado")
    assert not RT.closing("Olá, comprei dois bilhetes para sábado e queria saber se posso trocar para domingo, obrigado")


def test_thank_you_in_a_filed_conversation_goes_to_answered_and_keeps_the_thread():
    st, c = state(), StubClassifier()
    st.record("<orig@x>", [], RT.Decision("F1", False, "claude", "high", "refund"), moved=True)
    d = RT.decide(msg("Re: Reembolso", "Obrigada, já recebi o reembolso.", headers="In-Reply-To: <orig@x>\r\n"),
                  st, StubRedactor(), c)
    assert (d.folder, d.source, c.calls) == (RT.ANSWERED, "rule", 0)
    st.record("<thanks@x>", ["<orig@x>"], d, moved=True)
    assert st.thread_folder(["<thanks@x>", "<orig@x>"]) == "F1"


def test_server_settings_override_the_shared_config(tmp_path):
    import os
    base = tmp_path / "router.json"
    base.write_text('{"state_dir": "~/x", "imap": {"host": "h", "user": "a@lebillet.eu"}, "poll_seconds": 120}')
    local = tmp_path / "local.json"
    local.write_text('{"state_dir": "/var/lib/tickett-router", "imap": {"user": "support@tickett.com"}}')
    os.environ["ROUTER_LOCAL_CONFIG"] = str(local)
    try:
        cfg = RT.load_config(base)
    finally:
        del os.environ["ROUTER_LOCAL_CONFIG"]
    assert cfg["imap"] == {"host": "h", "user": "support@tickett.com"} and cfg["poll_seconds"] == 120
    assert str(cfg["state_dir"]) == "/var/lib/tickett-router"


def test_the_server_password_comes_from_the_environment():
    import os
    os.environ["IMAP_PASSWORD"] = "from-systemd"
    try:
        assert RT.keychain_password("svc", "acct") == "from-systemd"
    finally:
        del os.environ["IMAP_PASSWORD"]


NAMES = {"F1": "1 Cancelamentos e Reembolsos", "F8": "8 Não-cliente", "A": "10 Respondidos", "P": "0 Por Responder"}


def test_a_handoff_goes_to_the_person_folder_and_keeps_the_category_in_the_reason():
    d = RT.Decision("F1", False, "claude", "high", "refund for a cancelled event")
    out = {"handoff": True, "handoff_reason": "tickett_refund"}
    p = RT.to_person(d, out, NAMES)
    assert p.folder == "P" and p.reason.startswith("1 Cancelamentos e Reembolsos -> tickett_refund")
    assert p.suggested == "F1"


def test_legal_mail_goes_to_a_person_but_drafts_and_scams_stay_put():
    legal = RT.Decision("F1", True, "claude", "high", "lawyer letter")
    assert RT.to_person(legal, None, NAMES).folder == "P"
    reply = RT.Decision("F1", False, "claude", "high", "x")
    assert RT.to_person(reply, {"handoff": False}, NAMES).folder == "F1"
    assert RT.to_person(RT.Decision("F8", True, "rule", "high", "scam"), None, NAMES).folder == "F8"
    no_folder = {k: v for k, v in NAMES.items() if k != "P"}
    assert RT.to_person(legal, None, no_folder).folder == "F1"   # not configured: behaves as before


def test_the_handoff_folder_is_created_with_the_others():
    names = RT.folder_names({"folders": {"F1": "1 X"}, "answered_folder": "10 R", "handoff_folder": "0 Por Responder"})
    assert names == {"F1": "1 X", "A": "10 R", "P": "0 Por Responder"}
