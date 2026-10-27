"""The oracle: the candidate engine of the 0.3.0 -> 0.4.0 step against the simulator of
conforming 0.3.0 writers.

The two encodings of §7's target are independent: the step's engine
(``runstate.migrations.v0_3_0_to_v0_4_0_engine``) never imports ``tests/migration_sim``,
and the simulator never imports the step. This module, and the mutation test that runs
it (``tests/migration_mutation.py``), and the goldens harness
(``tests/test_migration_goldens.py``) are the only ones that import both.
Where they disagree, the prose decides which is wrong (commits-and-lineage.md §7, "The
referee").

A history's facts are derived from its events (``tests/migration_derive.py``): one
version per claim and per third party, each stating the classes its elements declare,
and the oracle classifier's answers. So "correct under true facts" is a mechanical
check. Over the families the mutation test needs (``families``) and every named history,
five properties:

1. **No silent answer:** where two histories write one log and differ in truth, the
   engine, under the facts true of both, keeps both truths, and reports a fork in each
   projection where they differ. It is checked over the named pairs; over the
   families' shared logs it follows from 2, 5 and narrowing (the argument at
   ``test_narrowing_only_removes_candidates``), which is checked instead.
2. **Every truth reachable:** under a history's own facts, its truth is a candidate:
   its readings (Span's placements included, ``Truth.places``) and its end times.
3. **Every output defined:** no crash, and every refusal names a fact or a record.
4. **No unique wrong answer** under true facts.
5. **An order-free candidate set,** with the decode following its stated rule: the
   engine's forks, read off one layer of its graph each, are the explicit list's; and
   its decode, fork by fork from the last claim back, gives what the rule gives over
   the list.

A log the step's own parse refuses (an in-claim rewind) is outside the engine's
domain: its refusal names the record, which property 3 accepts.

**Two speeds.** The families' tests are marked ``oracle``: the pre-commit hook and CI's
default job deselect them, and CI's ``oracle`` job runs them when the step, its engine
or facts, the simulator or this oracle changes, and on demand. The named histories, the
named pairs and the direct checks run everywhere, in seconds.
"""

from __future__ import annotations

import functools
import json
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from dataclasses import fields, replace
from functools import partial
from typing import Any, NamedTuple

import pytest

import runstate.migrations.v0_3_0_to_v0_4_0 as STEP
import runstate.migrations.v0_3_0_to_v0_4_0_engine as E
import runstate.migrations.v0_3_0_to_v0_4_0_facts as V
import tests.migration_derive as D
import tests.migration_sim.enumerate as enumeration
import tests.test_migration_sim as S
from runstate.migrations import MigrationError, Row
from runstate.migrations.v0_3_0_to_v0_4_0 import parse
from tests.migration_sim import history as H
from tests.migration_sim import truth as T
from tests.migration_sim.render import render

# states in one layer: the oracle's logs are small, and weak facts widen them
CAP = 60000
# distinct partial paths at one state, for a claim's readings and the explicit
# candidates
PATHS = 4000
# states in one layer for a refusal's relaxed runs: each relaxes a fact to its whole
# domain, and too many to follow counts as admitted, so a smaller cap only names more
BLAME_CAP = 300
# states summed over every layer: the oracle's logs are small, so it never binds
BUDGET = 10_000_000
# labels past the log's highest step that a run of passes no record pins is followed to,
# a claim: the oracle's histories run on by at most one pass
RUN_ON = 2

# ----- the two encodings' names, read alike -------------------------------------------


def _node(n: T.NodeId | None) -> E.Node | None:
    if n is None:
        return None
    if isinstance(n, T.CommitId):
        return E.CommitId(n.claim_seq, n.nth)
    return E.ExitId(n.claim_seq)


def _how(h: H.ProcessEnd) -> V.Exited | V.Killed:
    return V.Killed(h.signal) if isinstance(h, H.Killed) else V.Exited(h.code)


class Want(NamedTuple):
    """A truth in the engine's names: each subject's reading (the claims', and the
    run's, 0), and each commit's ``t``."""

    readings: dict[int, E.Reading]
    times: dict[E.CommitId, float]


def want(h: H.History) -> Want:
    """The truth of ``h``, read as the engine reads a path: Origin, End and Span per
    claim, and the run's End."""
    t = truth(h)
    rows = rendered(h).rows
    named = {r.seq: _claim_seq(r) for r in rows if r.topic == "lifecycle.stopped"}
    claims = [r.seq for r in rows if r.topic == "lifecycle.started"]
    L = t.lineage
    placed = _placements(t, rows)
    out: dict[int, E.Reading] = {}
    for c in [*claims, E.RUN]:
        start = next((s for s in L.starts if s.claim_seq == c), None)
        deaths = sorted(
            (
                (V.Outcome(d.outcome.value), E.Source(d.source.value))
                for d in L.deaths
                if d.claim_seq == c
            ),
            key=repr,
        )
        wds = sorted(
            (w.request_id, w.by) for w in L.withdrawals if (named[w.by] or E.RUN) == c
        )
        terms = (
            sorted(
                (E.PTerminated(x.launch, _how(x.how)) for x in L.terminations), key=repr
            )
            if c == E.RUN
            else []
        )
        commits = sorted(
            (
                (x.node.nth, _node(x.parent), x.values, placed[x.node])
                for x in L.commits
                if x.node.claim_seq == c
            ),
            key=repr,
        )
        rewinds = sorted(
            ((r.commits, _node(r.target)) for r in L.rewinds if r.claim_seq == c),
            key=repr,
        )
        ex = next((e for e in L.exits if e.node.claim_seq == c), None)
        ended = (
            None
            if ex is None
            else (E.Kind(ex.kind.value), _node(ex.parent), ex.values, ex.honored)
        )
        out[c] = E.Reading(
            None if start is None else _node(start.resume_from),
            E.EndValue(tuple(deaths), tuple(wds), tuple(terms)),
            E.SpanValue(tuple(commits), tuple(rewinds), ended),
        )
    times = {E.CommitId(x.node.claim_seq, x.node.nth): x.t for x in L.commits}
    return Want(out, times)


_BOUNDARIES = ("lifecycle.started", "control.stop", "lifecycle.stopped")


def _placements(t: T.Truth, rows: Sequence[Row]) -> dict[T.CommitId, int]:
    """Each commit's placement as the engine's Span reads it: the seq of the latest
    boundary (a started, a control.stop or a stopped) at or before the record it
    stands just after (``Truth.places``)."""
    bounds = [r.seq for r in rows if r.topic in _BOUNDARIES]
    out: dict[T.CommitId, int] = {}
    for p in t.places:
        if isinstance(p.record, T.Commit):
            assert isinstance(p.place, T.After)  # a commit is a record the step adds
            out[p.record.node] = max(b for b in bounds if b <= p.place.seq)
    return out


def _claim_seq(r: Row) -> int | None:
    value: int | None = json.loads(r.body)["claim_seq"]
    return value


def readings_of(ctx: E.Context, pieces: Sequence[E.Piece]) -> dict[int, E.Reading]:
    """Every subject's reading on one path."""
    mine: dict[int, list[E.Piece]] = defaultdict(list)
    for p in pieces:
        mine[E._subject(ctx, p)].append(p)
    subjects = [r.seq for r in ctx.recs if r.topic is E.Topic.STARTED] + [E.RUN]
    return {s: E.reading(ctx, s, mine.get(s, [])) for s in subjects}


def matches(ctx: E.Context, pieces: Sequence[E.Piece], w: Want) -> bool:
    """Whether a path writes the truth ``w``: every reading, and every commit's ``t``
    among the times it allows."""
    if readings_of(ctx, pieces) != w.readings:
        return False
    times = E.times(ctx, pieces)
    return all(w.times[c] in times[c] for c in w.times)


# ----- the engine on a log ------------------------------------------------------------


# A history's render, truth and observation, each read by several checks: kept for the
# histories one family test reads together. The size changes no result.
rendered = functools.lru_cache(maxsize=4096)(render)
truth = functools.lru_cache(maxsize=4096)(T.truth)
observed = functools.lru_cache(maxsize=4096)(D.observe)


def run(histories: Sequence[H.History]) -> E.Graph:
    """The engine on the log ``histories`` write, under the facts and answers true of
    every one of them."""
    rows = rendered(histories[0]).rows
    cfg = D.configuration([observed(h) for h in histories])
    return E.candidates(
        list(rows),
        cfg,
        born=D.born(histories[0]),
        cap=CAP,
        budget=BUDGET,
        run_on=RUN_ON,
    )


def parsed(h: H.History) -> bool:
    """Whether the step's parse reads the log: what it refuses never reaches the
    engine."""
    try:
        parse(list(rendered(h).rows))
    except MigrationError:
        return False
    return True


def _fits(ctx: E.Context, p: E.Piece, w: Want) -> bool:
    """Whether a piece can stand in the truth ``w``: the structural ones are checked as
    they come, the rest (values, end times) on the whole path."""
    r = w.readings.get(E._subject(ctx, p))
    if r is None:
        return False
    if isinstance(p, E.PStart):
        return r.origin == p.resume
    if isinstance(p, E.PCommit):
        return any(
            (nth, parent, after) == (p.nth, p.parent, p.gaps.after)
            for nth, parent, _, after in r.span.commits
        )
    if isinstance(p, E.PExit):
        x = r.span.exit
        return (
            x is not None
            and (x[0], x[1], x[3]) == (p.kind, p.parent, p.honored)
            and (x[2] is None) == p.discarded
        )
    if isinstance(p, E.PDied):
        return (p.outcome, p.source) in r.end.deaths
    if isinstance(p, E.PWithdrawal):
        return (p.request_id, p.by) in r.end.withdrawals
    if isinstance(p, E.PRewind):
        return (p.commits, p.target) in r.span.rewinds
    if isinstance(p, E.PRunEnd):
        return len(r.span.commits) == p.commits
    if isinstance(p, E.PTerminated):
        return p in r.end.terminations
    return True


def reachable(g: E.Graph, w: Want, *, cap: int) -> bool:
    """Whether some complete path of the graph writes the truth ``w``: a pass over the
    live edges that drops every edge with a piece the truth cannot hold, keeping per
    state the distinct piece sequences that reach it.
    """
    keep = [
        [e for e in es if all(_fits(g.ctx, p, w) for p in e.pieces)] for es in g.live
    ]
    sub = E.Graph(g.ctx, g.widths, E._alive(keep), g.stopped_at, g.widest)
    try:
        found = E.chains(sub, cap=cap)
    except E.TooMany:
        pytest.fail("the truth-guided pass found too many paths to check")
    return any(matches(g.ctx, ps, w) for ps in found)


def times_over(g: E.Graph) -> dict[E.CommitId, set[float]] | None:
    """Each commit's end times over every complete path; None where the paths are too
    many to list."""
    out: dict[E.CommitId, set[float]] = defaultdict(set)
    try:
        for ps in E.chains(g, cap=PATHS):
            for c, ts in E.times(g.ctx, ps).items():
                out[c] |= ts
    except E.TooMany:
        return None
    return dict(out)


# ----- the histories ------------------------------------------------------------------


def families() -> dict[str, Callable[[], Iterator[H.History]]]:
    """The families the mutation test needs: each catches a mutant no named history
    does (measured 2026-10-26, Task 20b). Every mutant the simulator's other families
    and the oracle's own ``slot``, ``placed-drains``, ``dated-closes`` and
    ``dead-takeovers`` caught, a named history catches too (task-20b-report.md)."""
    return {
        "save-points": partial(S._histories, S.FAMILIES["save-points"]),
        "positional": partial(S._histories, S.FAMILIES["positional"]),
        "rewinds": lambda: enumeration.rewinds(passes=3),
        "ended-stops": enumeration.ended_stops,
    }


FAMILIES = families()


def by_log(histories: Iterator[H.History]) -> dict[tuple[Row, ...], list[H.History]]:
    groups: dict[tuple[Row, ...], list[H.History]] = defaultdict(list)
    for h in histories:
        groups[rendered(h).rows].append(h)
    return groups


def distinct(hs: Sequence[H.History]) -> list[H.History]:
    """One history per distinct truth."""
    seen: dict[T.Lineage, H.History] = {}
    for h in hs:
        seen.setdefault(truth(h).lineage, h)
    return list(seen.values())


# ----- 2, 3 and 4: each history under its own facts -----------------------------------


def own(h: H.History) -> None:
    """Under the history's own facts: no crash and no refusal (3); its truth a candidate
    (2); and where the engine reads one lineage, that lineage is the truth (4)."""
    if not parsed(h):
        return  # the step's parse refuses it, naming the record
    g = run([h])
    assert E.admits(
        g
    ), f"refused at record {g.stopped_at} under the history's own facts"
    w = want(h)
    assert reachable(g, w, cap=PATHS), "the history's truth is no candidate"
    if not E.forked(g, cap=PATHS):  # one answer: the truth, its end times too
        assert readings_of(g.ctx, E.witness(g)) == w.readings
        times = times_over(g)
        if times is not None:
            for c, ts in times.items():
                if len(ts) == 1:
                    assert w.times[c] in ts


@pytest.mark.parametrize("name", list(S.NAMED))
def test_named_histories_under_their_own_facts(name: str) -> None:
    own(S.NAMED[name])


@pytest.mark.oracle
@pytest.mark.parametrize("family", list(FAMILIES))
def test_families_under_their_own_facts(family: str) -> None:
    for h in FAMILIES[family]():
        own(h)


# ----- 1: no silent answer ------------------------------------------------------------


PROJECTIONS: tuple[tuple[E.Projection, Callable[[E.Reading], object]], ...] = (
    (E.Projection.ORIGIN, lambda r: r.origin),
    (E.Projection.END, lambda r: r.end),
    (E.Projection.SPAN, lambda r: r.span),
)


def silent(one: H.History, other: H.History) -> list[str]:
    """Where the engine, under the facts true of both, leaves either truth out, or
    reports no fork in a projection where the two differ. A truth's end times are part
    of its being a candidate (``matches``)."""
    g = run([one, other])
    assert E.admits(g), "refused under the facts true of both"
    a, b = want(one), want(other)
    found = [
        f"the {name} truth is no candidate"
        for name, w in (("first", a), ("second", b))
        if not reachable(g, w, cap=PATHS)
    ]
    forked = {(k.subject, k.projection) for k in E.forked(g, cap=PATHS)}
    for s, ra in a.readings.items():
        for projection, of in PROJECTIONS:
            if of(ra) != of(b.readings[s]) and (s, projection) not in forked:
                found.append(f"claim {s} {projection.value}")
    return found


def test_named_pairs_are_forks() -> None:
    for pair in S.AMBIGUOUS:
        one, other = S.NAMED[pair.one], S.NAMED[pair.other]
        assert silent(one, other) == [], (pair.one, pair.other)


# Property 1 over every other shared log follows from 2, 5 and narrowing. Where two
# histories write one log, the facts true of both state, per version, the union of what
# each history's own facts state, and the classifier gives the union of their answers
# (``D.configuration``). Wider facts admit a superset of lineages (the engine's
# "Narrowing"), so each truth, a candidate under its own facts (2), is a candidate
# under the facts true of both. Where the two truths differ in a projection, two
# candidates read differently at that projection's key, and the engine's forks are the
# explicit list's (5): it reports the fork. The named pairs above, each an ambiguity
# §7 names, are checked directly; narrowing is checked here.

# the named histories short enough that every widened run lists its candidates at once
NARROWING_RECORDS = 10


def test_narrowing_only_removes_candidates() -> None:
    """Under a history's own facts with one class added to one stated fact, in every
    version, each candidate of its own facts is still one. A widened run too wide to
    follow at BLAME_CAP is skipped, and such runs stay few."""
    decided = undecided = 0
    for name, h in S.NAMED.items():
        rows = list(render(h).rows)
        if len(rows) > NARROWING_RECORDS or not parsed(h):
            continue
        cfg = D.configuration([observed(h)])
        own = candidates_listed(
            E.candidates(
                rows, cfg, born=D.born(h), cap=CAP, budget=BUDGET, run_on=RUN_ON
            )
        )
        for fact in E._stated(cfg):
            if isinstance(fact, E.Classified):
                continue
            for cls in E._unstated(cfg, fact):
                try:
                    wide = candidates_listed(
                        E.candidates(
                            rows,
                            E._added(cfg, fact, cls),
                            born=D.born(h),
                            cap=BLAME_CAP,
                            budget=BUDGET,
                            run_on=RUN_ON,
                        )
                    )
                except (E.Refused, E.TooMany):
                    undecided += 1
                    continue
                decided += 1
                assert all(c in wide for c in own), (name, fact, cls)
    assert undecided * 20 < decided, (decided, undecided)


# ----- E2's cost, pinned: the step's exit-honored sites are the truth's costs -----------


def _step_resolutions(h: H.History) -> Any:
    from runstate.migrations.ambiguity import REFUSE, Assumptions, Chosen, Limits
    from runstate.migrations.ambiguity import Resolutions as R

    limits = Limits(cap=CAP, budget=BUDGET, run_on=RUN_ON)
    choosers = {
        "end": Chosen(None, REFUSE.chooser),
        "span": Chosen("fewest", STEP.FEWEST.chooser),
        "origin": Chosen("newest", STEP.NEWEST.chooser),
        "writer": Chosen(None, REFUSE.chooser),
        "end-time": Chosen("lower", STEP.END_TIME.generic[0].chooser),
    }
    cfg = D.configuration([observed(h)])
    return R(Assumptions(cfg, "derived", limits), choosers)


def _born_history(h: H.History) -> Any:
    from runstate.migrations.ambiguity import Entry, Limits

    if h.born is H.Born.V0_3_0:
        return ()
    return (
        Entry(
            run_id="h",
            from_format="0.2.0",
            to_format="0.3.0",
            runstate="test",
            step_source="sha256:0",
            at=0.0,
            limits=Limits(cap=CAP, budget=BUDGET, run_on=RUN_ON),
            assumed=None,
            facts=None,
            resolved={},
            readings={},
            accepted={},
        ),
    )


def costs_pinned(h: H.History) -> None:
    """Where the engine reads one lineage, the truth: the exits where the step's
    ``exit-honored`` divergence fires are exactly the exits of the truth's stated
    costs (``Truth.costs``, §7's E2), named by their 0.3.0 seq."""
    if not parsed(h):
        return
    g = run([h])
    if E.forked(g, cap=PATHS):
        return  # the decode picks a reading: the truth's costs may be another's
    rows = list(rendered(h).rows)
    exits = {
        p.claim: p.seq for part in E.written(g) for p in part if isinstance(p, E.PExit)
    }
    want = sorted({exits[c.exit.claim_seq] for c in truth(h).costs})
    migrated = STEP.V0_3_0_to_V0_4_0().transform(
        rows, resolutions=_step_resolutions(h), history=_born_history(h)
    )
    assert list(migrated.divergences["exit-honored"]) == want


@pytest.mark.parametrize("name", list(S.NAMED))
def test_named_e2_costs_are_the_steps_exit_honored_sites(name: str) -> None:
    costs_pinned(S.NAMED[name])


@pytest.mark.oracle
@pytest.mark.parametrize("family", list(FAMILIES))
def test_family_e2_costs_are_the_steps_exit_honored_sites(family: str) -> None:
    for h in FAMILIES[family]():
        costs_pinned(h)


# ----- 5: the candidate set is order-free, and the decode follows its rule ------------


def candidates_listed(g: E.Graph) -> list[dict[int, E.Reading]]:
    """The candidates, listed: each complete path's readings, once."""
    seen: dict[tuple[Any, ...], dict[int, E.Reading]] = {}
    for ps in E.chains(g, cap=PATHS):
        rs = readings_of(g.ctx, ps)
        seen.setdefault(tuple(sorted(rs.items())), rs)
    return list(seen.values())


def first(q: E.Question) -> E.Answer:
    return q.answers[0]


def last(q: E.Question) -> E.Answer:
    return q.answers[-1]


Path = tuple[tuple[E.Piece, ...], ...]


def _reads(ctx: E.Context, path: Path) -> dict[E.Key, tuple[E.Read, ...]]:
    """What one listed path reads at each key, in order."""
    out: dict[E.Key, tuple[E.Read, ...]] = defaultdict(tuple)
    for k, part in enumerate(path):
        for p in part:
            r = E._read(ctx, p, k)
            if r is not None:
                out[r[0]] += (r[1],)
    return out


def _pieces(path: Path) -> tuple[E.Piece, ...]:
    return tuple(p for part in path for p in part)


def last_first(ctx: E.Context) -> list[int]:
    """The decode's order of subjects: the claims, last first, then the run."""
    claims = sorted(r.seq for r in ctx.recs if r.topic is E.Topic.STARTED)
    return [*reversed(claims), E.RUN]


def listed_questions(
    ctx: E.Context, listed: Sequence[Path], order: Sequence[int]
) -> list[E.Question]:
    """The forks of an explicit list of paths, subject by subject in ``order``, then by
    projection and position: what the engine's ``questions`` must find, read off the
    list instead of the graph. Their witnesses are any listed path that reads each
    answer."""
    reads = [_reads(ctx, path) for path in listed]
    keys = {key for r in reads for key in r}
    rank = {s: k for k, s in enumerate(order)}
    projections = list(E.Projection)
    out: list[E.Question] = []
    for key in sorted(
        (x for x in keys if x.subject in rank),
        key=lambda x: (rank[x.subject], projections.index(x.projection), x.position),
    ):
        by: dict[tuple[E.Read, ...], Path] = {}
        for path, r in zip(listed, reads):
            by.setdefault(r.get(key, ()), path)
        if len(by) > 1:
            answers = tuple(
                E.Answer(v, partial(_pieces, path))
                for v, path in sorted(by.items(), key=lambda kv: repr(kv[0]))
            )
            out.append(E.Question(key, answers))
    return out


def decoded_by_list(
    ctx: E.Context, listed: Sequence[Path], pick: E.Chooser, order: Sequence[int]
) -> dict[int, E.Reading]:
    """The decode's rule applied to the explicit list, subject by subject in ``order``:
    each fork read off the paths the earlier choices left."""
    left = list(listed)
    while True:
        forks = listed_questions(ctx, left, order)
        if not forks:
            break
        q = forks[0]
        choice = pick(q)
        assert choice is not None
        left = [
            path for path in left if _reads(ctx, path).get(q.key, ()) == choice.value
        ]
    readings = {
        tuple(sorted(readings_of(ctx, [p for part in path for p in part]).items()))
        for path in left
    }
    assert len(readings) == 1
    return dict(next(iter(readings)))


def ordered(h: H.History | Sequence[H.History]) -> None:
    """Property 5: the engine's forks are the explicit list's, each gathered over its
    own window of the graph; and its decode, fork by fork, gives what the rule gives
    over the list."""
    hs = [h] if isinstance(h, H.History) else list(h)
    if not parsed(hs[0]):
        return
    g = run(hs)
    try:
        listed = E.paths(g, cap=PATHS)
    except E.TooMany:
        return  # too many to list: the engine's own forks stand alone
    found = [
        (q.key, tuple(a.value for a in q.answers)) for q in E.questions(g, cap=PATHS)
    ]
    want_ = [
        (q.key, tuple(a.value for a in q.answers))
        for q in listed_questions(g.ctx, listed, last_first(g.ctx))
    ]
    assert found == want_
    for pick in (first, last):
        d = E.decode(g, pick, cap=PATHS)
        assert E.questions(d, cap=PATHS) == []
        got = readings_of(g.ctx, E.witness(d))
        assert got == decoded_by_list(g.ctx, listed, pick, last_first(g.ctx))


@pytest.mark.parametrize("name", list(S.NAMED))
def test_named_decodes_follow_the_rule(name: str) -> None:
    ordered(S.NAMED[name])


def test_named_pairs_decode_by_the_rule() -> None:
    for pair in S.AMBIGUOUS:
        ordered([S.NAMED[pair.one], S.NAMED[pair.other]])


@pytest.mark.oracle
@pytest.mark.parametrize("family", list(FAMILIES))
def test_family_decodes_follow_the_rule(family: str) -> None:
    for hs in by_log(FAMILIES[family]()).values():
        reps = distinct(hs)
        if len(reps) > 1:
            ordered(reps[:2])


# ----- 3: a stated fact the log contradicts refuses, naming it (Review Focus 8) -------


def _narrowed(
    cfg: V.Configuration, version: str, name: str, keep: frozenset[Any]
) -> V.Configuration:
    workers = dict(cfg.workers)
    workers[version] = replace(workers[version], **{name: keep})
    return cfg._replace(workers=workers)


class FalseFact(NamedTuple):
    """A fact narrowed in one named history: in each claim, without each class it
    declares in turn."""

    history: str
    fact: E.WorkerFact


# A refusal's names cost a relaxed pass per stated fact, each as long as the log. The
# sample: for each fact and class whose narrowing refuses in some named history, the
# shortest such history, narrowing that fact (measured 2026-10-26: 47 pairs, over 2,437
# refusing narrowings in 143 histories). Two load pairs are named only where a class
# added alone decides what the whole domain cannot follow at BLAME_CAP. A new fact or
# class re-measures it.
FALSE_FACT_SAMPLE = (
    FalseFact("an exit in its first pass, nothing after", E.WorkerFact.DISCARDS),
    FalseFact("an exit in its first pass, nothing after", E.WorkerFact.FINAL_STEP),
    FalseFact("an exit in its first pass, nothing after", E.WorkerFact.HONORED),
    FalseFact("an exit in its first pass, nothing after", E.WorkerFact.LOAD),
    FalseFact("an exit in its first pass, nothing after", E.WorkerFact.LOOP_END),
    FalseFact("an exit in its first pass, nothing after", E.WorkerFact.STRIDE),
    FalseFact("an exit in its first pass, nothing after", E.WorkerFact.VALUES),
    FalseFact("an exit in its first pass, a register after", E.WorkerFact.VALUES),
    FalseFact("a save before the tick, the close undated", E.WorkerFact.BEAT_POS),
    FalseFact("a save before the tick, the close undated", E.WorkerFact.CLOSING),
    FalseFact("a save before the tick, the close undated", E.WorkerFact.FINAL_STEP),
    FalseFact("a save before the tick, the close undated", E.WorkerFact.LEAVE_SAVE),
    FalseFact("a save before the tick, the close undated", E.WorkerFact.LOOP_END),
    FalseFact("a save before the tick, the close undated", E.WorkerFact.SAVE_AT),
    FalseFact("a save before the tick, the close undated", E.WorkerFact.WATERMARK),
    FalseFact("no save before the tick, the close dated", E.WorkerFact.LEAVE_SAVE),
    FalseFact("beat-ends-iteration H1", E.WorkerFact.BEAT_COV),
    FalseFact("beat-ends-iteration H1", E.WorkerFact.CLOSING),
    FalseFact("beat-ends-iteration H1", E.WorkerFact.MARKER),
    FalseFact("beat-ends-iteration H1", E.WorkerFact.SAVE_AT),
    FalseFact("beat-ends-iteration H1", E.WorkerFact.SAVE_COV),
    FalseFact("beat-ends-iteration H2", E.WorkerFact.BEAT_POS),
    FalseFact("valueless-gap H1", E.WorkerFact.BEAT_COV),
    FalseFact("valueless-gap H2", E.WorkerFact.STRIDE),
    FalseFact("progress before a beat", E.WorkerFact.LOAD),
    FalseFact("golden ERRORED", E.WorkerFact.ERROR_FINAL_STEP),
    FalseFact("golden LATER_ERROR_HONORED", E.WorkerFact.HONORED),
    FalseFact("post-beat-values H2", E.WorkerFact.VALUES),
    FalseFact(
        "golden LOADING_RETRY_BESIDE_A_DISPLACED_WORKER", E.WorkerFact.ERROR_FINAL_STEP
    ),
    FalseFact("golden LOADING_RETRY_BESIDE_A_DISPLACED_WORKER", E.WorkerFact.TAKEOVER),
    FalseFact("golden TWO_CLAIMS_ONE_LAUNCH", E.WorkerFact.TAKEOVER),
    FalseFact(
        "golden RESUMED_FROM_A_STEP_COMMITTED_AFTER_THE_CLAIM", E.WorkerFact.LOAD
    ),
    FalseFact("golden TRAILING", E.WorkerFact.WATERMARK),
    FalseFact("golden AFTER_AN_ATTRIBUTED_DEATH", E.WorkerFact.STRAYS),
    FalseFact("final-step H2, discarded", E.WorkerFact.DISCARDS),
    FalseFact("golden DOUBLY_DISPLACED", E.WorkerFact.LOAD),
    FalseFact("golden RESUMED_PAST_A_PREEMPTED_EXIT", E.WorkerFact.WATERMARK),
    FalseFact("golden LOADING_RETRY_RELEASED", E.WorkerFact.LOADING),
    FalseFact("golden LOADING_RETRY_RELEASED", E.WorkerFact.VALUES),
    FalseFact("resume-claim H3, newer", E.WorkerFact.LOAD),
    FalseFact("golden LOADING", E.WorkerFact.LOADING),
    FalseFact("a save that never landed, save-start", E.WorkerFact.BEAT_POS),
    FalseFact("a save that never landed, save-start", E.WorkerFact.SAVE_AT),
    FalseFact("MIDSAVE, landed", E.WorkerFact.CUT_SAVE),
    FalseFact("MIDSAVE, closed", E.WorkerFact.CUT_SAVE),
    FalseFact("MIDSAVE, save-start", E.WorkerFact.CUT_SAVE),
    FalseFact("SLOT", E.WorkerFact.SAVE_COV),
)


@pytest.mark.parametrize(
    "case",
    FALSE_FACT_SAMPLE,
    ids=[f"{c.history}: {c.fact.value}" for c in FALSE_FACT_SAMPLE],
)
def test_a_false_fact_refuses_naming_it(case: FalseFact) -> None:
    """A fact a claim's elements exercise, stated without the class one element
    declares: where the log then admits no history, the refusal proves that fact, its
    relaxation admitting one."""
    h = S.NAMED[case.history]
    assert parsed(h)
    ob = D.observe(h)
    cfg = D.configuration([ob])
    rows = list(render(h).rows)
    refused = 0
    for seq, facts in ob.claims.items():
        if not facts.get(case.fact.value):  # no element of it in this claim
            continue
        version = f"claim@{seq}"
        declared = getattr(cfg.workers[version], case.fact.value)
        for cls in sorted(declared, key=repr):
            false = _narrowed(cfg, version, case.fact.value, declared - {cls})
            g = E.candidates(
                rows, false, born=D.born(h), cap=CAP, budget=BUDGET, run_on=RUN_ON
            )
            if not E.admits(g):
                refused += 1
                b = E.blame(
                    rows,
                    false,
                    born=D.born(h),
                    cap=BLAME_CAP,
                    budget=BUDGET,
                    run_on=RUN_ON,
                )
                assert any(case.fact in n for n in b.names), (cls, b)
    assert refused, "the sample's narrowing admits a history"


# ----- 3, directly: a stated fact the log contradicts refuses (Important 2) --------

_P = H.Process("sim", 1000)


def _claim0(fold: H.Fold) -> H.Claim:
    return H.Claim(
        0, _P, H.Gate.FREE, H.Fresh(), None, H.Clock.LOADED, fold, H.CutSave.CLOSED
    )


def _history(*events: H.Event) -> H.History:
    h = H.History(events, H.Born.V0_3_0)
    assert H.conforms(h)
    return h


def _ticked_pass(step: int) -> list[H.Event]:
    return [
        H.Begin(0, step),
        H.Finish(0, False, H.TICKED),
        H.Beat(0, H.Watermark.CONTROL),
    ]


def _exits(final: H.FinalStep) -> list[H.Event]:
    return [H.Exit(0, False, None, final, H.Drain.EXIT, False), H.Halt(0, H.Exited(0))]


def _refusal(
    h: H.History, edit: Callable[[V.Configuration], V.Configuration]
) -> E.Blame:
    """The history's own facts admit its log; edited, they admit nothing: the refusal's
    names."""
    cfg = D.configuration([D.observe(h)])
    rows = list(render(h).rows)

    def run_(c: V.Configuration) -> E.Graph:
        return E.candidates(
            rows, c, born=D.born(h), cap=CAP, budget=BUDGET, run_on=RUN_ON
        )

    assert E.admits(run_(cfg))
    false = edit(cfg)
    assert not E.admits(run_(false))
    return E.blame(
        rows, false, born=D.born(h), cap=BLAME_CAP, budget=BUDGET, run_on=RUN_ON
    )


def _worker(
    fact: str, keep: frozenset[Any]
) -> Callable[[V.Configuration], V.Configuration]:
    return lambda cfg: _narrowed(cfg, "claim@1", fact, keep)


def _third(
    seq: int, fact: str, keep: frozenset[Any]
) -> Callable[[V.Configuration], V.Configuration]:
    def edit(cfg: V.Configuration) -> V.Configuration:
        third = dict(cfg.third_parties)
        third[f"tp@{seq}"] = replace(third[f"tp@{seq}"], **{fact: keep})
        return cfg._replace(third_parties=third)

    return edit


def test_a_null_final_step_reads_the_clock() -> None:
    """A null ``final_step`` is a reading: an exit inside pass 0 under ``finished``
    writes null where ``yielded`` would write 0, so stating ``yielded`` refuses, though
    no later record reads a step (the review's probe; ``blind``)."""
    h = _history(
        _claim0(H.Fold.AT_CLAIM),
        H.Begin(0, 0),
        H.Emit(0, "loss", H.Stamp.METRIC),
        *_exits(H.FinalStep.FINISHED),
    )
    b = _refusal(h, _worker("final_step", frozenset({V.FinalStep.YIELDED})))
    assert (E.WorkerFact.FINAL_STEP,) in b.names


def test_a_false_watermark_refuses() -> None:
    """A heartbeat's ``consumed_seq`` is the newest control record before it; stating
    ``tail`` says the record just before it, a value here."""
    h = _history(
        _claim0(H.Fold.AT_CLAIM),
        H.StopRequest("x"),
        H.Begin(0, 0),
        H.Emit(0, "loss", H.Stamp.METRIC),
        H.Finish(0, False, H.TICKED),
        H.Beat(0, H.Watermark.CONTROL),
        *_exits(H.FinalStep.YIELDED),
    )
    b = _refusal(h, _worker("watermark", frozenset({V.Watermark.TAIL})))
    assert (E.WorkerFact.WATERMARK,) in b.names


def test_a_false_honored_encoding_refuses() -> None:
    """A stop posted after the last tick: the exit lists it under ``exit``, which drains
    it, and not under ``ticks``."""
    h = _history(
        _claim0(H.Fold.AT_CLAIM),
        *_ticked_pass(0),
        H.StopRequest("x"),
        *_exits(H.FinalStep.YIELDED),
    )
    b = _refusal(h, _worker("honored", frozenset({V.Honored.TICKS})))
    assert (E.WorkerFact.HONORED,) in b.names


def test_a_false_fold_refuses() -> None:
    """A stop a third party's note names after the claim: an at-claim writer still takes
    it in at its next drain, an at-drain one skips it, and the exit's list says
    which."""
    h = _history(
        _claim0(H.Fold.AT_CLAIM),
        H.StopRequest("x"),
        H.ThirdPartyStop(
            0, H.Report(None), None, H.ThirdPartyFinal.NULL, H.Honors.UNDISCHARGED
        ),
        *_ticked_pass(0),
        *_exits(H.FinalStep.YIELDED),
    )
    b = _refusal(h, _worker("fold", frozenset({V.Fold.AT_DRAIN})))
    assert (E.WorkerFact.FOLD,) in b.names


def test_a_false_third_party_final_step_refuses() -> None:
    """A third party that writes a null ``final_step`` after a tick: ``progress`` says
    the tick's step."""
    h = _history(
        _claim0(H.Fold.AT_CLAIM),
        *_ticked_pass(0),
        H.ThirdPartyStop(
            0, H.Report(None), None, H.ThirdPartyFinal.NULL, H.Honors.NONE
        ),
        *_exits(H.FinalStep.YIELDED),
    )
    seq = next(r.seq for r in render(h).rows if r.topic == "lifecycle.stopped")
    edit = _third(seq, "final_step", frozenset({V.ThirdFinal.PROGRESS}))
    assert (E.ThirdFact.FINAL_STEP,) in _refusal(h, edit).names


def test_a_false_third_party_honored_refuses() -> None:
    """A third party that lists nothing after a stop no stopped has named:
    ``undischarged`` says the stop."""
    h = _history(
        _claim0(H.Fold.AT_CLAIM),
        H.StopRequest("x"),
        H.ThirdPartyStop(
            0, H.Report(None), None, H.ThirdPartyFinal.NULL, H.Honors.NONE
        ),
        *_ticked_pass(0),
        *_exits(H.FinalStep.YIELDED),
    )
    seq = next(r.seq for r in render(h).rows if r.topic == "lifecycle.stopped")
    edit = _third(seq, "honored", frozenset({V.ThirdHonored.UNDISCHARGED}))
    assert (E.ThirdFact.HONORED,) in _refusal(h, edit).names


def test_a_relaxation_too_wide_to_follow_is_undecided() -> None:
    """Blame names only a relaxation proven to admit a history; one with more states
    than its cap to follow is reported undecided, with the cap (review Important 3)."""
    h = _history(
        _claim0(H.Fold.AT_CLAIM),
        H.Begin(0, 0),
        H.Emit(0, "loss", H.Stamp.METRIC),
        *_exits(H.FinalStep.FINISHED),
    )
    cfg = _worker("final_step", frozenset({V.FinalStep.YIELDED}))(
        D.configuration([D.observe(h)])
    )
    b = E.blame(
        list(render(h).rows), cfg, born=D.born(h), cap=1, budget=BUDGET, run_on=RUN_ON
    )
    assert b.cap == 1 and b.undecided and not b.names


def test_a_budget_too_small_refuses_naming_the_record() -> None:
    """The states summed over the layers are bounded apart from the widest layer: past
    the budget the run is refused, naming the record it was reached at."""
    h = S.NAMED["golden DISPLACED"]
    rows = list(render(h).rows)
    cfg = D.configuration([D.observe(h)])
    with pytest.raises(E.Refused, match=r"more than 3 states summed over the layers"):
        E.candidates(rows, cfg, born=D.born(h), cap=CAP, budget=3, run_on=RUN_ON)


def test_a_refusal_can_name_every_fact() -> None:
    """The facts a refusal may name are every field of the two fact types."""
    assert {n.value for n in E.WorkerFact} == {f.name for f in fields(V.WorkerFacts)}
    assert {n.value for n in E.ThirdFact} == {f.name for f in fields(V.ThirdPartyFacts)}


def test_the_decode_reads_the_last_claim_first() -> None:
    """The critique's P6: RESUMED_BESIDE_A_REAL_BEAT, under the reference driver with
    any-save loads and in-pass saves a cut can finish, has three candidates: A {0, 1, 2}
    with C <- A@2; A {0, 1, 2} with C <- B@2; and A {0, 1} with C <- B@2. Per-claim
    choosers over a coupled set depend on their order, so the order is a stated rule:
    from the last claim back. An Origin chooser that takes the oldest holder, and a Span
    chooser that takes the least, then give C <- A@2, which forces A {0, 1, 2}; the
    first claim forward would give A {0, 1} and C <- B@2.
    """
    h = S.NAMED["golden RESUMED_BESIDE_A_REAL_BEAT"]
    cfg = D.configuration([D.observe(h)])
    loose = {
        k: replace(
            f,
            load=frozenset({V.Load.FRESH, V.Load.NEWEST, V.Load.OLDER}),
            save_cov=frozenset(V.Saved),
            save_at=frozenset({V.SaveAt.AT_CLOSE, V.SaveAt.IN_PASS}),
            cut_save=frozenset({V.CutSave.LANDED}),
        )
        for k, f in cfg.workers.items()
    }
    g = E.candidates(
        list(render(h).rows),
        cfg._replace(workers=loose),
        born=D.born(h),
        cap=CAP,
        budget=BUDGET,
        run_on=RUN_ON,
    )
    assert len(candidates_listed(g)) == 3

    def holder(a: E.Answer) -> int:
        """The oldest claim whose node the answer resumes from or continues."""
        nodes = [r.resume for r in a.value if isinstance(r, E.Resumes)]
        nodes += [r.parent for r in a.value if isinstance(r, E.PCommit)]
        return min((n.claim for n in nodes if n is not None), default=0)

    def oldest_then_least(q: E.Question) -> E.Answer:
        if len({holder(a) for a in q.answers}) > 1:
            return min(q.answers, key=holder)
        return min(q.answers, key=lambda a: len(a.value))

    got = readings_of(g.ctx, E.witness(E.decode(g, oldest_then_least, cap=PATHS)))
    a, c = got[1], got[12]
    assert c.origin == E.CommitId(1, 2) and len(a.span.commits) == 3
    forward = decoded_by_list(
        g.ctx, E.paths(g, cap=PATHS), oldest_then_least, [1, 8, 12, E.RUN]
    )
    assert forward[12].origin == E.CommitId(8, 0) and len(forward[1].span.commits) == 2


def test_a_tick_at_once_after_its_close_dates_the_commit() -> None:
    """§7, "The end-time fork": a commit whose close its own tick follows at once has
    that tick's ``t``, and no fork. Every pass of RESUMED_BESIDE_A_REAL_BEAT ticks at
    once after its close, so under its own facts each commit has one end time, the
    truth's. The five properties cannot see this rule go: without it the engine reports
    each such time as a fork between the record before the close and the tick, which
    holds the truth."""
    h = S.NAMED["golden RESUMED_BESIDE_A_REAL_BEAT"]
    assert times_over(run([h])) == {c: {t} for c, t in want(h).times.items()}


# ----- whether a tick dates a commit is a reading (the Task 22 review's Critical 1) ------


def _six_records() -> tuple[tuple[Row, ...], list[H.History]]:
    """The review's six-record ``save-points`` log, which four histories write: two
    claims, the second's three step-0 values, then its tick at step 0."""
    for rows, hs in by_log(FAMILIES["save-points"]()).items():
        if [r.topic for r in rows] == [
            "lifecycle.started",
            "value",
            "lifecycle.started",
            "value",
            "value",
            "lifecycle.heartbeat",
        ] and json.loads(rows[5].body) == {
            "step": 0,
            "consumed_seq": 0,
            "claim_seq": 3,
            "t": 6.0,
        }:
            return rows, hs
    raise AssertionError("the six-record log is gone from save-points")


def test_whether_a_tick_dates_a_commit_is_the_end_time_projections():
    """The tick may date claim 3's commit, or the commit may have ended before it,
    undated: detection reports the end-time fork, and each chooser writes its own log
    (``lower`` the commit before the tick, which stays a heartbeat; ``upper`` the
    commit in the tick's place)."""
    from runstate.migrations.ambiguity import REFUSE, Assumptions, Chosen, Limits
    from runstate.migrations.ambiguity import Resolutions as R

    rows, hs = _six_records()
    cfg = D.configuration([observed(h) for h in hs])
    step = STEP.V0_3_0_to_V0_4_0()
    limits = Limits(cap=CAP, budget=BUDGET, run_on=RUN_ON)
    found = step.detect(
        list(rows), assumptions=Assumptions(cfg, "x", limits), history=()
    )
    assert [f.seq for f in found.forks["end-time"]] == [3]
    assert not any(found.forks[p] for p in ("end", "span", "origin"))
    out = {}
    for name, chooser in (("lower", 0), ("upper", 1)):
        res = R(
            Assumptions(cfg, "x", limits),
            {
                "end": Chosen(None, REFUSE.chooser),
                "span": Chosen("fewest", STEP.FEWEST.chooser),
                "origin": Chosen("newest", STEP.NEWEST.chooser),
                "writer": Chosen(None, REFUSE.chooser),
                "end-time": Chosen(name, STEP.END_TIME.generic[chooser].chooser),
            },
        )
        m = step.transform(list(rows), resolutions=res, history=())
        out[name] = [(x.topic, json.loads(x.body).get("t")) for x in m.rows[6:]]
        assert m.readings["end-time"] == {
            3: {
                "0": (
                    {"is": "EndedAt", "after": 5, "t": 5.0}
                    if name == "lower"
                    else {"is": "DatedBy", "seq": 6}
                )
            }
        }
    assert out["lower"] == [("lifecycle.commit", 5.0), ("lifecycle.heartbeat", 6.0)]
    assert out["upper"] == [("lifecycle.commit", 6.0)]


class _TwoVersionsRead:
    """Every claim answered as two worker versions, keyed by strings, whose hash the
    seed changes (as mycooc's classifier answers several); every stopped the worker's
    own; a stepped value a pass's, a stepless one a register."""

    def __init__(self, log: Sequence[Row]) -> None:
        self._stepless = {
            x.seq
            for x in log
            if x.topic == "value" and json.loads(x.body)["step"] is None
        }

    def started(self, seq: int) -> frozenset[str]:
        return frozenset({"version-a", "version-b"})

    def stopped(self, seq: int) -> frozenset[V.StopAnswer]:
        return frozenset({V.OwnStop()})

    def value(self, seq: int) -> frozenset[V.ValueRole]:
        return frozenset({V.ValueRole(V.Role.PASS, seq in self._stepless)})


class TwoVersions:
    def read(self, log: Sequence[Row]) -> _TwoVersionsRead:
        return _TwoVersionsRead(log)


_SEEDED = """
import dataclasses, hashlib, json, sys
sys.path.insert(0, {root!r})
import runstate.migrations.v0_3_0_to_v0_4_0 as STEP
import runstate.migrations.v0_3_0_to_v0_4_0_engine as E
import runstate.migrations.v0_3_0_to_v0_4_0_facts as V
import tests.test_migration_goldens as T
import tests.test_migration_oracle as O
import tests.test_migration_v0_3_0_to_v0_4_0 as S
from runstate.migrations import MigrationError
d = hashlib.sha256()
for rid in T.G.GOLDEN:
    m = T.migrated(rid)
    d.update(repr((m.rows, sorted(m.readings.items()), sorted(m.divergences.items()))).encode())
for rows, hs in list(O.by_log(O.FAMILIES["positional"]()).items())[:60]:
    if O.parsed(hs[0]):
        g = O.run(hs)
        d.update(repr([[(e.src, e.dst, e.pieces) for e in es] for es in g.live]).encode())
a = S.CHECKPOINTED
b = dataclasses.replace(a, final_step=frozenset(V.FinalStep),
                        error_final_step=frozenset(V.FinalStep),
                        watermark=frozenset(V.Watermark))
two = V.Configuration({{"version-a": a, "version-b": b}}, {{}}, O.TwoVersions())
for rid, rows in T.G.GOLDEN.items():
    try:
        g = E.candidates(list(rows), two, born=V.Born030(), cap=3000,
                         budget=6_000_000, run_on=2)
        if not E.admits(g):
            continue
        m = S.migrated(list(rows), two, **S.CHOSEN)
    except MigrationError:
        continue
    d.update(repr([[(e.src, e.dst, e.pieces) for e in es] for es in g.live]).encode())
    d.update(repr((m.rows, sorted(m.readings.items()))).encode())
print(d.hexdigest())
"""


def test_the_step_writes_the_same_bytes_whatever_the_hash_seed():
    """Every set the pass's successors come from is iterated in a fixed order, and a
    classifier's answers in the configuration's declared order, so the states'
    numbering, the path written and the witnesses do not depend on ``PYTHONHASHSEED``:
    the goldens' outputs, 60 ``positional`` graphs, and the goldens under a classifier
    answering two string-keyed versions per claim (whose graphs numbered their states
    by hash order before fix round 2), under two seeds, in two processes."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = str(Path(__file__).resolve().parent.parent)
    digests = []
    for seed in ("1", "2"):
        done = subprocess.run(
            [sys.executable, "-c", _SEEDED.format(root=root)],
            capture_output=True,
            text=True,
            timeout=600,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        assert done.returncode == 0, done.stderr
        digests.append(done.stdout.strip())
    assert digests[0] == digests[1]


# ----- the horizon refuses only where its cut could change a reading (fix round 2) ----


class _AnyEnd:
    """An End chooser for a check of the refusals, not of the readings: the first
    answer."""

    def choose(self, q: E.Question) -> E.Answer:
        return q.answers[0]


def _true_resolutions(hs: Sequence[H.History]) -> Any:
    """The step's resolutions under the facts true of every history writing a log."""
    from runstate.migrations.ambiguity import Assumptions, Chosen, Limits
    from runstate.migrations.ambiguity import Resolutions as R

    choosers = {
        "end": Chosen("first", _AnyEnd()),
        "span": Chosen("fewest", STEP.FEWEST.chooser),
        "origin": Chosen("newest", STEP.NEWEST.chooser),
        "writer": Chosen("newest", STEP.WRITER_NEWEST.chooser),
        "end-time": Chosen("lower", STEP.END_TIME.generic[0].chooser),
    }
    cfg = D.configuration([observed(h) for h in hs])
    return R(Assumptions(cfg, "derived", Limits(CAP, BUDGET, RUN_ON)), choosers)


@pytest.mark.oracle
@pytest.mark.parametrize("family", list(FAMILIES))
def test_family_runs_are_never_refused_at_the_horizon_under_true_facts(
    family: str,
) -> None:
    """The step's own refusals, not the engine's: under the facts true of every
    history writing a log, no candidate closes a pass at the horizon with its loop free
    to continue, since no history runs on that far. A run the step refuses there is a
    cut nothing reads, refused (fix round 1 refused one ``rewinds`` log so)."""
    step = STEP.V0_3_0_to_V0_4_0()
    for rows, hs in by_log(FAMILIES[family]()).items():
        if not parsed(hs[0]):
            continue
        try:
            step.transform(
                list(rows),
                resolutions=_true_resolutions(hs),
                history=_born_history(hs[0]),
            )
        except MigrationError as exc:
            assert "horizon" not in str(exc), rows


def _the_rewinds_log() -> tuple[tuple[Row, ...], list[H.History]]:
    """The ``rewinds`` log fix round 1 refused at the horizon: 13 records, 40
    histories, a second claim at seq 10 that its stopped names. A pass begun at the
    horizon and cut there made no commit."""
    for rows, hs in by_log(FAMILIES["rewinds"]()).items():
        if (
            len(rows) == 13
            and len(hs) == 40
            and rows[9].topic == "lifecycle.started"
            and json.loads(rows[12].body).get("claim_seq") == 10
        ):
            return rows, hs
    raise AssertionError("the rewinds log is gone from its family")


def test_a_cut_nothing_reads_does_not_refuse() -> None:
    """Two runs fix round 1 refused at the horizon, each of which writes, as it did at
    bfa5c6c, byte for byte (checked at the time): a pass begun at the horizon and cut
    there, under ``stride`` {next, skip}; and a claim that loads from outside and dies
    before its first pass, whose loaded step nothing reads."""
    rows, hs = _the_rewinds_log()
    m = STEP.V0_3_0_to_V0_4_0().transform(
        list(rows), resolutions=_true_resolutions(hs), history=_born_history(hs[0])
    )
    assert not any(m.divergences.values())
    assert [x.topic for x in m.rows] == [
        "lifecycle.started",
        *["value", "lifecycle.commit", "lifecycle.heartbeat"] * 4,
        "supervisor.died",
        "lifecycle.started",
        "value",
        "lifecycle.commit",
        "lifecycle.stopped",
    ]
    from tests.test_migration_v0_3_0_to_v0_4_0 import REFERENCE, under
    from tests.migration_goldens import DEAD, _beat, _dated, _started, _stopped, r

    w = REFERENCE.workers["ref"]
    outside = REFERENCE._replace(
        workers={"ref": replace(w, load=w.load | {V.Load.OUTSIDE})}
    )
    loaded = _dated(
        [
            r(1, "lifecycle.started", _started(DEAD, 1.0)),
            r(2, "lifecycle.started", _started("local://n1/12", 2.0)),
            r(3, "lifecycle.heartbeat", _beat(0, 0, 2, 3.0)),
            r(4, "lifecycle.stopped", _stopped(True, None, 0, 2, [], 4.0)),
        ]
    )
    from runstate.migrations.ambiguity import Chosen

    choosers = {
        "end": Chosen("first", _AnyEnd()),
        "span": Chosen("fewest", STEP.FEWEST.chooser),
        "origin": Chosen("newest", STEP.NEWEST.chooser),
        "writer": Chosen("newest", STEP.WRITER_NEWEST.chooser),
        "end_time": Chosen("lower", STEP.END_TIME.generic[0].chooser),
    }
    for run_on in (0, 2, 8):
        res = under(outside, **choosers)
        res = res._replace(
            assumptions=res.assumptions._replace(
                limits=res.assumptions.limits._replace(run_on=run_on)
            )
        )
        m = STEP.V0_3_0_to_V0_4_0().transform(loaded, resolutions=res, history=())
        assert [x.topic for x in m.rows] == [
            "lifecycle.started",
            "supervisor.died",
            "lifecycle.started",
            "lifecycle.commit",
        ]
