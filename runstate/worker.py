"""The reference worker loop (docs/design-v0.2.md §6).

A worker-side runtime over a Channel. The worker reports *current values* (set
via ``set``); each ``tick(step)`` drains ``control.*``, registers/cancels
subscriptions, and services the due ones by emitting ``value`` envelopes
correlated by ``request_id``.

The clock is injectable (``now``) for deterministic tests.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass

from .channel import Channel, Envelope, EpisodeHolder
from .vocabulary.payloads import Heartbeat, Nak, Started, Stopped, Topic, Value
from .vocabulary.handle import local_handle
from .vocabulary.launch import current_launch_id
from .vocabulary.schedule import (
    Condition,
    Subscription,
    is_unsatisfiable,
    malformed_schedule,
    malformed_stop_trigger,
    references_episode_local,
    satisfied,
)
from .observables import boundary_voided, live_episode


@dataclass
class PendingStop:
    """A drained, undischarged commanded stop -- the request half of a stop pair,
    live until the next ``lifecycle.stopped`` discharges it (specs/stop-discharge.md).
    """

    request_id: str | None
    from_: Condition | None
    registered_at: float


class Worker:
    def __init__(self, channel: Channel, *, now: Callable[[], float] = time.time):
        self._ch = channel
        self._now = now
        self._values: dict[str, object] = {}
        self._subs: dict[str, tuple[str | None, Subscription]] = (
            {}
        )  # request_id -> (name, sub)
        # Drained, undischarged commanded stops: (request_id, from_, registered_at).
        # A stop is the request half of a request/outcome pair -- live until the
        # next lifecycle.stopped discharges it (specs/stop-discharge.md).
        self._pending_stops: list[PendingStop] = []
        self._cursor = 0
        self._stopped = False
        self._last_step: int | None = None
        # Attaching CAS-claims the episode: a worker that loses (a live episode
        # already exists) sets _lost and exits without acting on the channel.
        self._lost = False
        self._started_seq = None  #      this episode's own claim, once won
        while True:
            # HEAD-FIRST (§4): read the head the claim will assert, then compute
            # the folds from topic-filtered reads CAPPED at it. CAS success at
            # `last` proves nothing landed past the cap, so the capped folds
            # equal an unfiltered same-read's folds -- the same-read fusion
            # (specs/stop-discharge.md) preserved by assertion, at
            # O(lifecycle + control) instead of O(N_total) (which measured
            # ~3.4 s and ~0.8 GB transient on a 10^6-envelope log; §12.5).
            last = self._ch.last_seq()
            envs = [
                e
                for e in self._ch.read(
                    topics=[
                        Topic.LIFECYCLE_STOPPED,
                        Topic.LIFECYCLE_STARTED,
                        Topic.CONTROL_UNSUBSCRIBE,
                        Topic.LIFECYCLE_NAK,
                    ]
                )
                if e.seq <= last
            ]
            # The discharge floor: the latest lifecycle.stopped already on the
            # log. Every control.stop below it is answered (discharged) by that
            # stopped -- its designated counter-record -- so the drain skips it
            # (specs/stop-discharge.md).
            self._discharge_floor = max(
                (e.seq for e in envs if e.topic == Topic.LIFECYCLE_STOPPED), default=0
            )
            # The positional answer fold (specs/service-worker.md): a
            # control.subscribe is live until an unsubscribe or nak bearing
            # its request_id FOLLOWS it by seq; the drain skips answered
            # subscribes (so a resumed episode neither resurrects an expired
            # lease nor re-naks a refused request).
            self._answers: dict[str, list[int]] = {}
            for e in envs:
                if e.request_id is not None and e.topic in (
                    Topic.CONTROL_UNSUBSCRIBE,
                    Topic.LIFECYCLE_NAK,
                ):
                    self._answers.setdefault(e.request_id, []).append(e.seq)
            # Prior episodes' boundaries, for the time-lease discharge
            # (specs/time-lease-boundary.md) -- same capped read, zero extra I/O.
            self._started_seqs = [
                e.seq for e in envs if e.topic == Topic.LIFECYCLE_STARTED
            ]
            if live_episode(self._ch) is not None:
                # ORDER IS LOAD-BEARING, and a consumer depends on it off-repo:
                # this precedes the claim send, so a loser has no claim of its
                # own on the log. That is what makes "a clean exit always leaves
                # a stopped, and the one path that skips the write also leaves
                # nothing for a death to attach to" true. Moving the claim above
                # this check breaks that reasoning silently.
                self._lost = True
                break
            claim = self._ch.send(
                asdict(Started(handle=local_handle(), t=self._now())),
                topic=Started.TOPIC,
                # The claim names the launch it answers (ambient; None if no
                # launcher spawned us). This is what lets a launcher's death
                # record be attributed to an episode instead of forging the
                # run's verdict (specs/launcher-record-identity.md).
                request_id=current_launch_id(),
                expected_seq=last,
            )
            if claim is not None:
                self._started_seq = claim
                # Pin this episode's liveness (iff the backend offers the capability):
                # a session-bound signal an observer reads to detect a cross-host death
                # where os.kill abstains. Taken AFTER the claim CAS -- a liveness signal,
                # never a claim gate (the CAS alone arbiters the claim).
                if isinstance(self._ch, EpisodeHolder):
                    self._ch.hold_episode(claim)
                break  # won the claim

    @property
    def claimed(self) -> bool:
        """True if this worker won the episode claim; False if it lost."""
        return not self._lost

    def __enter__(self) -> "Worker":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._lost:
            return
        if exc_type is not None:
            self.stopped(error=str(exc), final_step=self._last_step)
        else:
            self.stopped(final_step=self._last_step)  #  default: no claim -> preempted

    def steps(self, total: int | None = None, *, start: int = 0) -> Iterator[int]:
        """Drive the worker over a loop. Yields each step; after the body it
        ``tick``s (servicing the values set this iteration) and stops on a
        commanded stop. Pair with ``with Worker(...) as w`` so the dying breath
        (completed / preempted / errored) is emitted on exit.

        ``start`` is keyword-only (default 0). Pass ``start=k`` to resume a run
        from checkpoint step ``k`` — steps are then emitted as ``k, k+1, …``
        (run-absolute), and ``lifecycle.stopped`` records the correct
        ``final_step``.
        """
        if self._lost:
            return
        step = start
        while total is None or step < total:
            self._last_step = step
            yield step
            if self.tick(
                step
            ):  #   truthy -> a commanded stop triggered; stop at this safe point
                return
            step += 1

    def serve(self) -> Iterator[int]:
        """Drive a service: yield (the body does its work — set values, pace
        itself), then tick STEPLESS (``step=None``: the heartbeat carries a
        null step and step-keyed conditions nak — design §7's service worker),
        and exit on a commanded stop or, at zero demand, via the careful death
        (``retire``). ``steps(total)`` runs on the launch contract's target;
        ``serve()`` runs on the log's leased demand — the two protocol-visible
        continuation policies (specs/service-worker.md). Pair with
        ``with Worker(...) as w`` like ``steps``."""
        if self._lost:
            return
        i = 0
        while True:
            yield i
            if self.tick(step=None):  #    commanded stop triggered
                return
            if not self._subs and self.retire():
                return
            i += 1

    def set(self, name: str, value: object) -> None:
        """Update the worker's current value for ``name`` — the register a
        fired subscription samples. OBSERVER-chosen cadence: records land on
        the log only on demand (the service persona). To log a point
        unconditionally — worker-chosen cadence — use ``emit``."""
        self._values[name] = value

    def emit(self, name: str, value: object) -> None:
        """Log ``value`` for ``name`` NOW: the unconditional broadcast point
        (envelope ``request_id=None``, design §6), stamped with the worker's
        current step and clock — and update the ``set`` register, so a
        concurrent subscription can never disagree with the log about the
        current value.

        The two-persona split: ``emit`` is WORKER-chosen cadence — points that
        matter unconditionally (a training curve; the log-as-cache plane
        ``ensure``/``history`` reads). ``set`` is observer-chosen cadence. A
        claim-race loser's emit touches nothing (``claimed`` is the tell).
        Raises ValueError before the first tick or on a stepless worker: a
        ``step=None`` point would permanently poison ``history()`` for the
        name (the log is append-only); genuinely stepless or caller-clocked
        points use raw ``channel.send`` deliberately.

        A worker that has written its dying breath may not act on the channel
        either -- the same rule as a claim-race loser, for the same reason.
        ``stopped`` is the terminal, and a later episode's claim sits above it,
        so a post-terminal ``emit`` lands in the SUCCESSOR's window and wins its
        cell under take-the-latest. Measured: a legitimate episode 2 emitting
        101.0 at step 1, overwritten by episode 1's post-terminal -999.0. No
        third party and no displacement required -- ``stopped()`` already
        latches, and this closes the same latch over the write paths."""
        if self._lost or self._stopped:
            return
        if self._last_step is None:
            raise ValueError(
                f"emit({name!r}) before the first tick / on a stepless worker "
                "would log a step=None point, permanently poisoning history() "
                "for the name; use channel.send for stepless or caller-clocked "
                "points"
            )
        self._values[name] = value
        self._send_value(name, value, step=self._last_step, request_id=None)

    def tick(self, step: int | None) -> bool:
        """Drain control, service due subscriptions, beacon a heartbeat. Returns
        True iff a pending commanded stop has triggered (stop at this safe
        point). The decision is a monotone *level*, not a one-shot pulse: once
        a stop's condition holds it holds at every later safe point until the
        episode stops, so a caller that misses one True recovers it at the
        next. The worker's own *completion* is a separate opt-in claim
        (``w.stopped(completed=True)``); a commanded stop carries no reason —
        commandedness is recoverable from the control.stop on the log.

        A claim-race LOSER may not act on the channel, explicit calls included
        (see ``stopped``): a loser's tick touches nothing and returns True —
        stop at this safe point; ``claimed`` tells a lost claim from a
        commanded stop. A worker past its own dying breath likewise touches
        nothing and returns True: it has already stopped, and its heartbeat
        would date a run whose successor owns the beacon plane."""
        if self._lost or self._stopped:
            return True
        self._last_step = step
        self._drain_control(step)
        self._service(step)
        self._ch.send(
            asdict(Heartbeat(step=step, consumed_seq=self._cursor, t=self._now())),
            topic=Heartbeat.TOPIC,
        )
        return self._stop_decision(step)

    @property
    def stop_pending(self) -> bool:
        """The same decision ``tick`` returns, as a side-effect-free poll
        evaluated at the worker's last safe point. For a callback-guest whose
        host loop cannot act on ``tick``'s return: poll this at your own safe
        point instead; reading it consumes nothing. True for a claim loser
        too (its only instruction is to stop); ``claimed`` says which."""
        return self._stop_decision(self._last_step)

    @property
    def pinned(self) -> bool:
        """Someone holds a live claim on my output (design §7: reads never
        pin, subscribes do). Plain truth of the live registration set — it can
        mislead before the first drain (a pre-staged subscribe sits undrained),
        so consult it only after a tick; the blessed paths (``serve``,
        ``retire``) do so structurally (specs/service-worker.md)."""
        return bool(self._subs)

    def retire(self) -> bool:
        """The careful death (specs/service-worker.md): try to stop because
        demand drained to zero. True = the dying breath is on the log (or this
        worker already stopped / lost its claim — nothing left to do); False =
        new control arrived and the worker should keep serving.

        The death-CAS mirrors the birth claim — episodes are CAS-claimed at
        both ends. Discipline: ``expected_seq`` comes only from a read, never
        from an own append's returned seq (an own append can land on top of an
        unseen racing subscribe); any record found — including the worker's
        own naks/expiry unsubscribes — forces one more read, so the CAS fires
        only against a tail this loop has fully seen and drained."""
        if self._lost or self._stopped:
            return True
        observed = self._cursor
        while True:
            tail = self._ch.read(after=observed)
            if tail:
                for e in tail:
                    if e.topic.startswith("control."):
                        self._cursor = e.seq
                        try:
                            self._handle_control(e, self._last_step)
                        except Exception as exc:
                            self._nak(e.request_id, "malformed", str(exc))
                observed = tail[-1].seq
                continue  #  re-read: the drain may have appended answers
            if self._subs:
                return False  #              new mail — keep serving
            body = asdict(
                Stopped(
                    completed=False,
                    error=None,
                    final_step=self._last_step,
                    t=self._now(),
                )
            )
            if (
                self._ch.send(body, topic=Stopped.TOPIC, expected_seq=observed)
                is not None
            ):
                self._stopped = True  #      the idempotent latch; __exit__ no-ops
                return True
            # CAS lost: something landed after `observed` — loop re-reads.

    def stopped(
        self,
        *,
        completed: bool = False,
        error: str | None = None,
        final_step: int | None = None,
    ) -> None:
        """Emit the cooperative dying breath (lifecycle.stopped). Its existence = a
        clean, resumable halt. ``completed=True`` is the opt-in completion claim; the
        default (completed=False, no error) projects to ``preempted``; an ``error``
        projects to ``errored``. A resumable/chunked worker leaves the claim
        unset on a pause: claiming ``completed`` per chunk reads as done-done
        and silently truncates a consumer like ``ensure`` (the worker contract,
        specs/preempted-vs-completed.md). Idempotent — first writer wins. A claim-race
        LOSER may not act on the channel, explicit calls included — else the
        minimal example's ``w.stopped(completed=True)`` idiom would, in a
        double-spawn, write a completed claim onto the winner's live log
        (specs/lazy-launch.md)."""
        if self._stopped or self._lost:
            return
        self._stopped = True
        if final_step is None:
            final_step = self._last_step  #  auto-fill from the last yielded step
        body = asdict(
            Stopped(
                completed=completed, error=error, final_step=final_step, t=self._now()
            )
        )
        self._ch.send(body, topic=Stopped.TOPIC)

    # ----- internals -----

    def _drain_control(self, step: int | None) -> None:
        # "control.>" is a read-glob (the substrate expands it to the control.*
        # family), not a wire topic -- it stays a bare string; there is no Topic
        # member for a query pattern.
        for e in self._ch.read(after=self._cursor, topics=["control.>"]):
            self._cursor = e.seq
            # One bad control request must never be fatal: refuse it with a nak
            # and carry on. The reason distinguishes a structural problem
            # (malformed -- couldn't interpret the body) from a clean semantic
            # refusal (unsatisfiable) and an unknown verb (unsupported).
            try:
                self._handle_control(e, step)
            except Exception as exc:
                self._nak(e.request_id, "malformed", str(exc))

    def _handle_control(self, e: Envelope, step: int | None) -> None:
        if e.topic == Topic.CONTROL_SUBSCRIBE:
            if e.request_id is None:
                self._nak(None, "malformed", "subscribe requires a request_id")
            elif any(a > e.seq for a in self._answers.get(e.request_id, ())):
                # Already answered (unsubscribed or naked) later on the log --
                # history, never again input (the positional answer fold).
                return
            elif (
                references_episode_local(e.body)
                and self._started_seq is not None
                and boundary_voided(e.seq, self._started_seqs, self._started_seq)
            ):
                # Episode-boundary discharge (specs/time-lease-boundary.md):
                # a time-lease is a contract with one living episode, and a
                # prior episode's started -- already on the log -- is its
                # counter-record. Pop-then-skip: the void answers THIS
                # subscribe, but its arrival still rescinds the same-id
                # predecessor (registrations are slots, not a set -- a
                # superseded immortal sub must not resurrect).
                self._subs.pop(e.request_id, None)
                return
            elif (problem := malformed_schedule(e.body)) is not None:
                # The structural gate (design §6 `malformed`): the full grammar
                # check, before any semantic check -- so a registered schedule
                # is guaranteed to evaluate cleanly at every safe point.
                self._nak(e.request_id, "malformed", problem)
            elif is_unsatisfiable(e.body, step=step):
                self._nak(
                    e.request_id, "unsatisfiable", "schedule can produce no fires"
                )
            else:
                self._subs[e.request_id] = (
                    e.name,
                    Subscription(e.body, registered_at=self._now()),
                )
        elif e.topic == Topic.CONTROL_UNSUBSCRIBE:
            if e.request_id is None:
                self._nak(None, "malformed", "unsubscribe requires a request_id")
            else:
                self._subs.pop(e.request_id, None)
        elif e.topic == Topic.CONTROL_STOP:
            if e.seq < self._discharge_floor:
                # Already answered: a lifecycle.stopped follows this stop on
                # the log, discharging it. History, never again input -- and
                # silently so: "already answered" is not a refusal (the nak
                # reasons have no word for it), and a discharged-but-malformed
                # stop was already naked by its own era's worker. (The rule's
                # public observer home: observables.undischarged_stops.)
                return
            # The structural gate again: naks a bad stop like a bad subscribe,
            # so the pending set only ever holds conditions the unguarded
            # tick-time eval site (`_stop_decision`) can't choke on.
            if (problem := malformed_stop_trigger(e.body)) is not None:
                self._nak(e.request_id, "malformed", problem)
            elif is_unsatisfiable(e.body, step=step):
                # e.g. a step-keyed stop on a stepless worker: it can never
                # fire, so nak (parity with subscribe) rather than silently
                # never auto-stopping.
                self._nak(e.request_id, "unsatisfiable", "stop trigger can never fire")
            else:
                self._pending_stops.append(
                    PendingStop(e.request_id, e.body.get("from"), self._now())
                )
        else:
            self._nak(e.request_id, "unsupported", f"unknown control topic {e.topic!r}")

    def _nak(self, request_id: str | None, reason: str, message: str) -> None:
        self._ch.send(
            asdict(Nak(reason=reason, message=message)),
            topic=Nak.TOPIC,
            request_id=request_id,
        )

    def _send_value(
        self,
        name: str | None,
        value: object,
        *,
        step: int | None,
        request_id: str | None,
    ) -> None:
        try:
            self._ch.send(
                asdict(Value(value=value, step=step, t=self._now())),
                topic=Value.TOPIC,
                name=name,
                request_id=request_id,
            )
        except (TypeError, ValueError) as exc:
            # A user value that won't serialize is the user's own bug,
            # surfaced clearly at the point we try to report it (not the
            # opaque json error) -- and fatal: a broken reporting path
            # should stop the run, not silently drop the metric. The
            # escape hatch is json_default on current_channel()/create_channel().
            raise TypeError(
                f"value for {name!r} ({type(value).__name__}) is not "
                f"JSON-serializable; pass json_default to "
                f"current_channel()/create_channel() to coerce it"
            ) from exc

    def _service(self, step: int | None) -> None:
        now = self._now()
        for request_id, (name, sub) in list(self._subs.items()):
            # Registered => evaluates cleanly: the structural gate validated
            # the full grammar at drain time, so tick cannot raise here.
            decision = sub.tick(step=step, now=now)
            if decision.fire:
                value = self._values.get(name) if name is not None else None
                self._send_value(name, value, step=step, request_id=request_id)
            if decision.expired:
                # Emit-then-delete: the expiry counter-record -- the worker
                # completing the subscribe/unsubscribe pair, the same shape as
                # stopped completing stop -- lands on the log before memory
                # changes, so a crash between the two re-derives correctly
                # (specs/service-worker.md).
                self._ch.send(
                    {}, topic=Topic.CONTROL_UNSUBSCRIBE, request_id=request_id
                )
                del self._subs[request_id]

    def _stop_decision(self, step: int | None) -> bool:
        """Does any pending stop's condition hold at (step, now)? Conditions
        are monotone (schedule.py), so the decision latches by inheritance --
        no fired flag, and evaluating it consumes nothing. Combination is the
        condition-algebra's own any-join: the first satisfied condition stops
        the run. A lost claim decides True outright — the one permanent level:
        a loser may do no work, so every safe point is its stop point."""
        if self._lost:
            return True
        now = self._now()
        return any(
            stop.from_ is None
            or satisfied(
                stop.from_, step=step, time_seconds=now - stop.registered_at, count=0
            )
            for stop in self._pending_stops
        )
