# The layers

A map of runstate's internal structure. Companion to `positioning.md`, which answers the same
question from outside.

**Numbering note.** `backlog/protocol-algebra.md` also uses L1/L2/L3, for *different* things — its
L1 is the free monoid (this doc's layer 0), its L2 the elimination discipline, its L3 the two
observers. This doc uses bare numbers and always cites the other by name ("protocol-algebra's L2").

**This doc is a map, not a decision procedure.** An earlier revision ended with a five-step "where
does a new thing go" test; it is deleted and the reason is recorded at the end, because the reason
is more useful than the test was.

## The stack

| | layer | contents | note |
|---|---|---|---|
| **0** | **Substrate** | `send` / `read` / `latest` / `last_seq`, CAS via `send(expected_seq=)` | the only layer whose guarantees survive an uncooperative writer |
| **1** | **Content** | `value` — `(name, step) → body`. Open, app-owned `name` axis | |
| **2** | **Selection** | the condition algebra: `vocabulary/schedule.py` | **a library, not a protocol** — imports nothing |
| **3** | **Identity** | `lifecycle.started` / `stopped` — the claim, episodes | where *who produced it* enters |
| **4** | **Detection** | `lifecycle.heartbeat`, `launcher.launched` / `terminated` | see the caveat below — the heartbeat is not only detection |
| **5** | **Control** | `control.stop` (linear), `control.subscribe` / `unsubscribe` (affine), `lifecycle.nak` | addressed to a producer ⇒ presupposes layer 3 |
| **6** | **Materialization** | `ensure`, `history` — *"I want this content; produce what is missing"* | composes 2 + 3 |
| **7** | **The declarative graph** | dependencies *between* runs | **not runstate** — see the L7 caveat |

### Known layering violations

The stack is a map of intent; the code has three edges that cross it, and a doc that hid them would
be worse than useless.

| edge | site |
|---|---|
| 4 → 5, read | `launcher.py:388` — `ensure_served` gates a spawn on `live_demand`, the `control.subscribe` fold |
| 4 → 5, **write** | `watcher.py:380` — `Watcher.broadcast` *emits* `Topic.CONTROL_SUBSCRIBE`. Detection writing control |
| 4 → 5, read | `watcher.py:471,488` — `await_consumed` reads `lifecycle.nak` and `Heartbeat.consumed_seq` |

All three run through the `Watcher`, which is the honest signal: it is not a layer member (below).

## Three properties, stated correctly

**Layer 0 is the only layer whose guarantees survive a writer who declines the library.** This is
*not* "there is no enforcement above 0" — there is, and it is load-bearing: payload `__post_init__`
refusals (`Stopped(completed=True, error='x')` raises), the `Worker`'s total refusal to touch the
channel after losing a claim, `emit`'s stepless guard, the verdict folds refusing to guess. All of
it is local and bypassable by one raw `send`, and the JSON schemas are a **conformance test, not a
runtime gate** — nothing under `runstate/` imports `jsonschema`.

**The condition algebra is a library and should stay one.** `vocabulary/schedule.py` imports
*nothing* — not `Channel`, not `Topic`. No records, no obligations, reusable by anything slicing
indexed data. **`history` is not part of it**: it is a *replay* of the algebra that reads
`lifecycle.started` for its epoch and ships in `memoizer.py`. It belongs to layer 6.

**Layer 4 is where evidence about a run arrives from outside its own report — but the heartbeat is
multiplexed across three planes**, and calling it detection is a post-hoc story:

- `t` dates the beacon — detection, the story that holds;
- `step` is the **dense progress axis** — `observables.progress` folds it and `ensure`'s loop
  condition depends on it. A layer-6 dependency;
- `consumed_seq` is the **control-acceptance watermark** — written from `Worker._cursor`, polled by
  `watcher.await_consumed` to answer "has my control request been drained?" A layer-5 dependency.

protocol-algebra records this under *"standing counterexamples against over-formalizing"*: **"the
heartbeat is deliberately *enriched* (`{step, consumed_seq}`), not Unit/terminal."** The `launcher.*`
pair *is* purely "because the self-report can fail to arrive." The heartbeat is not.

## The seam: what couples selection to identity

Layer 2 sits beneath layer 3 by import graph. The coupling is **two mechanisms and one gap** — not,
as an earlier revision claimed, a single epoch lookup.

1. **The epoch anchor.** `memoizer._epoch` reads `lifecycle.started.t`; a time-referencing `history`
   with no `started` **raises**. Measured: same six `value` records, same schedule, `started.t=1000`
   → steps `[3,4,5]`; `started.t=1003` → `[]`.
2. **The boundary eliminator** — the stronger one. `references_time` × `boundary_voided`: an
   **identity record destroys a selection registration**. Measured: a time-referencing subscribe
   survives one `lifecycle.started` and is eliminated by the second; the step-only equivalent
   survives both.
3. **The gap: `count`.** `satisfied()` has *three* coordinates, and `count` is per-`Subscription`,
   so it is episode-local for exactly the reason time is — but `references_time` does not see it, so
   it gets no boundary discharge. Measured over three 3-step episodes: `until={"count":5}` fires
   `[3,3,3]` (budget resets each episode); the time equivalent fires `[3,0,0]`.

So the honest statement is not "time couples information to process" but: **only `step` is intrinsic
to the information. `time_seconds` and `count` are both measured from a registration that can
outlive the episode receiving it — and only one of them is discharged at the boundary.**

## Correspondences

What each layer is an instance of. Useful for orienting someone who knows one of these fields, and
for noticing when a proposal is re-deriving something with a name.

| layer | it is an instance of |
|---|---|
| **0** | a write-ahead log / Kafka partition. The CAS is **optimistic concurrency control**; Herlihy puts compare-and-swap at consensus number ∞, which is why protocol-algebra notes a linearizable CAS is already consensus-complete |
| **1** | a sparse relation — a Datomic datom minus the entity; Prometheus's `(metric, t) → value` but indexed by **step**, not time |
| **2** | a **free term algebra** (protocol-algebra says so, and that it is deliberately *not* a normal form). Shape-wise, stream windowing — Flink/Beam windows, SQL `OVER` |
| **3** | **fencing epochs**, and a lease *without expiry*. Nearest live analogues: a ZooKeeper ephemeral znode; Erlang's registered name surviving a restart — identity outliving the process holding it |
| **4** | **Chandra–Toueg failure detectors** (the ◊P the dead-end doc names). Two independent witnesses ≈ a liveness probe plus a controller reaper |
| **5** | **linear/affine logic** (Girard), with Γ as the literal linear context. `request_id` + `nak` is the **correlation identifier** pattern |
| **6** | `make` / Nix / Bazel — content-addressed build; `ensure` is cache-hit-or-produce |
| **7** | Nix derivations, Airflow/Dagster DAGs |

Cross-cutting, and these are the load-bearing ones:

- **Event sourcing + CQRS.** Append-only records plus folds *is* event sourcing; the fold-vs-query
  duality is CQRS. Folds are catamorphisms over the free monoid of layer 0.
- **Advisory vs mandatory locking (POSIX `flock`).** The sharpest analogy for
  `positioning.md`'s recorded-vs-enforced boundary: **the claim is `flock`** — a record everyone
  *agrees* to check, which stops nobody who declines. It is exactly why evicting a live claim spawns
  a second worker while revoking no authority.
- **CRDT join-semilattices.** protocol-algebra already frames Γ that way for the multi-writer case,
  and `RunResult` is a join into a closed verdict lattice.
- **Fencing tokens (Kleppmann).** "One winner at an instant, no protection afterwards" is the
  textbook problem and fencing is the textbook answer — see `specs/write-authority.md` for why that
  answer is not available here.

## Is subscription more primitive than lifecycle?

Half yes. **Demand-as-algebra is beneath** (layer 2, zero imports, already shipped).
**Demand-as-request is above** (layer 5) and structurally must be: a subscription is *addressed to a
producer*, so publishing one presupposes something to receive it.

Push the information-first view all the way and you land on a content-addressed build system:
`ensure` is `make`, the content-addressed run id is the hash, reuse is a cache hit. Much of that is
absorbed already — `specs/run-id-recipe.md` makes identity content-derived, and
`backlog/store-deliberation.md` records the prior art converging the same way.

## The layer-7 caveat

**runstate does schedule.** `sweep.py` is a sequential scheduler with a `resume` policy that reads
`peek_terminal` and a `stop_on_failure` policy, calling `launcher.launch()` in the loop.
`Watcher.broadcast` is the cross-run barrier ("no Experiment class"). `ensure` is a one-node planner:
read-first, produce-on-miss, re-drive `preempted`, refuse on no progress.

The line runstate does not cross is the **declarative graph** — dependencies *between* runs — not
planning as such. Saying "planning is scheduling and scheduling is not runstate's" is false in this
repo, and `backlog/run-scoped-halt.md` already corrected the same overreach once.

## What the stack does not place

Recorded because the gaps are informative, not because the map should grow to cover them.

- **`RunResult` / the verdict lattice** — joins layer 3 (`stopped`) and layer 4 (`terminated`), then
  adds `PRESUMED_DEAD`, which comes from *neither*: it is the `Watcher` inferring from an **absence**
  of records. A verdict backed by no record fits no row. protocol-algebra's L3 gives it a home
  ("the canonical projection of their join, at the edge"); this stack has no edge.
- **`Watcher`** — spans 3, 4 and 5, and is the only **stateful** thing in the library (cursors, a
  staleness clock). "Stateful observer" is a category the stack lacks, and `observables.py`'s own
  docstring draws the line the stack does not: *needs a cursor or a clock? it's the Watcher's*. All
  three layering violations above are Watcher edges.
- **Channel locators** — `create_channel` / `attach_channel` / `RunNotFound`, the layout, the root
  env var. Addressing and birth-vs-attach are not among layer 0's four operations.
- **`EpisodeHolder` / `EpisodeProbe`** — a fifth and sixth backend operation, deliberately off the
  base ABC. So "layer 0 is four operations" holds only of the *required* tier, and the episode lock
  is a detection concern implemented at layer 0.
- **`store` recipes** — content-addressed placement, dedup, membership, GC grace. Neither layer 6
  nor "not runstate."
- **`Producer`** — a *seam* between layer 6 and a launcher. The stack has no row for seams.

## Why there is no decision procedure here

An earlier revision ended with five steps for placing a new concept. It was tested by running it
over the vocabulary that actually shipped, and it **routed 6 of the 10 reserved topics into the
value plane** — including `lifecycle.stopped`, `launcher.terminated` and `control.unsubscribe`.

One bug caused five of the six: it asked *"does this message carry an obligation?"* where
protocol-algebra's L2 asks it of a **pair** — *"a new convention message must arrive as an intro/elim
**pair** with a designated discharge (multiplicity declared), **or** be a pure value carrying no
obligation."* Eliminators never carry obligations, so a per-message reading silently deletes every
elim half.

It also **blessed the eviction of a live claim** — which passes all five steps and was refuted after
three revisions — because no step asked what a record *causes off-log*. And it admitted a
`control.halt` verb that `backlog/run-scoped-halt.md` rules out by name.

The corrected version would have been protocol-algebra's L2 restated, plus `positioning.md`'s
recorded-vs-enforced caveat, plus the minimality rule that rejected `lifecycle.expired` twice — i.e.
a second, worse copy of three documents that already exist. **The decision rule lives in
protocol-algebra's L2. Go there.** What this map adds is only *where things sit*, and the honest
lesson is that a map does not become a procedure by being drawn carefully.
