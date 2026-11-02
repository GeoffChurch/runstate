from pathlib import Path

import pytest

from runstate import formats
from runstate.formats import (
    LOG_FORMAT,
    LogFormatMismatch,
    LogFormatMissing,
    parse,
)
from tests.format_helpers import toy_layout


def test_parse_orders_versions_numerically():
    assert parse("0.10.0") > parse("0.9.9")
    with pytest.raises(ValueError):
        parse("0.3.0.dev0")


def test_directory_layout_addresses():
    lay = toy_layout("0.2.0")
    assert lay.sqlite_path(Path("/r"), "abc") == Path("/r/v0.2.0/abc.db")
    assert lay.pg_schema() == "runstate_v0_2_0"


def test_registry_holds_the_current_format():
    assert LOG_FORMAT == "0.4.0"
    assert formats.FORMATS[LOG_FORMAT].version == LOG_FORMAT


def test_older_than_is_newest_first(monkeypatch):
    monkeypatch.setattr(
        formats,
        "FORMATS",
        {v: toy_layout(v) for v in ["0.1.0", "0.2.0", "0.3.0"]},
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
    cmd = formats.sqlite_migrate_command(Path("/r"))
    newer = LogFormatMismatch(found="0.3.0", expected="0.2.0", where="/r", command=cmd)
    older = LogFormatMismatch(found="0.1.0", expected="0.2.0", where="/r", command=cmd)
    assert "upgrade runstate" in str(newer)
    assert "run `runstate migrate /r` to move it" in str(older)


def test_migrate_commands_are_whole():
    """I5: a message that says to migrate gives the command in full."""
    assert formats.sqlite_migrate_command(Path("/a b")) == "runstate migrate '/a b'"
    assert formats.POSTGRES_MIGRATE_COMMAND == (
        "runstate migrate '<dsn>' --backend postgres"
    )


def test_onboarding_text_names_the_move_and_the_sidecars(tmp_path):
    text = formats.sqlite_onboarding(tmp_path, "abc")
    assert "4729fcd" in text and "v0.2.0" in text and "runstate migrate" in text
    assert "-wal" in text and "-shm" in text and "-journal" in text
    assert isinstance(
        LogFormatMissing(where="x", instructions=text), formats.LogFormatError
    )


def test_postgres_onboarding_text_names_the_schema_the_tombstone_and_the_command():
    """T1: the postgres instructions, without a server (the DSN suite runs them,
    in test_log_formats_postgres.py). They name the schema the legacy table was
    found in, move it and leave the tombstone in one transaction, and give the
    whole migrate command."""
    text = formats.postgres_onboarding("abc", 'my "app"')
    assert "4729fcd" in text and "'abc'" in text
    sql = "\n".join(ln for ln in text.splitlines() if ln.startswith("  ")).strip()
    assert sql.startswith("BEGIN;") and sql.endswith("COMMIT;")
    assert 'ALTER TABLE "my ""app""".log SET SCHEMA runstate_v0_2_0;' in sql
    assert 'CREATE TABLE "my ""app""".log (LIKE runstate_v0_2_0.log);' in sql
    assert 'BEFORE INSERT ON "my ""app""".log' in sql
    assert f"`{formats.POSTGRES_MIGRATE_COMMAND}`" in text


# §3, "The inverse": sqlite_address undoes the forward map, names nothing it would
# not build, and gives every copy of one run the same (root, run_id).

_ROOTS = [
    Path("/data"),
    Path("/data/runs"),
    Path("rel"),
    Path("/a/v1"),
    Path("/x/0.4.0"),
]
_RIDS = ["r", "3fa9c1d2", "a.b", "v0.4.0", "rid with space", "%3F?#"]


@pytest.mark.parametrize("root", _ROOTS)
@pytest.mark.parametrize("run_id", _RIDS)
def test_the_inverse_undoes_every_forward_address(root, run_id):
    formats.check_sqlite_names(root, run_id)  # every case admitted by §3's rules
    for version, layout in formats.FORMATS.items():
        p = layout.sqlite_path(root, run_id)
        assert layout.sqlite_run(p) == (root, run_id)
        assert formats.sqlite_address(p) == formats.VersionedAddress(
            root, run_id, version
        )
    legacy = formats.legacy_sqlite_path(root, run_id)
    assert formats.legacy_sqlite_run(legacy) == (root, run_id)
    assert formats.sqlite_address(legacy) == formats.LegacyAddress(root, run_id)


@pytest.mark.parametrize(
    "path",
    [
        "/data/v0.4.0/r.db",
        "/data/v0.2.0/r.db",
        "/data/r.db",
        "/data/v1/r.db",
        "/data/runs/v0.3.0/a.b.db",
    ],
)
def test_the_inverse_names_nothing_the_forward_map_would_not_build(path):
    p = Path(path)
    addr = formats.sqlite_address(p)
    if isinstance(addr, formats.VersionedAddress):
        assert formats.FORMATS[addr.format].sqlite_path(addr.root, addr.run_id) == p
    elif isinstance(addr, formats.LegacyAddress):
        assert formats.legacy_sqlite_path(addr.root, addr.run_id) == p
    else:
        pytest.fail(f"{path} is an address, got {addr!r}")


def test_a_newer_format_directory_names_its_root_never_a_run():
    assert formats.sqlite_address(
        Path("/data/v99.0.0/r.db")
    ) == formats.NewerFormatRoot(Path("/data"), "99.0.0")
    # its interior is unknown, so even a non-.db file says the same
    assert formats.sqlite_address(
        Path("/data/v99.0.0/anything")
    ) == formats.NewerFormatRoot(Path("/data"), "99.0.0")


def test_an_unregistered_older_format_directory_and_a_sidecar_are_no_address():
    assert formats.sqlite_address(Path("/data/v0.1.5/r.db")) is None
    for sidecar in ("-wal", "-shm", "-journal"):
        assert formats.sqlite_address(Path(f"/data/v0.4.0/r.db{sidecar}")) is None
        assert formats.sqlite_address(Path(f"/data/r.db{sidecar}")) is None


def test_every_copy_of_a_migrated_run_names_one_root_and_run():
    root = Path("/data")
    copies = [layout.sqlite_path(root, "r") for layout in formats.FORMATS.values()]
    copies.append(formats.legacy_sqlite_path(root, "r"))  # the tombstone
    pairs = set()
    for p in copies:
        addr = formats.sqlite_address(p)
        assert isinstance(addr, (formats.VersionedAddress, formats.LegacyAddress))
        pairs.add((addr.root, addr.run_id))
    assert pairs == {(root, "r")}


@pytest.mark.parametrize("run_id", ["", ".", "..", "a/b", "../x", "/abs"])
def test_a_run_id_that_is_not_one_filename_is_refused(run_id):
    with pytest.raises(ValueError, match="one filename"):
        formats.check_sqlite_names(Path("/data"), run_id)


@pytest.mark.parametrize("root", ["/data/v0.4.0", "v1.2.3", "/data/v99.0.0/"])
def test_a_root_named_like_a_format_directory_is_refused(root):
    with pytest.raises(ValueError, match="named like a format directory"):
        formats.check_sqlite_names(Path(root), "r")


def test_a_root_merely_containing_a_version_is_admitted():
    for root in ("/data/v1", "/data/v0.4", "/data/v0.4.0x", "/v0.4.0/data"):
        formats.check_sqlite_names(Path(root), "r")
