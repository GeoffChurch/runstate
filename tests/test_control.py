"""Tests for runstate.control: Commands, Checker, Ack semantics."""

import pytest

from runstate import open_channel, control, events


def _pair(tmp_path, backend, run_id="r"):
    w = open_channel(run_id, role="worker", root=str(tmp_path), backend=backend)
    o = open_channel(run_id, role="orchestrator", root=str(tmp_path), backend=backend)
    return w, o


def _drain_orch(o):
    """Collect all messages currently pending for the orchestrator."""
    out = []
    while True:
        m = o.recv(timeout=0)
        if m is None:
            break
        out.append(m)
    return out


# ----- parse / send round-trip -----


def test_stop_now_roundtrip(backend, tmp_path):
    w, o = _pair(tmp_path, backend)
    cmd_id = control.send_stop(o)
    assert isinstance(cmd_id, str)
    msg = w.recv()
    cmd = control.parse(msg)
    assert isinstance(cmd, control.StopNow)
    assert cmd.command_id == cmd_id
    w.close()
    o.close()


def test_stop_at_step_roundtrip(backend, tmp_path):
    w, o = _pair(tmp_path, backend)
    cmd_id = control.send_stop_at_step(o, 200)
    msg = w.recv()
    cmd = control.parse(msg)
    assert isinstance(cmd, control.StopAtStep)
    assert cmd.at == 200
    assert cmd.command_id == cmd_id
    w.close()
    o.close()


def test_parse_returns_none_for_non_command_dict(backend, tmp_path):
    assert control.parse({"type": "Progress", "step": 1}) is None
    assert control.parse({"foo": "bar"}) is None
    assert control.parse({}) is None


def test_parse_returns_none_for_malformed_command(backend, tmp_path):
    # Missing command_id
    assert control.parse({"type": "StopNow"}) is None
    # StopAtStep missing 'at'
    assert control.parse({"type": "StopAtStep", "command_id": "x" * 12}) is None


# ----- Checker basic semantics -----


def test_checker_stop_now_returned_with_ack(backend, tmp_path):
    w, o = _pair(tmp_path, backend)
    checker = control.Checker(w)

    cmd_id = control.send_stop(o)
    result = checker.check(current_step=None)

    assert isinstance(result, control.StopNow)
    assert result.command_id == cmd_id

    # Worker should have sent an Ack to orchestrator.
    msgs = _drain_orch(o)
    assert len(msgs) == 1
    ack = events.parse(msgs[0])
    assert isinstance(ack, events.Ack)
    assert ack.of == "StopNow"
    assert ack.command_id == cmd_id

    w.close()
    o.close()


def test_checker_stop_at_step_not_fired_no_ack(backend, tmp_path):
    """current_step < at → return None, no Ack."""
    w, o = _pair(tmp_path, backend)
    checker = control.Checker(w)

    control.send_stop_at_step(o, 200)
    result = checker.check(current_step=199)

    assert result is None
    # No Ack should be sent.
    assert o.recv(timeout=0) is None

    w.close()
    o.close()


def test_checker_stop_at_step_fires_at_threshold(backend, tmp_path):
    """current_step >= at → return command, emit Ack."""
    w, o = _pair(tmp_path, backend)
    checker = control.Checker(w)

    cmd_id = control.send_stop_at_step(o, 200)

    # Held but not fired.
    assert checker.check(current_step=100) is None
    assert checker.check(current_step=150) is None
    # Fire when we reach 200.
    result = checker.check(current_step=200)
    assert isinstance(result, control.StopAtStep)
    assert result.at == 200
    assert result.command_id == cmd_id

    # Ack present, carries original command_id.
    msgs = _drain_orch(o)
    assert len(msgs) == 1
    ack = events.parse(msgs[0])
    assert isinstance(ack, events.Ack)
    assert ack.of == "StopAtStep"
    assert ack.command_id == cmd_id

    w.close()
    o.close()


def test_checker_returns_none_when_no_messages(backend, tmp_path):
    w, o = _pair(tmp_path, backend)
    checker = control.Checker(w)
    assert checker.check(current_step=5) is None
    assert o.recv(timeout=0) is None
    w.close()
    o.close()


def test_checker_ignores_non_command_dicts(backend, tmp_path):
    """Non-Command dicts are drained-and-ignored, not surfaced or ack'd."""
    w, o = _pair(tmp_path, backend)
    checker = control.Checker(w)

    o.send({"type": "Progress", "step": 1, "metrics": {"loss": 1.0}})
    o.send({"type": "Whatever", "data": "stuff"})

    result = checker.check(current_step=None)
    assert result is None
    # No Ack.
    assert o.recv(timeout=0) is None

    w.close()
    o.close()


# ----- Ack semantics: supersede / subsume / repeated check -----


def test_checker_superseded_stop_at_step_dropped_without_ack(backend, tmp_path):
    """Second StopAtStep supersedes the first; first dropped without Ack."""
    w, o = _pair(tmp_path, backend)
    checker = control.Checker(w)

    # First StopAtStep — held pending (at=200, current<200).
    cmd_id_a = control.send_stop_at_step(o, 200)
    assert checker.check(current_step=10) is None
    # No Ack yet.
    assert o.recv(timeout=0) is None

    # Second StopAtStep arrives; supersedes the first.
    cmd_id_b = control.send_stop_at_step(o, 100)
    # current_step=100 → second fires.
    result = checker.check(current_step=100)
    assert isinstance(result, control.StopAtStep)
    assert result.at == 100
    assert result.command_id == cmd_id_b

    # Exactly one Ack, for the second (acted-on) command. cmd_id_a is dropped.
    msgs = _drain_orch(o)
    assert len(msgs) == 1
    ack = events.parse(msgs[0])
    assert isinstance(ack, events.Ack)
    assert ack.command_id == cmd_id_b
    assert ack.command_id != cmd_id_a

    w.close()
    o.close()


def test_checker_repeated_check_on_held_no_premature_ack(backend, tmp_path):
    """Calling check() repeatedly while a StopAtStep is held should NOT ack."""
    w, o = _pair(tmp_path, backend)
    checker = control.Checker(w)

    control.send_stop_at_step(o, 200)
    for step in range(0, 200, 50):
        assert checker.check(current_step=step) is None

    # No Ack emitted across many checks.
    assert o.recv(timeout=0) is None

    # Now fire.
    result = checker.check(current_step=200)
    assert isinstance(result, control.StopAtStep)

    # Ack emitted exactly once now.
    msgs = _drain_orch(o)
    assert len(msgs) == 1

    w.close()
    o.close()


def test_checker_stop_now_subsumes_pending_stop_at_step(backend, tmp_path):
    """StopNow subsumes a held StopAtStep; held is dropped without Ack."""
    w, o = _pair(tmp_path, backend)
    checker = control.Checker(w)

    # Held StopAtStep.
    cmd_id_a = control.send_stop_at_step(o, 200)
    assert checker.check(current_step=10) is None

    # StopNow arrives.
    cmd_id_b = control.send_stop(o)
    result = checker.check(current_step=10)
    assert isinstance(result, control.StopNow)
    assert result.command_id == cmd_id_b

    # Exactly one Ack, for StopNow only. StopAtStep is dropped silently.
    msgs = _drain_orch(o)
    assert len(msgs) == 1
    ack = events.parse(msgs[0])
    assert isinstance(ack, events.Ack)
    assert ack.of == "StopNow"
    assert ack.command_id == cmd_id_b

    w.close()
    o.close()


# ----- Checker isolation -----


def test_two_checkers_have_independent_state(backend, tmp_path):
    """Two Checkers wrapping the same Channel maintain independent pending state.

    Note: they DO share the underlying message queue, so a message
    consumed by one is gone from the other's perspective.
    """
    w, o = _pair(tmp_path, backend)
    c1 = control.Checker(w)
    c2 = control.Checker(w)

    control.send_stop_at_step(o, 200)

    # c1 drains the message, holds it pending.
    assert c1.check(current_step=10) is None
    # c2 sees nothing — message already consumed by c1.
    assert c2.check(current_step=10) is None
    # c1 still has it pending; firing condition met.
    result = c1.check(current_step=200)
    assert isinstance(result, control.StopAtStep)
    # c2 still has nothing pending.
    assert c2.check(current_step=300) is None

    w.close()
    o.close()


# ----- functional check() convenience -----


def test_check_function_caches_per_channel(backend, tmp_path):
    """Functional control.check() shares state across calls on the same Channel."""
    w, o = _pair(tmp_path, backend)

    control.send_stop_at_step(o, 200)

    # First call drains the message, holds it.
    assert control.check(w, current_step=10) is None
    # Second call should see the held state and fire when threshold met.
    result = control.check(w, current_step=200)
    assert isinstance(result, control.StopAtStep)

    w.close()
    o.close()
