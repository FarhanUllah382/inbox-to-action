# Local setup

**Prerequisites:** Python 3.11+, Node 18+, Docker (for Postgres), a Claude API key, Google OAuth credentials, and a Notion integration token + database ID.

```bash
# 1. Install dependencies
pip install -r requirements.txt
cd web && npm install && cd ..

# 2. Configure environment
cp .env.example .env                          # fill in ANTHROPIC_API_KEY, APP_ACCESS_TOKEN, GOOGLE_CLIENT_ID/SECRET, NOTION_*, USER_TIMEZONE
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

## Running the demo mode locally (no Gmail/Notion/Calendar/Claude key needed)

Set `DEMO_MODE=true` in `.env` and skip the Google/Notion/Anthropic variables entirely. The backend seeds a fixed synthetic inbox and replays the committed extraction snapshot (`app/demo_snapshot.json`) instead of calling Claude — see `scripts/build_demo_snapshot.py` if you need to regenerate that snapshot with a real key.
