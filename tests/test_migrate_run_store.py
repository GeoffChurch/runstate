"""The sqlite run-store migration tool (scripts/migrate_run_store.py).

Migration is an explicit operator act because ``_open_existing`` may not mutate
what it attaches to: a stale pointer can resolve to a *foreign* valid sqlite db
(the PR #14 harm). These tests pin both halves of that -- the tool does reach a
real log, and it refuses to touch anything that is not one.
"""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from runstate.channel.sqlite import SqliteChannel

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "migrate_run_store.py"


def _load():
    spec = importlib.util.spec_from_file_location("migrate_run_store", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mig = _load()


def _indexes(path: Path) -> set[str]:
    with sqlite3.connect(path) as c:
        return {
            r[0]
            for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='log'"
            )
        }


@pytest.fixture
def stale_log(tmp_path):
    """A log as it existed BEFORE the name index -- a real one, then the index
    dropped, which is exactly what an on-disk pre-#19 log looks like."""
    path = tmp_path / "run-a.db"
    ch = SqliteChannel(path)
    ch.send({"v": 1}, topic="value", name="loss")
    ch.close()
    with sqlite3.connect(path, isolation_level=None) as c:
        c.execute("DROP INDEX idx_log_topic_name_seq")
    assert "idx_log_topic_name_seq" not in _indexes(path)
    return path


def test_migrate_adds_the_missing_index(stale_log):
    outcome = mig.migrate(stale_log, dry_run=False)
    assert outcome.startswith("added")
    assert "idx_log_topic_name_seq" in outcome
    assert "idx_log_topic_name_seq" in _indexes(stale_log)


def test_migrate_is_idempotent(stale_log):
    mig.migrate(stale_log, dry_run=False)
    assert mig.migrate(stale_log, dry_run=False) == "up to date"


def test_dry_run_reports_without_mutating(stale_log):
    outcome = mig.migrate(stale_log, dry_run=True)
    assert outcome.startswith("would add")
    assert "idx_log_topic_name_seq" not in _indexes(stale_log)  # untouched


def test_migration_preserves_the_records(stale_log):
    mig.migrate(stale_log, dry_run=False)
    ch = SqliteChannel(stale_log, create=False)
    assert [(e.topic, e.name, e.body) for e in ch.read()] == [
        ("value", "loss", {"v": 1})
    ]


def test_a_foreign_sqlite_db_is_refused(tmp_path):
    # THE guarantee: a stale pointer resolving to someone else's valid sqlite db
    # must come back untouched -- no `log` table conjured into it.
    foreign = tmp_path / "not-ours.db"
    with sqlite3.connect(foreign, isolation_level=None) as c:
        c.execute("CREATE TABLE contacts (id INTEGER PRIMARY KEY, who TEXT)")
        c.execute("INSERT INTO contacts (who) VALUES ('ada')")

    assert mig.migrate(foreign, dry_run=False) == "skipped (not a runstate log)"

    with sqlite3.connect(foreign) as c:
        tables = {
            r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert tables == {"contacts"}
    assert "log" not in tables


def test_a_table_named_log_with_the_wrong_shape_is_refused(tmp_path):
    # Name collision is not identity: only our exact column set qualifies.
    impostor = tmp_path / "impostor.db"
    with sqlite3.connect(impostor, isolation_level=None) as c:
        c.execute("CREATE TABLE log (id INTEGER PRIMARY KEY, message TEXT)")

    assert mig.migrate(impostor, dry_run=False) == "skipped (not a runstate log)"
    assert _indexes(impostor) == set()


def test_a_non_sqlite_file_is_reported_not_raised(tmp_path):
    junk = tmp_path / "notes.db"
    junk.write_bytes(b"this is not a database")
    assert mig.migrate(junk, dry_run=False).startswith(("unreadable", "FAILED"))


def test_main_walks_a_store_and_reports(tmp_path, capsys):
    for n in ("run-a", "run-b"):
        ch = SqliteChannel(tmp_path / f"{n}.db")
        ch.send({"v": 1}, topic="value", name="loss")
        ch.close()
        with sqlite3.connect(tmp_path / f"{n}.db", isolation_level=None) as c:
            c.execute("DROP INDEX idx_log_topic_name_seq")

    assert mig.main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "2 log(s), 2 changed, 0 failed" in out
    for n in ("run-a", "run-b"):
        assert "idx_log_topic_name_seq" in _indexes(tmp_path / f"{n}.db")


def test_main_returns_nonzero_when_a_log_fails(tmp_path, capsys):
    (tmp_path / "broken.db").write_bytes(b"not a database")
    assert mig.main([str(tmp_path)]) == 1
    assert "1 failed" in capsys.readouterr().out
