"""
Static extraction results for the 10 fixed demo emails (app/demo_data.py).

DEMO_MODE must make zero live Anthropic calls and needs no API key at all
(public demo security requirement) — so instead of calling Extractor()
here, app/routes_extract.py looks up the same message_id in this fixture.
Regenerate it with scripts/build_demo_snapshot.py if demo_data.py's emails
ever change; a missing entry is a bug, not something to silently skip.
"""

import functools
import json
from pathlib import Path

SNAPSHOT_PATH = Path(__file__).parent / "demo_snapshot.json"


@functools.lru_cache(maxsize=1)
def load_demo_snapshot() -> dict[str, dict]:
    with open(SNAPSHOT_PATH) as f:
        data = json.load(f)
    data.pop("_meta", None)  # documentation about the fixture, not an email result
    return data


def get_demo_snapshot_result(message_id: str) -> dict:
    snapshot = load_demo_snapshot()
    if message_id not in snapshot:
        raise RuntimeError(
            f"No demo snapshot entry for message_id={message_id!r} — every demo email "
            "needs one; re-run scripts/build_demo_snapshot.py after changing app/demo_data.py"
        )
    return snapshot[message_id]
