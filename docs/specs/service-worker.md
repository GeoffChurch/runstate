# Spec: the service worker (leased demand, the careful death, `serve`)

**Status:** SHIPPED 2026-06-10 (converged and implemented the same day; kept
as the record of what was built — `runstate/worker.py` (`serve`/`retire`/
`pinned`, the expiry records, the answer-fold refold), `schedule.py`,
`observables.live_demand`, `watcher.await_consumed`,
`tests/test_service_worker.py`, `examples/monitor/`). Produced by a five-agent
design review (clean-room enumeration, unification adversary, prior-art
survey, ownership audit, and an adversarial attack on the then-leading
proposal) plus dialectic. Two candidate framings were refuted on the way and
are recorded below — including the proposal this spec's author entered with.
First half of Cluster 1 (synergy map); the validating consumer is the
on-demand host-metrics monitor (`examples/monitor/`, this spec's deliverable);
the second half (lazy-launch, the relaunch decider) is `specs/lazy-launch.md` (shipped 2026-06-11), a follow-on spec that
consumes the demand fold this one makes possible.

## The model

> **One worker primitive, two demand durabilities.** *Durable* demand is the
> launch contract (the target you were started with — run-episodes
> Decision 4); *leased* demand is subscriptions (rescindable, expirable). A
> worker class is not a kind of worker; it is which demand its continuation
> consults. **Eager is the primitive** — a null worker that opts out of
> control entirely is a valid protocol citizen and is eager by construction —
> and the service policy is a worker-owned, explicit **opt-in by verb**:
> nothing couples termination to observation unless the worker calls the verb
> that introduces the coupling. (Greenfield-justified, not compatibility: the
> coupling has two correct answers by workload, which is the definition of
> policy. The autonomous requirement, named here once: **a disconnected
> dashboard must not stop training** — an autonomous run's life never depends
> on its observers.)

Prior-art anchor (survey): Orleans is the near-isomorph — eternal identity ÷
activations ≈ `run_id` ÷ episodes; durable reminders vs volatile timers ≈ the
two demand durabilities. We copy durable-demand-reactivates and
the-entity-deactivates-itself; we *reject* Orleans' idle-timeout, which is
forced there only because grain references are unenumerable — runstate's
demand is explicit log records, so **ref-count-exact, no grace** is available
and correct (the log buffers demand across relaunch, so the races that force
grace windows elsewhere don't exist; cold-start hysteresis relocates to the
demand side as client-chosen `until` keepalive leases).

**The invariant this spec names and keeps:** *registered ⟺ a future fire is
possible.* Registration is not a leased state; it is derived from
fire-possibility (`every` absent ⟹ one-shot; `until` bounds firing, not
membership). Corollary: **pinned ⟺ someone holds an outstanding claim on your
output** — design-v0.2.md §7's read-vs-subscribe line as a theorem. There is deliberately
no pure pin (existence-demand decoupled from data-demand): durable existence
lives in the launch contract; anticipatory warmth is honest **renewed**
periodic demand (`every: {time_seconds: …}`, refreshed — a time-referencing
registration is episode-scoped per `specs/time-lease-boundary.md`, which is
the right semantics: standing warmth nobody renews was the immortal-pin smell
all along), not a new vocabulary.

## The four pieces

### 1. Expiry counter-records — universal, all workers

Today a registration that dies of natural causes (its `until` met, or its
single one-shot fire consumed) is deleted from worker memory with **no log
record** — so the drain rule's fold is wrong across episodes (a resumed
worker resurrects dead keepalives and re-fires consumed one-shots), demand is
not log-derivable (the fact the relaunch decider needs), and until-expiry is
an *unsignalled* never-fire case (design-v0.2.md §6 has three handlers; this
was a silent fourth).

**The completed fold rule — positional, the discharge floor's third
instance:** *a `control.subscribe` is live until an **answer** — a
`control.unsubscribe` or a `lifecycle.nak` bearing its `request_id` — **follows
it by `seq`**.* (Strictly positional, never an id-set: a later subscribe
reusing an answered `request_id` is a fresh, live request — so
resubscribe-after-refusal needs no fresh id, a keepalive refresh that crashed
mid-cycle refolds live, and an unsubscribe that *precedes* its subscribe
answers nothing, matching the in-memory drain exactly. Naks with
`request_id = null` answer nothing.) The worker guarantees every drained
subscribe is eventually answered by exactly one of the two:

- **Expiry → the worker appends `control.unsubscribe`** with that
  `request_id` (body `{}`, exactly the client's verb), *then* deletes
  (emit-then-delete: the record lands before memory changes, so a crash
  between the two re-derives correctly). Covers both expiry causes: `until`
  met, and one-shot consumed. No new vocabulary, no schema change; and
  visibility scoping delivers the expiry notice to exactly the right observer
  for free (the record carries their `request_id`).
- **Refusal → the nak already on the log is the answer.** Refusal happens at
  registration alone: the structural gate (`malformed_schedule`, schedule.py)
  validates the full grammar at drain time, so registered ⟹ evaluates cleanly
  (*amended 2026-07-10*: the second nak site — a registration *naked at
  service time*, a schedule that registered but blew up when evaluated
  mid-`_service` — is subsumed by the gate and gone). A refusal needs no
  unsubscribe. Consequence, deliberate: **nak is final** — a refold skips a
  subscribe whose nak is on the log, so resumed episodes stop re-nakking the
  same bad request (today's unbounded duplicate-nak growth), and a subscribe
  refused under one episode's conditions (e.g. step-keyed, stepless episode)
  stays refused; the requester was told, and resubscribes under conditions
  that admit it.

**The fold gets one public home: `observables.live_demand(channel) ->
list[Envelope]`** — the subscribe envelopes with no following answer. The
worker's refold and the relaunch decider consume the *same named rule*; two
private copies of one boundary rule is the F7 failure class `latest_episode`
exists to prevent, and a cli-status "pinned" column plus log-derivable
idle-vs-dead fall out of the public form for free. (Value-blind: it reads
schedule *shape* — the time-atom check of `specs/time-lease-boundary.md` —
never payloads. *Amended 2026-06-11*: the original "body-untouched" purity
claim was consumed by that spec, which also added a third answer kind —
**a time-referencing subscribe can be discharged recordlessly by the next
episode boundary**, so "answered by exactly one of the two" reads "…or by
the boundary `started` already on the log.")

Implementation shape: the refold's answer-awareness mirrors the discharge
floor — `__init__` already reads the whole log for the CAS claim; the same
read computes the positional fold (zero extra I/O; at implementation,
consolidate the claim's read, the discharge floor, the answer fold, and the
liveness check onto that one read). Mid-episode the worker's
own answers are in-memory knowledge. The worker also re-drains its **own**
unsubscribes next tick (the cursor sits behind them): an unsubscribe for an
unknown `request_id` is a **silent no-op**, never a nak — else the worker
naks itself once per expiry.

**The invariant is enforced, not just named:** *a registration expires the
moment no future fire is possible.* The shipped expiry checks (`until` met;
one-shot consumed) are two cases of it; this spec adds the third: after a
fire, an `every` that can never be satisfied again on this worker's
coordinates — e.g. a step-only `every` on a stepless service, the most
natural subscription a service receives — **expires** (fires its legitimate
once, then retires with its expiry record) instead of sitting registered
forever as an accidental pin that outlives its crashed client.

This is *bookkeeping, not policy* — the log's meaning must not depend on a
constructor flag — hence universal. (Role-table note for design §5: the
worker completing the subscribe/unsubscribe pair is the same shape as the
worker completing stop/`stopped`; the producer-role convention gains this
documented completion case.)

### 2. The careful death — `Worker.retire() -> bool`

A zero-demand death races demand: between the tick's drain-read and the dying
breath, a subscribe can land — orphaned (never serviced, never naked) and
unrescued (the lazy-launcher saw a live episode at send time). The birth side
already closes its race with the CAS claim; **the death gets the mirror**:

```
retire():  loop:
             ONE global read past the last observed seq; process its
               control.* records (evaluated at the worker's last safe-point
               coordinate — self._last_step; None for a stepless service)
             if that processing appended anything (a nak, an expiry
               unsubscribe): continue          # re-read before any CAS
             if pinned: return False           # new mail — keep serving
             send lifecycle.stopped with expected_seq = that read's last seq
             if the CAS lost: continue         # something landed after it
             on win: mark stopped (the idempotent latch); return True
```

The load-bearing discipline: **the CAS's `expected_seq` comes only from a
read, never from the worker's own append's returned seq** — an own append can
land *on top of* an unseen racing subscribe, and using its seq would let the
CAS win past it (the orphan this loop exists to prevent). Hence
append ⟹ one more read. Termination: a quiet log costs two iterations; each
foreign append costs one more (an adversarial append stream can starve the
death indefinitely — accepted, it is no new power: such a writer could
equally send stops). If `send(expected_seq=)` *raises* (the indeterminate
backend wedge), the shipped backends guarantee nothing was written — retire
may simply re-loop; an unhandled raise reaching `__exit__` verdicts
`errored`, which is correct for a wedged backend. A conditional stop drained
inside `retire()` that has not yet triggered is simply pending — and the
retire's own `stopped` discharges it, by stop-discharge's shipped broadcast
rule; stated here so no one "fixes" it.

Episodes are now CAS-claimed at **both ends**; the log cannot lose a message
in either gap. The reap's `stopped` makes no claim (`completed=False` →
`preempted`) — idle is the resumable verdict, and "idle by design vs dead" is
log-derivable (a `stopped` with zero live leased demand, foldable thanks to
piece 1), so no wire field declares the worker class. `retire` (the worker
retires itself), not "reap" (observers reap corpses — launcher vocabulary).

The retry-on-loss is why no constructor flag exists anywhere in this design:
a lost death must *return to serving*, which only a loop can do — a
`service=True` flag routed through `tick()`'s return cannot re-enter the loop
from `__exit__`. **The opt-in is which verbs you call.**

### 3. The driver — `Worker.serve()`

`steps(total)` and `serve()` are named policies over the true primitives
(`tick` + the worker-owned stop fold), and they are exactly the two whose
continuation source is *protocol-visible*: the launch contract's target
(durable) and the log's subscription level (leased). The third source —
convergence/patience — is workload-private and correctly has no driver.
("Run while ∃ live demand of either durability" — the conceptual subsumer —
is the refuted lazy-primitive rule; the two drivers are its sound projection.)

```python
def serve(self):                      # stepless: a service has no step axis
    if self._lost: return
    i = 0
    while True:
        yield i                       # body does its work (set values, sleep)
        if self.tick(step=None):      # drain, service, beacon; commanded stop?
            return
        if not self.pinned and self.retire():
            return
        i += 1
```

Ticks are stepless (`step=None`): the heartbeat carries `step: null` (§7's
service-worker line), step-keyed subscriptions nak as unsatisfiable
(existing, correct), and emitted values are honestly outside the step-indexed
`value_series` domain — service consumers read the register
(`latest("value", name)`), and a fabricated safe-point "step" would be a
coordinate lie. Pacing belongs to the body (sleep or work). First-launch
safety by construction: the first `tick` drains the subscribe that launched
the worker before any retire check (register-before-reap, owned by loop
order). Composition admitted but not built: a target-bounded service
(`serve` ∧ step-bound) if a consumer ever pays for it.

### 4. The gauge — `Worker.pinned`

```python
@property
def pinned(self) -> bool:    # someone holds a live claim on my output
```

Plain truth of the live registration set — which means it **can** mislead as
a demand gauge before the first drain (a pre-staged subscribe sits undrained
while `pinned` reads False). The property does not pretend otherwise; instead
the blessed paths (`serve`, `retire`) structurally cannot consult it that
early, and the bare-tick recipe is documented tick-first
(`w.tick(...); if not w.pinned and w.retire(): ...`). The `stop_pending`
sibling: a side-effect-free level for host loops. Named for the mechanism
(`pinned` — §7: reads never pin, subscribes do), not the interpretation
("demand" stays prose).

## The pinned-service pattern (eager-as-lazy, documented not defaulted)

A service can be made to *act* eager: pre-stage a pinning subscription before
the spawn (the durable channel holds it), with a real recurring schedule —
e.g. `{every: {step: 1}, until: {step: N}}` from a consumer that genuinely
wants the series. Two sharp edges, stated loudly: a bare `{until: {step: N}}`
is **one-shot and evaporates after its single fire** (the invariant: `until`
bounds firing, not membership), and the pattern binds only spawn paths that
remember the pin — which is exactly why it is a pattern and not the
primitive (a bare-spawned worker must be fail-safe, not fail-deadly).

## Algebra hygiene — close the accidental pure pin

`{"from": {"count": k}}`, k ≥ 1, is a circular gate: `from` opens only at
fire-count ≥ k, and count advances only on fires — never fires, never
expires: a pure pin by accident, violating the invariant. (`every` containing
a count atom is the same circle one fire later: `_triggers` evaluates `every`
at `count=0`, so it fires once and is registered forever.)

The subscription schema **already forbids** count atoms outside `until`
(`count` is grammatical only in `UntilTerm`) — but the worker does not
schema-validate bodies, so a nonconformant sender reaches the hole. Fix,
worker-side defense-in-depth matching the schema exactly: **a count atom
anywhere inside `from` or `every` naks as `malformed`** ("count thresholds
are valid only in `until`") — on `control.subscribe` **and** on
`control.stop`'s `from` (whose decision evaluates at `count=0` forever, the
same circle). This closes every variant at once — the conjunctive case, the
`any`-armed case (including stepless workers, where the step arm can't open
either), and the `every` circle — with no openability analysis needed. The
remaining never-recur shapes (a step-only `every` on a stepless worker) are
handled by the enforced invariant in piece 1: fire once, then expire.

## Rejected alternatives (the refutation record)

- **Lazy as the primitive** (run-while-∃-demand as the universal rule):
  killed by the undemanded bootstrap — a bare-spawned worker (ray / submitit /
  `python train.py`, the unbindable paths) dies at tick 1; the self-demand
  repair is circular. Its true content survives demoted: demand-folded-
  against-progress **is** `ensure(until=)`, at Layer 3.
- **The reap as condition-algebra data** (`control.stop {from:{subs:0}}`):
  monotonicity is load-bearing — a non-monotone demand coordinate breaks the
  stop latch, the conjunctive-corner check, and the static nak; and the
  discharge fold kills a standing stop at each episode's end. The reap is
  standing configuration, not a command-fact — stop-discharge's own ontology.
- **Property + recipe only** (no worker-side behavior; this spec's author's
  entering position): refuted — soundness needs the death-CAS and the expiry
  records, neither reachable from recipe code (`stopped()` exposes no CAS;
  expiry is loop-internal). Protocol behavior wearing recipe clothing.
- **Constructor flag / universal reference-loop behavior**: the flag cannot
  own the retry-on-loss (above); universal reap inverts Requirement B.
- **Layer-3 reaper / orchestrator stop-on-idle / substrate registry**:
  unbindable (the shape of stop-discharge's rejected A6 — an optional helper
  cannot bind foreign spawners), dead-reckons expiry the log didn't record,
  or re-creates the rejected liveness lease and breaks observer invisibility.
- **A `lifecycle.expired` event** instead of reusing `control.unsubscribe`:
  a second counter-record kind for one fact — the fold grows a case, the
  lifecycle schema bumps, and canonical form loses (one fact, one record).
- **A `demand_bound`/class field on `started`**: the demand fold (piece 1)
  answers idle-vs-dead from the log; a wire field would freeze a speculative
  judgment under `additionalProperties: false`.
- **A pure-pin vocabulary**: breaks registered⟺fire-possible; both genuine
  existence-demand cases already have homes (launch contract; honest periodic
  demand).
- **Worker-side "park until conditions admit it"** (the would-be revisit
  trigger for nak-finality): already a consumer composition — the lifecycle
  is logged, so the parked client watches for the next `lifecycle.started`
  and resubscribes (same `request_id` is fine; the fold is positional). A
  worker-side park would add a *third* registration state to a two-state
  fold, for something derivable. No trigger remains.

## Scenarios (the contract)

| scenario | behavior |
|---|---|
| fresh launch on a pre-staged subscribe | first tick drains it before any retire check — never reaps at birth |
| keepalive refresh (unsub+resub) in one drain | whole-tick drain; `pinned` never observes the transient zero |
| refresh genuinely separated in time | retire, then relaunch on the new subscribe — correct service behavior, not a bug |
| subscribe lands between final drain and dying breath | death-CAS loses → re-drain → `pinned` again → keep serving |
| client dies; keepalive `until` lapses while the worker is live | expiry unsubscribe written; demand fold drops it; **episode N+1 does not resurrect it** |
| client dies; the worker also dies (crash) before noticing the lapse | *(amended by `specs/time-lease-boundary.md`)* the lease re-anchors **at most once** (into its first possible drainer) and is recordlessly voided by the boundary after that — the relaunch decider expects nothing |
| a subscribe nak'd in episode N re-encountered by episode N+1 | skipped — the nak is its answer (no duplicate nak per episode; resubscribe to be re-considered) |
| one-shot served, nothing else | its expiry record lands; worker retires — a query-response service in one episode |
| a subscribe raced into `retire()` | registered during the retire drain; `pinned` again → keep serving; first serviced at the next full tick (one body-cycle of latency, by design) |
| commanded stop while pinned | the stop fold is unchanged and wins; `stopped` discharges it |
| conditional stop drained during `retire()`, not yet triggered | pending; the retire's own `stopped` discharges it (the shipped broadcast rule) |
| autonomous run, dashboard attached/detached | never calls `serve`/`retire` ⇒ no term couples its life to observation |

## Implementation sketch

`worker.py`: emit-then-delete `control.unsubscribe` at `_service`'s
`decision.expired` site (the naked-at-service site keeps its nak as the
answer — no unsubscribe); unknown-id unsubscribe stays a silent no-op; the
`__init__` whole-log read additionally computes the positional answer fold
(the discharge-floor pattern) so the refold skips answered subscribes;
`pinned` property; `retire()` (the fused read→process→CAS loop above; sets
the idempotent stopped-latch on win only); `serve()` generator.
`schedule.py`: `Subscription` expires when no future fire is possible (the
never-recur `every` case); count atoms in `from`/`every` nak as `malformed`
(subscribe and stop). `watcher.py`: `await_consumed` treats an answer as an
answer — a nak bearing the `request_id` resolves it regardless of the
heartbeat watermark, and a terminal `stopped` with no following episode
resolves it as refused-by-death (else a request answered inside a winning
`retire()` drain deadlocks its waiter, whose nak arrives after the final
heartbeat).
`observables.py`: `live_demand` (above). Implementation note: the static
check (`is_unsatisfiable`, at registration) and the enforced dynamic expiry
(after a fire) are one predicate — "zero future fires from this state" —
evaluated at two times; implement it once in `schedule.py`, not as a third
parallel check.
No schema or wire change (`control.unsubscribe` body `{}` conforms as-is;
`tests/test_schema.py` extends its scenario to cover a worker-written one).

## Docs deliverables (the fold-back list)

design-v0.2.md: §5 role table gains the worker-as-pair-completer case
(`control.unsubscribe` expiry records); §6's "three never-fire causes, three
handlers" → four (the expiry record is the fourth, `request_id`-addressed);
§6's ack paragraph + `await_consumed` contract: an answer resolves regardless
of the watermark, and terminal-`stopped`-with-no-following-episode resolves
refused-by-death (note the asymmetry is principled: the autonomous worker's
plain `stopped()` needs no death-CAS *because* refused-by-death answers its
raced subscribers observer-side); §6 reference-loop steps (emit-then-delete;
answered-subscribe skip; count-atom naks); §7's "one follow-by-`seq` fold,
two directions" → **name the pairing-by-seq rule once** (stop/`stopped`,
episode terminality, subscribe/answer — and note `value` fires are
deliberately *not* answers, keeping the fold body-light), instances
enumerated — the formal version goes to
`docs/backlog/protocol-algebra.md`; §7's lifeline paragraph rewritten (the
kernel "ref-count-exact, stops at zero" survives; "needs no dedicated
mechanism" and the "startup grace window" do not); §12.1 pointer to this
spec's demand fold; §12.5 annotated (the replay boundary is now *bounded* by
expiry records). stop-discharge.md: one forward supersede-note (the
rescission claim was incomplete until expiry records; counter-records are now
{unsubscribe, nak}, positional). backlog/run-episodes.md: the no-grace
section superseded-stronger (the death-CAS closes the race its argument never
covered; the "subscription-driven launch" precondition dissolves — fail-safe
by construction); open questions mode-vs-recipe and idle-vs-finished →
answered. backlog/index.md + synergy-map.md: Cluster-1 first half specced;
vocabulary updated (retire/pinned/leased); the discharge-by-id entry notes
the answer fold as a second positional rule needing causal generalization
under replication — sharpened now that the worker itself writes `control.*`.
README's "next up" line. CLAUDE.md architecture/test lines post-implementation.

## Non-goals

- Lazy-launch / the relaunch decider (the follow-on spec; this spec's demand
  fold is its input). One input recorded for it: `ensure` over a *stepless*
  service is unsupported (no step axis to fold progress on — its no-progress
  guard is disarmed exactly when it would be needed). *(The originally
  recorded second constraint — bound the relaunch cadence against re-anchored
  leases — was deleted by `specs/time-lease-boundary.md`: the boundary rule
  bounds the ghost at ≤2 relaunches by construction; the waker needs no flap
  policy.)*
- The second `ensure` producer and the index algebra (after lazy-launch).
- Prewarm helpers, grace windows, a time-indexed value series.
- Any constructor flag, mode, or wire declaration of worker class.

## Tests (TDD targets; all backends)

- Expiry records: until-lapse and one-shot-consumed each append
  `control.unsubscribe` with the right `request_id` (emit before delete); a
  resumed episode re-registers **none** of: an expired lease, a consumed
  one-shot, a naked subscribe (the resurrection regression, all three
  answer kinds); no duplicate nak across episodes; the worker re-draining its
  own unsubscribe is a silent no-op; a registration-time nak writes no
  unsubscribe (the nak is the answer).
- `pinned`: false on empty; true after a drain registers; false after the
  client unsubscribes; false after until-expiry within the same tick.
- `retire`: wins on a quiet log (one `stopped`, `preempted` projection);
  loses to a raced subscribe (a subscribe sent after the worker's last tick →
  `retire()` returns False, the subscription is registered and serviced);
  wins even when its own drain appends records (a malformed subscribe in the
  tail → nak appended → the CAS still wins on the post-drain observation);
  idempotence with `__exit__` (no second `stopped`).
- `serve`: serves a pre-staged subscribe from tick 0; exits on zero demand;
  exits on commanded stop; the death-race scenario end-to-end.
- Algebra: `{"from": {"count": 1}}`, `{"every": {"count": 2}}`, and
  `{"from": {"any": [{"count": 1}, {"step": 5}]}}` all nak `malformed`
  ("count thresholds are valid only in `until`"), on stop's `from` too;
  count in `until` still registers. A step-only `every` on a stepless worker
  fires once, expires, and writes its expiry record (the enforced invariant);
  an `every` with a time arm recurs.
- `retire` race-discipline: a subscribe landing between the read and the CAS
  is caught (CAS loses, next read registers it); an own-append (nak) followed
  by a racing subscribe is caught (append forces the re-read) — the A1
  interleaving pinned as a regression test.
- `await_consumed`: resolves on a nak even when no later heartbeat ever
  carries the watermark (the retire-win path); resolves refused-by-death on
  terminal `stopped` with no following episode.
- `live_demand`: empty log → []; subscribe → [it]; subscribe…unsubscribe →
  []; subscribe…nak(its id) → []; answered id re-subscribed later → [the
  later one] (positional, not id-set); unsubscribe-before-subscribe answers
  nothing; agrees with the worker's `pinned` after every scenario row above.
- `examples/monitor/`: an on-demand host-metrics service (stepless,
  `latest`-read, keepalive-leased) driven by a small client — the dogfood.
  Carries the cadence guidance: the body's sleep must be ≪ min(lease period,
  staleness threshold) — one tick cadence serves beacon, lease-expiry check,
  and refresh-ack latency all at once.
