"""Channel conformance tests — parametrized over both backends."""

import os
import time
from pathlib import Path

import pytest

from runstate import open_channel


def _pair(tmp_path, backend, run_id="r1"):
    """Open both ends of a Channel for one run."""
    w = open_channel(run_id, role="worker", root=str(tmp_path), backend=backend)
    o = open_channel(run_id, role="orchestrator", root=str(tmp_path), backend=backend)
    return w, o


def test_send_recv_roundtrip(backend, tmp_path):
    w, o = _pair(tmp_path, backend)
    w.send({"a": 1})
    assert o.recv() == {"a": 1}
    o.send({"b": 2})
    assert w.recv() == {"b": 2}
    w.close()
    o.close()


def test_recv_timeout_zero_returns_none_when_empty(backend, tmp_path):
    w, o = _pair(tmp_path, backend)
    assert o.recv(timeout=0) is None
    assert w.recv(timeout=0) is None
    w.close()
    o.close()


def test_recv_timeout_finite_returns_none_after_deadline(backend, tmp_path):
    w, o = _pair(tmp_path, backend)
    start = time.monotonic()
    result = o.recv(timeout=0.2)
    elapsed = time.monotonic() - start
    assert result is None
    assert 0.15 <= elapsed <= 0.5  # generous bounds for the 50ms poll
    w.close()
    o.close()


def test_direction_safety_worker_does_not_read_own_sends(backend, tmp_path):
    """A worker-role Channel must never return its own sent messages."""
    w, o = _pair(tmp_path, backend)
    w.send({"x": 1})
    # Worker reads from to_worker; nothing was sent to worker.
    assert w.recv(timeout=0) is None
    # Orchestrator should still see it.
    assert o.recv() == {"x": 1}
    w.close()
    o.close()


def test_ordering_per_direction_preserved(backend, tmp_path):
    w, o = _pair(tmp_path, backend)
    for i in range(10):
        w.send({"i": i})
    received = []
    while True:
        msg = o.recv(timeout=0)
        if msg is None:
            break
        received.append(msg["i"])
    assert received == list(range(10))
    w.close()
    o.close()


def test_crash_recovery_reopen_sees_pending_messages(backend, tmp_path):
    """Messages sent before close+reopen are still readable after."""
    w, o = _pair(tmp_path, backend, run_id="recovery_run")
    w.send({"persistent": True})
    o.close()
    w.close()

    # Reopen orchestrator side; should still see the pending message.
    o2 = open_channel("recovery_run", role="orchestrator",
                     root=str(tmp_path), backend=backend)
    assert o2.recv(timeout=0) == {"persistent": True}
    o2.close()


def test_bidirectional_independent_streams(backend, tmp_path):
    """Worker sends and orchestrator sends don't interfere."""
    w, o = _pair(tmp_path, backend)
    w.send({"from": "worker", "n": 1})
    o.send({"from": "orch", "n": 2})
    w.send({"from": "worker", "n": 3})

    # Orchestrator receives only worker's sends.
    assert o.recv() == {"from": "worker", "n": 1}
    assert o.recv() == {"from": "worker", "n": 3}
    assert o.recv(timeout=0) is None

    # Worker receives only orchestrator's send.
    assert w.recv() == {"from": "orch", "n": 2}
    assert w.recv(timeout=0) is None

    w.close()
    o.close()


def test_recv_blocks_until_message_arrives(backend, tmp_path):
    """recv(timeout=None) blocks until a message arrives."""
    import threading
    w, o = _pair(tmp_path, backend)

    received = []

    def reader():
        msg = o.recv(timeout=2.0)
        received.append(msg)

    t = threading.Thread(target=reader)
    t.start()
    time.sleep(0.1)  # let reader enter recv
    w.send({"awaited": True})
    t.join(timeout=2.0)
    assert received == [{"awaited": True}]

    w.close()
    o.close()


def test_open_channel_rejects_unknown_backend(tmp_path):
    with pytest.raises(ValueError, match="Unknown backend"):
        open_channel("x", role="worker", root=str(tmp_path), backend="redis")  # type: ignore[arg-type]


def test_open_channel_rejects_unknown_role(backend, tmp_path):
    with pytest.raises(ValueError, match="role must be"):
        open_channel("x", role="invalid", root=str(tmp_path), backend=backend)  # type: ignore[arg-type]
