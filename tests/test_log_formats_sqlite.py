"""log-formats.md §4 on sqlite: the five opening checks, in order."""

import sqlite3
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
from runstate.formats import FORMATS


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
