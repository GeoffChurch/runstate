"""Validate that all messages our library produces conform to the JSON Schema.

The schema is the canonical wire-format definition. The Python helpers
are the reference implementation; this test verifies that everything
they emit is protocol-conformant.
"""

import json
from dataclasses import asdict
from pathlib import Path

import pytest
import jsonschema

from runstate import control, events


SCHEMA_PATH = Path(__file__).parent.parent / "protocol" / "messages-v0.1.schema.json"


@pytest.fixture(scope="module")
def schema():
    with SCHEMA_PATH.open() as f:
        return json.load(f)


@pytest.fixture(scope="module")
def command_validator(schema):
    """Validator for the Command union (orchestrator → worker)."""
    return jsonschema.Draft202012Validator({"$ref": "#/$defs/Command", **schema})


@pytest.fixture(scope="module")
def event_validator(schema):
    """Validator for the Event union (worker → orchestrator)."""
    return jsonschema.Draft202012Validator({"$ref": "#/$defs/Event", **schema})


# ----- Command shapes -----


def test_stop_now_conforms(command_validator):
    cmd = control.StopNow()
    command_validator.validate(asdict(cmd))


def test_stop_at_step_conforms(command_validator):
    cmd = control.StopAtStep(at=200)
    command_validator.validate(asdict(cmd))


def test_stop_at_step_with_zero_at_conforms(command_validator):
    # at=0 is allowed (per schema: minimum=0).
    cmd = control.StopAtStep(at=0)
    command_validator.validate(asdict(cmd))


# ----- Event shapes -----


def test_progress_conforms(event_validator):
    evt = events.Progress(step=10, metrics={"loss": 1.5, "lr": 0.001})
    event_validator.validate(asdict(evt))


def test_progress_without_step_conforms(event_validator):
    """step is optional (nullable)."""
    evt = events.Progress(metrics={"loss": 1.5})
    event_validator.validate(asdict(evt))


def test_progress_empty_metrics_conforms(event_validator):
    """metrics may be empty {} (no required keys per schema)."""
    evt = events.Progress(metrics={}, step=0)
    event_validator.validate(asdict(evt))


def test_stopped_conforms(event_validator):
    evt = events.Stopped(reason="natural", metadata={"final_step": 100})
    event_validator.validate(asdict(evt))


def test_stopped_without_metadata_conforms(event_validator):
    evt = events.Stopped(reason="diverged")
    event_validator.validate(asdict(evt))


def test_ack_conforms(event_validator):
    evt = events.Ack(of="StopNow", command_id="abc123def456")
    event_validator.validate(asdict(evt))


def test_ack_of_stop_at_step_conforms(event_validator):
    evt = events.Ack(of="StopAtStep", command_id="abc123def456")
    event_validator.validate(asdict(evt))


# ----- Schema rejects malformed shapes -----


def test_schema_rejects_unknown_command_type(command_validator):
    with pytest.raises(jsonschema.ValidationError):
        command_validator.validate({"type": "Whatever", "command_id": "x" * 12})


def test_schema_rejects_command_missing_command_id(command_validator):
    with pytest.raises(jsonschema.ValidationError):
        command_validator.validate({"type": "StopNow"})


def test_schema_rejects_stop_at_step_missing_at(command_validator):
    with pytest.raises(jsonschema.ValidationError):
        command_validator.validate({"type": "StopAtStep", "command_id": "x" * 12})


def test_schema_rejects_stop_at_step_negative_at(command_validator):
    with pytest.raises(jsonschema.ValidationError):
        command_validator.validate(
            {"type": "StopAtStep", "command_id": "x" * 12, "at": -1}
        )


def test_schema_rejects_command_id_wrong_length(command_validator):
    with pytest.raises(jsonschema.ValidationError):
        command_validator.validate({"type": "StopNow", "command_id": "tooshort"})


def test_schema_rejects_command_id_non_hex(command_validator):
    with pytest.raises(jsonschema.ValidationError):
        command_validator.validate({"type": "StopNow", "command_id": "xxxxxxxxxxxx"})


def test_schema_rejects_progress_missing_metrics(event_validator):
    with pytest.raises(jsonschema.ValidationError):
        event_validator.validate({"type": "Progress", "step": 1})


def test_schema_rejects_progress_non_numeric_metric(event_validator):
    with pytest.raises(jsonschema.ValidationError):
        event_validator.validate(
            {"type": "Progress", "metrics": {"loss": "not_a_number"}}
        )


def test_schema_rejects_stopped_missing_reason(event_validator):
    with pytest.raises(jsonschema.ValidationError):
        event_validator.validate({"type": "Stopped"})


def test_schema_rejects_ack_unknown_of(event_validator):
    with pytest.raises(jsonschema.ValidationError):
        event_validator.validate(
            {"type": "Ack", "of": "FrobNicate", "command_id": "x" * 12}
        )


def test_schema_rejects_extra_properties(command_validator):
    """additionalProperties: false should reject unexpected fields."""
    with pytest.raises(jsonschema.ValidationError):
        command_validator.validate(
            {"type": "StopNow", "command_id": "x" * 12, "extra": "rejected"}
        )
