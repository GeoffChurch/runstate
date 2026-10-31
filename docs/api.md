# API reference — the public surface

Every name exported from the top-level `runstate` package (`runstate.__all__`),
grouped as [`runstate/__init__.py`](../runstate/__init__.py) groups them. Each
summary lifts the load-bearing phrases from the name's own docstring; the code
and the docstrings are authoritative where this page lags.

*Derived, not authoritative: on any disagreement the schemas,
`design-v0.2.md` and [`specs/commits-and-lineage.md`](specs/commits-and-lineage.md)
win (log format 0.4.0; the § references below are that spec's). A drift-guard test (`tests/test_public_api.py`) asserts
every name in `runstate.__all__` appears on this page.*

Reading order for newcomers is [`guide.md`](guide.md); the model is
[`overview.md`](overview.md). Writing a **non-Python** implementation? See
[`implementers-guide.md`](implementers-guide.md) — the language-neutral
conformance reference (this page is the Python surface).

---

## Substrate

The opinion-free topic-log transport.

Opening a run splits on **creation policy** into two total locators plus the
worker's ambient factory, so birth is always explicitly named and observing a run
never mutates it (docs/specs/channel-locators.md).

### `attach_channel`

```python
attach_channel(run_id: str, *, root=None, backend="sqlite", json_default=None) -> Channel
```

Attach to an **existing** run's channel; raise `RunNotFound` if it has no records
— never creating or mutating any backing store (the foreign-db-safe open), yet
**writable** (an attached handle reads *and* writes, so the stop path attaches
then sends). `root` is the directory (sqlite) or namespace (memory) holding runs;
`run_id` selects one. Repeated calls on the same `(root, run_id)` share the run's
log. `json_default` is a sender-side `json.dumps` hook for coercing exotic value
payloads.

### `create_channel`

```python
create_channel(run_id: str, *, root=None, backend="sqlite", json_default=None) -> Channel
```

Open-or-create (birth) a run's channel; idempotent via the birth-CAS. The one
side-effecting locator — creation is *always* this explicitly-named call, never a
default of attaching. `root` / `run_id` / `json_default` and the backend set are
as for `attach_channel`. Backends: `"memory"`, `"sqlite"` (default), `"postgres"`
(the `[postgres]` extra). Raises `ValueError` on a bad backend/root and
`ImportError` (with an install hint) if `backend="postgres"` without psycopg.

### `current_channel`

```python
current_channel(json_default=None) -> Channel
```

Worker-side: open (or birth) the channel for the run this process was launched
into. A launcher sets `RUNSTATE_RUN_ID` / `RUNSTATE_CHANNEL_ROOT` /
`RUNSTATE_CHANNEL_BACKEND` in the worker's environment; `current_channel` reads
them and delegates to `create_channel` (open-or-create, so a launcher-less direct
run still births). See [environment variables](#environment-variables) below.
Raises `KeyError` if `RUNSTATE_RUN_ID` is unset.

### `RunNotFound`

The uniform absence signal (a `LookupError`): `attach_channel` raises it when a
run has no records, so a nonexistent and an empty run are identically "no run"
(docs/specs/channel-locators.md).

### Log formats

- `LOG_FORMAT`: the log format this release reads and writes.
- `LogFormatError`, `LogFormatMismatch` and `LogFormatMissing`: see
  `docs/specs/log-formats.md` §4.

### `Channel`

```python
send(body, *, topic, name=None, request_id=None) -> int
send(body, *, topic, name=None, request_id=None, expected_seq: int | None) -> int | None
read(after=0, *, topics=None, name=None, request_ids=None, limit=None) -> list[Envelope]
latest(topic, name=None) -> Envelope | None
last_seq() -> int
close() -> None
```

A handle on one run's append-only topic log (the ABC every backend implements).
`send` appends and returns the new `seq`; with `expected_seq` it is a
**compare-and-append** (append only if the log head is still `expected_seq`;
returns `None` if it lost — the primitive behind the single-spawn / episode
self-claim). Its two overloads type it by that mode: only a compare-and-append can
return `None`. `read` takes a caller-owned cursor (`after`); the substrate keeps no
per-reader state. `latest(topic, name)` reads the current value of a
register-like topic. `last_seq()` is the CAS's read half (the log's last `seq`,
`0` = empty), O(1) on every backend.

### `Body`

```python
Body = dict[str, Any]
```

The opaque envelope payload type — an arbitrary JSON-serializable dict the
substrate never parses.

### `Envelope`

```python
Envelope(seq: int, topic: str, name: str | None, request_id: str | None, body: Body)
```

One record in a channel's log. `topic` is the closed, protocol-owned routing
key; `name` is the open, application-owned identifier (e.g. a metric name);
`request_id` correlates a response to its request and scopes visibility; `body`
is opaque to the substrate.

## Worker and the checkpoint recipe

The reference worker and the recipe it saves checkpoints by
([`specs/commits-and-lineage.md`](specs/commits-and-lineage.md) §4, §6), with
the coordinate types its drivers and `ensure` take (§2, §4).

### `Worker`

```python
Worker(channel: Channel, *, resume_from: NodeRef | LatestClaimHead | None,
       sources: Sequence[str], save: SavePolicy, now=time.time)
# values
emit(name, value) -> None          # an eager stream's element, committed by the next node
set(name, value) -> None           # a declared source's register, sampled on demand
include(seq: int) -> None          # commit a value another handle sent
# the iteration
commit() -> bool                   # end an iteration: one lifecycle.commit; the stop decision
heartbeat() -> bool                # liveness inside a slow iteration; not a node
rewind(target: NodeRef | None) -> None
discard() -> None                  # the open iteration is torn
# drivers
steps(until: Target | None) -> Iterator[int]
serve() -> Iterator[int]
# the exit
stopped(*, completed: bool, error: str | None) -> None
retire() -> bool
# levels (properties)
claimed: bool          # won the claim (vs lost the race)
released: bool         # read a death report naming its own claim
stop_pending: bool     # the stop decision, as a side-effect-free poll
pinned: bool           # some subscription is live for this claim
resume_node: int | None  # the node the claim resumed from (a rewind moves it)
```

The reference worker of
[`specs/commits-and-lineage.md`](specs/commits-and-lineage.md) §4; a context
manager (`with Worker(ch, resume_from=..., sources=[...], save=...) as w:`)
whose block's end writes the exit: errored on an exception, preempted
otherwise, unless the worker already stopped. Constructing it **claims** the
run: `resume_from` is where the claim starts (a `NodeRef`, such as a
checkpoint object or `AtNode(x)`; `LatestClaimHead()`, which continues the
nearest extendable node of the head, for a worker that holds no state; or
`None`, a fresh start), validated before the claim CAS. `sources` declares the
names sampled on demand (`[]` declares none); every other name is an eager
stream. `save` is the checkpoint save policy. All three are required. `now` is
the clock every record's `t` reads, exempt from the no-default rule.

`commit()` ends an iteration: it drains control (the death reports naming the
claim first), evaluates the subscriptions at the node being written, writes
one sample per fired source and one `lifecycle.commit` (its `values` the
pending values, then the samples), runs the save policy, and returns the stop
decision. **A `commit()` that raises before its node lands tears the
iteration**, as `discard()` does; a save that raises after it landed
propagates, the node standing. `heartbeat()` drains and writes liveness only.
`steps(until)` yields each iteration's index, `count(Commits(), position)`,
before its commit, so a resumed driver continues from the log; it ends at the
`Target` or on a stop, and raises `ValueError` at once on a target naming a
declared source. `serve()` iterates while some subscription is live for the
claim and retires at zero. `stopped(completed=True)` closes the lineage; a
non-completed exit is extendable unless the iteration was discarded. `retire()`
is the careful death: False, writing no exit, if any subscription is live
after either of its reads.

**Fences.** A claim-race loser, a released worker (one that read a death
report naming its claim) and a worker after its own exit write nothing:
`commit()`, `heartbeat()` and `retire()` return True, `stop_pending` is True,
and the other calls do nothing. After `discard()`, `emit`, `set`, `include` and
`rewind` raise instead, since the worker discarded the iteration itself. Each
compare-and-append loop (the claim, a drain's refusal, the exit) gives up after
a bound, raising `WorkerStarvedError`.

**What the claim guarantees — and where it stops.** The claim CAS gives **at
most one claimant at the instant of claiming**. It does *not* give
single-writer over time: nothing revokes a claim once won but a death report,
and a worker that has not yet read one keeps writing for at most the
iteration in flight. Its records are honest reports of what it did, never
assertions that it still holds the claim. Three attempts to extend the
guarantee past that instant — a lock-as-arbiter, an epoch fence in the
substrate, and a per-tick self-check in the worker — are refuted in
`specs/write-authority.md`; read it before proposing a fourth.

### `SavePolicy`

```python
class SavePolicy(Protocol):
    def after(self, node: int, *, commits: int, exit: bool) -> None: ...
```

Whether to save at a moment the Worker chose: after each commit lands, and after
an extendable exit. `node` is the node that just landed, `commits` its
`count(Commits(), node)`.

### `NoSave`

```python
NoSave()
```

Never saves. Passed explicitly, since a chunked run without checkpoints restarts at
the root every claim.

### `Every`

```python
Every(k: int, fn: Callable[[int], None])
```

Calls `fn(node)` at each commit whose `commits` is a multiple of `k`, and at every
extendable exit, so a preempted chunk always leaves a checkpoint of where it
stopped. `k >= 1`.

### `CheckpointChoice`

```python
class CheckpointChoice(Protocol):
    def choose(self, ch, candidates: Sequence[C], *, head: HeadStrategy) -> C | None: ...
```

Which of the caller's checkpoints to resume from: one of `candidates`, the very
object, or `None`. A candidate is complete when its node is at or below the
head's `w`; only complete ones qualify.

### `OnHeadLineage`

```python
OnHeadLineage()
```

The complete candidate on the head's lineage nearest the head: the suggested
preset, consistent with the reads, and respecting a rewind.

### `MostProgress`

```python
MostProgress(stream: str)
```

The complete candidate with the largest `count(stream, node)`, which may lie on a
branch a rewind abandoned. Ties go to the higher node seq.

### `Commits`

```python
Commits()
```

The lineage's own clock: `count(Commits(), node)` is the number of
`lifecycle.commit` nodes on the lineage up to and including `node`. A singleton
marker, so `str | Commits` names either kind of progress axis.

### `Target`

```python
Target(x: str | Commits, n: int)
Target.from_atom(atom: Mapping[str, object]) -> Target
```

"The first `n` of `x`": one type for `steps(until)`, `ensure` and the producer
seam. `n >= 0`, checked once; a target of 0 is reached at once. `atom()` is its
wire spelling, `{stream: x, n}` or `{commits: n}`, and the classmethod
`Target.from_atom(atom)` its inverse (`Target.from_atom(t.atom()) == t`), raising
`ValueError` on any mapping that is not exactly one of those shapes with a number
and name `Target` accepts.

### `NodeRef`

```python
class NodeRef(Protocol):
    @property
    def node(self) -> int: ...
```

A fixed node, which `Worker(resume_from=)` and `rewind()` take: a checkpoint
object, or `AtNode(x)`.

## Launchers and death reports

Opt-in orchestration: spawn a worker and bracket it with `supervisor.launched`
/ `supervisor.terminated`, reporting a claimant's death with `supervisor.died`
(§3). The channel locators are uniform; `launch`'s target is the launcher's own
record type.

### `Launcher`

```python
class Launcher(Protocol[T_contra]):
    def create_channel(self, run_id: str) -> Channel: ...
    def attach_channel(self, run_id: str) -> Channel: ...
    def launch(self, run_id: str, target: T_contra) -> LaunchHandle: ...
```

Protocol: spawn a worker into a run. A launcher is typed by its **target
record**, contravariant: `ThreadLauncher` is a `Launcher[ThreadTarget]` and
`LocalLauncher` a `Launcher[Command]`, so a mismatched pair is a type error at
the call site. The two channel methods bind the launcher's `root` / `backend`
— `create_channel` for the launch flow (births the run), `attach_channel` for
probes (the deciders catch `RunNotFound`). A launch writes
`supervisor.launched` and, at its reap, `supervisor.terminated`, both naming
the launch by its id, and a `supervisor.died` beside the `terminated` when the
newest claim naming the launch has no readable exit.

### `LaunchHandle`

```python
run_id: str
channel: Channel
handle: str            # the portable liveness token (local://host/pid)
launch_id: str         # the launch's correlation id, which its claim re-emits
is_alive() -> bool
wait(timeout=None) -> int | None    # blocks until finished, and reaps
terminate() -> None                 # force-kill where the substrate allows
```

Protocol: the observable surface of a launched worker, common to every
launcher. `is_alive` answers liveness and writes nothing; `wait` blocks until
the worker finishes, and reaps it, which writes the launch's `terminated` and
any `died`; `terminate` force-kills where the substrate allows (`ThreadLauncher`
cannot, and raises).

### `ThreadLauncher`

```python
ThreadLauncher(*, root=None, backend="memory")
launch(run_id, target: ThreadTarget) -> LaunchHandle
```

In-process launcher (tests / single-process orchestration): runs
`target.fn(channel, *target.args, **target.kwargs)` on a thread, the launch id
bound for it, so the claim its Worker writes names the launch. When the thread
finishes, the newest claim naming the launch gets a death report unless it has
a readable exit: a raise is `errored`, a return `lost` (only while the claim is
held), with `force=True`. Defaults to the memory backend (`root`/`backend` say
where a log lives, not what it holds). The concrete handle carries a
`.exception`.

### `LocalLauncher`

```python
LocalLauncher(*, root, backend="sqlite")
launch(run_id, target: Command) -> LaunchHandle
reap() -> None
```

Spawn workers as local subprocesses (the full handle story). `launch` runs
`target.argv` with `os.environ`, then `target.env`, then the `RUNSTATE_*`
variables; the child calls `runstate.current_channel()` to re-derive the same
run's channel — so the backend must be cross-process durable (sqlite, the
default). A handle's reap writes `terminated`, and a `died` (`reap: exit <n>`,
`reap: signal <s>`) when the newest claim naming the launch has the child's
handle and no readable exit. `reap()` reaps every finished child, and kills
(`SIGKILL`, through its `Popen`, never by pid) a live child of its own whose
claim a death report ended: an orphan, which a successor would otherwise run
beside. As a context manager it reaps on exit, and does not block on
stragglers.

### `ThreadTarget`

```python
ThreadTarget(fn, *, args: tuple, kwargs: Mapping[str, object])
```

What `ThreadLauncher` runs: `fn(channel, *args, **kwargs)` on a thread. Frozen;
`args` and `kwargs` required. `Drivable`: `driven(until)` is the copy whose
`kwargs` carry `until`.

### `Command`

```python
Command(argv: Sequence[str], *, env: Mapping[str, str])
```

What `LocalLauncher` runs: `argv`, with `env` over `os.environ` and under the
launcher's `RUNSTATE_*` variables. Frozen; `env` required. Not `Drivable`.

### `Drivable`

```python
class Drivable(Protocol):
    def driven(self, until: Target) -> Self: ...
```

A target a drive can aim: `launch_producer` launches `target.driven(until)`.

### `relaunch_if_needed`

```python
relaunch_if_needed(launcher: Launcher[T], run_id: str, target: T) -> LaunchHandle | None
```

The durable-demand decider: launch `target` into `run_id` unless its newest
claim is held. A held claim is first given to `report_if_dead`: when its
report lands, the claim has ended and it launches; when nothing reports, it
returns `None`. It never kills. A best-effort single-spawn guard over a log
read: correctness rests on the worker's claim CAS.

### `ensure_served`

```python
ensure_served(launcher: Launcher[T], run_id: str, target: T, *, demand: LiveDemand) -> LaunchHandle | None
```

Wake a service when there is live demand and no held claim —
`relaunch_if_needed`'s leased-demand sibling (two demand durabilities, two
deciders). `demand` is the caller's `LiveDemand` fold for the run, held across
calls (a one-shot caller passes a fresh one). A held claim is first given to
`report_if_dead`, and when its report lands the demand is read again, since the
report may have voided the only lease. Caller-invoked: subscribe, then
`ensure_served`. The standing form is this in a loop with a **mandatory
`launcher.reap()`** per cycle. Never `Watcher.add()` the returned handle (it
may lose the claim race); `observe()` the run.

### `report_death`

```python
report_death(ch, claim_seq: int, *, outcome: Outcome, evidence: str, force: bool) -> int | None
```

Appends a `supervisor.died` for the claim, by a CAS at the W it reads, and returns
its seq: `None` when the claim has a readable exit, or for a `lost` report on a
claim that has ended. Raises `ClaimAliveError` when the claim's handle probes alive,
unless `force`. Evidence, never inference.

### `report_if_dead`

```python
report_if_dead(ch) -> int | None
```

The probe's report: when the latest claim is held and its session probe finds its
session ended, or its handle is on this host and probes dead, it reports `lost`.
The Worker's claim path, the deciders and `ensure` call it before they act.

### `new_launch_id`

```python
new_launch_id() -> str
```

Mints the correlation id for one launch
([`specs/launcher-record-identity.md`](specs/launcher-record-identity.md)), opaque
and unique: never derived from a pid (a thread launcher's spawns share one) or a
log position (a slow claim can land after a later launch). A launcher stamps it as
the `request_id` of its `supervisor.*` records and hands it to the worker, whose
`lifecycle.started` re-emits it. Public because `Launcher` is: a custom launcher
mints its ids here.

### `LAUNCH_ID_ENV`

```python
LAUNCH_ID_ENV = "RUNSTATE_LAUNCH_ID"
```

The variable a launcher sets in its worker's environment to hand it the launch id
(the cross-process transport; see [Environment variables](#environment-variables)).

### `launch_claim`

```python
launch_claim(ch, launch_id: str, *, after: int, w: int) -> Envelope | None
```

The newest claim (`lifecycle.started`) whose `request_id` is `launch_id`, above
`after` and at or below `w`, or `None`: the claim a launcher's reap reports a death
against with `report_death`, never a later launch's. `w` is the seq the caller has
read to (`ch.last_seq()` at the reap); `after` may skip the log before the launch
began, or be 0. Both change the answer, so both are required.

## The observer plane and orchestration

Pure folds `log → view` (stateless), plus the stateful `Watcher` (§5). A claim
has **ended** once its exit or a death report naming it is on the log, read by
presence: the one predicate the claim gate, lease voiding, the verdict and the
Watcher share.

### `Watcher`

```python
Watcher(*, heartbeat_timeout: float | None, now=time.time, sleep=time.sleep,
        poll_interval=0.05)
add(handle: LaunchHandle) -> None
observe(run_id: str, channel: Channel) -> None
poll(run_id) -> RunStatus
wait(run_id, *, on_event=None, timeout=None) -> RunResult
wait_all(*, on_event=None, timeout=None) -> dict[str, RunStatus]
iter_events(timeout=None) -> Iterator[tuple[str, Envelope]]
broadcast(name: str | None, schedule: Condition, *, request_id=None) -> str
pending_stops(run_id) -> list[Envelope]
```

The stateful failure detector. `add`/`observe` track a run; `poll` returns a
`RunStatus` (`Running | RunResult`); `wait` blocks to a verdict; `wait_all`
covers a set; `broadcast` fans one subscription across all tracked runs under
a shared `request_id` — the **cross-run barrier** (`name=None` is pure
demand); a malformed schedule raises `MalformedSchedule` before anything is
written. `heartbeat_timeout` is required: how long a run may go unwitnessed
before `heartbeat_stale`, or `None` for no staleness tier. The Watcher adds the
inference tiers that need state (`probed_dead` for a handle it holds,
`session_ended` from a backend's session probe, `heartbeat_stale` from witnessed
arrival), and writes nothing, but for a reap of a handle it holds, which writes
through the launcher's reap. A Watcher holding a launch's handle reads that
launch's verdict (the claims naming its launch id); otherwise it reads the
latest claim's. `poll` raises `KeyError` for an untracked run.

### `Watcher.pending_stops`

```python
w.pending_stops(run_id: str) -> list[Envelope]
```

The run's pending stops — `undischarged_stops`, held per run as a
`PendingStops` fold, so each call reads only the records since the last.

### `await_consumed`

```python
await_consumed(channel, seq, *, timeout=None,
               poll_interval=0.05, now=time.time, sleep=time.sleep) -> Nak | RunResult | None
```

Did the control request at `seq` land? The request is the record at `seq`,
and its `request_id` is the name its answers bear, so the caller passes no id.
- a `Nak`, when a refusal names it;
- `None`, accepted, when a watermark of a claim reaches it (a commit's or a
  heartbeat's `consumed_seq`), or an answer names it: a node's `fired` or
  `expired`, or an exit's `honored`;
- the terminal `RunResult`, when the latest claim ended by its own exit above
  the request and no watermark of it reaches the request (the claim saw the
  request and ended without taking it), and when the head is closed, since
  nothing will ever take it;
- otherwise it waits for the next claim, as for a request posted after the
  latest claim ended, or above the last watermark of a claim death reports
  ended.

Raises `ValueError` at once, where `None` would be a false "accepted": no
record at `seq`; a record that is not a `control.subscribe` or `control.stop`
(a `control.withdraw` needs no worker); a nameless request (a missing or empty
id); an id an exit's `honored`, a withdrawal or a refusal spent before `seq`
(request ids are one namespace); and a subscription whose id is a void lease,
bound to a claim that has ended, checked again before any `None`. Raises
`TimeoutError` if `timeout` elapses, and `MalformedRecordError` on a refusal
whose body does not parse.

### `RunStatus`

```python
RunStatus = Running | RunResult
```

The sum type `Watcher.poll` / `wait_all` return: the run is either still in
flight (`Running`) or ended (`RunResult`).

### `Running`

```python
Running(head: Head, commits: int, beacon_age: float)
```

The non-terminal arm of `RunStatus`: the newest claim's `head` and its
`count(Commits(), head)`, from folds the Watcher holds per run, and
`beacon_age`, the seconds since the Watcher last witnessed the run alive (a
new `started`, `commit`, `heartbeat` or `rewind` naming the latest claim), or
since the seeded record's `t` — the gradient toward `heartbeat_stale`.

### `RunResult`

```python
RunResult(outcomes: frozenset[Outcome], reports: tuple[DeathReport, ...],
          node: int | None, claim_seq: int | None, malformed: tuple[int, ...],
          tier: Tier, error: str | None, run_id: str | None)
```

The verdict, by viewpoint (`tier`): the claim's readable exit (`EXIT`), else
the death reports naming it (`DIED`), or an unclaimed run's launch
(`TERMINATED`), or one of the Watcher's inferences. `outcomes` is never empty:
one outcome, but under death reports the set of their outcomes, a `lost` for
each unreadable record that ended the claim, less `lost` beside another. There
is deliberately **no `success`**: a consumer's policy tests the set, as
`ensure` does against `Outcome.failures()`. `reports` holds the readable death
reports (under `DIED` only); `node` is the exit node (under `EXIT` only);
`claim_seq` the claim the verdict is about (`None` for a run nothing claimed);
`malformed` the seqs of the unreadable records skipped; `error` the exit's
error. `run_id` is stamped by a reader that knows the run (the Watcher,
`ensure`); a pure read of a channel passes `None`.

### `Outcome`

```python
Outcome  # StrEnum: COMPLETED, PREEMPTED, ERRORED, KILLED, LOST, PRESUMED_DEAD
Outcome.failures() -> frozenset  # {errored, killed, lost, presumed_dead}
Outcome.deaths() -> frozenset    # {preempted, errored, killed, lost}: a death report's
```

The CLOSED codomain of a verdict's outcomes. Each member IS its wire string
(`Outcome.COMPLETED == "completed"`). It lives in the vocabulary because a wire
field carries it (`Died.outcome`). `lost` says its writer saw the claim end
without an exit, and nothing of how; `presumed_dead` is the Watcher's
inference, which no record carries.

### `Tier`

```python
Tier  # StrEnum: EXIT, DIED, TERMINATED, PROBED_DEAD, HEARTBEAT_STALE, SESSION_ENDED
```

The viewpoint a verdict rests on: the claim's exit, the death reports, an
unclaimed run's launch, or one of the Watcher's inferences (a handle probed dead,
witnessed liveness gone stale, the claim's session ended).

### `DeathReport`

```python
DeathReport(seq: int, outcome: Outcome, evidence: str, t: float)
```

A readable `supervisor.died` naming the claim, in `RunResult.reports`.

### `peek_terminal`

```python
peek_terminal(channel) -> RunResult | None
```

How the run ended, at a W taken first, or `None` while the newest claim is held
or nothing has ended. The record-based verdict: the newest claim's readable
exit, else the death reports naming it. A run nothing claimed takes its newest
`supervisor.terminated` (`Tier.TERMINATED`: a signal reads `killed`, an exit 0
`completed`, any other exit `errored`), which raises `MalformedRecordError` if
unreadable. The inference tiers are the stateful Watcher's.

### `worker_completed`

```python
worker_completed(result: RunResult | None) -> bool
```

Did the worker close its lineage — "the head is closed"? True if and only if
the verdict is the claim's own exit (`Tier.EXIT`) and it completed. A death
report is never a completion, and an unclaimed run's exit-0 `terminated` reads
`completed` under `TERMINATED`, a fact about a **process**, which is not this.
Ask this, not `COMPLETED in outcomes`, wherever the question is "is the work
done?" — an `sbatch` exits 0 at *submit* time.

### `last_activity`

```python
last_activity(channel) -> float | None
```

When this run last did anything, from the log alone — the freshness clock a
third-party observer reads: the largest `t` among the newest record of each
dated topic, `lifecycle.started` / `commit` / `heartbeat` / `rewind` /
`stopped` and `supervisor.launched`, a few O(1) reads, never a `max` over the
whole log. Never a `died` or a `terminated`: each is dated when its writer saw
an end, maybe hours after the run last did anything. (`value` is excluded: its
`t` is the data plane's.) `None` if no dated record exists yet. A junk `t` is
skipped. Cross-clock by construction — a display-only estimate, never an
ordering key.

### `latest_episode`

```python
latest_episode(channel) -> Envelope | None
```

The latest `lifecycle.started` envelope, or None if nothing ever claimed.
*Latest* means latest — live or ended alike. Its `seq` is the claim's name,
which every record speaking for the claim carries as `claim_seq`, and its rank;
its body parses via `Started(**e.body)`.

### `live_episode`

```python
live_episode(channel) -> str | None
```

The claim gate: the newest claim's handle unless that claim has **ended** (its
exit or a death report naming it is on the log, an unreadable one included),
else `None`. It never probes a handle, so a dead process with no report reads
live; the probe is `report_if_dead`, which the actors call before they act. An
unreadable claim raises `MalformedRecordError`.

### `live_demand`

```python
live_demand(channel) -> list[Envelope]
```

The live subscriptions: each `control.subscribe` id's newest send, unless it is
**spent by name** (a `control.withdraw`, a `lifecycle.nak` or an exit's
`honored` names it, wherever it sits; request ids are one namespace), or
**expired** in its class's scope (a lease once any node's `expired` names it; a
durable subscription once a node on the head's lineage does), or a **void
lease**, bound by `lifecycle.bound` to a claim that has ended. A nameless
subscribe is never live; one whose body does not parse is live until a `nak`
names it. Empty while the head is closed. One-shot, scanning the whole log; a
polled path holds `LiveDemand`.

### `undischarged_stops`

```python
undischarged_stops(channel) -> list[Envelope]
```

The pending `control.stop` records, each id's newest send: by name, for the
whole log, a stop is spent once an exit's `honored`, a `control.withdraw` or a
`lifecycle.nak` names it, wherever that sits, since a stop is a request about
the process. A death report spends nothing, and an empty-id stop is never
pending. Note **pending ≠ due**. One-shot; a polled path holds `PendingStops`,
as `Watcher.pending_stops` does.

### `handle_pid`

```python
handle_pid(handle: str) -> int | None
```

The pid of a `local://host/pid` handle; None for a non-`local` scheme or an
unparseable token. Deliberately host-blind — pure grammar, no liveness claim
(hostname scoping lives in `resolve` only).

## The lineage reads

Every read that depends on a branch takes a required, keyword-only `head`
(§5). Each is one-shot; a polled path holds the folds below.

### `Head`

```python
Head(node: int | None, settled: bool, w: int)
```

A resolved head: the head node (`None` for the empty lineage), whether the read's
subject can no longer grow, and the `last_seq()` it was resolved at. A `Head` is
itself a `HeadStrategy` that resolves to itself.

### `HeadStrategy`

```python
class HeadStrategy(Protocol):
    def resolve(self, ch) -> Head: ...
```

How a read chooses its head, chosen once at the edge: every read that depends on
a branch takes a required, keyword-only `head`.

### `LatestClaimHead`

```python
LatestClaimHead()
```

The newest claim's head: the order among claims, then that writer's own order.
Settled if and only if the head is closed.

### `AtNode`

```python
AtNode(node: int)
```

That node (forensics; an abandoned branch). Always settled; also a `NodeRef`.

### `Element`

```python
Element(node: int, seq: int, value: Any, t: float | None)
```

One element of an eager stream, or one sample of a source: the committing node,
the value's seq, its `value` and `t`.

### `Complete`

```python
Complete(elements: tuple[Element, ...], node: int | None)
```

`prefix`'s first `n` elements. Only this outcome has `elements`.

### `Pending`

```python
Pending(partial: tuple[Element, ...], node: int | None)
```

Fewer than `n` elements, and the read is not settled: more may come.

### `SettledShort`

```python
SettledShort(partial: tuple[Element, ...], node: int | None)
```

Fewer than `n` elements, and the read is settled: no more will ever come.

### `AlignedRow`

```python
AlignedRow(x: int, node: int, cells: Mapping[str, tuple[Element, ...]])
```

One row of `aligned`: the axis's count at the node, the node, and per name its
elements there.

### `Streams`

```python
Streams(eager: frozenset[str], sources: frozenset[str])
```

The names with a committed value on a lineage, each by the committing claim's
declaration; a name that changed kind is in both.

### `prefix_len`

```python
prefix_len(ch, x: str | Commits, *, head: HeadStrategy) -> int
```

`count(x, head)`: what `progress` was, as a count. A name that was a source
throughout counts 0.

### `prefix`

```python
prefix(ch, stream: str, n: int, *, head: HeadStrategy) -> Complete | Pending | SettledShort
```

The threshold read: the first `n` elements, or what was found, told apart by
settledness.

### `series`

```python
series(ch, stream: str, *, head: HeadStrategy) -> list[Element]
```

One eager stream's elements, in lineage order.

### `last_element`

```python
last_element(ch, stream: str, *, head: HeadStrategy) -> Element | None
```

The last element in lineage order, which is not always the newest by seq.

### `aligned`

```python
aligned(ch, names: Sequence[str], *, head: HeadStrategy, axis: str | Commits) -> list[AlignedRow]
```

Values aligned by node: one row per node committing any of `names`, its x the
axis's count there. Cells are tuples, so a value that is a JSON array stays one
element.

### `answers`

```python
answers(ch, request_id: str, *, head: HeadStrategy) -> list[Element]
```

The answers to a subscription on the head's lineage: at each node whose `fired`
names it, the sample of its target. A pure demand has none.

### `streams`

```python
streams(ch, *, head: HeadStrategy) -> Streams
```

The eager and source names with a committed value on the lineage.

## The held folds

Each holds its state and reads only the records since its last poll, and equals its
pure read after every record.

### `HeadFold`

```python
HeadFold().poll(ch) -> Head
```

`LatestClaimHead`, held.

### `PrefixLen`

```python
PrefixLen(x: str | Commits).poll(ch, head: Head) -> int
```

`prefix_len`, held; `.at(ch, node, *, w)` looks up any node's count.

### `LastElement`

```python
LastElement(stream: str).poll(ch, head: Head) -> Element | None
```

`last_element`, held.

### `Answers`

```python
Answers(request_id: str).poll(ch, head: Head) -> list[Element]
```

`answers`, held: it re-derives the list on a head that does not descend from its
last one, so a caller that streams answers diffs the lists.

### `LiveDemand`

```python
LiveDemand().poll(ch) -> list[Envelope]
```

`live_demand`, held: by name, for the whole log, reading the newest head itself.
`ensure_served` takes one per run.

### `PendingStops`

```python
PendingStops().poll(ch) -> list[Envelope]
```

`undischarged_stops`, held; the Watcher's `pending_stops` holds one per run.

## The memoizer

Read-first, produce-on-miss over a run's lineage (§5; `specs/memoizer.md`).
`launch_producer` and `foreign_episode` return the seam's Protocols; their
concrete classes are private.

### `history`

```python
history(ch, stream: str, schedule: Condition, *, head: HeadStrategy) -> list[Element]
```

Replay `schedule` over the head's lineage, root first, once per node as live
evaluation does, and return the elements of `stream` committed at the nodes
where it fires. Time atoms read each node's `t`, from the epoch, the earliest
`started.t`. The schedule is parsed first, so a malformed one raises
`MalformedSchedule`. No take-the-latest collapse: an element's index is unique
along a lineage.

### `ensure`

```python
ensure(producer: Producer, target: Target, *, poll_interval=0.01,
       sleep=time.sleep) -> Reached | Unreachable
```

Drive `producer` until the newest claim's lineage holds `target`. **Read
first:** `count(x, head) >= n` at `LatestClaimHead` returns `Reached` at the
head, and a closed head below `n` returns `Unreachable` at it, without
producing. Otherwise `producer.extend(target)`, then poll the newest claim's
head with held folds, calling `report_if_dead` on each poll. Neither outcome
carries elements: read anything at `AtNode(node)`, which is permanent. A target
its drive's claim declares a source raises `ValueError`. The failure exits are
per drive: `RunFailedError`, `NoProgressError`, `RecordlessExitError`. While a
drive's claim is held, `ensure` waits, whatever its handle says. There is no
`head` parameter: the produce branch follows the newest claim.

### `Reached`

```python
Reached(node: int | None)
```

The newest claim's lineage holds the target, at `node`: read anything there at
`AtNode(node)`. `None` only for a target of 0 on the empty lineage.

### `Unreachable`

```python
Unreachable(closed_at: int)
```

The head is closed below the target: nothing more can be committed.

### `Producer`

```python
class Producer(Protocol):
    run_id: str
    @property
    def channel(self) -> Channel: ...
    def extend(self, target: Target) -> LiveHandle: ...
```

A run `ensure` can read and extend: `channel` and `run_id` locate its log, and
`extend(target)` drives toward `target`, returning a liveness handle, never
`None`. `launch_producer`'s opens its channel once, on first access, and holds it;
the caller that made it closes `producer.channel`.

### `LiveHandle`

```python
class LiveHandle(Protocol):
    @property
    def launch_id(self) -> str | None: ...
    def is_alive(self) -> bool: ...
    def wait(self) -> object: ...
```

What `ensure` drives: the drive's `launch_id`, which its claim names, or `None`
for a foreign handle; `is_alive`, which writes nothing; and `wait`, which reaps.
A launcher's `LaunchHandle` is one, and so is `foreign_episode`'s.

### `launch_producer`

```python
D = TypeVar("D", bound=Drivable)
launch_producer(launcher: Launcher[D], run_id: str, target: D) -> Producer
```

The default producer: `launcher` relaunching `target` into `run_id`, each
argument required. Each `extend(until)` launches `target.driven(until)` through
`relaunch_if_needed`, so the worker receives the drive's `Target` as `until`,
which it passes to `steps(until)`; while a claim is held, it hands back
`foreign_episode`. For a launcher whose target is `Drivable`, a `ThreadLauncher`
and a `ThreadTarget`. A `Command` is not `Drivable`, so a subprocess, ray or
service producer is the consumer's own object of the same shape (the seam).

### `foreign_episode`

```python
foreign_episode(channel) -> LiveHandle
```

The producer gate's foreign half: a handle for a claim the producer did not
launch, whose `is_alive()` is a pure read, "the newest claim has not ended", by
presence; it probes nothing, writes nothing, names no launch (`launch_id` is
`None`), and `wait()` reaps nothing. Compose a producer's `extend` as
`relaunch_if_needed(...) or foreign_episode(channel)`.

## Errors

Every error a public call raises is on the surface: the substrate's
`RunNotFound`, `LogFormatError`, `LogFormatMismatch` and `LogFormatMissing`
(above), and these.

### `MalformedRecordError`

```python
MalformedRecordError(seq: int, topic: str, detail: str)
```

A record a read must interpret cannot be: the writer violated the convention.
A read raises rather than guesses, since completeness is well-formedness and a
read never waits. Structural checks run on every node a walk visits (a
`parent`, `resume_from` or `values` seq at or above its record's own; a
`parent` or `resume_from` that is not an extendable node), membership only for
the records a read resolves, and a malformed value body only when its value is
read, never when it is counted. A fold raises wherever its pure read raises,
but for the checks the pure walk alone makes. The verdict reports an unreadable
exit or death report in `RunResult.malformed` instead, and `ended` never
raises.

### `MalformedSchedule`

```python
class MalformedSchedule(ValueError): ...
```

A schedule subscription-v0.4 refuses, or a pure demand with a `from`, an `every`
or a `count` atom. Its message names the offending key by its path in the body
(`from.any[1].time_seconds`), and is the worker's `malformed` nak message.
`history` and `Watcher.broadcast` parse their schedule first and raise it to the
caller, writing nothing.

### `ClaimAliveError`

```python
ClaimAliveError(claim_seq: int, handle: str)
```

`report_death` refused: the claim's handle probes alive, and the caller did not
pass `force=True`.

### `ReportStarvedError`

```python
ReportStarvedError(claim_seq: int, attempts: int)  # a RuntimeError
```

A death report lost its CAS `attempts` times in a row (`CAS_ATTEMPTS`): the log
grew faster than the report could read it, and nothing was appended. It names the
claim; the caller that knows the run adds it (`LocalLauncher.reap` notes it).
`report_death`, `report_if_dead` and a launcher's reap raise it.

### `WorkerStarvedError`

```python
WorkerStarvedError(step: Literal["claim", "drain", "exit"], claim_seq: int | None, attempts: int)
```

One of the `Worker`'s compare-and-append loops lost its CAS `attempts` times in a
row: the log grew faster than the worker could read it, and that loop appended
nothing. `step` names the loop: the `claim` (`claim_seq` is then `None`), a
drain's refusal, or the `exit`, after which the claim is still held, for a death
report to end; after a starved exit only `stopped()` may be retried.

### `RunFailedError`

```python
RunFailedError(run_id: str, result: RunResult, *, target: Target, count: int)
```

A drive's claim ended with an outcome in `Outcome.failures()`. `result` is its
verdict at raise time, stamped with the run, the retry decision's input; `count`
is the target's count at the head then.

### `NoProgressError`

```python
NoProgressError(run_id: str, *, target: Target, count: int)
```

A drive's claim ended, not failing, having written no commit: a re-drive would
spin. Progress is measured per drive by `Commits()`, not by the target's stream.

### `RecordlessExitError`

```python
RecordlessExitError(run_id: str, *, launch_id: str | None, target: Target, count: int)
```

A drive's handle ended with no claim naming it and no other claim held, a second
time in a row at an unchanged head and `Commits()` count: the re-drive reproduced
it. `launch_id` is the second drive's, `None` for a foreign handle. A sibling of
`RunFailedError`, not a subclass: no claim ended, so there is no verdict.

## Convention vocabulary

### `Topic`

```python
Topic  # StrEnum: the closed, protocol-owned routing keys of log format 0.4.0
# VALUE; LIFECYCLE_STARTED, LIFECYCLE_COMMIT, LIFECYCLE_HEARTBEAT, LIFECYCLE_REWIND,
# LIFECYCLE_STOPPED, LIFECYCLE_BOUND, LIFECYCLE_NAK; SUPERVISOR_LAUNCHED,
# SUPERVISOR_TERMINATED, SUPERVISOR_DIED; CONTROL_SUBSCRIBE, CONTROL_STOP, CONTROL_WITHDRAW
```

The CLOSED, protocol-owned routing keys (`Envelope.topic`) — the complete
enumerable set the 0.4.0 schemas pin. Each member IS its wire string
(`Topic.VALUE == "value"`); the `name` axis stays open/app-owned. A body's
`<Payload>.TOPIC` is the member of its topic (`Stopped.TOPIC is
Topic.LIFECYCLE_STOPPED`).

### `Condition`

```python
Condition = dict[str, Any]
```

A term of the subscription **condition-algebra** over counts, on the wire: an
atom (`{"commits": n}`, `{"stream": x, "n": n}`, `{"time_seconds": s}`, or
`{"count": c}` in an `until` only), or `{"any": [...]}` / `{"all": [...]}`,
fully recursive. Used as `from` / `every` / `until` in a subscription body and
as the `schedule` argument to `history` and `Watcher.broadcast`. A schedule
with a `time_seconds` or `count` atom is a lease, per claim; one of lineage
counts only is durable, per lineage.

## Convention bodies (typed)

Frozen dataclasses mirroring the schemas: serialize via `dataclasses.asdict`,
parse via `Cls(**body)`. Each carries a `TOPIC` ClassVar (the `Topic` member it
rides on). The [wire topics](#wire-topics) table below maps each to its schema.

### `Value`

```python
Value(value: Any, t: float | None)
```

`value`-`v0.3`: a value of the stream or source the envelope `name` names; the
envelope `request_id` is null. `step` is removed: a value belongs to the node
whose `values` names its seq. `t` is the writer's wall clock, or null.

### `Started`

```python
Started(handle: str, t: float, resume_from: int | None, sources: list[str])
```

The claim: who computes (`handle`, the worker's self-reported liveness
handle), when (`t`, the run epoch), where it starts (`resume_from`, an
extendable node of this run, or null for no node of it), and which names it
exposes as sources (`[]` declares none). Its envelope `request_id` is the
launch id, or null when nobody launched the worker.

### `Commit`

```python
Commit(claim_seq, consumed_seq, parent, values, fired, expired, t)  # lifecycle.commit
```

A lineage node, one per `commit()`: the claim it speaks for, the drain's
watermark, the node it extends, the value seqs it commits, and the subscriptions
that fired and expired there.

### `Heartbeat`

```python
Heartbeat(claim_seq: int, consumed_seq: int, t: float)
```

Liveness only, not a node: the claim it speaks for, the control-drain
watermark, and freshness. `step` is removed: progress is the lineage's
`Commits()` count.

### `Rewind`

```python
Rewind(claim_seq, resume_from, t)  # lifecycle.rewind
```

Restates the claim's position, never who: an extendable node of the run, or null
for the root.

### `Stopped`

```python
Stopped(completed: bool, error: str | None, claim_seq: int, honored: list[str],
        parent: int | None, values: list[int] | None, fired: list[str],
        expired: list[str], t: float)
```

The worker's exit, its claim's last node; at most one per claim. `completed`
closes the lineage; `values` is null when the exit discarded its open
iteration (torn), else the pending values then the samples. `honored` is the
set of stops it honoured. Enforced: a completed exit carries no error; an error
exit discards its iteration; an exit that discarded its iteration evaluates
nothing (`fired` and `expired` empty). The exit is itself a node, named by
its seq.

### `Bound`

```python
Bound(claim_seq: int)
```

`lifecycle.bound`: a lease (a subscription with a time or count atom) bound to
the claim `claim_seq` names; the envelope `request_id` is the lease.
CAS-appended by the drain before the registration takes effect; the lease is
void once that claim has ended.

### `Nak`

```python
Nak(reason: NakReason, message: str)
```

A refused control request, correlated by `request_id` (null for a nameless
one). The refusal spends the id; dropped, never fatal to the worker.

### `NakReason`

```python
class NakReason(StrEnum): MALFORMED, UNSATISFIABLE, UNSUPPORTED
```

`Nak.reason`, lifecycle-v0.6's: why the worker refused a control request.

### `Withdraw`

```python
Withdraw(t)  # control.withdraw; request_id names what it spends
```

The asker's cancellation of a stop or a subscription.

### `Launched`

```python
Launched(handle: str, status: Literal["running"], t: float)
```

`supervisor.launched`: spawn-intent + the worker's liveness handle; the
envelope `request_id` is the launch id. `t` = the spawner's wall-clock at
launch.

### `Terminated`

```python
Terminated(reason: Reap, exit_code: int | None, signal: int | None, t: float)
```

`supervisor.terminated`: how a launch's process ended, `exited(exit_code)` XOR
`killed(signal)`; the envelope `request_id` is the launch id. `t` = the
reaper's wall-clock. It names a launch, not a claim: it is the verdict only of
a run nothing claimed.

### `Reap`

```python
class Reap(StrEnum): EXITED, KILLED
```

`Terminated.reason`, supervisor-v0.5's: how a launch's process ended.

### `Died`

```python
Died(claim_seq, outcome, evidence, t)  # supervisor.died
```

The death report: a third party's account of how the claim ended, `outcome` a
death's (`preempted`, `errored`, `killed`, `lost`), never a completion.

### `Node`

```python
class Node(Protocol): ...  # a Commit or a Stopped
```

A lineage node: `claim_seq`, `parent`, `fired`, `expired`, `committed`,
`iterations`, `extendable`, `closed`, `t`. Closed if and only if a completed exit;
torn if and only if an exit that discarded its iteration; extendable otherwise.

---

## Wire topics

The reserved topics of log format 0.4.0, their body shape, producer/consumer,
and the schema file that pins each (`protocol/`). The substrate routes on
`topic`/`name`/`request_id` and never parses `body`.

| topic | body | produced by | consumed by | schema |
|---|---|---|---|---|
| `control.subscribe` | `Condition` (`{from?, every?, until?}`); `name` = the source, or null for pure demand | any asker | worker | `subscription-v0.4` |
| `control.stop` | `{from?}`; `request_id` required | any asker | worker | `subscription-v0.4` |
| `control.withdraw` | `Withdraw {t}`; `request_id` = the request it spends | the asker | worker, observers | `subscription-v0.4` |
| `lifecycle.started` | `Started {handle, t, resume_from, sources}`; `request_id` = the launch id | worker | observers | `lifecycle-v0.6` |
| `lifecycle.commit` | `Commit {claim_seq, consumed_seq, parent, values, fired, expired, t}` | worker | observers | `lifecycle-v0.6` |
| `lifecycle.heartbeat` | `Heartbeat {claim_seq, consumed_seq, t}` | worker | observers | `lifecycle-v0.6` |
| `lifecycle.rewind` | `Rewind {claim_seq, resume_from, t}` | worker | observers | `lifecycle-v0.6` |
| `lifecycle.stopped` | `Stopped {completed, error, claim_seq, honored, parent, values, fired, expired, t}` | worker | observers | `lifecycle-v0.6` |
| `lifecycle.nak` | `Nak {reason, message}`; `request_id` = the refused request | worker | the requester (by `request_id`) | `lifecycle-v0.6` |
| `lifecycle.bound` | `Bound {claim_seq}`; `request_id` = the lease | worker | worker, observers | `lifecycle-v0.6` |
| `supervisor.launched` | `Launched {handle, status, t}`; `request_id` = the launch id | launcher | observers | `supervisor-v0.5` |
| `supervisor.terminated` | `Terminated {reason, exit_code?, signal?, t}`; `request_id` = the launch id | launcher | observers | `supervisor-v0.5` |
| `supervisor.died` | `Died {claim_seq, outcome, evidence, t}` | any party with evidence | observers, worker | `supervisor-v0.5` |
| `value` | `Value {value, t?}`; `name` = the stream or source | worker | observers | `value-v0.3` |

The envelope itself is pinned by `envelope-v0.2`. Each convention schema is
`additionalProperties: false` and independently versioned; adding a field is a
deliberate version bump (never silent).

## Environment variables

`current_channel()` (and `LocalLauncher`, which sets them for the child) reads:

| variable | meaning | default |
|---|---|---|
| `RUNSTATE_RUN_ID` | the run to attach to | required (else `KeyError`) |
| `RUNSTATE_CHANNEL_ROOT` | the directory/namespace holding runs | `None` |
| `RUNSTATE_CHANNEL_BACKEND` | `memory` / `sqlite` / `postgres` | `sqlite` |
| `RUNSTATE_LAUNCH_ID` | the launch's correlation id (re-emitted on `started`) | `None` |
| `RUNSTATE_SQLITE_JOURNAL_MODE` | sqlite journal mode (`DELETE` on NFS) | `WAL` |

## Builtin exceptions

Beside the errors above, the public functions raise builtins: `create_channel`
raises `ValueError` (bad backend) / `ImportError` (postgres without psycopg);
`current_channel` raises `KeyError` (unset `RUNSTATE_RUN_ID`); `Watcher.poll`
raises `KeyError` (untracked run); `Worker.emit` raises `ValueError` on a
declared source, `set` on an undeclared name, and `include` on a seq it may not
commit; `Worker.steps` and `ensure` raise `ValueError` on a target naming a
declared source; `report_death` raises `ValueError` on a record that is not a
claim, an outcome that is not a death's, or empty evidence; `Watcher.broadcast`
raises `ValueError` on an empty `request_id`; `await_consumed` raises
`ValueError` and `TimeoutError` as its entry says.
