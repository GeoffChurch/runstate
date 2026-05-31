"""The subscription convention's condition-algebra (docs/design-v0.2.md §6).

A Condition is a threshold over (step, time, count) or an any/all of Conditions:
  Threshold := {step: N} | {time_seconds: S} | {count: C}
  Condition := Threshold | {any: [Condition, ...]} | {all: [Condition, ...]}
`any` = whichever crosses first (OR); `all` = whichever crosses last (AND).
"""

from runstate.schedule import is_unsatisfiable, satisfied


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


# ----- is_unsatisfiable: static zero-fire detection -----


def test_until_already_satisfied_is_unsatisfiable():
    assert is_unsatisfiable({"until": {"step": 50}}, step=100)
    assert is_unsatisfiable({"until": {"count": 0}}, step=0)  # 0-fire budget


def test_future_threshold_is_satisfiable():
    # clean >= semantics: a not-yet-reached `from` just waits, it's not zero-fire
    assert not is_unsatisfiable({"from": {"step": 100}}, step=50)


def test_step_from_on_stepless_worker_is_unsatisfiable():
    assert is_unsatisfiable({"from": {"step": 100}}, step=None)


def test_from_after_until_empty_window_is_unsatisfiable():
    # the gate opens (step>=100) only after it has already closed (step>=50)
    assert is_unsatisfiable({"from": {"step": 100}, "until": {"step": 50}}, step=0)


def test_from_before_until_is_satisfiable():
    assert not is_unsatisfiable({"from": {"step": 50}, "until": {"step": 100}}, step=0)


def test_until_count_budget_with_step_from_is_not_empty_window():
    # until = (step>=50 AND fired>=5): at from's corner count=0, so until is open
    sched = {"from": {"step": 100}, "until": {"all": [{"step": 50}, {"count": 5}]}}
    assert not is_unsatisfiable(sched, step=0)


def test_from_after_until_across_dimensions_is_speed_contingent_not_empty():
    # from on step, until on time -> a fast run fires; not a static contradiction
    sched = {"from": {"step": 100}, "until": {"time_seconds": 60}}
    assert not is_unsatisfiable(sched, step=0)


def test_any_from_punts_rather_than_normalizing():
    # from with an `any` has many corners; we don't flag it (degrades to dynamic)
    # -- this from reduces to step>=5, so the window [5,50) is non-empty anyway
    sched = {"from": {"any": [{"step": 100}, {"step": 5}]}, "until": {"step": 50}}
    assert not is_unsatisfiable(sched, step=0)


def test_corner_check_respects_steplessness():
    # on a stepless worker a `step` atom in `until` never closes the window, so
    # the empty-window check must NOT inject a concrete step at from's corner.
    sched = {"from": {"time_seconds": 5}, "until": {"step": 0}}
    assert not is_unsatisfiable(sched, step=None)  # it fires at t>=5


def test_corner_check_still_catches_genuine_stepless_empty_window():
    # time-based empty window IS a contradiction even on a stepless worker
    sched = {"from": {"time_seconds": 100}, "until": {"time_seconds": 50}}
    assert is_unsatisfiable(sched, step=None)
