"""Tests for runstate.events: typed events + parse/send round-trip."""

import pytest

from runstate import open_channel, events


def _pair(tmp_path, backend, run_id="r"):
    w = open_channel(run_id, role="worker", root=str(tmp_path), backend=backend)
    o = open_channel(run_id, role="orchestrator", root=str(tmp_path), backend=backend)
    return w, o


def test_progress_send_recv_roundtrip(backend, tmp_path):
    w, o = _pair(tmp_path, backend)
    events.progress(w, step=10, metrics={"loss": 1.5})
    msg = o.recv()
    event = events.parse(msg)
    assert isinstance(event, events.Progress)
    assert event.step == 10
    assert event.metrics == {"loss": 1.5}
    w.close()
    o.close()


def test_progress_step_optional(backend, tmp_path):
    w, o = _pair(tmp_path, backend)
    events.progress(w, metrics={"loss": 1.5})  # no step
    msg = o.recv()
    event = events.parse(msg)
    assert isinstance(event, events.Progress)
    assert event.step is None
    assert event.metrics == {"loss": 1.5}
    w.close()
    o.close()


def test_stopped_send_recv_roundtrip(backend, tmp_path):
    w, o = _pair(tmp_path, backend)
    events.stopped(w, reason="natural", metadata={"final_step": 100})
    msg = o.recv()
    event = events.parse(msg)
    assert isinstance(event, events.Stopped)
    assert event.reason == "natural"
    assert event.metadata == {"final_step": 100}
    w.close()
    o.close()


def test_stopped_metadata_optional(backend, tmp_path):
    w, o = _pair(tmp_path, backend)
    events.stopped(w, reason="diverged")
    msg = o.recv()
    event = events.parse(msg)
    assert isinstance(event, events.Stopped)
    assert event.reason == "diverged"
    assert event.metadata is None
    w.close()
    o.close()


def test_ack_send_recv_roundtrip(backend, tmp_path):
    w, o = _pair(tmp_path, backend)
    # Workers don't usually construct Ack directly, but the type
    # supports it for testing/symmetry.
    events.send(w, events.Ack(of="StopNow", command_id="abc123def456"))
    msg = o.recv()
    event = events.parse(msg)
    assert isinstance(event, events.Ack)
    assert event.of == "StopNow"
    assert event.command_id == "abc123def456"
    w.close()
    o.close()


def test_parse_returns_none_for_unknown_type():
    assert events.parse({"type": "Whatever"}) is None
    assert events.parse({"foo": "bar"}) is None
    assert events.parse({}) is None


def test_parse_returns_none_for_malformed_event():
    # Progress missing metrics
    assert events.parse({"type": "Progress", "step": 1}) is None
    # Stopped missing reason
    assert events.parse({"type": "Stopped"}) is None
    # Ack missing fields
    assert events.parse({"type": "Ack"}) is None
    assert events.parse({"type": "Ack", "of": "StopNow"}) is None


def test_progress_dataclass_construction():
    """Progress can be constructed positionally with just metrics."""
    p = events.Progress(metrics={"loss": 1.0})
    assert p.step is None
    assert p.metrics == {"loss": 1.0}
    assert p.type == "Progress"


def test_event_type_field_is_constant():
    """The type field is set by the dataclass; can't be overridden."""
    p = events.Progress(metrics={})
    assert p.type == "Progress"
    s = events.Stopped(reason="x")
    assert s.type == "Stopped"
    a = events.Ack(of="StopNow", command_id="x" * 12)
    assert a.type == "Ack"
