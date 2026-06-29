"""Concurrency sub-suite: the substrate under *real* contention, organized by the
contention TIER a backend supports (see ``conftest.conc_backend`` + the ``tier``
marker). The sequential conformance suite can't see races -- the CAS-atomicity P0
(F1) slipped past it -- so these race the CAS the substrate's claims rest on.

Tiers, increasing in strength (all live here now; test_channel.py keeps the
deterministic CAS conformance + the fault-injection tests -- the flakiness firewall):

- ``in_process`` -- N threads, every backend (the multi-handle CAS race, the
  shared-handle race, the memory seq-RMW, the two-Worker muzzle).
- ``cross_process`` -- N real OS processes on one log; a file/networked backend only
  (memory is in-process).
- ``cross_host`` -- the shared-log CAS as the cross-host claim arbiter under
  multi-client contention; reached by postgres (one server = one total order;
  with one CI server this is multi-client-to-one-server, not literally multi-host).
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
    must hold under true parallelism (no shared GIL), and the shape postgres's shared-log
    CAS extends to cross-host (multi-client to one server)."""
    if "fork" not in mp.get_all_start_methods():
        pytest.skip("the cross_process racer needs the 'fork' start method")
    n = 8
    ctx = mp.get_context("fork")
    for trial in range(3):                       # races are flaky; several trials
        run_id = f"{conc_backend.namespace}-race-{trial}"
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
            run_id = f"{conc_backend.namespace}-claim-{trial}"
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


# ===================== in_process tier (relocated) =====================
# The in-process CAS races + the memory seq-RMW vector, gathered here so every
# *real*-contention test lives in one tier-organized home; test_channel.py keeps
# the deterministic CAS conformance + fault-injection (the flakiness firewall).


def test_concurrent_cas_admits_exactly_one_winner(open_channel):
    """A concurrent multi-handle CAS must admit exactly one winner.

    ``send(expected_seq=)`` is the primitive the run-episodes self-claim and the
    §12.1 single-spawn guard rest on: of N workers racing to claim a run, exactly
    one wins ``send(..., expected_seq=last)`` and the rest get ``None``. The
    sequential CAS test (test_channel.py) can't see the race; this opens N handles
    on the SAME run (separate sqlite connections; the registry-shared memory
    log+lock) and fires them through a barrier. Several trials, because a racy CAS
    only *sometimes* admits >1 winner.
    """
    n = 8
    seed = open_channel()
    for trial in range(10):
        log = seed.read()
        last = log[-1].seq if log else 0  # each trial's winner advances this
        barrier = threading.Barrier(n)
        results: list = [None] * n
        errors: list = []

        def claim(i):
            try:
                ch = open_channel()
                barrier.wait(timeout=10)  # line up so the check+INSERT windows overlap
                results[i] = ch.send(
                    {"who": i}, topic="lifecycle.started", expected_seq=last
                )
            except BaseException as exc:  # a loser must get None back, never an error
                errors.append(exc)
                barrier.abort()  # don't leave peers waiting on a dead participant

        threads = [threading.Thread(target=claim, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        winners = [r for r in results if r is not None]
        # A loser waits out the winner's write lock (busy_timeout) and reads the
        # new seq; it must come back None, never a "database is locked" error.
        assert not errors, f"[trial {trial}] losing claimants raised: {errors!r}"
        assert len(winners) == 1, (
            f"[trial {trial}] expected exactly 1 winner, got {len(winners)}: {results}"
        )


def test_shared_handle_concurrent_sends_are_serialized(ch):
    """ONE channel instance used from several threads must serialize internally.

    This is the ThreadLauncher topology: the worker thread and the orchestrator's
    Watcher hold the SAME handle (launcher.py hands one channel to both sides).
    The multi-handle race test above can't see it — each thread there opens its
    own connection. Here mixed traffic (CAS claims + plain sends) hammers one
    instance: every acknowledged send must actually be in the log (an ack that a
    concurrent CAS's rollback can erase is corruption), at most one CAS wins, and
    nobody sees an error (e.g. sqlite's "cannot start a transaction within a
    transaction" from two CAS sends sharing one connection).
    """
    n = 8
    for trial in range(10):
        log = ch.read()
        last = log[-1].seq if log else 0
        len_before = len(log)
        barrier = threading.Barrier(n)
        results: list = [None] * n
        errors: list = []

        def send(i):
            try:
                barrier.wait(timeout=10)  # overlap the send windows
                if i % 2:
                    results[i] = ch.send({"who": i}, topic="value", name="plain")
                else:
                    results[i] = ch.send(
                        {"who": i}, topic="lifecycle.started", expected_seq=last
                    )
            except BaseException as exc:
                errors.append(exc)
                barrier.abort()  # don't leave peers waiting on a dead participant

        threads = [threading.Thread(target=send, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"[trial {trial}] concurrent sends raised: {errors!r}"
        winners = [results[i] for i in range(0, n, 2) if results[i] is not None]
        # 0 winners is legitimate (a plain send can land first and move MAX(seq))
        assert len(winners) <= 1, f"[trial {trial}] CAS admitted {len(winners)} winners"
        acked = [r for r in results if r is not None]
        assert len(set(acked)) == len(acked), f"[trial {trial}] duplicate seqs: {acked}"
        in_log = {e.seq for e in ch.read()}
        missing = sorted(s for s in acked if s not in in_log)
        assert not missing, f"[trial {trial}] acknowledged sends erased: {missing}"
        # and nothing landed un-acked (a CAS that inserted but reported None)
        assert len(in_log) - len_before == len(acked), (
            f"[trial {trial}] log grew by {len(in_log) - len_before}, acked {len(acked)}"
        )


def test_concurrent_writers_produce_unique_contiguous_seqs(tmp_path):
    """The memory backend's hand-rolled seq read-modify-write must be atomic across
    instances: 4 threads x 3000 plain sends through distinct handles on one in-memory
    log -> no lost or duplicated seqs. (A genuinely distinct vector from the CAS: the
    plain append admits *all*, but the seq must stay unique + contiguous.)"""
    writers, n = 4, 3000
    # Force the GIL to switch as often as possible so the seq RMW window is reliably
    # interleaved (otherwise the race hides behind the GIL).
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-9)
    try:
        chans = [
            locate("race", root=tmp_path, backend="memory")  # all share one log
            for _ in range(writers)
        ]

        def hammer(c):
            for i in range(n):
                c.send({"i": i}, topic="value")

        threads = [threading.Thread(target=hammer, args=(c,)) for c in chans]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        sys.setswitchinterval(old)

    total = writers * n
    seqs = [e.seq for e in chans[0].read()]
    assert len(seqs) == total  # no lost writes
    assert sorted(seqs) == list(range(1, total + 1))  # unique + contiguous
