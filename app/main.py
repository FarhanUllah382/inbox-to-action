"""
FastAPI app entry point.

Single process — see CLAUDE.md V1 architectural constraints. V2.7 (Design
Decisions V2.7, Decision 1) adds the one deliberate, user-confirmed
exception to that doc's "no background workers" default: an in-process
APScheduler job (app/scheduler.py) that calls the same run_scan()/
run_extract() functions POST /scan and POST /extract already call, just on
a ~2-minute timer instead of a click. Still one process, still no new
service/port/container — just a scheduled call inside the process that was
already running. Run with: uvicorn app.main:app --reload
"""

import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import scheduler as poll_scheduler
from app.auth import require_token
from app.config import DEMO_MODE
from app.db import engine, init_db
from app.routes_auth import router as auth_router
from app.routes_candidates import router as candidates_router
from app.routes_demo import router as demo_router
from app.routes_events import router as events_router
from app.routes_extract import router as extract_router
from app.routes_scan import router as scan_router
from app.routes_scheduling import router as scheduling_router

app = FastAPI(title="Inbox-to-Action")

# The Next.js review UI calls this API directly from the browser. Defaults
# to local dev; in production set ALLOWED_ORIGINS to a comma-separated list
# of the deployed frontend origin(s), e.g. https://inbox-to-action-web.onrender.com
_allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in _allowed_origins.split(",") if origin.strip()],
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type", "Authorization"],
)

# Every route is bearer-token-protected by default (app/auth.py) except
# /health (Render's health check has no way to send a header) and
# GET /auth/google/callback (Google redirects the browser there directly —
# can't carry an Authorization header, so that one route protects itself
# differently; see app/routes_auth.py). auth_router is deliberately NOT
# given a router-level dependency here, since that would apply to the
# callback route too — /login is protected individually in routes_auth.py
# instead. GET /events also can't use a router-level dependency (EventSource
# can't send custom headers) — see app/routes_events.py.
app.include_router(auth_router)
app.include_router(scan_router, dependencies=[Depends(require_token)])
app.include_router(extract_router, dependencies=[Depends(require_token)])
app.include_router(candidates_router, dependencies=[Depends(require_token)])
app.include_router(scheduling_router, dependencies=[Depends(require_token)])
app.include_router(events_router)
# No token dependency: DEMO_MODE itself is the gate (enforced inside the
# route), since anonymous public demo visitors have no token to send —
# same reasoning as /health and GET /events above.
app.include_router(demo_router)


@app.on_event("startup")
async def on_startup() -> None:
    init_db()
    if not DEMO_MODE:
        # Fail closed at boot, not two minutes later inside the scheduler's
        # first silently-logged poll failure: checks the configured
        # LLM_PROVIDER's key is actually present (phase1_extraction/extractor.py)
        # without constructing a real SDK client or making a network call —
        # skipped entirely in DEMO_MODE, which needs no key of any kind.
        from phase1_extraction.extractor import validate_provider_config

        validate_provider_config()
    if DEMO_MODE:
        # Public demo instance: seed the fixed synthetic inbox and run one
        # extraction pass immediately, so a visitor sees populated
        # candidates (including the injection/sender-trust flags) without
        # having to click "Extract tasks now" first. Both are idempotent —
        # see app/demo_data.py and run_extract()'s extracted_at filter —
        # so repeated restarts never re-seed or re-extract.
        from sqlmodel import Session

        from app.demo_data import seed_demo_data
        from app.routes_extract import run_extract

        try:
            with Session(engine) as session:
                seed_demo_data(session)
                await run_extract(session)
        except Exception as e:  # noqa: BLE001 — see comment below
            # get_demo_snapshot_result() (app/demo_snapshot.py) raises loudly
            # on a missing entry rather than skip silently — correct for
            # catching a drifted snapshot during development, but letting it
            # propagate here would crash the whole process at boot (failing
            # Render's health check) over one bad candidate instead of
            # degrading to an empty-but-reachable demo. Logged loud, not
            # silently swallowed — this is exactly the "demo must never
            # break" case, not a license to ignore a real snapshot/data bug.
            print(f"[startup] demo seed/extract failed, continuing with an empty demo: {e}")
    poll_scheduler.start()


@app.on_event("shutdown")
def on_shutdown() -> None:
    poll_scheduler.shutdown()


@app.get("/health")
def health() -> dict:
    # demo_mode is exposed here (unauthenticated) specifically so the web
    # UI can show its "synthetic data" banner before a token is entered.
    return {"status": "ok", "demo_mode": DEMO_MODE}
