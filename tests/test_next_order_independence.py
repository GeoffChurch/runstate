"""Order independence, as a property test, for log format 0.4.0
(commits-and-lineage.md §2, "Positions not compared"; §10, "Order independence").

The rule: a record that answers, ends, extends or commits another NAMES it, so the
named reads cannot depend on where records land. Random histories are driven
through the staged ``Worker``, by its public API only, every record tagged with
its writer; each history is then replayed in random linear extensions of the five
orders §2 says the rules rely on, and no others:

  1. the order among claims (the claim CAS);
  2. each writer's own program order;
  3. "a record follows what it names": a record follows the seqs it names
     (``claim_seq``, ``parent``, ``resume_from``, each seq in ``values``, and
     ``consumed_seq`` with every control record that watermark passes), and the
     requests it names (a node's ``fired`` and ``expired``, an exit's
     ``honored``, a ``nak``, a ``bound`` and a ``control.withdraw`` follow the
     sends of the ids they name);
  4. a record its writer CAS-appends at W follows every record at or below W
     (``lifecycle.started``, ``lifecycle.stopped``, a drain's ``lifecycle.bound``
     and ``lifecycle.nak``, and every ``supervisor.died``: each lands at the tail
     its writer read);
  5. the substrate contract, which a replay into a fresh channel keeps.

Plus the external plane's own causality, as in 0.3.0: a launch's
``supervisor.terminated`` follows the claim that answers it.

Each replay re-sequences into a fresh memory channel, renaming every seq a record
names (``claim_seq``, ``consumed_seq``, ``parent``, ``values``, ``resume_from``)
to its record's new seq: a node's and a claim's name IS its seq. The named reads,
each identified by ORIGINAL seq, must be identical across every replay:
``undischarged_stops``, ``live_demand``, ``live_episode``, ``peek_terminal``, the
head, ``prefix_len``, ``series``, ``last_element``, ``streams``, ``aligned``,
``answers`` for every subscription, the order within each node and each node's
``fired`` and ``expired`` along the head's lineage.

The generator's model of the protocol is the spec's, not the Worker's: it decides
what to send from §3 and §4 and asks the Worker only what its public surface
answers (``claimed``, ``pinned``, ``stop_pending``, ``released``). It writes no
record the schemas reject. A claim's liveness handle is set where it meets the
log, by the test's channel wrapper, so a claim can probe dead (a pid that no
process holds) while its Worker object lives on: that is how a displaced worker
is made, and how the next claim's ``report_if_dead`` comes to report it.

Size: ``RUNSTATE_ORDER_HISTORIES`` (default 40) x ``RUNSTATE_ORDER_PERMUTATIONS``
(default 8), fixed seeds."""

from __future__ import annotations

import collections
import itertools
import os
import random
import socket
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from typing import Any

from runstate._next import (
    Answers,
    AtNode,
    Commits,
    Envelope,
    HeadFold,
    LastElement,
    LatestClaimHead,
    LiveDemand,
    MalformedRecordError,
    NoSave,
    Outcome,
    PendingStops,
    PrefixLen,
    Target,
    Topic,
    Worker,
    aligned,
    answers,
    last_element,
    live_demand,
    live_episode,
    peek_terminal,
    prefix_len,
    report_death,
    series,
    streams,
    undischarged_stops,
)
from runstate.channel.memory import MemoryChannel
from runstate.vocabulary.launch import launch_scope

N_HISTORIES = int(os.environ.get("RUNSTATE_ORDER_HISTORIES", "40"))
N_PERMUTATIONS = int(os.environ.get("RUNSTATE_ORDER_PERMUTATIONS", "8"))

HOST = socket.gethostname()
_DEAD_PIDS = itertools.count(4194303, -1)

# The names a history uses: ``lr`` is a source of every claim that declares it;
# ``aux`` is a source of some claims and an eager stream of others, so it changes
# kind between claims (§2, "Membership"); ``loss`` and ``ext`` are eager streams,
# ``ext`` only ever reaching a node by ``include``.
NAMES = ("loss", "aux", "ext", "lr")
EAGER = ("loss", "aux", "ext")
COUNTED: tuple[str | Commits, ...] = (Commits(), "loss", "aux", "ext")

# The records a writer compare-and-appends at the tail it read (§2, "Positions not
# compared"; §3, "The helpers"; §4, "The claim", "A drain", "The exit").
_CAS_TOPICS = frozenset(
    {
        Topic.LIFECYCLE_STARTED,
        Topic.LIFECYCLE_STOPPED,
        Topic.LIFECYCLE_BOUND,
        Topic.LIFECYCLE_NAK,
        Topic.SUPERVISOR_DIED,
    }
)
_NODES = frozenset({Topic.LIFECYCLE_COMMIT, Topic.LIFECYCLE_STOPPED})
_REQUESTS = frozenset({Topic.CONTROL_SUBSCRIBE, Topic.CONTROL_STOP})
_CONTROL = frozenset({*_REQUESTS, Topic.CONTROL_WITHDRAW})
# The fields that name a seq, by the topics that carry them (lifecycle-v0.6,
# supervisor-v0.5): the replay renames each.
_SEQ_FIELDS = {
    Topic.LIFECYCLE_STARTED: ("resume_from",),
    Topic.LIFECYCLE_COMMIT: ("claim_seq", "consumed_seq", "parent"),
    Topic.LIFECYCLE_HEARTBEAT: ("claim_seq", "consumed_seq"),
    Topic.LIFECYCLE_REWIND: ("claim_seq", "resume_from"),
    Topic.LIFECYCLE_STOPPED: ("claim_seq", "parent"),
    Topic.LIFECYCLE_BOUND: ("claim_seq",),
    Topic.SUPERVISOR_DIED: ("claim_seq",),
}


def _dead_pid() -> int:
    """A pid no process on this host holds, so a handle naming it probes dead."""
    while True:
        pid = next(_DEAD_PIDS)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return pid
        except PermissionError:
            continue


class Tagged:
    """One writer's view of the shared channel: every append is tagged with it.
    A worker's view also sets the handle its claim states, where the record meets
    the log, so the test chooses whether the claim probes dead or alive."""

    def __init__(
        self,
        ch: MemoryChannel,
        writer: str,
        tags: dict[int, str],
        handle: str | None = None,
    ) -> None:
        self._ch, self.writer, self._tags, self._handle = ch, writer, tags, handle

    def send(  # type: ignore[no-untyped-def]
        self, body, *, topic, name=None, request_id=None, expected_seq=None
    ):
        if topic == Topic.LIFECYCLE_STARTED and self._handle is not None:
            body = {**body, "handle": self._handle}
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


@dataclass
class History:
    """One generated history: its records in seq order, each record's writer, the
    subscription ids it sent, and the features it exercised."""

    envs: list[Envelope]
    tags: dict[int, str]
    subscriptions: list[str]
    features: collections.Counter[str]


def _extendable_nodes(ch: MemoryChannel) -> list[int]:
    """The run's extendable nodes (§2): every commit, and every exit that did not
    complete and did not discard its iteration."""
    out = []
    for e in ch.read(topics=[Topic.LIFECYCLE_COMMIT, Topic.LIFECYCLE_STOPPED]):
        if e.topic == Topic.LIFECYCLE_COMMIT:
            out.append(e.seq)
        elif not e.body["completed"] and e.body["values"] is not None:
            out.append(e.seq)
    return out


def _record_features(envs: list[Envelope]) -> collections.Counter[str]:
    """What the log itself shows a history exercised, by the records' kinds."""
    out: collections.Counter[str] = collections.Counter()
    for e in envs:
        b = e.body
        out[f"record: {e.topic}"] += 1
        if e.topic == Topic.LIFECYCLE_NAK:
            out[f"nak: {b['reason']}"] += 1
        if e.topic in _NODES:
            if b["fired"]:
                out["node fires"] += 1
            if b["expired"]:
                out["node expires"] += 1
            if len(b["values"] or ()) > 1:
                out["node commits several values"] += 1
        if e.topic == Topic.LIFECYCLE_STOPPED:
            kind = (
                "closed"
                if b["completed"]
                else "torn" if b["values"] is None else "extendable"
            )
            out[f"exit: {kind}"] += 1
            if b["honored"]:
                out["exit honors a stop"] += 1
        if e.topic == Topic.SUPERVISOR_DIED:
            out[f"died: {b['outcome']}"] += 1
    return out


def _head_is_closed(ch: MemoryChannel) -> bool:
    return LatestClaimHead().resolve(ch).settled


class _Generator:
    """The random actors of one history: two askers, a supervisor, a launcher, a
    raw value writer, and one to four claims' workers."""

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.ch = MemoryChannel()
        self.tags: dict[int, str] = {}
        self.features: collections.Counter[str] = collections.Counter()
        self.clock = [1000.0]
        self.clients = [Tagged(self.ch, w, self.tags) for w in ("client-A", "client-B")]
        self.supervisor = Tagged(self.ch, "supervisor", self.tags)
        self.launcher = Tagged(self.ch, "launcher", self.tags)
        self.raw = Tagged(self.ch, "raw", self.tags)
        self.ids = itertools.count(1)
        self.owner: dict[str, Tagged] = {}  # a request id -> its asker
        self.target: dict[str, str | None] = {}  # a subscription id -> its name
        self.displaced: list[Worker] = []

    def now(self) -> float:
        return self.clock[0]

    def tick(self) -> None:
        self.clock[0] += self.rng.choice([0.5, 1.0, 5.0, 40.0])

    # ----- the askers (§3, subscription-v0.4) --------------------------------------

    def client_action(self) -> None:
        rng, c = self.rng, self.rng.choice(self.clients)
        roll = rng.random()
        if roll < 0.14:
            self._stop(c)
        elif roll < 0.42:
            self._subscribe(c)
        elif roll < 0.50 and self.target:
            self._resend(rng.choice(sorted(self.target)))
        elif roll < 0.64 and self.owner:
            rid = rng.choice(sorted(self.owner))
            self.owner[rid].send(
                {"t": self.now()},
                topic=Topic.CONTROL_WITHDRAW,
                name=None,
                request_id=rid,
            )
            self.features["withdraw"] += 1
        elif roll < 0.74:  # a value no claim's worker wrote: foreign
            self.raw.send(
                {"value": rng.random(), "t": self.now()},
                topic=Topic.VALUE,
                name=rng.choice(["ext", "loss"]),
            )
            self.features["foreign value"] += 1

    def _stop(self, c: Tagged) -> None:
        rid = f"{c.writer}-stop-{next(self.ids)}"
        body = self.rng.choice(
            [
                {},
                {},
                {"from": {"commits": self.rng.randint(0, 4)}},
                {"from": {"stream": "loss", "n": self.rng.randint(0, 5)}},
                {"from": {"time_seconds": self.rng.choice([1, 60])}},
                {"from": {"stream": "lr", "n": 1}},  # refused where lr is a source
                {"from": {"any": [{"commits": 2}, {"time_seconds": 30}]}},
            ]
        )
        c.send(body, topic=Topic.CONTROL_STOP, name=None, request_id=rid)
        self.owner[rid] = c
        self.features["stop"] += 1

    def _subscribe(self, c: Tagged) -> None:
        rng = self.rng
        rid = f"{c.writer}-sub-{next(self.ids)}"
        kind = rng.choice(
            ["source", "source", "source", "lease", "lease", "demand", "demand"]
            + ["refused", "refused"]
        )
        name: str | None
        body: dict[str, Any]
        if kind == "source":  # durable: lineage atoms only
            name = rng.choice(["lr", "lr", "aux"])
            body = rng.choice(
                [
                    {"every": {"commits": 1}},
                    {"every": {"commits": 2}, "until": {"commits": rng.randint(0, 6)}},
                    {},
                    {"from": {"commits": rng.randint(0, 3)}},
                    {"every": {"stream": "loss", "n": 1}},
                    {"every": {"commits": 0}, "until": {"stream": "loss", "n": 4}},
                ]
            )
            self.features["subscribe: durable source"] += 1
        elif kind == "lease":  # a time or count atom
            name = rng.choice(["lr", "aux"])
            body = rng.choice(
                [
                    {
                        "every": {"commits": 1},
                        "until": {"time_seconds": rng.choice([5, 30, 300])},
                    },
                    {"every": {"commits": 1}, "until": {"count": rng.randint(1, 3)}},
                    {"from": {"time_seconds": 2}},
                ]
            )
            self.features["subscribe: lease"] += 1
        elif kind == "demand":  # pure demand: a lifetime, no schedule
            name = None
            body = rng.choice(
                [
                    {},
                    {"until": {"commits": rng.randint(0, 5)}},
                    {"until": {"time_seconds": rng.choice([5, 300])}},
                ]
            )
            self.features["subscribe: pure demand"] += 1
        else:  # refused, by name
            name, body = rng.choice(
                [
                    (
                        "loss",
                        {"every": {"commits": 1}},
                    ),  # an eager stream: unsatisfiable
                    ("lr", {"from": {"commits": 3}, "until": {"commits": 2}}),  # empty
                    (None, {"every": {"commits": 1}}),  # pure demand's: malformed
                    ("lr", {"every": {"stream": "lr", "n": 1}}),  # a source's count
                ]
            )
            self.features["subscribe: refused"] += 1
        c.send(body, topic=Topic.CONTROL_SUBSCRIBE, name=name, request_id=rid)
        self.owner[rid], self.target[rid] = c, name

    def _resend(self, rid: str) -> None:
        """The owner re-sends its subscription: a new schedule for the same target
        and class, or (refused) another class or target (§3, "A re-send")."""
        rng, name = self.rng, self.target[rid]
        if rng.random() < 0.4:  # change what readers read: refused malformed
            if name is None:
                name, body = "lr", {"every": {"commits": 1}}
            else:
                body = {"until": {"time_seconds": 60}}
            self.features["re-send: refused"] += 1
        else:
            body = (
                {"until": {"commits": rng.randint(1, 6)}}
                if name is None
                else {"every": {"commits": rng.choice([1, 2])}}
            )
            self.features["re-send"] += 1
        self.owner[rid].send(
            body, topic=Topic.CONTROL_SUBSCRIBE, name=name, request_id=rid
        )

    # ----- the claims (§4) ---------------------------------------------------------

    def run(self) -> History:
        rng = self.rng
        n_claims = rng.randint(1, 4)
        live_end = rng.random() < 0.2
        for k in range(n_claims):
            for _ in range(rng.randint(0, 3)):  # while down
                self.client_action()
            self.tick()
            last = k == n_claims - 1
            ending = (
                "live"
                if last and live_end
                else rng.choice(
                    [
                        "complete",
                        "preempt",
                        "error",
                        "discard",
                        "crash",
                        "displaced",
                        "release",
                        "retire",
                    ]
                )
            )
            alive = ending in ("live", "release") or (
                ending == "displaced" and rng.random() < 0.25
            )
            self._claim(k, ending, alive)
        for _ in range(rng.randint(0, 2)):  # after the last claim
            self.client_action()
        for d in self.displaced:  # a displaced worker's late calls
            if rng.random() < 0.5:
                before = self.ch.last_seq()
                d.emit("loss", 0.0)
                d.stopped(completed=False, error=None)
                if self.ch.last_seq() > before:
                    self.features["a displaced worker's late record"] += 1
        envs = self.ch.read()
        self.features.update(_record_features(envs))
        return History(envs, self.tags, sorted(self.target), self.features)

    def _resume(self) -> Any:
        rng, nodes = self.rng, _extendable_nodes(self.ch)
        roll = rng.random()
        if roll < 0.2 or not nodes and roll < 0.6:
            self.features["resume: none"] += 1
            return None
        if roll < 0.75 and not _head_is_closed(self.ch):
            self.features["resume: latest head"] += 1
            return LatestClaimHead()
        if nodes:
            self.features["resume: a node"] += 1
            return AtNode(rng.choice(nodes))
        self.features["resume: none"] += 1
        return None

    def _claim(self, k: int, ending: str, alive: bool) -> None:
        rng = self.rng
        handle = f"local://{HOST}/{os.getpid() if alive else _dead_pid()}"
        wch = Tagged(self.ch, f"worker-{k}", self.tags, handle=handle)
        launch_id = f"launch-{k}" if rng.random() < 0.5 else None
        scope: AbstractContextManager[Any] = nullcontext()
        if launch_id is not None:
            self.launcher.send(
                {"handle": handle, "status": "running", "t": self.now()},
                topic=Topic.SUPERVISOR_LAUNCHED,
                request_id=launch_id,
            )
            scope = launch_scope(launch_id)
            self.features["launch"] += 1
        sources = rng.choice([["lr"], ["lr"], ["lr", "aux"], []])
        reports_before = len(self.ch.read(topics=[Topic.SUPERVISOR_DIED]))
        with scope:
            w = Worker(
                wch,  # type: ignore[arg-type]
                resume_from=self._resume(),
                sources=sources,
                save=NoSave(),
                now=self.now,
            )
        if len(self.ch.read(topics=[Topic.SUPERVISOR_DIED])) > reports_before:
            self.features["death report: the claim path's probe"] += 1
        if not w.claimed:
            self.features["claim lost"] += 1
            self._terminate(launch_id, code=0)
            return
        claim = self.ch.latest(Topic.LIFECYCLE_STARTED)
        assert claim is not None
        self.features[f"sources {sorted(sources)}"] += 1
        stopped = self._drive(w, sources, ending, claim.seq)
        self._end(w, ending, stopped, claim.seq)
        if ending not in ("live", "displaced", "release") and rng.random() < 0.7:
            self._terminate(launch_id, code=0 if ending != "error" else 1)
        if rng.random() < 0.15:  # a report with a cause, on any claim, ended or not
            old = rng.choice(self.ch.read(topics=[Topic.LIFECYCLE_STARTED]))
            report_death(
                self.supervisor,  # type: ignore[arg-type]
                old.seq,
                outcome=Outcome.KILLED,
                evidence="reap: signal 9",
                force=True,
            )
            self.features["death report: with a cause, late"] += 1

    def _terminate(self, launch_id: str | None, *, code: int) -> None:
        if launch_id is not None:
            self.launcher.send(
                {
                    "reason": "exited",
                    "exit_code": code,
                    "signal": None,
                    "t": self.now(),
                },
                topic=Topic.SUPERVISOR_TERMINATED,
                request_id=launch_id,
            )
            self.features["terminated"] += 1

    def _iteration(self, w: Worker, sources: list[str]) -> bool:
        """One iteration's work before its commit; True when a heartbeat returned
        the stop decision."""
        rng = self.rng
        if rng.random() < 0.35:
            self.client_action()
        if self.displaced and rng.random() < 0.5:
            self._displaced_acts()
        self.tick()
        if "lr" in sources and rng.random() < 0.8:
            w.set("lr", rng.random())
        if "aux" in sources:
            if rng.random() < 0.6:
                w.set("aux", rng.random())
        elif rng.random() < 0.4:
            w.emit("aux", rng.random())
        n = rng.choice([0, 1, 1, 1, 2])
        for _ in range(n):
            w.emit("loss", rng.random())
        if n == 2:
            self.features["two emits of one name in one iteration"] += 1
        if rng.random() < 0.2:
            seq = self.raw.send(
                {"value": rng.random(), "t": self.now()},
                topic=Topic.VALUE,
                name=rng.choice(["ext", "ext", "loss"]),
            )
            w.include(seq)
            self.features["include"] += 1
        if rng.random() < 0.2:
            self.features["heartbeat"] += 1
            if w.heartbeat():
                return True
        if rng.random() < 0.12:
            nodes = _extendable_nodes(self.ch)
            target = AtNode(rng.choice(nodes)) if nodes and rng.random() < 0.8 else None
            w.rewind(target)
            self.features["rewind"] += 1
        return False

    def _displaced_acts(self) -> None:
        """A displaced worker, released or not yet, still calling: an emit lands
        until a drain has read the report; a drain latches it (§4, "A released
        worker")."""
        d = self.rng.choice(self.displaced)
        before = self.ch.last_seq()
        roll = self.rng.random()
        if roll < 0.5:
            d.emit("loss", -1.0)
        elif roll < 0.8:
            d.commit()
        else:
            d.heartbeat()
        if self.ch.last_seq() > before:
            self.features["a displaced worker's late record"] += 1
        if d.released:
            self.features["released by a report"] += 1

    def _drive(self, w: Worker, sources: list[str], ending: str, claim: int) -> bool:
        """Drive the claim's iterations; True when a stop ended them."""
        rng = self.rng
        n = rng.randint(0, 5)
        if ending == "retire":
            for _ in range(n + 3):
                if not w.pinned:
                    if w.retire():
                        self.features["retired"] += 1
                        return False
                    self.features["retire refused"] += 1
                if self._iteration(w, sources) or w.commit():
                    return True
            return w.stop_pending
        if ending == "release" and n:
            at = rng.randrange(n)
        else:
            at = -1
        if rng.random() < 0.5:  # steps(): to a target, or unbounded
            target = Target(rng.choice([Commits(), "loss"]), rng.randint(0, 4))
            for i, _ in enumerate(w.steps(target if rng.random() < 0.7 else None)):
                if i == at:
                    self._release(claim)
                if self._iteration(w, sources):
                    return True
                if i >= n:
                    break
            self.features["driver: steps"] += 1
            return w.stop_pending
        for i in range(n):  # the worker calls commit() itself
            if i == at:
                self._release(claim)
            if self._iteration(w, sources) or w.commit():
                return True
        self.features["driver: commit()"] += 1
        return False

    def _release(self, claim: int) -> None:
        """A supervisor's report from evidence (an accounting record), naming the
        live claim: its worker reads it at its next drain and fences itself."""
        report_death(
            self.supervisor,  # type: ignore[arg-type]
            claim,
            outcome=self.rng.choice(
                [Outcome.PREEMPTED, Outcome.KILLED, Outcome.ERRORED, Outcome.LOST]
            ),
            evidence="slurm: TIMEOUT",
            force=True,
        )
        self.features["death report: a release"] += 1

    def _end(self, w: Worker, ending: str, stopped: bool, claim: int) -> None:
        rng = self.rng
        if ending == "release" and not w.released:
            self._release(claim)
            w.emit("loss", -2.0)  # lands: no drain has read the report yet
            w.commit()  # the drain reads it, and the worker writes nothing more
        if w.released:
            self.features["released"] += 1
            w.stopped(completed=False, error=None)  # does nothing
            return
        if stopped:
            self.features["ending: a stop"] += 1
            w.stopped(completed=False, error=None)
            return
        self.features[f"ending: {ending}"] += 1
        if ending == "complete":
            w.stopped(completed=True, error=None)
        elif ending in ("preempt", "retire"):
            w.stopped(completed=False, error=None)
        elif ending == "error":
            w.stopped(completed=False, error="boom")
        elif ending == "discard":
            w.discard()
            w.stopped(completed=rng.random() < 0.3, error=None)
        elif ending == "crash":
            if rng.random() < 0.4:  # its launcher's reap reports it
                report_death(
                    self.supervisor,  # type: ignore[arg-type]
                    claim,
                    outcome=Outcome.KILLED,
                    evidence="reap: signal 9",
                    force=True,
                )
                self.features["death report: a reap"] += 1
        elif ending == "displaced":
            self.displaced.append(w)
        # "live": the claim stays held, by a live handle


def generate(seed: int) -> History:
    """One random history, driven through the staged Worker's public API."""
    return _Generator(seed).run()


# ----- the causal orders, and the replay ---------------------------------------------


def causal_edges(h: History) -> list[set[int]]:
    """``preds[i]``: the indices that must precede ``envs[i]`` in any reordering."""
    envs, tags = h.envs, h.tags
    index = {e.seq: i for i, e in enumerate(envs)}
    preds: list[set[int]] = [set() for _ in envs]
    last_of_writer: dict[str, int] = {}
    last_claim: int | None = None
    sends: dict[str, list[int]] = collections.defaultdict(list)  # an id's requests
    controls: list[int] = []  # the control records, in seq order
    claim_of_launch: dict[str, int] = {}
    for i, e in enumerate(envs):
        writer = tags[e.seq]
        if writer in last_of_writer:  # each writer's own order
            preds[i].add(last_of_writer[writer])
        last_of_writer[writer] = i
        if e.topic == Topic.LIFECYCLE_STARTED:  # the order among claims
            if last_claim is not None:
                preds[i].add(last_claim)
            last_claim = i
            if e.request_id is not None:
                claim_of_launch[e.request_id] = i
        if e.topic in _CAS_TOPICS:  # CAS-appended at W: after every record below
            preds[i].update(range(i))
        # a record follows what it names: the seqs ...
        for key in _SEQ_FIELDS.get(e.topic, ()):
            v = e.body[key]
            if isinstance(v, int) and v >= 1:
                preds[i].add(index[v])
        if e.topic in (Topic.LIFECYCLE_COMMIT, Topic.LIFECYCLE_HEARTBEAT):
            passed = e.body["consumed_seq"]  # and the requests its watermark passes
            preds[i].update(j for j in controls if envs[j].seq <= passed)
        if e.topic in _NODES:
            preds[i].update(index[v] for v in e.body["values"] or ())
            for r in (*e.body["fired"], *e.body["expired"]):
                preds[i].update(sends[r])
        if e.topic == Topic.LIFECYCLE_STOPPED:
            for r in e.body["honored"]:
                preds[i].update(sends[r])
        # ... and the requests
        if e.topic in (
            Topic.LIFECYCLE_NAK,
            Topic.LIFECYCLE_BOUND,
            Topic.CONTROL_WITHDRAW,
        ) and (e.request_id is not None):
            preds[i].update(sends[e.request_id])
        if e.topic == Topic.SUPERVISOR_TERMINATED and e.request_id in claim_of_launch:
            preds[i].add(claim_of_launch[e.request_id])
        if e.topic in _REQUESTS and e.request_id is not None:
            sends[e.request_id].append(i)
        if e.topic in _CONTROL:
            controls.append(i)
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
    """Write ``envs`` in ``order`` into a fresh channel, renaming every seq a record
    names to that record's new seq. Returns the channel and new seq -> original."""
    new_of_old = {envs[i].seq: k + 1 for k, i in enumerate(order)}

    def renamed(v: Any) -> Any:
        return new_of_old[v] if isinstance(v, int) and v >= 1 else v

    ch = MemoryChannel()
    for i in order:
        e = envs[i]
        body = dict(e.body)
        for key in _SEQ_FIELDS.get(e.topic, ()):
            body[key] = renamed(body[key])
        if e.topic in _NODES and body["values"] is not None:
            body["values"] = [renamed(v) for v in body["values"]]
        ch.send(body, topic=e.topic, name=e.name, request_id=e.request_id)
    return ch, {new: old for old, new in new_of_old.items()}


# ----- the reads ---------------------------------------------------------------------


def _attempt(read: Callable[[], Any], old: Callable[[int], int]) -> Any:
    """A read's value, or how it raised, by original seq."""
    try:
        return ("value", read())
    except MalformedRecordError as exc:
        return ("raised", "MalformedRecordError", old(exc.seq))
    except ValueError as exc:
        return ("raised", "ValueError", str(exc).split(":")[0])


def reads(ch: MemoryChannel, old_of_new: dict[int, int], rids: list[str]) -> Any:
    """The named reads, every seq they return identified by its ORIGINAL seq."""

    def old(seq: int | None) -> int | None:
        return None if seq is None else old_of_new[seq]

    def element(e: Any) -> Any:
        return (old(e.node), old(e.seq), e.value, e.t)

    def ids(envs: list[Envelope]) -> frozenset[int | None]:
        return frozenset(old(e.seq) for e in envs)

    def verdict() -> Any:
        r = peek_terminal(ch)
        if r is None:
            return None
        return (
            r.outcomes,
            frozenset((old(d.seq), d.outcome, d.evidence, d.t) for d in r.reports),
            old(r.node),
            old(r.claim_seq),
            frozenset(old(s) for s in r.malformed),
            r.tier,
            r.error,
        )

    head = LatestClaimHead().resolve(ch)

    def lineage() -> list[Any]:
        """The head's lineage, newest first: each node's values in its own order,
        and its ``fired`` and ``expired``."""
        out, node = [], head.node
        while node is not None:
            body = ch.read(after=node - 1, limit=1)[0].body
            values = (
                None if body["values"] is None else [old(v) for v in body["values"]]
            )
            out.append((old(node), values, body["fired"], body["expired"]))
            node = body["parent"]
        return out

    def aligned_rows() -> Any:
        return [
            (
                row.x,
                old(row.node),
                tuple(
                    (n, tuple(element(e) for e in c))
                    for n, c in sorted(row.cells.items())
                ),
            )
            for row in aligned(ch, list(NAMES), head=head, axis=Commits())
        ]

    def kinds() -> Any:
        s = streams(ch, head=head)
        return (frozenset(s.eager), frozenset(s.sources))

    o = old_of_new.__getitem__
    return {
        "head": (old(head.node), head.settled),
        "lineage": _attempt(lineage, o),
        "undischarged_stops": _attempt(lambda: ids(undischarged_stops(ch)), o),
        "live_demand": _attempt(lambda: ids(live_demand(ch)), o),
        "live_episode": _attempt(lambda: live_episode(ch), o),
        "peek_terminal": _attempt(verdict, o),
        "prefix_len": {
            str(x): _attempt(lambda x=x: prefix_len(ch, x, head=head), o)
            for x in COUNTED
        },
        "series": {
            s: _attempt(lambda s=s: [element(e) for e in series(ch, s, head=head)], o)
            for s in EAGER
        },
        "last_element": {
            s: _attempt(
                lambda s=s: (lambda e: None if e is None else element(e))(
                    last_element(ch, s, head=head)
                ),
                o,
            )
            for s in EAGER
        },
        "streams": _attempt(kinds, o),
        "aligned": _attempt(aligned_rows, o),
        "answers": {
            r: _attempt(lambda r=r: [element(e) for e in answers(ch, r, head=head)], o)
            for r in rids
        },
    }


def _histories() -> Iterator[tuple[int, History]]:
    for seed in range(N_HISTORIES):
        yield seed, generate(seed)


def test_named_reads_are_invariant_under_causal_reordering() -> None:
    reordered = 0
    for seed, h in _histories():
        preds = causal_edges(h)
        rng = random.Random(seed)
        baseline = reads(*replay(h.envs, list(range(len(h.envs)))), h.subscriptions)
        for _ in range(N_PERMUTATIONS):
            order = linear_extension(preds, rng)
            reordered += order != sorted(order)
            got = reads(*replay(h.envs, order), h.subscriptions)
            assert got == baseline, (
                f"history seed {seed}: the named reads moved under a causal "
                f"reordering\n  order: {order}\n  records: "
                f"{[(e.seq, e.topic, e.name, e.request_id, e.body) for e in h.envs]}\n"
                f"  moved: { {k: (baseline[k], got[k]) for k in got if got[k] != baseline[k]} }"
            )
    # the property is vacuous if the orderings barely move
    assert reordered >= 0.9 * N_HISTORIES * N_PERMUTATIONS


def test_the_causal_constraints_are_not_vacuous() -> None:
    """The edges leave real freedom: almost every history admits a reordering, and
    the generator exercises every feature the property is about."""
    free = 0
    features: collections.Counter[str] = collections.Counter()
    for seed, h in _histories():
        order = linear_extension(causal_edges(h), random.Random(seed))
        free += order != sorted(order)
        features.update({f: 1 for f, n in h.features.items() if n})
    assert free >= 0.9 * N_HISTORIES
    missing = [f for f in COVERED if not features[f]]
    assert not missing, f"no history exercised {missing}"


# Each is exercised by at least one of the default 40 histories.
COVERED = (
    "stop",
    "withdraw",
    "subscribe: durable source",
    "subscribe: lease",
    "subscribe: pure demand",
    "subscribe: refused",
    "re-send",
    "rewind",
    "include",
    "heartbeat",
    "foreign value",
    "launch",
    "terminated",
    "resume: none",
    "resume: latest head",
    "resume: a node",
    "driver: steps",
    "driver: commit()",
    "two emits of one name in one iteration",
    "ending: complete",
    "ending: preempt",
    "ending: error",
    "ending: discard",
    "ending: crash",
    "ending: displaced",
    "ending: a stop",
    "retired",
    "released",
    "death report: the claim path's probe",
    "death report: a release",
    "death report: a reap",
    "death report: with a cause, late",
    "a displaced worker's late record",
    "re-send: refused",
    "nak: malformed",
    "nak: unsatisfiable",
    "record: lifecycle.bound",
    "record: lifecycle.rewind",
    "node fires",
    "node expires",
    "exit: closed",
    "exit: torn",
    "exit: extendable",
    "exit honors a stop",
)


# ----- every fold equals its pure read (§5, "Bounded reads") -------------------------


def _outcome(read: Callable[[], Any]) -> Any:
    try:
        return ("value", read())
    except MalformedRecordError as exc:
        return ("raised", exc.seq)


def _fold_mismatches(h: History, seed: int) -> list[Any]:
    """Replay ``h`` record by record, polling every fold after each record, with
    the newest claim's head and with one other node's head, against its pure read
    then. The pairing is ``tests/test_next_folds.py``'s."""
    rng = random.Random(seed)
    ch = MemoryChannel()
    head_fold = HeadFold()
    counts = {x: PrefixLen(x) for x in COUNTED}
    lasts = {s: LastElement(s) for s in EAGER}
    answered = {r: Answers(r) for r in h.subscriptions}
    demand, stops = LiveDemand(), PendingStops()
    nodes: list[int] = []
    bad: list[Any] = []

    def check(what: Any, fold: Callable[[], Any], pure: Callable[[], Any]) -> None:
        if _outcome(fold) != _outcome(pure):
            bad.append((e.seq, what, _outcome(fold), _outcome(pure)))

    for e in h.envs:
        assert (
            ch.send(e.body, topic=e.topic, name=e.name, request_id=e.request_id)
            == e.seq
        )
        if e.topic in _NODES:
            nodes.append(e.seq)
        check(
            "HeadFold",
            lambda: head_fold.poll(ch),
            lambda: LatestClaimHead().resolve(ch),
        )
        heads = [LatestClaimHead().resolve(ch)]
        if nodes:
            heads.append(AtNode(rng.choice(nodes)).resolve(ch))
        for hd in heads:
            for x, fold in counts.items():
                check(
                    ("PrefixLen", str(x), hd.node),
                    lambda: fold.poll(ch, hd),
                    lambda: prefix_len(ch, x, head=hd),
                )
            for s, fold in lasts.items():
                check(
                    ("LastElement", s, hd.node),
                    lambda: fold.poll(ch, hd),
                    lambda: last_element(ch, s, head=hd),
                )
            for r, fold in answered.items():
                check(
                    ("Answers", r, hd.node),
                    lambda: fold.poll(ch, hd),
                    lambda: answers(ch, r, head=hd),
                )
        check("LiveDemand", lambda: demand.poll(ch), lambda: live_demand(ch))
        check("PendingStops", lambda: stops.poll(ch), lambda: undischarged_stops(ch))
    return bad


def test_every_fold_equals_its_pure_read_over_worker_histories() -> None:
    """After every record of every generated history, each incremental fold
    (``HeadFold``, ``PrefixLen``, ``LastElement``, ``Answers``, ``LiveDemand``,
    ``PendingStops``) equals its pure read."""
    for seed, h in _histories():
        bad = _fold_mismatches(h, seed)
        assert (
            not bad
        ), f"history seed {seed}: a fold differs from its pure read: {bad[:3]}"
