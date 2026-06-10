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
- **Tolerant reader:** the substrate admits foreign bodies on any topic, so
  every fold skips records missing its required keys; selection never raises.

## Surface

```python
# moved verbatim (semantics unchanged), live_episode refactored through latest_episode
RunResult; peek_terminal(channel) -> RunResult | None
live_episode(channel) -> str | None          # newly re-exported at package root

# new
latest_episode(channel) -> Envelope | None   # the latest lifecycle.started envelope
progress(channel) -> int | None              # max step the trajectory reached
value_series(channel) -> dict[str, dict[int, Any]]   # {name: {step: value}}

# vocabulary/handle.py (NOT this module — handle grammar, not a log fold)
handle_pid(handle: str) -> int | None
```

All exported from the package root.

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
`live_episode` = `latest_episode` + no-`stopped`-after + handle-resolves;
`peek_terminal`'s `_terminal_unless_followed` stays the generic mirror (it
also serves the launcher pair).

### `progress` — publishing `memoizer._progress`

`max(latest heartbeat.step, latest stopped.final_step)`, **`None` if neither
axis has a value** — the fold `memoizer._progress` already is (mycooc's
`_channel_progress` documents itself as a hand copy of it), with one
public-surface correction: `None` for absence instead of the in-band `-1`
sentinel (the repo's own convention — `peek_terminal`/`latest` return `None`;
the `-1` was private arithmetic convenience, and stays *inside* the memoizer
as a local adaptation). Semantics otherwise byte-identical to the shipped
private fold — the frontier of the two `latest`s, episode-rewind behavior
included; no redesign here.

### `value_series` — the register projection on the (name, step) plane

A `value` event is a *sample* of the worker's current-value function
(`set(name, value)` + `tick(step)`: one value per (name, step); concurrent
subscriptions duplicate samples differing only in `request_id`). The log
therefore determines a partial function **(name, step) → value**, and the fold
that recovers it is §4's **register projection** (`latest`) lifted pointwise:
**last-write-wins by `seq` per (name, step) cell.**

- **Serendipity (episode rewinds resolve themselves):** when ep2 resumes from
  an earlier checkpoint than ep1 reached, the rewritten steps' samples
  last-win and the orphaned branch drops out — the fold returns the
  **as-resumed trajectory** with zero episode-awareness code. The raw events
  stay on the log for forensics; the observable is the canonical projection,
  not the only view.
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

## Tests (TDD targets; observables tests parametrized over both backends)

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
