"""
Manual demo-reset endpoint for the public demo instance (security hardening).

Lets a visitor put the demo back into its freshly-seeded state on demand,
without waiting for the automatic 30-min reset (app/scheduler.py). DEMO_MODE
is the gate, enforced here — never touches the database when it's false.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.config import DEMO_MODE
from app.db import get_session
from app.demo_reset import reset_demo
from app.rate_limit import rate_limit_demo_reset

router = APIRouter(tags=["demo"])


@router.post("/demo/reset", dependencies=[Depends(rate_limit_demo_reset)])
async def demo_reset(session: Session = Depends(get_session)):
    if not DEMO_MODE:
        raise HTTPException(403, "Demo reset is only available on the public demo instance")
    await reset_demo(session)
    return {"status": "reset"}
