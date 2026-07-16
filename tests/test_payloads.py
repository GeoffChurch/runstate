"""Typed convention bodies (frozen dataclasses) — the Python mirror of the
lifecycle/launcher/value schemas. The substrate stays dict-based; these
serialize (asdict) at the send boundary and parse (Cls(**body)) on read.
"""

from dataclasses import asdict

import pytest

from runstate.vocabulary.payloads import (
    Heartbeat,
    Launched,
    Nak,
    Started,
    Stopped,
    Terminated,
    Value,
)

_SAMPLES = [
    Value(value=0.5, step=10, t=0.0),
    Value(value={"x": 1}, step=None, t=1.5),
    Started(handle="local://h/1", t=0.0),
    Heartbeat(step=7, consumed_seq=3, t=0.0),
    Heartbeat(step=None, consumed_seq=0, t=2.5),
    Stopped(completed=True, error=None, final_step=9, t=3.0),
    Stopped(completed=False, error="boom", final_step=1, t=4.0),
    Nak(reason="malformed", message="bad request"),
    Launched(handle="local://h/1", status="running", t=5.0),
    Terminated(reason="exited", exit_code=0, signal=None, t=6.0),
    Terminated(reason="killed", signal=9, exit_code=None, t=7.0),
]


@pytest.mark.parametrize("body", _SAMPLES, ids=lambda b: type(b).__name__)
def test_body_roundtrips_through_its_dict(body):
    # serialize for the wire, parse back -> identical value object
    assert type(body)(**asdict(body)) == body


def test_terminated_coupling_rejects_illegal_states():
    # exited(exit_code) XOR killed(signal) -- illegal states unrepresentable
    Terminated(reason="exited", exit_code=0, signal=None, t=0.0)  # ok
    Terminated(reason="killed", signal=9, exit_code=None, t=0.0)  # ok
    for kwargs in (
        {"reason": "exited", "exit_code": 0, "signal": 9},  # exited + a signal
        {"reason": "killed", "signal": 9, "exit_code": 5},  # killed + an exit_code
        {
            "reason": "exited",
            "exit_code": None,
            "signal": None,
        },  # exited needs exit_code
        {"reason": "killed", "exit_code": None, "signal": None},  # killed needs signal
        {"reason": "bogus", "exit_code": None, "signal": None},  # unknown reason
    ):
        with pytest.raises(ValueError):
            Terminated(t=0.0, **kwargs)


def test_completed_with_error_rejected():
    # completed=True ⟹ error is None: the invariant is enforced in __post_init__
    with pytest.raises(ValueError):
        Stopped(completed=True, error="x", final_step=None, t=0.0)
