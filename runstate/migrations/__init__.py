"""Migration steps between log formats (docs/specs/log-formats.md §6).

A step declares its FROM and TO formats, reads a run's rows in FROM, and returns
its rows in TO. Steps are retained, never deleted: pinned consumers upgrade on
their own schedule, so any old format may still need moving forward."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, Protocol

from .. import formats

if TYPE_CHECKING:
    from .stores import Store


class Row(NamedTuple):
    seq: int
    topic: str
    name: str | None
    request_id: str | None
    body: str  # the stored JSON text, byte-for-byte unless a step rewrites it
    created_at: float


class Step(Protocol):
    FROM: str
    TO: str

    def is_live(self, rows: list[Row]) -> bool: ...

    def transform(self, rows: list[Row]) -> list[Row]: ...


class MigrationError(Exception):
    pass


def chain(start: str, target: str) -> list[Step]:
    edges: dict[str, Step] = {}
    for s in STEPS:
        if s.FROM in edges:
            raise MigrationError(f"two steps leave format {s.FROM}")
        edges[s.FROM] = s
    out: list[Step] = []
    at = start
    while at != target:
        step = edges.get(at)
        if step is None or formats.parse(step.TO) > formats.parse(target):
            raise MigrationError(f"no path from format {start} to {target}")
        out.append(step)
        at = step.TO
    return out


def migrate(store: "Store", run_ids: list[str] | None, *, to: str) -> list[str]:
    """Move each run (every run under the store when None) to format ``to``,
    one step at a time. Returns the run ids that moved."""
    moved = []
    for run_id, fmt in sorted(store.formats_of(run_ids).items()):
        if fmt == to:
            continue
        for step in chain(fmt, to):
            store.migrate_one(step, run_id)
        moved.append(run_id)
    return moved


# The steps, retained forever. Imported last: each step module imports Row and
# MigrationError from this one.
from .v0_2_0_to_v0_3_0 import V0_2_0_to_V0_3_0  # noqa: E402

STEPS: tuple[Step, ...] = (V0_2_0_to_V0_3_0(),)
