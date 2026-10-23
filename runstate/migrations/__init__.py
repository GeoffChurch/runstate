"""Migration steps between log formats (docs/specs/log-formats.md §6).

A step declares its FROM and TO formats, reads a run's rows in FROM, and returns
its rows in TO. Steps are retained, never deleted: pinned consumers upgrade on
their own schedule, so any old format may still need moving forward."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, NamedTuple, Protocol

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
    RENUMBERS: ClassVar[bool]  # may change a seq or a topic; at most one step does

    def is_live(self, rows: list[Row]) -> bool: ...

    def transform(self, rows: list[Row]) -> list[Row]: ...


class MigrationError(Exception):
    pass


# The one format a renumbering step may leave (log-formats.md §6).
RENUMBERING_FROM = "0.3.0"


def _check_one_renumberer() -> None:
    renumberers = [s for s in STEPS if s.RENUMBERS]
    if len(renumberers) > 1:
        raise MigrationError(
            "more than one step renumbers: "
            + ", ".join(f"{s.FROM} -> {s.TO}" for s in renumberers)
        )
    for s in renumberers:
        if s.FROM != RENUMBERING_FROM:
            raise MigrationError(
                f"the renumbering step leaves format {s.FROM}, not {RENUMBERING_FROM}"
            )


def transform_checked(step: Step, rows: list[Row]) -> list[Row]:
    """``step.transform(rows)``, refused unless a step that does not renumber
    returned every input row with its seq and topic, any rows it appended
    sitting above the input's last seq (log-formats.md §6)."""
    out = step.transform(rows)
    if step.RENUMBERS:
        return out
    topics = {r.seq: r.topic for r in out}
    for r in rows:
        if topics.get(r.seq) != r.topic:
            raise MigrationError(
                f"step {step.FROM} -> {step.TO} does not renumber, but its output"
                f" has no row at seq {r.seq} with topic {r.topic!r}"
            )
    last = max((r.seq for r in rows), default=-1)
    kept = {r.seq for r in rows}
    appended = [r.seq for r in out if r.seq not in kept]
    if any(q <= last for q in appended) or len(topics) != len(out):
        raise MigrationError(
            f"step {step.FROM} -> {step.TO} does not renumber, but it appended a row"
            f" at or below seq {last}, or repeated a seq"
        )
    return out


def chain(start: str, target: str) -> list[Step]:
    _check_one_renumberer()
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
    one step at a time. Returns the run ids that moved. Stops at the first
    refusal, which names its run."""
    moved = []
    for run_id, fmt in sorted(store.formats_of(run_ids).items()):
        if fmt == to:
            continue
        try:
            for step in chain(fmt, to):
                store.migrate_one(step, run_id)
        except MigrationError as exc:
            raise MigrationError(f"run {run_id!r}: {exc}") from exc
        moved.append(run_id)
    return moved


# The steps, retained forever. Imported last: each step module imports Row and
# MigrationError from this one.
from .v0_2_0_to_v0_3_0 import V0_2_0_to_V0_3_0  # noqa: E402

STEPS: tuple[Step, ...] = (V0_2_0_to_V0_3_0(),)
