"""
In-memory fake Notion for public demo mode (security hardening).

DEMO_MODE visitors can click Approve and see the full flow work end to
end, including a resulting "Notion task" — without this instance ever
holding a real Notion API key or writing to a real workspace. Exactly one
function creates these fake pages (mirrors app/notion_client.py's own
"exactly one write path" design), and they live only in process memory —
gone on restart, same as the rest of the demo data.
"""

from datetime import date as date_type, datetime, timezone
from typing import Optional

_pages: dict[str, dict] = {}
_counter = 0


def create_fake_task_page(
    task: Optional[str],
    resolved_due_date: Optional[date_type],
    assignee: Optional[str],
    reason: str,
    gmail_link: str,
) -> str:
    global _counter
    _counter += 1
    page_id = f"demo-page-{_counter}"
    _pages[page_id] = {
        "id": page_id,
        "task": task,
        "resolved_due_date": resolved_due_date.isoformat() if resolved_due_date else None,
        "assignee": assignee,
        "reason": reason,
        "gmail_link": gmail_link,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return page_id


def list_fake_pages() -> list[dict]:
    return list(_pages.values())


def reset() -> None:
    """Clears all fake pages — called by app/demo_reset.py's periodic/manual demo reset."""
    global _counter
    _pages.clear()
    _counter = 0
