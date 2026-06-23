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


@pytest.fixture(params=["memory", "sqlite", "sqlite:delete"])
def ch(request, tmp_path, monkeypatch):
    """A fresh, empty Channel for each backend. sqlite runs under both journal
    modes -- WAL (the local default) and the NFS-safe DELETE rollback journal --
    so the conformance + CAS suite must pass independently under each."""
    if request.param.startswith("sqlite"):
        monkeypatch.setenv("RUNSTATE_SQLITE_JOURNAL_MODE",
                           "DELETE" if request.param == "sqlite:delete" else "WAL")
        from runstate.channel.sqlite import SqliteChannel

        channel = SqliteChannel(tmp_path / "run.db")
    else:
        from runstate.channel.memory import MemoryChannel

        channel = MemoryChannel()
    yield channel
    channel.close()


@pytest.fixture(params=["memory", "sqlite", "sqlite:delete"])
def open_channel(request, tmp_path, monkeypatch):
    """Factory: each call opens a handle on the SAME run, so several handles
    (e.g. a worker and a separate observer, or N racing claimants) share one
    log. Delegates to the real ``runstate.channel.open_channel`` rather than
    hand-constructing backends, so handles share exactly what the library
    shares (separate sqlite connections on one file; the registry-co-located
    memory log + lock) and the fixture can't drift from the locator. sqlite runs
    under both journal modes (WAL and the NFS-safe DELETE)."""
    from runstate.channel import open_channel as locate

    backend = "sqlite" if request.param.startswith("sqlite") else request.param
    if request.param.startswith("sqlite"):
        monkeypatch.setenv("RUNSTATE_SQLITE_JOURNAL_MODE",
                           "DELETE" if request.param == "sqlite:delete" else "WAL")

    made = []

    def _open():
        c = locate("run", root=str(tmp_path), backend=backend)
        made.append(c)
        return c

    yield _open
    for c in made:
        c.close()
