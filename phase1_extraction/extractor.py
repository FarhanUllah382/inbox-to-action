"""
LLM Extractor

Calls either the Claude or Gemini API with the extraction prompt and
returns structured JSON, selected via LLM_PROVIDER (phase1_extraction/config.py
-- default "anthropic", so every prior phase's behavior is unchanged unless
this is explicitly set to "gemini"). Both providers return output validated
against the exact same ExtractionResult schema, so every caller downstream
(app/routes_extract.py, the eval scripts) stays provider-agnostic.

Optionally accepts correction_notes (Design Decisions V2.5, Decision 4) —
a plain list of strings the *caller* fetches from the correction_notes
table (see app/correction_notes.py) and passes in. This module never
queries the database itself, so it stays exactly what it's always been: a
standalone, DB-independent module phase1_extraction/run_experiment.py can
run with nothing but an LLM API key (PHASE1_README.md's Phase 1 isolation
requirement). When correction_notes is None or empty (the default), the
call is byte-identical to pre-V2.5 behavior — extraction_prompt.py itself
is never touched; notes are appended to the already-built user prompt.
"""

import json
import re
import sys
import time
from pathlib import Path
from typing import Optional, Dict, Any, List

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from pydantic import BaseModel, ValidationError

from phase1_extraction.extraction_prompt import EXTRACTION_SYSTEM_PROMPT, build_user_prompt
from phase1_extraction.config import (
    LLM_PROVIDER,
    ANTHROPIC_API_KEY,
    ANTHROPIC_MODEL,
    GEMINI_API_KEY,
    GEMINI_MODEL,
)

# Gemini free-tier rate limiting (phase1_extraction/extractor.py's
# _extract_gemini) — generous enough for the per-minute quota observed on
# the free tier (5 requests/minute for gemini-2.5-flash as of this
# writing) to actually clear between retries, without retrying forever.
_GEMINI_MAX_RETRIES = 5
_GEMINI_DEFAULT_RETRY_SECONDS = 65.0
_RETRY_DELAY_RE = re.compile(r"retry in ([\d.]+)s", re.IGNORECASE)


def _parse_gemini_retry_delay(error: Exception) -> Optional[float]:
    """Best-effort extraction of the API's own suggested wait time from its
    error message (e.g. "...Please retry in 55.88s"). Returns None if the
    message doesn't contain one, so the caller falls back to a fixed delay
    instead of guessing a number from an unexpected message shape."""
    match = _RETRY_DELAY_RE.search(str(error))
    return float(match.group(1)) if match else None


NOTES_BLOCK_TEMPLATE = """

Notes from this user's past corrections:
{notes}

These notes describe how this user has corrected extraction judgment calls before — apply them as guidance where relevant. Return ONLY a valid JSON object with no additional text."""


def _append_correction_notes(user_prompt: str, correction_notes: Optional[List[str]]) -> str:
    """Appends a distinct, clearly-labeled notes block (Decision 4) — never
    edits extraction_prompt.py's locked base content. Restates the
    JSON-only instruction at the end so the model's last-seen instruction
    is still the output-format rule, regardless of prompt wording changes."""
    if not correction_notes:
        return user_prompt
    notes_text = "\n".join(f"- {note}" for note in correction_notes)
    return user_prompt + NOTES_BLOCK_TEMPLATE.format(notes=notes_text)


class ExtractionError(Exception):
    """Error during extraction — covers API failures, unparseable output,
    and output that fails schema validation, for either provider."""
    pass


class ExtractionResult(BaseModel):
    """The one JSON contract this project returns regardless of model
    (CLAUDE.md's "Claude extraction contract", provider-neutral now).
    Both the Claude and Gemini paths validate their parsed response against
    this exact schema before returning, so nothing downstream needs to
    know which provider produced a given result.

    `injection_suspected` defaults to True (fail-closed) rather than being
    strictly required, matching the pre-existing defensive default
    app/routes_extract.py already applied for a response that omitted it
    (`result.get("injection_suspected", True)`) — a missing safety signal
    is treated as suspect, not silently ignored."""

    actionable: bool
    task: Optional[str] = None
    deadline: Optional[str] = None
    assignee: Optional[str] = None
    reason: str
    confidence: float
    injection_suspected: bool = True
    proposed_meeting_time: Optional[str] = None


def _validate_result(raw: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return ExtractionResult.model_validate(raw).model_dump()
    except ValidationError as e:
        raise ExtractionError(f"Model output failed schema validation: {e}\nRaw: {raw}")


def validate_provider_config() -> None:
    """Fail-closed check for the configured LLM_PROVIDER's key — presence
    only, no SDK client construction and no network call. Exists so
    app/main.py's startup can check this eagerly (DEMO_MODE needs no key
    at all and skips this) without also depending on whatever constructing
    a real provider client happens to do — see Extractor.__init__, which
    calls this same check before doing that."""
    if LLM_PROVIDER == "anthropic":
        if not ANTHROPIC_API_KEY:
            raise ExtractionError(
                "ANTHROPIC_API_KEY is not set but LLM_PROVIDER=anthropic "
                "(the default) — set the key, or set LLM_PROVIDER=gemini "
                "with GEMINI_API_KEY instead."
            )
    elif LLM_PROVIDER == "gemini":
        if not GEMINI_API_KEY:
            raise ExtractionError(
                "GEMINI_API_KEY is not set but LLM_PROVIDER=gemini — set "
                "the key, or set LLM_PROVIDER=anthropic (the default) with "
                "ANTHROPIC_API_KEY instead."
            )
    else:
        raise ExtractionError(
            f"Unknown LLM_PROVIDER={LLM_PROVIDER!r} — expected 'anthropic' or 'gemini'."
        )


class Extractor:
    """Wrapper around the configured LLM provider for email extraction."""

    def __init__(self):
        validate_provider_config()
        self.provider = LLM_PROVIDER

        if self.provider == "anthropic":
            import httpx2
            from anthropic import Anthropic

            # Some hosts (seen on Render's free tier) resolve api.anthropic.com's
            # AAAA record but have no working outbound IPv6 route, so the client
            # hangs ~30-40s per call before failing with a generic "Connection
            # error." Forcing the client to bind IPv4 skips the dead IPv6 attempt
            # entirely. anthropic>=1.0 depends on the `httpx2` package (its httpx
            # successor), not `httpx` — the client passed to `http_client=` must
            # be an httpx2.Client.
            self._ipv4_http_client = httpx2.Client(transport=httpx2.HTTPTransport(local_address="0.0.0.0"))
            self.client = Anthropic(api_key=ANTHROPIC_API_KEY, http_client=self._ipv4_http_client)
            self.model = ANTHROPIC_MODEL

        else:  # "gemini" — validate_provider_config() already ruled out anything else
            from google import genai

            self.client = genai.Client(api_key=GEMINI_API_KEY)
            self.model = GEMINI_MODEL

    def extract(
        self,
        email_data: Dict[str, str],
        thread_context: str = "",
        correction_notes: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Extract structured task data from an email.

        Args:
            email_data: dict with 'from', 'subject', 'body'
            thread_context: optional recent thread context
            correction_notes: optional list of approved correction note
                strings, fetched by the caller (never by this module)

        Returns:
            dict matching ExtractionResult's schema, regardless of provider

        Raises:
            ExtractionError: if extraction fails, output can't be parsed,
                or output fails schema validation
        """
        user_prompt = build_user_prompt(email_data, thread_context)
        user_prompt = _append_correction_notes(user_prompt, correction_notes)

        try:
            if self.provider == "gemini":
                raw = self._extract_gemini(user_prompt)
            else:
                raw = self._extract_anthropic(user_prompt)
        except ExtractionError:
            raise
        except Exception as e:
            raise ExtractionError(f"Unexpected error during extraction: {e}")

        return _validate_result(raw)

    def _extract_anthropic(self, user_prompt: str) -> Dict[str, Any]:
        """Unchanged from the pre-Gemini implementation: prompt-based JSON,
        manually parsed. Schema validation now happens once, in extract(),
        shared with the Gemini path — this just has to return a dict."""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=500,
            system=EXTRACTION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        # Extract response text. content[0] isn't always the text block —
        # the model can prepend a ThinkingBlock, which has no .text attr.
        text_block = next((b for b in response.content if b.type == "text"), None)
        if text_block is None:
            raise ExtractionError(
                f"No text block in Claude response. Block types: "
                f"{[b.type for b in response.content]}"
            )
        response_text = text_block.text.strip()

        # Remove markdown code fence if present
        if response_text.startswith("```json"):
            response_text = response_text[7:]  # Remove ```json
        if response_text.startswith("```"):
            response_text = response_text[3:]  # Remove ```
        if response_text.endswith("```"):
            response_text = response_text[:-3]  # Remove trailing ```

        response_text = response_text.strip()

        try:
            return json.loads(response_text)
        except json.JSONDecodeError as e:
            raise ExtractionError(
                f"Failed to parse Claude response as JSON. "
                f"Response: {response_text[:200]}\nError: {e}"
            )

    def _extract_gemini(self, user_prompt: str) -> Dict[str, Any]:
        """Gemini's native structured-output mode: response_schema pins the
        model to ExtractionResult's shape server-side, so there's no
        markdown-fence-stripping step like the Claude path needs — the SDK
        already guarantees response.text is a JSON string matching the
        schema. Still goes through the same _validate_result() afterward,
        same as Claude, rather than trusting the SDK's guarantee blindly.

        Retries on a 429 (free-tier quota is 5 requests/minute as of this
        writing) or a 503 (transient "model overloaded" — observed in
        practice, unrelated to quota) rather than failing the case
        outright. The API's own error message sometimes includes a
        suggested retry delay ("...retry in 55.8s"); parsed when present,
        with a generous fixed fallback otherwise (503s don't come with
        one). Any other error propagates immediately, unretried."""
        from google.genai import types
        from google.genai.errors import APIError

        response = None
        last_error = None
        for attempt in range(_GEMINI_MAX_RETRIES):
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=EXTRACTION_SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        response_schema=ExtractionResult,
                    ),
                )
                break
            except APIError as e:
                if getattr(e, "code", None) not in (429, 503) or attempt == _GEMINI_MAX_RETRIES - 1:
                    raise
                wait_seconds = _parse_gemini_retry_delay(e) or _GEMINI_DEFAULT_RETRY_SECONDS
                print(
                    f"[extractor] Gemini rate limit hit (attempt {attempt + 1}/"
                    f"{_GEMINI_MAX_RETRIES}), retrying in {wait_seconds:.0f}s..."
                )
                time.sleep(wait_seconds)
                last_error = e

        if response is None:
            raise ExtractionError(f"Gemini rate limit exceeded after {_GEMINI_MAX_RETRIES} attempts: {last_error}")

        if not response.text:
            raise ExtractionError("Empty response from Gemini")

        try:
            return json.loads(response.text)
        except json.JSONDecodeError as e:
            raise ExtractionError(
                f"Failed to parse Gemini response as JSON. "
                f"Response: {response.text[:200]}\nError: {e}"
            )


def extract_email(
    email_data: Dict[str, str],
    thread_context: str = "",
    correction_notes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Convenience function to extract an email.

    Args:
        email_data: dict with 'from', 'subject', 'body'
        thread_context: optional recent thread context
        correction_notes: optional list of approved correction note strings

    Returns:
        dict with extraction result
    """
    extractor = Extractor()
    return extractor.extract(email_data, thread_context, correction_notes)
