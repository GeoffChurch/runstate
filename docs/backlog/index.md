# Backlog

Forward-looking ideas for runstate. One file per idea where it warrants
elaboration; smaller items can live inline here under each section.

This index is a **snapshot of open work, not a changelog**: when an idea is
executed, delete its file or remove its inline entry (the shipped design lives in
`docs/specs/`; git carries the history). Refuted ideas (with diagnosis) move to
`docs/dead_ends/`, parallel to this directory.

## Start here (taking on the upcoming work)

A fresh session should read, in order: **`../../CLAUDE.md`** (orientation + the
shipped-vs-deferred scope snapshot) → **`../design-v0.2.md`** (the converged
design; §12 = open implementation items, §14 = scope) → **this file** (the
discoverable work list). For the v0.3 thread, also read the shipped specs
**`../specs/run-episodes.md`**, **`../specs/run-id-recipe.md`**,
**`../specs/memoizer.md`**, and **`../specs/store.md`** (the relational layer,
dissolved into recipes over the existing basis; decision trail
[store-deliberation](store-deliberation.md)), and the trail
**`../design-v0.3-exploration.md`**.
For the **dependency/synergy view** over this list — the items grouped into
clusters that unlock each other, with a sequencing — see
[synergy-map](synergy-map.md).

## Next pickups

- **The 2026-07 holistic review is CLOSED (stage 7 synthesis, 2026-07-16).** Its
  deliberation ledger (`review-2026-07-agenda.md`, 8 items: 7 shipped, 1 dropped)
  is **pruned per the backlog convention** — git carries the deliberation, the
  shipped rationale lives in `../specs/`, and the one refutation worth keeping is
  [`../dead_ends/window-closed.md`](../dead_ends/window-closed.md). What the review
  *left open* is the two living entries below (third-party-observer;
  mycooc-migration-audit). Its verified closing facts: the launcher-identity
  migration is **moot** (2,531 consumer logs scanned — 1,131 `launcher.launched` +
  1,129 `launcher.terminated`, **every one carrying a `request_id`, zero NULLs**),
  and `wal-liveness-mtime` is **resolved** by observer-clock's `last_activity`
  (both entries deleted on resolution).
- **[third-party-observer](third-party-observer.md)** — [LIVING · opened
  2026-07-14, the review's stage 6 executed adversarially] **the log records what a
  run DID, but not WHEN it did it nor WHAT IT WAS ASKED TO DO** — and neither
  absence is visible to the party that launched the run. The persona that falls into
  both (a viewer/TUI/scheduler attaching to a run it did not start) is new, and it
  is the one the TUI/viz work depends on. Headline: a run dead 21 days reads as
  `Running(beacon_age=9.5e-06)`, because no liveness record carries a clock. Six
  items + the ship order; each graduates to its own spec.
- **[mycooc-migration-audit](mycooc-migration-audit.md)** — the ledger of the
  mycooc migration (runstate's first end-to-end consumer), now essentially
  closed: F1–F3 and F5–F8 shipped across June; the **F9/F10** minors resolved
  2026-07-17 (`await_consumed`'s `request_ids=` push-down; the episode +
  resumable-must-be-`preempted` rules surfaced where consumers read); **F4**
  partially closed (`with`-scoped decider probes + the ownership contract; the
  two ergonomic asks left open on the bounded-growth measurement).
- **[ensure-await-completion](ensure-await-completion.md)** —
  `ensure(await_complete=True)`: gate on the producer's `completed` verdict, not the
  step/time window — for a consumer that depends on a post-terminal off-channel
  artifact (the two-channel-publish race). ~8 lines in `memoizer.py`, **no wire
  change**; consults the verdict plane already wired in. Rejects the contrast case
  (a `{completed:true}` *condition-algebra* term — wrong plane). Surfaced by the
  translation dogfood; **not urgent** (1 repo, 2 instances, 1 already mitigated) —
  promote when a live consumer depends on a post-terminal artifact.
- **[conventions-hygiene](conventions-hygiene.md)** — only the pid `?start=`
  disambiguator (F9) remains, deferred (rationale in the file); the rest of the
  adversarial orthonormal-basis audit of the L2 conventions resolved, and the basis
  audited as largely tight.
- **[release-and-stability-contract](release-and-stability-contract.md)** —
  [PROPOSED 2026-07-16, owner-gated] the release *mechanics* are prepared
  (packaging metadata, `MANIFEST.in`, a tag-gated PyPI trusted-publishing
  workflow, `CHANGELOG.md`) but every *policy* is open: what freezes at first
  release + what SemVer means against per-convention wire versions; the public
  form of the migration doctrine (retain scripts once strangers hold logs); the
  end-of-schema-file-deletion trigger; deprecation policy; the version-naming
  tension (`0.2.0.dev0` vs the docs' "v0.3" vs wire-`v0.4`); and the schemas-
  not-in-the-wheel question. Six decision points, each with a recommendation;
  nothing ruled. Blocks the first `pip install runstate`.

## Filed from the runstate-tui cockpit build (GitHub issues)

The cockpit is [the acceptance test for this ledger](cockpit.md); building it surfaced three
upstream asks. They live as GitHub issues (the **inbox**); each graduates to a design entry here —
with the basis-rubric trail — when taken up. Not urgent (the TUI is early).

- **[#15](https://github.com/GeoffChurch/runstate/issues/15) — `read()` query layer** (`filter=`,
  `before=`/`max_seq=`, windowing): push filter / scrollback / search into the substrate (SQL
  `WHERE`, pg full-text, sqlite FTS5) instead of the O(all-records) client-side scan every consumer
  reimplements. The drill-down's filter/search rest on it, and a future multi-frontend daemon
  exposes it as thin RPC. Bounds are **seq-only, never on `t`** (t is never an ordering key).
- **[#16](https://github.com/GeoffChurch/runstate/issues/16) — change-notification (poll → push)**:
  a `wait_for_change()` / `subscribe(after)` primitive so consumers react to new commits instead of
  per-tick `last_seq()` polling — pg `LISTEN/NOTIFY`, sqlite WAL/inotify. The push upgrade of the
  watermark/delta model; same idea as the channel-postgres deferred "low-latency push" note below.
- **[#17](https://github.com/GeoffChurch/runstate/issues/17) — liveness / `conflicted` as an
  observable**: expose the atomic live/stale/dead/conflicted verdict (corroborated by `resolve()` +
  `launcher.terminated`) as a Watcher method, so a daemon + TUI + Emacs share **one** verdict rather
  than each re-deriving it (the §3.2/§12 "verdicts live upstream" rule). The 2026-07 cockpit
  red-team showed pure-log conflict detection is unreliable — the naive triggers fire on ordinary
  crash+relaunch. Downstream *representation* stays tui-side (`runstate-tui` `liveness-overlay.md`).

## Long-term ambition

- **A Prolog query layer over the log — spec for a probe** —
  [prolog-query-layer](prolog-query-layer.md). The cheapest test of the entry below, and unlike
  another design round **it cannot be refuted by something already in the repo**. Port the folds to
  SWI as tabled predicates and differential-test against the Python folds, which are the oracle;
  harvest the corpus from the existing suite, which `test_schema.py` already does the shape of. The
  showcase is that last-write-wins is *literally* mode-directed `max` over `Seq-Value` — revision-at-
  head lexicographic order — and `peek_terminal` becomes a real lattice join rather than a function
  resembling one. Read-only; the substrate, the claim and liveness are explicitly out, and **every
  refutation in this repo is language-independent**, so none is retired by it. Artifact is a separate
  package.
- **The relation as interface: demand-driven reads** —
  [demand-driven-reads](demand-driven-reads.md). A target rather than a defect. A bandit or Bayesian
  optimiser does not want `start`/`stop`; it wants to query a mostly-unmaterialised relation
  `(config, step, metric) → value` and be handed values as they arrive. Framed as **incremental
  tabling with answer subsumption**: the residual query (what the cache lacks) goes to a handler
  verbatim, so the cache is logically removable. Episodes do **not** dissolve into it — the claim
  becomes the cache's concurrency control, episodes make a fill restartable, and negative caching is
  required or the system livelocks. **Two pieces are buildable now, read-side, no protocol change:**
  parameterising the value fold's lattice (LWW today; subsumptive, full-set and divergence-inspection
  as instances — which un-defers the fork-surface now that a consumer exists), and the four-state
  cell projection (unknown / success / failure / impossible, all already derivable — `COMPLETED` vs
  `PREEMPTED` *is* impossible-vs-unknown on the step axis). Blocker for the rest: residual
  subtraction that preserves query *structure*, which is what #15 would have to become.

- [visualization-story](visualization-story.md) — a **separate project** on top of
  runstate owns the data-plane / viewer protocols (richer event types: Histogram,
  Image, Tensor; viewer-discovery; artifact-storage) + a companion webapp/TUI —
  closing the data-plane gap without bloating runstate's core. These protocols do
  **not** live in runstate; they depend on it. Discovery rides the dissolved
  relational layer (`../specs/store.md`: the root set + pointers + the dormant
  index). Revisit when a viewer audience exists.

## Protocol extensions (control plane)

- **A lifecycle record that speaks for an episode must name it** — [episode-aim](episode-aim.md).
  **Revision 2, attacked.** The launcher and control tiers aim; the lifecycle tier aims only where it
  *answers a request*, so `stopped` and `heartbeat` — the two records that speak for an episode
  without naming it — are exactly the two that get misattributed. `claim_seq`, **well-aimed** (no
  `started` between it and the record). Fixes a forged verdict truncating `ensure`, an unaimed
  heartbeat moving `progress`, and the **claim cascade** — which nothing else on the table reaches,
  because it is driven by a displaced worker's *own honest* dying breath. Aim buys
  **non-transferability, never forgery resistance** (a forger reads the aim in one call — measured),
  so the #39 closure and the co-gate on claim-eviction are both **withdrawn**. Open before building:
  the **startless run**, where aim is undefined and a live consumer depends on it. Heartbeat folds
  need latest-then-verify or they cost 2124×.
- **A halt that survives an episode boundary** — [run-scoped-halt](run-scoped-halt.md). `control.stop`
  is an **episode**-scoped request; a consumer reads it as a **run**-scoped halt, and they diverge at
  the boundary. Measured with no third party: an operator halts a run, an ordinary live worker honours
  the stop, the discharge fires correctly — and the run is claimable again and a new episode claims it.
  The discharge rule is right and must not be reopened. **Open need, no mechanism**: a value-plane
  register recipe was proposed and refuted — it designed the read path and asserted the write path,
  which is the exact inversion that killed `../specs/control-target.md` (measured there: *373 spawns in
  3 seconds*, and *"not last writer sets the goal — the writer with the fastest poll loop wins"*). The
  concept is already named as R6's **durable ceiling**, the one empty cell in the control 2×2, and A7
  already assigned it a home. Design the **write path first**. Also why `claim-eviction.md` would not
  have fixed #39.
- **`lifecycle.stopped` unbundling** — [lifecycle-stopped-unbundling](lifecycle-stopped-unbundling.md).
  The dying breath does five jobs and only ever all five. `claim-eviction.md` unbundles job 1
  (claim release); a second consumer wants job 4 (stop discharge) alone, which that design correctly
  refuses. Two instances is the point to decide between one-eliminator-per-job, one record with an
  explicit job set, or stopping at one. **Resolve the cheap question first**: whether a third party has
  any legitimate business discharging an operator's halt — if not, this closes with a consumer change
  and no protocol change.
- **One log file per writing machine** — [machine-partitioned-logs](machine-partitioned-logs.md).
  The surviving half of log-forking (`../dead_ends/log-forking.md`). Forking cannot *arbitrate*
  writers — a CAS on a single mutable pointer does, which is what the birth claim already is — but it
  can **contain** a wrong arbitration. Partition by writing machine and the fork boundary lands exactly
  where `resolve()` stops answering (same host: probe works, arbitrate; foreign host: probe abstains,
  so share nothing). Closes the blind spot `claim-eviction.md` documents and cannot fix. Open: seq
  identity across partitions (positional pairing breaks — Dolt hit this), which folds are unions and
  what they mean, and whether it fixes the splice or relocates it.

- **completion-reason register recipe — SHIPPED 2026-07-11** as a section in
  `../specs/completed-opt-in.md` ("Recipe: the completion-reason register"): a
  value-plane register carrying *why* a run stopped, blessing the SHAPE only (no
  vocabulary — workload words never enter the protocol) + the two safety rules
  the mycooc adoption's scars taught (episode-scope the read; the terminal owns
  done-ness, the register owns only why — never trust a pre-terminal register for
  done-ness). Defends the closed `outcome` enum from the recurring
  completion-classification bug class (mycooc 1963732, 8ea82e3). `RunResult.reason`
  stays branched-on-by-nobody by design (a refinement of `outcome`, not a why).
- **Cross-host liveness for the claim gate** — elaborated 2026-07-16 into
  [cross-host-claim-gate](cross-host-claim-gate.md) (DESIGN DELIBERATION, NOT
  CONVERGED, owner-gated: the two candidate designs analyzed against the
  invariants each touches, plus the owner-decision list). Summary:
  `live_episode` sits at the probe-only
  rung, so on a foreign host it goes blind and treats an unresolvable handle as live
  *forever*; a crashed foreign episode blocks the waker and the birth-CAS. The
  observe-then-claim / heartbeat-as-claim-detector approach is **refuted**
  (`../dead_ends/failure-detector.md`): claiming on heartbeat-staleness *inference*
  admits a double-live window that permanently poisons reuse, and the CAS it leans
  on is unreliable on the motivating NFS deployment. Sound path: the **claim** gate
  needs a *definitive* cross-host oracle — a connection-oriented backend's lock (the
  Postgres advisory lock now **ships** in channel-postgres, but as a *Watcher* signal; the
  deferred *co-arbiter* wires it into the claim gate) via the `resolve` seam — **plus**
  value-plane robustness
  ([value-plane-divergence-resolution](value-plane-divergence-resolution.md)) so a
  residual double-live can't sticky-poison the log. sqlite-NFS stays conservative
  single-host; the heartbeat ◊P detector remains the floor for *observation* (the
  `Watcher`), not the claim.
- **Watcher boundary-aware re-broadcast** — a time-keyed `broadcast` barrier
  subscription on a run that *resumes* is boundary-voided with no record
  (`../specs/time-lease-boundary.md`); today's steering is "spell barriers
  step-keyed" (design §9). A Watcher that watches `started`s and re-issues its
  broadcast across boundaries would lift the restriction. Small.
- **Time-axis unification** — three time anchorings now coexist by design:
  `history()` replays time atoms run-epoch-anchored; live subscriptions are
  episode-scoped (`time-lease-boundary`); stops re-anchor per episode
  (stop-discharge's note). Each is locally right; if a consumer ever needs them to
  agree, unify deliberately rather than ad hoc.
- **Discharge-by-id (merge-tolerant control folds)** — generalize the stop-discharge
  fold's positional rule ("pending until the *next* `lifecycle.stopped`",
  `../specs/stop-discharge.md`) — now two positional rules (the stop discharge AND
  the subscribe answer fold, design §7) — to explicit causal reference: a `stopped`
  names the `request_id`(s) it discharges. Makes the control fold commutative, so it
  survives multi-writer `control.*` (§12.7–8) and replicated logs, where "next" is
  not well-defined. No-op while every log has a single home — including the shipped
  single-instance Postgres backend (one total order, even under multi-writer `control.*`);
  revisit only with a replicated / multi-home log.
- [launcher-protocol-typing](launcher-protocol-typing.md) — the `Launcher` Protocol's
  `launch` can't be structurally typed (the two reference launchers have disjoint
  `launch` signatures: a callable `target` vs a `cmd`). Split the uniform
  `create_channel`/`attach_channel` from the per-launcher `launch` (helpers take a launch thunk). Interim:
  `launcher: Any` in the four helpers.
- **protocol-async-api** (inline; no file) — wrap the JSON Schema in AsyncAPI for
  a richer spec format (multi-channel, lifecycle events). Defer until the v0.2
  protocol grows enough to justify the layer.
- **Reconfigure command** — typed orchestrator-to-worker command for mid-flight
  hyperparameter changes. Dropped from v0.1 as speculative; add when at least 3
  concrete validated use cases exist.
- **Pause / Resume commands** — for cluster-preemption or "another job needs the GPU"
  scenarios. v0.2 if a real use case emerges; for now Stop + restart externally is
  the recommended pattern. *Prior art (Bluesky RunEngine, researched 2026-07-10):*
  deferred-pause-at-checkpoint independently validates the stop-at-next-safe-point
  level; the four-way post-pause fan-out (resume / stop / abort / halt) is a
  field-tested answer space for "paused, now what"; and **suspenders**
  (auto-suspend/auto-resume) need *non-monotone* band predicates — weigh that
  deliberately before extending the deliberately-monotone condition-algebra.
  (Bluesky's `RE.stop()` bakes `exit_status='success'` — the producer-baked bool
  runstate refuses; our no-`success` stance is the more principled projection.)
- **Snapshot command** — orchestrator asks worker to checkpoint without exiting.
  Workload-specific; users can define their own dict shape via raw Channel. Promote
  to first-class if a common shape emerges.
- **Cleanup / resource-pressure commands** — `Cleanup(level: str)` for "GPU memory
  pressure, please free buffers". Too workload-specific to standardize; leave to
  user-defined dicts.

## Backends

- **channel-postgres — SHIPPED** (`../specs/channel-postgres.md`): the cross-host backend.
  Claim = the uniform shared-log CAS (cross-host single-spawn + control fall out of it);
  liveness = the advisory lock as a Watcher-consumed signal, *not* a claim arbiter. Still
  open (deferred in the spec): low-latency push (LISTEN/NOTIFY — issue #16), cross-host auto-relaunch
  (the rejected co-arbiter — see "Cross-host liveness for the claim gate" above), sharding,
  HA.
- **channel-redis** — a Redis backend; an alternative to Postgres for cross-host scenarios
  (lighter daemon, weaker durability). Dominated by the shipped channel-postgres for the
  same use case; revisit only if the lighter-daemon tradeoff is specifically wanted.

(The substrate ships `MemoryChannel` + `SqliteChannel` + `PostgresChannel`; a *push*-based
backend is the deferred channel-postgres LISTEN/NOTIFY idea. `SubmititLauncher` /
`RayLauncher` / `K8sLauncher` are the ecosystem adapters below.)

## Derived tools

- [cockpit](cockpit.md) — **[MOVED 2026-07-17 → `GeoffChurch/runstate-tui`]** a
  control-plane **TUI** (its own private repo): groups of runs → a status table → act
  (stop). **No plots** — shows only what runstate uniquely knows (verdict, progress,
  freshness, episodes, stops, demand) and does the one thing no tracker can, which clears
  the "not another tracking tool" bar and dodges the data-plane protocol entirely. Its rule
  — **public API only; every gap is a finding** — makes it the review's stage 6 with a
  keyboard, permanently, and the acceptance test for this ledger's items 2–6. `cockpit.md`
  is now the runstate-facing record (the split rationale + the build's predictions: item 3
  **refuted**, item 5 **deferred**, item 4 **shipped** (`../specs/channel-locators.md` — the
  read-only `attach_channel`/`create_channel` split), item 6 maybe-dissolved-by-item-1,
  item 2 the target denominator). Supersedes the deleted `webapp-viewer.md`, and
  absorbed the `cli-status` / `cli-stop` one-liners (the predicted reconciliation:
  they died into the TUI's status table + stop action — a status/stop terminal
  tool is that project's, never this repo's).

## Open implementation items (mirrors design §12)

The *deferred* items from `design-v0.2.md §12` — kept there with their full
reasoning, listed here so they're discoverable as work (cross-ref, not moved):

- **Cursor persistence / crash-replay** (§12.5) — the worker's read cursor is
  in-memory; no restart persistence, so the at-least-once / at-most-once boundary
  (a `value` or `count`-`until` over-firing on replay) is unaddressed. *Measured
  2026-07-10:* the refold this would optimize costs ~2 ms at a 10⁶-envelope log;
  the dominant resume term was the attach-time unfiltered read — fixed same day
  via `last_seq()` + the head-first capped attach (design §4/§12.5; 3.4 s → 1.5 ms).
- **Multi-orchestrator attribution** (§12.7–8) — the drain model already makes
  every orchestrator's commands take effect; what's open is *attribution* (whose
  command), today a `request_id`-prefix stopgap.
- **GC / retention policy** (§12.9) — *in-log* retention is full, no GC (the
  precondition `peek_terminal` / resume rely on); *home-level* collection is
  recipe'd (`../specs/store.md` Recipe 3: pointer-rooted mark-and-sweep,
  selective-prune default). In-log compaction remains future work, elaborated
  2026-07-16 into [in-log-compaction](in-log-compaction.md) (DESIGN DELIBERATION,
  NOT CONVERGED, owner-gated: the heartbeat keep-latest candidate, what it does to
  the contiguous-`seq` contract, why the value plane is never compactable, and the
  owner-decision list).

## Deferred from the exogenous-commit audit (2026-06-20)

- **DELETE-mode busy-retry (J3)** — under `journal_mode=DELETE`, `read`/`latest`
  AND the unconditional-append branch of `send` lack the `SQLITE_BUSY` busy-retry
  the CAS path has, so a writer holding the lock past `busy_timeout` surfaces
  `database is locked` rather than waiting. Only reproduced with a *pathological
  unpaced* worker (a realistic paced worker never starved it, and the conformance
  suite runs clean under DELETE); take it up across read **and** unconditional-write
  if a realistic contention case ever reproduces it.
- **`Topic` placement (V3)** — `Topic` lives in `vocabulary/payloads.py` (a body
  module) though it is routing vocabulary; a `vocabulary/topics.py` would be the
  orthogonal home. Marginal.
- **Wontfix-by-design:** the empty-`RUNSTATE_SQLITE_JOURNAL_MODE` `ValueError`
  (J6 — fail-loud is correct), and the `"control.>"` read-glob staying a bare
  literal rather than a `Topic` member (V5 — a glob is not a wire topic).

## Ecosystem adapters (separate packages)

- **[submitit-launcher](submitit-launcher.md)** — `SubmititLauncher` for
  SLURM/AWS Batch/local. The bring-your-own-launcher **recipe ships now**
  (`examples/submitit/`, verified on `cluster="local"`); the design entry
  collects the decisions to promote it to a first-class adapter (in-repo
  `[submitit]` extra vs separate package; callable-vs-cmd target vs the
  [launcher-protocol-typing](launcher-protocol-typing.md) finding; Terminated
  mapping; the `slurm://jobid` handle + `squeue -j` resolve; the requeue ↔
  `steps(start=)` episode synergy).
- **runstate-ray** — `RayLauncher` for Ray actors.
- **runstate-k8s** — `K8sLauncher` for Kubernetes Jobs.
- **runstate-hydra** — Hydra config + sweep adapter; bridges Hydra multirun into a
  runstate sweep manifest.
- **runstate-mlflow** — exporter that mirrors the relational facts (the
  `../specs/store.md` Recipe-5 index sources: pointers, birth records, config
  records) into MLflow's tracking server.
- **runstate-wandb** — convenience for routing Progress events to wandb alongside
  the runstate Channel.

## Tactical

- **Windows support** — untested, unclaimed. The old rationale ("uses
  `fcntl.flock`") died with the v0.1 file backend — v0.2 holds no file locks
  (sqlite serializes internally; memory uses a threading lock). The actual
  suspects now: the `os.kill(pid, 0)` handle-probe semantics, the signal-based
  `Terminated(killed, signal=-rc)` mapping, and the fork-only concurrency tests.
- **xxhash** — faster file hashing for the reference `run_id()` recipe; stdlib
  SHA-256 is fine until codebase sizes get unwieldy.
- **Schema codegen for other languages** — Rust types via `quicktype` /
  `datamodel-code-generator` analogs. Test that generated types round-trip through
  the schema.
- **Protocol versioning** — v0.2 versions each convention schema on its own timeline
  (version-suffixed `$id`). Still open: v0.1↔v0.2 coexistence semantics on one
  channel, and how a reader negotiates the convention version.
- **`sweep` under placement** — `sweep`'s one-root assumption needs a per-rid
  wrapper under content-addressed placement (`../specs/run-id-recipe.md`).
- **[memoizer-index-algebra](memoizer-index-algebra.md)** — a derived, rebuildable,
  never-authoritative membership index over the value log; **dormant** until a
  consumer needs cross-run enumeration faster than a scan (the named trigger;
  `../specs/store.md` Recipe 5).

## Documentation

- **README recipes** — remaining: the synchronous-yield RPC pattern, and
  multi-orchestrator (a launcher + a separate UI sending stops). The
  reuse/divergence-preempt/killed-redrive recipes shipped.
- [protocol-algebra](protocol-algebra.md) — the principled constructions behind the
  layer interfaces (L1 free-monoid initiality, L2 designated intro/elim discipline +
  discharge folds = the context Γ, L3 observer-join), each yielding a **decision
  rule**, with retrodictions and the rejected-formalisms negative space. **Placement
  partly resolved (2026-07-16):** the reader-facing seed (the three decision rules +
  the intro/elim table) now lives in the implementer's guide "why layer" (§7,
  dated); the **formal** treatment's final home (design appendix vs `overview.md`
  incorporation) is still open.
