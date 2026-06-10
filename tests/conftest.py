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
    """Factory: each call opens a handle on the SAME run, so several handles
    (e.g. a worker and a separate observer, or N racing claimants) share one
    log. Delegates to the real ``runstate.channel.open_channel`` rather than
    hand-constructing backends, so handles share exactly what the library
    shares (separate sqlite connections on one file; the registry-co-located
    memory log + lock) and the fixture can't drift from the locator."""
    from runstate.channel import open_channel as locate

    made = []

    def _open():
        c = locate("run", root=str(tmp_path), backend=request.param)
        made.append(c)
        return c

    yield _open
    for c in made:
        c.close()
