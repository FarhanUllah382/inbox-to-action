"""
Automatic Gmail polling (Design Decisions V2.7, Decisions 1-2).

An in-process APScheduler job calls the exact same run_scan()/run_extract()
functions a manual POST /scan + POST /extract already call (app/routes_scan.py,
app/routes_extract.py) — same pipeline, same triage/policy/sender-trust/
Calendar-check behavior, just triggered on a timer instead of a click. This
is the one deliberate, user-confirmed exception to CLAUDE.md's "no background
workers" V1 default (see app/main.py's docstring) — V2.7's entire purpose is
turning manual-trigger scanning into automatic scanning.

Concurrency (Decision 2): APScheduler's default max_instances=1 on a job
means a still-running poll causes the next tick to be skipped rather than
overlapping — made explicit below rather than relied on silently.

Failure safety (Decision 2): a failed poll is logged, not surfaced as an
error, and simply retried next interval — except after 3 consecutive
failures, where the interval backs off to a fixed 10-minute floor
(BACKOFF_INTERVAL_SECONDS, not derived from the normal interval — see
app/config.py's GMAIL_POLL_INTERVAL_SECONDS) until a poll succeeds again,
to avoid hammering a Gmail API that's genuinely having trouble. If a
failure looks like an OAuth/token problem specifically (as opposed to a
transient network/API error), a single "reconnect Gmail" prompt is
published via app/events.py — debounced to once per failure episode, not
once per retry, so the user isn't spammed with the same prompt every 10
minutes while it's unresolved.

Known gap, documented rather than silently assumed correct: auth-failure
detection below is a best-effort string match. mcp_servers/gmail_mcp/server.py
doesn't wrap refresh errors the way notion_mcp/calendar_mcp do (return
{"error": ...}) — they propagate through FastMCP and app/mcp_clients.py's
unwrap() as a bare RuntimeError, so there's no typed exception to catch.
The exact text Google's real RefreshError produces hasn't been confirmed
against an actually-revoked token (V2.7 Decision 9 point 2 — deliberately
not tested against a real account as part of this implementation). Worst
case if the string doesn't match: an auth failure is treated as a generic
quiet failure (still gets the 10-minute backoff) rather than surfacing the
specific "reconnect Gmail" prompt — fails safe, not silently wrong.
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlmodel import Session

from app import demo_reset
from app.config import DEMO_MODE, GMAIL_POLL_INTERVAL_SECONDS
from app.db import engine
from app.events import event_bus
from app.routes_extract import run_extract
from app.routes_scan import ScanSetupError, run_scan

logger = logging.getLogger(__name__)

JOB_ID = "gmail_poll"
DEMO_RESET_JOB_ID = "demo_reset"
NORMAL_INTERVAL_SECONDS = GMAIL_POLL_INTERVAL_SECONDS
# Fixed floor, deliberately NOT scaled off NORMAL_INTERVAL_SECONDS — a
# demo-speed normal interval must never weaken the real backoff safety net.
BACKOFF_INTERVAL_SECONDS = 600
FAILURE_THRESHOLD = 3
# Public demo only (see start()) — periodically wipes and re-seeds the
# fixed demo inbox so a stream of visitors never sees a stale/approved-out
# state. 30 minutes is generous for one visitor's session, short enough
# that the demo doesn't stay messy for long.
DEMO_RESET_INTERVAL_SECONDS = 1800

# Best-effort substring match against Google auth library / OAuth error text
# — see module docstring's "Known gap" note.
_AUTH_FAILURE_MARKERS = (
    "invalid_grant",
    "unauthorized_client",
    "invalid_client",
    "token has been expired or revoked",
    "refresherror",
)


class _PollState:
    def __init__(self) -> None:
        self.consecutive_failures = 0
        self.auth_prompt_sent = False


_state = _PollState()
scheduler: AsyncIOScheduler | None = None


def _is_auth_failure(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _AUTH_FAILURE_MARKERS)


def get_status() -> dict:
    return {
        "consecutive_failures": _state.consecutive_failures,
        "backed_off": _state.consecutive_failures >= FAILURE_THRESHOLD,
        "interval_seconds": (
            BACKOFF_INTERVAL_SECONDS
            if _state.consecutive_failures >= FAILURE_THRESHOLD
            else NORMAL_INTERVAL_SECONDS
        ),
    }


def _handle_poll_failure(exc: Exception) -> None:
    _state.consecutive_failures += 1
    logger.warning("gmail poll failed (%d consecutive): %s", _state.consecutive_failures, exc)

    if _is_auth_failure(exc) and not _state.auth_prompt_sent:
        event_bus.publish(
            {
                "type": "auth_error",
                "message": "Gmail connection needs to be renewed — reconnect via /auth/google/login.",
            }
        )
        _state.auth_prompt_sent = True  # once per episode, not every retry

    if _state.consecutive_failures == FAILURE_THRESHOLD and scheduler is not None:
        logger.warning(
            "gmail poll: %d consecutive failures, backing off to %ds interval",
            FAILURE_THRESHOLD,
            BACKOFF_INTERVAL_SECONDS,
        )
        scheduler.reschedule_job(JOB_ID, trigger="interval", seconds=BACKOFF_INTERVAL_SECONDS)


def _handle_poll_success() -> None:
    was_backed_off = _state.consecutive_failures >= FAILURE_THRESHOLD
    _state.consecutive_failures = 0
    _state.auth_prompt_sent = False
    if was_backed_off and scheduler is not None:
        logger.info("gmail poll: recovered, resuming %ds interval", NORMAL_INTERVAL_SECONDS)
        scheduler.reschedule_job(JOB_ID, trigger="interval", seconds=NORMAL_INTERVAL_SECONDS)


async def poll_once(max_results: int = 20, max_emails: int = 20) -> None:
    """One scan+extract cycle. Never raises — every failure is caught and
    routed through _handle_poll_failure so a bad tick can't crash the
    scheduler or take down the process (Decision 2)."""
    with Session(engine) as session:
        try:
            await run_scan(session, max_results)
        except ScanSetupError as e:
            logger.info("gmail poll skipped — not configured yet: %s", e)
            return
        except Exception as e:  # noqa: BLE001 — deliberately broad, see docstring
            _handle_poll_failure(e)
            return

        try:
            await run_extract(session, max_emails)
        except Exception as e:  # noqa: BLE001 — deliberately broad, see docstring
            _handle_poll_failure(e)
            return

    _handle_poll_success()


async def _demo_reset_job() -> None:
    """Public-demo-only periodic reset. Never raises — a failed reset is
    logged and simply retried next interval, same "never crash the
    scheduler" convention as poll_once above."""
    try:
        with Session(engine) as session:
            await demo_reset.reset_demo(session)
    except Exception as e:  # noqa: BLE001 — deliberately broad, see docstring
        logger.warning("demo reset failed: %s", e)


def start() -> None:
    global scheduler
    if scheduler is not None:
        return  # already started — avoid double-registering the job on reload
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        poll_once,
        trigger="interval",
        seconds=NORMAL_INTERVAL_SECONDS,
        id=JOB_ID,
        max_instances=1,  # single-flight (Decision 2) — made explicit, not relied on silently
        coalesce=True,  # a burst of missed ticks (e.g. process was suspended) runs once, not N times
    )
    if DEMO_MODE:
        scheduler.add_job(
            _demo_reset_job,
            trigger="interval",
            seconds=DEMO_RESET_INTERVAL_SECONDS,
            id=DEMO_RESET_JOB_ID,
            max_instances=1,
        )
    scheduler.start()
    logger.info("gmail poll scheduler started (%ds interval)", NORMAL_INTERVAL_SECONDS)


def shutdown() -> None:
    global scheduler
    if scheduler is not None:
        scheduler.shutdown(wait=False)
        scheduler = None
