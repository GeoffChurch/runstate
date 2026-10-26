"""Migration steps between log formats (docs/specs/log-formats.md §6).

A step declares its FROM and TO formats, reads a run's rows in FROM, and returns
its rows in TO. Steps are retained, never deleted: pinned consumers upgrade on
their own schedule, so any old format may still need moving forward.

What a log leaves open is shared by every step (``ambiguity.py``): a step declares
its facts, its projections and its divergences, and ``migrate`` runs a detection
pass over the whole root, writing nothing, before it migrates any run."""

from __future__ import annotations

import time
from importlib import metadata
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple, Protocol

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


class MigrationError(Exception):
    pass


# The shared mechanism imports Row and MigrationError from this module.
from .ambiguity import (  # noqa: E402
    Assumptions,
    Choices,
    Detected,
    Divergence,
    Entry,
    Facts,
    History,
    Migrated,
    Projection,
    Report,
    Resolutions,
    Undecided,
    check_names,
    empty_report,
    resolutions,
)


class Step(Protocol):
    FROM: str
    TO: str
    RENUMBERS: ClassVar[bool]  # may change a seq or a topic; at most one step does
    FACTS: ClassVar[Facts[Any]]
    PROJECTIONS: ClassVar[tuple[Projection[Any, Any], ...]]  # in the order it asks
    DIVERGENCES: ClassVar[tuple[Divergence, ...]]

    def is_live(self, rows: list[Row]) -> bool: ...

    def detect(
        self, rows: list[Row], *, assumptions: Assumptions, history: History
    ) -> Detected:
        """The run's forks, per projection, from its input log alone, writing nothing.
        It takes no chooser."""
        ...

    def transform(
        self, rows: list[Row], *, resolutions: Resolutions, history: History
    ) -> Migrated:
        """The run's rows in ``TO``, each fork read by its projection's chooser, and
        where each divergence fires. Deterministic, so the dry run before the seal and
        the transform after it agree."""
        ...


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


def transform_checked(
    step: Step, rows: list[Row], *, resolutions: Resolutions, history: History
) -> Migrated:
    """``step.transform``, refused unless it names every projection and divergence the
    step declares, and unless a step that does not renumber returned every input row
    with its seq and topic, any rows it appended sitting above the input's last seq
    (log-formats.md §6)."""
    migrated = step.transform(rows, resolutions=resolutions, history=history)
    for kind, declared, given in (
        ("projections", [p.name for p in step.PROJECTIONS], migrated.readings),
        ("divergences", [d.name for d in step.DIVERGENCES], migrated.divergences),
    ):
        if sorted(declared) != sorted(given):
            raise MigrationError(
                f"step {step.FROM} -> {step.TO} reports the {kind} {sorted(given)}, "
                f"not the {sorted(declared)} it declares"
            )
    if step.RENUMBERS:
        return migrated
    out = migrated.rows
    seen: set[int] = set()
    for r in out:
        if r.seq in seen:
            raise MigrationError(
                f"step {step.FROM} -> {step.TO} does not renumber, but its output"
                f" repeats seq {r.seq}"
            )
        seen.add(r.seq)
    topics = {r.seq: r.topic for r in out}
    for r in rows:
        if topics.get(r.seq) != r.topic:
            raise MigrationError(
                f"step {step.FROM} -> {step.TO} does not renumber, but its output"
                f" has no row at seq {r.seq} with topic {r.topic!r}"
            )
    last = max((r.seq for r in rows), default=-1)
    kept = {r.seq for r in rows}
    for r in out:
        if r.seq not in kept and r.seq <= last:
            raise MigrationError(
                f"step {step.FROM} -> {step.TO} does not renumber, but it appended"
                f" a row at seq {r.seq}, at or below the input's last seq {last}"
            )
    return migrated


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


class Planned(NamedTuple):
    """One step as the user resolved it, built once, before any run is read: what the
    store needs to migrate a run by it."""

    step: Step
    resolutions: Resolutions
    accepted: frozenset[str]

    def diverged(self, migrated: Migrated) -> dict[Divergence, tuple[int, ...]]:
        """The divergences that fire on the run and that no ``--accept`` accepts."""
        return {
            d: migrated.divergences[d.name]
            for d in self.step.DIVERGENCES
            if migrated.divergences[d.name] and d.name not in self.accepted
        }

    def run(self, rows: list[Row], history: History) -> Migrated:
        """The checked transform, refused where a divergence fires unaccepted."""
        migrated = transform_checked(
            self.step, rows, resolutions=self.resolutions, history=history
        )
        self.check(migrated)
        return migrated

    def check(self, migrated: Migrated) -> None:
        """Refuse the run where a divergence fires that no ``--accept`` accepts."""
        diverged = self.diverged(migrated)
        if diverged:
            raise MigrationError(
                "; ".join(
                    f"the divergence {d.name} fires at seq "
                    f"{', '.join(map(str, seqs))}: {d.reads} Add --accept {d.name} "
                    "to migrate it"
                    for d, seqs in diverged.items()
                )
            )

    def entry(self, run_id: str, migrated: Migrated) -> Entry:
        a = self.resolutions.assumptions
        facts = self.step.FACTS
        return Entry(
            run_id,
            self.step.FROM,
            self.step.TO,
            metadata.version("runstate"),
            time.time(),
            a.cap,
            a.given,
            facts.describe(facts.of(a)),
            {name: c.spelling for name, c in self.resolutions.choosers.items()},
            migrated.readings,
            {d: seqs for d, seqs in migrated.divergences.items() if seqs},
        )


class RunRefused(NamedTuple):
    run_id: str
    message: str  # names the run: "run '<rid>': ..."


class Outcome(NamedTuple):
    """What ``migrate`` did: the runs it moved, and every refusal, each naming its run;
    ``report`` holds the divergences that refused runs, one summary each."""

    moved: tuple[str, ...]
    refused: tuple[RunRefused, ...]
    report: Report


def _detect(
    store: "Store",
    plans: dict[str, Planned],
    choices: Choices,
    run_id: str,
    start: str,
    to: str,
    report: Report,
) -> None:
    """One run's chain, in memory, writing nothing: each step's forks found on its
    input, which for a later step is the earlier one's output, and its transform dry-run
    once every fork of it has a chooser. A fork no ``--resolve`` decides goes in the
    report, and holds the later steps undecided; so does a divergence no ``--accept``
    accepts, which refuses the run."""
    steps = chain(start, to)
    rows = store.rows(start, run_id)
    history = store.history(run_id)
    for k, step in enumerate(steps):
        plan = plans[step.FROM]
        if step.is_live(rows):
            raise MigrationError("it has a live episode; stop it first")
        detected = step.detect(
            rows, assumptions=plan.resolutions.assumptions, history=history
        )
        open_ = [
            p
            for p in step.PROJECTIONS
            if detected.forks[p.name] and p.name not in choices.resolved
        ]
        if open_:
            for p in open_:
                report.fork(step, p, run_id, detected.forks[p.name])
            report.defer(steps[k + 1 :], run_id)
            return
        migrated = transform_checked(
            step, rows, resolutions=plan.resolutions, history=history
        )
        for d, seqs in plan.diverged(migrated).items():
            report.diverge(step, d, run_id, seqs)
        plan.check(migrated)
        history = (*history, plan.entry(run_id, migrated))
        rows = migrated.rows


def migrate(
    store: "Store", run_ids: list[str] | None, *, to: str, choices: Choices
) -> Outcome:
    """Move each run (every run under the store when None) to format ``to``, one step
    at a time, under ``choices``.

    First the detection pass: every run's chain in memory, sealing and writing nothing.
    It refuses the whole invocation (``Undecided``, with one summary per projection)
    unless every projection that has a fork in some run has a ``--resolve``. Then each
    run is migrated; a run refused by a step, a chooser, ``refuse`` or an unaccepted
    divergence is left untouched, and the others go on."""
    check_names(STEPS)
    plans = {
        s.FROM: Planned(s, resolutions(s, choices), choices.accepted) for s in STEPS
    }
    todo = {
        rid: fmt for rid, fmt in sorted(store.formats_of(run_ids).items()) if fmt != to
    }
    report = empty_report()
    refused: dict[str, str] = {}
    for rid, fmt in todo.items():
        try:
            _detect(store, plans, choices, rid, fmt, to, report)
        except MigrationError as exc:
            refused[rid] = str(exc)
    if report.forks:
        raise Undecided(report)
    moved: list[str] = []
    for rid, fmt in todo.items():
        if rid in refused:
            continue
        try:
            for step in chain(fmt, to):
                store.migrate_one(plans[step.FROM], rid)
        except MigrationError as exc:
            refused[rid] = str(exc)
        else:
            moved.append(rid)
    return Outcome(
        tuple(moved),
        tuple(RunRefused(r, f"run {r!r}: {why}") for r, why in sorted(refused.items())),
        report,
    )


# The steps, retained forever. Imported last: each step module imports Row and
# MigrationError from this one.
from .v0_2_0_to_v0_3_0 import V0_2_0_to_V0_3_0  # noqa: E402

STEPS: tuple[Step, ...] = (V0_2_0_to_V0_3_0(),)
