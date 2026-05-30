"""The subscription convention's condition-algebra (docs/design-v0.2.md §6).

A Condition is a threshold over (step, time, count) or an any/all of Conditions:
  Threshold := {step: N} | {time_seconds: S} | {count: C}
  Condition := Threshold | {any: [Condition, ...]} | {all: [Condition, ...]}
`any` = whichever crosses first (OR); `all` = whichever crosses last (AND).
"""

from runstate.schedule import satisfied


def test_step_threshold():
    assert satisfied({"step": 100}, step=100, time_seconds=0, count=0)
    assert satisfied({"step": 100}, step=150, time_seconds=0, count=0)
    assert not satisfied({"step": 100}, step=99, time_seconds=0, count=0)


def test_step_threshold_is_never_satisfied_for_a_stepless_worker():
    assert not satisfied({"step": 100}, step=None, time_seconds=5, count=0)


def test_time_and_count_thresholds():
    assert satisfied({"time_seconds": 60}, step=0, time_seconds=60, count=0)
    assert not satisfied({"time_seconds": 60}, step=0, time_seconds=59, count=0)
    assert satisfied({"count": 3}, step=0, time_seconds=0, count=3)
    assert not satisfied({"count": 3}, step=0, time_seconds=0, count=2)


def test_any_is_whichever_crosses_first():
    cond = {"any": [{"step": 10}, {"time_seconds": 60}]}
    assert satisfied(cond, step=10, time_seconds=0, count=0)
    assert satisfied(cond, step=0, time_seconds=60, count=0)
    assert not satisfied(cond, step=9, time_seconds=59, count=0)


def test_all_is_whichever_crosses_last():
    cond = {"all": [{"step": 10}, {"time_seconds": 60}]}
    assert satisfied(cond, step=10, time_seconds=60, count=0)
    assert not satisfied(cond, step=10, time_seconds=59, count=0)
    assert not satisfied(cond, step=9, time_seconds=60, count=0)


def test_nested_any_all():
    cond = {"all": [{"step": 100}, {"any": [{"time_seconds": 60}, {"count": 5}]}]}
    assert satisfied(cond, step=100, time_seconds=60, count=0)
    assert satisfied(cond, step=100, time_seconds=0, count=5)
    assert not satisfied(cond, step=100, time_seconds=0, count=0)
    assert not satisfied(cond, step=99, time_seconds=60, count=5)
