"""
Phase 1 Configuration
"""

import os
from dotenv import load_dotenv

load_dotenv()

# Which LLM extractor.py calls — "anthropic" (default, matches every prior
# phase's behavior unchanged) or "gemini". Duplicated from app/config.py on
# purpose, same convention as ANTHROPIC_API_KEY/ANTHROPIC_MODEL below — this
# module must stay importable with no app/FastAPI/DB dependency (Phase 1
# isolation, see PHASE1_README.md).
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()

# Anthropic API
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = "claude-sonnet-5"

# Gemini (google-genai SDK) — alternate provider, selected via LLM_PROVIDER.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# Confirmed directly against the live API (client.models.list()), not
# guessed: as of this writing, "gemini-2.5-flash" is the only Flash model
# whose own description explicitly says "Stable version of Gemini 2.5
# Flash" — several newer-numbered Flash models exist but aren't labeled
# stable in the API's own listing. Override via GEMINI_MODEL once a later
# model earns that same explicit label.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Extraction parameters
THREAD_CONTEXT_DEPTH = 5
CONFIDENCE_THRESHOLD = 0.7  # Initial experimental threshold, tuned based on evaluation

# Test dataset
TEST_EMAILS_PATH = "phase1_extraction/test_emails.jsonl"

# Results output
RESULTS_OUTPUT_PATH = "phase1_extraction/evaluation_results.json"

print(f"Configuration loaded: {ANTHROPIC_MODEL}")
