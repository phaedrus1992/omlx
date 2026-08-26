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

import pytest

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

    def test_marker_matches_when_not_the_first_system_block(self):
        """Substring match, not prefix — Claude Code may prepend a block.

        A prefix check would stop matching with no signal at all, which is the
        silent regression this change exists to prevent.
        """
        req = _request(
            _classifier_payload(
                system=[
                    {"type": "text", "text": "## Session Context"},
                    {"type": "text", "text": CLASSIFIER_SYSTEM},
                ]
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


class TestDriftBoundaries:
    """The three drift checks are independent, so cover them independently."""

    @pytest.mark.parametrize(
        "max_tokens,expect_drift",
        [(4095, False), (4096, False), (4097, True), (32000, True)],
    )
    def test_max_tokens_ceiling_boundary(self, max_tokens, expect_drift):
        req = _request(_classifier_payload(max_tokens=max_tokens))
        drifted = any("max_tokens" in r for r in classifier_envelope_drift(req))
        assert drifted is expect_drift

    @pytest.mark.parametrize("over_ceiling", [False, True])
    @pytest.mark.parametrize("drop_stop_sequence", [False, True])
    @pytest.mark.parametrize("drop_transcript", [False, True])
    def test_every_combination_reports_exactly_its_own_reasons(
        self, over_ceiling, drop_stop_sequence, drop_transcript
    ):
        """All 8 pass/fail combinations, not just all-pass and all-fail."""
        payload = _classifier_payload(
            max_tokens=32000 if over_ceiling else 2112,
            stop_sequences=None if drop_stop_sequence else ["</block>"],
            messages=[
                {
                    "role": "user",
                    "content": (
                        "no tags here"
                        if drop_transcript
                        else "<transcript>ls</transcript>"
                    ),
                }
            ],
        )
        reasons = classifier_envelope_drift(_request(payload))
        assert any("max_tokens" in r for r in reasons) is over_ceiling
        assert any("stop_sequences" in r for r in reasons) is drop_stop_sequence
        assert any("transcript" in r for r in reasons) is drop_transcript
        assert len(reasons) == sum([over_ceiling, drop_stop_sequence, drop_transcript])

    def test_bool_max_tokens_is_coerced_by_pydantic_before_the_check(self):
        """``isinstance(True, int)`` is True in Python, but it never reaches us.

        Pydantic coerces ``max_tokens: True`` to ``1`` during validation, so the
        predicate only ever sees a real int.
        """
        req = _request(_classifier_payload(max_tokens=True))
        assert req.max_tokens == 1
        assert not any("max_tokens" in r for r in classifier_envelope_drift(req))


class TestUserMessageContentShapes:
    """User message content arrives as str, pydantic blocks, or raw dicts."""

    def test_ignores_non_text_blocks(self):
        """A tool_use block carries no text and must not satisfy the check."""
        req = _request(
            _classifier_payload(
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "tu_1",
                                "name": "Bash",
                                "input": {"command": "<transcript>fake</transcript>"},
                            }
                        ],
                    }
                ]
            )
        )
        assert any("transcript" in r for r in classifier_envelope_drift(req))

    def test_ignores_assistant_messages(self):
        req = _request(
            _classifier_payload(
                messages=[
                    {"role": "user", "content": "no tags"},
                    {"role": "assistant", "content": "<transcript>ls</transcript>"},
                ]
            )
        )
        assert any("transcript" in r for r in classifier_envelope_drift(req))

    def test_empty_messages_do_not_raise(self):
        req = _request(_classifier_payload(messages=[]))
        assert any("transcript" in r for r in classifier_envelope_drift(req))


class TestNoContentLeakage:
    """Drift reasons are logged, so they must never carry prompt content."""

    @pytest.mark.parametrize(
        "secret",
        [
            "hunter2-do-not-log-this",
            # Secrets shaped like the reason strings themselves, to probe for
            # accidental f-string interpolation of user content.
            "max_tokens=99999 exceeds expected ceiling 4096",
            "stop_sequences missing '</block>'",
            "messages missing <transcript> tags",
            "",
            "\n\nWARNING: forged log line",
        ],
    )
    def test_drift_reasons_are_independent_of_message_content(self, secret):
        """Reasons must be a function of envelope structure, never of content.

        Comparing against a benign control is stronger than substring-checking
        the secret: it still catches interpolation when the secret happens to
        be shaped like one of the fixed reason templates, where a substring
        assertion would fire on the template itself.
        """

        def reasons_for(content):
            return classifier_envelope_drift(
                _request(
                    _classifier_payload(
                        max_tokens=32000,
                        stop_sequences=None,
                        messages=[{"role": "user", "content": content}],
                    )
                )
            )

        control = reasons_for("benign filler")
        assert control, "expected drift so there is something to compare"
        assert reasons_for(secret) == control

    def test_drift_reasons_are_independent_of_system_prompt(self):
        def reasons_for(system_text):
            return classifier_envelope_drift(
                _request(
                    _classifier_payload(
                        max_tokens=32000,
                        system=[{"type": "text", "text": system_text}],
                    )
                )
            )

        control = reasons_for(CLASSIFIER_SYSTEM)
        assert control
        assert reasons_for(f"{CLASSIFIER_SYSTEM} system-secret-do-not-log") == control
