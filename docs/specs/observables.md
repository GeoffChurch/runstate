# Spec: observables (the stateless observer plane)

**Status:** SHIPPED 2026-06-10 (converged via the three-question deliberation
and implemented the same day; kept as the record of what was built — see
`runstate/observables.py`, `vocabulary/handle.py`, and
`tests/test_observables.py` / `tests/test_handle.py`). Completes the synergy-map **Cluster-3 read-projection
batch** (mycooc audit F5–F8); the consumer call sites in
`~/src/mycooc` (`channel_read.py`, `_channel_progress`,
`_channel_live_status`, `_channel_pid`, `_channel_is_alive`) are the
basis-completeness oracle, and the acceptance test is **deletion**: each helper
must collapse to a call into this module (the mycooc-side checklist:
`mycooc/docs/backlog/infrastructure/runstate-adoption-sweep.md`).

**Converted to reference by name 2026-10-03** ([`reference-by-name.md`](reference-by-name.md) §5, log
format 0.3.0): the folds read the records that **name** what they ask about, not the ones that sit next to
it. "The named reads" below states them; the stop fold gained an incremental form on the Watcher.

## The model

A new convention-layer module **`runstate/observables.py`** — the **stateless
observer plane**: pure, body-aware folds `log → derived view`. It absorbs
`liveness.py` (no users yet ⇒ free move, no shim): liveness is a *sub-concern*
of stateless observation, and `value_series` (data plane) made the old module
name a lie.

- **The role contract / membership test:** stateless, observer-side,
  derived-never-stored. Needs a cursor or a clock? It's the `Watcher`'s
  (the *stateful* observer). Parses a handle string? It's `vocabulary/`'s.
  The Layer-3 observer story becomes one crisp boundary: **observe
  statelessly** (this module) vs **watch statefully** (`watcher.py`).
- **Why "observables" (not `projections`/`observe`):** names the role, not the
  mechanism — a question you can ask of the run's state. Repo-coherent
  (observer-side vocabulary; the §7 **read-vs-subscribe** line is exactly the
  observation-is-non-disturbing boundary: reads never pin, subscribes do). The
  algebraic reading is apt and *classical*: the folds form a commutative
  function algebra on log-states — all commute, evaluation disturbs nothing —
  while the **condition-algebra** (`schedule.py`, monotone predicates) is the
  projection/question lattice beneath them (observables are built over
  projections, not the other way round). Design §4's "Read projections" keeps
  its term for the substrate level: the substrate *projects* (body-opaque
  shapes), the conventions *observe* (body-aware folds). NOT Rx-style
  observables — pull-side pure functions; the push side is the subscription
  convention.
- **Tolerance splits by plane:** the substrate admits foreign bodies on any
  topic. The measurement folds (`progress`, `value_series`, `live_demand`,
  and — added 2026-07-11 — `undischarged_stops`, the stop-discharge rule's
  observer home, pairing instance 1's public fold beside `live_demand`'s
  instance 2) skip what isn't a measurement — one lost point is marginal; the verdict
  folds (`peek_terminal`, `live_episode`, and `await_consumed`'s nak parse)
  decide categorical answers from single records and refuse to guess — an
  uninterpretable record raises the typed, catchable `MalformedRecordError`
  (loud-by-default is catchable; silent-by-default is unobservable).

## Surface

```python
# moved verbatim (semantics unchanged), live_episode refactored through latest_episode
RunResult; peek_terminal(channel) -> RunResult | None
live_episode(channel) -> str | None          # newly re-exported at package root

# new
latest_episode(channel) -> Envelope | None   # the latest lifecycle.started envelope
progress(channel) -> int | None              # max step the trajectory reached
value_series(channel) -> dict[str, dict[int, Any]]   # {name: {step: value}}

# the answer folds' public homes (reference by name)
live_demand(channel) -> list[Envelope]       # the subscribes no answer names
undischarged_stops(channel) -> list[Envelope]  # the stops no answer names

# vocabulary/handle.py (NOT this module — handle grammar, not a log fold)
handle_pid(handle: str) -> int | None

# watcher.py (NOT this module — it keeps a cursor)
Watcher.pending_stops(run_id) -> list[Envelope]  # undischarged_stops, incrementally
```

All exported from the package root.

### The named reads

Each fold reads the records that **name** what it asks about (`reference-by-name.md` §5), so its answer
is the same whatever order records arrive in, within the orders that remain: the order among claims, each
writer's own order, and the causal window after a claim.

- **The current episode** is the latest claim (`latest_episode`). The claim CAS totally orders claims, so a
  claim's `seq` is both its name and its rank; every record that speaks for the episode carries it as
  `claim_seq`.
- **The current heartbeat** is the newest beat naming the current claim (`current_heartbeat`). It searches
  newest-first, and on a miss (a displaced worker beating over its successor) searches backward toward the
  claim in a window that grows ×4 each time. It feeds `progress`, the Watcher's staleness credit and
  cold-attach seed, and `await_consumed`'s watermark.
- **A claim's terminal** is the `stopped` naming it (`_terminal_stopped`): strict for the verdict
  (`peek_terminal`), where a `stopped` that names nothing raises `MalformedRecordError`, and tolerant
  for `progress` and, deliberately, for the claim gate `live_episode`, which skip it (a record that
  names nothing is no evidence that the claim ended). Only records after the claim are
  read: a record cannot name a claim it never saw. Two terminals naming one claim resolve newest-wins.
- **The answer folds** join on `request_id`, wherever the answer sits: `live_demand` (a subscribe is live
  until an unsubscribe or nak names it, and a lease until its `lifecycle.bound` voids it) and
  `undischarged_stops` (a stop is pending until a `stopped.honored` or a nak names it). One answer rule
  serves both folds, the worker's drain, `Watcher.pending_stops` and `await_consumed`, so they cannot
  disagree.

**`Watcher.pending_stops` is the incremental form of `undischarged_stops`.** The pure fold reads every
stop, `stopped` and nak, so it is the one scaling cost (about 10 ms at 2,000 stops). The Watcher keeps, on
the run's entry, the unanswered stops by id, the **spent ids** (every id an answer has named), and its own
read cursor, separate from the one `iter_events` advances. Each call reads only the three topics after
the cursor: a stop is added unless its id is spent, and a `stopped` or nak spends the ids it names and
removes them. Remembering the spent ids is what makes it equal the pure fold, which treats an answered id
as spent wherever the answer sits; an answer that arrives before its stop is handled too. Memory grows by
one short id per answered stop, and time per call is proportional to the new records. The cursor starts at
0, so the first call computes the pure fold. It lives on the Watcher by the membership test: it keeps a
cursor.

### `latest_episode` — a named rule, not a computation

Returns the latest `lifecycle.started` envelope, `None` if no worker ever
attached. *Latest* means latest — live, cleanly ended, or crashed alike
(deliberately **not** "current": liveness is `live_episode`'s composition, and
the static reading wins over the dynamic connotation). The fold is trivially
`channel.latest("lifecycle.started")`; what the function owns is the
**episode-boundary derivation rule** — knowledge misapplied twice by the first
consumer (audit F7: oldest-`started` pid, unscoped status reads), and the one
place that changes if explicit episode markers ever land (run-episodes
Decision 1's named future refinement). Returns the raw `Envelope` (consumers
use `.seq` as the episode-window watermark — `ch.read(after=e.seq, …)` — and
`.body["handle"]`); **no reified Episode type** (nothing to normalize, unlike
`RunResult`'s three-tier unification, and a view type invites the span
ontology Decision 1 declined; `Started(**e.body)` is the typing idiom).
`live_episode` = `latest_episode` + no `stopped` naming it + handle-resolves;
`peek_terminal`'s `_episode_stopped` is its terminal mirror (the `stopped`
naming the latest claim). The launcher tier is **not** a mirror of it:
`_launcher_terminal` anchors to the *claimed* episode and pairs the death to
its launch by correlation id, because a third-party record is neither
self-identifying nor reliably ordered (`specs/launcher-record-identity.md`).
Both tiers obey one rule — **a terminal stands until a new episode claims** —
and `_verdict_record` names the single record `peek_terminal` speaks for (the
Watcher reads its seq rather than re-deriving which terminal counts).

### `progress` — publishing `memoizer._progress`

`max(current heartbeat.step, current terminal's final_step)`, **`None` if
neither axis has a value** — both records **naming the latest claim**, so it is
the current episode's frontier and may decrease across an episode boundary (a
resumed episode rolled the frontier back). Otherwise the fold `memoizer._progress` already is (mycooc's
`_channel_progress` documents itself as a hand copy of it), with one
public-surface correction: `None` for absence instead of the in-band `-1`
sentinel (the repo's own convention — `peek_terminal`/`latest` return `None`;
the `-1` was private arithmetic convenience, and stays *inside* the memoizer
as a local adaptation).

### `value_series` — the register projection on the (name, step) plane

A `value` event is a *sample* of the worker's current-value function
(`set(name, value)` + `tick(step)`: one value per (name, step); concurrent
subscriptions duplicate samples differing only in `request_id`). The log
therefore determines a partial function **(name, step) → value**, and the fold
that recovers it is §4's **register projection** (`latest`) lifted pointwise:
**last-write-wins by `seq` per (name, step) cell.**

- **Episode rewinds resolve only where they overlap:** when ep2 resumes from
  an earlier checkpoint than ep1 reached, the rewritten steps' samples
  last-win. The orphaned branch does **not** drop out: ep2 need not rewrite
  every cell below its own frontier (a coarser stride, or a metric it stops
  emitting), so ep1's values survive at the cells ep2 never revisited, and the
  fold can return a series no single execution produced. It is a convergent
  merge, not the as-resumed trajectory; projecting one lineage needs a fork
  point, which the log does not carry. The raw events stay on the log for
  forensics.
- **Shape: the family, zero arguments** — `{name: {step: value}}`, the whole
  curried projection in one log pass. Per-name access = indexing; name
  enumeration = `.keys()` (free); oracle-exact (mycooc's `channel_metrics`
  deletes with no call-site change). A pushed-down `name=` filter is a
  **compatible future refinement** when a large-log consumer exists — the
  hybrid's union return type is a wart we don't buy today.
- **Domain rules:** skip events with no `name`, null `step`, or no `"value"`
  key (tolerant reader; a stepless worker's values are outside the
  step-indexed observable's domain — a time-indexed sibling waits for a
  consumer, i.e. the viewer). `request_id` ignored — *dedup* concern only;
  **visibility is upstream** (the fold inherits the scope of the read view
  it's given; enforcement composes at the backend per design §6's
  filtering-not-enforcement caveat, and a pure cache-free fold is exactly what
  makes that composition leak-proof).
- **Determinism:** inner dicts sorted by step.
- **Factoring (internal):** `value_series = register-fold ∘ _value_points`,
  where `_value_points(channel)` is a lazy decode generator
  (envelope → `(name, step, value)` + the skip rules). Kept **private**: the
  designated escape hatch if a custom-fold consumer ever appears (promotion is
  compatible); until then the bring-your-own-fold seam is the substrate itself
  (`read` + a loop). No fold parameter — a parameterized fold is `reduce` in a
  trench coat (fails Independence), can't push filters into the backend the
  way data arguments can, and dissolves the observable's value as a
  *coordination point* (consumers' "loss series" agree by construction).

### `handle_pid` — the handle grammar owns its parse (audit F8)

`vocabulary/handle.py`, beside `resolve()`: `local://host/pid` → `int` pid;
non-`local` scheme or unparseable → `None`. `resolve()` refactors through it —
**one parse site**, so the deferred `?start=` pid-reuse disambiguator
(conventions-hygiene F9) lands in one function instead of breaking every
consumer's `rsplit`.

## Non-goals

- **No caching, no visibility logic, no fold parameters** — pure folds over
  the given read view; amortization is the Watcher's or a caller-owned cursor
  (§12.5); enforcement composes upstream.
- No time-indexed series (waits for the viewer thread).
- No `Episode` view type; no episode markers (run-episodes Decision 1 stands).
- No `liveness.py` compatibility shim (no users yet; clean move).

## Deliverables

- `runstate/observables.py` (absorbs `liveness.py`; module docstring carries
  the observe-vs-watch contrast + the not-Rx disarm); `handle_pid` in
  `vocabulary/handle.py`; package-root exports; `memoizer._progress` →
  adapter over public `progress`; import updates (`worker`, `watcher`,
  `sweep`, `memoizer`, `launcher`, `__init__`, tests).
- Tests: `tests/test_liveness.py` → `tests/test_observables.py` (move +
  extend); `handle_pid` cases in `tests/test_handle.py`.
- Docs: CLAUDE.md architecture map; design §9 gains the stateless-observable
  sentence (rev 7); trackers (audit F5–F8, synergy map Cluster 3, index) →
  shipped; mycooc sweep checklist flips its pending section to ready.

## Tests (TDD targets; observables tests parametrized over all backends)

- `latest_episode`: empty → None; one started → that envelope; started…stopped
  → still that envelope (ended ≠ absent); started…stopped…started → the second.
- `progress`: heartbeat-only / stopped-only / both (max wins) / neither →
  None; null-step heartbeat contributes nothing.
- `value_series`: grouping + step-sorted; same-(name, step) duplicate →
  last-wins by seq; rewind rewrite → as-resumed values win; null-step /
  missing-name / missing-value skipped; empty → {}; foreign body doesn't raise.
- `handle_pid`: `local://h/123` → 123; garbage / non-local scheme → None;
  `resolve` still resolves through it (existing tests stay green).
- Moved liveness tests stay green unchanged (the move is behavior-preserving).
