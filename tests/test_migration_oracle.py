"""The oracle: the candidate engine of the 0.3.0 -> 0.4.0 step against the simulator of conforming 0.3.0 writers.

The two encodings of §7's target are independent: the step's engine (``runstate.migrations.v0_3_0_to_v0_4_0_engine``)
never imports ``tests/migration_sim``, and the simulator never imports the step. This module is the only one that
imports both. Where they disagree, the prose decides which is wrong (commits-and-lineage.md §7, "The referee").

A history's facts are derived from its events (``tests/migration_derive.py``): one version per claim and per third
party, each stating the classes its elements declare, and the oracle classifier's answers. So "correct under true
facts" is a mechanical check. Over every family the simulator enumerates and every named history, five properties:

1. **No silent answer:** where two histories write one log and differ in truth, the engine reports a fork in the
   projection where they differ, under the facts true of both.
2. **Every truth reachable:** under a history's own facts, its truth is a candidate.
3. **Every output defined:** no crash, and every refusal names a fact or a record.
4. **No unique wrong answer** under true facts.
5. **An order-free candidate set,** with the decode following its stated rule: last claim first, re-pruning after
   each choice.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator, Sequence
from typing import Any, NamedTuple

import pytest

import runstate.migrations.v0_3_0_to_v0_4_0_engine as E
import runstate.migrations.v0_3_0_to_v0_4_0_facts as V
import tests.migration_derive as D
import tests.test_migration_sim as S
from tests.migration_sim import history as H
from tests.migration_sim import truth as T
from tests.migration_sim.render import render

CAP = (
    60000  # states in one layer: the oracle's logs are small, and weak facts widen them
)

# ----- the two encodings' names, read alike ----------------------------------------------------------------------------


def _node(n: T.NodeId | None) -> E.Node | None:
    if n is None:
        return None
    if isinstance(n, T.CommitId):
        return E.CommitId(n.claim_seq, n.nth)
    return E.ExitId(n.claim_seq)


def _how(h: H.ProcessEnd) -> V.Exited | V.Killed:
    return V.Killed(h.signal) if isinstance(h, H.Killed) else V.Exited(h.code)


class Want(NamedTuple):
    """A truth in the engine's names: the lineage less each commit's ``t``, and the ``t``s apart."""

    starts: frozenset[tuple[int, E.Node | None]]
    commits: dict[E.CommitId, tuple[E.Node | None, tuple[int, ...]]]
    times: dict[E.CommitId, float]
    rewinds: frozenset[tuple[int, int, E.Node | None]]
    exits: dict[int, tuple[E.Node | None, tuple[int, ...] | None, str, tuple[str, ...]]]
    deaths: tuple[tuple[int, str, str], ...]
    terminations: tuple[tuple[str | None, Any], ...]
    withdrawals: frozenset[tuple[str, int]]


def want(t: T.Truth) -> Want:
    L = t.lineage
    return Want(
        frozenset((s.claim_seq, _node(s.resume_from)) for s in L.starts),
        {
            E.CommitId(c.node.claim_seq, c.node.nth): (_node(c.parent), c.values)
            for c in L.commits
        },
        {E.CommitId(c.node.claim_seq, c.node.nth): c.t for c in L.commits},
        frozenset((r.claim_seq, r.commits, _node(r.target)) for r in L.rewinds),
        {
            e.node.claim_seq: (_node(e.parent), e.values, e.kind.value, e.honored)
            for e in L.exits
        },
        tuple(sorted((d.claim_seq, d.outcome.value, d.source.value) for d in L.deaths)),
        tuple(sorted(((x.launch, _how(x.how)) for x in L.terminations), key=repr)),
        frozenset((w.request_id, w.by) for w in L.withdrawals),
    )


class Got(NamedTuple):
    """A candidate in the same form, each commit's ``t`` a set: the end-time fork's bounds, or its tick."""

    starts: frozenset[tuple[int, E.Node | None]]
    commits: dict[E.CommitId, tuple[E.Node | None, tuple[int, ...]]]
    times: dict[E.CommitId, frozenset[float]]
    rewinds: frozenset[tuple[int, int, E.Node | None]]
    exits: dict[int, tuple[E.Node | None, tuple[int, ...] | None, str, tuple[str, ...]]]
    deaths: tuple[tuple[int, str, str], ...]
    terminations: tuple[tuple[str | None, Any], ...]
    withdrawals: frozenset[tuple[str, int]]

    def key(self) -> tuple[Any, ...]:
        """Comparable, the end times left out (their fork is reported apart)."""
        return (
            self.starts,
            tuple(sorted(self.commits.items())),
            self.rewinds,
            tuple(sorted(self.exits.items())),
            self.deaths,
            self.terminations,
            self.withdrawals,
        )


def got(ctx: E.Context, pieces: Sequence[E.Piece]) -> Got:
    L = E.lineage(ctx, pieces, when=E.EndTime.LOWER)
    return Got(
        frozenset((s.claim, s.resume_from) for s in L.starts),
        {c.node: (c.parent, c.values) for c in L.commits},
        E.times(ctx, pieces),
        frozenset((r.claim, r.commits, r.target) for r in L.rewinds),
        {e.node.claim: (e.parent, e.values, e.kind.value, e.honored) for e in L.exits},
        tuple(sorted((d.claim, d.outcome.value, d.source.value) for d in L.deaths)),
        tuple(sorted(((x.launch, x.how) for x in L.terminations), key=repr)),
        frozenset((w.request_id, w.by) for w in L.withdrawals),
    )


def matches(g: Got, w: Want) -> bool:
    return (
        g.starts == w.starts
        and g.commits == w.commits
        and all(w.times[c] in g.times[c] for c in w.times)
        and g.rewinds == w.rewinds
        and g.exits == w.exits
        and g.deaths == w.deaths
        and g.terminations == w.terminations
        and g.withdrawals == w.withdrawals
    )


# ----- the truth-guided pass --------------------------------------------------------------------------------------------


def _fits(p: E.Piece, w: Want) -> bool:
    """Whether a piece can stand in the truth ``w``: the structural pieces are checked as they come, the rest (values,
    end times) on the whole path."""
    if isinstance(p, E.PStart):
        return (p.claim, p.resume) in w.starts
    if isinstance(p, E.PCommit):
        c = E.CommitId(p.claim, p.nth)
        return c in w.commits and w.commits[c][0] == p.parent
    if isinstance(p, E.PExit):
        e = w.exits.get(p.claim)
        return (
            e is not None
            and e[0] == p.parent
            and e[2] == p.kind.value
            and e[3] == p.honored
            and (e[1] is None) == p.discarded
        )
    if isinstance(p, E.PDied):
        return (p.claim, p.outcome.value, p.source.value) in w.deaths
    if isinstance(p, E.PWithdrawal):
        return (p.request_id, p.by) in w.withdrawals
    if isinstance(p, E.PRewind):
        return (p.claim, p.commits, p.target) in w.rewinds
    if isinstance(p, E.PRunEnd):
        return sum(1 for c in w.commits if c.claim == p.claim) == p.commits
    return True


def reachable(g: E.Graph, w: Want, *, cap: int) -> bool:
    """Whether some complete path of the graph writes the truth ``w``: a pass over the live edges that drops every
    edge with a piece the truth cannot hold, keeping per state the distinct piece sequences that reach it.
    """
    keep = [[e for e in es if all(_fits(p, w) for p in e.pieces)] for es in g.live]
    sub = E.Graph(g.ctx, g.layers, g.edges, keep, g.stopped_at, g.widest)
    try:
        found = E.chains(sub, cap=cap)
    except E.TooMany:
        pytest.fail("the truth-guided pass found too many paths to check")
    return any(matches(got(g.ctx, ps), w) for ps in found)


# ----- running a history -------------------------------------------------------------------------------------------------


def run(histories: Sequence[H.History]) -> E.Graph:
    """The engine on the log ``histories`` write, under the facts and answers true of every one of them."""
    rows = list(render(histories[0]).rows)
    cfg = D.configuration([D.observe(h) for h in histories])
    return E.candidates(rows, cfg, born=D.born(histories[0]), cap=CAP)


# ----- 2. every truth reachable ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", list(S.NAMED))
def test_every_named_truth_is_a_candidate(name: str) -> None:
    h = S.NAMED[name]
    g = run([h])
    assert E.admits(g), f"refused at record {g.stopped_at}"
    assert reachable(g, want(T.truth(h)), cap=4000)
