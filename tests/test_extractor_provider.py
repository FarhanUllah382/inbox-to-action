"""
Tests for phase1_extraction/extractor.py's provider abstraction
(LLM_PROVIDER = "anthropic" | "gemini").

Every test here mocks the relevant SDK entry point directly (anthropic.Anthropic
/ google.genai.Client) regardless of which API keys happen to be set in the
ambient environment — this file must never make a real network call, in CI
or anywhere else, even if a real GEMINI_API_KEY/ANTHROPIC_API_KEY is present.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from phase1_extraction.extractor import Extractor, ExtractionError, validate_provider_config

VALID_RESULT = {
    "actionable": True,
    "task": "Update the pricing page",
    "deadline": "Friday",
    "assignee": None,
    "reason": "Direct request with a deadline.",
    "confidence": 0.95,
    "injection_suspected": False,
    "proposed_meeting_time": None,
}


def _anthropic_response(payload: dict):
    """Shape of a real anthropic.Message good enough for extractor.py's
    text-block lookup + markdown-fence stripping."""
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(payload))])


def _gemini_response(payload: dict):
    return SimpleNamespace(text=json.dumps(payload))


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------


def test_anthropic_provider_constructs_anthropic_client(monkeypatch):
    monkeypatch.setattr("phase1_extraction.extractor.LLM_PROVIDER", "anthropic")
    monkeypatch.setattr("phase1_extraction.extractor.ANTHROPIC_API_KEY", "fake-key")

    mock_client_class = MagicMock()
    monkeypatch.setattr("anthropic.Anthropic", mock_client_class)

    extractor = Extractor()
    assert extractor.provider == "anthropic"
    mock_client_class.assert_called_once()


def test_gemini_provider_constructs_gemini_client(monkeypatch):
    monkeypatch.setattr("phase1_extraction.extractor.LLM_PROVIDER", "gemini")
    monkeypatch.setattr("phase1_extraction.extractor.GEMINI_API_KEY", "fake-key")

    mock_client_class = MagicMock()
    monkeypatch.setattr("google.genai.Client", mock_client_class)

    extractor = Extractor()
    assert extractor.provider == "gemini"
    mock_client_class.assert_called_once_with(api_key="fake-key")


def test_default_provider_is_anthropic(monkeypatch):
    # phase1_extraction/config.py's os.getenv("LLM_PROVIDER", "anthropic")
    # default — verified directly against the parsing, not the already-
    # imported module singleton (which is fixed at first import).
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    import importlib
    import phase1_extraction.config as config

    importlib.reload(config)
    assert config.LLM_PROVIDER == "anthropic"


# ---------------------------------------------------------------------------
# Fail-closed on a missing key / unknown provider
# ---------------------------------------------------------------------------


def test_missing_anthropic_key_fails_clearly(monkeypatch):
    monkeypatch.setattr("phase1_extraction.extractor.LLM_PROVIDER", "anthropic")
    monkeypatch.setattr("phase1_extraction.extractor.ANTHROPIC_API_KEY", None)

    with pytest.raises(ExtractionError, match="ANTHROPIC_API_KEY"):
        Extractor()


def test_missing_gemini_key_fails_clearly(monkeypatch):
    monkeypatch.setattr("phase1_extraction.extractor.LLM_PROVIDER", "gemini")
    monkeypatch.setattr("phase1_extraction.extractor.GEMINI_API_KEY", None)

    with pytest.raises(ExtractionError, match="GEMINI_API_KEY"):
        Extractor()


def test_unknown_provider_fails_clearly(monkeypatch):
    monkeypatch.setattr("phase1_extraction.extractor.LLM_PROVIDER", "openai")

    with pytest.raises(ExtractionError, match="Unknown LLM_PROVIDER"):
        validate_provider_config()
    with pytest.raises(ExtractionError, match="Unknown LLM_PROVIDER"):
        Extractor()


# ---------------------------------------------------------------------------
# Both paths return the exact same schema
# ---------------------------------------------------------------------------


def test_anthropic_and_gemini_paths_return_same_schema(monkeypatch):
    monkeypatch.setattr("phase1_extraction.extractor.LLM_PROVIDER", "anthropic")
    monkeypatch.setattr("phase1_extraction.extractor.ANTHROPIC_API_KEY", "fake-key")
    mock_anthropic_client = MagicMock()
    mock_anthropic_client.messages.create.return_value = _anthropic_response(VALID_RESULT)
    monkeypatch.setattr("anthropic.Anthropic", MagicMock(return_value=mock_anthropic_client))

    anthropic_result = Extractor().extract({"from": "a@b.com", "subject": "s", "body": "b"})

    monkeypatch.setattr("phase1_extraction.extractor.LLM_PROVIDER", "gemini")
    monkeypatch.setattr("phase1_extraction.extractor.GEMINI_API_KEY", "fake-key")
    mock_gemini_client = MagicMock()
    mock_gemini_client.models.generate_content.return_value = _gemini_response(VALID_RESULT)
    monkeypatch.setattr("google.genai.Client", MagicMock(return_value=mock_gemini_client))

    gemini_result = Extractor().extract({"from": "a@b.com", "subject": "s", "body": "b"})

    assert set(anthropic_result.keys()) == set(gemini_result.keys()) == set(VALID_RESULT.keys())
    for key in VALID_RESULT:
        assert type(anthropic_result[key]) == type(gemini_result[key])  # noqa: E721
    assert anthropic_result == gemini_result == VALID_RESULT


# ---------------------------------------------------------------------------
# Invalid model output is rejected, same way for both providers
# ---------------------------------------------------------------------------


def test_anthropic_invalid_output_missing_required_field_rejected(monkeypatch):
    monkeypatch.setattr("phase1_extraction.extractor.LLM_PROVIDER", "anthropic")
    monkeypatch.setattr("phase1_extraction.extractor.ANTHROPIC_API_KEY", "fake-key")
    bad_payload = {k: v for k, v in VALID_RESULT.items() if k != "reason"}  # drop required field
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _anthropic_response(bad_payload)
    monkeypatch.setattr("anthropic.Anthropic", MagicMock(return_value=mock_client))

    with pytest.raises(ExtractionError, match="schema validation"):
        Extractor().extract({"from": "a@b.com", "subject": "s", "body": "b"})


def test_gemini_invalid_output_missing_required_field_rejected(monkeypatch):
    monkeypatch.setattr("phase1_extraction.extractor.LLM_PROVIDER", "gemini")
    monkeypatch.setattr("phase1_extraction.extractor.GEMINI_API_KEY", "fake-key")
    bad_payload = {k: v for k, v in VALID_RESULT.items() if k != "reason"}
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = _gemini_response(bad_payload)
    monkeypatch.setattr("google.genai.Client", MagicMock(return_value=mock_client))

    with pytest.raises(ExtractionError, match="schema validation"):
        Extractor().extract({"from": "a@b.com", "subject": "s", "body": "b"})


def test_missing_injection_suspected_defaults_fail_closed(monkeypatch):
    """injection_suspected has a fail-closed default (True) rather than
    being strictly required — matching the pre-existing app-level
    convention this schema formalizes (see ExtractionResult's docstring)."""
    monkeypatch.setattr("phase1_extraction.extractor.LLM_PROVIDER", "anthropic")
    monkeypatch.setattr("phase1_extraction.extractor.ANTHROPIC_API_KEY", "fake-key")
    payload = {k: v for k, v in VALID_RESULT.items() if k != "injection_suspected"}
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _anthropic_response(payload)
    monkeypatch.setattr("anthropic.Anthropic", MagicMock(return_value=mock_client))

    result = Extractor().extract({"from": "a@b.com", "subject": "s", "body": "b"})
    assert result["injection_suspected"] is True
