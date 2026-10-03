"""log-formats.md §4 on sqlite: the five opening checks, in order."""

import os
import sqlite3
import subprocess
import threading

import pytest

from runstate import (
    LOG_FORMAT,
    LogFormatMismatch,
    LogFormatMissing,
    RunNotFound,
    attach_channel,
    create_channel,
)
from runstate.channel.sqlite import SqliteChannel
from runstate.formats import FORMATS
from runstate.migrations import migrate
from runstate.migrations.stores import SqliteStore


def _path(root, rid):
    return FORMATS[LOG_FORMAT].sqlite_path(root, rid)


def test_create_births_at_the_current_address(tmp_path):
    ch = create_channel("r1", root=tmp_path)
    ch.send({"x": 1}, topic="value", name="n")
    ch.close()
    assert _path(tmp_path, "r1").exists()
    assert not (tmp_path / "r1.db").exists()


def test_attach_opens_the_current_address(tmp_path):
    create_channel("r1", root=tmp_path).send({}, topic="value", name="n")
    assert attach_channel("r1", root=tmp_path).last_seq() == 1


def test_nothing_anywhere_is_run_not_found(tmp_path):
    with pytest.raises(RunNotFound):
        attach_channel("ghost", root=tmp_path)


def test_a_newer_format_in_the_root_refuses_everything(tmp_path):
    (tmp_path / "v99.0.0").mkdir()
    with pytest.raises(LogFormatMismatch, match="upgrade runstate"):
        create_channel("r1", root=tmp_path)
    with pytest.raises(LogFormatMismatch, match="upgrade runstate"):
        attach_channel("r1", root=tmp_path)


def test_an_older_format_holding_the_run_says_migrate(tmp_path, monkeypatch):
    from runstate import formats
    from runstate.formats import DirectoryLayout

    monkeypatch.setitem(formats.FORMATS, "0.1.0", DirectoryLayout("0.1.0"))
    old = DirectoryLayout("0.1.0").sqlite_path(tmp_path, "r1")
    old.parent.mkdir()
    sqlite3.connect(old).close()
    for locate in (attach_channel, create_channel):
        with pytest.raises(LogFormatMismatch, match="runstate migrate"):
            locate("r1", root=tmp_path)


def test_a_v0_2_0_log_says_migrate(tmp_path):
    """Format 0.3.0 is current, so a run still at 0.2.0 is refused with the
    instruction to migrate it: never read as 0.3.0, never not found."""
    from runstate.channel.sqlite import SqliteChannel

    old = FORMATS["0.2.0"].sqlite_path(tmp_path, "r1")
    old.parent.mkdir()
    with SqliteChannel(old) as ch:
        ch.send({}, topic="value", name="n")
    for locate in (attach_channel, create_channel):
        with pytest.raises(LogFormatMismatch, match="runstate migrate") as exc:
            locate("r1", root=tmp_path)
        assert (exc.value.found, exc.value.expected) == ("0.2.0", "0.3.0")
    assert not _path(tmp_path, "r1").exists()  # create did not birth beside it


def test_a_legacy_log_is_missing_never_not_found(tmp_path):
    legacy = tmp_path / "r1.db"
    sqlite3.connect(legacy).close()
    before = legacy.read_bytes()
    for locate in (attach_channel, create_channel):
        with pytest.raises(LogFormatMissing, match="runstate migrate"):
            locate("r1", root=tmp_path)
    assert legacy.read_bytes() == before  # never opened, never mutated
    assert not _path(tmp_path, "r1").exists()  # create did not birth beside it


def test_a_partly_onboarded_root(tmp_path):
    """Review focus 1: onboarded runs open; leftovers raise Missing."""
    create_channel("moved", root=tmp_path).send({}, topic="value", name="n")
    sqlite3.connect(tmp_path / "left.db").close()
    assert attach_channel("moved", root=tmp_path).last_seq() == 1
    with pytest.raises(LogFormatMissing):
        attach_channel("left", root=tmp_path)


@pytest.mark.parametrize("rid", ["a?b", "a#b", "a%20b", "a b"])
def test_special_run_ids_round_trip(tmp_path, rid):
    """Review focus 2."""
    create_channel(rid, root=tmp_path).send({}, topic="value", name="n")
    assert attach_channel(rid, root=tmp_path).last_seq() == 1
    assert _path(tmp_path, rid).exists()


def test_concurrent_births_share_one_log(tmp_path):
    """Review focus 4: racing mkdir of the version directory."""
    errors, chans = [], []

    def birth():
        try:
            c = create_channel("race", root=tmp_path)
            c.send({}, topic="value", name="n")
            chans.append(c)
        except Exception as exc:  # noqa: BLE001 -- the assertion reports it
            errors.append(exc)

    threads = [threading.Thread(target=birth) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert attach_channel("race", root=tmp_path).last_seq() == 8
    for c in chans:
        c.close()


def test_create_does_not_create_a_missing_root(tmp_path):
    with pytest.raises(Exception):
        create_channel("r1", root=tmp_path / "absent")


# ----- onboarding: the LogFormatMissing instructions, run as written -------------


def _onboarding_loop(root, rid):
    """The shell loop the LogFormatMissing message gives, verbatim."""
    with pytest.raises(LogFormatMissing) as exc:
        attach_channel(rid, root=root)
    loop = str(exc.value).splitlines()[-1].strip()
    assert loop.startswith("mkdir -p ")
    return loop


def _run(loop):
    subprocess.run(["sh", "-c", loop], check=True)


_NOT_ROOT = pytest.mark.skipif(
    os.geteuid() == 0, reason="file permissions do not bind root (log-formats §5)"
)


@_NOT_ROOT
@pytest.mark.parametrize("journal", ["WAL", "DELETE"])
def test_onboarding_leaves_a_tombstone_an_old_writer_cannot_use(
    tmp_path, monkeypatch, crashed_wal_writer, journal
):
    """C1: a writer from before versioned addresses resolves only
    ``<root>/<rid>.db``. Moving the log away from it, and nothing more, let such a
    writer birth a fresh log there and recompute the run from step 0. The
    instructions leave an empty, read-only file in its place, so its next open
    fails loudly. Master's locator was ``SqliteChannel(<root>/<rid>.db)``, a class
    unchanged since, so these are its create and its attach."""
    crashed_wal_writer(tmp_path / "r1.db", 3)  # -wal and -shm left beside it
    _run(_onboarding_loop(tmp_path, "r1"))
    moved = FORMATS["0.2.0"].sqlite_path(tmp_path, "r1")
    assert sorted(p.name for p in moved.parent.iterdir()) == [
        "r1.db",
        "r1.db-shm",
        "r1.db-wal",
    ]
    tomb = tmp_path / "r1.db"
    assert tomb.stat().st_size == 0 and not tomb.stat().st_mode & 0o222
    assert sorted(p.name for p in tmp_path.iterdir()) == ["r1.db", "v0.2.0"]

    monkeypatch.setenv("RUNSTATE_SQLITE_JOURNAL_MODE", journal)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        SqliteChannel(tomb, create=True)
    with pytest.raises(RunNotFound):
        SqliteChannel(tomb, create=False)
    assert tomb.stat().st_size == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["r1.db", "v0.2.0"]


def test_a_tombstone_leaves_the_opening_checks_and_migrate_as_they_were(
    tmp_path, crashed_wal_writer
):
    """C1: the tombstone sits at the legacy address, which check 4 reads, but an
    onboarded run is answered by checks 2 and 3 first. Before ``migrate`` and
    after it, each of the five checks answers as it would without the tombstone."""
    crashed_wal_writer(tmp_path / "r1.db", 3)
    _run(_onboarding_loop(tmp_path, "r1"))

    for locate in (attach_channel, create_channel):  # 3: an older format holds it
        with pytest.raises(LogFormatMismatch, match="runstate migrate"):
            locate("r1", root=tmp_path)
    assert SqliteStore(tmp_path).formats_of(None) == {"r1": "0.2.0"}
    assert migrate(SqliteStore(tmp_path), None, to=LOG_FORMAT) == ["r1"]

    assert attach_channel("r1", root=tmp_path).last_seq() == 3  # 2: current
    with pytest.raises(RunNotFound):  # 5: nothing anywhere
        attach_channel("r2", root=tmp_path)
    create_channel("r2", root=tmp_path).send({}, topic="value", name="n")
    assert _path(tmp_path, "r2").exists()
    (tmp_path / "x.db").write_bytes((tmp_path / "v0.2.0" / "r1.db").read_bytes())
    with pytest.raises(LogFormatMissing):  # 4: only the legacy address holds it
        attach_channel("x", root=tmp_path)
    (tmp_path / "v99.0.0").mkdir()
    with pytest.raises(LogFormatMismatch, match="upgrade runstate"):  # 1: newer
        attach_channel("r1", root=tmp_path)


def test_the_onboarding_loop_can_be_run_again(tmp_path, crashed_wal_writer):
    """Running the loop twice must not move a tombstone over the log it marks,
    nor move a log over one already in place."""
    crashed_wal_writer(tmp_path / "r1.db", 3)
    loop = _onboarding_loop(tmp_path, "r1")
    _run(loop)
    moved = FORMATS["0.2.0"].sqlite_path(tmp_path, "r1")
    before = {p.name: p.read_bytes() for p in moved.parent.iterdir()}
    _run(loop)
    assert {p.name: p.read_bytes() for p in moved.parent.iterdir()} == before
    assert (tmp_path / "r1.db").stat().st_size == 0
