"""
Stored emails -> task_candidates.

Reuses the Phase 1 extractor unchanged. Deliberately stops at candidate
creation: no approval/edit logic and no Notion sync here (see CLAUDE.md
build order — those are separate phases). Synchronous, single request,
one email at a time — no background worker.

Ahead of extraction, each email passes through Triage (app/triage.py) —
a deterministic pre-filter, no LLM call (Design Decisions V2.2). Only
obviously non-actionable emails are skipped; extraction_prompt.py and
extractor.py are otherwise reached exactly as in V2.1, unchanged.

Each actionable candidate also gets a shadow-mode policy_decision (Design
Decisions V2.3, Decisions 1-2 & 4) via app/policy.py — computed and
stored for later reporting, but never acted on: AUTO_ACT_ENABLED is False
(app/config.py), so `status` is always "pending" here, exactly as before
V2.3. See app/policy.py's docstring for why.

V2.4 adds two more inputs to that same policy call: `injection_suspected`
(from extraction_prompt.py's output — see app/policy.py) and
sender_trust_signal (app/sender_trust.py), computed alongside triage,
before extraction, same as V2.2's should_triage_out(). Both are hard
overrides in compute_policy(), not new triage rules — triage itself
(app/triage.py) is untouched.

V2.5 (Design Decisions V2.5, Decision 4) fetches any active correction
notes once per /extract call — they don't vary within a batch — and
passes the same list into every extractor.extract() call below.
extractor.py itself never touches the database (Decision 4); this route
is the caller responsible for that. compute_policy()'s call is completely
unchanged from V2.4 — correction notes are never passed to it (Decision 2).

V2.6 (Design Decisions V2.6, Decisions 3-4) resolves a candidate's
`proposed_meeting_time` (app/meeting_time_resolver.py) and, if resolved,
runs the Scheduling Agent (app/scheduling.py) once at creation time —
same "compute once, store, display later" convention as policy_decision
(V2.3). This is a read-only calendar check; nothing here ever books
anything (Decision 5 — that's app/routes_scheduling.py's job, and
nowhere else). If the account hasn't granted Calendar scope, or the check
fails for any other reason, calendar_status is set to "unavailable" and
extraction/candidate-creation proceeds exactly as before (Decision 2) —
never raised as an ExtractionError.

V2.6 scheduling correction: a time-bearing deadline (e.g. "5 PM this
Friday") is also a scheduling input, not just an explicit meeting
proposal — see _resolve_scheduling_input() below. `proposed_meeting_time`
stays strictly primary; the deadline is only tried as a fallback when no
meeting was proposed. `deadline_resolved`/`resolved_due_date` themselves
are computed exactly as before via app/deadline_resolver.py (now fixed to
correctly resolve time-bearing phrases to a date — see that module) —
nothing about compute_policy()'s inputs or app/policy.py's rules changes
here beyond that resolver bug fix flowing through as intended.

V2.7 (Design Decisions V2.7): the actual extraction logic now lives in
run_extract(), a plain function taking a Session instead of `Depends`, so
app/scheduler.py's automatic poll can call the exact same code a manual
POST /extract already did (Decision 1's "no logic duplication" principle,
same pattern as run_scan() in app/routes_scan.py). Two additive changes
inside the existing candidate-creation block, nothing else touched:
1. injection_suspected/sender_trust — already computed here to feed
   compute_policy() and previously discarded afterward — are now also
   stored directly on the candidate, so the review UI can show a distinct
   "flagged" badge for a V2.4 override specifically, instead of lumping it
   in with every other review_required candidate (V2.7 Decision 7 item 3).
2. A `new_candidate` event is published via app/events.py's in-process bus
   right after a candidate is committed, for the review UI's SSE toast/badge
   (V2.7 Decision 5). Nothing about what gets computed or decided changes.
"""

from datetime import datetime, timezone as dt_timezone
from typing import Optional

from fastapi import APIRouter, Depends

from sqlmodel import Session, select

from app.config import DEMO_MODE, THREAD_CONTEXT_DEPTH
from app.correction_notes import get_active_notes
from app.db import get_session
from app.deadline_resolver import resolve_deadline_phrase
from app.demo_snapshot import get_demo_snapshot_result
from app.events import event_bus
from app.meeting_time_resolver import resolve_meeting_time_phrase
from app.models import Email, EmailAccount, TaskCandidate, User
from app.policy import compute_policy
from app.rate_limit import rate_limit_extract
from app.scheduling import check_availability
from app.sender_trust import sender_trust_signal
from app.triage import should_triage_out
from phase1_extraction.extractor import Extractor, ExtractionError

router = APIRouter(tags=["extract"])


def _resolve_scheduling_input(
    result: dict, received_at: datetime, user_timezone: str
) -> tuple[Optional[str], Optional[str], Optional[datetime]]:
    """Returns (scheduling_source, phrase, resolved_time). Meeting stays
    strictly primary — the deadline is only tried when no meeting was
    proposed, or the meeting phrase didn't resolve. Both use the exact
    same resolver (app/meeting_time_resolver.py), unchanged; a bare-date
    deadline with no clock time (the common case) correctly yields
    nothing here, same as before this change."""
    proposed_meeting_phrase = result.get("proposed_meeting_time")
    resolved_meeting_time = resolve_meeting_time_phrase(proposed_meeting_phrase, received_at, user_timezone)
    if resolved_meeting_time is not None:
        return "meeting", proposed_meeting_phrase, resolved_meeting_time

    deadline_phrase = result.get("deadline")
    resolved_deadline_time = resolve_meeting_time_phrase(deadline_phrase, received_at, user_timezone)
    if resolved_deadline_time is not None:
        return "deadline", deadline_phrase, resolved_deadline_time

    return None, None, None


async def _run_scheduling_agent(
    account: EmailAccount, proposed_start: datetime
) -> tuple[str, list]:
    """Returns (calendar_status, suggested_slots). Never raises — any
    failure (missing scope, MCP error, etc.) degrades to "unavailable"
    rather than breaking /extract (V2.6 Decision 2)."""
    try:
        result = await check_availability(account.access_token, account.refresh_token, proposed_start)
        return result.status, [slot.isoformat() for slot in result.suggested_slots]
    except Exception as e:  # noqa: BLE001 — deliberately broad, see docstring
        print(f"[extract] calendar check failed, degrading to unavailable: {e}")
        return "unavailable", []


def _build_thread_context(session: Session, email: Email) -> str:
    prior = session.exec(
        select(Email)
        .where(
            Email.thread_id == email.thread_id,
            Email.user_id == email.user_id,
            Email.id != email.id,
            Email.received_at < email.received_at,
        )
        .order_by(Email.received_at.desc())
        .limit(THREAD_CONTEXT_DEPTH)
    ).all()

    if not prior:
        return ""

    prior.reverse()  # chronological order
    blocks = [
        f"[{e.received_at.isoformat()}] From: {e.from_address}\nSubject: {e.subject}\n{e.cleaned_text}"
        for e in prior
    ]
    return "\n\n---\n\n".join(blocks)


async def run_extract(session: Session, max_emails: int = 20) -> dict:
    """The actual extraction pipeline — identical to what the route always
    did, just callable without an HTTP request (V2.7 Decision 1). Returns a
    zero-count dict (not an exception) when there's no user yet, matching
    the route's pre-existing behavior."""
    user = session.exec(select(User)).first()
    if user is None:
        return {
            "processed": 0,
            "candidates_created": 0,
            "not_actionable": 0,
            "triaged_out": 0,
            "failed": 0,
        }

    account = session.exec(select(EmailAccount).where(EmailAccount.user_id == user.id)).first()

    pending = session.exec(
        select(Email)
        .where(Email.user_id == user.id, Email.extracted_at == None)  # noqa: E711
        .order_by(Email.received_at.asc())
        .limit(max_emails)
    ).all()

    active_notes = get_active_notes(session)  # read-time-only, capped at 5 (V2.5 Decision 5)

    # DEMO_MODE: never construct a real Anthropic client — Extractor()'s
    # __init__ would do so even if .extract() were never called. The public
    # demo must make zero live Claude calls and needs no API key at all.
    extractor = None if DEMO_MODE else Extractor()
    candidates_created = 0
    not_actionable = 0
    triaged_out = 0
    failed = 0

    for email in pending:
        if should_triage_out(email):
            print(f"[extract] email {email.id} ({email.subject!r}) triaged out — skipped extraction")
            triaged_out += 1
            email.extracted_at = datetime.now(dt_timezone.utc)
            session.add(email)
            session.commit()
            continue

        sender_trust = sender_trust_signal(email)

        thread_context = _build_thread_context(session, email)
        email_data = {
            "from": email.from_address,
            "subject": email.subject,
            "body": email.cleaned_text,
        }

        try:
            if DEMO_MODE:
                # Replay the hand-labeled snapshot instead of calling Claude
                # (app/demo_snapshot.py) — thread_context/correction_notes
                # are irrelevant here since the result is fixed per email.
                result = get_demo_snapshot_result(email.message_id)
            else:
                result = extractor.extract(email_data, thread_context, correction_notes=active_notes)
        except ExtractionError as e:
            print(f"[extract] email {email.id} ({email.subject!r}) failed: {e}")
            failed += 1
            continue  # leave extracted_at null so it's retried on the next /extract call

        email.extracted_at = datetime.now(dt_timezone.utc)
        session.add(email)

        if result["actionable"]:
            candidates_created += 1
            resolved_due_date = resolve_deadline_phrase(
                result["deadline"], email.received_at, user.timezone
            )
            deadline_resolved = resolved_due_date is not None
            # Fail closed on a missing field, unlike triage's fail-open:
            # this is a hard-override safety input (app/policy.py), so if
            # the model ever omits it, treat the email as suspect rather
            # than silently trusting it.
            injection_suspected = result.get("injection_suspected", True)
            scheduling_source, scheduling_phrase, scheduling_time = _resolve_scheduling_input(
                result, email.received_at, user.timezone
            )
            candidate = TaskCandidate(
                email_id=email.id,
                claude_task=result["task"],
                claude_deadline_phrase=result["deadline"],
                claude_assignee=result["assignee"],
                claude_reason=result["reason"],
                claude_confidence=result["confidence"],
                resolved_due_date=resolved_due_date,
                policy_decision=compute_policy(
                    confidence=result["confidence"],
                    deadline_resolved=deadline_resolved,
                    assignee=result["assignee"],
                    injection_suspected=injection_suspected,
                    sender_trust_signal=sender_trust,
                ),
                # V2.7: persist the two raw signals that already fed the
                # policy call above — nothing new computed, just no longer
                # discarded after compute_policy() reads them.
                injection_suspected=injection_suspected,
                sender_trust_signal=sender_trust,
                deadline_resolved=deadline_resolved,
                proposed_meeting_phrase=scheduling_phrase,
                resolved_meeting_time=scheduling_time,
                scheduling_source=scheduling_source,
                status="pending",  # shadow mode only — see module docstring
            )
            session.add(candidate)
            session.commit()
            session.refresh(candidate)

            # Scheduling Agent (V2.6 Decision 4, corrected) — read-only
            # calendar check, whenever a scheduling-relevant time was
            # resolved from either an explicit meeting proposal or a
            # time-bearing deadline (_resolve_scheduling_input above). Runs
            # regardless of policy_decision/injection_suspected/sender_trust
            # (Decision 6 — those don't gate candidate creation today, so
            # there's nothing new to filter on here).
            if scheduling_time is not None:
                if account is None or DEMO_MODE:
                    # DEMO_MODE: never call the real Calendar API, even
                    # though a (placeholder-token) account row exists to
                    # satisfy Email's foreign key — see app/demo_data.py.
                    candidate.calendar_status = "unavailable"
                else:
                    candidate.calendar_status, candidate.suggested_meeting_slots = (
                        await _run_scheduling_agent(account, scheduling_time)
                    )
                session.add(candidate)
                session.commit()

            # V2.7 Decision 5 — notify any open review-UI tab. Fires AFTER
            # the scheduling check above (not right after creation) so a
            # client that refreshes the instant it's notified sees the
            # final calendar_status, not a pre-check snapshot with no
            # "Also add to calendar" button and no free/conflict message —
            # the exact staleness bug this ordering used to cause. Purely
            # additive either way: publishing has no effect if nothing is
            # subscribed (app/events.py), and never blocks/raises into this
            # loop.
            event_bus.publish(
                {
                    "type": "new_candidate",
                    "candidate_id": candidate.id,
                    "task": candidate.claude_task,
                    "email_subject": email.subject,
                }
            )
        else:
            not_actionable += 1

        session.commit()

    return {
        "processed": len(pending) - failed,
        "candidates_created": candidates_created,
        "not_actionable": not_actionable,
        "triaged_out": triaged_out,
        "failed": failed,
    }


@router.post("/extract", dependencies=[Depends(rate_limit_extract)])
async def extract(max_emails: int = 20, session: Session = Depends(get_session)):
    return await run_extract(session, max_emails)
