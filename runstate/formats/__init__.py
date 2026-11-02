"""Log formats: a log's format is the outermost part of its address
(docs/specs/log-formats.md).

A format is named by the runstate release that introduced it. ``LOG_FORMAT`` is
the one this release reads and writes; ``FORMATS`` holds every known format's
layout, so the locators can recognise an older log and the migration steps can
read it."""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from . import v0_2_0, v0_3_0, v0_4_0
from ._layout import DirectoryLayout, Layout

LOG_FORMAT = "0.4.0"

FORMATS: dict[str, Layout] = {
    m.LAYOUT.version: m.LAYOUT for m in (v0_2_0, v0_3_0, v0_4_0)
}

_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def parse(version: str) -> tuple[int, int, int]:
    """``"X.Y.Z"`` -> a comparable tuple. Anything else is not a format version."""
    m = _VERSION.match(version)
    if m is None:
        raise ValueError(f"not a format version (X.Y.Z): {version!r}")
    return (int(m[1]), int(m[2]), int(m[3]))


def older_than(version: str) -> list[tuple[str, Layout]]:
    """The registered formats older than ``version``, newest first."""
    v = parse(version)
    older = [(k, lay) for k, lay in FORMATS.items() if parse(k) < v]
    return sorted(older, key=lambda kv: parse(kv[0]), reverse=True)


def newer_in(names: Iterable[str], *, prefix: str, sep: str, than: str) -> list[str]:
    """The format versions newer than ``than`` among directory or schema names
    spelled ``<prefix>X<sep>Y<sep>Z``. A newer format's layout cannot be known,
    but its name can."""
    pat = re.compile(
        "^"
        + re.escape(prefix)
        + r"(\d+)"
        + re.escape(sep)
        + r"(\d+)"
        + re.escape(sep)
        + r"(\d+)$"
    )
    floor = parse(than)
    found = []
    for n in names:
        m = pat.match(n)
        if m and (int(m[1]), int(m[2]), int(m[3])) > floor:
            found.append(f"{m[1]}.{m[2]}.{m[3]}")
    return sorted(set(found), key=parse)


# A format directory's name, registered or not: ``v`` and three dot-separated
# integers (§3). Inside a root it names a format; as a root's own name it is refused.
_FORMAT_DIR = re.compile(r"^v(\d+\.\d+\.\d+)$")


def check_sqlite_names(root: Path, run_id: str) -> None:
    """§3's two name rules, which make the sqlite address scheme one-to-one: a
    run id is one filename, and a root is not named like a format directory.
    ``ValueError`` names the rule broken; both locators call this before any
    opening check, so a refused name creates nothing."""
    seps = [os.sep] if os.altsep is None else [os.sep, os.altsep]
    if run_id in ("", ".", "..") or any(sep in run_id for sep in seps):
        raise ValueError(
            f"a sqlite run id must be one filename (no path separator, not '', '.' "
            f"or '..'), got {run_id!r}: a path in it would put the log at another "
            f"run's address (log-formats.md §3)"
        )
    name = Path(os.path.abspath(root)).name
    if _FORMAT_DIR.match(name):
        raise ValueError(
            f"a sqlite root must not be named like a format directory (vX.Y.Z), got "
            f"{str(root)!r}: its logs' paths would also read as format {name[1:]} "
            f"logs under its parent (log-formats.md §3)"
        )


def legacy_sqlite_path(root: Path, run_id: str) -> Path:
    """The legacy address, from before versioned addresses: ``<root>/<rid>.db``."""
    return root / f"{run_id}.db"


def legacy_sqlite_run(path: Path) -> tuple[Path, str] | None:
    """The inverse of ``legacy_sqlite_path``."""
    if path.suffix != ".db":
        return None
    return path.parent, path.stem


@dataclass(frozen=True)
class VersionedAddress:
    """A run's log in a registered format, at that format's address."""

    root: Path
    run_id: str
    format: str


@dataclass(frozen=True)
class LegacyAddress:
    """A run's log, or its tombstone, at the legacy address (§7)."""

    root: Path
    run_id: str


@dataclass(frozen=True)
class NewerFormatRoot:
    """A root holding a format newer than this release knows. Its layout cannot be
    known, so neither can its run ids; every open under the root raises
    ``LogFormatMismatch`` (§4, check 1)."""

    root: Path
    format: str


def sqlite_address(
    path: Path,
) -> VersionedAddress | LegacyAddress | NewerFormatRoot | None:
    """The inverse of the sqlite address scheme (§3, "The inverse"): what ``path``
    is the address of, decided by its parent directory. A registered format's
    directory gives that layout's inverse; an unregistered newer one, its root and
    format, never a guessed run id; any other ``vX.Y.Z`` directory is no format and
    no root, so nothing; any other parent is the legacy address's root. Every copy
    of one run, current, sealed or tombstone, names the same ``(root, run_id)``."""
    m = _FORMAT_DIR.match(path.parent.name)
    if m is None:
        legacy = legacy_sqlite_run(path)
        return None if legacy is None else LegacyAddress(*legacy)
    version = m[1]
    layout = FORMATS.get(version)
    if layout is not None:
        found = layout.sqlite_run(path)
        return None if found is None else VersionedAddress(*found, format=version)
    if parse(version) > parse(LOG_FORMAT):
        return NewerFormatRoot(path.parent.parent, version)
    return None


def sqlite_migrate_command(root: Path) -> str:
    """The whole command that migrates the sqlite logs under ``root``."""
    return f"runstate migrate {shlex.quote(str(root))}"


# The whole command that migrates a postgres database. A DSN can carry a
# password, so a message names it by a placeholder, never by its value.
POSTGRES_MIGRATE_COMMAND = "runstate migrate '<dsn>' --backend postgres"


class LogFormatError(Exception):
    """A log is not in the format this runstate reads."""


class LogFormatMismatch(LogFormatError):
    """``command`` is the whole command that migrates logs where this one is."""

    def __init__(self, *, found: str, expected: str, where: str, command: str) -> None:
        self.found, self.expected, self.where = found, expected, where
        if parse(found) > parse(expected):
            action = f"upgrade runstate (this release reads format {expected})"
        else:
            action = f"run `{command}` to move it to format {expected}"
        super().__init__(
            f"the log at {where} is format {found}, not {expected}: {action}"
        )


class LogFormatMissing(LogFormatError):
    def __init__(self, *, where: str, instructions: str) -> None:
        self.where = where
        super().__init__(instructions)


# What every log written before versioned addresses is onboarded as.
LEGACY_FORMAT = "0.2.0"
_BASE_COMMIT = "4729fcd (2026-07-16, lifecycle-v0.4 and launcher-v0.4)"


# The legacy log's sidecars, moved with it: the -wal and -shm of WAL mode, and
# the -journal a writer in a rollback mode (DELETE, on NFS) leaves hot when it
# dies mid-transaction, holding the pages it overwrote.
_SIDECARS = ("-wal", "-shm", "-journal")

_WHY_TOMBSTONE = (
    "A writer still running an older runstate resolves only the old address, so "
    "without the tombstone it would start the run over there, silently."
)


def sqlite_onboarding(root: Path, run_id: str) -> str:
    """The one-time instructions for a log at the legacy address (§7): stop the
    writers, move each log into the 0.2.0 directory with its sidecars, and leave
    a tombstone, an empty read-only file, at its old address (§5). The loop
    skips empty files and logs already in place, so running it again is safe. A log
    it must skip because another sits at its destination is left untouched, every
    other log is still moved, and the loop then exits non-zero: that run is not
    onboarded."""
    base = root / ("v" + LEGACY_FORMAT)
    q_root, q_base = shlex.quote(str(root)), shlex.quote(str(base))
    sidecars = " ".join(["''", *_SIDECARS])
    loop = (
        f"rc=0; mkdir -p {q_base} && for f in {q_root}/*.db; do "
        '[ -s "$f" ] || continue; '
        f't={q_base}/"${{f##*/}}"; '
        'if [ -e "$t" ]; then echo "skipped $f: $t exists" >&2; rc=1; continue; fi; '
        f"for x in {sidecars}; do "
        'if [ -e "$f$x" ]; then mv "$f$x" "$t$x"; fi; done; '
        '[ -e "$f" ] || { : > "$f" && chmod a-w "$f"; }; done '
        '&& [ "$rc" = 0 ]'
    )
    return (
        f"{legacy_sqlite_path(root, run_id)} predates versioned addresses, so its format "
        f"is unknown and nothing will infer it. If it was written by runstate at "
        f"or after {_BASE_COMMIT}, it is format {LEGACY_FORMAT}: check that its "
        f"lifecycle.heartbeat bodies carry a `t` field. First stop every process "
        f"that writes under {root}, and drain any queued jobs that would. Then "
        f"move the log, with any {', '.join(_SIDECARS[:-1])} or {_SIDECARS[-1]} "
        f"file beside it, into "
        f"{base}/, and leave a tombstone at its old address: an empty, read-only "
        f"file. {_WHY_TOMBSTONE} Then run `{sqlite_migrate_command(root)}`. To do "
        f"this for every legacy log under the root:\n  {loop}"
    )


def postgres_onboarding(run_id: str, schema: str) -> str:
    """The one-time instructions for a run in the legacy ``log`` table, found in
    ``schema`` (§7): stop the writers, move the table into the 0.2.0 schema, and
    leave a tombstone in its place, an empty ``log`` with the old columns whose
    trigger refuses every insert (§5). One transaction, so it moves whole or not
    at all."""
    target = FORMATS[LEGACY_FORMAT].pg_schema()
    s = '"' + schema.replace('"', '""') + '"'
    fn = f"{s}.runstate_log_moved"
    sql = (
        f"BEGIN; CREATE SCHEMA {target}; "
        f"ALTER TABLE {s}.log SET SCHEMA {target}; "
        f"CREATE TABLE {s}.log (LIKE {target}.log); "
        f"CREATE FUNCTION {fn}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        f"RAISE EXCEPTION "
        f"'runstate: this log moved to schema {target}; upgrade runstate'; "
        f"END $$; "
        f"CREATE TRIGGER runstate_log_moved BEFORE INSERT ON {s}.log "
        f"FOR EACH ROW EXECUTE FUNCTION {fn}(); COMMIT;"
    )
    return (
        f"run {run_id!r} is in the unversioned `log` table of schema {s}, which "
        f"predates versioned addresses, so its format is unknown and nothing will "
        f"infer it. If it was written by runstate at or after {_BASE_COMMIT}, it "
        f"is format {LEGACY_FORMAT}. First stop every process that writes to it, and drain "
        f"any queued jobs that would. Then move the table into schema {target}, "
        f"and leave a tombstone in its place: an empty `log` that refuses every "
        f"insert. {_WHY_TOMBSTONE} Then run `{POSTGRES_MIGRATE_COMMAND}`:\n"
        f"  {sql}"
    )


__all__ = [
    "DirectoryLayout",
    "FORMATS",
    "LEGACY_FORMAT",
    "LOG_FORMAT",
    "POSTGRES_MIGRATE_COMMAND",
    "Layout",
    "LegacyAddress",
    "LogFormatError",
    "LogFormatMismatch",
    "LogFormatMissing",
    "NewerFormatRoot",
    "VersionedAddress",
    "check_sqlite_names",
    "legacy_sqlite_path",
    "legacy_sqlite_run",
    "newer_in",
    "older_than",
    "parse",
    "postgres_onboarding",
    "sqlite_address",
    "sqlite_migrate_command",
    "sqlite_onboarding",
]
