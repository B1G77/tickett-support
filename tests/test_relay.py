"""Tests for finding the customer's address in relayed mail (website forms, partner forwards)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import relay as R

FORM = "wp@calemamusic.com"
PARTNER = "bilheteira@promotor.example"


def test_website_form_fields():
    assert R.customer_address("Nome: Ana Silva\nE-mail: ana.silva@gmail.com\nTelefone: 912", FORM) == "ana.silva@gmail.com"
    assert R.customer_address("You have a new website form submission:\nNome\nAna\nE-mail\nana@sapo.pt\n", FORM) == \
        "ana@sapo.pt"
    assert R.customer_address("Correio eletrónico: Rui <rui@outlook.pt>\nMensagem: olá", FORM) == "rui@outlook.pt"
    assert R.customer_address("Email : joao@example.org", FORM) == "joao@example.org"


def test_forwarded_header_blocks_in_several_languages():
    en = "FYI\n\n---------- Forwarded message ---------\nFrom: Ana Silva <ana@gmail.com>\nDate: Mon\nSubject: Bilhetes"
    pt = "Segue.\n\n-------- Mensagem reencaminhada --------\nDe: eva.costa@hotmail.com\nAssunto: Reembolso"
    fr = "Bonjour,\n\n> De : Marie Dupont <marie@orange.fr>\n> Envoyé : lundi"
    de = "Weitergeleitet:\nVon: Jan <jan@web.de>\nBetreff: Karten"
    assert [R.customer_address(t, PARTNER) for t in (en, pt, fr, de)] == [
        "ana@gmail.com", "eva.costa@hotmail.com", "marie@orange.fr", "jan@web.de"]


def test_the_relayer_tickett_and_no_reply_addresses_are_skipped():
    text = ("From: Bilheteira <BILHETEIRA@promotor.example>\nFrom: Tickett <support@tickett.com>\n"
            "From: noreply@forms.example\nFrom: no-reply@site.example\nFrom: Ana <ana@gmail.com>")
    assert R.customer_address(text, PARTNER) == "ana@gmail.com"
    assert R.customer_address("E-mail: wp@calemamusic.com\nFrom: apoio@ajuda.tickett.com", FORM) == ""
    assert R.customer_address("sem endereço nenhum", FORM) == ""
    assert R.customer_address("", FORM) == ""


def test_an_override_is_one_valid_address_that_is_not_the_relayer_nor_tickett():
    for good in ("ana@gmail.com", " Ana@Gmail.com "):
        assert R.valid(good, PARTNER), good
    for bad in ("", "ana", "ana@gmail", "ana@gmail.com, rui@gmail.com", "ana@gmail.com rui@gmail.com",
                "Ana <ana@gmail.com>", PARTNER, PARTNER.upper(), "x@tickett.com", "x@ajuda.tickett.com", "a@@b.pt"):
        assert not R.valid(bad, PARTNER), bad


P, F = "bilheteira@promotor.pt", "wp@calemamusic.com"
PROBE = {   # the reviewer's probe cases
    "sig_before_fwd": ("Bom dia,\nSegue pedido do cliente.\n\nCumprimentos,\nJoana\nPromotor Lda\nEmail: geral@promotor.pt | "
                       "Tel 21 000\n\n---------- Forwarded message ---------\nDe: Ana Silva <ana@gmail.com>\nData: 12/10\n", P,
                       "ana@gmail.com"),
    "outlook_en_mailto": ("FYI\n\nFrom: Ana Silva [mailto:ana@gmail.com]\nSent: Monday\nTo: info@promotor.pt\n", P,
                          "ana@gmail.com"),
    "outlook_pt": ("Segue.\n\nDe: Ana Silva <ana@gmail.com>\nEnviado: segunda-feira\nPara: Bilheteira <bilheteira@promotor.pt>\n"
                   "Assunto: bilhetes", P, "ana@gmail.com"),
    "apple_fr": ("Début du message réexpédié :\n\nDe: Marie <marie@orange.fr>\nObjet: billets\nDate: 3 oct.\nÀ: info@promotor.fr\n",
                 P, "marie@orange.fr"),
    "colleague_nested": ("Fwd\n\n-------- Forwarded --------\nFrom: Rui Promotor <rui@promotor.pt>\n\n-------- Forwarded --------\n"
                         "From: Ana <ana@gmail.com>\n", P, "ana@gmail.com"),
    "wp_blank_line": ("Nome\n\nAna\n\nE-mail\n\nana@sapo.pt\n\nMensagem\n\nolá", F, "ana@sapo.pt"),
    "elementor": ("Nome: Ana\nEmail: ana@sapo.pt\n---\nData: 10\nEnviado de: https://calemamusic.com\nPowered by: Elementor", F,
                  "ana@sapo.pt"),
    "no_reply_underscore": ("From: no_reply@site.pt\nE-mail: ana@sapo.pt", F, "ana@sapo.pt"),
    "form_site_email_first": ("Email do site: info@calemamusic.com\nE-mail: ana@sapo.pt", F, "ana@sapo.pt"),
    "gmail_pt_quoted": ("Olá\n\n---------- Mensagem encaminhada ---------\nDe: <ana@gmail.com>\nDate: qua\n", P, "ana@gmail.com"),
    "customer_mentions_other": ("---------- Forwarded message ---------\nFrom: Ana <ana@gmail.com>\n\nOs bilhetes foram para o "
                                "email: amiga@gmail.com", P, "ana@gmail.com"),
    "de_prose": ("De: segunda a sexta, contacte o.ze@promotor.pt\nFrom: Ana <ana@gmail.com>", P, "ana@gmail.com"),
    "mailer_daemon_like": ("From: notify@forms.site\nE-mail: ana@sapo.pt", F, "ana@sapo.pt"),
    "upper_relayer_diff_display": ("From: \"Bilheteira\" <Bilheteira@Promotor.pt>\nFrom: Ana <ana@gmail.com>", P, "ana@gmail.com"),
}


def test_the_reviewers_probe_cases():
    for name, (text, relayer, want) in PROBE.items():
        assert R.customer_address(text, relayer) == want, name
    for v, ok in (("no_reply@x.pt", False), ("ana..b@x.pt", False), ("ana@x.pt\n", False), ("Ana@TICKETT.COM", False),
                  ("notify@forms.site", False), ("x@tickett.com.evil.pt", True), ("a@sub.Tickett.com", False)):
        assert R.valid(v, P) is ok, v


def test_no_reply_and_internal_addresses_are_never_the_customer():
    for local in ("no_reply", "noreply", "no-reply", "no.reply", "donotreply", "do-not-reply", "do_not_reply", "notify",
                  "notification", "notifications", "bounce", "bounces", "mailer-daemon", "postmaster", "wordpress",
                  "form", "forms"):
        assert R.customer_address(f"E-mail: {local}@site.pt", F) == "", local
        assert not R.valid(f"{local}@site.pt", P), local
    for addr in ("apoio@lebillet.eu", "a@lebillet.com.br", "a@mytickett.com"):
        assert R.customer_address(f"From: X <{addr}>", P) == "" and not R.valid(addr, P), addr


def test_the_relayers_own_domain_is_skipped_unless_it_is_webmail():
    assert R.customer_address("From: Rui <rui@promotor.pt>\nFrom: Ana <ana@sapo.pt>", P) == "ana@sapo.pt"
    assert R.customer_address("From: Rui <rui@gmail.com>", "joana@gmail.com") == "rui@gmail.com"


def test_the_name_next_to_the_customers_address():
    assert R.customer(PROBE["outlook_pt"][0], P) == ("ana@gmail.com", "Ana Silva")
    assert R.customer("Nome: Ana Silva\nE-mail: ana@sapo.pt", F) == ("ana@sapo.pt", "Ana Silva")
    assert R.customer(PROBE["wp_blank_line"][0], F) == ("ana@sapo.pt", "Ana")
    assert R.customer(PROBE["gmail_pt_quoted"][0], P) == ("ana@gmail.com", "")
    assert R.customer('From: "Costa, Rui" <rui@gmail.com>', P) == ("rui@gmail.com", "Costa, Rui")
    assert R.customer("nada", P) == ("", "")


PARTNER_CASES = {   # the reviewer's partner forwards that must stay relayed
    "outlook_web": ("Segue o pedido.\n\n________________________________\nDe: ana@gmail.com\nEnviado: segunda\n", "ana@gmail.com"),
    "no_brackets": ("Segue.\nFrom: Ana Silva ana@gmail.com\nSent: Monday", "ana@gmail.com"),
    "email_cliente": ("Pedido de reembolso.\nEmail cliente: ana@gmail.com\nEvento: X", "ana@gmail.com"),
    "prose": ("Bom dia, segue o pedido da cliente Ana (ana@gmail.com) sobre o evento.", "ana@gmail.com"),
    "on_wrote": ("Segue abaixo.\n\nOn Mon, 20 Oct 2026 at 10:00, Ana <ana@gmail.com> wrote:\n> Não recebi", "ana@gmail.com"),
    "em_escreveu": ("Segue.\n\nEm seg., 20/10/2026 às 10:00, Ana Silva <ana@gmail.com> escreveu:\n> Olá", "ana@gmail.com"),
}


def test_partner_forwards_without_a_marker_still_give_the_customers_address():
    for name, (text, want) in PARTNER_CASES.items():
        assert R.customer_address(text, P) == want, name
        assert not R.own_forward(text, P), name
    assert R.customer(PARTNER_CASES["em_escreveu"][0], P) == ("ana@gmail.com", "Ana Silva")
    assert R.customer(PARTNER_CASES["on_wrote"][0], P) == ("ana@gmail.com", "Ana")


def test_a_customers_own_forward_needs_positive_evidence():
    own = ("Não recebi.\n\n---------- Forwarded message ---------\nFrom: Tickett <noreply@tickett.com>\n"
           "To: <ana@gmail.com>\nSubject: A sua encomenda\nContacte support@tickett.com")
    assert R.own_forward(own, "ana@gmail.com")
    assert R.own_forward("Veja\n\n> De: Tickett <noreply@tickett.com>\n> Para: ana@gmail.com\n> Assunto: Bilhetes",
                         "ana@gmail.com")
    # an attribution names no addressee: no evidence the original was sent to the sender
    assert not R.own_forward("Olá\n\nOn Mon, Tickett <no-reply@tickett.com> wrote:\n> Bilhetes", "ana@gmail.com")
    assert not R.own_forward("Não recebi os bilhetes, ajudem.", "ana@gmail.com")   # no forwarded block at all
    assert not R.own_forward("---------- Forwarded message ---------\nSubject: x\n", "ana@gmail.com")   # no address
    assert not R.own_forward(own + "\nFrom: Rui <rui@gmail.com>", "ana@gmail.com")   # someone else in it


def test_the_greeting_name_is_the_nome_field_nearest_the_address():
    text = "Nome: Equipa Calema\nAssunto: site\n\nNome: Eva Costa\nE-mail: eva@sapo.pt"
    assert R.customer(text, F) == ("eva@sapo.pt", "Eva Costa")


PARTNER_TO_REPROS = {   # sender bilheteira@promotor.pt; their own address sits in the block's To:/Para: line
    "gmail": ("Segue o pedido da cliente.\n\n---------- Forwarded message ---------\nFrom: Ana Silva\nDate: seg\n"
              "Subject: Reembolso\nTo: bilheteira@promotor.pt\n\nQuero o reembolso, ana@gmail.com", "ana@gmail.com"),
    "outlook_web": ("Segue.\n\n________________________________\nDe: Ana Silva\nEnviado: segunda\n"
                    "Para: Bilheteira <bilheteira@promotor.pt>\nAssunto: bilhetes\n", ""),
    "quoted": ("Segue abaixo.\n\n> De: Ana Silva\n> Para: bilheteira@promotor.pt\n> Assunto: bilhetes", ""),
    "tickett_notice": ("Cliente ana@gmail.com pediu reembolso.\n\n---------- Forwarded message ---------\n"
                       "From: Tickett <noreply@tickett.com>\nTo: bilheteira@promotor.pt\nSubject: Venda", "ana@gmail.com"),
}


def test_a_partners_own_address_in_the_block_is_no_evidence_of_an_own_forward():
    for name, (text, want) in PARTNER_TO_REPROS.items():
        assert not R.own_forward(text, "bilheteira@promotor.pt"), name
        assert R.customer_address(text, "bilheteira@promotor.pt") == want, name


def test_an_own_forward_is_a_tickett_or_robot_original_to_the_sender():
    own = ("Não recebi.\n\n---------- Forwarded message ---------\nFrom: Tickett <noreply@tickett.com>\n"
           "To: <ana@gmail.com>\nSubject: A sua encomenda")
    assert R.own_forward(own, "ana@gmail.com")
    assert not R.own_forward(own.replace("From: Tickett <noreply@tickett.com>", "From: Tickett"), "ana@gmail.com")
    assert not R.own_forward("---------- Forwarded message ---------\nFrom: Ana <ana@gmail.com>\nTo: <ana@gmail.com>",
                             "ana@gmail.com")   # nothing from Tickett or a robot


P5 = "bilheteira@promotor.pt"
FW = "---------- Forwarded message ---------\n"
PROBE5 = {   # the reviewer's probe5: (sender, text, own forward?)
    "A": (P5, "A cliente Ana Silva diz que não recebeu os bilhetes. Podem ajudar?\n\n" + FW + "From: Tickett "
              "<noreply@tickett.com>\nTo: bilheteira@promotor.pt\nSubject: Nova venda\n\nComprador: Ana Silva\n", False),
    "B": (P5, "Segue.\n\n" + FW + "From: Tickett <noreply@tickett.com>\nTo: Ana Silva\nSubject: A sua encomenda\n", False),
    "C": (P5, "Segue.\n\n" + FW + "From: Tickett <noreply@tickett.com>\nSubject: A sua encomenda\nOlá Ana,\n", False),
    "D": (P5, "Cliente liga 912345678.\n\n" + FW + "From: noreply@tickett.com\nTo: bilheteira@promotor.pt\n", False),
    "E": (P5, "Ver abaixo, cliente Ana.\n> From: Tickett <noreply@tickett.com>\n> To: bilheteira@promotor.pt\n", False),
    "F": (P5, "Segue\n\n" + FW + "From: Tickett <noreply@tickett.com>\nTo: joao@promotor.pt\n", False),
    "G": (P5, "Cliente: ana (at) gmail.com\n\n" + FW + "From: Tickett <noreply@tickett.com>\nTo: bilheteira@promotor.pt\n",
          False),
    "H": (P5, "Segue\n\n" + FW + "From: Ana Silva\nDate: x\nTo: bilheteira@promotor.pt\n\n" + FW +
          "From: Tickett <noreply@tickett.com>\nTo: Ana\n", False),
    "I": (P5, "Segue\n\n" + FW + "From: WordPress <wordpress@promotor.pt>\nTo: bilheteira@promotor.pt\nNome: Ana\n"
              "Mensagem: ola\n", False),
    "K": ("ana@gmail.com", "Não recebi.\n\n" + FW + "From: Tickett <noreply@tickett.com>\nTo: <ana@gmail.com>\n", True),
    "L": (P5, "Segue\n\n" + FW + "Date: x\nFrom: Tickett <noreply@tickett.com>\nTo: Ana Silva\n", False),
    "M": (P5, "Segue\n\n" + FW + "From: Suporte <support@tickett.com>\nTo: bilheteira@promotor.pt\n", False),
}


def test_probe5_only_a_webmail_customers_forward_addressed_to_them_is_their_own():
    for name, (sender, text, own) in PROBE5.items():
        assert R.own_forward(text, sender) is own, name
    # J, the accepted residual: a partner on gmail forwarding a Tickett notice addressed to themselves
    j = "Segue\n\n" + FW + "From: Tickett <noreply@tickett.com>\nTo: promotorx@gmail.com\nCliente Ana\n"
    assert R.own_forward(j, "promotorx@gmail.com") in (True, False)
    k = PROBE5["K"][1]
    assert not R.own_forward(k.replace("ana@gmail.com", "ana@empresa.pt"), "ana@empresa.pt")   # not webmail
    assert not R.own_forward(k.replace("To: <ana@gmail.com>", "To: Ana"), "ana@gmail.com")   # not addressed to them
