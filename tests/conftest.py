"""Shared test fixtures.

The substrate conformance tests run against *every* Channel backend via the
``ch`` fixture, parametrized over backends; each must pass independently.
"""

from types import SimpleNamespace

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


# --- concurrency sub-suite: tier-gated backend fixture ---
# A backend declares the strongest contention tier it supports; a concurrency test
# declares the tier it needs (``@pytest.mark.tier``). The fixture SKIPS (not xfails)
# a backend below the required tier -- "not applicable by nature", not "known bug".
_TIERS = ["in_process", "cross_process", "cross_host"]
_MAX_TIER = {
    "memory": "in_process",       # shared via a process-global registry, NOT across OS processes
    "sqlite": "cross_process",    # one db file; multiple connections / OS processes on a local FS
    "sqlite:delete": "cross_process",
    # "postgres": "cross_host",   # the advisory-lock claim oracle -- the cross-host TDD target
}


@pytest.fixture(params=["memory", "sqlite", "sqlite:delete"])
def conc_backend(request, tmp_path, monkeypatch):
    """A backend for the concurrency sub-suite. Reads the test's ``@pytest.mark.tier``
    (default ``in_process``) and SKIPS backends that don't reach it. Yields the locator
    config so a test can open the channel from any thread or process; the sqlite journal
    mode is set in the env (inherited by forked children) AND carried explicitly (so a
    spawned child can set it itself)."""
    param = request.param
    marker = request.node.get_closest_marker("tier")
    required = marker.args[0] if marker else "in_process"
    if _TIERS.index(_MAX_TIER[param]) < _TIERS.index(required):
        pytest.skip(f"{param}: max tier {_MAX_TIER[param]!r} < required {required!r}")
    backend = "sqlite" if param.startswith("sqlite") else param
    journal = "DELETE" if param == "sqlite:delete" else ("WAL" if backend == "sqlite" else None)
    if journal:
        monkeypatch.setenv("RUNSTATE_SQLITE_JOURNAL_MODE", journal)
    return SimpleNamespace(param=param, backend=backend, root=str(tmp_path), journal=journal)
