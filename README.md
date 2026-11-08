<!-- LIVE DEMO: https://inbox-to-action-demo-web.onrender.com -->

# Inbox-to-Action

[![CI](https://github.com/FarhanUllah382/inbox-to-action/actions/workflows/ci.yml/badge.svg)](https://github.com/FarhanUllah382/inbox-to-action/actions/workflows/ci.yml)

**Gmail → Claude → a human-approved task in Notion — built around treating inbox content as adversarial input, not just data to summarize.**

🔗 **[Live demo](https://inbox-to-action-demo-web.onrender.com)** — a fixed synthetic inbox (including two prompt-injection attempts and a spoofed sender), no real Gmail/Notion/Calendar access. Free tier — first load takes ~45 seconds if the service has been idle.

Gmail is where work arrives. Notion is where work gets tracked. Nothing bridges them automatically — so tasks buried in email either get manually copied over or forgotten.

Inbox-to-Action is that bridge. It watches a Gmail inbox, uses Claude to figure out which emails actually contain a request directed at you, and shows you a clean queue of candidate tasks. You approve, edit, or dismiss each one. **Only on explicit approval does anything get written to Notion.** The system never acts on your inbox by itself.

```text
Gmail  →  preprocess  →  Claude extraction  →  task candidate  →  you review it  →  Notion
                                                                        ↑
                                                          approve / edit / dismiss
```

## Why this exists

A client emails: *"Can you also update the pricing page by Friday?"* The action is buried inside a normal-looking email. It gets missed, or someone has to manually re-type it into a task tracker. Inbox-to-Action closes that gap without becoming a new task manager — Gmail and Notion stay exactly where they are; this is the intelligence layer in between.

## What makes this different from "connect an LLM to your inbox"

An inbox is one of the few places where untrusted, adversarial-by-default text (email content from strangers) gets piped directly into an LLM. That's the actual hard problem here, not the CRUD app around it. A few things this project does about it:

- **Human approval is structurally required, not just a UI convention.** There is exactly one place in the codebase that can write to Notion (`POST /candidates/{id}/approve`), and it's the only code path that ever calls Notion's API. There's no `AUTO_ACT_ENABLED` flip reachable from anywhere else. Same for Calendar — booking a meeting is a separate, explicit action from approving a task, never bundled together.
- **Prompt-injection resistance is a designed boundary, not an afterthought.** Email content is passed to Claude inside explicit `<email_content>` tags with a system-prompt instruction to treat everything inside as untrusted data to *analyze*, never as instructions to *follow*. The model is asked to flag suspected injection attempts (`injection_suspected`) as a distinct output field.
- **A deterministic sender-trust check catches what content-level defenses can't.** A well-formed, high-confidence, perfectly-worded request from a spoofed identity (e.g. a display name styled like an internal corporate directory entry, on a free webmail domain, with no connection between the two) will sail past any content-based injection check — because the content itself isn't malicious, the *identity* is. `app/sender_trust.py` catches that pattern independently and is a hard override in the policy engine, regardless of how confident the extraction was.
- **Claude never touches dates, identities it wasn't given, or IDs.** It extracts a deadline as a *phrase* ("Friday") — resolving that into an actual calendar date is a deterministic function using the email's timestamp and the user's timezone, done entirely outside the model. Gmail links are built by the backend from IDs the Gmail API returned; the model is never asked to produce one. It's told explicitly not to infer a person's identity from "you" — an assignee is only ever set when a name is actually written in the email.
- **Confidence gates visibility and action separately, and conservatively.** `CONFIDENCE_THRESHOLD` decides what's even worth showing you; a much higher, separate `AUTO_CONFIDENCE_THRESHOLD` gates a *shadow-mode-only* auto-approval policy engine that currently creates nothing on its own — it computes and logs what it *would* do, so the decision logic can be evaluated against real traffic before it's ever allowed to act.

## Architecture

```mermaid
flowchart LR
    subgraph Sources
        GM[Gmail]
        CAL[Google Calendar]
        NO[Notion]
    end

    subgraph Backend["FastAPI — single process"]
        SCHED[APScheduler\n~2-min poll]
        SCAN[Scan\nfetch + store]
        PRE[Preprocess\nstrip HTML / quotes / signatures]
        TRI[Triage\ndeterministic pre-filter]
        EXT[Claude Extraction]
        POL[Policy Engine\nshadow mode]
        SCHAGENT[Scheduling Agent\nread-only availability]
        DB[(PostgreSQL)]
        SSE[SSE endpoint: GET events]
    end

    UI[Next.js Review UI]

    SCHED --> SCAN
    GM -- Gmail MCP --> SCAN --> PRE --> DB
    DB --> TRI --> EXT --> POL --> DB
    EXT -.-> SCHAGENT
    CAL -- Calendar MCP --> SCHAGENT --> DB
    DB --> SSE --> UI
    UI <--> DB
    UI -- explicit approve --> NO
    NO -- Notion MCP --> UI
    UI -- explicit add-to-calendar --> CAL
```

Gmail, Notion, and Calendar are each reached through a small, purpose-built MCP server (`mcp_servers/`) that the backend spawns as a local subprocess over stdio — no new network service per integration, and each server exposes only the two or three tools the pipeline actually needs (e.g. Calendar's server exposes `list_events` and `create_event`, nothing broader).

Everything runs as one FastAPI process (see `app/main.py`). The ~2-minute Gmail poll (`app/scheduler.py`) is an in-process scheduled job that calls the *exact same* scan/extract functions a manual trigger uses — there's one pipeline implementation, not a duplicated "automatic" version and a "manual" version that can drift apart.

## Tech stack

| Layer | Choice | Why |
| --- | --- | --- |
| Backend | FastAPI, single process | The pipeline is a sequence of function calls, not a service mesh — there's no reason for it to be one yet |
| AI | Claude API, structured JSON output | Well-designed prompt + structured output over anything fancier; no fine-tuning, no RAG |
| Scheduling | APScheduler (in-process) | A recurring job inside the process that's already running, not a new Celery/Redis stack |
| Database | PostgreSQL + SQLModel (SQLite for the public demo) | Plain relational schema — six tables, no ORM magic |
| Email/Calendar/Notion | MCP servers over stdio | Each integration is a small, auditable local subprocess with a minimal tool surface |
| Frontend | Next.js (App Router) + React, no UI framework | Small enough not to need one; CSS Modules over the existing design tokens |
| Live updates | Server-Sent Events | One-directional server→client notifications don't need a WebSocket |

## Data model

`users → email_accounts → emails → task_candidates → user_decisions / notion_tasks / calendar_bookings`

Two fields on `task_candidates` are easy to conflate and deliberately aren't: `deadline_phrase` is exactly what Claude extracted ("Friday"), set once and never touched again; `resolved_due_date` is the deterministic, backend-computed calendar date derived from that phrase plus the email's timestamp and the user's timezone — nullable, because guessing wrong is worse than leaving it blank. `user_decisions` is a separate, append-only log of what a human actually did with each candidate, kept distinct from `task_candidates.status` specifically so it survives as a clean dataset for evaluating and improving extraction quality later.

## Running it locally

Fastest path — demo mode, no external services or API keys at all:

```bash
pip install -r requirements.txt
DEMO_MODE=true DATABASE_URL=sqlite:///./demo.db uvicorn app.main:app --reload
```

That starts the backend only, seeded with the same fixed synthetic inbox the public demo uses. See [`docs/SETUP.md`](docs/SETUP.md) for the full setup (Postgres, OAuth, Notion, the review UI, and connecting a real Gmail inbox).

## Validating extraction quality

Before any of the surrounding infrastructure was built, extraction accuracy was tested in isolation — a standalone script (`phase1_extraction/`) with no FastAPI, database, or OAuth dependency, run against a small hand-labeled set of real emails. That's deliberate: extraction quality is the actual risk in a project like this, not the CRUD around it.

```bash
python phase1_extraction/run_experiment.py
```

Historical result on the 15-email hand-labeled set: **100% precision and recall** on the actionable/not-actionable classification (0 false positives, 0 false negatives). This reflects whenever the script was last run with a real API key — it has not been reproduced as part of this change (no key available in this environment) — so treat it as context, not a freshly verified number. Later phases each added their own regression check against this same baseline (`phase1_extraction/run_adversarial_eval.py` for prompt-injection resistance, `run_v25_eval.py` and `run_v26_eval.py` for the correction-learning and scheduling additions) to catch a change that improves one thing while quietly breaking another.

## Results

As of 2026-11-08, no `ANTHROPIC_API_KEY` was available in the environment this change was built in, so the Claude-dependent eval scripts below were **not** re-run as part of this change. What follows is exactly what was and wasn't verified today, with no numbers invented or carried forward as if freshly checked.

**Verified today (fully offline, no API key, deterministic code only):** `phase1_extraction/run_v26_eval.py`'s three non-Claude checks — `calendar_check_correctness()`, `deadline_resolution_fix_check()`, `scheduling_priority_check()` — all **PASS** (run via `python -c` against the individual functions, since the script's 4th check needs a key and would otherwise abort the run). These exercise `app/scheduling.py`'s conflict/alternative-slot logic, `app/deadline_resolver.py`'s date resolution (including the time-bearing-phrase fix), and `app/routes_extract.py`'s meeting-vs-deadline priority rule — the same logic now also covered by `tests/test_scheduling.py` (9 pytest cases, added in this change) and `tests/test_deadline_resolver.py`.

**Not re-run today (require a real Claude API call):**
| Script | Fixture | Size |
| --- | --- | --- |
| `phase1_extraction/run_experiment.py` | `test_emails.jsonl` | 15 emails |
| `phase1_extraction/run_adversarial_eval.py` | `adversarial_test_emails.jsonl` | 9 cases |
| `phase1_extraction/run_v25_eval.py` | `correction_fixture.jsonl` + the 15-email set | 4 + 15 cases |
| `phase1_extraction/run_v25_risky_note_stresstest.py` | 4 hardcoded cases | 4 cases |
| `phase1_extraction/run_v26_eval.py` (`scheduling_field_check` only) | `scheduling_test_emails.jsonl` | 9 cases |

The "100% precision/recall" figure quoted above for `run_experiment.py` is historical — from whenever that script was last run with a real key, not reproduced today. These are small, hand-labeled sets (4–15 cases each), not a production-scale benchmark; anyone picking this up with a real `ANTHROPIC_API_KEY` can reproduce or refresh every number above by running the commands shown in each script's own docstring.

## Design process

See [`CHANGELOG.md`](CHANGELOG.md) for what shipped in each phase. Every phase past the initial MVP was designed before it was built — `docs/DESIGN_DECISIONS_V2.1.md` through `V2.7.md` each lay out the specific questions a phase raised (build vs. adopt an integration, what a "trust policy" should actually gate, how prompt-injection defense changes the rollout order of an otherwise-approved feature) and the reasoning behind each call, ahead of writing any code for it. `CLAUDE.md` captures the standing constraints those decisions operate inside — precision over recall, no background infrastructure until a phase genuinely needs it, nothing auto-created without a human in the loop.

## Security model

- **Auth.** Every route except `GET /health` and the Google OAuth callback requires `Authorization: Bearer <APP_ACCESS_TOKEN>`, checked with a constant-time comparison (`app/auth.py`). Unset or shorter than 32 characters fails every request closed (401) — it never falls back to "no auth required." `GET /events` (Server-Sent Events) accepts the same token as a query parameter too, since the browser's native `EventSource` API can't send custom headers.
- **Human approval is structurally required, not a UI convention.** `POST /candidates/{id}/approve` is the only code path in the codebase that calls the Notion API. There is no flag that routes around it.
- **Injection handling.** Email content reaches Claude inside explicit `<email_content>` tags with an instruction to treat it as untrusted data to analyze, never as instructions to follow. The model's own `injection_suspected` field is a hard override in the policy engine (`app/policy.py`), regardless of extraction confidence.
- **Sender trust.** `app/sender_trust.py` is a deterministic, content-independent check for identity spoofing (e.g. a corporate-directory-style display name with no connection to its free-webmail address) — added because a well-formed, high-confidence request from a spoofed sender passes every content-level check by design. Also a hard override.
- **Rate limits.** `POST /scan` and `POST /extract` are capped (in-process, no Redis — see CLAUDE.md's V1 constraints) so a leaked token can't be used to burn Claude/Gmail API credits in a loop. The automatic ~2-minute poll calls the same underlying functions directly and isn't subject to this limit — its own interval already bounds it.
- **Public demo mode is isolated by construction, not by the same auth boundary.** `DEMO_MODE=true` (used only by the demo service pair in `render.yaml`) makes the auth check pass through unconditionally — there's no real token protecting it — but every write it can reach is fake: synthetic seed emails (`app/demo_data.py`), no Gmail/Notion/Calendar calls, and "approve" writes to an in-memory fake Notion (`app/demo_notion.py`) that's gone on restart. The private instance is unaffected by any of this as long as `DEMO_MODE` is unset there.

## Limitations

Documented honestly rather than omitted:

- **Single-owner, single-process, V1-scoped.** One Gmail account, one Postgres instance, no horizontal scaling (the in-process scheduler and OAuth pending-flow state are both in-memory — see `render.yaml`'s comment on the real API service). This is a deliberate MVP boundary (`CLAUDE.md`), not an oversight.
- **The rate limiter is in-memory and per-process.** It resets on every restart/redeploy and wouldn't coordinate across multiple instances — adequate for a single-instance app, not a general-purpose limiter.
- **`CONFIDENCE_THRESHOLD` and `AUTO_CONFIDENCE_THRESHOLD` are starting values**, tuned against a 15-email hand-labeled set and a small adversarial set (`phase1_extraction/`) — real, reproducible numbers on those sets, not a claim of accuracy at production scale or on inboxes very different from the ones evaluated.
- **The auto-eligible policy decision is shadow-mode only.** It's computed and stored on every candidate for later evaluation; nothing in the codebase currently acts on it — every candidate still requires a human to approve/edit/dismiss it (see `AUTO_ACT_ENABLED` in `app/config.py`).
- **The sender-trust and injection checks are evaluated against a small, hand-built adversarial set** (`phase1_extraction/adversarial_test_emails.jsonl`), not a large or continuously-updated one — they catch the specific patterns that set covers, not every possible spoofing or injection technique.
- **Auth-failure detection in the automatic poller is a best-effort string match** against Google's refresh-error text (`app/scheduler.py`), not a typed exception — documented in that module as a known gap, not verified against a real revoked token as part of this work.
- **The public demo no longer makes live Claude calls.** It runs on SQLite and replays a committed extraction snapshot (`app/demo_snapshot.json`, produced by `scripts/build_demo_snapshot.py`) instead. The snapshot's task/deadline/confidence values are hand-labeled, not live-model output, because no Anthropic API key was available when it was built — it can be regenerated for real by running `scripts/build_demo_snapshot.py` with a real key.
- **The Claude-dependent eval scripts (`run_experiment.py`, `run_adversarial_eval.py`, `run_v25_eval.py`, `run_v25_risky_note_stresstest.py`, and `run_v26_eval.py`'s `scheduling_field_check`) were not re-run as part of this change** — see "Results" above for exactly what was and wasn't verified and why.

## Explicitly out of scope (for now)

Slack/Docs ingestion, meeting transcripts, team-wide commitment tracking, semantic deduplication, fine-tuned models, and general "AI chief of staff" functionality are deliberate non-goals for this stage, not oversights — see `docs/spec.md` §13 for the fuller list. The MVP bar is narrower and concrete: a real Gmail email arrives, the system identifies the right action, a human approves it, and it shows up in Notion with correct context and a working link back to the original email.

## License

See [`LICENSE`](LICENSE).
