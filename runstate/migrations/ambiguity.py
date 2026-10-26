"""What every migration step shares for what a log leaves open (log-formats.md §6,
"Ambiguity").

A step declares three closed sets, and the runner treats every step alike through them:
- **its facts** (``Facts``): what a consumer may state about its writers, one type the
  step reads, with the statement that holds when the consumer states nothing. A step
  with nothing to state declares ``NO_FACTS``;
- **its projections** (``Projection``): the kinds of question the log can leave open,
  each answered by a chooser (``Chooser``). Every projection offers ``refuse``;
- **its divergences** (``Divergence``): the rules under which a read of the migrated log
  answers differently from the same read of the input log. A run where one fires is
  refused unless the user accepts it by name.

The user's word reaches the runner once, as ``Choices``, parsed and checked here from
the command line's ``--assume``, ``--resolve`` and ``--accept``. The runner resolves
them per step into ``Assumptions``, which detection reads, and ``Resolutions``, which
the transform reads. A migrated run's ``Entry`` in the root's manifest records them.

Every step may import this module (the plan's import rule): it holds no format's
semantics.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    NamedTuple,
    Protocol,
    TypeAlias,
    TypeVar,
    cast,
    runtime_checkable,
)

from . import MigrationError, Row

if TYPE_CHECKING:
    from . import Step

Json: TypeAlias = "None | bool | int | float | str | list[Json] | dict[str, Json]"

# ----- choosers ------------------------------------------------------------------------


class Refusal(NamedTuple):
    """A chooser's refusal of a run, with its reason."""

    reason: str


Q = TypeVar("Q", contravariant=True)
A = TypeVar("A", covariant=True)


@runtime_checkable
class Chooser(Protocol[Q, A]):
    """Decides a projection's forks: it answers a question its step asks, or refuses
    the run. Deterministic: a function of the question alone, so the detection pass's
    dry run and the transform agree. Consumer code is named ``module:attr``."""

    def choose(self, question: Q) -> A | Refusal: ...


class Refuse:
    """``refuse``: every projection's built-in, the only one any step's resolutions
    may hold for a projection the user did not resolve."""

    def choose(self, question: object) -> Refusal:
        return Refusal("its chooser is refuse")


class Builtin(NamedTuple):
    """A chooser runstate ships, by the name the command line spells."""

    name: str
    chooser: Chooser[Any, Any]
    description: str  # one line


REFUSE = Builtin("refuse", Refuse(), "refuse every run with a fork of it")


class Chosen(NamedTuple):
    """A chooser, with its spelling: a built-in's name, or ``module:attr``."""

    spelling: str
    chooser: Chooser[Any, Any]


UNRESOLVED = Chosen(REFUSE.name, REFUSE.chooser)
"""What a step's resolutions hold for a projection the user did not resolve: by the
detection pass, one that no run has a fork of."""

# ----- a step's declarations ---------------------------------------------------------

QQ = TypeVar("QQ")
AA = TypeVar("AA")


@dataclass(frozen=True)
class Projection(Generic[QQ, AA]):
    """One kind of question a step can find open: its command-line name, a one-sentence
    definition, what its readings are, and the step's generic built-in choosers, to
    which ``refuse`` is always added. ``QQ`` is the question the step asks a chooser,
    ``AA`` its answer."""

    name: str  # kebab-case
    definition: str
    readings: str
    generic: tuple[Builtin, ...]

    @property
    def builtins(self) -> tuple[Builtin, ...]:
        return (*self.generic, REFUSE)

    def ask(self, resolutions: Resolutions, question: QQ) -> AA:
        """The chooser's answer, the run refused if it refuses. The step checks the
        answer against what the log proves."""
        chosen = resolutions.choosers[self.name]
        answer = chosen.chooser.choose(question)
        if isinstance(answer, Refusal):
            raise MigrationError(
                f"--resolve {self.name}={chosen.spelling} refused it: {answer.reason}"
            )
        return cast(AA, answer)


class Divergence(NamedTuple):
    """A rule under which a read of the migrated log answers differently from the same
    read of the input log, corrections included. It is no ambiguity: no two histories
    differ. ``reads`` says which reads it changes and how, in one sentence."""

    name: str  # kebab-case
    reads: str


F = TypeVar("F")


@dataclass(frozen=True)
class Facts(Generic[F]):
    """What a step lets a consumer state about its writers: one value of ``kind``, which
    ``--assume module:attr`` names. ``unstated`` is the statement that holds when the
    consumer states nothing, every fact its whole domain. ``describe`` renders a
    statement for the manifest, its classifier's identity included."""

    kind: type[F]
    unstated: F
    describe: Callable[[F], Json]

    def of(self, assumptions: Assumptions) -> F:
        facts = assumptions.facts
        if not isinstance(facts, self.kind):  # checked when the choices were parsed
            raise MigrationError(f"the facts {facts!r} are no {self.kind.__name__}")
        return facts


class NoFacts:
    """The facts of a step that lets a consumer state nothing."""


NO_FACTS: Facts[NoFacts] = Facts(NoFacts, NoFacts(), lambda _: None)


class Fork(NamedTuple):
    """One open question at the record at ``seq``: two histories that write the log
    there call for different outputs. ``settled_by`` names the facts whose statement
    would settle it, none where no fact can."""

    seq: int
    settled_by: tuple[str, ...]


class Detected(NamedTuple):
    """A run's forks, by projection name: every projection the step declares."""

    forks: Mapping[str, tuple[Fork, ...]]


class Migrated(NamedTuple):
    """A run's rows in the step's ``TO`` format, with what the manifest records: the
    reading each fork was given, by projection and seq, and where each divergence's rule
    fires, by name. Every declared name is present."""

    rows: list[Row]
    readings: Mapping[str, Mapping[int, Json]]
    divergences: Mapping[str, tuple[int, ...]]


# ----- the user's word -----------------------------------------------------------------


class Assumed(NamedTuple):
    """One ``--assume module:attr``: its spelling and the object it names."""

    spelling: str
    facts: object


@dataclass(frozen=True)
class Choices:
    """What the user gave, across a chain: possibly fewer choosers than its steps'
    projections, since a later step's forks are found only once the earlier steps'
    are decided. ``cap`` bounds the work a step may do on one run (a step's refusal past
    it names the record), set where the command line is read and recorded with the
    run."""

    assumed: tuple[Assumed, ...]
    resolved: Mapping[str, Chosen]
    accepted: frozenset[str]
    cap: int


class Assumptions(NamedTuple):
    """What one step's detection reads: the facts stated for it (or its ``unstated``
    ones, ``given`` None) and the cap."""

    facts: object
    given: str | None
    cap: int


class Resolutions(NamedTuple):
    """What one step's transform reads: its assumptions, and exactly one chooser for
    each projection it declares."""

    assumptions: Assumptions
    choosers: Mapping[str, Chosen]


def assumptions(step: Step, choices: Choices) -> Assumptions:
    """The facts stated for ``step``: the one ``--assume`` whose object is its facts'
    kind, else its unstated facts."""
    mine = [a for a in choices.assumed if isinstance(a.facts, step.FACTS.kind)]
    if len(mine) > 1:
        raise MigrationError(
            f"two --assume name step {_label(step)}'s facts: "
            + ", ".join(a.spelling for a in mine)
        )
    if mine:
        return Assumptions(mine[0].facts, mine[0].spelling, choices.cap)
    return Assumptions(step.FACTS.unstated, None, choices.cap)


def resolutions(step: Step, choices: Choices) -> Resolutions:
    """``step``'s resolutions: the chooser given for each projection it declares, and
    ``refuse`` for each it was not given, which the detection pass allows only where no
    run has a fork of it."""
    return Resolutions(
        assumptions(step, choices),
        {
            p.name: (
                choices.resolved[p.name] if p.name in choices.resolved else UNRESOLVED
            )
            for p in step.PROJECTIONS
        },
    )


# ----- the command line ----------------------------------------------------------------


def _label(step: Step) -> str:
    return f"{step.FROM} -> {step.TO}"


def check_names(steps: Sequence[Step]) -> None:
    """Refuse a registry where two projections, or two divergences, share a name: the
    command line names each once across every step."""
    for kind, names in (
        ("projection", [p.name for s in steps for p in s.PROJECTIONS]),
        ("divergence", [d.name for s in steps for d in s.DIVERGENCES]),
    ):
        twice = sorted({n for n in names if names.count(n) > 1})
        if twice:
            raise MigrationError(f"two steps declare the {kind} {', '.join(twice)}")


def _import(spelling: str, flag: str) -> object:
    """The object ``module:attr`` names; ``attr`` may be dotted."""
    module, _, attr = spelling.partition(":")
    try:
        obj: object = importlib.import_module(module)
        for part in attr.split("."):
            obj = getattr(obj, part)
    except (ImportError, AttributeError, ValueError) as exc:
        raise MigrationError(f"{flag}: cannot import it: {exc}") from exc
    return obj


def parse_choices(
    steps: Sequence[Step],
    *,
    assume: Sequence[str],
    resolve: Sequence[str],
    accept: Sequence[str],
    cap: int,
) -> Choices:
    """The command line's choices, each resolved once, before the detection pass:
    ``--assume module:attr`` to an object of exactly one step's facts' kind;
    ``--resolve <projection>=<chooser>`` to a built-in, or to the ``Chooser`` that
    ``module:attr`` names; ``--accept <divergence>`` to a declared name."""
    check_names(steps)
    if cap < 1:
        raise MigrationError(f"--cap {cap}: a step must be let hold one state at least")
    projections = {p.name: p for s in steps for p in s.PROJECTIONS}
    divergences = {d.name for s in steps for d in s.DIVERGENCES}
    assumed: list[Assumed] = []
    for spelling in assume:
        facts = _import(spelling, "--assume")
        if not any(isinstance(facts, s.FACTS.kind) for s in steps):
            raise MigrationError(
                f"--assume {spelling}: a {type(facts).__name__} states no step's "
                "facts"
            )
        assumed.append(Assumed(spelling, facts))
    resolved: dict[str, Chosen] = {}
    for item in resolve:
        name, eq, spelling = item.partition("=")
        if not eq or not spelling:
            raise MigrationError(f"--resolve {item}: spell it <projection>=<chooser>")
        if name not in projections:
            raise MigrationError(
                f"--resolve {item}: no step declares the projection {name!r}; "
                f"they are {', '.join(sorted(projections))}"
            )
        if name in resolved:
            raise MigrationError(f"--resolve {name} is given twice")
        resolved[name] = _chooser(projections[name], spelling)
    for name in accept:
        if name not in divergences:
            raise MigrationError(
                f"--accept {name}: no step declares the divergence {name!r}; "
                f"they are {', '.join(sorted(divergences))}"
            )
    if len(set(accept)) < len(accept):
        raise MigrationError("an --accept is given twice")
    choices = Choices(tuple(assumed), resolved, frozenset(accept), cap)
    for s in steps:
        assumptions(s, choices)  # two --assume for one step refuse here
    return choices


def _chooser(projection: Projection[Any, Any], spelling: str) -> Chosen:
    flag = f"--resolve {projection.name}={spelling}"
    if ":" not in spelling:
        found = [b for b in projection.builtins if b.name == spelling]
        if not found:
            raise MigrationError(
                f"{flag}: no such built-in; they are "
                + ", ".join(b.name for b in projection.builtins)
            )
        return Chosen(spelling, found[0].chooser)
    obj = _import(spelling, flag)
    if not isinstance(obj, Chooser):
        raise MigrationError(f"{flag}: a {type(obj).__name__} is no chooser")
    return Chosen(spelling, obj)


# ----- the manifest ----------------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    """What one run was migrated under by one step: one line of the root's manifest."""

    run_id: str
    from_format: str
    to_format: str
    runstate: str  # the version that ran it
    at: float
    cap: int
    assumed: str | None  # the --assume spelling; None when its facts were unstated
    facts: Json  # the step's rendering of them
    resolved: Mapping[str, str]  # each projection's chooser, as spelled
    readings: Mapping[str, Mapping[int, Json]]  # each fork's reading
    accepted: Mapping[str, tuple[int, ...]]  # each divergence that fired, its seqs

    def to_json(self) -> str:
        return json.dumps(
            {
                "run_id": self.run_id,
                "from": self.from_format,
                "to": self.to_format,
                "runstate": self.runstate,
                "at": self.at,
                "cap": self.cap,
                "assumed": self.assumed,
                "facts": self.facts,
                "resolved": dict(self.resolved),
                "readings": {
                    p: {str(seq): r for seq, r in rs.items()}
                    for p, rs in self.readings.items()
                },
                "accepted": {d: list(seqs) for d, seqs in self.accepted.items()},
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, text: str) -> Entry:
        e = json.loads(text)
        return cls(
            e["run_id"],
            e["from"],
            e["to"],
            e["runstate"],
            e["at"],
            e["cap"],
            e["assumed"],
            e["facts"],
            e["resolved"],
            {
                p: {int(seq): r for seq, r in rs.items()}
                for p, rs in e["readings"].items()
            },
            {d: tuple(seqs) for d, seqs in e["accepted"].items()},
        )


History: TypeAlias = tuple[Entry, ...]
"""A run's manifest entries, oldest first: how the earlier steps migrated it. A later
step may read an earlier step's choices from it (the owner's D3)."""

# ----- the report --------------------------------------------------------------------------


class Site(NamedTuple):
    run_id: str
    seq: int


EXAMPLES = 3  # example sites per summary


@dataclass
class Report:
    """What the detection pass found that needs the user's word: the forks of each
    projection no ``--resolve`` decides, the later steps undecided until they are, and
    the sites of each divergence no ``--accept`` accepts."""

    forks: dict[tuple[str, Projection[Any, Any]], list[tuple[str, Fork]]]
    undecided: dict[str, set[str]]  # a later step's label -> its runs
    diverged: dict[tuple[str, Divergence], list[Site]]

    def fork(
        self,
        step: Step,
        projection: Projection[Any, Any],
        run_id: str,
        forks: Iterable[Fork],
    ) -> None:
        self.forks.setdefault((_label(step), projection), []).extend(
            (run_id, f) for f in forks
        )

    def diverge(
        self, step: Step, divergence: Divergence, run_id: str, seqs: Iterable[int]
    ) -> None:
        self.diverged.setdefault((_label(step), divergence), []).extend(
            Site(run_id, s) for s in seqs
        )

    def defer(self, steps: Iterable[Step], run_id: str) -> None:
        for s in steps:
            self.undecided.setdefault(_label(s), set()).add(run_id)


def empty_report() -> Report:
    return Report({}, {}, {})


def _sites(sites: Sequence[Site]) -> str:
    runs = len({s.run_id for s in sites})
    shown = ", ".join(f"{s.run_id} seq {s.seq}" for s in sites[:EXAMPLES])
    return f"{len(sites)} in {runs} run{'s' * (runs != 1)}, e.g. {shown}"


def render(report: Report) -> str:
    """One summary per projection and per divergence, never a list of every site."""
    out: list[str] = []
    for (label, p), found in report.forks.items():
        width = max(len(b.name) for b in p.builtins)
        lines = [f"{p.name} ({label}): {p.definition}", f"  readings: {p.readings}"]
        for k, b in enumerate(p.builtins):
            head = "  built-ins: " if k == 0 else "             "
            lines.append(f"{head}{b.name.ljust(width)}  {b.description}")
        lines.append(f"  forks: {_sites([Site(r, f.seq) for r, f in found])}")
        settling: dict[str, int] = {}
        for _, f in found:
            for fact in f.settled_by:
                settling[fact] = settling.get(fact, 0) + 1
        open_ = sum(1 for _, f in found if not f.settled_by)
        parts = [f"{fact} ({n})" for fact, n in sorted(settling.items())]
        if open_:
            parts.append(f"no fact ({open_})")
        lines.append(f"  settled by stating: {', '.join(parts)}")
        lines.append(f"  add: --resolve {p.name}=<built-in or module:attr>")
        out.append("\n".join(lines))
    if report.undecided:
        out.append(
            "undecided until these are resolved, since their forks are found on the "
            "output of the steps before them: "
            + "; ".join(
                f"{label} ({len(runs)} run{'s' * (len(runs) != 1)})"
                for label, runs in sorted(report.undecided.items())
            )
        )
    for (label, d), sites in report.diverged.items():
        out.append(
            "\n".join(
                [
                    f"{d.name} ({label}): {d.reads}",
                    f"  sites: {_sites(sites)}",
                    f"  refused until: --accept {d.name}",
                ]
            )
        )
    return "\n\n".join(out)


class Undecided(MigrationError):
    """The detection pass's refusal: a projection that occurs has no ``--resolve``. It
    writes nothing."""

    def __init__(self, report: Report) -> None:
        super().__init__(render(report))
        self.report = report
