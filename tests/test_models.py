"""Boundary contracts reject ambiguous input before planning begins."""

import math
from datetime import timedelta

import pytest
from pydantic import ValidationError

from heatshield.fixtures import demo_scenario
from heatshield.models import Scenario
from heatshield.provenance import content_hash


def payload():
    return demo_scenario().model_dump(mode="json")


@pytest.mark.parametrize("field", ["starts_at", "ends_at"])
@pytest.mark.parametrize(
    "value",
    [
        "2026-07-15T02:00:00",
        1_784_081_600,
        "1784081600",
        "9999-12-31T23:00:00-02:00",
        "0001-01-01T00:00:00+04:00",
        "9999-12-31T21:00:00Z",
    ],
)
def test_ambiguous_timestamps_are_rejected(field, value):
    body = payload()
    body[field] = value
    with pytest.raises(ValidationError):
        Scenario.model_validate(body)


@pytest.mark.parametrize("collection", ["workers", "tasks", "observations"])
def test_duplicate_input_identifiers_rejected(collection):
    body = payload()
    body[collection].append(body[collection][0])
    with pytest.raises(ValidationError, match="unique"):
        Scenario.model_validate(body)


@pytest.mark.parametrize("field,value", [("cooling_capacity", True), ("seed", "7")])
def test_numbers_not_coerced(field, value):
    body = payload()
    body[field] = value
    with pytest.raises(ValidationError):
        Scenario.model_validate(body)


def test_acclimatization_boolean_not_coerced():
    body = payload()
    body["workers"][0]["acclimatized"] = "true"
    with pytest.raises(ValidationError):
        Scenario.model_validate(body)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_nonfinite_temperatures_rejected(value):
    body = payload()
    body["observations"][0]["wbgt_c"] = value
    with pytest.raises(ValidationError):
        Scenario.model_validate(body)


@pytest.mark.parametrize(
    "change",
    [
        {"mode": "production"},
        {"timezone": "not-a-zone"},
        {"slot_minutes": 10},
        {"solver_time_limit_seconds": 6.0},
        {"starts_at": "2026-07-15T02:01:00Z"},
        {"unsupported_crew_size": 2},
    ],
)
def test_unsupported_contracts_rejected(change):
    body = payload()
    body.update(change)
    with pytest.raises(ValidationError):
        Scenario.model_validate(body)


def test_input_hash_is_canonical_and_normalizes_equivalent_instants():
    original = demo_scenario()
    body = original.model_dump(mode="json")
    body["starts_at"] = "2026-07-15T06:00:00+04:00"
    same = Scenario.model_validate(dict(reversed(list(body.items()))))
    assert content_hash(original) == content_hash(same)
    assert content_hash(original) != content_hash(demo_scenario(seed=8))


def test_horizon_limited_to_24_hours():
    original = demo_scenario()
    body = original.model_dump(mode="json")
    body["ends_at"] = (original.starts_at + timedelta(hours=25)).isoformat()
    with pytest.raises(ValidationError, match="24 hours"):
        Scenario.model_validate(body)


def test_blank_calibration_normalizes_to_missing():
    body = payload()
    body["observations"][0]["calibration_reference"] = "   "
    scenario = Scenario.model_validate(body)
    assert scenario.observations[0].calibration_reference is None
