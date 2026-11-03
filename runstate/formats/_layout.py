"""How a log is addressed in one format, and the tables it is written in
(docs/specs/log-formats.md §3)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, Protocol


class Row(NamedTuple):
    """One record as a log's table stores it: the columns every registered format's
    log holds, on either backend (Postgres's ``run_id`` and its writer stamp aside).
    The migration steps read and write a run's rows as these, and a run copy copies
    them (docs/specs/run-copy.md)."""

    seq: int
    topic: str
    name: str | None
    request_id: str | None
    body: str  # the stored JSON text, byte for byte
    created_at: float


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

    def sqlite_run(self, path: Path) -> tuple[Path, str] | None:
        """The inverse of ``sqlite_path``: the ``(root, run_id)`` whose log in
        this format is at ``path``, or None when no address of this format is
        there (§3, "The inverse"). One-to-one under §3's two name rules."""
        ...

    def pg_schema(self) -> str: ...

    @property
    def sqlite_ddl(self) -> str:
        """The format's log table and indexes, as one ``executescript``."""
        ...

    @property
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
    sqlite_ddl: str
    pg_ddl: tuple[str, ...]

    def sqlite_path(self, root: Path, run_id: str) -> Path:
        return root / f"v{self.version}" / f"{run_id}.db"

    def sqlite_run(self, path: Path) -> tuple[Path, str] | None:
        if path.suffix != ".db" or path.parent.name != f"v{self.version}":
            return None
        return path.parent.parent, path.stem

    def pg_schema(self) -> str:
        return "runstate_v" + self.version.replace(".", "_")
