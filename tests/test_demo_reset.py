"""
Tests for app/demo_reset.py's reset_demo() and the POST /demo/reset
endpoint (security hardening). The public demo must always return to its
fixed, freshly-seeded state, using zero live Anthropic calls — everything
comes from the snapshot fixture (app/demo_snapshot.py), never a real
Claude API call. DEMO_MODE defaults to "false" in tests/conftest.py, so
tests exercising demo-mode extraction patch app.routes_extract's own
DEMO_MODE name directly (bound once at import time via `from app.config
import DEMO_MODE`) rather than reimporting the whole app.
"""

import asyncio

from sqlmodel import Session, select

from app.db import engine, init_db
from app.demo_data import seed_demo_data
from app.demo_reset import reset_demo
from app.models import TaskCandidate
from app.rate_limit import _demo_reset_limiter
from app.routes_extract import run_extract


def _candidate_ids(session: Session) -> set:
    return set(session.exec(select(TaskCandidate.id)).all())


def test_reset_restores_full_candidate_set(monkeypatch):
    monkeypatch.setattr("app.routes_extract.DEMO_MODE", True)
    init_db()

    with Session(engine) as session:
        seed_demo_data(session)
        asyncio.run(run_extract(session))
        first_ids = _candidate_ids(session)

        assert first_ids, "seeding + extracting from the snapshot should create candidates"

        asyncio.run(reset_demo(session))
        second_ids = _candidate_ids(session)

    # Same count, not accumulated, not empty — reset_demo() deletes before
    # re-seeding, so the restored set matches the first batch's size
    # exactly rather than growing with each reset. (SQLite recycles row
    # ids once a table is emptied, so the ids themselves may repeat —
    # that's expected, not evidence of a leftover row.)
    assert len(second_ids) == len(first_ids)
    assert second_ids, "reset should leave the demo populated, not empty"

    with Session(engine) as session:
        total_rows = len(session.exec(select(TaskCandidate)).all())
    assert total_rows == len(second_ids), "no accumulated/duplicated rows after reset"


def test_demo_mode_never_calls_anthropic(monkeypatch):
    monkeypatch.setattr("app.routes_extract.DEMO_MODE", True)
    init_db()

    def _fail_if_constructed(self, *args, **kwargs):
        raise AssertionError("a real Anthropic client must never be constructed in DEMO_MODE")

    monkeypatch.setattr("phase1_extraction.extractor.Extractor.__init__", _fail_if_constructed)

    with Session(engine) as session:
        seed_demo_data(session)
        asyncio.run(run_extract(session))
        asyncio.run(reset_demo(session))
    # No assertion needed beyond "didn't raise" — _fail_if_constructed
    # would have blown up the flow above if Extractor() were ever called.


def test_demo_reset_endpoint_403_when_demo_mode_false(client):
    # No auth header — this route is deliberately callable by anonymous
    # public demo visitors (app/main.py); DEMO_MODE itself is the gate.
    _demo_reset_limiter._calls.clear()  # isolate from other tests' rate-limit state

    with Session(engine) as session:
        before = _candidate_ids(session)

    res = client.post("/demo/reset")
    assert res.status_code == 403

    with Session(engine) as session:
        after = _candidate_ids(session)
    assert before == after  # no DB changes


def test_demo_reset_endpoint_rate_limited_on_rapid_second_call(client):
    _demo_reset_limiter._calls.clear()

    first = client.post("/demo/reset")
    assert first.status_code == 403  # DEMO_MODE is false in tests — gated before the limiter matters

    second = client.post("/demo/reset")
    assert second.status_code == 429
