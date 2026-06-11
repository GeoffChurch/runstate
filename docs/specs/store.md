# Spec: the Store (dissolved — the relational layer as recipes over the existing basis)

**Status:** DRAFT 2026-06-11, adversarial round folded (7 findings),
rubric round folded (8 findings — headline: the gate gets a public home
by the project's own F7 doctrine, so "zero new surface" is amended to
**one six-line helper + one bug fix**, stated honestly). Deliberation
trail: `docs/backlog/store-deliberation.md` (7-agent panel). Consistency
round folded (13 fold-back targets added; the decision record's Q4 gate
paragraph corrected to this spec's shape). The dissolution pins pending.
This spec records the month's fourth and largest dissolution (after the
pure pin, the waker flap policy, and the function producer —
`service-worker.md`, `time-lease-boundary.md`, `derived-runs.md`): the
**Store** — the headline Layer-4 component since 2026-05 ("Store
Protocol + backends; the structure a content-addressed `run_id`
discards, so nothing else can supply it") — ships as **documented
recipes, one liveness-handle helper, and pin tests**. The relational
layer was already in the basis.

## The finding

The May framing asked "where does relational truth live?" and offered a
component. The question dissolves per fact, on the two axes that
actually discriminate: **re-verifiable at read?** × **must outlive the
run's channel?** Each relational fact lands on an existing affordance:

| fact | home | mechanism |
| --- | --- | --- |
| rid → location | the channel's **address** | content-addressed placement (Recipe 1); nothing stored, nothing stale. Derived runs are **parent-scoped**: location is a function of (parent home, arid) — see Recipe 4 |
| the dedup gate | channel **placement** + the shipped birth-CAS | two demands for one rid converge on one log; the CAS arbitrates (Recipe 2) |
| run ↔ experiment membership | the cell **pointer** (current) + the tracked tabulated overview (archival) | an experiment-as-set is a bundling for overviews; readlink + `summary.csv`/git |
| provenance (analysis-of, …) | a **backward record on the child's own log**, at birth | the git-parents shape (Recipe 4); forward maps always computed |
| "reused-from" | **dissolves** | under content addressing it is membership multiplicity (one rid, two experiments), not an edge |
| cell → current rid | the pointer itself | readlink; replaces the `.run_id` marker file |
| the config projection | the worker's **resolved config, one record on its own log** | queries are by config fields and a rid cannot be unhashed; worker-side emission covers loose runs |

What a "Store" would have added on top of these is exactly two things,
both rejected with named revival triggers: an *authoritative second
source of truth* (rejected: every store-beside-truth ships a repair verb
eventually — Hive `MSCK`, `nix-store --repair`, `mlflow gc` — and
arbitration between two authorities is strictly worse than rebuilding
one), and a *fast cross-run query surface* (deferred: an index is a pure
cache over the homes above, and at measured costs — a full tree walk of
2k+ cells is ~26 ms warm; a full channel fold ~0.14 ms — nothing needs
it today).

## Invariants (binding on every recipe and every future revision)

- **Verify-at-use, inside the query helper.** The relational layer only
  *locates*; terminality/progress/liveness are read from the candidate's
  channel at decision time. No store, index, or pointer ever caches run
  state — under B′ episodes those are episode-relative and mutable.
- **Failure polarity: false-miss only.** Every relational-layer failure
  (stale pointer, deleted home, missing record) must degrade to
  recompute, never to a false hit.
- **Facts are true-at-append observations** ("derived from R", "resolved
  config was C"), never mutable states ("R lives at P", "R is
  complete").
- **Write at dispatch/birth**, when the preimage is in hand — never at
  completion (the completion-time `.run_id` marker rotted to 3/2052
  coverage; see `../backlog/mycooc-adoption.md`).
- **Any index is a pure cache**: rebuildable, deletable, never
  authoritative, never written from the dispatch path; a rebuild-and-diff
  invariant in CI guards against authority creep (the first convenience
  fact written only to an index silently converts it into an undeclared
  store).

## Recipe 1 — placement: the content-addressed home

Every run lives at **`runs/<rid[:2]>/<rid>/`** under a caller-owned root
— channel (`{rid}.db`) and artifacts together. An experiment cell is a
**thin directory**: a pointer to the home (`run -> ../../runs/ab/abc…`)
plus cell-local policy files (`.skip`, `.failure`). **Cell ≠ run is the
load-bearing distinction** — their conflation was the
membership-as-placement gap itself. A cell is *not* a bare symlink: an
operator's `touch cell/.skip` must not skip the run for every claiming
experiment.

What this dissolves: the reuse scan and symlink machinery (deleted, not
optimized — see Recipe 2), the `.run_id`/`.config_hash` marker files
(the filename and the pointer carry the fact), the live custody bug
(today a reused run's physical db sits under the *first* experiment's
tree, so `rm -rf E1` destroys a run E2 depends on), and the
foreign-extend wart (every driver bakes `runs/<rid>`, never its own cell
path). Stale bindings become checkable: pointer rid ≠ freshly computed
rid.

Costs, accepted eyes-open: the cell/run split ripples through every
reader (mechanical; POSIX symlink traversal keeps file I/O transparent;
known leaks: `tar`/`rsync` need dereferencing, `du` under-reports,
`os.walk` needs `followlinks`); kernel false-hits get *quieter* (silent
convergence on one run instead of visible duplicates — the
own-your-`run_id`-partition burden, rate unchanged, detection harder);
loose runs (no rid computed) stay outside the namespace.

Library scope: **zero for placement itself** — it is policy over
`open_channel(run_id, root)` (pure path construction,
`channel/__init__.py:47`; nothing in the library enumerates
directories, so nesting is library-invisible). Mechanics for the wiring
plan: the dispatcher `mkdir`s the home before opening the channel
(sqlite does not create parent dirs), and the reference launchers take
`root=` at construction, so per-rid homes mean per-rid launcher (or
producer-side `open_channel`) construction — which breaks the
one-root-many-runs helpers as shipped: `sweep(variants, launcher)` and
the lazy-launch activator-table recipe both hold ONE launcher over N
rids and need a per-rid wrapper loop (named here so the wiring plan
prices them; an `open_channel` layout hook is the revival trigger for
library surface if a second consumer hits it). An unclaimed serendipity
the same mechanics buy: `RUNSTATE_CHANNEL_ROOT` *is* the run's home, so
a spawned worker derives its artifact/checkpoint dir from env —
**checkpoint custody falls out of placement for free**. The shard
(`rid[:2]`) is part of the recipe so independent tools (a future
viewer) can construct paths — for **first-class runs**; derived runs
are parent-scoped (Recipe 4). Shard rationale, since it is a forever
cross-tool contract: flat is adequate at today's 2k cells, degrades as
directory listing at 100x; two hex chars is git's object-store
precedent — the least-arbitrary sharded choice. The pointer is a
**relative** symlink (root-relocatable — the right default) at the
cell's actual depth; copying an example verbatim at the wrong depth is
the first mistake a second consumer will make, so compute it
(`os.path.relpath(home, cell)`), never hand-write it.

## Recipe 2 — demand: dispatch is `ensure`, and the producer gates with a foreign-episode handle

Dispatching a cell is `ensure(producer, name, until=target)` against the
rid's one home. The cases fall out of shipped semantics:

- **Satisfied / `completed`** → a pure log read (cache hit;
  `memoizer.py:202-205`). `ensure` never extends `completed` — under the
  B′ stop-signaling model (`specs/completed-opt-in.md`,
  `specs/preempted-vs-completed.md`) `completed` means *done-done* and
  `preempted` is the extendable bucket, so a converged run demanded
  further returns its history unextended, correctly.
- **Partial (`preempted`)** → extend. The terminal-vs-partial reuse
  distinction evaporates: "reuse" of a half-trained run *is* `ensure`
  driving it further.
- **Concurrent demand** → the worker birth-CAS (`send(...,
  expected_seq=…)`, one critical section across processes —
  `channel/sqlite.py:85-117`) arbitrates; the latecomer's `ensure`
  poll-waits on the winner's log (no attach occurs — it is a wait, and
  the satisfied window is then a pure read).

**The producer's `extend` MUST gate on the live episode, and the gate
returns a foreign-episode handle — not `None`, and never an
unconditional launch.** The handle: `is_alive()` re-reads
`live_episode(channel) is not None`; `wait()` is a no-op. Because this
is one-right-shape correctness machinery (not workload policy), it gets
a **public home — `foreign_episode(channel)`, exported beside
`launch_producer`** — by the F7 doctrine (two private copies of one
boundary rule is the failure class `latest_episode` was created to
prevent — the mycooc-audit F7 finding, `specs/observables.md`); the gate
composes as `relaunch_if_needed(...) or foreign_episode(channel)`. This revises the producer-seam contract
(extend returns *a liveness handle for the work that will satisfy the
demand* — own spawn or foreign episode; `None` leaves the contract),
folded into `specs/memoizer.md` Decision 5 — until that fold, the two
specs would actively disagree about the seam. Why this exact shape —
the three failure modes it navigates:

1. **Ungated** (launch unconditionally): `ensure`'s outer loop
   (`memoizer.py:207-233`) spawns a fresh CAS-loser per ensure
   iteration while a foreign winner serves, and the no-progress guard
   (`memoizer.py:227`) **raises spuriously while the winner is mid-load**
   (claimed, zero steps).
2. **None-gated** (return `None` when live): the inner wait's only exits
   are satisfaction or a terminal *record* (`memoizer.py:210-217`) — a
   winner that crashes **recordless** (SIGKILLed runner, never reaped:
   no `stopped`, no `terminated`) leaves the latecomer polling
   **forever**. `ensure` has no hang timeout by design, and the
   consumer's wall-clock budget lives inside `extend`, which a gated
   latecomer never enters. (The library's own `launch_producer` gate
   currently has this shape — `memoizer.py:57-59` → `relaunch_if_needed`
   — and inherits the hang; fixing it to the foreign-handle shape is a
   deliverable below: a bug fix to an existing helper, not new surface.)
3. **Foreign-handle-gated**: liveness is re-verified every poll (the
   verify-at-use invariant, restored); a recordless winner crash flips
   `is_alive()` false → the wait breaks and the next extend **re-drives**
   (`relaunch_if_needed` sees no live episode and launches the recovery
   episode — the lazy-launch re-wake posture). Implementation finding,
   folded back from TDD: the no-progress guard is **own-spawn-scoped** —
   `ensure` recognizes foreign handles and exempts them, because a
   foreign episode ending without progress (preempted below target, or
   recordless death) is no evidence a relaunch would spin: *we never
   launched*. The guard remains for own spawns that burn without
   progress. (The scoping is also what preserves the pre-existing
   re-drive semantics for a foreign episode that stops preempted below
   target.)

**Gate scope (single-host honesty — the lazy-launch twin paragraph).**
The handle rides `live_episode`, whose conservatism is asymmetric across
its two consumers here: an *unresolvable* handle (another host's
episode, on the shared-FS multi-host layout placement invites) reads
live — which is exactly right for GC (Recipe 3 refuses to collect what
it cannot probe) but means the gate's wait is **unbounded for a crashed
foreign-host winner** (false-keep vs unbounded-wait: polarity holds in
both, costs differ). A second, distinct caveat: local pid-reuse can keep
`is_alive()` true spuriously (rare). Both resolve at the
staleness-based liveness tier when it lands (backlog: "Cross-host
liveness for the claim gate"); until then, cross-host demand convergence
inherits lazy-launch's single-host scope.

**Scope of the arbitration claim.** The CAS makes the *claim* exclusive;
it does not make the loser inert — that is a worker obligation. The
guarantee holds iff (a) workers are **claim-honoring**: the loser must
not act (the reference `Worker` drivers no-op on `_lost`; a surgical
adoption like mycooc's `_run_body` — which today trains regardless and
only gates the terminal emit — must early-exit when `not w.claimed`:
a consumer rider below), and (b) launchers are **reap-disciplined**:
`ThreadLauncher` writes `terminated{exit_code: 0}` unconditionally for a
loser, which the launcher liveness tier reads as a **completed terminal
while the winner is live** — an ungated latecomer over `ThreadLauncher`
returns a truncated series as a *false cache hit*, violating the
polarity invariant. Multi-demand over `ThreadLauncher` is
forbidden-by-doc (consistent with `specs/lazy-launch.md`'s
single-spawner scoping); the pins use the foreign-handle gate, which
never consults the loser's terminal.

**The claim-window residue, and its consumer obligation.** Two
dispatchers inside the post-launch/pre-claim window cost one wasted
spawn (producer-dependent: a full process start for subprocess
producers), and the spurious no-progress raise survives only there.
"The retry succeeds" needs an actor: the consumer catches the
no-progress `RuntimeError`; if `live_episode(channel) is not None` it is
the claim-window collision — re-enter `ensure` (bounded), else surface.
A *slow* winner is a legitimately unbounded wait (`ensure` has no hang
timeout); consumers that need a wall-clock budget on waiting wrap the
`ensure` call, not the producer.

## Recipe 3 — GC: mark-and-sweep over the heap

`rm -rf <experiment>` stops being how space is reclaimed (it deletes
pointers, and under the old layout it was the custody bug). Collection
is a sweep: **experiment pointers are the roots, `runs/` is the heap**.
Discipline, in order:

- **Pointer-before-ensure at dispatch**: the cell's pointer is created
  *before* the first `ensure` on the home, so a fresh dispatch is never
  unrooted (a crash between pointer and `ensure` leaves a dangling
  pointer — benign: re-dispatch re-ensures). This closes the
  mark/collect TOCTOU for new demand. The general obligation, stated
  once: **persistence requires a root; `ensure` alone rents.** A run
  ensured with no pointer (ad-hoc exploration, any non-experiment
  consumer) is collectible at the next sweep regardless of age —
  polarity-sound, but consumers who want keeping must root (a pointer
  anywhere in the root set, or a sweep-exempt root for loose work).
  Corollary: re-binding a cell to a new rid **unroots the old home** —
  the old binding's audit trail and its object go together (see the
  revival trigger).
- **Sweep is an offline operation**: run it when no dispatcher is active
  on the root set. Collection under an in-flight reader is not
  false-miss-benign — unlinking a sqlite file under a polling `ensure`
  leaves it watching a frozen inode. A grace window (skip homes younger
  than T; re-verify pointers after marking) belts-and-suspenders the
  same race.
- **Collectible** iff: no pointer references the home ∧
  `live_episode(channel) is None` ∧ no *nested* home (Recipe 4) is
  pointed-to or live — nested children pin the parent.
- **Selective prune is the default**: delete the fat artifacts
  (checkpoints, tensors), keep the thin channel and `analysis/`. An
  analysis of a pruned run is the **only surviving record of its
  properties** — its read-set is gone, so it can never be recomputed.
  Whole-home deletion is an explicit destructive choice that forfeits
  the nested analyses with it.

`live_episode`'s conservatism helps here: an unresolvable (foreign-host)
handle reads live, so the sweep refuses to collect what it cannot
probe.

## Recipe 4 — provenance: the child records its parent at birth

A derived run appends one record onto **its own log** at creation, while
the preimage is in hand, as a **value-plane register**: `topic="value"`,
`name="analyzed"` (the consumer owns the name), body `{"value":
{"analyzed": <rid>, …}, "step": null, "t": …}` — the wrapper the
`value-v0.2` schema pins, with `step` explicitly present-but-nullable, so
the record is wire-conformant from day one. The third instance of an
established pattern (mycooc's status register; the resolved-config
record): a *register*, read by `latest`, invisible to every shipped fold
(`_value_points` skips `step=null`; `live_demand` skips request_id-less
records; the worker drain is topic-scoped). Two spellings are
**rejected**: a *bare* edge dict under `topic="value"`
(wire-nonconformant — the wrapper is `additionalProperties: false`), and
an **app-minted topic** (e.g. `topic="provenance"`): the envelope
schema's topic-openness is wire mechanics, not namespace governance —
design §4 makes the split load-bearing ("`topic` closed/protocol-owned,
`name` open/app-owned"; §13 rejects topic-carrying-user-identifiers
outright), and squatting a topic now would manufacture a collision with
this very record's own promotion trigger below. Known residue, loud not
silent: `history()` on the register's name raises (`step=null` is not a
trajectory) — nothing calls it. Backward edges only; every forward map
(children-of, experiments-of) is computed, never stored — the git
parents-vs-branches split.

**Derived addressing is parent-scoped.** Derived homes nest —
`runs/<R[:2]>/<R>/<derived-subdir>/<arid>.db`, the decided custody policy and
the same placement-as-fact move as Recipe 1 (the subdir name is
consumer-chosen; mycooc uses `analysis/`) — so a derived location is a
function of **(parent home, arid)**, not of arid alone (the identity
embeds the parent only as a hash: verifiable, not readable). Lookups
that hold a bare arid go through the index (Recipe 5) or the parent.

The record is not redundant with nested placement, because the two
encode different structures: **custody is a tree (one home per run);
provenance is a DAG** (the git-parents shape is multi-parent) — they
merely coincide at n=1, which is why placement *looks* like it already
carries the edge. The postgres-backend future (where channels stop
being files and placement-as-edge dies outright) corroborates rather
than grounds the choice. Named residue: a genuine **multi-parent
derived run** (joint compute over two parents — no consumer today;
`--compare` deliberately dissolved into two singles) has identity
(composes) and a record (DAG-ready) but no single nested home — the
rule when one arrives: a **designated parent** hosts the home; the
record carries all parents. Not a convention: nothing in the library
routes on it (the CLAUDE.md helper test fails, correctly). **Promotion
trigger:** the Cluster-4 viewer protocol needing cross-workload
edge-walking — at that point the *protocol* mints the vocabulary (a
convention with a schema and a version; never the app pre-squatting the
topic axis), not before.

## Recipe 5 — the index (documented, dormant)

When a consumer outgrows direct reads, the index is a **consumer-side
fold**, pure cache: sources are the `runs/` listing (filenames are
rids), experiment-tree readlinks (membership), birth records
(provenance), resolved-config records (the projection), and tracked
`summary.csv`s (history — the only source for experiments whose outputs
were deleted; roughly a third of mycooc's tracked experiments survive
*only* there). Incremental refresh: channel-sourced facts ride the
substrate's caller-owned cursor contract (the log is its own
change-feed); placement and membership facts (the `runs/` listing,
readlinks) have no change-feed and are rescanned — at ~26 ms warm, the
rescan *is* the refresh. Every hit is verified against the candidate's
channel (Invariant 1); CI holds rebuild-and-diff empty.

**Dormant-with-trigger, even consumer-side**: day-one queries under
Recipe 1 are a path stat (rid → exists), a readlink walk (membership),
and a record read (provenance). Build it when a dashboard polls
cross-run queries or the warm walk stops being free at scale. (Distinct
from the *other* dormant "index" — `backlog/memoizer-index-algebra.md`
is the emission-filter algebra over one run's demand, unrelated to this
cross-run relational cache; two dormant-with-trigger entries now share
the word.)

## The consumer decisions (mycooc-side, recorded here for the wiring plan)

- **Runner-as-worker.** The experiment runner becomes an actual worker
  on its own channel: episode claim per invocation via the birth-CAS (a
  concurrent second invocation loses cleanly instead of racing a
  pidfile), heartbeats while dispatching, drains `control.stop`, exits
  `preempted` (more sweeps later = next episode; invocations-as-episodes
  is exactly B′) or `completed` (grid done). Deletes the hand-rolled
  plane: `.pid` + alive-checks (stale-unlink race), the `.status`
  tmp+rename register, the `--stop` SIGTERM ladder. **"Experiment" never
  enters runstate vocabulary** — this is a run whose worker is a
  dispatcher; the experiment's channel carries operational telemetry,
  never relational facts.
- **The claim guard** (Recipe 2's obligation): `_run_body` early-exits
  when `not w.claimed` — today it trains regardless of the claim and
  only gates the terminal emit, which under a shared home means a
  claim-window loser trains anyway: duplicate compute, two trainers on
  one home's checkpoint files, interleaved foreign `value` emissions
  (same-step float divergence makes `history` raise).
- **Membership is pointers-only** + two riders: `summary.csv` gains a
  `rid` column (formalizing the archival role it already plays), and the
  worker emits its resolved config as an ordinary record on its own
  channel (the one config record; the orchestrator's merged config is
  never persisted as a fact). The pointer and the CSV are not two
  encodings of one concern — they split along a real axis (mutable
  current binding vs derived archival view, whose *git history* is the
  archival order) — with one rule to state: **authority transfers at
  deletion.** For experiments whose outputs are gone, the tracked CSV is
  the *sole* surviving source; this coexists with "any index is a pure
  cache" only because verify-at-use keeps it off the dispatch path (a
  dead row → channel gone → false-miss). **Revival trigger for member
  records, written down:** a real consumer needing queryable membership
  *order*, in-flight rosters, per-cell records beyond what the tabulated
  CSV serves, or **binding history** finer than git-commit granularity
  (a cell re-bound between commits overwrites the pointer and the
  regenerated CSV row, and unroots the old home — commit-sampled audit
  is the accepted resolution today) — none exists.
- **Inventory of behaviors whose homes move** (named here so the wiring
  plan prices them): the per-dispatch wall-clock budget bounds the
  subprocess inside `extend` and does not bound a latecomer's *wait* —
  bound the `ensure` call if needed; `.failure` stays cell policy while
  failure *detail* is a run-home fact (the winner's cell and the home's
  `main.log` hold it); `reuse_from` validation (the `.config_hash`
  reader) is subsumed by the pointer-rid comparison; dry-run's
  "would be reused" preview becomes a path stat + `progress` read; the
  smoke gate reads progress through the pointer and may be measuring a
  run this invocation didn't launch (same rid ⇒ same config, so foreign
  progress is valid evidence); the no-progress catch + bounded re-enter
  (Recipe 2).

## The root set, and the named residue

The irreducible authoritative kernel is the **root set**: which roots
(`runs/`, the experiments tree, loose-run dirs) a consumer's folds
enumerate — a configured constant today, `SELECT DISTINCT` under a
server backend. Prior art's floor is exactly this (Iceberg/Delta
converged from authoritative-store to facts-in-band + derived views + a
catalog that only enumerates roots).

**The A-residue, recorded not built:** scattered multi-host with no
shared filesystem and no central channel backend — pure
fleet-enumeration queries ("all runs at commit Y, across machines") have
no derived answer in that middle. It shrinks to nothing when the
postgres channel backend lands (the index becomes a materialized view;
an authoritative store would be redundant dual-write tables beside the
same instance).

## Non-goals

- A Store Protocol, FileStore/SqliteStore/PostgresStore backends, or any
  library index component.
- A `relation.*` convention or any new schema (triggers above).
- A substrate enumeration primitive (`list_runs`) — the root set is
  consumer configuration; the design forbids *reader* registries and is
  silent on root listing, which needs no substrate help.
- Artifact transport/sharing (symlinks, checkpoints) — the library
  transports messages, not files.
- Member records on the experiment channel (revival trigger above).
- Any CLI (the per-run `runstate status` idea stays parked in the
  backlog on its own merits, unrelated to this spec).

## The dissolution pins (tests; if pin 1 or 2a needs new library surface, this spec is refuted)

The pin producers gate via the public `foreign_episode` helper. The
refutation predicate is counterfactual and honest about the helper: pins
1 and 2a must be *passable* on shipped machinery alone (a test-local
gate would suffice for them); the helper exists by the F7 doctrine for
the gate's one-right-shape, not to make any pin pass.

1. **Reuse-as-extend across drivers:** producer A (`ensure(...,
   until={"step": 3})`) computes steps 0–2 and stops `preempted`;
   producer B — a *different* producer object, same rid, same root (two
   "experiments") — `ensure(..., until={"step": 8})` **extends the same
   log**: exactly one `{rid}.db` under the root, two `lifecycle.started`
   episodes, the full series 0–7 returned.
2. **The latecomer:** (a) while A's episode is live, B's gated `ensure`
   poll-waits — zero launches by B while the episode lives — and returns
   the identical satisfied history after the winner delivers (the gate
   handle is built test-locally here, proving the counterfactual: pin 2a
   needs shipped machinery only); (b) **the crash case**: the foreign
   winner is replaced mid-wait by a claim that died recordless (a
   `started` whose handle resolves dead, no `stopped`) — B's `ensure`
   **recovers**: the wait breaks, the next extend launches the recovery
   episode, the series completes. A None-gate producer polls forever
   here; a hang-guard `sleep` converts the hang into a loud failure.
3. **`foreign_episode` + the `launch_producer` hang fix (library,
   TDD):** the helper exported beside `launch_producer` (unit-pinned:
   `is_alive()` tracks `live_episode` call-by-call; `wait()` no-op); the
   default producer's gate becomes `relaunch_if_needed(...) or
   foreign_episode(channel)` instead of returning `None`
   (`memoizer.py`), and `ensure`'s no-progress guard gains the own-spawn
   scope. Red = pin 2b against `launch_producer` trips the hang guard;
   green = recovery. One small helper (the F7 doctrine) plus a bug fix;
   pins 1–2a do not depend on either.

## Deliverables / fold-backs

**Code (TDD):** the pins in `tests/test_memoizer.py` (beside the
derived-runs pin) + `foreign_episode` + the `launch_producer`
foreign-handle fix — which retires `ensure`'s `handle is None` wait
branch (`memoizer.py:211-216`) and migrates the None-seam test fixtures
(`test_ensure_redrives_when_extend_noops_onto_a_live_episode`,
`_FakeProducer`'s `return None`).

**Spec fold-backs (the seam + the contradicted claims):**
- `specs/memoizer.md` — Decision 5 restated (extend returns *a liveness
  handle for the work that will satisfy the demand* — own spawn or
  foreign episode; `None` and the truthy-iff-triggered-production clause
  leave the contract); Decision 3 step 3 (the wait-for-terminal no-op
  branch → handle-liveness in both cases); the `launch_producer`
  paragraph + exports; a correction note (the seam's *attribute* shape
  held; the *return contract* is revised here).
- `specs/derived-runs.md` — the `.run_id`-marker-first locator →
  superseded by the cell pointer; "single-driver-per-rid / don't
  parallelize demand" → restated per Recipe 2 (gated concurrency is the
  designed case; the spurious raise survives only in the claim window,
  with its consumer obligation); the analysis-channel paths gain the
  content-addressed home; the triangulation note gains the revised
  seam's behavioral constraint (both shipped mycooc producers violate it
  today — wiring-plan items).
- `specs/stop-discharge.md` A7 — "eventually the Store's fact" → the
  cell-local `.skip` policy file / caller relaunch policy.
- `specs/lazy-launch.md` — one line at the activator recipe: per-rid
  homes need a per-rid wrapper loop.
- `specs/run-id-recipe.md` — gains "identity locates" beside "identity
  composes"; note `sweep`'s one-root assumption likewise.

**Design + backlog fold-backs:**
- `design-v0.2.md` — :294 Layer-4 paragraph rewritten to the
  dissolution; §12.9 (:273) annotated (home-level GC = Recipe 3,
  consumer-side; in-log retention stays full); the §14 open-items list
  refreshed (lazy-launch closed; GC partially settled).
- `backlog/index.md` — §Layer-4 rewritten; the Next-pickups Store bullet
  → dissolved, pointing here; "depends on Store landing first" /
  "post-relational-layer" lines re-keyed to root-set + Recipe-5;
  the §12.9 mirror line; the runstate-mlflow exporter line re-keyed;
  Start-here gains this spec + the deliberation record.
- `backlog/synergy-map.md` — Cluster 2 re-stated (the join key stands;
  the component dissolves); the Cluster-1 "Next" line; the Cluster-4
  freeze + sequencing lines re-keyed (the unfreeze trigger doubles as
  Recipe 4's promotion trigger).
- `backlog/mycooc-adoption.md` — upstream item 1 →
  resolved-by-dissolution; the "one big deferred piece left" banner and
  the "queries with no home" section annotated with their recipe homes
  (the facts stand; the framing is settled).
- `backlog/visualization-story.md` — every "Store lands first"
  dependency re-keyed to root-set/Recipe-5 (rides its own pending
  staleness rewrite).
- `backlog/store-deliberation.md` — Q4 annotated: superseded by this
  spec's amended deliverable (one helper + one fix) and the
  foreign-handle gate shape; kept as the decision trail (the
  `design-v0.2-exploration.md` precedent for post-execution records).

**Top-level docs:** CLAUDE.md (scope snapshot → dissolved, this spec;
the "Where to put new ideas" Store line; the architecture map gains the
missing `memoizer.py` line incl. `foreign_episode`; the stale §12
open-items line); README.md:82 ("Next up" — all three clauses stale);
`docs/overview.md` (:349-351 likewise); `docs/README.md` arc-name nit.

**Deliberately separate:** the **mycooc wiring plan** (the cell/run
split migration, runner-as-worker, the claim guard, the producer-gate
fixes, riders, GC script) — the largest consumer migration of the arc;
it gets the June sweeps' gate discipline and its own planning artifact.
