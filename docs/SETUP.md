# Local setup

**Prerequisites:** Python 3.11+, Node 18+, Docker (for Postgres), an API key for the LLM provider you're using (Claude or Gemini — see below), Google OAuth credentials, and a Notion integration token + database ID.

```bash
# 1. Install dependencies
pip install -r requirements.txt
cd web && npm install && cd ..

# 2. Configure environment
cp .env.example .env                          # fill in LLM_PROVIDER + its key (ANTHROPIC_API_KEY or GEMINI_API_KEY), APP_ACCESS_TOKEN, GOOGLE_CLIENT_ID/SECRET, NOTION_*, USER_TIMEZONE
cp web/.env.local.example web/.env.local       # default (http://localhost:8000) is fine unless the backend runs elsewhere

# 3. Start Postgres
docker compose up -d

# 4. Start the backend (also starts the ~2-minute auto-poll scheduler)
uvicorn app.main:app --reload

# 5. Start the review UI (separate terminal)
cd web && npm run dev
```

Every route except `/health` and the Google OAuth callback requires the `APP_ACCESS_TOKEN` you set in step 2 (see README's "Security model") — the review UI will prompt for it once, in a plain login screen, and keep it in `sessionStorage` for that browser tab. Generate one with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Open `http://localhost:3000`, connect Gmail via the login flow, and the pipeline runs on its own from there — a "Scan Gmail now" / "Extract tasks now" pair of buttons is also available for on-demand testing without waiting for the next poll.

## Running the demo mode locally (no Gmail/Notion/Calendar/LLM key needed)

Set `DEMO_MODE=true` in `.env` and skip the Google/Notion/LLM-provider variables entirely. The backend seeds a fixed synthetic inbox and replays the committed extraction snapshot (`app/demo_snapshot.json`) instead of calling any model — see `scripts/build_demo_snapshot.py` if you need to regenerate that snapshot with a real key.

## Choosing an LLM provider

`LLM_PROVIDER` (`.env`) selects which model `phase1_extraction/extractor.py` calls — `anthropic` (default) or `gemini`. Both paths return the exact same validated JSON schema, so nothing downstream needs to know which one is active. Only the chosen provider's key needs to be set:

- `anthropic`: `ANTHROPIC_API_KEY`
- `gemini`: `GEMINI_API_KEY`, optionally `GEMINI_MODEL` (defaults to `gemini-2.5-flash` — the current API's own "stable" Flash release; see `phase1_extraction/config.py`'s comment for how that was confirmed rather than guessed)

A missing key for whichever provider is selected fails closed at startup (`app/main.py`), except in `DEMO_MODE`, which needs no key of either kind.

## How the web app detects demo mode

`web/app/page.tsx` calls `GET /api/info` (not `/health` — some browser ad/privacy blockers block any request whose URL contains "health", which silently broke demo detection for visitors running one). The public demo build sets `NEXT_PUBLIC_DEMO_MODE=true` at build time (`render.yaml`, `inbox-to-action-demo-web` only) to skip that call entirely; everywhere else, the app retries `/api/info` every 3s for up to 90s (to ride out a cold Render free-tier instance waking up) before showing a "didn't respond" screen with a Retry button, instead of falling back to the login screen.
