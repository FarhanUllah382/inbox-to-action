"""
Sender-trust signal (Design Decisions V2.4, Decision 3).

Deterministic, header-only signal computed before extraction, alongside
Triage (V2.2) — no LLM call. This is a separate signal from Triage: Triage
decides whether an email reaches extraction at all; this decides how much
the *policy* (app/policy.py) should trust an email that does reach and
pass extraction. Feeds compute_policy() as a hard override (Decision 4) —
never triage itself, which stays untouched.

Targets the failure candidate id=9 exposed under V2.3: a well-formed,
high-confidence request whose actual problem was sender identity, not
content — content-level defenses (extraction_prompt.py's
injection_suspected) cannot catch that; only a sender-identity signal can.

Implementation note (empirically verified — see the Decision 6 adversarial
run): the doc's illustrative example is an org-name pattern ("Acme Legal
<random123@gmail.com>"), but candidate id=9's actual sender used a display
name like "SARKAR Y,T" — not org-sounding, but formatted like an
auto-generated corporate-directory name (ALL-CAPS surname + comma-
separated initials) on a personal webmail domain, with no textual link
between the name and the address (see the fictional reconstruction
"SARKAR Y,T" <renewals.desk.0000@gmail.example> in
phase1_extraction/adversarial_test_emails.jsonl and app/demo_data.py — the
real address isn't reproduced here). A keyword-only reading of
"professional-sounding" would not have caught id=9 at all.
`_looks_professional` below therefore checks both patterns:
organizational/role keywords, OR corporate-directory-style name formatting.
"""

import re
from email.utils import parseaddr
from typing import Literal

from app.config import KNOWN_CONTACT_DOMAINS

SenderTrust = Literal["known", "unknown_domain", "suspicious"]

# Common free/personal webmail providers — legitimate on their own, but a
# professional-looking display name paired with one of these, with no
# obvious connection between the two, is the id=9 pattern. "gmail.example"
# is RFC 2606's reserved-for-documentation TLD, not a real provider — it's
# here solely so the fictional "SARKAR Y,T" <renewals.desk.0000@gmail.example>
# reconstruction (phase1_extraction/adversarial_test_emails.jsonl,
# app/demo_data.py) still triggers this rule without using any real address;
# every actual free-webmail domain below is unaffected by its presence.
_FREE_WEBMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
    "icloud.com", "live.com", "protonmail.com", "mail.com", "yandex.com",
    "gmail.example",
}

# Organizational/role words that make a display name read as a business
# identity (the design doc's own example: "Acme Legal <random123@gmail.com>").
_ORG_KEYWORDS = (
    "legal", "support", "billing", "accounts", "sales", "hr", "security",
    "notifications", "service", "help", "info", "contact", "admin",
    "team", "finance", "procurement",
)

# Corporate-directory-style formatting (e.g. "SARKAR Y,T" — the id=9
# pattern, see the module docstring): an all-caps surname followed by
# comma-separated initials,
# the shape an Exchange/Outlook directory auto-generates — not how someone
# would normally label their own personal webmail account.
_DIRECTORY_NAME_RE = re.compile(r"^[A-Z]{2,}\s+[A-Z](,[A-Z])*$")


def _looks_professional(display_name: str) -> bool:
    name = display_name.strip()
    if not name:
        return False
    if any(keyword in name.lower() for keyword in _ORG_KEYWORDS):
        return True
    if _DIRECTORY_NAME_RE.match(name):
        return True
    return False


def _display_name_mismatch(display_name: str, local_part: str) -> bool:
    if not _looks_professional(display_name):
        return False
    # "No obvious connection": none of the display name's words appear in
    # the address's local part.
    name_words = [w.lower() for w in re.split(r"[^a-zA-Z]+", display_name) if len(w) > 1]
    local = local_part.lower()
    return not any(word in local for word in name_words)


def sender_trust_signal(email) -> SenderTrust:
    try:
        display_name, address = parseaddr(email.from_address or "")
        address = address.lower()
        local_part, _, domain = address.partition("@")

        if domain and domain in KNOWN_CONTACT_DOMAINS:
            return "known"

        if domain in _FREE_WEBMAIL_DOMAINS and _display_name_mismatch(display_name, local_part):
            return "suspicious"

        return "unknown_domain"
    except Exception:
        # Fail toward the non-disruptive signal: "unknown_domain" alone
        # never forces review (Decision 4), so an evaluation error can't
        # itself trigger a hard override — but it also never silently
        # upgrades a sender to "known".
        return "unknown_domain"
