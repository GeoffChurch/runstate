"""The subscription state machine (docs/design-v0.2.md §6).

A subscription fires at ``from`` (default: the next safe point), repeats every
``every`` (absent => one-shot), and expires per ``until``. Each ``tick`` returns
``(fire, expired)``.
"""

from runstate.schedule import Subscription


def test_empty_schedule_fires_once_now():
    sub = Subscription({}, registered_at=0.0)
    assert sub.tick(step=0, now=0.0) == (True, True)


def test_from_step_fires_once_at_that_step():
    sub = Subscription({"from": {"step": 100}}, registered_at=0.0)
    assert sub.tick(step=50, now=0.0) == (False, False)
    assert sub.tick(step=100, now=0.0) == (True, True)


def test_every_step_recurs_from_registration():
    sub = Subscription({"every": {"step": 10}}, registered_at=0.0)
    assert sub.tick(step=0, now=0.0).fire          # first fire at the next safe point
    assert not sub.tick(step=5, now=0.0).fire
    d = sub.tick(step=10, now=0.0)
    assert d.fire and not d.expired


def test_every_until_count_fires_exactly_n_times():
    sub = Subscription({"every": {"step": 1}, "until": {"count": 3}}, registered_at=0.0)
    results = [sub.tick(step=i, now=0.0) for i in range(5)]
    assert [d.fire for d in results] == [True, True, True, False, False]
    assert results[2].expired


def test_every_until_step_stops_at_threshold():
    sub = Subscription({"every": {"step": 1}, "until": {"step": 3}}, registered_at=0.0)
    results = [sub.tick(step=i, now=0.0) for i in range(5)]
    assert [d.fire for d in results] == [True, True, True, False, False]
    assert results[3].expired


def test_every_whichever_first_steps_or_seconds():
    sub = Subscription(
        {"every": {"any": [{"step": 10}, {"time_seconds": 60}]}}, registered_at=0.0
    )
    assert sub.tick(step=0, now=0.0).fire           # first fire
    assert not sub.tick(step=5, now=30.0).fire      # 5 steps, 30s since last
    assert sub.tick(step=5, now=60.0).fire          # 60s since last -> fire
    assert sub.tick(step=15, now=60.0).fire         # 10 steps since last -> fire
