from pathlib import Path

import pytest

from runstate import formats
from runstate.formats import (
    LOG_FORMAT,
    DirectoryLayout,
    LogFormatMismatch,
    LogFormatMissing,
    parse,
)


def test_parse_orders_versions_numerically():
    assert parse("0.10.0") > parse("0.9.9")
    with pytest.raises(ValueError):
        parse("0.3.0.dev0")


def test_directory_layout_addresses():
    lay = DirectoryLayout("0.2.0")
    assert lay.sqlite_path(Path("/r"), "abc") == Path("/r/v0.2.0/abc.db")
    assert lay.pg_schema() == "runstate_v0_2_0"


def test_registry_holds_the_current_format():
    assert LOG_FORMAT == "0.2.0"
    assert formats.FORMATS[LOG_FORMAT].version == LOG_FORMAT


def test_older_than_is_newest_first(monkeypatch):
    monkeypatch.setattr(
        formats,
        "FORMATS",
        {v: DirectoryLayout(v) for v in ["0.1.0", "0.2.0", "0.3.0"]},
    )
    assert [v for v, _ in formats.older_than("0.3.0")] == ["0.2.0", "0.1.0"]


def test_newer_in_parses_names_and_ignores_strangers():
    names = ["v0.2.0", "v0.3.0", "v1.0.0", "vx", "notes", "v0.3"]
    assert formats.newer_in(names, prefix="v", sep=".", than="0.2.0") == [
        "0.3.0",
        "1.0.0",
    ]
    schemas = ["runstate_v0_3_0", "public", "runstate_v0_1_0"]
    assert formats.newer_in(schemas, prefix="runstate_v", sep="_", than="0.2.0") == [
        "0.3.0"
    ]


def test_mismatch_message_says_which_way():
    newer = LogFormatMismatch(found="0.3.0", expected="0.2.0", where="/r")
    older = LogFormatMismatch(found="0.1.0", expected="0.2.0", where="/r")
    assert "upgrade runstate" in str(newer)
    assert "runstate migrate" in str(older)


def test_onboarding_text_names_the_move_and_the_sidecars(tmp_path):
    text = formats.sqlite_onboarding(tmp_path, "abc")
    assert "4729fcd" in text and "v0.2.0" in text and "runstate migrate" in text
    assert "-wal" in text and "-shm" in text
    assert isinstance(
        LogFormatMissing(where="x", instructions=text), formats.LogFormatError
    )
