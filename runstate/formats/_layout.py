"""How a log is addressed in one format (docs/specs/log-formats.md §3)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class Layout(Protocol):
    """Where a run's log lives in one format, per persistent backend. Memory logs
    have no address and no layout."""

    @property
    def version(self) -> str: ...

    def sqlite_path(self, root: Path, run_id: str) -> Path: ...

    def pg_schema(self) -> str: ...


@dataclass(frozen=True)
class DirectoryLayout:
    """``<root>/v<version>/<rid>.db`` on sqlite; schema ``runstate_v<X>_<Y>_<Z>`` on
    postgres. A later format with a different layout defines its own Layout."""

    version: str

    def sqlite_path(self, root: Path, run_id: str) -> Path:
        return root / f"v{self.version}" / f"{run_id}.db"

    def pg_schema(self) -> str:
        return "runstate_v" + self.version.replace(".", "_")
