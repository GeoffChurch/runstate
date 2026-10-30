"""How a log is addressed in one format, and the tables it is written in
(docs/specs/log-formats.md §3)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class Layout(Protocol):
    """Where a run's log lives in one format, and its tables, per persistent
    backend. Memory logs have no address and no layout.

    The tables belong to the format, not to the channel: every writer of a log, a
    channel's birth or a migration's destination, takes them from the format it
    writes, so a change to the current format's table never reaches an older
    format's destination."""

    @property
    def version(self) -> str: ...

    def sqlite_path(self, root: Path, run_id: str) -> Path: ...

    def pg_schema(self) -> str: ...

    def sqlite_ddl(self) -> str:
        """The format's log table and indexes, as one ``executescript``."""
        ...

    def pg_ddl(self) -> tuple[str, ...]:
        """The format's log table and indexes, one statement each, unqualified:
        each runs under a search path set to the format's schema."""
        ...


@dataclass(frozen=True, kw_only=True)
class DirectoryLayout:
    """``<root>/v<version>/<rid>.db`` on sqlite; schema ``runstate_v<X>_<Y>_<Z>`` on
    postgres. A later format with a different layout defines its own Layout.

    Every field is required: a format states its tables, so no format inherits
    another's by omission."""

    version: str
    sqlite_script: str
    pg_statements: tuple[str, ...]

    def sqlite_path(self, root: Path, run_id: str) -> Path:
        return root / f"v{self.version}" / f"{run_id}.db"

    def pg_schema(self) -> str:
        return "runstate_v" + self.version.replace(".", "_")

    def sqlite_ddl(self) -> str:
        return self.sqlite_script

    def pg_ddl(self) -> tuple[str, ...]:
        return self.pg_statements
