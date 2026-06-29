"""Shared test fixtures.

The substrate conformance tests run against *every* Channel backend via the
``ch`` fixture, parametrized over backends; each must pass independently.
"""

import os
import uuid
from types import SimpleNamespace

import pytest


@pytest.fixture(scope="session")
def pg_dsn():
    """The DSN of a live Postgres for the backend tests, or SKIP. The shared-table
    backend can't be conjured from ``tmp_path``; without a server its tests don't
    run (set ``RUNSTATE_TEST_PG_DSN`` to a direct/session-pooled endpoint)."""
    dsn = os.environ.get("RUNSTATE_TEST_PG_DSN")
    if not dsn:
        pytest.skip("RUNSTATE_TEST_PG_DSN unset; skipping the Postgres backend tests")
    return dsn


@pytest.fixture(scope="session")
def pg_ready(pg_dsn):
    """The DSN, with the shared ``log`` table provisioned once for the session. The
    channel ``__init__`` only probes for the table (DDL is concurrency-unsafe and
    lives in ``ensure_schema``), so the fixtures call it here rather than per test.

    Session-end guard: every channel a test opened must be closed, so the test
    role's backend count returns to its baseline. A leak (a path that forgets
    ``close()``) would silently march toward ``max_connections`` -- assert it away."""
    import time

    import psycopg

    from runstate.channel.postgres import ensure_schema

    ensure_schema(pg_dsn)

    def _others():
        with psycopg.connect(pg_dsn) as c:
            return c.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid <> pg_backend_pid()"
            ).fetchone()[0]

    baseline = _others()
    yield pg_dsn
    # a freshly-closed client lingers briefly in pg_stat_activity, so converge.
    deadline = time.monotonic() + 5.0
    while _others() > baseline and time.monotonic() < deadline:
        time.sleep(0.1)
    leaked = _others() - baseline
    assert leaked <= 0, f"postgres connections leaked above baseline: +{leaked}"


@pytest.fixture(autouse=True)
def _reset_memory_registry():
    """open_channel(..., backend="memory") shares logs through a process-global
    registry keyed by (root, run_id); clear it between tests so a reused run_id
    can't leak one test's log into the next."""
    from runstate.channel import _MEMORY_LOGS

    _MEMORY_LOGS.clear()
    yield
    _MEMORY_LOGS.clear()


@pytest.fixture(params=["memory", "sqlite", "sqlite:delete", "postgres"])
def ch(request, tmp_path, monkeypatch):
    """A fresh, empty Channel for each backend. sqlite runs under both journal
    modes -- WAL (the local default) and the NFS-safe DELETE rollback journal --
    so the conformance + CAS suite must pass independently under each. postgres
    mints a unique uuid run_id per test (the shared ``log`` table has no per-test
    freshness -- uuid isolation stands in for ``tmp_path``'s)."""
    if request.param == "postgres":
        from runstate.channel.postgres import PostgresChannel

        channel = PostgresChannel(request.getfixturevalue("pg_ready"),
                                  run_id=f"ch-{uuid.uuid4()}")
    elif request.param.startswith("sqlite"):
        monkeypatch.setenv("RUNSTATE_SQLITE_JOURNAL_MODE",
                           "DELETE" if request.param == "sqlite:delete" else "WAL")
        from runstate.channel.sqlite import SqliteChannel

        channel = SqliteChannel(tmp_path / "run.db")
    else:
        from runstate.channel.memory import MemoryChannel

        channel = MemoryChannel()
    yield channel
    channel.close()


@pytest.fixture(params=["memory", "sqlite", "sqlite:delete", "postgres"])
def open_channel(request, tmp_path, monkeypatch):
    """Factory: each call opens a handle on the SAME run, so several handles
    (e.g. a worker and a separate observer, or N racing claimants) share one
    log. Delegates to the real ``runstate.channel.open_channel`` rather than
    hand-constructing backends, so handles share exactly what the library
    shares (separate sqlite connections on one file; the registry-co-located
    memory log + lock; one shared postgres ``log`` table) and the fixture can't
    drift from the locator. sqlite runs under both journal modes (WAL and the
    NFS-safe DELETE); postgres mints one uuid run_id all handles share."""
    from runstate.channel import open_channel as locate

    if request.param == "postgres":
        root = request.getfixturevalue("pg_ready")  # the DSN; schema ensured
        backend = "postgres"
        run_id = f"open-{uuid.uuid4()}"
    else:
        backend = "sqlite" if request.param.startswith("sqlite") else request.param
        if request.param.startswith("sqlite"):
            monkeypatch.setenv("RUNSTATE_SQLITE_JOURNAL_MODE",
                               "DELETE" if request.param == "sqlite:delete" else "WAL")
        root = str(tmp_path)
        run_id = "run"

    made = []

    def _open():
        c = locate(run_id, root=root, backend=backend)
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
    "postgres": "cross_host",     # the shared-log CAS is the cross-host claim arbiter (one server = one total order)
}


@pytest.fixture(params=["memory", "sqlite", "sqlite:delete", "postgres"])
def conc_backend(request, tmp_path, monkeypatch):
    """A backend for the concurrency sub-suite. Reads the test's ``@pytest.mark.tier``
    (default ``in_process``) and SKIPS backends that don't reach it. Yields the locator
    config so a test can open the channel from any thread or process; the sqlite journal
    mode is set in the env (inherited by forked children) AND carried explicitly (so a
    spawned child can set it itself). ``namespace`` is a per-test uuid the tests prefix
    onto their hand-built run_ids -- harmless for the tmp_path-isolated backends, and the
    isolation that lets postgres's one shared ``log`` table host many tests / xdist workers."""
    param = request.param
    marker = request.node.get_closest_marker("tier")
    required = marker.args[0] if marker else "in_process"
    if _TIERS.index(_MAX_TIER[param]) < _TIERS.index(required):
        pytest.skip(f"{param}: max tier {_MAX_TIER[param]!r} < required {required!r}")
    if param == "postgres":
        root = request.getfixturevalue("pg_ready")  # the DSN; schema ensured once
        backend, journal = "postgres", None
    else:
        backend = "sqlite" if param.startswith("sqlite") else param
        journal = "DELETE" if param == "sqlite:delete" else ("WAL" if backend == "sqlite" else None)
        if journal:
            monkeypatch.setenv("RUNSTATE_SQLITE_JOURNAL_MODE", journal)
        root = str(tmp_path)
    return SimpleNamespace(param=param, backend=backend, root=root, journal=journal,
                           namespace=uuid.uuid4().hex)
