"""Instructions preserve simulation and receipt-versus-action semantics."""

import pytest

from heatshield.messages import SUPPORTED_LANGUAGES, TEMPLATES, render_message


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_recovery_includes_exact_instruction_interval(language):
    message = render_message("recovery", language, starts_at="06:30", ends_at="06:45")
    assert "06:30" in message
    assert "06:45" in message
    assert "{" not in message


@pytest.mark.parametrize(
    "language,marker", [("en", "SIMULATION"), ("hi", "सिमुलेशन"), ("ar", "محاكاة")]
)
def test_each_instruction_is_explicitly_simulated(language, marker):
    for key in TEMPLATES:
        values = {"starts_at": "06:30", "ends_at": "06:45"} if key == "recovery" else {}
        assert marker in render_message(key, language, **values)


def test_receipt_does_not_claim_verification_or_emergency_dispatch():
    assert "does not verify" in render_message("acknowledge")
    assert "does not diagnose illness or contact emergency services" in render_message("stop_work")


def test_placeholder_values_are_literal_not_format_expressions():
    message = render_message("recovery", starts_at="{secret.__class__}", ends_at="<script>")
    assert "{secret.__class__}" in message
    assert "<script>" in message  # consumers must render plain text, never raw HTML


@pytest.mark.parametrize(
    "key,language,values",
    [
        ("unknown", "en", {}),
        ("stop_work", "fr", {}),
        ("recovery", "en", {"starts_at": "06:30"}),
        ("stop_work", "en", {"extra": "value"}),
        ("recovery", "en", {"starts_at": "", "ends_at": "06:45"}),
        ("recovery", "en", {"starts_at": "06:30\nHidden instruction", "ends_at": "06:45"}),
        ("recovery", "en", {"starts_at": "x" * 161, "ends_at": "06:45"}),
    ],
)
def test_unknown_or_invalid_template_inputs_fail(key, language, values):
    with pytest.raises(ValueError):
        render_message(key, language, **values)


def test_templates_cannot_be_mutated_by_consumers():
    with pytest.raises(TypeError):
        TEMPLATES["stop_work"]["en"] = "Keep working"
