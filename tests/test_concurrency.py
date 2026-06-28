"""Concurrency sub-suite: the substrate under *real* contention, organized by the
contention TIER a backend supports (see ``conftest.conc_backend`` + the ``tier``
marker). The sequential conformance suite can't see races -- the CAS-atomicity P0
(F1) slipped past it -- so these race the CAS the substrate's claims rest on.

Tiers, increasing in strength:

- ``in_process`` -- N threads, every backend. The CAS race here lives in
  ``test_channel.py::test_concurrent_cas_admits_exactly_one_winner``.
- ``cross_process`` -- N real OS processes on one log; a file/networked backend only
  (memory is in-process). This module.
- ``cross_host`` -- the claim oracle under cross-host contention; the postgres TDD
  target (no backend reaches it yet, so those tests skip everywhere for now).
"""

import multiprocessing as mp
import sys
import threading

import pytest

from runstate import Worker
from runstate.channel import open_channel as locate


def _race_cas(i, root, run_id, backend, journal, fire, result_q):
    """One racer process: open the run's channel, line up at the barrier, then fire
    the birth-CAS on the empty log. Reports ``("ok", won)`` or ``("error", repr)`` so
    the parent never blocks on a crashed child. Top-level so fork/spawn can reach it;
    the journal mode travels in the args so a spawned child sets it before opening."""
    import os

    try:
        if journal:
            os.environ["RUNSTATE_SQLITE_JOURNAL_MODE"] = journal
        ch = locate(run_id, root=root, backend=backend)
        try:
            fire.wait(timeout=15)               # overlap the check+INSERT windows
            won = ch.send({"who": i}, topic="lifecycle.started", expected_seq=0) is not None
        finally:
            ch.close()
    except BaseException as exc:                 # a loser must get None, never raise (e.g. "database is locked")
        result_q.put(("error", repr(exc)))
        return
    result_q.put(("ok", won))


@pytest.mark.tier("cross_process")
def test_cas_admits_one_winner_across_processes(conc_backend):
    """N real OS processes race the birth-CAS on one log -> exactly one wins; the
    losers get ``None``, never a "database is locked" error. The cross-process tier
    (skipped for in-process-only backends): the stronger claim a file/networked backend
    must hold under true parallelism (no shared GIL), and the shape the postgres claim
    oracle extends to cross-host."""
    if "fork" not in mp.get_all_start_methods():
        pytest.skip("the cross_process racer needs the 'fork' start method")
    n = 8
    ctx = mp.get_context("fork")
    for trial in range(3):                       # races are flaky; several trials
        run_id = f"race-{trial}"
        locate(run_id, root=conc_backend.root, backend=conc_backend.backend).close()  # create the file/schema
        fire = ctx.Barrier(n)
        q = ctx.Queue()
        procs = [
            ctx.Process(
                target=_race_cas,
                args=(i, conc_backend.root, run_id, conc_backend.backend,
                      conc_backend.journal, fire, q),
            )
            for i in range(n)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=30)

        results = [q.get(timeout=10) for _ in range(n)]
        errors = [r[1] for r in results if r[0] == "error"]
        wins = [r[1] for r in results if r[0] == "ok"]
        assert not errors, f"[trial {trial}] racers raised (losers must get None): {errors}"
        assert sum(wins) == 1, f"[trial {trial}] expected exactly 1 winner by return value, got {sum(wins)}"
        # Harvest the LOG, not just the return values: the original F1 signature is
        # a loser that INSERTS a row but returns None -- a second lifecycle.started
        # the return-count can't see. Exactly one winner must leave exactly one record.
        seed = locate(run_id, root=conc_backend.root, backend=conc_backend.backend)
        try:
            started = seed.read(topics=["lifecycle.started"])
        finally:
            seed.close()
        assert len(started) == 1, (
            f"[trial {trial}] CAS left {len(started)} lifecycle.started rows "
            f"(an inserted-but-unacked loser -- the F1 corruption signature)")


def _claim_then_emit(i, root, run_id, backend, claimed_barrier, results):
    """Thread body: construct a Worker (the birth-CAS race), record the verdict, then
    HOLD at a barrier so the winner stays LIVE while the loser claims. Without the hold,
    a fast 1-step winner stops before the loser contends and the loser then legitimately
    starts episode 2 (the run-episodes model: a run hosts many sequential episodes) -- no
    muzzle to observe. With the hold, the loser always faces a live winner and is muzzled."""
    ch = locate(run_id, root=root, backend=backend)
    with Worker(ch, now=lambda: 0.0) as w:       # the claim races here
        results[i] = w.claimed
        claimed_barrier.wait(timeout=10)         # winner held live until the loser has claimed
        for _step in w.steps(total=1):
            w.set("loss", float(i))              # only the winner runs; the loser's steps() is a no-op


@pytest.mark.tier("in_process")
def test_two_workers_racing_the_claim_muzzle_the_loser(conc_backend):
    """Two Worker constructors contend for one run -> exactly one claims a live episode;
    the loser is muzzled (no second lifecycle.started, no loser value, no loser stopped).
    The barrier holds the winner live across the loser's claim, so this pins the
    single-live-episode guard + the loser muzzle under a *real* claim race (read ->
    live_episode -> CAS -> retry), not the pre-seeded short-circuit the sequential muzzle
    test (test_worker.py) exercises."""
    n = 2
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-9)                  # fine interleaving so the claim's internal race is real
    try:
        for trial in range(5):
            run_id = f"claim-{trial}"
            seed = locate(run_id, root=conc_backend.root, backend=conc_backend.backend)
            seed.send({"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="obs")
            seed.close()
            claimed_barrier = threading.Barrier(n)
            results = [None] * n
            threads = [
                threading.Thread(
                    target=_claim_then_emit,
                    args=(i, conc_backend.root, run_id, conc_backend.backend, claimed_barrier, results),
                )
                for i in range(n)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=15)

            check = locate(run_id, root=conc_backend.root, backend=conc_backend.backend)
            try:
                started = check.read(topics=["lifecycle.started"])
                values = check.read(topics=["value"], name="loss")
            finally:
                check.close()
            assert results.count(True) == 1, f"[trial {trial}] expected one claimant, got {results}"
            assert len(started) == 1, f"[trial {trial}] {len(started)} lifecycle.started rows (double-live claim)"
            assert len(values) == 1, (
                f"[trial {trial}] {len(values)} loss values (the loser was not muzzled): "
                f"{[v.body for v in values]}")
    finally:
        sys.setswitchinterval(old)
