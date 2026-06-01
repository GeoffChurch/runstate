"""Typed convention bodies (frozen dataclasses) — the Python mirror of the
lifecycle/launcher/value schemas. The substrate stays dict-based; these
serialize (asdict) at the send boundary and parse (Cls(**body)) on read.
"""

from dataclasses import asdict

import pytest

from runstate.payloads import (
    Heartbeat,
    Launched,
    Nak,
    Started,
    Stopped,
    Terminated,
    Value,
)

_SAMPLES = [
    Value(value=0.5, step=10),
    Value(value={"x": 1}, step=None),
    Started(handle="local://h/1", hostname=None, attached_at=0.0),
    Heartbeat(step=7, consumed_seq=3),
    Heartbeat(step=None, consumed_seq=0),
    Stopped(reason="completed", error=None, final_step=9),
    Stopped(reason="errored", error="boom", final_step=1),
    Nak(reason="malformed", message="bad request"),
    Launched(handle="local://h/1", status="running"),
    Terminated(reason="exited", exit_code=0, signal=None),
    Terminated(reason="killed", signal=9, exit_code=None),
]


@pytest.mark.parametrize("body", _SAMPLES, ids=lambda b: type(b).__name__)
def test_body_roundtrips_through_its_dict(body):
    # serialize for the wire, parse back -> identical value object
    assert type(body)(**asdict(body)) == body


def test_terminated_coupling_rejects_illegal_states():
    # exited(exit_code) XOR killed(signal) -- illegal states unrepresentable
    Terminated(reason="exited", exit_code=0, signal=None)  # ok
    Terminated(reason="killed", signal=9, exit_code=None)  # ok
    for kwargs in (
        {"reason": "exited", "exit_code": 0, "signal": 9},        # exited + a signal
        {"reason": "killed", "signal": 9, "exit_code": 5},        # killed + an exit_code
        {"reason": "exited", "exit_code": None, "signal": None},  # exited needs exit_code
        {"reason": "killed", "exit_code": None, "signal": None},  # killed needs signal
        {"reason": "bogus", "exit_code": None, "signal": None},   # unknown reason
    ):
        with pytest.raises(ValueError):
            Terminated(**kwargs)
