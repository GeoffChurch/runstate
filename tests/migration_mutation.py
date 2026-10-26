"""A's mutation test of the candidate engine (the plan's Task 20, Step 3; arch-A.md).

Each mutation narrows one of the engine's candidate rules: it drops a reading, a branch
or a bound that some conforming history needs. The oracle
(``tests/test_migration_oracle.py``) must then fail: a history whose truth is no longer
a candidate (property 2), or two histories one log writes that the engine no longer
tells apart (property 1). A mutation the oracle does not catch is a blind spot of the
oracle: no family or named history needs that rule. It is recorded, and closed by a
family or argued harmless.

Run from the repository root: ``python -m tests.migration_mutation [name ...]``. It
prints, per mutation, the first check that caught it, or ``SURVIVED``.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import partial
from typing import Any

import runstate.migrations.v0_3_0_to_v0_4_0_engine as E
import tests.test_migration_oracle as O
import tests.test_migration_sim as S
from runstate.migrations.v0_3_0_to_v0_4_0_facts import Load

Patch = dict[str, Callable[..., Any]]


def _without(
    original: Callable[..., Iterator[Any]], drop: Callable[[Any], bool]
) -> Callable[..., Iterator[Any]]:
    """A generator rule with the outcomes ``drop`` names removed."""

    def narrowed(*args: Any, **kwargs: Any) -> Iterator[Any]:
        return (x for x in original(*args, **kwargs) if not drop(x))

    return narrowed


def _mutations() -> dict[str, Patch]:
    cut, offers, loads, emit, rewinds = E._cut, E._offers, E._loads, E._emit, E._rewinds
    acted, settle, covered, ahead, reading = (
        E._acted,
        E._settle,
        E._covered,
        E._ahead_of,
        E.reading,
    )
    return {
        # a cut pass: no save begun; finished by its save; a landed save that did not
        # finish it
        "cut: no save begun": {
            "_cut": _without(cut, lambda c: not c.pieces and not c.kept)
        },
        "cut: finished by its save": {"_cut": _without(cut, lambda c: bool(c.pieces))},
        "cut: a landed save kept unfinished": {"_cut": _without(cut, lambda c: c.kept)},
        # a pass's close: at a value, or otherwise
        "close: at a value": {
            "_closes": lambda f, x, _c=E._closes: _c(f, x)
            and x.last not in (E.Last.EITHER, E.Last.CLOSES)
        },
        "close: other": {
            "_closes": lambda f, x, _c=E._closes: _c(f, x)
            and x.last in (E.Last.EITHER, E.Last.CLOSES)
        },
        "cut: its landed save promised": {"_landed": lambda c: []},
        "save: the silent act": {"_save_act": lambda f, x: None},
        "labels: skipped": {
            "_labels": lambda f, x, bound, _l=E._labels: [
                b for b in _l(f, x, bound) if b == x.floor
            ]
        },
        "continue: unsaved": {
            "_covered": lambda stated, slot, x: (
                covered(stated, slot, x)
                if x.closed is None
                else E._made(slot, x, x.closed)
            )
        },
        "rewind: root": {"_rewinds": _without(rewinds, lambda r: r[0] is None)},
        "rewind: a save": {"_rewinds": _without(rewinds, lambda r: r[0] is not None)},
        # what a worker may save
        "offer: before its loop": {
            "_offers": lambda ctx, w: (
                frozenset() if w.stage is E.Stage.LOADING else offers(ctx, w)
            )
        },
        "offer: in a pass": {
            "_offers": lambda ctx, w: (
                frozenset() if w.stage is E.Stage.ITERATING else offers(ctx, w)
            )
        },
        "offer: between passes": {
            "_offers": lambda ctx, w: (
                frozenset() if w.stage is E.Stage.BETWEEN else offers(ctx, w)
            )
        },
        "offer: after its exit": {
            "_offers": lambda ctx, w: (
                frozenset() if w.stage is E.Stage.EXITED else offers(ctx, w)
            )
        },
        "offer: a closed pass's state": {"_completed": lambda ctx, w, node: None},
        "offer: an older promise": {
            "_offer": lambda ctx, i, slot, older, hs, _o=E._offer: (
                x for x in _o(ctx, i, slot, older, hs) if x[1] == older
            )
        },
        # what a claim loads
        "load: fresh": {
            "_loads": _without(loads, lambda ld: ld.origin is E.Origin.FRESH)
        },
        "load: restart": {
            "_loads": _without(loads, lambda ld: ld.origin is E.Origin.RESTART)
        },
        "load: a save": {
            "_loads": _without(loads, lambda ld: ld.origin is E.Origin.NODE)
        },
        "load: outside": {
            "_loads": _without(loads, lambda ld: ld.origin is E.Origin.OUTSIDE)
        },
        "load: an older save": {
            "_loads": lambda ctx, f, world: loads(ctx, _no_older(f), world)
        },
        "clock: zero": {
            "_idles": lambda f, loaded, _i=E._idles: [
                x for x in _i(f, loaded) if not (x == 0 and loaded)
            ]
        },
        "clock: loaded": {
            "_idles": lambda f, loaded, _i=E._idles: [
                x for x in _i(f, loaded) if x != loaded or not loaded
            ]
        },
        # values, ticks and exits
        "value: a register": {
            "_emit": _without(
                emit, lambda yp: not any(isinstance(p, E.PHold) for p in yp[1])
            )
        },
        "value: a metric": {
            "_emit": _without(
                emit, lambda yp: any(isinstance(p, E.PHold) for p in yp[1])
            )
        },
        "value: a stray": {"_stray": lambda ctx, world, rec, roles: False},
        "value: another claim's worker": {
            "_emit": lambda ctx, w, rec, roles: (
                emit(ctx, w, rec, roles) if not E._owing(w) else iter(())
            )
        },
        "tick: dates its close": {"_dated": lambda x: None},
        "exit: discards": {
            "_discards": lambda f, open_pass, _d=E._discards: [
                d for d in _d(f, open_pass) if not d
            ]
        },
        "exit: keeps": {
            "_discards": lambda f, open_pass, _d=E._discards: [
                d for d in _d(f, open_pass) if d
            ]
            or ([] if open_pass else [False])
        },
        # third parties, the gate, a worker's end
        "third: the named worker ends first": {
            "_ahead_of": lambda ctx, i, world, rec: (
                x
                for x in ahead(ctx, i, world, rec)
                if not x[1] or any(isinstance(p, E.PCommit) for p in x[1])
            )
        },
        "third: a commit before the withdrawal": {
            "_ahead_of": lambda ctx, i, world, rec: (
                x
                for x in ahead(ctx, i, world, rec)
                if not any(isinstance(p, E.PCommit) for p in x[1])
            )
        },
        "gate: a live takeover": {"_gate": _no_live_takeover()},
        "owing: halts after its act": {
            "_acted": lambda ctx, i, world, w: (
                x
                for x in acted(ctx, i, world, w)
                if any(y.claim == w.claim for y in x.workers)
            )
        },
        "owing: lives on": {
            "_acted": lambda ctx, i, world, w: (
                x
                for x in acted(ctx, i, world, w)
                if not E._owing(w) or not any(y.claim == w.claim for y in x.workers)
            )
        },
        "settle: stays alive": {
            "_settle": lambda ctx, i, world, rec, must_end: (
                x
                for x in settle(ctx, i, world, rec, must_end)
                if {y.claim for y in x[0].workers} != {y.claim for y in world.workers}
                or x[1]
            )
        },
        "writers: any live worker (0.2.0)": {
            "_writers": lambda ctx, world, rec: [
                w for w in world.workers if w.claim == rec.claim
            ]
        },
        "drain: a tick or exit": {"_drain": lambda ctx, world, w, seq: w},
        "time: upper bounds": {
            "bounds": lambda ctx, own, ended, _b=E.bounds: (_b(ctx, own, ended)[0], ())
        },
        # the projections
        "reading: deaths": {
            "reading": lambda ctx, s, ps: _blank(reading(ctx, s, ps), deaths=True)
        },
        "reading: withdrawals": {
            "reading": lambda ctx, s, ps: _blank(reading(ctx, s, ps), withdrawals=True)
        },
        "reading: exit": {
            "reading": lambda ctx, s, ps: _blank(reading(ctx, s, ps), exit=True)
        },
        "reading: origin": {
            "reading": lambda ctx, s, ps: reading(ctx, s, ps)._replace(origin=None)
        },
    }


def _no_older(f: Any) -> Any:
    from dataclasses import replace

    return replace(f, load=f.load - {Load.OLDER})


def _no_live_takeover() -> Callable[..., Iterator[Any]]:
    gate = E._gate

    def narrowed(
        ctx: E.Context, i: int, world: E.World, rec: E.Rec, pieces: tuple[E.Piece, ...]
    ) -> Iterator[Any]:
        p = world.latest
        if (
            p
            and any(w.claim == p for w in world.workers)
            and not (p in world.exited or p in world.released)
        ):
            return iter(())
        return gate(ctx, i, world, rec, pieces)

    return narrowed


def _blank(
    r: E.Reading, *, deaths: bool = False, withdrawals: bool = False, exit: bool = False
) -> E.Reading:
    end = r.end._replace(
        deaths=() if deaths else r.end.deaths,
        withdrawals=() if withdrawals else r.end.withdrawals,
    )
    span = r.span._replace(exit=None) if exit else r.span
    return r._replace(end=end, span=span)


@contextmanager
def applied(patch: Patch) -> Iterator[None]:
    saved = {name: getattr(E, name) for name in patch}
    try:
        for name, fn in patch.items():
            setattr(E, name, fn)
        yield
    finally:
        for name, fn in saved.items():
            setattr(E, name, fn)


def checks() -> Iterator[tuple[str, Callable[[], None]]]:
    """The oracle's checks, cheapest first: the named histories, the named pairs, the
    named rules the five properties cannot see, then each family."""
    for name, h in S.NAMED.items():
        yield f"named {name}", partial(O.own, h)
    for pair in S.AMBIGUOUS:
        one, other = S.NAMED[pair.one], S.NAMED[pair.other]
        yield f"pair {pair.one} | {pair.other}", partial(_no_silence, one, other)
    yield "the decode's order (P6)", O.test_the_decode_reads_the_last_claim_first
    yield "a tick dates its close", O.test_a_tick_at_once_after_its_close_dates_the_commit
    for fam, make in O.FAMILIES.items():
        yield f"family {fam}", partial(_family_own, make)
    for fam, make in O.FAMILIES.items():
        yield f"family {fam} pairs", partial(_family_pairs, make)


def _family_own(make: Callable[[], Iterator[Any]]) -> None:
    for h in make():
        O.own(h)


def _no_silence(one: Any, other: Any) -> None:
    assert O.silent(one, other) == []


def _family_pairs(make: Callable[[], Iterator[Any]]) -> None:
    for hs in O.by_log(make()).values():
        reps = [h for h in O.distinct(hs) if O.parsed(h)]
        for k, one in enumerate(reps):
            for other in reps[k + 1 :]:
                assert O.silent(one, other) == []


def caught(patch: Patch) -> str | None:
    """The first check that fails under the mutation, or None."""
    with applied(patch):
        for name, check in checks():
            try:
                check()
            except (
                Exception
            ) as exc:  # an assertion, a refusal, a crash: each is the oracle failing
                return f"{name}: {type(exc).__name__}"
    return None


def main(argv: list[str]) -> int:
    mutations = _mutations()
    chosen = argv or list(mutations)
    survivors = []
    for name in chosen:
        t0 = time.time()
        found = caught(mutations[name])
        print(f"{name}: {found or 'SURVIVED'} ({time.time() - t0:.0f}s)", flush=True)
        if found is None:
            survivors.append(name)
    print(f"survivors: {survivors}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
