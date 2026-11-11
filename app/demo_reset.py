"""
Resets the public demo back to its initial seeded state.

Called by both app/routes_demo.py's manual POST /demo/reset and
app/scheduler.py's automatic 30-min job — both gate on DEMO_MODE
themselves, so this function doesn't check it again; it would run the
same regardless of mode, and the callers are what make it demo-only.
"""

from sqlmodel import Session, delete

from app import demo_notion
from app.demo_data import seed_demo_data
from app.models import CalendarBooking, Email, NotionTask, TaskCandidate, UserDecision
from app.routes_extract import run_extract


async def reset_demo(session: Session) -> None:
    # FK-safe delete order: children before parents. User/EmailAccount are
    # kept — seed_demo_data() is idempotent and reuses them.
    session.exec(delete(CalendarBooking))
    session.exec(delete(NotionTask))
    session.exec(delete(UserDecision))
    session.exec(delete(TaskCandidate))
    session.exec(delete(Email))
    session.commit()

    demo_notion.reset()

    seed_demo_data(session)
    await run_extract(session)  # replays from the snapshot (app/demo_snapshot.py) — zero Anthropic calls
