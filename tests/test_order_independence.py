"""Order independence, as a property test (specs/reference-by-name.md §2, §6).

The rule: a record that answers, ends or concerns another NAMES it, so the named
reads cannot depend on where records land. Random histories are driven through
the real ``Worker``, every record tagged with its writer; each history is then
replayed in random linear extensions of the four orders the rule leaves standing:

  1. each writer's own order;
  2. the order among claims (the claim CAS);
  3. "a record follows what it names" (``claim_seq`` on heartbeat / stopped /
     bound; ``honored`` on stopped; ``request_id`` on unsubscribe / nak / value /
     bound, naming the subscribe or stop they answer; a claim's launch id naming
     its ``launcher.launched``);
  4. "a launch's death follows its claim" (a ``launcher.terminated`` is written
     only after the claim it answers).

Each replay re-sequences into a fresh memory channel, renaming every
``claim_seq`` to its claim's new seq (a claim's name IS its seq). The named reads
-- ``undischarged_stops``, ``live_demand``, ``live_episode``, ``progress`` and
``peek_terminal``'s verdict record and result -- must be identical across all of
them. The generator writes NO malformed records (nothing that names nothing),
which is what excludes the spike's one residual by construction.

Size: ``RUNSTATE_ORDER_HISTORIES`` (default 40) x ``RUNSTATE_ORDER_PERMUTATIONS``
(default 8), fixed seed. The spike's full run is 2000 x 40."""

from __future__ import annotations

import collections
import itertools
import os
import random
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any


import runstate.worker as worker_mod
from runstate.channel.memory import MemoryChannel
from runstate.channel.envelope import Envelope
from runstate.observables import (
    _verdict_record,
    live_demand,
    live_episode,
    peek_terminal,
    progress,
    undischarged_stops,
)
from runstate.vocabulary.launch import launch_scope
from runstate.vocabulary.payloads import Topic

N_HISTORIES = int(os.environ.get("RUNSTATE_ORDER_HISTORIES", "40"))
N_PERMUTATIONS = int(os.environ.get("RUNSTATE_ORDER_PERMUTATIONS", "8"))

HOST = socket.gethostname()
_DEAD_PIDS = itertools.count(4194303, -1)


def _dead_pid() -> int:
    while True:
        pid = next(_DEAD_PIDS)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return pid
        except PermissionError:
            continue


class Tagged:
    """One writer's view of the shared channel: every append is tagged with it."""

    def __init__(self, ch: MemoryChannel, writer: str, tags: dict[int, str]) -> None:
        self._ch, self.writer, self._tags = ch, writer, tags

    def send(  # type: ignore[no-untyped-def]
        self, body, *, topic, name=None, request_id=None, expected_seq=None
    ):
        seq = self._ch.send(
            body,
            topic=topic,
            name=name,
            request_id=request_id,
            expected_seq=expected_seq,
        )
        if seq is not None:
            self._tags[seq] = self.writer
        return seq

    def read(self, *a, **k):  # type: ignore[no-untyped-def]
        return self._ch.read(*a, **k)

    def latest(self, *a, **k):  # type: ignore[no-untyped-def]
        return self._ch.latest(*a, **k)

    def last_seq(self) -> int:
        return self._ch.last_seq()

    def close(self) -> None:
        pass


@contextmanager
def _handle_as(handle: str) -> Iterator[None]:
    saved = worker_mod.local_handle
    worker_mod.local_handle = lambda: handle
    try:
        yield
    finally:
        worker_mod.local_handle = saved


def history(seed: int) -> tuple[list[Envelope], dict[int, str]]:
    """One random run history, driven through the real Worker. Returns the
    envelopes and each record's writer. Writes only well-formed records."""
    rng = random.Random(seed)
    ch = MemoryChannel()
    tags: dict[int, str] = {}
    clock = [1000.0]

    def now() -> float:
        return clock[0]

    clients = [Tagged(ch, "client-A", tags), Tagged(ch, "client-B", tags)]
    third = Tagged(ch, "third", tags)
    launcher = Tagged(ch, "launcher", tags)
    raw = Tagged(ch, "raw", tags)
    ids = itertools.count(1)
    subs: dict[str, Tagged] = {}  # request_id -> its owning client
    stops: list[str] = []

    def client_action() -> None:
        c = rng.choice(clients)
        roll = rng.random()
        if roll < 0.12:
            rid = f"{c.writer}-stop-{next(ids)}"
            body = {} if rng.random() < 0.6 else {"from": {"step": rng.randint(0, 6)}}
            c.send(body, topic=Topic.CONTROL_STOP, request_id=rid)
            stops.append(rid)
        elif roll < 0.16:  # a refused stop: the worker naks it by name
            c.send(
                {"from": {"bogus": 1}},
                topic=Topic.CONTROL_STOP,
                request_id=f"{c.writer}-badstop-{next(ids)}",
            )
        elif roll < 0.45:
            rid = f"{c.writer}-sub-{next(ids)}"
            kind = rng.random()
            if kind < 0.3:
                body: dict[str, Any] = {"every": {"step": rng.choice([1, 2])}}
            elif kind < 0.6:  # a time lease
                body = {
                    "every": {"step": 1},
                    "until": {"time_seconds": rng.choice([5, 30, 300])},
                }
            elif kind < 0.75:  # a count lease
                body = {"every": {"step": 1}, "until": {"count": rng.randint(1, 4)}}
            elif kind < 0.9:
                body = (
                    {} if rng.random() < 0.5 else {"from": {"step": rng.randint(0, 5)}}
                )
            else:  # refused: the worker naks it by name
                body = {"frm": {"step": 1}}
            c.send(body, topic=Topic.CONTROL_SUBSCRIBE, name="loss", request_id=rid)
            subs[rid] = c
        elif roll < 0.55 and subs:  # the owner re-sends: an update of the same request
            rid = rng.choice(sorted(subs))
            subs[rid].send(
                {"every": {"step": 1}, "until": {"time_seconds": 30}},
                topic=Topic.CONTROL_SUBSCRIBE,
                name="loss",
                request_id=rid,
            )
        elif roll < 0.75 and subs:
            rid = rng.choice(sorted(subs))
            subs[rid].send({}, topic=Topic.CONTROL_UNSUBSCRIBE, request_id=rid)
        elif roll < 0.85:
            raw.send(
                {"value": rng.random(), "step": rng.randint(0, 9), "t": now()},
                topic=Topic.VALUE,
                name="raw",
            )

    n_eps = rng.randint(1, 4)
    displaced: list[worker_mod.Worker] = []
    live_end = rng.random() < 0.25
    for k in range(n_eps):
        for _ in range(rng.randint(0, 3)):  # while down
            client_action()
        clock[0] += rng.choice([1, 10, 100])
        alive = k == n_eps - 1 and live_end
        handle = f"local://{HOST}/{os.getpid() if alive else _dead_pid()}"
        launch_id = None
        if rng.random() < 0.5:
            launch_id = f"launch-{k}-{seed}"
            launcher.send(
                {"handle": handle, "t": now(), "status": "running"},
                topic=Topic.LAUNCHER_LAUNCHED,
                request_id=launch_id,
            )
        wch = Tagged(ch, f"worker-{k}", tags)
        with _handle_as(handle):
            if launch_id is not None:
                with launch_scope(launch_id):
                    w = worker_mod.Worker(wch, now=now)  # type: ignore[arg-type]
            else:
                w = worker_mod.Worker(wch, now=now)  # type: ignore[arg-type]
        if not w.claimed:
            continue
        ending = (
            "live"
            if alive
            else rng.choice(
                ["stop", "complete", "error", "crash", "crash", "displaced", "retire"]
            )
        )
        stopped_by_command = False
        for i in range(rng.randint(0, 6)):
            if rng.random() < 0.4:
                client_action()
            if displaced and rng.random() < 0.3:  # a displaced worker's late records
                d = displaced.pop()
                d.set("loss", 0.0)
                if rng.random() < 0.5:
                    d.tick(step=99)
                d.stopped()
            clock[0] += rng.choice([1, 2, 20])
            w.set("loss", rng.random())
            if ending == "retire":
                if w.tick(step=None):
                    stopped_by_command = True
                    break
                if not w.pinned and w.retire():
                    break
            elif w.tick(step=i):
                stopped_by_command = True
                break
        if stopped_by_command or ending == "stop":
            w.stopped()
        elif ending == "complete":
            w.stopped(completed=True)
        elif ending == "error":
            w.stopped(error="boom")
        elif ending == "retire":
            if not w.retire():
                w.stopped()
        elif ending == "displaced":
            displaced.append(w)
        elif ending == "crash" and rng.random() < 0.4:
            # a third party releases the stranded claim (runstate#39), naming it --
            # and, sometimes, a stop it has no business discharging (a forger can
            # still name one)
            claim = ch.latest(Topic.LIFECYCLE_STARTED)
            assert claim is not None
            third.send(
                {
                    "completed": False,
                    "error": None,
                    "final_step": None,
                    "claim_seq": claim.seq,
                    "honored": [r for r in stops if rng.random() < 0.2],
                    "t": now(),
                },
                topic=Topic.LIFECYCLE_STOPPED,
            )
        if launch_id is not None and ending != "live" and rng.random() < 0.7:
            code = 0 if ending in ("stop", "complete", "retire") else 1
            launcher.send(
                {"reason": "exited", "exit_code": code, "signal": None, "t": now()},
                topic=Topic.LAUNCHER_TERMINATED,
                request_id=launch_id,
            )
    for _ in range(rng.randint(0, 2)):  # after the last episode
        client_action()
    for d in displaced:  # a displaced worker's dying breath, late
        if rng.random() < 0.5:
            d.stopped()
    return ch.read(), tags


def causal_edges(envs: list[Envelope], tags: dict[int, str]) -> list[set[int]]:
    """``preds[i]`` = indices that must precede ``envs[i]`` in any reordering."""
    index = {e.seq: i for i, e in enumerate(envs)}
    preds: list[set[int]] = [set() for _ in envs]
    last_of_writer: dict[str, int] = {}
    last_claim: int | None = None
    by_rid: dict[tuple[str, str], list[int]] = collections.defaultdict(list)
    claim_of_launch: dict[str, int] = {}
    for i, e in enumerate(envs):
        writer = tags[e.seq]
        if writer in last_of_writer:  # 1. each writer's own order
            preds[i].add(last_of_writer[writer])
        last_of_writer[writer] = i
        if e.topic == Topic.LIFECYCLE_STARTED:  # 2. the order among claims
            if last_claim is not None:
                preds[i].add(last_claim)
            last_claim = i
            if e.request_id is not None:
                claim_of_launch[e.request_id] = i
                preds[i].update(by_rid[(Topic.LAUNCHER_LAUNCHED, e.request_id)])
        # 3. a record follows what it names
        if "claim_seq" in e.body and e.body["claim_seq"] is not None:
            preds[i].add(index[e.body["claim_seq"]])  # heartbeat, stopped, bound
        if e.topic == Topic.LIFECYCLE_STOPPED:
            for r in e.body["honored"]:
                preds[i].update(by_rid[(Topic.CONTROL_STOP, r)])
        if e.request_id is not None:
            if e.topic in (
                Topic.CONTROL_UNSUBSCRIBE,
                Topic.LIFECYCLE_NAK,
                Topic.LIFECYCLE_BOUND,
                Topic.VALUE,
            ):
                preds[i].update(by_rid[(Topic.CONTROL_SUBSCRIBE, e.request_id)])
            if e.topic == Topic.LIFECYCLE_NAK:
                preds[i].update(by_rid[(Topic.CONTROL_STOP, e.request_id)])
            # 4. a launch's death follows its claim
            if e.topic == Topic.LAUNCHER_TERMINATED and e.request_id in claim_of_launch:
                preds[i].add(claim_of_launch[e.request_id])
            by_rid[(e.topic, e.request_id)].append(i)
    return preds


def linear_extension(preds: list[set[int]], rng: random.Random) -> list[int]:
    succ: list[list[int]] = [[] for _ in preds]
    indegree = [len(p) for p in preds]
    for i, p in enumerate(preds):
        for j in p:
            succ[j].append(i)
    ready = [i for i, d in enumerate(indegree) if d == 0]
    order: list[int] = []
    while ready:
        k = rng.randrange(len(ready))
        ready[k], ready[-1] = ready[-1], ready[k]
        i = ready.pop()
        order.append(i)
        for s in succ[i]:
            indegree[s] -= 1
            if indegree[s] == 0:
                ready.append(s)
    assert len(order) == len(preds), "cycle in the causal constraints"
    return order


def replay(
    envs: list[Envelope], order: list[int]
) -> tuple[MemoryChannel, dict[int, int]]:
    """Write ``envs`` in ``order`` into a fresh channel, renaming each claim_seq to
    its claim's new seq. Returns the channel and new seq -> original seq."""
    new_of_old = {envs[i].seq: k + 1 for k, i in enumerate(order)}
    ch = MemoryChannel()
    for i in order:
        e = envs[i]
        body = e.body
        if body.get("claim_seq") is not None:
            body = {**body, "claim_seq": new_of_old[body["claim_seq"]]}
        ch.send(body, topic=e.topic, name=e.name, request_id=e.request_id)
    return ch, {new: old for old, new in new_of_old.items()}


def reads(ch: MemoryChannel, old_of_new: dict[int, int]) -> dict[str, Any]:
    """The named reads, with every envelope identified by its ORIGINAL seq."""

    def ids(envs: list[Envelope]) -> frozenset[int]:
        return frozenset(old_of_new[e.seq] for e in envs)

    verdict = _verdict_record(ch)
    return {
        "undischarged_stops": ids(undischarged_stops(ch)),
        "live_demand": ids(live_demand(ch)),
        "live_episode": live_episode(ch),
        "progress": progress(ch),
        "verdict_record": None if verdict is None else old_of_new[verdict.seq],
        "peek_terminal": peek_terminal(ch),
    }


def test_named_reads_are_invariant_under_causal_reordering() -> None:
    reordered = 0
    for seed in range(N_HISTORIES):
        envs, tags = history(seed)
        preds = causal_edges(envs, tags)
        rng = random.Random(seed)
        baseline = reads(*replay(envs, list(range(len(envs)))))
        for _ in range(N_PERMUTATIONS):
            order = linear_extension(preds, rng)
            reordered += order != sorted(order)
            got = reads(*replay(envs, order))
            assert got == baseline, (
                f"history seed {seed}: the named reads moved under a causal "
                f"reordering\n  order: {order}\n  records: "
                f"{[(e.seq, e.topic, e.request_id, e.body) for e in envs]}\n"
                f"  baseline: {baseline}\n  reordered: {got}"
            )
    # the property is vacuous if nothing ever moved
    assert N_HISTORIES * N_PERMUTATIONS == 0 or reordered > 0


def test_the_causal_constraints_are_not_vacuous() -> None:
    """The edges leave real freedom: some history admits a reordering."""
    free = 0
    for seed in range(N_HISTORIES):
        envs, tags = history(seed)
        order = linear_extension(causal_edges(envs, tags), random.Random(seed))
        free += order != sorted(order)
    assert N_HISTORIES == 0 or free > 0
