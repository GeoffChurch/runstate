"""Format 0.2.0 -> 0.3.0: reference by name (docs/specs/reference-by-name.md §6).

Format 0.2.0 related records by log position; 0.3.0 relates them by name
(lifecycle-v0.5, subscription-v0.3). This step writes the names, and **every
name it writes is exactly what the positional rule said** the record answered.
It keeps every record's seq, because a ``claim_seq`` points at a seq. It deletes
nothing, and appends only ``lifecycle.bound`` records, after the last record.
It ports the spike's backfill (``374c1a2:scripts/backfill_reference_by_name.py``),
which was measured exact on 2,562 of 2,569 copies of real consumer logs; the
other 7 are the stale-beat leak below. Two rules go further than the spike's,
which merged requests the positional rule kept apart: a stop id that several
requests bear, and a nak before every subscribe of its id. Neither case occurs
in the real corpus or in the spike's synthetic world.

The rules:

- **Stops.** A nameless stop gets ``stop@<seq>``. A stop keeps its id only when
  no other request bears it: when it is the only stop with that id, and no
  subscription's. Otherwise every stop bearing it gets ``<id>#<seq>``.
  Positionally each stop record was its own request, while in 0.3.0 the requests
  of one id are one request, which every nak bearing that id answers. Each
  ``stopped.honored`` lists the stops that ``stopped`` cleared under the old rule:
  every stop since the previous ``stopped``, blind to both author and body.
- **Episodes.** The ``claim_seq`` of a heartbeat or a ``stopped`` is the latest
  claim before it. A ``stopped`` before any claim names none (null).
- **Subscriptions.** A reused id is split into segments at each answer, and the
  later segments become ``<id>#<k>``, together with their answers and value
  fires. An answer before every subscribe of its id answered nothing, so it is
  renamed to name nothing: an unsubscribe always, and a nak when a subscription
  bears its id. Any other such nak refused a stop, and names it.
- **Leases.** One ``lifecycle.bound`` for each episode-local subscription the
  positional boundary rule voided, naming the first claim between it and the
  latest claim.

Every minted name is fresh: ``_fresh`` checks it against every request id in the
run and every name minted before it.

**Refused:** a run with a heartbeat before any claim. ``Heartbeat.claim_seq`` is
a required integer, so 0.3.0 cannot hold such a beat, and leaving it unnamed
would put a 0.2.0 record in a 0.3.0 log (log-formats.md §2, rule 5). It is never
dropped.

Two reads differ from 0.2.0's, both positional defects that the names fix
(the spike's T4): the **stale-beat leak**, where ``progress`` read an earlier
episode's heartbeat because the current claim had none; and a **naked stop**,
which ``undischarged_stops`` kept listing until the next ``stopped`` and which
its nak now answers.

**Known limits:**

- A historical misattribution is copied faithfully, not corrected
  (episode-aim's objection). A displaced worker's late heartbeat or ``stopped``
  is named for its successor's claim, because the positional rule attributed it
  there, and the log does not record which worker wrote it.
- The ``bound`` records are inferred from the positional rule. The 0.2.0 log
  never recorded which episode registered a lease.
- The real corpus has no subscribes, naks or unsubscribes, so those rules are
  tested only on synthetic logs.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Any

from ..channel.envelope import Body, Envelope
from ..vocabulary.handle import resolve
from ..vocabulary.payloads import Topic
from ..vocabulary.schedule import references_episode_local
from . import MigrationError, Row


def _fresh(base: str, taken: set[str]) -> str:
    """``base``, or ``base~k`` for the least k >= 2 not in ``taken``; then taken."""
    cand, k = base, 1
    while cand in taken:
        k += 1
        cand = f"{base}~{k}"
    taken.add(cand)
    return cand


@dataclass
class Backfill:
    """A log as the rules rewrite it. ``envs`` holds the records in their input
    order; ``appended`` the records after the last; ``rebodied`` the seqs whose
    body changed; ``taken`` every request id in use, seeded with the run's own."""

    envs: list[Envelope]
    taken: set[str]
    rebodied: set[int] = field(default_factory=set)
    appended: list[Envelope] = field(default_factory=list)

    def mint(self, base: str) -> str:
        return _fresh(base, self.taken)

    def set_rid(self, i: int, rid: str) -> None:
        self.envs[i] = replace(self.envs[i], request_id=rid)

    def name(self, i: int, **fields: Any) -> None:
        e = self.envs[i]
        self.envs[i] = replace(e, body={**e.body, **fields})
        self.rebodied.add(e.seq)


def _subscribed(envs: list[Envelope]) -> set[str]:
    return {
        e.request_id
        for e in envs
        if e.topic == Topic.CONTROL_SUBSCRIBE and e.request_id is not None
    }


def _name_stops(log: Backfill, envs: list[Envelope]) -> None:
    shared = _subscribed(envs)
    uses = Counter(
        e.request_id
        for e in envs
        if e.topic == Topic.CONTROL_STOP and e.request_id is not None
    )
    pending: list[str] = []  #  the stops since the last stopped
    for i, e in enumerate(envs):
        if e.topic == Topic.CONTROL_STOP:
            rid = e.request_id
            if rid is None:
                rid = log.mint(f"stop@{e.seq}")
                log.set_rid(i, rid)
            elif uses[rid] > 1 or rid in shared:
                rid = log.mint(f"{rid}#{e.seq}")
                log.set_rid(i, rid)
            pending.append(rid)
        elif e.topic == Topic.LIFECYCLE_STOPPED:
            log.name(i, honored=sorted(pending))
            pending = []


def _name_episodes(log: Backfill, envs: list[Envelope]) -> None:
    claim: int | None = None
    for i, e in enumerate(envs):
        if e.topic == Topic.LIFECYCLE_STARTED:
            claim = e.seq
        elif e.topic == Topic.LIFECYCLE_STOPPED:
            log.name(i, claim_seq=claim)
        elif e.topic == Topic.LIFECYCLE_HEARTBEAT:
            if claim is None:
                raise MigrationError(
                    f"the heartbeat at seq {e.seq} precedes every claim: format 0.3.0 "
                    "requires a heartbeat to name its claim, so this run cannot move "
                    "to it"
                )
            log.name(i, claim_seq=claim)


@dataclass
class _Segment:
    """The current request of one subscription id: its name, and whether an
    answer has spent it."""

    name: str
    k: int = 0
    live: bool = True


def _segment_subscriptions(log: Backfill, envs: list[Envelope]) -> None:
    subscribed = _subscribed(envs)
    segments: dict[str, _Segment] = {}
    for i, e in enumerate(envs):
        rid = e.request_id
        if rid is None:
            continue
        seg = segments.get(rid)
        if e.topic == Topic.CONTROL_SUBSCRIBE:
            if seg is None:
                seg = segments[rid] = _Segment(rid)
            elif not seg.live:
                seg.k += 1
                seg.live = True
                seg.name = log.mint(f"{rid}#{seg.k}")
            if seg.name != rid:
                log.set_rid(i, seg.name)
        elif e.topic == Topic.VALUE:
            if seg is not None and seg.name != rid:
                log.set_rid(i, seg.name)
        elif e.topic in (Topic.CONTROL_UNSUBSCRIBE, Topic.LIFECYCLE_NAK):
            if seg is None:
                if e.topic == Topic.CONTROL_UNSUBSCRIBE or rid in subscribed:
                    log.set_rid(i, log.mint(f"{rid}!orphan@{e.seq}"))
                continue
            if seg.name != rid:
                log.set_rid(i, seg.name)
            seg.live = False


def _bind_voided_leases(log: Backfill) -> None:
    """The positional boundary rule voided an episode-local subscription when a
    claim lay strictly between it and the latest claim. Bind each such one to
    the first of those claims, which voids it by name."""
    claims = [e.seq for e in log.envs if e.topic == Topic.LIFECYCLE_STARTED]
    latest = claims[-1] if claims else 0
    live: dict[str, Envelope] = {}
    for e in log.envs:
        if e.request_id is None:
            continue
        if e.topic == Topic.CONTROL_SUBSCRIBE:
            live[e.request_id] = e
        elif e.topic in (Topic.CONTROL_UNSUBSCRIBE, Topic.LIFECYCLE_NAK):
            live.pop(e.request_id, None)
    last = log.envs[-1].seq if log.envs else 0
    for rid, e in live.items():
        if not references_episode_local(e.body):
            continue
        between = [c for c in claims if e.seq < c < latest]
        if between:
            body: Body = {"claim_seq": between[0]}
            seq = last + len(log.appended) + 1
            log.appended.append(Envelope(seq, Topic.LIFECYCLE_BOUND, None, rid, body))


def backfill(envs: list[Envelope]) -> Backfill:
    """The names the positional rule implied, for one run's records in seq order."""
    log = Backfill(list(envs), {e.request_id for e in envs if e.request_id is not None})
    _name_stops(log, envs)
    _name_episodes(log, envs)
    _segment_subscriptions(log, envs)
    _bind_voided_leases(log)
    return log


def _dumps(body: Body) -> str:
    return json.dumps(body, separators=(",", ":"))  # as the channels write a body


class V0_2_0_to_V0_3_0:
    FROM = "0.2.0"
    TO = "0.3.0"

    def is_live(self, rows: list[Row]) -> bool:
        """0.2.0's liveness, positional: the latest claim is live unless a
        ``stopped`` follows it, whatever that record's body, or its handle
        resolves dead. An unresolvable foreign handle reads as live. The launcher
        tier does not participate."""
        claim = next(
            (x for x in reversed(rows) if x.topic == Topic.LIFECYCLE_STARTED), None
        )
        if claim is None:
            return False
        if any(x.topic == Topic.LIFECYCLE_STOPPED and x.seq > claim.seq for x in rows):
            return False
        handle = json.loads(claim.body).get("handle")
        if not isinstance(handle, str):
            raise MigrationError(
                f"the claim at seq {claim.seq} carries no handle, so whether it "
                "is live cannot be told"
            )
        return resolve(handle) is not False

    def transform(self, rows: list[Row]) -> list[Row]:
        """Each record keeps its seq, topic, name and ``created_at``, and its body
        text byte for byte unless the body changed. An appended ``bound`` takes
        the latest ``created_at`` in the run."""
        envs = [
            Envelope(x.seq, x.topic, x.name, x.request_id, json.loads(x.body))
            for x in rows
        ]
        log = backfill(envs)
        out = [
            x._replace(
                request_id=e.request_id,
                body=_dumps(e.body) if x.seq in log.rebodied else x.body,
            )
            for x, e in zip(rows, log.envs, strict=True)
        ]
        created = max((x.created_at for x in rows), default=0.0)
        out += [
            Row(e.seq, e.topic, e.name, e.request_id, _dumps(e.body), created)
            for e in log.appended
        ]
        return out
