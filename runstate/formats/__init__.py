"""Log formats: a log's format is the outermost part of its address
(docs/specs/log-formats.md).

A format is named by the runstate release that introduced it. ``LOG_FORMAT`` is
the one this release reads and writes; ``FORMATS`` holds every known format's
layout, so the locators can recognise an older log and the migration steps can
read it."""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable
from pathlib import Path

from . import v0_2_0, v0_3_0
from ._layout import DirectoryLayout, Layout

LOG_FORMAT = "0.3.0"

FORMATS: dict[str, Layout] = {m.LAYOUT.version: m.LAYOUT for m in (v0_2_0, v0_3_0)}

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


class LogFormatError(Exception):
    """A log is not in the format this runstate reads."""


class LogFormatMismatch(LogFormatError):
    def __init__(self, *, found: str, expected: str, where: str) -> None:
        self.found, self.expected, self.where = found, expected, where
        if parse(found) > parse(expected):
            action = f"upgrade runstate (this release reads format {expected})"
        else:
            action = f"run `runstate migrate` to move it to format {expected}"
        super().__init__(
            f"the log at {where} is format {found}, not {expected}: {action}"
        )


class LogFormatMissing(LogFormatError):
    def __init__(self, *, where: str, instructions: str) -> None:
        self.where = where
        super().__init__(instructions)


_BASE = "0.2.0"  # what every log written before versioned addresses can be onboarded as
_BASE_COMMIT = "4729fcd (2026-07-16, lifecycle-v0.4 and launcher-v0.4)"


# The legacy log's sidecars, moved with it: the -wal and -shm of WAL mode.
_SIDECARS = ("-wal", "-shm")

_WHY_TOMBSTONE = (
    "A writer still running an older runstate resolves only the old address, so "
    "without the tombstone it would start the run over there, silently."
)


def sqlite_onboarding(root: Path, run_id: str) -> str:
    """The one-time instructions for a log at the legacy address (§7): stop the
    writers, move each log into the 0.2.0 directory with its sidecars, and leave
    a tombstone, an empty read-only file, at its old address (§5). The loop
    skips empty files and logs already in place, so running it again is safe."""
    base = root / ("v" + _BASE)
    q_root, q_base = shlex.quote(str(root)), shlex.quote(str(base))
    sidecars = " ".join(["''", *_SIDECARS])
    loop = (
        f"mkdir -p {q_base} && for f in {q_root}/*.db; do "
        '[ -s "$f" ] || continue; '
        f't={q_base}/"${{f##*/}}"; '
        'if [ -e "$t" ]; then echo "skipped $f: $t exists" >&2; continue; fi; '
        f'for x in {sidecars}; do if [ -e "$f$x" ]; then mv "$f$x" "$t$x"; fi; done; '
        '[ -e "$f" ] || { : > "$f" && chmod a-w "$f"; }; done'
    )
    return (
        f"{root / (run_id + '.db')} predates versioned addresses, so its format is unknown "
        f"and nothing will infer it. If it was written by runstate at or after {_BASE_COMMIT}, "
        f"it is format {_BASE}: check that its lifecycle.heartbeat bodies carry a `t` field. "
        f"First stop every process that writes under {root}, and drain any queued jobs that "
        f"would. Then move the log, with any {' and '.join(_SIDECARS)} files beside it, into "
        f"{base}/, and leave a tombstone at its old address: an empty, read-only file. "
        f"{_WHY_TOMBSTONE} Then run `runstate migrate {q_root}`. To do this for every "
        f"legacy log under the root:\n  {loop}"
    )


def postgres_onboarding(run_id: str, schema: str) -> str:
    """The one-time instructions for a run in the legacy ``log`` table, found in
    ``schema`` (§7): stop the writers, move the table into the 0.2.0 schema, and
    leave a tombstone in its place, an empty ``log`` with the old columns whose
    trigger refuses every insert (§5). One transaction, so it moves whole or not
    at all."""
    target = DirectoryLayout(_BASE).pg_schema()
    s = '"' + schema.replace('"', '""') + '"'
    fn = f"{s}.runstate_log_moved"
    sql = (
        f"BEGIN; CREATE SCHEMA {target}; ALTER TABLE {s}.log SET SCHEMA {target}; "
        f"CREATE TABLE {s}.log (LIKE {target}.log); "
        f"CREATE FUNCTION {fn}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        f"RAISE EXCEPTION 'runstate: this log moved to schema {target}; upgrade runstate'; "
        f"END $$; "
        f"CREATE TRIGGER runstate_log_moved BEFORE INSERT ON {s}.log "
        f"FOR EACH ROW EXECUTE FUNCTION {fn}(); COMMIT;"
    )
    return (
        f"run {run_id!r} is in the unversioned `log` table of schema {s}, which predates "
        f"versioned addresses, so its format is unknown and nothing will infer it. If it "
        f"was written by runstate at or after {_BASE_COMMIT}, it is format {_BASE}. First "
        f"stop every process that writes to it, and drain any queued jobs that would. Then "
        f"move the table into schema {target}, and leave a tombstone in its place: an empty "
        f"`log` that refuses every insert. {_WHY_TOMBSTONE} Then run `runstate migrate`:\n"
        f"  {sql}"
    )


__all__ = [
    "DirectoryLayout",
    "FORMATS",
    "LOG_FORMAT",
    "Layout",
    "LogFormatError",
    "LogFormatMismatch",
    "LogFormatMissing",
    "newer_in",
    "older_than",
    "parse",
    "postgres_onboarding",
    "sqlite_onboarding",
]
