# SPDX-License-Identifier: Apache-2.0
"""
Tests for Claude Code auto-mode safety-classifier detection.

Claude Code sends an internal Bash safety-classifier request that carries no
``thinking`` field at all. On a reasoning model oMLX therefore falls through to
the model default (thinking on), the model reasons past Claude Code's ~35-45s
deadline, and the request is cancelled — surfacing to the user as "model is
temporarily unavailable" (see issue #1).

The envelopes below are transcribed from a live capture against Claude Code
2.1.246, not invented.
"""

from omlx.api.anthropic_models import MessagesRequest
from omlx.api.anthropic_utils import (
    classifier_envelope_drift,
    is_auto_mode_classifier_request,
)

CLASSIFIER_SYSTEM = (
    "You are a security monitor for autonomous AI coding agents. "
    "Decide whether the proposed action is safe."
)


def _classifier_payload(**overrides):
    """The real captured classifier envelope, with optional field overrides."""
    payload = {
        "model": "Qwen3.6-35B-A3B-OptiQ-4bit",
        "max_tokens": 2112,
        "stream": False,
        "stop_sequences": ["</block>"],
        "system": [{"type": "text", "text": CLASSIFIER_SYSTEM}],
        "messages": [
            {
                "role": "user",
                "content": "<transcript>rm /tmp/foo.txt</transcript> Is this safe?",
            }
        ],
    }
    payload.update(overrides)
    return payload


def _normal_turn_payload(**overrides):
    """A normal Claude Code conversation turn, which must never match."""
    payload = {
        "model": "Qwen3.6-35B-A3B-OptiQ-4bit",
        "max_tokens": 32000,
        "stream": True,
        "thinking": {"type": "adaptive"},
        "system": [
            {"type": "text", "text": "You are Claude Code, Anthropic's official CLI for Claude."}
        ],
        "messages": [{"role": "user", "content": "what does this function do?"}],
        "tools": [
            {"name": "Bash", "description": "run a command", "input_schema": {"type": "object"}}
        ],
    }
    payload.update(overrides)
    return payload


def _request(payload) -> MessagesRequest:
    return MessagesRequest.model_validate(payload)


class TestIsAutoModeClassifierRequest:
    """The predicate must match the classifier and nothing else."""

    def test_matches_real_captured_envelope(self):
        assert is_auto_mode_classifier_request(_request(_classifier_payload())) is True

    def test_matches_when_system_is_a_bare_string(self):
        req = _request(_classifier_payload(system=CLASSIFIER_SYSTEM))
        assert is_auto_mode_classifier_request(req) is True

    def test_marker_match_is_case_insensitive(self):
        req = _request(
            _classifier_payload(
                system=[{"type": "text", "text": CLASSIFIER_SYSTEM.upper()}]
            )
        )
        assert is_auto_mode_classifier_request(req) is True

    def test_rejects_normal_turn(self):
        assert is_auto_mode_classifier_request(_request(_normal_turn_payload())) is False

    def test_rejects_streaming_request(self):
        """A streaming request is never the classifier — it is non-streaming."""
        req = _request(_classifier_payload(stream=True))
        assert is_auto_mode_classifier_request(req) is False

    def test_rejects_request_carrying_tools(self):
        """A real conversation turn discussing the classifier still carries tools."""
        req = _request(
            _classifier_payload(
                tools=[
                    {
                        "name": "Bash",
                        "description": "run a command",
                        "input_schema": {"type": "object"},
                    }
                ]
            )
        )
        assert is_auto_mode_classifier_request(req) is False

    def test_empty_tools_list_still_matches(self):
        """``tools: []`` is absence of tools, not presence."""
        req = _request(_classifier_payload(tools=[]))
        assert is_auto_mode_classifier_request(req) is True

    def test_rejects_missing_marker(self):
        req = _request(
            _classifier_payload(system=[{"type": "text", "text": "You are a helpful assistant."}])
        )
        assert is_auto_mode_classifier_request(req) is False

    def test_rejects_billing_header_alone(self):
        """Regression guard for issue #1.

        The legacy ``x-anthropic-billing-header:`` marker appears on ordinary
        Agent SDK turns, not just the classifier. Matching on it alone would
        disable thinking on every request.
        """
        req = _request(
            _normal_turn_payload(
                system=[
                    {
                        "type": "text",
                        "text": "x-anthropic-billing-header: cc_version=2.1.246",
                    },
                    {"type": "text", "text": "You are a Claude agent."},
                ],
                stream=False,
                tools=[],
            )
        )
        assert is_auto_mode_classifier_request(req) is False

    def test_handles_absent_system(self):
        req = _request(_classifier_payload(system=None))
        assert is_auto_mode_classifier_request(req) is False


class TestClassifierEnvelopeDrift:
    """Drift reporting is the early warning that Anthropic changed the format."""

    def test_captured_envelope_reports_no_drift(self):
        assert classifier_envelope_drift(_request(_classifier_payload())) == []

    def test_reports_oversized_max_tokens(self):
        drift = classifier_envelope_drift(_request(_classifier_payload(max_tokens=32000)))
        assert any("max_tokens" in reason for reason in drift)

    def test_reports_missing_stop_sequence(self):
        drift = classifier_envelope_drift(_request(_classifier_payload(stop_sequences=None)))
        assert any("stop_sequences" in reason for reason in drift)

    def test_reports_missing_transcript_tags(self):
        req = _request(
            _classifier_payload(
                messages=[{"role": "user", "content": "is rm /tmp/foo.txt safe?"}]
            )
        )
        drift = classifier_envelope_drift(req)
        assert any("transcript" in reason for reason in drift)

    def test_finds_transcript_in_structured_content_blocks(self):
        req = _request(
            _classifier_payload(
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "<transcript>ls</transcript>"},
                        ],
                    }
                ]
            )
        )
        assert classifier_envelope_drift(req) == []

    def test_accumulates_multiple_drift_reasons(self):
        req = _request(
            _classifier_payload(
                max_tokens=32000,
                stop_sequences=None,
                messages=[{"role": "user", "content": "no tags here"}],
            )
        )
        assert len(classifier_envelope_drift(req)) == 3


class TestNoContentLeakage:
    """Drift reasons are logged, so they must never carry prompt content."""

    def test_drift_reasons_exclude_prompt_text(self):
        secret = "hunter2-do-not-log-this"
        req = _request(
            _classifier_payload(
                max_tokens=32000,
                stop_sequences=None,
                messages=[{"role": "user", "content": secret}],
            )
        )
        for reason in classifier_envelope_drift(req):
            assert secret not in reason
