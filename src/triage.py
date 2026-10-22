#!/usr/bin/env python3
"""Stage 2 - split extracted messages into customer mail and everything else.

Nothing is deleted. Every message gets a `bucket` and a human-readable
`reason`, so the excluded pile can be reviewed before anyone removes it.

The safe error here is keeping a non-customer message, not dropping a
customer. When no rule matches, the message is treated as customer mail.

Domain lists are deliberately explicit rather than clever: they are meant
to be read and corrected by a person. The server's own SpamAssassin verdict
is NOT used - it flagged 0 of 4,770 messages, including obvious phishing.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
IN = DATA / "01_extracted.jsonl"
OUT = DATA / "02_triaged.jsonl"

# Brand-impersonation and scam senders. Verified by reading their subjects.
PHISHING_DOMAINS = {
    "facebooknoreply-notification.com", "notifyfacebook-noreply.com",
    "s.ksmg.org", "appsheet.com", "st.sellpia247.com",
}
# Untargeted commercial mail.
SPAM_DOMAINS = {"zirenma.tech", "evepla.com", "capcut.org.uk"}
# Genuine automated notifications from platforms LeBillet actually uses.
SOCIAL_DOMAINS = {
    "business.facebook.com", "support.facebook.com", "facebookmail.com",
    "mail.instagram.com", "email.apple.com",
}
VENDOR_DOMAINS = {"mkt.ctt.pt", "incm.pt"}
INTERNAL_DOMAINS = {"lebillet.eu", "lebillet.com.br", "lebillet.pt", "tickett.com", "mytickett.com"}
# Promoter/partner sites whose contact forms deliver customer enquiries here.
FORM_DOMAINS = {"calemamusic.com"}
NEWSLETTER_FORM = re.compile(r"formul[áa]rio.*\bnewsletter\b", re.I)
# Consumer webmail. Used only to stop infrastructure rules firing on real people.
FREEMAIL = {
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.fr", "hotmail.es",
    "hotmail.pt", "hotmail.de", "hotmail.co.uk", "outlook.com", "outlook.pt",
    "outlook.es", "outlook.fr", "live.com", "live.nl", "live.fr", "live.com.pt",
    "msn.com", "icloud.com", "me.com", "yahoo.com", "yahoo.com.br", "yahoo.co.uk",
    "yahoo.fr", "yahoo.es", "yahoo.de", "ymail.com", "aol.com", "sapo.pt",
    "iol.pt", "clix.pt", "uol.com.br", "terra.com.br", "bol.com.br",
    "bluewin.ch", "free.fr", "orange.fr", "laposte.net", "libero.it",
    "gmx.de", "web.de", "mail.com", "protonmail.com", "proton.me",
}

# Meta "verification badge" and fake-policy scams, seen in pt/es/fr/de/en.
PHISHING_SUBJECT = re.compile(
    r"(insignia|selo|badge|abzeichen|verifica[cç][aã]o|v[ée]rification)\s*"
    r"(azul|blue|blaue|de verifica)|"
    r"(suspend(i|e)[dmo]|suspens[aã]o).{0,25}(p[aá]gina|page|conta|account)|"
    r"violation of intellectual property|policy violation|"
    r"content compliance issue|card billing violation|"
    r"meta business (security|support)|"
    r"advertising campaign activity",
    re.IGNORECASE,
)
# Scam content. The self-audit found ~100 of these in the customer bucket,
# sent from hundreds of throwaway domains, so no domain list can catch them.
# Patterns are deliberately narrow: "wire transfer" or "copyright" alone can
# appear in a genuine customer's email or signature.
PHISHING_BODY = re.compile(
    r"copyright (infringement|violation|notice|polic|issue|claim|report)|"
    r"intellectual property (rights|violation|infringement|owned)|"
    r"propriedade intelectual|direitos autorais|policy violation|violation notice|"
    r"community standards|padr[õo]es da comunidade|restringimos a sua p[áa]gina|"
    r"your (facebook |fb )?page (has been|will be|is|may be) (restricted|suspended|"
    r"disabled|deleted|removed|unpublished|scheduled|under review|at risk|eligible|flagged)|"
    r"sua p[áa]gina (foi|ser[áa]|est[áa]|pode ser|agora [ée])\b|votre page (a [ée]t[ée]|sera)|"
    r"meta (business|verified)|blue (badge|tick)|remittance|payment advice|"
    r"transfer record|swift copy|wire transfer (confirmation|copy|slip)|"
    r"renouvellement (automatique|de votre|de vos|du service)|"
    r"services? (suspended|suspendus?|actuellement indisponibles)|"
    r"verify your (account|identity)|unusual (sign-?in|activity)|"
    r"iniciar sesi[óo]n cerca de|mailbox (is )?(full|storage)|password (expires|expiry)|"
    r"seo (services|ranking)|guest post|backlinks?|web ?design (services|proposal)|"
    r"investment opportunit|cryptocurrenc|bitcoin|"
    # Families found in the final audit of the discovery sample.
    r"invited you to test openai|openai advertising|ads ?gpt|\[limited time offer\]|"
    r"compliance confirmation needed|vos publicit[ée]s sont soumises|metamask|"
    r"with seo\b|increase online visibility|factura electr[óo]nica para|"
    r"su factura ha sido confirmada|facture [ée]lectronique #|ihre rechnung wurde best[äa]tigt|"
    # Fake e-invoice family: an invoice or payment "linked to" our own address.
    r"(gekoppeld aan|verkn[üu]pft mit|linked to|associated with|voor het account|"
    r"f[üu]r|voor) (?:atendimento@lebillet|support@tickett)",
    re.IGNORECASE,
)
# A word mixing Latin with Greek/Cyrillic letters ("ΜеtаΜаѕk") is a spoof.
# Adjacent letters only, so "Билеты Calema" (two separate words) is fine.
MIXED_SCRIPT = re.compile(r"[A-Za-z][\u0370-\u03ff\u0400-\u04ff]|[\u0370-\u03ff\u0400-\u04ff][A-Za-z]")
# Scam phrases a genuine customer can also write (a proof of payment, "a sua página
# está com erro"). Mail caught only by one of these, or by a DMARC failure, is filed
# with the scams but never sent to Trash without a person seeing it.
AMBIGUOUS_BODY = re.compile(
    r"sua p[áa]gina|votre page|remittance|payment advice|transfer record|swift copy|"
    r"wire transfer|unusual (sign-?in|activity)"
)
FORWARD_SUBJECT = re.compile(r"^\s*(fw|fwd|enc|tr|rv)\s*:", re.IGNORECASE)
FORWARD_BODY = re.compile(
    r"(-{2,}\s*(forwarded message|mensagem reencaminhada|mensagem encaminhada|"
    r"message transf[ée]r[ée]|urspr[uü]ngliche nachricht)\s*-{2,})|"
    r"^\s*(de|from|van)\s*:.*\n\s*(enviad|sent|date|data)",
    re.IGNORECASE | re.MULTILINE,
)


def looks_forwarded(rec: dict) -> bool:
    """A partner forwarding a customer's email is still customer mail."""
    return bool(
        FORWARD_SUBJECT.search(rec.get("subject", ""))
        or FORWARD_BODY.search(rec.get("body_text", "")[:1500])
    )


def safe_to_purge(reason: str) -> bool:
    """Is a phishing verdict (the reason from classify) certain enough to delete unreviewed?"""
    if reason.startswith("DMARC"):
        return False
    return not (reason.startswith("scam content pattern") and AMBIGUOUS_BODY.search(reason))


def classify(rec: dict) -> tuple[str, str]:
    domain = rec.get("from_domain", "")
    subject = rec.get("subject", "")

    if domain in PHISHING_DOMAINS:
        return "phishing", f"known scam sender ({domain})"
    if PHISHING_SUBJECT.search(subject):
        return "phishing", "brand-impersonation subject pattern"
    if rec.get("dmarc_fail") and domain not in FORM_DOMAINS:
        return "phishing", "DMARC failed - sender likely forged"
    if MIXED_SCRIPT.search(subject):
        return "phishing", "mixed-script (homoglyph) subject"
    if domain not in INTERNAL_DOMAINS:
        hit = PHISHING_BODY.search(subject + "\n" + rec.get("body_text", "")[:3000])
        if hit:
            return "phishing", f"scam content pattern ({hit.group(0)[:30].lower()})"

    # Rescue before any bulk/internal rule can bin a real customer.
    if domain in FORM_DOMAINS and NEWSLETTER_FORM.search(subject):
        return "vendor", "newsletter sign-up on the website"
    if domain in FORM_DOMAINS:
        return "customer", f"website contact form ({domain})"
    if looks_forwarded(rec) and domain not in INTERNAL_DOMAINS:
        return "customer", "forwarded to support by a partner"

    if domain in SPAM_DOMAINS:
        return "spam", f"bulk commercial sender ({domain})"
    if domain in SOCIAL_DOMAINS:
        return "social", f"platform notification ({domain})"
    if domain in VENDOR_DOMAINS:
        return "vendor", f"service notification ({domain})"
    if domain in INTERNAL_DOMAINS:
        # atendimento@ receives copies of staff replies sent to customers.
        # Those belong to their case as the answer given - they are not
        # internal chatter, and they are not customer requests either.
        external = [a for a in rec.get("to", [])
                    if a.rpartition("@")[2] not in INTERNAL_DOMAINS]
        if external:
            return "staff_reply", f"reply sent to a customer by {rec.get('from_email','')}"
        return "internal", f"LeBillet internal ({domain})"

    if rec.get("list_unsubscribe") and not rec.get("in_reply_to"):
        return "spam", "bulk mailing list, not a reply"
    # Only trust Auto-Submitted from infrastructure senders: a customer's
    # out-of-office responder sets it too, and that is still a customer.
    if rec.get("auto_submitted", "").startswith("auto") and domain not in FREEMAIL:
        return "vendor", "auto-generated message"

    return "customer", "no exclusion rule matched"


def main() -> int:
    if not IN.exists():
        print(f"missing {IN} - run extract.py first", file=sys.stderr)
        return 1

    counts: dict[str, int] = {}
    with IN.open(encoding="utf-8") as src, OUT.open("w", encoding="utf-8") as dst:
        for line in src:
            rec = json.loads(line)
            bucket, reason = classify(rec)
            rec["bucket"], rec["bucket_reason"] = bucket, reason
            counts[bucket] = counts.get(bucket, 0) + 1
            dst.write(json.dumps(rec, ensure_ascii=False) + "\n")

    total = sum(counts.values())
    print(f"triaged {total} messages -> {OUT}\n", file=sys.stderr)
    for bucket, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>5}  {100*n/total:>5.1f}%  {bucket}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
