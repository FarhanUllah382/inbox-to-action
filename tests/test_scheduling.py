"""
Tests for app/scheduling.py's _overlaps()/_find_alternative_slots() and
app/routes_extract.py's _resolve_scheduling_input() — both are fully
offline/deterministic (no API key involved), but previously had no
dedicated pytest coverage; they were only exercised by the standalone,
Claude-dependent script phase1_extraction/run_v26_eval.py, which isn't run
in CI. Fixtures/assertions ported from that script's
calendar_check_correctness() and scheduling_priority_check().
"""

from datetime import datetime, timedelta, timezone

from app.routes_extract import _resolve_scheduling_input
from app.scheduling import _find_alternative_slots, _overlaps

TZ = timezone.utc

# Known fixture: a Tuesday, with two busy blocks later that week.
PROPOSED = datetime(2026, 8, 18, 10, 0, tzinfo=TZ)  # Tuesday 10:00-10:30 proposed
BUSY_EVENTS = [
    (datetime(2026, 8, 18, 9, 30, tzinfo=TZ), datetime(2026, 8, 18, 10, 30, tzinfo=TZ)),  # overlaps proposed
    (datetime(2026, 8, 18, 11, 0, tzinfo=TZ), datetime(2026, 8, 18, 12, 0, tzinfo=TZ)),
    (datetime(2026, 8, 19, 9, 0, tzinfo=TZ), datetime(2026, 8, 19, 17, 0, tzinfo=TZ)),  # all Wed busy
]


def test_conflict_detected_at_proposed_time():
    proposed_end = PROPOSED + timedelta(minutes=30)
    conflict = any(_overlaps(PROPOSED, proposed_end, b_s, b_e) for b_s, b_e in BUSY_EVENTS)
    assert conflict


def test_alternative_slots_avoid_busy_events_and_weekends():
    alternatives = _find_alternative_slots(PROPOSED, 30, BUSY_EVENTS, TZ)
    assert alternatives, "a free slot should exist later that week"

    for slot in alternatives:
        slot_end = slot + timedelta(minutes=30)
        overlaps_any = any(_overlaps(slot, slot_end, b_s, b_e) for b_s, b_e in BUSY_EVENTS)
        assert not overlaps_any
        assert slot >= PROPOSED
        assert slot.weekday() < 5  # Monday-Friday only


def test_alternative_slots_capped_at_three():
    alternatives = _find_alternative_slots(PROPOSED, 30, BUSY_EVENTS, TZ)
    assert len(alternatives) <= 3


def test_genuinely_free_slot_has_no_conflict():
    free_start = datetime(2026, 8, 20, 14, 0, tzinfo=TZ)
    free_end = free_start + timedelta(minutes=30)
    conflict = any(_overlaps(free_start, free_end, b_s, b_e) for b_s, b_e in BUSY_EVENTS)
    assert not conflict


# --- _resolve_scheduling_input(): meeting-vs-deadline priority ---

RECEIVED_AT = datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc)  # a Monday
USER_TZ = "Asia/Karachi"


def test_meeting_only_resolves_to_meeting():
    result = {"deadline": None, "proposed_meeting_time": "Thursday at 2pm"}
    source, phrase, resolved = _resolve_scheduling_input(result, RECEIVED_AT, USER_TZ)
    assert source == "meeting"
    assert resolved is not None


def test_deadline_with_clock_time_resolves_to_deadline():
    result = {"deadline": "5 PM this Friday", "proposed_meeting_time": None}
    source, phrase, resolved = _resolve_scheduling_input(result, RECEIVED_AT, USER_TZ)
    assert source == "deadline"
    assert resolved is not None


def test_both_present_meeting_wins():
    result = {"deadline": "5 PM this Friday", "proposed_meeting_time": "Thursday at 2pm"}
    source, phrase, resolved = _resolve_scheduling_input(result, RECEIVED_AT, USER_TZ)
    assert source == "meeting"


def test_bare_date_deadline_with_no_meeting_resolves_to_neither():
    # A bare date has no clock time — not a scheduling input on its own.
    result = {"deadline": "Friday", "proposed_meeting_time": None}
    source, phrase, resolved = _resolve_scheduling_input(result, RECEIVED_AT, USER_TZ)
    assert source is None
    assert resolved is None


def test_neither_present_resolves_to_neither():
    result = {"deadline": None, "proposed_meeting_time": None}
    source, phrase, resolved = _resolve_scheduling_input(result, RECEIVED_AT, USER_TZ)
    assert source is None
    assert resolved is None
