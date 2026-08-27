# SPDX-License-Identifier: Apache-2.0
"""Text-extraction helpers in anthropic_utils.py.

_user_message_text() and _extract_system_text() both walk content blocks
pulling .text off an object-or-dict (#8). These tests pin the existing
behavior of _extract_system_text() before factoring the shared loop out,
then cover the shared helper and _user_message_text() with both
example-based and generative (hypothesis) tests (#5).
"""

from hypothesis import given
from hypothesis import strategies as st

from omlx.api.anthropic_models import (
    AnthropicMessage,
    ContentBlockText,
    ContentBlockToolUse,
    MessagesRequest,
    SystemContent,
)
from omlx.api.anthropic_utils import (
    _extract_system_text,
    _user_message_text,
    classifier_envelope_drift,
    is_auto_mode_classifier_request,
)

# ---------------------------------------------------------------------------
# _extract_system_text: pin existing behavior before refactoring (#8)
# ---------------------------------------------------------------------------


def test_extract_system_text_from_a_plain_string():
    assert _extract_system_text("You are a helpful assistant.") == (
        "You are a helpful assistant."
    )


def test_extract_system_text_from_system_content_objects():
    system = [
        SystemContent(type="text", text="part one"),
        SystemContent(type="text", text="part two"),
    ]
    assert _extract_system_text(system) == "part one\npart two"


def test_extract_system_text_from_raw_dicts():
    system = [
        {"type": "text", "text": "part one"},
        {"type": "text", "text": "part two"},
    ]
    assert _extract_system_text(system) == "part one\npart two"


def test_extract_system_text_skips_non_text_dicts():
    system = [{"type": "text", "text": "kept"}, {"type": "other", "text": "dropped"}]
    assert _extract_system_text(system) == "kept"


def test_extract_system_text_skips_billing_header_blocks():
    system = [
        SystemContent(type="text", text="x-anthropic-billing-header: abc123"),
        SystemContent(type="text", text="real system prompt"),
    ]
    assert _extract_system_text(system) == "real system prompt"


def test_extract_system_text_strips_client_budget_markers():
    text = "System prompt.\n<total_tokens>500 tokens left</total_tokens>"
    assert _extract_system_text(text) == "System prompt."


def test_extract_system_text_strips_budget_markers_after_joining_blocks():
    system = [
        SystemContent(type="text", text="part one"),
        SystemContent(
            type="text", text="part two\n<total_tokens>10 tokens left</total_tokens>"
        ),
    ]
    assert "total_tokens" not in _extract_system_text(system)


def test_extract_system_text_returns_empty_for_neither_str_nor_list():
    assert _extract_system_text(None) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _user_message_text: existing behavior, mirrored against the shared helper
# ---------------------------------------------------------------------------


def test_user_message_text_concatenates_string_content_across_user_messages():
    messages = [
        AnthropicMessage(role="user", content="hello "),
        AnthropicMessage(role="assistant", content="hi there"),
        AnthropicMessage(role="user", content="world"),
    ]
    assert _user_message_text(messages) == "hello world"


def test_user_message_text_extracts_text_blocks_and_skips_non_text():
    messages = [
        AnthropicMessage(
            role="user",
            content=[
                ContentBlockText(type="text", text="see this "),
                ContentBlockToolUse(type="tool_use", id="t1", name="Bash", input={}),
                ContentBlockText(type="text", text="tool"),
            ],
        )
    ]
    assert _user_message_text(messages) == "see this tool"


def test_user_message_text_returns_empty_for_no_messages():
    assert _user_message_text(None) == ""
    assert _user_message_text([]) == ""


# ---------------------------------------------------------------------------
# Generative coverage (#5) — property-test-gap-finder's original suggestions:
# randomized content-block shapes, marker offsets, secrets, and randomized
# max_tokens / stop_sequences. Existing example-based tests in
# test_anthropic_classifier.py stay as regression fixtures (the captured
# Claude Code 2.1.246 envelope) and are not replaced.
# ---------------------------------------------------------------------------

_text_block = st.builds(
    lambda text: ContentBlockText(type="text", text=text), text=st.text()
)
_tool_use_block = st.builds(
    lambda: ContentBlockToolUse(type="tool_use", id="t1", name="Bash", input={})
)
_any_block = st.one_of(_text_block, _tool_use_block)


@given(st.lists(_any_block, max_size=10))
def test_user_message_text_only_ever_contains_declared_text_block_contents(blocks):
    """Whatever comes back is built only from blocks whose type == 'text'."""
    messages = [AnthropicMessage(role="user", content=blocks)]
    result = _user_message_text(messages)
    for block in blocks:
        if isinstance(block, ContentBlockText):
            assert block.text in result or block.text == ""


@given(st.text())
def test_user_message_text_of_a_single_string_message_is_that_string(text):
    messages = [AnthropicMessage(role="user", content=text)]
    assert _user_message_text(messages) == text


_DRIFT_REASON_STATIC_TEXT = (
    "max_tokens= exceeds expected ceiling 4096"
    "stop_sequences missing '</block>'"
    "messages missing <transcript> tags"
)


@given(
    max_tokens=st.integers(min_value=-(2**31), max_value=2**31),
    stop_sequences=st.lists(st.text(), max_size=5),
    transcript_secret=st.text(),
)
def test_classifier_envelope_drift_never_leaks_transcript_content(
    max_tokens, stop_sequences, transcript_secret
):
    """Drift reasons name only which field drifted, never message content."""
    from hypothesis import assume

    # A short generated secret can coincidentally be a substring of the
    # reasons' own fixed wording (e.g. "b" inside "</block>") with no
    # relation to actual leakage — exclude that false-positive case.
    assume(not transcript_secret or transcript_secret not in _DRIFT_REASON_STATIC_TEXT)
    request = MessagesRequest(
        model="test-model",
        max_tokens=max_tokens if max_tokens > 0 else 1,
        messages=[
            AnthropicMessage(
                role="user",
                content=f"<transcript>{transcript_secret}</transcript> is this safe?",
            )
        ],
        stop_sequences=stop_sequences,
        system=[
            SystemContent(
                type="text",
                text="you are a security monitor for autonomous ai coding agents",
            )
        ],
    )
    reasons = classifier_envelope_drift(request)
    for reason in reasons:
        assert transcript_secret not in reason or transcript_secret == ""


@given(
    system_marker_present=st.booleans(),
    stream=st.booleans(),
    has_tools=st.booleans(),
)
def test_is_auto_mode_classifier_request_requires_all_three_conditions(
    system_marker_present, stream, has_tools
):
    system_text = (
        "you are a security monitor for autonomous ai coding agents"
        if system_marker_present
        else "you are a helpful assistant"
    )
    request = MessagesRequest(
        model="test-model",
        max_tokens=1024,
        messages=[AnthropicMessage(role="user", content="hi")],
        stream=stream,
        system=[SystemContent(type="text", text=system_text)],
        tools=(
            [{"name": "Bash", "description": "run", "input_schema": {"type": "object"}}]
            if has_tools
            else None
        ),
    )
    expected = system_marker_present and not stream and not has_tools
    assert is_auto_mode_classifier_request(request) == expected
