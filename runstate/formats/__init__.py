"""Log formats: a log's format is the outermost part of its address
(docs/specs/log-formats.md).

A format is named by the runstate release that introduced it. ``LOG_FORMAT`` is
the one this release reads and writes; ``FORMATS`` holds every known format's
layout, so the locators can recognise an older log and the migration steps can
read it."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from . import v0_2_0, v0_3_0
from ._layout import DirectoryLayout, Layout

LOG_FORMAT = "0.2.0"

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


def sqlite_onboarding(root: Path, run_id: str) -> str:
    return (
        f"{root / (run_id + '.db')} predates versioned addresses, so its format is unknown "
        f"and nothing will infer it. If it was written by runstate at or after {_BASE_COMMIT}, "
        f"it is format {_BASE}: check that its lifecycle.heartbeat bodies carry a `t` field. "
        f"Move it, with any -wal and -shm files beside it, into {root / ('v' + _BASE)}/, "
        f"then run `runstate migrate {root}`. To move every legacy log under the root:\n"
        f"  mkdir -p '{root}/v{_BASE}' && for f in '{root}'/*.db '{root}'/*.db-wal "
        f"'{root}'/*.db-shm; do [ -e \"$f\" ] && mv \"$f\" '{root}/v{_BASE}/'; done"
    )


def postgres_onboarding(run_id: str) -> str:
    schema = DirectoryLayout(_BASE).pg_schema()
    return (
        f"run {run_id!r} is in the unversioned `log` table, which predates versioned "
        f"addresses, so its format is unknown and nothing will infer it. If it was written "
        f"by runstate at or after {_BASE_COMMIT}, it is format {_BASE}. Move the table, "
        f"then run `runstate migrate`:\n"
        f"  CREATE SCHEMA {schema}; ALTER TABLE public.log SET SCHEMA {schema};"
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
