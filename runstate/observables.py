"""The stateless observer plane (docs/specs/observables.md; design §8, §9).

Observables: pure, body-aware folds ``log -> derived view`` — the questions you
can ask of a run without disturbing it (the read side of design §7's
read-vs-subscribe line; reads never pin a worker, subscriptions do). Observe
*statelessly* here; watch *statefully* with the ``Watcher`` (whose *preferred*
staleness input is arrival time — skew-immune, but only for a beacon it witnessed;
since observer-clock the beacon also carries its own ``t``, so ``last_activity`` dates
a run from the log alone and the Watcher seeds from ``t`` the prefix it did not witness).
Not Rx-style observables — pull-side pure functions; the push side is the subscription
convention.

Membership test: stateless, observer-side, derived-never-stored. Needs a
cursor or a clock? It's the Watcher's. Parses a handle string? It's
``vocabulary/``'s. Tolerance splits by plane (the substrate admits foreign
bodies on any topic): measurement folds (``progress``, ``value_series``,
``live_demand``) skip what isn't a measurement — one lost point is marginal;
verdict folds (``peek_terminal``, ``live_episode``) decide categorical
answers from single records and refuse to guess — an uninterpretable record
raises ``MalformedRecordError``, typed and catchable.

The liveness tiers live here too: ``peek_terminal`` covers the two *terminal*
tiers that are a pure read of the log — a clean ``lifecycle.stopped`` (the
worker's own report) and a reaped ``launcher.terminated`` (the manner of
death); the non-terminal tiers (handle probe, heartbeat staleness) are the
stateful Watcher's.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Optional, TypeVar

from .channel import Channel, Envelope
from .vocabulary.payloads import Stopped, Terminated, Topic
from .vocabulary.handle import resolve

_T = TypeVar("_T")


class MalformedRecordError(Exception):
    """A record on a verdict topic cannot be interpreted — the writer violated
    the convention. Raised by the verdict folds (``peek_terminal``,
    ``live_episode``, ``await_consumed``'s nak parse), which decide categorical
    answers from single records and refuse to guess; the measurement folds
    (``progress``, ``value_series``, ``live_demand``) skip junk instead.
    Callers wanting degradation catch this."""

    def __init__(self, seq: int, topic: str, detail: str) -> None:
        super().__init__(
            f"uninterpretable record at seq {seq} on topic {topic!r}: {detail}"
        )
        self.seq = seq
        self.topic = topic
        self.detail = detail


def verdict_parse(cls: type[_T], e: Envelope) -> _T:
    """Parse a verdict-plane record via ``cls(**body)``, wrapping the writer's
    convention violation (bad keys -> TypeError, a payload-constraint violation
    -> ValueError) in the typed MalformedRecordError."""
    try:
        return cls(**e.body)
    except (TypeError, ValueError) as exc:
        raise MalformedRecordError(e.seq, e.topic, str(exc)) from exc


class Outcome(StrEnum):
    """The CLOSED, normalized terminal verdict — the codomain of ``RunResult.outcome``.
    StrEnum: each member IS its wire string (``Outcome.COMPLETED == "completed"``), so it
    serializes byte-identically and compares equal to the bare strings on existing logs —
    zero channel migration. The single authoritative home for the vocabulary: peek_terminal,
    the Watcher, sweep, and the memoizer reference these members instead of re-spelling the
    literals (which had drifted into four uncoordinated copies)."""

    COMPLETED = "completed"
    PREEMPTED = "preempted"
    ERRORED = "errored"
    KILLED = "killed"
    PRESUMED_DEAD = "presumed_dead"

    @classmethod
    def failures(cls) -> frozenset["Outcome"]:
        """The death outcomes (the worker died, not a clean finish/preempt) — the subset
        sweep and the memoizer stop-and-surface on, spelled once here."""
        return frozenset({cls.ERRORED, cls.KILLED, cls.PRESUMED_DEAD})


@dataclass(frozen=True)
class RunResult:
    # ``outcome`` is the CLOSED, normalized verdict consumers branch/aggregate on
    # (it unifies the worker-stop, reaped-death, and inferred-death tiers into one
    # vocabulary). ``reason`` is the verbatim per-tier label — the raw "why". For the
    # lifecycle tier, reason == outcome (the verbatim worker reason is gone; B′ removes
    # Stopped.reason, and commandedness is recoverable from the control.stop on the
    # log). The launcher tier keeps its finer labels ("exited" / "killed"). There is
    # deliberately no ``success`` bool: it is a pure projection of ``outcome`` that
    # would bake one contested policy ("is a clean non-completion a success?") into
    # the producer; consumers apply their own (e.g. sweep fails on the bottom three).
    outcome: Outcome  # the closed verdict vocabulary (see Outcome above)
    reason: str
    # run_id is stamped by the Watcher (which knows the run); peek_terminal works
    # from a bare channel and leaves it None.
    run_id: Optional[str] = None
    error: Optional[str] = None
    final_step: Optional[int] = None

    @property
    def done(self) -> bool:
        """A RunResult is the terminal arm of RunStatus (see watcher.Running)."""
        return True


def latest_episode(channel: Channel) -> Envelope | None:
    """The latest ``lifecycle.started`` envelope, or None if no worker ever
    attached. *Latest* means latest — live, cleanly ended, or crashed alike
    (liveness is ``live_episode``'s composition; None = the run was never
    started, which is information, not a degenerate case). The envelope's
    ``seq`` is the episode's NAME: every lifecycle record that speaks for the
    episode carries it as ``claim_seq`` (reference by name, lifecycle-v0.5).

    THE ONE ORDER THE EPISODE FOLDS STILL NEED is the order among claims, and it
    comes from the claim CAS (``send(expected_seq=)``): a claim lands only on a
    tail it has fully read, so claims are totally ordered by the log's single
    arbiter, and a claim's ``seq`` is at once its name and its rank. "Latest
    claim" consults no other record's position.

    The fold is one ``latest`` call; what this function owns is the
    episode-boundary *rule* (specs/run-episodes.md Decision 1: an episode is a
    read-side derivation, not a record) — named in the one place that changes
    if explicit episode markers ever land, instead of being re-derived (and
    misapplied — audit F7) by every consumer."""
    return channel.latest(Topic.LIFECYCLE_STARTED)


def _claim_name(e: Envelope) -> tuple[bool, Optional[int]]:
    """``(interpretable, claim_seq)`` for a record that speaks for an episode.
    ``claim_seq`` may be None (a terminal for a run that never claimed);
    ``interpretable`` is False when the record carries no usable name at all --
    a pre-v0.5 or hand-composed body, which names nothing."""
    if "claim_seq" not in e.body:
        return False, None
    c = e.body["claim_seq"]
    if c is None or is_step(c):
        return True, c
    return False, None


def _honored(e: Envelope) -> list[str]:
    """The stop ``request_id``s a ``lifecycle.stopped`` names as honored --
    tolerant: a body that names none (malformed, or a third party's release)
    discharges nothing."""
    h = e.body.get("honored")
    if isinstance(h, list):
        return [r for r in h if isinstance(r, str)]
    return []


# THE ANSWER RULE, in one place (reference-by-name §3): which records answer a
# request, and which ids an answer names. A subscribe is answered by an
# unsubscribe (the client's, or the worker's own expiry record) or a nak; a
# stop by a stopped that honors it or a nak. Once any answer names an id, the
# id is spent, wherever the answer sits. The worker's drain, ``live_demand``,
# ``undischarged_stops``, the Watcher's ``pending_stops`` and
# ``await_consumed`` all fold with these, so they cannot disagree.
_SUBSCRIBE_ANSWERS = (Topic.CONTROL_UNSUBSCRIBE, Topic.LIFECYCLE_NAK)
_STOP_ANSWERS = (Topic.LIFECYCLE_STOPPED, Topic.LIFECYCLE_NAK)


def _answer_names(e: Envelope) -> list[str]:
    """The request_ids an answer record NAMES: a ``stopped`` names the stops in
    its ``honored`` (tolerant, ``_honored``); an unsubscribe or a nak names its
    own ``request_id``, and a null one names nothing."""
    if e.topic == Topic.LIFECYCLE_STOPPED:
        return _honored(e)
    return [] if e.request_id is None else [e.request_id]


def _terminal_stopped(
    channel: Channel, claim: Envelope | None, *, strict: bool
) -> Envelope | None:
    """The ``lifecycle.stopped`` that NAMES ``claim`` (its ``claim_seq``), or the
    claimless terminal (``claim_seq`` None) when nothing ever claimed. A terminal
    ends the episode it names, wherever it lands: a displaced worker's honest
    dying breath names its own old claim and cannot release its successor.

    Latest-then-verify: the newest ``stopped`` usually names the current claim
    (O(1)); only on a miss is the window scanned, newest-first.

    THE WINDOW IS CAUSAL, NOT ATTRIBUTION. Only records after the claim are read:
    a record cannot name a claim it never saw, so nothing at or before the claim
    can speak for it -- and a malformed record from a dead past stays out of
    scope instead of poisoning the present forever (an append-only log cannot
    retract it). Inside the window, ``strict`` (the verdict plane) raises on a
    record that names nothing; tolerant callers skip it. Several terminals
    naming one claim (a third party's release beside the worker's own) are a
    conflict names cannot resolve: the newest wins, the one residual order."""
    floor = claim.seq if claim is not None else 0
    want = claim.seq if claim is not None else None
    latest = channel.latest(Topic.LIFECYCLE_STOPPED)
    if latest is None or latest.seq <= floor:
        return None
    candidates: list[Envelope] = [latest]
    scanned = False
    while candidates:
        e = candidates.pop(0)
        ok, named = _claim_name(e)
        if not ok:
            if strict:
                raise MalformedRecordError(
                    e.seq, e.topic, "names no claim (claim_seq missing or junk)"
                )
        elif named == want:
            return e
        if not candidates and not scanned:
            scanned = True
            window = channel.read(after=floor, topics=[Topic.LIFECYCLE_STOPPED])
            candidates = [w for w in reversed(window) if w.seq != latest.seq]
    return None


def live_episode(channel: Channel) -> Optional[str]:
    """Handle of the currently-live episode, or None: the latest claim
    (``latest_episode``) with no ``stopped`` NAMING it, whose worker resolves
    alive (a started-then-crashed episode resolves dead -> not live). A
    ``stopped`` that names no claim releases nothing (it is not evidence).

    THE LAUNCHER TIER DOES NOT PARTICIPATE. Only a later ``lifecycle.stopped``
    and a ``resolve()``-dead handle release a claim; a ``launcher.terminated``
    never does, however well correlated, because it is not an eliminator for
    ``lifecycle.started``. ``peek_terminal`` reads it and this does not, so a run
    can be a KILLED *verdict* and a held *claim* at once -- deliberate (the claim
    gate takes definitive evidence, not a third party's report), and the reason a
    consumer with a stranded foreign-host claim cannot clear it by writing a
    death record. Stated because its absence sent one downstream repo at the
    wrong plane for a week."""
    started = latest_episode(channel)
    if started is None:
        return None
    if _terminal_stopped(channel, started, strict=False) is not None:
        return None
    try:
        handle = started.body["handle"]
    except KeyError as exc:
        raise MalformedRecordError(
            started.seq, started.topic, "missing 'handle'"
        ) from exc
    if not isinstance(handle, str):
        raise MalformedRecordError(
            started.seq, started.topic, f"handle must be a string, got {handle!r}"
        )
    if resolve(handle) is False:
        return None
    return handle


def _episode_stopped(channel: Channel) -> Envelope | None:
    """The current episode's terminal ``stopped`` (the one naming the latest
    claim), tolerant -- the measurement plane's selector (``progress``)."""
    return _terminal_stopped(channel, latest_episode(channel), strict=False)


def _launch_id(e: Envelope) -> str:
    """The launch a ``launcher.*`` record names. Every launcher record must name
    one (launcher-v0.3 requires the ``request_id``): a death record that names no
    launch asserts the unknowable "the run is dead" and cannot be attributed —
    the verdict plane refuses to guess, so it is malformed, loudly."""
    if e.request_id is None:
        raise MalformedRecordError(
            e.seq,
            e.topic,
            "no request_id: a launcher record must name the launch it reports "
            "(launcher-v0.3)",
        )
    return e.request_id


# DOWNSTREAM DEPENDENCY: a consumer's argument that a wrapper cannot forge a
# COMPLETED verdict for a live worker rests on this correlation — a loser's
# ``terminated`` carries the loser's launch id and can never be attributed to the
# winner's claim. Correlating on anything weaker, or dropping the requirement that
# the claim name its launch, silently breaks that argument off-repo.
def _launcher_terminal(channel: Channel) -> Envelope | None:
    """The launcher tier's verdict record (specs/launcher-record-identity.md).

    Anchored to the **claimed** episode, never to log position: a run's episode
    is its *claim* (``lifecycle.started``), so the only death that can speak for
    the run is the death of *the launch that claim answered* — found by
    correlation id. Position cannot do this job: a reap is a reader-side
    observation that lands arbitrarily late (a stale one after a relaunch), and
    a claim-race loser's launch can be the newest one on the log. Both forgeries
    die by construction here — the stale reap names the OLD launch, and the
    loser's death names a launch no episode ever claimed.

    Two silences are deliberate. A claim with no launch id (nobody launched this
    worker — it was hand-run) has no launcher record that speaks for it; the
    Watcher's inference tiers still do. And a claimed episode whose launch has
    not ended yet is, simply, running.

    If nothing ever claimed, the launcher's records stand alone: that is the
    null-worker startup crash, whose ``terminated`` is its ONLY possible
    terminal. Like every terminal here, it stands until an episode claims.

    THE CLAIM IS THE BOUNDARY. A death that speaks for *this* episode can only
    follow the claim it answers -- nothing exits before it claimed -- so records
    at or before ``started`` are out of scope, and the read is windowed to the
    suffix. That is not an optimization bolted on: reading beyond the boundary
    lets a malformed record from a dead past poison the live present PERMANENTLY,
    because an append-only log cannot retract it, where a windowed fold is
    superseded by the next claim. Correlation still does the attributing (a reap
    lands arbitrarily late, so position never attributes); the window only bounds
    what must be *interpretable*."""
    started = latest_episode(channel)
    if started is None:
        deaths = channel.read(topics=[Topic.LAUNCHER_TERMINATED])
        for e in deaths:
            _launch_id(e)  #  every death names its launch, or the tier is poisoned
        return deaths[-1] if deaths else None
    deaths = channel.read(after=started.seq, topics=[Topic.LAUNCHER_TERMINATED])
    for e in deaths:
        _launch_id(e)  #      every death names its launch, or the tier is poisoned
    return next(
        (e for e in reversed(deaths) if e.request_id == started.request_id), None
    )


def _verdict_record(channel: Channel) -> Envelope | None:
    """The single record ``peek_terminal`` speaks for, or None. Split out so the
    Watcher can ask *where* the verdict sits (its seq) without re-deriving
    "which terminal counts" — a re-derivation that is exactly what forges
    verdicts."""
    stopped = _terminal_stopped(channel, latest_episode(channel), strict=True)
    if stopped is not None:
        return stopped
    return _launcher_terminal(channel)


def peek_terminal(channel: Channel) -> Optional[RunResult]:
    """Return a terminal RunResult if the run has left a terminal *record*, else
    None. This is the record-based verdict (a clean ``lifecycle.stopped``, or a
    reaped ``launcher.terminated``); the inference-based tier (heartbeat
    staleness ⟹ ``presumed_dead``) is the stateful Watcher's job.

    A clean ``lifecycle.stopped`` takes precedence (the worker's own report);
    otherwise a reaped ``launcher.terminated`` gives the manner of death.

    **Episode-aware, and both tiers on the same rule: a terminal stands until a
    new episode CLAIMS.** The stop tier reads the latest ``stopped`` unless a
    newer ``started`` follows it. The launcher tier reads the death of the launch
    that the latest claim answered (``_launcher_terminal``) — correlated by id,
    because a third-party death record is neither self-identifying nor reliably
    ordered."""
    record = _verdict_record(channel)
    if record is None:
        return None
    if record.topic == Topic.LIFECYCLE_STOPPED:
        s = verdict_parse(Stopped, record)
        if (
            s.error is not None
        ):  #         NB: `is not None`, not truthiness — "" still errors
            outcome = Outcome.ERRORED
        elif s.completed:
            outcome = Outcome.COMPLETED
        else:
            outcome = Outcome.PREEMPTED
        # reason == outcome for this tier, as a PLAIN string (audit V2): reason
        # is str-typed and user-facing -- an Outcome member here leaks the enum
        # repr into drivers' output (reason=<Outcome.COMPLETED: 'completed'>).
        return RunResult(
            outcome=outcome, reason=str(outcome), error=s.error, final_step=s.final_step
        )
    t = verdict_parse(Terminated, record)
    if t.reason == "killed":
        outcome = Outcome.KILLED
    elif t.exit_code == 0:
        outcome = Outcome.COMPLETED
    else:
        outcome = Outcome.ERRORED
    return RunResult(outcome=outcome, reason=t.reason)


def worker_completed(result: Optional[RunResult]) -> bool:
    """Did the WORKER declare the work finished? ``Outcome.COMPLETED`` has two
    sources, and only one of them is a statement about the *work*:

    - the worker's own ``lifecycle.stopped(completed=True)`` -- ``reason`` is
      ``"completed"`` (``peek_terminal`` synthesizes it from the outcome);
    - a reaped ``launcher.terminated(exited, exit_code=0)`` -- ``reason`` is
      ``"exited"``, a fact about a PROCESS.

    The two are not confusable, and that is enforced on both sides:
    ``Terminated.reason`` is pinned to ``exited``/``killed``
    (launcher-v0.4, ``additionalProperties: false``), and ``Stopped`` carries no
    ``reason`` field at all -- a hand-composed record spelling either the other
    way raises ``MalformedRecordError`` before it reaches here.

    Ask this, not ``outcome == COMPLETED``, wherever the question is "is the
    work done?" rather than "did a process exit cleanly?". An ``sbatch`` exits 0
    at *submit* time and a wrapper can exit 0 while its worker runs on: both
    read COMPLETED on the launcher tier while the work is unfinished or not yet
    begun."""
    return (
        result is not None
        and result.outcome == Outcome.COMPLETED
        and result.reason == str(Outcome.COMPLETED)
    )


_DATED_TOPICS = (
    Topic.LIFECYCLE_STARTED,
    Topic.LIFECYCLE_HEARTBEAT,
    Topic.LIFECYCLE_STOPPED,
    Topic.LAUNCHER_LAUNCHED,
    Topic.LAUNCHER_TERMINATED,
)


def last_activity(channel: Channel) -> Optional[float]:
    """The wall-clock of the run's most recent dated record — ``max(t)`` over the latest
    ``started`` / ``heartbeat`` / ``stopped`` / ``launched`` / ``terminated``
    (observer-clock §6) — or None if the run has emitted none of those. The freshness /
    "when did this last do anything" signal a third-party observer or the store GC's
    grace window needs (and what mycooc reached past the API to compute with
    ``SELECT max(created_at)``), now a few O(1) ``latest`` reads.

    **All** dated lifecycle + launcher records, not just the beats: a run that *started*
    or *launched* but has not beaconed yet must still have an age, or the GC's
    "skip homes younger than T" cannot protect a genuinely-recent home. ``value`` is
    excluded — its ``t`` is present-nullable (the data plane), the documented blind spot
    for a convention-opt-out worker.

    **max-among-the-≤5-finalists**, deliberately: *not* "the ``t`` of the single
    newest-by-``seq`` record" (which differs under skew, since ``t`` is non-monotone vs
    ``seq``) and *not* ``max(t)`` over the whole log (which one fast-clocked record would
    pin into the future forever). Largest plausible last-activity ⟹ smallest age ⟹ errs
    toward *not* deleting — what the GC's irreversible grace window wants. A measurement
    fold: it skips a junk/absent ``t`` rather than raising (the tolerance split)."""
    ts: list[float] = []
    for topic in _DATED_TOPICS:
        e = channel.latest(topic)
        if e is not None:
            t = e.body.get("t")
            if isinstance(t, (int, float)) and not isinstance(t, bool):
                ts.append(float(t))
    return max(ts) if ts else None


def lease_void(
    bound_claims: set[int], drainer_claim: Optional[int], drainer_ended: bool
) -> bool:
    """The episode-boundary discharge, by name (specs/time-lease-boundary.md,
    converted): a lease is a contract with the ONE episode that registered it,
    recorded as ``lifecycle.bound`` naming that claim. It is void for every
    other episode, and for everyone once its own episode has a terminal.
    ONE predicate, shared by the worker (drain form: drainer = its own live
    claim, never ended) and ``live_demand`` (observer form: drainer = the
    latest claim, ended iff a terminal names it). A lease nobody bound yet is
    not void: no episode has taken it, so the next one to drain it will."""
    return any(k != drainer_claim for k in bound_claims) or (
        drainer_claim in bound_claims and drainer_ended
    )


def _episode_ended(channel: Channel, claim: Envelope) -> bool:
    """Does a terminal NAME ``claim``? A ``stopped`` naming it, or a
    ``launcher.terminated`` naming the launch it answered. Tolerant (the
    measurement plane): junk names nothing."""
    if _terminal_stopped(channel, claim, strict=False) is not None:
        return True
    if claim.request_id is None:
        return False
    return any(
        e.request_id == claim.request_id
        for e in channel.read(
            after=claim.seq,
            topics=[Topic.LAUNCHER_TERMINATED],
            request_ids=[claim.request_id],
        )
    )


def live_demand(channel: Channel) -> list[Envelope]:
    """The live leased demand: every ``control.subscribe`` whose ``request_id``
    no answer NAMES -- an answer is a ``control.unsubscribe`` or ``lifecycle.nak``
    bearing that id, wherever it lands -- and, if it is a lease, that is not
    void (``lease_void``: bound by ``lifecycle.bound`` to an episode other than
    the latest claim, or to the latest claim once a terminal names it).

    Reference by name (specs/service-worker.md, converted): a ``request_id``
    names ONE request. Re-sending a live id is the same request -- its latest
    schedule stands (the owner's own write order, which any log preserves) --
    and once any answer names it the id is spent: a later subscribe reusing it
    is dead on arrival, never a fresh request. So the answer is the same
    whatever order the records arrive in. Null-id records answer nothing.
    The one public home of the rule the worker's refold and the relaunch
    decider both consume. Value-blind: it reads schedule *shape*, never payloads."""
    subs: dict[str, Envelope] = {}  #  request_id -> its latest form
    answered: set[str] = set()
    bound: dict[str, set[int]] = {}
    for e in channel.read(
        topics=[Topic.CONTROL_SUBSCRIBE, *_SUBSCRIBE_ANSWERS, Topic.LIFECYCLE_BOUND]
    ):
        if e.request_id is None:
            continue
        if e.topic == Topic.CONTROL_SUBSCRIBE:
            subs[e.request_id] = e
        elif e.topic == Topic.LIFECYCLE_BOUND:
            ok, k = _claim_name(e)
            if ok and k is not None:
                bound.setdefault(e.request_id, set()).add(k)
        else:
            answered.update(_answer_names(e))
    claim = latest_episode(channel)
    current = claim.seq if claim is not None else None
    ended = claim is not None and bool(bound) and _episode_ended(channel, claim)
    return sorted(
        (
            e
            for r, e in subs.items()
            if r not in answered and not lease_void(bound.get(r, set()), current, ended)
        ),
        key=lambda e: e.seq,
    )


def undischarged_stops(channel: Channel) -> list[Envelope]:
    """The ``control.stop`` envelopes no answer NAMES -- pending until a
    ``lifecycle.stopped`` lists its ``request_id`` in ``honored`` (the worker
    served it) or a ``lifecycle.nak`` bears it (the worker refused it). The
    stop rule's public observer home, mirroring ``live_demand``: "is there an
    unhonored stop?" for a status surface or a dispatch gate. The worker's
    drain applies the same rule.

    Reference by name, so the answer is the same whatever order records arrive
    in, and **the discharge is no longer author-blind**: a ``stopped`` that
    names no stop -- a third party releasing a stranded claim -- discharges
    nothing (runstate#39's mechanism, closed for attribution; a forger can
    still name a stop, and no representation stops that). A stop with no
    ``request_id`` is malformed (subscription-v0.3) and refused by every worker,
    so it is never pending. **Pending ≠ due** still: a stop with a ``from``
    is pending the moment it lands but fires only when its condition crosses."""
    stops: dict[str, Envelope] = {}
    answered: set[str] = set()
    for e in channel.read(topics=[Topic.CONTROL_STOP, *_STOP_ANSWERS]):
        if e.topic == Topic.CONTROL_STOP:
            if e.request_id is not None:
                stops[e.request_id] = e
        else:
            answered.update(_answer_names(e))
    return sorted(
        (e for r, e in stops.items() if r not in answered), key=lambda e: e.seq
    )


def progress(channel: Channel) -> Optional[int]:
    """The CURRENT episode's step frontier, from the DENSE axis (the heartbeat
    beats every tick regardless of emission): the latest
    ``lifecycle.heartbeat.step`` and **this episode's** ``lifecycle.stopped``
    (``_episode_stopped``, not a bare ``latest``), whichever is greater; None if
    neither axis has a value yet.

    NOT "max the trajectory ever reached", and the difference is the whole point:
    a *prior* episode's ``final_step`` is not this episode's frontier. Reading it
    positionally reported a preempted episode-1 terminal while episode 2 was still
    climbing, which closed the window early and made ``ensure`` return a series
    spliced across both episodes **as complete** — no error, no re-drive.
    Both registers are selected by NAME (lifecycle-v0.5): the heartbeat and the
    ``stopped`` that name the latest claim. A displaced worker that keeps beating
    names its own old claim, so it no longer drives this frontier (runstate#32).

    It follows that this value may DECREASE across an episode boundary. That is
    correct, not a defect to smooth over: a resumed episode genuinely rolled the
    frontier back, and those steps must be recomputed. A monotone watermark here
    would re-open the splice it just closed.

    THE WINDOW FENCEPOST (the one home for the rule a second implementation and
    a viewer both need): a target ``until={"step": N}`` is the **half-open**
    window ``[0, N)`` — steps ``0 … N-1`` — so the target is reached iff
    ``progress + 1 >= N`` (equivalently ``progress >= N - 1``); ``progress is
    None`` (no stepped record) is window-step 0, so ``N == 0`` is trivially
    reached. This is what ``ensure``/``history`` gate on internally
    (``memoizer._window_step``); a consumer asking "did this run reach its
    target?" uses this arithmetic, not a bespoke off-by-one."""
    steps = []
    claim = latest_episode(channel)
    hb = current_heartbeat(channel, claim)
    if hb is not None and is_step(hb.body.get("step")):
        steps.append(hb.body["step"])
    stopped = _terminal_stopped(channel, claim, strict=False)
    if stopped is not None and is_step(stopped.body.get("final_step")):
        steps.append(stopped.body["final_step"])
    return max(steps) if steps else None


def current_heartbeat(channel: Channel, claim: Envelope | None) -> Envelope | None:
    """The newest heartbeat NAMING ``claim``, or None. Latest-then-verify: the
    newest beat on the log usually names the live claim (O(1)); only on a miss
    (a displaced worker beating over its successor) is the tail searched,
    newest-first, in a window that doubles back toward the claim -- so the miss
    costs O(distance to this episode's newest beat), not O(episode length).
    Tolerant: an unnamed beat names nothing."""
    if claim is None:
        return None
    hb = channel.latest(Topic.LIFECYCLE_HEARTBEAT)
    if hb is None or hb.seq <= claim.seq:
        return None
    if _claim_name(hb) == (True, claim.seq):
        return hb
    hi, width = hb.seq, 64
    while hi > claim.seq:
        lo = max(claim.seq, hi - width)
        window = channel.read(after=lo, topics=[Topic.LIFECYCLE_HEARTBEAT])
        for e in reversed(window):
            if e.seq <= hi and _claim_name(e) == (True, claim.seq):
                return e
        hi, width = lo, width * 4
    return None


def is_step(v: object) -> bool:
    """A conforming step measurement: an int (bool excluded — JSON ``true`` is
    not an integer). Wrong-typed junk isn't a measurement (the tolerance split,
    module docstring): skip it, never compare against it."""
    return isinstance(v, int) and not isinstance(v, bool)


def _value_points(channel: Channel) -> Iterator[tuple[str, Any, Any]]:
    """Decode ``value`` envelopes to ``(name, step, value)`` samples, lazily.
    Applies the domain rules: skip records with no envelope ``name``, a null
    (or wrong-typed) ``step``, or no ``"value"`` key — a stepless emission is
    outside the step-indexed observable's domain, and the substrate admits
    foreign bodies on any topic. Private: the designated escape hatch if a
    custom-fold consumer ever appears; until then the bring-your-own-fold seam
    is the substrate itself (``read`` + a loop)."""
    for e in channel.read(topics=[Topic.VALUE]):
        if e.name is None or "value" not in e.body or not is_step(e.body.get("step")):
            continue
        yield e.name, e.body["step"], e.body["value"]


def value_series(channel: Channel) -> dict[str, dict[int, Any]]:
    """``{name: {step: value}}`` — the run's reported values as functions of
    step, in one log pass (per-name access = indexing; name enumeration =
    ``.keys()``).

    A ``value`` event is a *sample* of the worker's current-value function
    (``set(name, value)`` + ``tick(step)``: one value per (name, step);
    concurrent subscriptions duplicate samples differing only in
    ``request_id``), so the fold is the substrate's register projection
    (design §4 ``latest``) lifted pointwise: last-write-wins by ``seq`` per
    (name, step) cell. Under an episode rewind the OVERLAPPED steps last-win. The
    abandoned branch does **not** drop out: a resumed episode need not rewrite
    every cell below its own frontier — a coarser stride, or a metric it stops
    emitting — so the earlier episode's values survive at the cells the resumed
    one never revisited, and a caller can receive a series no single execution
    produced. This is a **convergent merge** (last-write-wins per cell), not a
    consistent snapshot; projecting one lineage needs a fork point, which the
    log does not carry. The raw events stay on the log for forensics. Inner
    dicts are step-sorted.

    Pure and cache-free: the fold inherits the scope of the read view it is
    given (visibility/enforcement compose upstream — design §6); ``request_id``
    is a dedup concern only and is ignored."""
    out: dict[str, dict[int, Any]] = {}
    for name, step, value in _value_points(channel):
        out.setdefault(name, {})[step] = value
    return {name: dict(sorted(series.items())) for name, series in out.items()}
