"""
App Configuration (Phase 2+)
"""

import os
from dotenv import load_dotenv

load_dotenv()

# Database
DATABASE_URL = os.getenv("DATABASE_URL")

# Public demo mode (security hardening) — when true, no real Gmail/Notion/
# Calendar calls are made: the pipeline runs against a fixed set of
# synthetic seed emails and "approve" writes to an in-memory fake Notion
# instead of the real API. See app/demo_data.py. Never true on the private
# instance; Render's demo service pair (render.yaml) is the only place
# this is set.
DEMO_MODE = os.getenv("DEMO_MODE", "false").strip().lower() == "true"

# Which LLM phase1_extraction/extractor.py calls — "anthropic" (default;
# the private instance's behavior is unchanged unless this is explicitly
# set) or "gemini". See phase1_extraction/config.py for why this is
# duplicated rather than imported from there.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()

# Anthropic API (reused from Phase 1)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = "claude-sonnet-5"

# Gemini (google-genai SDK) — alternate provider, selected via LLM_PROVIDER.
# See phase1_extraction/config.py's comment for how GEMINI_MODEL's default
# was confirmed against the live API rather than guessed.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Extraction parameters (reused from Phase 1)
THREAD_CONTEXT_DEPTH = 5
CONFIDENCE_THRESHOLD = 0.7

# Google OAuth
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback")
# Calendar scope added V2.6 Decision 2 — calendar.events (read/write events
# only, not full calendar settings) rather than the broader `calendar`
# scope, matching this project's existing "minimum scopes necessary"
# convention. Existing users must explicitly re-consent to this new scope
# (Google requires this; there's no way to silently expand access) — see
# app/scheduling.py for the graceful-degradation path when they haven't.
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]

# Single MVP test user. V1 processes one person's inbox (see CLAUDE.md); the
# user's IANA timezone must be explicit, never defaulted to UTC (Decision 5).
USER_TIMEZONE = os.getenv("USER_TIMEZONE")

# Notion (Phase 4)
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

# V2.7 — Gmail poll interval, in seconds. Configurable via env var so it can
# be sped up temporarily (e.g. for a live demo) without touching code. The
# failure-backoff interval in app/scheduler.py is a fixed floor, NOT derived
# from this value — a demo-speed setting here never weakens that fail-safe.
GMAIL_POLL_INTERVAL_SECONDS = int(os.getenv("GMAIL_POLL_INTERVAL_SECONDS", "120"))

# Triage (V2.2) — deterministic pre-filter ahead of extraction (Design
# Decisions V2.2, Decision 2). Empty blocklist by default: matches Decision
# 3's fail-open policy — nothing is blocked by domain until the user
# explicitly configures one. User-editable, same convention as
# CONFIDENCE_THRESHOLD.
TRIAGE_BLOCKED_DOMAINS: list[str] = []
ACTION_KEYWORDS = ["please", "review", "due", "deadline", "assign"]

# Trust/risk policy (V2.3) — shadow mode only (Design Decisions V2.3,
# Decision 5). app/policy.py computes and app/routes_extract.py stores a
# policy_decision on every candidate, but nothing currently reads
# AUTO_ACT_ENABLED to branch on it — the real auto-create/undo mechanism
# (Decision 3) is deferred until this flag's flip is separately approved,
# and not before V2.4 (injection-defense boundary) exists.
#
# DO NOT set this to True. Doing so has no effect yet (no code path
# checks it), but flipping it is explicitly reserved for a distinct,
# separately-approved decision per Decision 5 — not something to change
# while touching this file for an unrelated reason.
AUTO_ACT_ENABLED = False

# Separate, higher bar than CONFIDENCE_THRESHOLD (which only gates review-UI
# *visibility*, not action). Starting value per Decision 2; tunable only
# after shadow-mode evaluation (Decision 6).
AUTO_CONFIDENCE_THRESHOLD = 0.95

# Sender-trust signal (V2.4, Decision 3) — deterministic, header-only check
# alongside triage, feeding compute_policy() as a hard override (Decision
# 4). Empty by default: matches the same conservative-default convention
# as TRIAGE_BLOCKED_DOMAINS — no sender is pre-trusted until the user
# explicitly configures one. User-editable.
KNOWN_CONTACT_DOMAINS: list[str] = []

# Scheduling Agent (V2.6, Decision 4) — not specified in the design doc;
# simple, stated defaults. "Business hours" bound the nearest-free-slot
# search; meeting duration is used both for the free/busy check window and
# for events actually created via the explicit approval step (Decision 5).
BUSINESS_HOURS_START = 9   # 9am, in the user's IANA timezone
BUSINESS_HOURS_END = 17    # 5pm, in the user's IANA timezone
MEETING_DURATION_MINUTES = 30
