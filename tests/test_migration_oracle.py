"""The oracle: the candidate engine of the 0.3.0 -> 0.4.0 step against the simulator of
conforming 0.3.0 writers.

The two encodings of §7's target are independent: the step's engine
(``runstate.migrations.v0_3_0_to_v0_4_0_engine``) never imports ``tests/migration_sim``,
and the simulator never imports the step. This module is the only one that imports both.
Where they disagree, the prose decides which is wrong (commits-and-lineage.md §7, "The
referee").

A history's facts are derived from its events (``tests/migration_derive.py``): one
version per claim and per third party, each stating the classes its elements declare,
and the oracle classifier's answers. So "correct under true facts" is a mechanical
check. Over every family the simulator enumerates and every named history, five
properties:

1. **No silent answer:** where two histories write one log and differ in truth, the
   engine reports a fork in the projection where they differ, under the facts true of
   both.
2. **Every truth reachable:** under a history's own facts, its truth is a candidate.
3. **Every output defined:** no crash, and every refusal names a fact or a record.
4. **No unique wrong answer** under true facts.
5. **An order-free candidate set,** with the decode following its stated rule: last
   claim first, re-pruning after each choice.

A log the step's own parse refuses (M9: an in-claim rewind) is outside the engine's
domain: its refusal names the record, which property 3 accepts.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from dataclasses import replace
from functools import partial
from typing import Any, NamedTuple

import pytest

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
    t = T.truth(h)
    rows = render(h).rows
    named = {r.seq: _claim_seq(r) for r in rows if r.topic == "lifecycle.stopped"}
    claims = [r.seq for r in rows if r.topic == "lifecycle.started"]
    L = t.lineage
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
                (x.node.nth, _node(x.parent), x.values)
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


def run(histories: Sequence[H.History]) -> E.Graph:
    """The engine on the log ``histories`` write, under the facts and answers true of
    every one of them."""
    rows = list(render(histories[0]).rows)
    cfg = D.configuration([D.observe(h) for h in histories])
    return E.candidates(rows, cfg, born=D.born(histories[0]), cap=CAP)


def parsed(h: H.History) -> bool:
    """Whether the step's parse reads the log: what it refuses (M9) never reaches the
    engine."""
    try:
        parse(list(render(h).rows))
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
            nth == p.nth and parent == p.parent for nth, parent, _ in r.span.commits
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


class Report(NamedTuple):
    """What the engine says of a log: each subject's readings, and each commit's end
    times over every path."""

    readings: dict[int, set[E.Reading]]
    times: dict[E.CommitId, set[float]] | None  # None: too many paths to list


def report(g: E.Graph) -> Report:
    found = {s: set(E.readings(g, s, cap=PATHS)) for s in E.subjects(g)}
    times: dict[E.CommitId, set[float]] | None = defaultdict(set)
    try:
        for ps in E.chains(g, cap=PATHS):
            for c, ts in E.times(g.ctx, ps).items():
                times[c] |= ts  # type: ignore[index]
    except E.TooMany:
        times = None
    return Report(found, None if times is None else dict(times))


def unique(r: Report) -> bool:
    return all(len(rs) == 1 for rs in r.readings.values())


# ----- the histories ------------------------------------------------------------------


def families() -> dict[str, Callable[[], Iterator[H.History]]]:
    out: dict[str, Callable[[], Iterator[H.History]]] = {
        name: partial(S._histories, f) for name, f in S.FAMILIES.items()
    }
    out["slot"] = lambda: enumeration.slot(beats=4, run_on=1)
    out["rewinds"] = lambda: enumeration.rewinds(passes=3)
    out["ended-stops"] = enumeration.ended_stops
    return out


FAMILIES = families()


def by_log(histories: Iterator[H.History]) -> dict[tuple[Row, ...], list[H.History]]:
    groups: dict[tuple[Row, ...], list[H.History]] = defaultdict(list)
    for h in histories:
        groups[render(h).rows].append(h)
    return groups


def distinct(hs: Sequence[H.History]) -> list[H.History]:
    """One history per distinct truth."""
    seen: dict[T.Lineage, H.History] = {}
    for h in hs:
        seen.setdefault(T.truth(h).lineage, h)
    return list(seen.values())


# ----- 2, 3 and 4: each history under its own facts -----------------------------------


def own(h: H.History) -> None:
    """Under the history's own facts: no crash and no refusal (3); its truth a candidate
    (2); and where the engine reads one lineage, that lineage is the truth (4)."""
    if not parsed(h):
        return  # the step's parse refuses it, naming the record (M9)
    g = run([h])
    assert E.admits(
        g
    ), f"refused at record {g.stopped_at} under the history's own facts"
    w = want(h)
    assert reachable(g, w, cap=PATHS), "the history's truth is no candidate"
    r = report(g)
    if unique(r):
        assert {s: next(iter(rs)) for s, rs in r.readings.items()} == w.readings
        if r.times is not None:
            for c, ts in r.times.items():
                if len(ts) == 1:
                    assert w.times[c] in ts


@pytest.mark.parametrize("name", list(S.NAMED))
def test_named_histories_under_their_own_facts(name: str) -> None:
    own(S.NAMED[name])


@pytest.mark.parametrize("family", list(FAMILIES))
def test_families_under_their_own_facts(family: str) -> None:
    for h in FAMILIES[family]():
        own(h)


# ----- 1: no silent answer ------------------------------------------------------------


PROJECTIONS: tuple[tuple[str, Callable[[E.Reading], object]], ...] = (
    ("origin", lambda r: r.origin),
    ("end", lambda r: r.end),
    ("span", lambda r: r.span),
)


def silent(one: H.History, other: H.History) -> list[str]:
    """Where the engine, under the facts true of both, reports no fork where two
    histories' truths differ: each such subject and projection, or end time."""
    g = run([one, other])
    assert E.admits(g), "refused under the facts true of both"
    a, b = want(one), want(other)
    r = report(g)
    found: list[str] = []
    for s, ra in a.readings.items():
        rb = b.readings[s]
        for part, of in PROJECTIONS:
            va, vb = of(ra), of(rb)
            if va != vb and not {va, vb} <= {of(x) for x in r.readings[s]}:
                found.append(f"claim {s} {part}")
    if a.readings == b.readings and r.times is not None:
        for c, t in a.times.items():
            if t != b.times[c] and not {t, b.times[c]} <= r.times[c]:
                found.append(f"{c} end time")
    return found


def test_named_pairs_are_forks() -> None:
    for pair in S.AMBIGUOUS:
        one, other = S.NAMED[pair.one], S.NAMED[pair.other]
        assert silent(one, other) == [], (pair.one, pair.other)


@pytest.mark.parametrize("family", list(FAMILIES))
def test_family_logs_two_truths_write_are_forks(family: str) -> None:
    for hs in by_log(FAMILIES[family]()).values():
        reps = [h for h in distinct(hs) if parsed(h)]
        for k, one in enumerate(reps):
            for other in reps[k + 1 :]:
                assert silent(one, other) == []


# ----- 5: the candidate set is order-free, and the decode follows its rule ------------


def candidates_listed(g: E.Graph) -> list[dict[int, E.Reading]]:
    """The candidates, listed: each complete path's readings, once."""
    seen: dict[tuple[Any, ...], dict[int, E.Reading]] = {}
    for ps in E.chains(g, cap=PATHS):
        rs = readings_of(g.ctx, ps)
        seen.setdefault(tuple(sorted(rs.items())), rs)
    return list(seen.values())


def first(subject: int, found: Sequence[E.Reading]) -> E.Reading:
    return min(found, key=repr)


def last(subject: int, found: Sequence[E.Reading]) -> E.Reading:
    return max(found, key=repr)


def decoded_by_list(
    listed: list[dict[int, E.Reading]], subjects: Sequence[int], pick: E.Chooser
) -> dict[int, E.Reading]:
    """The decode's rule applied to the explicit list: last claim first, keeping the
    candidates each choice leaves."""
    left = listed
    for s in subjects:
        found = sorted({c[s] for c in left}, key=repr)
        if len(found) > 1:
            choice = pick(s, found)
            assert choice is not None
            left = [c for c in left if c[s] == choice]
    assert len({tuple(sorted(c.items())) for c in left}) == 1
    return left[0]


def ordered(h: H.History | Sequence[H.History]) -> None:
    hs = [h] if isinstance(h, H.History) else list(h)
    if not parsed(hs[0]):
        return
    g = run(hs)
    try:
        listed = candidates_listed(g)
    except E.TooMany:
        return  # too many to list: the engine's own sets stand alone
    # order-free: each subject's readings, as the engine computes them per claim, are
    # the listed candidates'
    for s in E.subjects(g):
        assert set(E.readings(g, s, cap=PATHS)) == {c[s] for c in listed}
    # the decode: last claim first, re-pruning after each choice, as the stated rule
    # over the list
    for pick in (first, last):
        d = E.decode(g, pick, cap=PATHS)
        got = candidates_listed(d)
        assert len(got) == 1
        assert got[0] in listed
        assert got[0] == decoded_by_list(listed, E.subjects(g), pick)


@pytest.mark.parametrize("name", list(S.NAMED))
def test_named_decodes_follow_the_rule(name: str) -> None:
    ordered(S.NAMED[name])


def test_named_pairs_decode_by_the_rule() -> None:
    for pair in S.AMBIGUOUS:
        ordered([S.NAMED[pair.one], S.NAMED[pair.other]])


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


# A refusal's names cost a relaxed pass per stated fact, each as long as the log. The
# named histories longer than this, d78b72f1's three (370 to 4,243 records), state
# false no fact and class that a shorter one does not (measured 2026-10-26: 41 in all).
FALSE_FACT_RECORDS = 200


@pytest.mark.parametrize(
    "name",
    [n for n, h in S.NAMED.items() if len(render(h).rows) <= FALSE_FACT_RECORDS],
)
def test_a_false_fact_refuses_naming_it(name: str) -> None:
    """Each fact a claim's elements exercise, stated without the class one element
    declares: where the log then admits no history, the refusal names that fact."""
    h = S.NAMED[name]
    if not parsed(h):
        return
    ob = D.observe(h)
    cfg = D.configuration([ob])
    rows = list(render(h).rows)
    for seq, facts in ob.claims.items():
        version = f"claim@{seq}"
        for fact, elements in facts.items():
            if fact not in E._WORKER_DOMAINS or not elements:
                continue
            declared = getattr(cfg.workers[version], fact)
            for cls in sorted(declared, key=repr):
                false = _narrowed(cfg, version, fact, declared - {cls})
                g = E.candidates(rows, false, born=D.born(h), cap=CAP)
                if not E.admits(g):
                    named = E.blame(rows, false, born=D.born(h), cap=BLAME_CAP)
                    assert any(fact in n.split(" + ") for n in named), (
                        fact,
                        cls,
                        named,
                    )


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
        list(render(h).rows), cfg._replace(workers=loose), born=D.born(h), cap=CAP
    )
    listed = candidates_listed(g)
    assert len(listed) == 3

    def holder(r: E.Reading) -> int:
        return r.origin.claim if r.origin is not None else 0

    def oldest_then_least(subject: int, found: Sequence[E.Reading]) -> E.Reading:
        if len({r.origin for r in found}) > 1:
            return min(found, key=holder)
        return min(found, key=lambda r: len(r.span.commits))

    got = candidates_listed(E.decode(g, oldest_then_least, cap=PATHS))
    assert len(got) == 1
    a, c = got[0][1], got[0][12]
    assert c.origin == E.CommitId(1, 2) and len(a.span.commits) == 3
    forward = decoded_by_list(
        listed, sorted(s for s in E.subjects(g) if s), oldest_then_least
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
    r = report(run([h]))
    assert r.times == {c: {t} for c, t in want(h).times.items()}
