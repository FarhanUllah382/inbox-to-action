"""
Tests for app/sender_trust.py's sender_trust_signal() (Design Decisions
V2.4, Decision 3) — including the candidate id=9 pattern (a corporate-
directory-style display name on a personal webmail domain) that motivated
it. The address below is a fictional reconstruction, not the real sender
that originally exposed this gap.
"""

from app import sender_trust


class FakeEmail:
    def __init__(self, from_address: str) -> None:
        self.from_address = from_address


def test_known_contact_domain(monkeypatch):
    monkeypatch.setattr(sender_trust, "KNOWN_CONTACT_DOMAINS", ["bizco.com"])
    email = FakeEmail('"Dana Park" <dana.park@bizco.com>')
    assert sender_trust.sender_trust_signal(email) == "known"


def test_ordinary_personal_address_is_unknown_domain():
    email = FakeEmail("priya.iyer@northgate-consulting.com")
    assert sender_trust.sender_trust_signal(email) == "unknown_domain"


def test_org_keyword_display_name_on_free_webmail_is_suspicious():
    # The design doc's own example.
    email = FakeEmail('"Acme Legal" <random123@gmail.com>')
    assert sender_trust.sender_trust_signal(email) == "suspicious"


def test_directory_style_name_on_free_webmail_is_suspicious():
    # candidate id=9's pattern — the reason this check exists at all.
    # Fictional reconstruction; gmail.example is RFC 2606's
    # reserved-for-documentation TLD, not a real provider.
    email = FakeEmail('"SARKAR Y,T" <renewals.desk.0000@gmail.example>')
    assert sender_trust.sender_trust_signal(email) == "suspicious"


def test_professional_name_with_matching_local_part_is_not_suspicious():
    # Display name says "support" (an org keyword) on a free webmail
    # domain, but the local part visibly IS "support" — no identity
    # mismatch to flag, unlike adv-6's "IT Support Team" <admin.helpdesk@...>.
    email = FakeEmail('"Support Team" <support@gmail.com>')
    assert sender_trust.sender_trust_signal(email) != "suspicious"


def test_personal_name_on_free_webmail_is_not_suspicious():
    # An ordinary personal name on gmail is not "professional-sounding" —
    # must not be flagged just for using a free webmail provider.
    email = FakeEmail('"Marcus Webb" <marcus.webb@gmail.com>')
    assert sender_trust.sender_trust_signal(email) != "suspicious"


def test_malformed_sender_fails_toward_unknown_domain():
    email = FakeEmail("")
    assert sender_trust.sender_trust_signal(email) == "unknown_domain"
