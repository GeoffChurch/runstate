"""Shared test fixtures.

The substrate conformance tests run against *every* Channel backend via the
``ch`` fixture, parametrized over backends; each must pass independently.
"""

import pytest


@pytest.fixture(autouse=True)
def _reset_memory_registry():
    """open_channel(..., backend="memory") shares logs through a process-global
    registry keyed by (root, run_id); clear it between tests so a reused run_id
    can't leak one test's log into the next."""
    from runstate.channel import _MEMORY_LOGS

    _MEMORY_LOGS.clear()
    yield
    _MEMORY_LOGS.clear()


@pytest.fixture(params=["memory", "sqlite"])
def ch(request, tmp_path):
    """A fresh, empty Channel for each backend."""
    if request.param == "sqlite":
        from runstate.channel.sqlite import SqliteChannel

        channel = SqliteChannel(tmp_path / "run.db")
    else:
        from runstate.channel.memory import MemoryChannel

        channel = MemoryChannel()
    yield channel
    channel.close()


@pytest.fixture(params=["memory", "sqlite"])
def open_channel(request, tmp_path):
    """Factory: each call opens a Channel on the SAME run, so several handles
    (e.g. a worker and a separate observer) share one log."""
    made = []
    if request.param == "sqlite":
        from runstate.channel.sqlite import SqliteChannel

        path = tmp_path / "run.db"

        def _open():
            c = SqliteChannel(path)
            made.append(c)
            return c
    else:
        from runstate.channel.memory import MemoryChannel

        shared: list = []

        def _open():
            c = MemoryChannel(shared)
            made.append(c)
            return c

    yield _open
    for c in made:
        c.close()
