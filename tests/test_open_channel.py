"""Locating a run's channel: open_channel(run_id, ...) and worker-side attach().

open_channel returns a Channel for a run; repeated calls on the same (root,
run_id) share the run's log. attach() is the worker-side convenience that reads
the run from the environment (set by a Launcher).
"""

import pytest

from runstate.channel import open_channel


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_open_channel_shares_run_by_id(backend, tmp_path):
    a = open_channel("run-1", root=tmp_path, backend=backend)
    b = open_channel("run-1", root=tmp_path, backend=backend)
    a.send({"v": 1}, topic="value", name="loss")
    assert [e.body for e in b.read()] == [{"v": 1}]


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_open_channel_distinct_runs_are_isolated(backend, tmp_path):
    a = open_channel("run-a", root=tmp_path, backend=backend)
    b = open_channel("run-b", root=tmp_path, backend=backend)
    a.send({"v": 1}, topic="value", name="loss")
    assert b.read() == []


def test_open_channel_unknown_backend_raises(tmp_path):
    with pytest.raises(ValueError):
        open_channel("r", root=tmp_path, backend="nope")


def test_attach_reads_env(monkeypatch, tmp_path):
    import runstate

    monkeypatch.setenv("RUNSTATE_RUN_ID", "envrun")
    monkeypatch.setenv("RUNSTATE_CHANNEL_ROOT", str(tmp_path))
    monkeypatch.setenv("RUNSTATE_CHANNEL_BACKEND", "sqlite")
    ch = runstate.attach()
    ch.send({"v": 1}, topic="value", name="loss")
    other = open_channel("envrun", root=tmp_path, backend="sqlite")
    assert [e.body for e in other.read()] == [{"v": 1}]


def test_memory_root_none_is_not_the_string_none():
    # None is a registry sentinel, never str()'d into the namespace "None"
    a = open_channel("reg-none", backend="memory")
    b = open_channel("reg-none", root="None", backend="memory")
    a.send({"v": 1}, topic="value", name="x")
    assert b.read() == []


def test_memory_root_spellings_of_one_path_share(tmp_path):
    # memory mirrors sqlite's identity: two spellings of one location = one log
    a = open_channel("reg-path", root=str(tmp_path), backend="memory")
    b = open_channel("reg-path", root=str(tmp_path) + "/.", backend="memory")
    a.send({"v": 1}, topic="value", name="x")
    assert [e.body for e in b.read()] == [{"v": 1}]
