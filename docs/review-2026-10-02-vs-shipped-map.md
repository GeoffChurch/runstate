# Shipped runstate vs if-built-today: a concern-by-concern map

Neutral map for two reviewers. No verdicts. Built 2026-10-02 against the working tree on branch
`spec/episode-aim` (HEAD `88ba806`).

**Not read, as instructed:** `docs/handoff-if-built-today.md`, `docs/review-*.md`,
`docs/if-built-today-outline.md`, `dawkins.html`. Consumer repos were not consulted. Every consumer
figure below is quoted from a document in this repo, and marked when it can't be checked here.

## 0. Conventions

**Paths.**
- `IBT/x.md:L` is `docs/backlog/if-built-today/x.md`, line L.
- `design §N:L` is `docs/design-v0.2.md`.
- `specs/`, `backlog/` and `dead_ends/` are under `docs/`.
- Code paths are under `runstate/`, for example `worker.py:63-135`.

**Moving-parts counting rule.** The rule is the same for both sides, and the lists matter more than
the totals.
- **R**: wire record kinds or relations that a party must write or read for this concern.
- **F**: fold, reading or derivation rules that a reader must apply.
- **I**: named invariants or obligations that the docs state as rules a writer or reader must hold.
- **X**: mechanisms outside the log: CAS, probes, locks, clocks, solvers, policies, helpers.
- **U** (if-built-today only): something the docs call necessary but leave unspecified or
  delegate. U items are listed and not added to the total, so that a missing mechanism does not read
  as simplicity.
- Machinery shared across concerns is counted once, where it first appears: the CAS (C2), the
  Watcher (C4), and IBT's term index, solver and coverage checker (C8). Later concerns write
  "(C2)" and the like instead of re-counting it.

---

## 1. Scope of each side

**Shipped runstate.** It is "a protocol for **cooperative bidirectional control of a long-running
scientific worker**, plus a reference Python implementation" (design §1:12). It is a per-run,
append-only topic log, with opt-in typed conventions on top: control, lifecycle, launcher and value.
Its one-line claim is that "a run is a durable, first-class identity that outlives the processes
executing it" (`positioning.md:6-14`).

It rests on a stated premise, **one authoritative sequencer per run**, and says outright that "the
causal regime — spacelike writers, no global order — is a different protocol, not a later version
of this one" (design §14:315).

It is implemented and tested:
- about 4.8k lines of code and schemas, and about 397 test functions (about 1020 cases with the
  Postgres DSN set, per `CLAUDE.md`);
- three backends;
- two downstream consumers (mycooc and translation).

Its guarantee boundary is deliberately narrow:
- **Enforced:** atomic appends, a total order, and a single CAS winner.
- **Recorded:** everything else.
- **Never:** start or stop processes, or enforce anything over time.

(`positioning.md:59-97`.)

**if-built-today.** It is design documents only, with nothing implemented. The README is explicit
about scope:
- *"Status: a design in its own right, not a migration plan. It is written standalone — the
  question is whether this is a good design, not whether it beats what exists."* (`IBT/README.md:3-4`)
- *"What it is for. Several agents, spread over hosts, cooperating on work that takes hours and may
  die partway: producing values, asking each other for values, and having to agree about what has
  been produced and what never will be."* (`IBT/README.md:6-8`)
- The regime is Morton's "slow inconsistent regime" (`IBT/README.md:10-15`).
- The data model is "a monotone store of polarised literals" (title, `IBT/README.md:1`).

Layers 0–4 are "workload-free" (`IBT/README.md:39-41`). Runs, metrics and steps appear only in
`5-domain.md`, as "one worked instance" (`IBT/5-domain.md:3`). That file also costs it against
runstate: "A **rewrite, not a refactor**, with consumers on the current API"
(`IBT/5-domain.md:107-109`).

**How the two scopes relate.** Read literally, IBT is a **generalisation in a different regime**,
with runstate's domain as one instance. Its own "honest cost" section prices it as a
**replacement** for runstate. The two documents agree on where the line between them falls:
- Shipped's §14 says the no-global-order regime is a different protocol.
- IBT targets exactly that regime: unordered, lossy broadcast and no central store
  (`IBT/5-domain.md:114-119`; `IBT/open.md:46-53`).

### Shipped capabilities that if-built-today does not attempt

- **Process launching and reaping**: `Launcher`/`LaunchHandle`, `ThreadLauncher`, `LocalLauncher`,
  the handle grammar and `reap()`.
- **The `sweep` scheduler.**
- **The cross-run barrier** (`Watcher.broadcast`).
- **The reference worker loop**: safe points, `steps`/`serve`/`tick`/`retire`.
- **The registration watermark and nak feedback.**
- **The liveness detector cascade.**
- **Cold third-party staleness from dated records.** IBT holds that absolute time is "needed for
  nothing" (`IBT/1-logic.md:303`).
- **A concrete wire encoding, backends and a conformance suite.**
- **The relational recipes** (placement, GC, provenance register).
- **A migration doctrine.**
- **An operator halting a run that someone else relaunches.** IBT lists this as unsolved
  (`IBT/README.md:182-184`).

### if-built-today capabilities that shipped does not attempt

- **Told negative facts over regions** (`¬Q`).
- **Four-valued statuses**, with conflict kept visible as `{t,f}`.
- **Questions with explicit per-variable quantifiers**, including `∀`, e.g. "some config that
  passes on every seed".
- **Per-instance residuals, and settledness proved by a finite cover.**
- **Demand derived by rules** (magic templates).
- **Constraint propagators acting as producers.**
- **Dispute and readjudication without retraction**, plus trust policy. Provenance is designed but
  not built.
- **Speculation.**
- **Summary tiers and the may-lift**, which avoid contextuality.
- **Unforgeable lease withdrawal by hash commitment.**
- **Semantics that tolerate replication with no central store.**
- **Per-metric typed constructors, checked on arrival.**

Both sides leave out the artifact plane (C21) and enforcement (C16).

---

## 2. Summary table

The key to the status words:
- **mechanism**: specified, plus code if shipped.
- **policy only**: rules stated, no mechanism.
- **by reference**: points at the shipped mechanism.
- **delegated**: left to a named outside party.
- **unsolved (stated)**: explicitly listed as unsolved.
- **not addressed**: no mention.

| # | concern | shipped | if-built-today |
|---|---|---|---|
| C1 | Run identity, naming, placement | mechanism + recipes | identity principle; placement not addressed |
| C2 | Claim / single-spawn / CAS | mechanism | by reference (the shipped CAS); claim record unspecified |
| C3 | Episodes, restart, attribution of records | mechanism (implicit, positional) | principle (identity as data); minting unspecified |
| C4 | Liveness and failure detection | mechanism (tiered) | unsolved (stated); principles for dating observations |
| C5 | Completion and negative information | mechanism (`completed` bit, `final_step`, never-fire handlers) | mechanism (told falsity, settledness) |
| C6 | Terminal verdict | mechanism | treated as a report; no vocabulary |
| C7 | Stopping a run | mechanism (stop and discharge) | self-withdrawal designed; operator halt unsolved (stated) |
| C8 | Demand, subscriptions, condition algebra | mechanism | mechanism (questions, residual) |
| C9 | Leases, lazy launch, "still wanted" | mechanism | domain policy (lease records); launching delegated |
| C10 | Request acknowledgement and refusal | mechanism | not addressed beyond reject-on-arrival |
| C11 | Value plane | mechanism (last-write-wins read) | mechanism (typed atoms, no merge) |
| C12 | Memoisation (`ensure`, `history`) | mechanism | mechanism (residual = memo check); guards delegated |
| C13 | Derived runs / relational layer | recipes + one helper | rules derive demand; layout not addressed |
| C14 | Reclamation / GC / retention | in-log full retention; home-level recipe | policy interface + invariants |
| C15 | Time and clocks | mechanism | principles |
| C16 | Write authority, provenance, forgery, dispute | scope statement; provenance deferred | dispute mechanism; provenance unbuilt |
| C17 | Wire format and schema discipline | mechanism (JSON Schema stack) | record grammar; encoding unspecified |
| C18 | Backends, transport, ordering | mechanism (3 backends, one sequencer) | assumptions only (unordered broadcast) |
| C19 | Observer plane: folds, monotonicity, summaries | mechanism | rules + aggregation tiers |
| C20 | Launching and orchestration helpers | mechanism | delegated (scheduling policy) |
| C21 | Artifact plane | out of scope | unsolved (stated) |

---

## 3. The concerns

### C1. Run identity, naming and placement

**Shipped mechanism.**
- A `run_id` is opaque and chosen by the caller. The blessed recipe is a content hash of
  output-determining inputs, with no library function (`specs/run-id-recipe.md:3-42, 116-123`).
  - Extendable runs exclude the step target from the hash (`:62-78`).
  - Derived runs hash their full read-set (`:91-114`; `specs/derived-runs.md:32-83`).
- One `(run_id, root)` maps deterministically to one durable log (`specs/run-episodes.md:13-18`).
- Existence is defined by records: "a run exists iff `last_seq() > 0`". `attach_channel` raises
  `RunNotFound`; `create_channel` births (`specs/channel-locators.md:26-61`;
  `channel/base.py:32-42`).
- Placement recipe:
  - Each run lives at `runs/<rid[:2]>/<rid>/`.
  - An experiment cell is a thin directory holding a pointer plus policy files. "Cell ≠ run is the
    load-bearing distinction" (`specs/store.md:73-126`).
  - Derived runs nest under their parent (`:299-305`).
- On Postgres a run is its rows in one shared table keyed `(run_id, seq)`
  (`specs/channel-postgres.md:45-65`).

**if-built-today mechanism.**
- "A run is a durable identity that outlives its processes" and "Content-addressed identity" are
  listed as forced by the domain (`IBT/5-domain.md:10-14`).
- `R` in `at(R, S, M)` is a content-addressed run id, citing the shipped recipe: "the pattern is
  runstate's, the choice of inputs the user's" (`IBT/5-domain.md:38-39`).
- The key's granularity is "a schema's claim about what determines the value"
  (`IBT/2-polarity.md:186-193`). For nondeterministic runs, key by episode, `at(E,S,M)` with
  `episode(E, r)`, or never vouch (`IBT/5-domain.md:43-50`).
- **Placement: not addressed** as a layout. "No central store. Each agent holds a lagged local copy
  and replicates preferentially what it demands" (`IBT/open.md:46-53`).
- A constraint on placement-based reasoning: "Content-addressed placement *is* a partitioning
  policy … Replicating by address is fine; **concluding absence from ownership is not.**"
  (`IBT/1-logic.md:159-162`).

**Known defects and history (shipped).**
- **Phantom runs and foreign-db mutation** from the old creating `open_channel`
  (`specs/channel-locators.md:14-24`). Fixed by the locator split.
- **Marker rot and the custody bug:**
  - The completion-time `.run_id` marker rotted to 3/2052 coverage (`specs/store.md:64-66`).
  - `rm -rf E1` destroyed a run that E2 depended on (`store.md:84-91`).
- **False hits:**
  - A false-hit run id now converges silently on one home (`store.md:96-98`;
    `run-id-recipe.md:80-89`).
  - The target-independence precondition: "the one place extend can silently corrupt reuse"
    (`specs/run-episodes.md:153-159`).
  - The derived-run aliasing trap `h(R_rid, code)` (`specs/derived-runs.md:46-57`).
  - mycooc's dirty-vs-clean hash bug (`run-id-recipe.md:46-49`).
- **`sweep` and the lazy-launch activator** hold one root and need a per-rid wrapper under
  placement (`store.md:106-113`; `backlog/index.md:382-383`).

**Moving parts.**
- **Shipped, about 8.**
  - F1: the `RunNotFound` existence rule.
  - I5: own your partition; exclude the target; target-independence; hash only settled snapshots;
    cell ≠ run.
  - X2: the two locators; path and shard construction.
- **IBT, about 3.**
  - I3: identity is data; key granularity is a schema choice; don't conclude absence from
    ownership.
  - U: where records persist; replication policy; who mints episode ids (see C3).

**Open questions.**
- Shipped decides "not launched yet" from absence at an address: `RunNotFound` is caught as "launch
  it" (`specs/channel-locators.md:113-116`), and "rid → exists" is a path stat
  (`store.md:341-342`).
- IBT's `IBT/1-logic.md:159-162` and its rule that `∅` cannot be affirmed
  (`IBT/2-polarity.md:356-364`) treat exactly that inference as outside the coordination-free
  class.
- So it is unclear whether IBT would keep an existence test at all, and if so where.

---

### C2. The claim: single-spawn and the CAS

**Shipped mechanism.**
- **The substrate op.** `send(..., expected_seq=S)` appends iff the log's last seq is `S`. Otherwise
  it returns `None`, meaning provably lost. It raises only when the outcome is indeterminate
  (design §4:64; `channel/base.py:48-62`).
- **Per-backend implementation:**
  - **SQLite**: one guarded `INSERT … WHERE (SELECT COALESCE(MAX(seq),0) FROM log) = ?`, with
    SQLITE_BUSY disambiguation (`channel/sqlite.py:207-245`).
  - **Postgres**: `PRIMARY KEY (run_id, seq)`; a `UniqueViolation` returns `None`
    (`channel/postgres.py:91-97, 233-245`).
  - **Memory**: under a lock (`channel/memory.py:50-57`).
- **The worker self-claims** (`worker.py:63-135`):
  - It reads the head and computes folds from topic-filtered reads capped at that head.
  - It pre-checks `live_episode`.
  - It CAS-appends `lifecycle.started{handle, t}`, with `request_id = current_launch_id()`.
  - The loser sets `_lost` and never acts on the channel (`worker.py:108-116, 164, 227, 255, 296,
    347`).
  - Why the claim is the worker's and not the launcher's: `specs/run-episodes.md:53-94`.
- **The death is CAS'd too.** `retire()` takes `expected_seq` only from a read
  (`worker.py:284-327`; `specs/service-worker.md:136-181`).
- **The guarantee's scope:** "at most one claimant at the instant of claiming", not single-writer
  over time (`specs/write-authority.md:10-19, 113-134`; `docs/api.md:147-155`).
- **Pre-checks are optimisations.** `relaunch_if_needed` (`launcher.py:339-365`) avoids a wasted
  spawn; the CAS is the guarantee.
- **On Postgres** the winner then takes an episode advisory lock. It is "a liveness signal, never a
  claim gate" (`worker.py:129-134`; `specs/channel-postgres.md:18-33`).

**if-built-today mechanism.**
- Single-spawn is named "the one irreducibly coordinating requirement" (`IBT/open.md:49-53`).
- "*Run iff no other agent is running this* is mutual exclusion, not a query … a roster is
  needed — priced in open.md" (`IBT/1-logic.md:225-233`).
- **Mechanism by reference only:** "Order is needed in exactly one place, the **claim**, where
  `send(expected_seq=)` is a compare-and-swap" (`IBT/5-domain.md:117-119`).
- `IBT/README.md:203-207` calls `specs/write-authority.md` "unchanged by any of this". It also says
  "the shipped compare-and-swap *is* a unique constraint, `PRIMARY KEY (run_id, seq)`".
- Consequences acknowledged without single-spawn:
  - Two agents may "separately spend six hours computing the same one" (`IBT/1-logic.md:230-233`).
  - Concurrent overlapping demands each produce (`IBT/3-questions.md:182-183`).
- **Unspecified:**
  - the claim record's shape;
  - how an episode or attempt id is minted;
  - where the CAS's shared frontier lives when there is no central store.

**Known defects and history (shipped).**
- **The wasted spawn's "funeral."** A loser's reaped `terminated{0}` forged the run's verdict
  (`specs/run-episodes.md:85-88` amendment; `specs/lazy-launch.md:100-128`;
  `specs/launcher-record-identity.md:12-28`).
- **`stopped()` lacked the loser guard** (`specs/lazy-launch.md:129-135`).
- **Issue #32, single writer over time.** Three refuted mechanisms
  (`specs/write-authority.md:21-111`):
  - promote the advisory lock to a claim arbiter;
  - an epoch-fenced append, refuted five ways, including "the birth CAS cannot be fenced";
  - per-tick displacement detection, refuted seven ways.
  - "Why fencing tokens are not available here" (`:136-179`).
- **Dead ends:**
  - observe-then-claim (`dead_ends/failure-detector.md`);
  - forking the log (`dead_ends/log-forking.md`);
  - per-episode loglets. Measured: with per-segment seq, "both claims win", because "a CAS
    arbitrates only writers who share a frontier" (`dead_ends/per-episode-loglets.md`).
- **SQLite on NFS** can admit two CAS winners, so single-writer is REQUIRED there
  (`channel/sqlite.py` class docstring; `backlog/cross-host-claim-gate.md:50-54`).
- **Cross-host wedge.** An unresolvable crashed foreign claim reads live forever. That blocks
  `ensure`, `ensure_served` and the birth pre-check (`backlog/cross-host-claim-gate.md:12-58`).
  - A consumer built `reclaim_experiment.py`, which forges `lifecycle.stopped`. This was "forced,
    not sloppy" (`:352-371`), and cost about 20 GPU-hours before it existed (`:357-358`).
- **The claim-window spurious no-progress raise** (`specs/store.md:223-232`; now claim-aware,
  `memoizer.py:428-437`).

**Moving parts.**
- **Shipped, about 11.**
  - R1: `lifecycle.started`.
  - F1: the `live_episode` pre-check.
  - I6: the loser never acts; the pre-check comes before the claim ("ORDER IS LOAD-BEARING",
    `worker.py:109-114`); claim before allocating anything; single-writer only at the instant;
    single-writer required on SQLite-NFS; the death CAS's `expected_seq` comes only from a read.
  - X3: the CAS; the death CAS; the optional episode lock.
- **IBT, about 2.**
  - I1: single-spawn is the one coordinated act.
  - X1: the shipped CAS, by reference.
  - U: the claim record; episode minting; where the frontier lives; the roster.

**Open questions.**
- IBT drops the central store (`IBT/open.md:46-53`), yet keeps a CAS (`IBT/5-domain.md:117-119`),
  and a CAS needs a shared frontier (`dead_ends/per-episode-loglets.md`).
- Whether IBT's claim lives on a single-sequencer log like shipped's (design §14:315), or on
  something else, is not stated.

---

### C3. Episodes, restart, and which episode a record belongs to

**Shipped mechanism.**
- **Episodes are implicit:** a `started … stopped` span paired by `seq`, "no new schema field"
  (`specs/run-episodes.md:27-32`).
  - `latest_episode` is the latest `started` (`observables.py:117-130`).
  - `live_episode` is the latest `started`, with no later `stopped`, whose handle does not resolve
    dead (`observables.py:133-165`).
- **"A terminal stands until a new episode claims"** (`observables.py:264-269`; `_episode_stopped`
  `:168-177`).
- **The launcher tier is anchored to the claimed episode by launch id.** The id is carried in
  `request_id` and passed to the worker via `RUNSTATE_LAUNCH_ID`
  (`observables.py:200-241`; `vocabulary/launch.py`; `specs/launcher-record-identity.md:54-95`).
- **Heartbeat, stopped and value records name no episode.** They are attributed by position.
- **Restart is autonomous extension** (`specs/run-episodes.md:96-115`; `worker.py:153-174`):
  - The `run_id` excludes the target.
  - The worker resumes from its own checkpoint via `steps(start=k)`.
  - The checkpoint mechanism is the worker's (`run-episodes.md:128-132`).
- **Cross-episode control is re-derived from seq 0** (design §7:176).
- **The claim is a Markov boundary.** Episode-scoped folds read only the suffix (design §14:317).

**if-built-today mechanism.**
- **"Identity is data, never position.** `heartbeat(episode2, 500)`, not *the heartbeat after the
  second `started`*" (`IBT/README.md:81-88`). The reason given is that under permanence a mis-aimed
  record can't be corrected.
- "Status cycles; values do not … the attempt index goes in the term and the cycle lives in the
  *sequence of attempts*" (`IBT/5-domain.md:19-20`).
- Key values by episode when production is nondeterministic, `at(E,S,M)` with `episode(E, r)`
  (`IBT/5-domain.md:46-48`).
- A warning: a lexicographic *combining* order with `attempt` at the head "fabricates attempt 2"
  (`IBT/4-aggregation.md:17`).
- **Unspecified:**
  - who mints episode or attempt ids, and whether that needs the claim;
  - how a reader finds the *current* episode. "Latest" is an argmax, which IBT classes as a tier-3
    report (`IBT/4-aggregation.md:12, 75`);
  - resume and extend semantics.

**Known defects and history (shipped).** All of these are attribution-by-position failures,
fixed or proposed.
- **Cross-episode stop replay**, where the resume dies at its first step (`specs/stop-discharge.md:18-21`).
- **Leases and count budgets resurrected per episode** (`specs/service-worker.md:59-66`;
  `specs/time-lease-boundary.md:62-85`; `layers.md:80-84`).
- **Late-reap and claim-loser forged verdicts** (`specs/launcher-record-identity.md:12-28`).
- **`progress` read a prior episode's `final_step`**, so "`ensure` return[ed] a series spliced
  across both episodes **as complete**" (`observables.py:470-474`).
- **A post-terminal `emit` landed in the successor's window.** Measured: episode 2 emitted 101.0,
  which was overwritten by episode 1's -999.0 (`worker.py:219-226`).
- **Proposed, not shipped: `backlog/episode-aim.md` (revision 2), the `claim_seq` stamp.**
  - It fixes the claim cascade, the unaimed heartbeat moving the frontier, and the forged verdict
    that truncated `ensure` (`:51-57`).
  - "Aim buys non-transferability, never forgery resistance" (`:20-39`).
  - Its cost is `+181/−24`. The heartbeat fold is 2124× slower without latest-then-verify
    (`:103-118`).
  - The "startless run" case is unanswered (`:121-132`).
- **`value_series` is a convergent merge.** "A caller can receive a series no single execution
  produced" (`observables.py:535-542`).
- **Explicit episode ids were declined** as "a future refinement" (`specs/run-episodes.md:27-32`;
  `specs/observables.md:68-82`).
- **Dead end: per-episode loglets.** Its conclusion is that the defect "is fixed by **correlation,
  not segmentation**".

**Moving parts.**
- **Shipped, about 15.**
  - R3: `started`; `stopped`; the launch-id correlation on launcher records.
  - F6: `latest_episode`, `live_episode`, `_episode_stopped`, `_launcher_terminal`,
    `boundary_voided`, the discharge floor.
  - I5: terminal-stands-until-claim; the Markov boundary; target-independence; checkpoint what you
    did; the single-writer premise behind the heartbeat register (`observables.py:475-479`).
  - X1: ambient launch-id transport (env var or ContextVar).
- **IBT, about 5.**
  - R2: an episode argument in every record; `episode(E, r)`.
  - I3: identity is data; attempt goes in the term; no lexicographic combining.
  - U3: minting; the "current episode" read; resume and extend.

**Open questions.**
- A dense attempt index ("attempt 2") looks like it needs the same claim arbitration as C2. IBT does
  not say.
- Shipped `ensure` relies on take-the-latest to return the "as-resumed" trajectory. Under IBT's
  episode-keyed values, picking "the" trajectory is a report. Which report it is, and whether
  `ensure` can use one, is unspecified.

---

### C4. Liveness and failure detection

**Shipped mechanism.**
- **A layered, opt-in detector** (design §8:193-199; `watcher.py:243-295`):
  1. `lifecycle.stopped`.
  2. `launcher.terminated`.
  3. A handle probe.
     - **3b.** The Postgres episode advisory lock via `EpisodeHolder`/`EpisodeProbe`. It can only
       vote dead past a birth grace, and never vetoes staleness (`watcher.py:85-116`;
       `channel/base.py:101-128`; `specs/channel-postgres.md:146-235`).
  4. Heartbeat staleness.
- **`resolve()` is scoped to the hostname**: True or False here, `None` for a foreign host or
  scheme (`vocabulary/handle.py:47-71`).
- **The claim gate is conservative.** `live_episode` uses records plus `resolve`, and treats
  unresolvable as live. The launcher tier never releases a claim (`observables.py:133-165`).
- **Freshness:** `last_activity` is the max `t` among the latest of five dated topics
  (`observables.py:326-362`).
- **The Watcher prefers witnessed arrivals**, and seeds `last_hb_seq`, `last_step` and
  `last_heartbeat_at` from the record's `t` on a cold attach (`specs/observer-clock.md:153-207`).
- **Rules:**
  - "Staleness … is a LOCAL inference."
  - "Time never arbitrates a claim or a death verdict," with irreversible actions gated on
    record-plane facts (`observer-clock.md:125-149`).
- **Rejected:** a substrate liveness lease (design §13:303).

**if-built-today mechanism.**
- **Explicitly unsolved:** "**Cross-host liveness.** You still need a handle and a probe, and it
  still abstains off-host — and that abstention must not become a stored verdict."
  (`IBT/README.md:177-178`).
- **Probes and clocks belong to the "non-monotone core".** Their observations cross into the
  monotone layer as timestamped literals, "a fact about the past", carrying "who observed, by whose
  clock" (`IBT/1-logic.md:258-293`).
- **Exhaustion is kept separate from falsity.** "Could a third party post this knowing only that
  the process died? For exhaustion yes — a pid probe suffices" (`IBT/2-polarity.md:200-205`).
- "*Liveness* still needs an acknowledgement" (`IBT/1-logic.md:221-223`).
- Duration is a local question, answered by a local *monotonic* clock (`IBT/1-logic.md:301-312`).
- **Its reading of shipped:** "Exhaustion arrives three ways in runstate today"
  (`IBT/5-domain.md:185-189`; checked in §4, claim 8).
- **No heartbeat schema, staleness rule or detector cascade is specified.**

**Known defects and history (shipped).**
- **Dead end: heartbeat staleness as the claim detector.** "The *weakest* detector for the
  *highest-stakes* decision" (`dead_ends/failure-detector.md:27-80`).
- **A dead run read as `Running(beacon_age=9.5e-06)` on a cold attach.** Five mycooc runs dead
  12–21 days were painted live (`observer-clock.md:43-46`). Fixed by dated beacons.
- **WAL file mtime was 306 s stale** on a log 1 s old (`observer-clock.md:49-54`).
- **Zombies read alive to `kill -0`**, so per-cycle `reap()` is mandatory (`specs/lazy-launch.md:81-87`).
- **`resolve()` used to probe the local pid table for any host's handle.** A false dead off-host
  could double-claim (`specs/lazy-launch.md:164-167`).
- **The dead-vs-busy ambiguity** (design §8:197). **Idle-reap** can falsely release the Postgres
  lock (`channel-postgres.md:218-229`).
- **Cross-host claim gate: deliberation, not converged** (`backlog/cross-host-claim-gate.md`).
  - Its field rule: "a heuristic may VETO, never AUTHORISE" (`:373-386`).
  - A death record never revokes a claim whose probe says alive (pinned test cited at `:404-407`).
- **`PRESUMED_DEAD` is backed by no record** (`layers.md:142-145`).

**Moving parts.**
- **Shipped, about 21.**
  - R5: `started{handle}`, `heartbeat{t}`, `stopped`, `launched{handle}`, `terminated`.
  - F5: `live_episode`, the record tiers, the `Watcher.poll` cascade, `last_activity`, the
    heartbeat seed.
  - I6: staleness is local; time never arbitrates an irreversible act; abstain means conservatively
    live; the lock is never a claim gate; veto, never authorise; the timeout must exceed the max gap
    between beacons.
  - X5: `resolve`; `LaunchHandle.is_alive`; the Postgres lock with its grace; the staleness clock;
    `reap`.
- **IBT, about 7.**
  - R1: dated observation literals.
  - I4: exhaustion ≠ falsity; abstention is not a stored verdict; a literal records observer and
    clock; local stamps are monotonic.
  - X2: the probe; the local clock.
  - U: the cascade, the heartbeat, thresholds, cross-host death.

**Open questions.** IBT's "cross-host liveness" exclusion may or may not also cover
**cold-attach** staleness. That is the shipped `observer-clock` problem, which shipped solved with
wall-clock `t` (see C15).

---

### C5. Completion and negative information ("done", "no more")

**Shipped mechanism.**
- **`lifecycle.stopped{completed, error, final_step, t}`**
  (`specs/completed-opt-in.md:23-59`; `vocabulary/payloads.py:112-136`):
  - Its existence means a clean, *resumable* halt.
  - `completed=True` is the worker's sole terminal claim.
  - The default projects to `preempted`.
  - `completed ⟹ error=None` is enforced.
- **`worker_completed`** separates the worker's claim from a launcher's exit 0
  (`observables.py:299-323`).
- **Per-request "this will never fire"**: five causes, five handlers (design §6:153).
  - static `nak unsatisfiable`;
  - dynamic `lifecycle.stopped`;
  - the patience cap;
  - the expiry `control.unsubscribe`;
  - the recordless episode-boundary void.
- **There is no record that asserts absence over a region.** IBT's census says "the deployed
  protocol has no `¬Q`" (`IBT/open.md:71-76`), which matches the schema set in `protocol/`.
- **The "why"** goes in a value-plane register recipe, not in the convention body
  (`completed-opt-in.md:74-101`).

**if-built-today mechanism.**
- **Polarity is one declared relation**, `told(A, P)`, `P ∈ {pos, neg}`. `¬Q` is a told region
  (`IBT/2-polarity.md:5-10, 168-171, 415-478`).
- **"Falsity is told, never inferred."** The posting rule is "Post what you know"
  (`IBT/2-polarity.md:136-193`).
- **Ways to come to know a negative:** a finished producer, a solver, or a closed producer set.
  Exhaustion ≠ falsity (`IBT/2-polarity.md:195-235`).
- **Settledness:**
  - Knowledge-completeness of a set of atoms is affirmable only by a finite cover. Stream
    completeness is exhaustion, which is control (`IBT/2-polarity.md:93-129`).
  - Settledness of a question: `IBT/3-questions.md:95-136`.
- **Per-key complement ("vouching")** is honest only when the key determines the value
  (`IBT/2-polarity.md:178-193`).
- **Worked example:** `told(at(r, S, M), neg) :- S > 743, is_metric(M)` settles an unbounded
  question with a finite cover (`IBT/5-domain.md:79-89`). `never` becomes `¬Q` over a singleton
  (`:17-18`).
- **Open.** Exhaustion "needs a shape that is not `¬Q`" (`IBT/open.md:5-11`).

**Known defects and history (shipped).**
- **The completed-by-default footgun** silently truncated `ensure` (`completed-opt-in.md:11-21`).
- **`ensure` could not express "finished before `up_to`"** (`specs/preempted-vs-completed.md:8-22`).
- **A launcher-tier exit 0 read as COMPLETED**, so `ensure` returned empty or truncated series
  (runstate#30) (`preempted-vs-completed.md:47-52`; `observables.py:299-318`). Hence
  `RecordlessExitError` (`memoizer.py:45-63`).
- **The completion-classification bug class in mycooc** (`backlog/index.md:200-208`).
- **Figures from IBT's census, which cannot be checked here:**
  - 411 of 2,743 stops carry no `final_step`.
  - 1,643 values arrive after their stop, all within the closed region (`IBT/open.md:78-83`).

**Moving parts.**
- **Shipped, about 10.**
  - R3: `stopped{completed, error, final_step}`, `nak`, the expiry unsubscribe.
  - F3: the outcome projection, `worker_completed`, `progress`.
  - I4: `completed ⟹ error=None`; resumable chunks stay `preempted`; claim `completed` only when
    intrinsically done; the terminal owns done-ness while the register owns only why.
- **IBT, about 7.**
  - R1: `told(A, neg)` regions.
  - F2: the union status fold; settledness by finite cover.
  - I4: post what you know; falsity is told; exhaustion ≠ falsity; vouch only when the key
    determines the value.
  - The coverage checker is counted in C8.
  - U1: the exhaustion record.

**Open questions.** Shipped's `final_step` acts like an implicit negative tail: no steps beyond
this, in this episode. IBT's `¬Q` makes that explicit and permanent. Under shipped's resume model a
later episode legitimately posts beyond a prior `final_step`. How IBT's tail interacts with resume
is not worked; it depends on the key-by-episode choice in C3.

---

### C6. The terminal verdict

**Shipped mechanism.**
- **`peek_terminal(channel) → RunResult | None`** (`observables.py:244-296`).
  `_verdict_record` picks this episode's `stopped`, or else the claimed launch's `terminated`.
- **The projection:**
  - From `stopped`: error → ERRORED; completed → COMPLETED; otherwise PREEMPTED.
  - From `terminated`: killed → KILLED; exit 0 → COMPLETED; otherwise ERRORED.
- **The output is a closed `Outcome` enum** with five values, plus a verbatim `reason`. There is
  deliberately no `success` bool (`observables.py:71-114`; design §9:240-253).
- **The Watcher adds `PRESUMED_DEAD`**, with reasons `probed_dead`, `heartbeat_stale` and
  `episode_lock_released` (`watcher.py:243-295`).
- **Verdict folds refuse to guess:** they raise `MalformedRecordError` (`observables.py:44-68`).
- **"No 'done forever' concept."** The terminal is the latest episode's (`specs/run-episodes.md:34-45`).
- **Decision rule L3:** join the observers at the edge, never in the data
  (`backlog/protocol-algebra.md` §L3).

**if-built-today mechanism.**
- "**The verdict as a join of two partial observers** — and it is a *report*, which is why it may
  use the narrowing reading that derivation may not" (`IBT/5-domain.md:14-15`).
- **Mapping:** `peek_terminal` needs an emptiness test (`¬∃ started`), "measured to **retract a
  published verdict** when a later claim arrives" (`IBT/5-domain.md:147-148`).
- **A `verdict(Outcome, FinalStep)` relation appears only as an illustration**
  (`IBT/1-logic.md:440-441`; `IBT/3-questions.md:365-366`; dispute example
  `IBT/3-provenance.md:23-24`).
- **Reports never feed demand** (`IBT/1-logic.md:125-131`).
- **No verdict vocabulary, projection or tiering is specified.**

**Known defects and history (shipped).**
- **Forged verdicts** (late reap; a claim loser's clean exit) made `ensure` silently return a
  truncated series (`specs/launcher-record-identity.md:3-28`).
  - Fixed by launcher-v0.3 ids.
  - Old logs were migrated by positional guessing (`:138-151`).
- **Vocabulary changes:**
  - `success` removed.
  - `stopped` → `preempted`.
  - `Stopped.reason` removed (`specs/preempted-vs-completed.md:74-98`; `specs/completed-opt-in.md`).
- **Malformed third-party `stopped` records** raise from `peek_terminal`. This happened in 6 of
  the 11 non-worker discharges (`observables.py:450-453`).
  - Append-only repairability is pinned (`backlog/episode-aim.md:96-101`).
- **Consumer census:** one consumer's 785 `started` records carry zero launch ids, so no launcher
  record can ever speak for its claims (`CLAUDE.md`, "Count before designing for a consumer").
- **Waking a completed run demotes its verdict** (`specs/lazy-launch.md:55-57`).

**Moving parts.**
- **Shipped, about 12.**
  - R2: `stopped`, `terminated`.
  - F5: `_episode_stopped`, `_launcher_terminal`, `_verdict_record`, the projection, the inference
    tiers.
  - I5: terminal-stands-until-claim; refuse to guess; no `success`; the launcher tier is anchored
    to the claim; ask `worker_completed`, not `outcome == COMPLETED`.
- **IBT, about 2.**
  - F1: the verdict is a join, read as a report.
  - I1: reports stay outside derivation.
  - U: vocabulary and projection.

**Open questions.** In shipped, a verdict being voided by the next claim is *designed semantics*
("terminal" means the latest episode ended and is extendable). IBT describes the same behaviour as
a retracted verdict. Whether those are the same thing depends on whether a verdict is about a run or
about an episode. The two documents frame it differently.

---

### C7. Stopping a run

**Shipped mechanism.**
- **`control.stop {from?}`** is one-shot (design §6:117-122; `StopTrigger` in
  `protocol/subscription-v0.2.schema.json`).
- **The worker keeps a pending set** of `(request_id, from, registered_at)`. Its decision is the
  `any`-join of those conditions, a monotone *level* that latches (`worker.py:33-42, 417-439,
  497-513`; `specs/stop-discharge.md:44-71`).
- **Discharge:** the next `lifecycle.stopped`, by seq, discharges *every* pending stop
  (`stop-discharge.md:46-53`). This is implemented in two places:
  - the worker's `_discharge_floor` (`worker.py:84-90, 418-425`);
  - the observer fold `undischarged_stops` (`observables.py:428-460`).
- **A stop sent while the run is down** is honoured exactly once by the next episode (the "blip")
  (`stop-discharge.md:108-132`).
- **A time-keyed `from` re-anchors per episode**, deliberately (`stop-discharge.md:233-242`;
  `time-lease-boundary.md:136-142`).
- **Termination is owned by the worker** (design §7:174). A stop is a recorded *request*
  (`positioning.md:89-95`).

**if-built-today mechanism.**
- **Self-withdrawal:**
  - An asker that stops wanting a question stops renewing it; nothing is posted
    (`IBT/README.md:182-183`; `IBT/3-questions.md:189-209`).
  - Early withdrawal works by revealing a committed secret, `withdrawn(k)` (`IBT/5-domain.md:231-249`).
- **An operator halting someone else's demand is explicitly unsolved:** "**The halt does not
  dissolve** … That needs a write and an authority rule, and always did" (`IBT/README.md:182-184`).
- "A producer stopped by an operator's `stop` — a told fact, never an inferred silence"
  (`IBT/3-questions.md:194-197`). **The `stop` relation is not defined.**
- **A port of shipped's fold** (as a fold rewrite, not as IBT's own design):
  `discharged(C) :- stop(C), stopped(S), S > C.` / `unhandled = stops − discharged`
  (`IBT/5-domain.md:133-140`).

**Known defects and history (shipped).**
- **The original failures:** S1 lost edge (F2), S2 poisoned replay, S3 a resume that dies at its
  first step, S4 clobber. All fixed (`stop-discharge.md:16-34, 108-118`).
- **Refuted alternatives A1–A7**, including episode-start fencing (`stop-discharge.md:134-204`).
- **#39: discharge is author-blind and body-blind.** "11 of 37 stops were discharged by a record the
  worker did not write, 6 of them by a *malformed* one … No harm had landed — in every case the halt
  was already served exogenously" (`observables.py:445-456`; `backlog/cross-host-claim-gate.md:217-228`).
- **Run-scoped halt** (`backlog/run-scoped-halt.md:11-48`):
  - An honoured stop leaves the run claimable again, because the stop is *episode*-scoped while
    the consumer reads it as *run*-scoped.
  - The value-register recipe was refuted (`:50-99`).
  - The "durable ceiling" is the empty cell in the control 2×2 (`specs/control-target.md:169-184`).
- **`lifecycle.stopped` does five jobs.** A consumer forges it to get only job 4
  (`backlog/lifecycle-stopped-unbundling.md:3-20`).
- **`lifecycle.evicted` is deferred** (`backlog/claim-eviction.md:1-34`). It would not fix the
  honest-worker route of #39 (`run-scoped-halt.md:146-153`).

**Moving parts.**
- **Shipped, about 10.**
  - R2: `control.stop`; `lifecycle.stopped` as the discharge.
  - F3: the pending-set any-join, the discharge floor, `undischarged_stops`.
  - I5: a stop is a request; pairing by seq; at-least-once toward an idempotent effect;
    episode-scoped; malformed stops are naked.
- **IBT, about 5.**
  - R3: the lease renewal and `withdrawn(k)` (also counted in C9); `stop`, which is undefined.
  - F1: the lapse reading.
  - I1: self-withdrawal needs no post.
  - U2: the operator halt's write path and authority; how a producer observes a `stop`.

**Open questions.** IBT replaces "stop" with "withdraw demand" for the asker's own demand. Shipped's
`control.stop` is mostly an operator addressing a worker it did not launch, the case IBT leaves
open. So the two designs cover overlapping but different halves of "stopping".

---

### C8. Demand: subscriptions, questions and the condition algebra

**Shipped mechanism.**
- **`control.subscribe`** carries `name` (the target metric), a `request_id`, and a body
  `{from?, every?, until?}` (design §6:117-148; `protocol/subscription-v0.2.schema.json`;
  `vocabulary/schedule.py:25-140`).
  - A `Condition` is built over `step | time_seconds | count` with `any`/`all`.
  - `count` is allowed only in `until`.
- **The algebra is free.** There is no normal form, and conditions are never compared or hashed
  (design §6:144).
- **The worker's drain order is fixed:** answered-skip → boundary-void → malformed → unsatisfiable
  → register (design §6:156; `worker.py:376-411`).
- **"Registered ⟺ a future fire is possible."** Expiry writes a counter-record before deleting
  (emit-then-delete) (`specs/service-worker.md:43-53, 57-134`; `worker.py:477-495`).
- **The positional answer fold** (`observables.live_demand`, `observables.py:377-425`). A
  subscription is live until an unsubscribe or nak bearing its `request_id` *follows it by seq*.
- **Episode-local atoms** (`time_seconds`, `count`) make the whole subscription episode-scoped
  (`specs/time-lease-boundary.md:24-85`; `schedule.py:231-264`).
- **Serving:**
  - A fired subscription samples the register `self._values.get(name)` (`worker.py:484`).
  - `emit` logs unconditionally (`worker.py:203-237`).
- **`every` is a delta** since the last fire (`schedule.py:97-118`).
- **Visibility via `request_id` is read-side filtering**, not enforcement (design §6:152).

**if-built-today mechanism.**
- **A question is a tell of `asked(Q)`** (`IBT/3-questions.md:5-34, 211-232`).
  - Q is a positive formula with its quantifiers written out.
  - Free variables range; bound variables are what the question wants witnessed.
  - This is magic templates, one generic relation (`:21-27`).
  - Rules may derive questions (`:28-31`).
- **Matching is up to renaming of bound variables.** A small "question API" serves other languages
  (`:44-55`).
- **"Asking claims nothing"** (`:65-73`).
- **Answers stream individually**, and a stream ends when its question is *settled* (`:75-93`).
- **Settledness follows Nelson-style readings.** The residual is the undecided instances, and the
  residual being empty *is* settledness (`:95-187`).
- **Constraints come from a fixed CLP domain.** "Propagate, never label." Solvers are producers
  (`IBT/1-logic.md:319-375`). Which domain and algorithm is open (`IBT/open.md:16-19`).
- **A subscription is the reader's own rule plus routing:** "a cursor into a per-functor term
  index". The call table, registry, work set and cursor are one object (`IBT/3-questions.md:312-360`).
  Constraints need a canonical form (`:357-360`).
- **Subsumption runs two ways; anti-unification is refused** (`:293-310`).
- **An optional structural dependency graph**, `needs(g, p)` (`:328-349`).
- **`every` splits** (`IBT/open.md:84-89`):
  - a stride is a congruence range;
  - the delta and time readings are firing schedules, which are control.
- **Read-side engine:** a per-functor term index, a constraint solver in the read path, and a
  coverage checker (`IBT/5-domain.md:121-126`).

**Known defects and history (shipped).**
- **Stop was mistyped as a subscription** (`stop-discharge.md:29-34`).
- **Expired leases resurrected; naks were duplicated per episode** (`service-worker.md:59-66, 85-96`).
- **An accidental pure pin**, `{from:{count:k}}` (`service-worker.md:245-264`).
- **A ghost-lease relaunch loop** (`time-lease-boundary.md:12-22`). Count budgets were refunded per
  episode (`:62-85`).
- **`every` as a delta is non-monotone when replayed:** log `{0,11,20}` → `[0,11]` but
  `{0,10,11,20}` → `[0,10,20]` (`backlog/memoizer-index-algebra.md:35-66`).
- **The empty-window check punts** on `any` in `from` (design §6:153).
- **control-target R10:** the subscription algebra *can* express durable demand, via
  `{"from":{"step":N-1},"until":{"count":1}}` (`specs/control-target.md:236-257`).

**Moving parts.**
- **Shipped, about 16.**
  - R3: `subscribe`, `unsubscribe` (from the client or as an expiry), `nak`.
  - F5: `live_demand`, `boundary_voided`, `satisfied`/`Subscription.tick`, `is_unsatisfiable`,
    `malformed_schedule`.
  - I7: registered ⟺ fire-possible; emit-then-delete; fixed drain order; `count` only in `until`;
    episode-local atoms are episode-scoped; pop-then-skip slot semantics; no normal form.
  - X1: ticking at safe points.
- **IBT, about 15.**
  - R1: `asked`.
  - F5: matching up to renaming; the Nelson readings and settledness; the residual; subsumption;
    threshold reads.
  - I6: asking claims nothing; quantifiers live only in questions; a question stays the same
    question (only ground constants in its constraints); constraints take a canonical form; no
    anti-unification; propagate, never label.
  - X3: the term index, the CLP solver, the coverage checker.
  - U3: the constraint domain (Open 3); the admission rule (Open 10); the routing and transport for
    subscriptions.

**Open questions.**
- Shipped's `ensure` and `history` path does not go through subscriptions at all ("`set` only feeds
  *subscription-driven* emission and `ensure` never subscribes", `specs/derived-runs.md:88-92`).
- IBT unifies demand-for-production and demand-for-notification under `asked`.
- Reviewers comparing "subscriptions" should therefore check which shipped path they are mapping.

---

### C9. Leases, lazy launch, and "still wanted"

**Shipped mechanism.**
- **"One worker primitive, two demand durabilities"** (`specs/service-worker.md:17-31`):
  - durable demand is the launch target;
  - leased demand is subscriptions.
- **`serve()`** is stepless. **`retire()`** is the death CAS when nothing pins the worker.
  **`pinned`** reports whether anything does (`worker.py:176-194, 275-327`).
- **Client keepalive** is `until={time_seconds: N}` plus renewal. Time-referencing registrations
  are episode-scoped (design §7:178; `time-lease-boundary.md`).
- **The deciders:**
  - `ensure_served` wakes a run iff it has live leased demand and no live episode. The caller
    invokes it; the daemon form is a recipe with a mandatory per-cycle `reap()`
    (`specs/lazy-launch.md:18-98`; `launcher.py:368-403`).
  - `relaunch_if_needed` handles durable demand (`launcher.py:339-365`).
- **Counting is ref-count-exact, with no grace windows** (design §7:178; `service-worker.md:33-41`).
- **Off-host it is conservative:** it never wakes a run it cannot probe (`lazy-launch.md:46-52`).

**if-built-today mechanism.**
- **Production is gated on three things:** asked ∧ unsettled ∧ still wanted. "Still wanted" is
  control, supplied by the non-monotone core. "How it is supplied — a lease, a session, a durable
  standing order — is a scheduling policy, not part of this layer." (`IBT/3-questions.md:203-209`)
- **The domain policy** (`IBT/5-domain.md:200-249`):
  - A lease is a duration renewed one-way by the asker.
  - The scheduler times it from local receipt, on its own monotonic clock.
  - The lease is an argument of the question, `asked(Q, lease(C, N, D))` with `C = hash(k)`.
  - `withdrawn(k)` withdraws it. That needs no ordering, can't be forged, and is per asker.
  - `N` keeps renewals distinct.
  - Expiry is the backstop.
- **Durable demand is a lease of unbounded duration.** The risk of relaunching forever is the
  Open 10 admission rule (`IBT/5-domain.md:206-208`; `IBT/open.md:94-96`).
- **"Nothing may derive settledness from demand going quiet"** (`IBT/3-questions.md:122-124`).
- **Launching: not addressed** (see C20).

**Known defects and history (shipped).**
- **"Lazy as the primitive" was refuted** by the undemanded-bootstrap problem
  (`service-worker.md:268-272`).
- **The ghost-lease flap.** The waker's backoff policy was deleted by the boundary rule, which
  caps it at ≤2 relaunches (`time-lease-boundary.md:12-22, 89-108`).
- **The adversarial round refuted the lazy-launch draft three ways:** the ThreadLauncher
  degeneracy, the deletion of a null worker's verdict, and a "zombie factory"
  (`lazy-launch.md:3-16`).
- **No `ensure` over stepless services** (`lazy-launch.md:145-147`).
- **"Acceptance ≠ will-serve":** a lease can be voided with zero fires (`time-lease-boundary.md:120-132`).
- **control-target R2:** durable demand has no actuator without a daemon. The daemon was refuted
  for lacking a flap guard, which was a circularity (`control-target.md:87-100, 376-379`).

**Moving parts.**
- **Shipped, about 9** (not counting items already counted in C2 and C8).
  - R1: the retire `stopped`.
  - F2: `pinned`; the `ensure_served` decision.
  - I5: ref-count-exact with no grace; register before reap; reap is mandatory in a waker;
    acceptance ≠ will-serve; renew leases.
  - X1: the polling waker.
- **IBT, about 10.**
  - R2: `asked(Q, lease(…))`, `withdrawn(k)`.
  - F1: the lease-liveness reading.
  - I5: renewals are one-way; timing is from local receipt; withdrawal is unforgeable by preimage;
    expiry is the backstop; "still wanted" is never a fact.
  - X2: the scheduler's clock; hashing with random secrets.
  - U2: the scheduler/launcher; the admission rule.

---

### C10. Request acknowledgement and refusal

**Shipped mechanism.**
- **No per-request ack** (rejected as "relocated, not eliminated", design §13:301).
- **The heartbeat's `consumed_seq`** is the registration watermark.
- **`lifecycle.nak`** carries `reason ∈ {malformed, unsatisfiable, unsupported}` (design §6:153).
- **`await_consumed` is answer-first.** Its codomain is `Nak | terminal RunResult | None`
  (`watcher.py:434-…`).
- **One bad message is naked and never fatal** (`worker.py:367-374`).

**if-built-today mechanism.**
- **Records are rejected on arrival** by checks on the message alone:
  - an unranged variable (`IBT/0-substrate.md:25-26`);
  - a type error (`IBT/1-logic.md:509-513`);
  - a posted relation the schema declares derived-only (`IBT/3-provenance.md:43-48`).
- "*Liveness* still needs an acknowledgement" (`IBT/1-logic.md:221-223`).
- "Admission control still needs an explicit mechanism," and its signal is a quantity
  (`IBT/3-questions.md:370-371, 374-381`).
- **Not addressed:**
  - an ack record;
  - a refusal record;
  - how a poster learns that a post was rejected.

**Known defects and history (shipped).**
- **Duplicate naks per episode** (`service-worker.md:93-94`).
- **`await_consumed` deadlocked on a retire win** until answer-first (`service-worker.md:332-337`).
- **A `t`-less heartbeat** would block `await_consumed` (`observer-clock.md:318-324`).
- **The service-time nak site was removed** (`service-worker.md:85-91`).

**Moving parts.**
- **Shipped, about 6.**
  - R2: `consumed_seq`, `nak`.
  - F1: `await_consumed`.
  - I3: the watermark advances only after registration; a nak is final; one bad message is never
    fatal.
- **IBT, about 1.**
  - I1: reject on arrival, on the message alone.
  - U2: feedback to the poster; admission.

---

### C11. The value plane

**Shipped mechanism.**
- **The `value` topic** has an open, app-owned envelope `name` and the body
  `{value: Any, step: int|null, t: number|null}` (`protocol/value-v0.2.schema.json`; design §6:124-125).
- **Two write paths** (`worker.py:196-237`):
  - `set` is observer-cadence.
  - `emit` is worker-cadence. It refuses `step=None`, and refuses writes from a claim loser or after
    the worker's own terminal.
- **The read is last-write-wins by seq per `(name, step)`**: `value_series`
  (`observables.py:525-551`) and `history` (`memoizer.py:266-273`).
  - It is "a **convergent merge** … not a consistent snapshot" (`observables.py:535-542`).
- **The divergence raise is deleted.** Take-the-latest is justified by a reachability argument
  (`backlog/value-plane-divergence-resolution.md:36-77`).
- **The declared model** is "a silently interleaved series — a real cost, but a declared one"
  (`specs/write-authority.md:169-173`).
- **Stepless values** sit outside the step-indexed folds. Registers are read by `latest`, as in the
  completion reason and provenance (`completed-opt-in.md:74-101`; `store.md:276-297`).

**if-built-today mechanism.**
- **One relation for every metric, typed per metric:** `at(R, S, M)` with
  `M = loss(Float) | accuracy(Float) | converged(Bool) | …` (`IBT/5-domain.md:23-36`;
  `IBT/1-logic.md:482-518`).
  - A misspelt metric is an undeclared constructor, rejected on arrival.
  - Sharing schemas at runtime is out of scope.
- **Nothing merges:** `p(a,1)` and `p(a,2)` are two atoms (`IBT/0-substrate.md:14-18, 51-57`).
  There is no functional dependency; "Constraints are asked, never asserted"
  (`IBT/1-logic.md:397-480`).
- **Two kinds of conflict** (`IBT/2-polarity.md:390-403`):
  - a *domain* conflict, found by a user's per-relation rule;
  - a *valuation* conflict, which is `{t,f}`.
  - `conflicted(K)` must be declared per relation (`IBT/1-logic.md:432-442`).
- **Last-write-wins is "`argmax` over `seq`; a report"**, tier 3 (`IBT/4-aggregation.md:12, 75`).
- **Key granularity and vouching** decide whether a disagreement becomes visible
  (`IBT/5-domain.md:40-50, 98-103`).

**Known defects and history (shipped).**
- **The sticky divergence raise** permanently blocked reuse. It was deleted (G1)
  (`value-plane-divergence-resolution.md:16-34`).
- **Spliced series** (`observables.py:470-474, 535-541`).
- **Spec/code drift:**
  - `specs/observables.md:114-119` says an episode rewind makes "the orphaned branch drop out".
  - The current `observables.py:535-541` says "The abandoned branch does **not** drop out".
- **A post-terminal `emit`** overwrote the successor (`worker.py:219-226`). An `emit` with
  `step=None` poisons `history` (`worker.py:214-217`).
- **A displaced writer wins cells** under global-seq last-write-wins. The proposed remedy is to
  stamp the writer's claim seq (`dead_ends/per-episode-loglets.md`, "Where it goes instead"). It
  points at `backlog/episode-correlation.md`, which **does not exist in the tree**. The nearest file
  is `backlog/episode-aim.md`.
- **Corpus figures quoted by IBT, which cannot be checked here:**
  - 1,714 of 1,719 divergent cells are `status`;
  - 16 hand-rolled guard sites;
  - an ulp guard in `mycooc/analyze_run.py`;
  - 0.34% compression given up (`IBT/5-domain.md:262-270`).

**Moving parts.**
- **Shipped, about 11.**
  - R1: `value`.
  - F4: `value_series`, the `history` collapse, the `_value_points` domain rules, the `latest`
    register.
  - I5: `set` vs `emit` cadence; no `step=None` emit; no post-terminal emit; last-write-wins is the
    declared model; `ensure` never re-drives a completed run (the premise of G1's soundness).
  - X1: `json_default`.
- **IBT, about 8.**
  - R1: `at(R, S, M)` with per-metric constructors.
  - F1: per-relation `conflicted` rules.
  - I5: no merging; no functional dependency; constraints are asked; key granularity is a choice;
    the signature is agreed at deployment.
  - X1: the arrival type check.

**Open questions.** Shipped resolves non-reproducible resumes by take-the-latest, under the
reachability argument. IBT resolves them by schema choice: key by episode, or don't vouch. The two
give different answers on the same log. Which one `ensure`-style reuse wants is not argued on the
IBT side.

---

### C12. Memoisation (`ensure`, `history`)

**Shipped mechanism.**
- **`history(channel, name, schedule)`** replays the condition algebra over the logged values. It
  collapses take-the-latest, and evaluates time relative to the epoch (the earliest `started.t`)
  (`memoizer.py:246-294`).
- **`ensure(producer, name, *, until)`** (`memoizer.py:369-473`):
  - **Read first.** If the window is closed or `worker_completed`, return `history`.
  - **Otherwise** call `producer.extend(until)` and wait on the handle it returns.
  - **Re-drive** on `preempted`.
  - **Raise:**
    - `RunFailedError` on a failure outcome;
    - `RecordlessExitError` at the fixed point;
    - `NoProgressError`, which is scoped to its own spawn, claim-aware and axis-aware.
- **Progress comes from the dense heartbeat axis**; content comes from the value series
  (`specs/memoizer.md:74-88`).
- **The half-open fencepost:** `until={"step": N}` means `[0, N)` (`observables.py:486-493`;
  `memoizer.py:323-335`).
- **The producer seam** is `.channel`/`.run_id`/`.extend(until)`. It returns a liveness handle,
  or `foreign_episode(channel)` when someone else's episode is live (`memoizer.py:102-148`;
  `store.md:148-191`).
- **Time is the consumer's own poll clock** (`specs/ensure-until-condition.md:82-138`).
- **No hang timeout**, by design (`memoizer.py:391`).

**if-built-today mechanism.**
- **The memo check is per-instance, "not a memo *table*".** The doc weighs keying on calls against
  keying on answers (`IBT/3-questions.md:143-158`).
- **The producer is handed a residual.** It is computed locally and best-effort, and consumed as a
  scheduling decision. An empty residual is settledness (`IBT/3-questions.md:156-171`).
- **Identical questions collapse into one record, so nothing runs: "The store was the cache"**
  (`IBT/5-domain.md:91-93`). Containment compaction is unnecessary (`IBT/3-questions.md:180-183`).
- **Some residuals never empty, which risks relaunching forever.** That needs the admission rule
  (`IBT/3-questions.md:173-178`; `IBT/open.md:94-96`).
- **Its mapping of shipped:**
  - "`ensure` is the one place the library's core operation leaves the fragment": a temporal-delta
    guard and a fixpoint guard (`IBT/5-domain.md:178-183`).
  - "The store can tell you what happened; it cannot tell you that *nothing* happened, and `ensure`
    needs exactly that" (`:168-171`).
- **The guards themselves are unspecified.** They belong to the non-monotone core.

**Known defects and history (shipped).**
- **The completion gaps** (C5): early completion, the `completed` default, the launcher exit 0.
- **The no-progress guard:**
  - It false-raised on the time axis (`ensure-until-condition.md:179-210`).
  - It is structurally dead for time targets (`control-target.md:144-167`; `memoizer.py:439-461`).
- **The epochless livelock:** about 97k re-drives (`ensure-until-condition.md:127-134`).
- **Producer gating:**
  - A producer that returned `None` instead of a handle hung forever on a winner that died without
    a record (`store.md:169-178`).
  - The spurious raise in the claim window (`store.md:223-232`).
- **Dead ends:** push-or-fail `extend` (`dead_ends/ensure-extend-pushorfail.md`); the
  `window_closed` helper (`dead_ends/window-closed.md`).
- **The sticky divergence raise** (G1). Killed-redrive was resolved as a caller recipe
  (`backlog/ensure-redrive-recoverable-terminations.md:1-10`).
- **Parked:** the off-channel artifact race, `ensure(await_complete=True)`
  (`backlog/ensure-await-completion.md`).
- **Still deferred:** the `from`/`every` emission filter, because `every` is non-monotone
  (`backlog/memoizer-index-algebra.md:35-66`).

**Moving parts.**
- **Shipped, about 18.**
  - F5: the `history` replay, `_satisfied`/`_window_step`, `_epoch`, `_elapsed`, `progress`.
  - I8: the half-open window; the completed/preempted contract; the guard is scoped to its own
    spawn; claim-aware; axis-aware; a clean exit implies progress (an invariant "promoted to prose
    2026-07-16"); `extend` never returns `None`; `count` is rejected.
  - X2: the poll clock; the producer seam.
  - Plus three typed errors.
- **IBT, about 3** (most of its machinery is counted in C8).
  - I3: memoisation is per instance; the residual is never published; the store is the cache.
  - U2: the relaunch-forever guard and admission; the temporal and fixpoint tests.

---

### C13. Derived runs and the relational layer

**Shipped mechanism.** The relational layer is dissolved into recipes plus one helper
(`specs/store.md:25-71`).
- **Placement:** see C1.
- **Dispatch** is `ensure` gated by `foreign_episode`.
- **GC** is mark-and-sweep.
- **Provenance** is a backward value-register record on the child's own log, written at birth
  (`:274-323`).
- **The index** is a dormant, pure cache (`:325-347`).
- **The invariants:** verify at use; fail only by false miss; facts are true-at-append; write at
  birth; an index is a pure cache.
- **Derived identity composes**, and there is a one-step-run convention (`specs/derived-runs.md:32-112`).
- **"Experiment" never enters the runstate vocabulary** (`store.md:351-361`).
- **The root set** is the irreducible authoritative kernel (`store.md:399-414`).

**if-built-today mechanism.**
- **Rules derive questions**, for example `asked(∃V. loss(K, V)) :- asked(∃W. report(K, W))`
  (`IBT/3-questions.md:28-31`).
- **The logical layer needs no dependency graph.** An optional structural `needs(g, p)` is
  available, and reclamation's reachability does need a graph (`IBT/3-questions.md:328-349`).
- **Provenance is an annotation branch** (`IBT/README.md:150-166`; `IBT/3-provenance.md`).
- **Shipped programs as data:** `program(h, Source)` and `asked(run(h, Args))`. This is "the
  escape hatch *beside* the questions" (`IBT/open.md:20-45`).
- **Not addressed:** experiments and membership, on-disk layout, and run enumeration.

**Known defects and history (shipped).**
- Marker rot, the custody bug, the ungated producer spin and the `None`-gated hang (`store.md:164-191`).
- The derived-run aliasing trap (`derived-runs.md:46-57`).
- A multi-parent derived run is a named residue (`store.md:313-319`).
- The "A-residue": scattered multi-host with no shared filesystem (`store.md:408-414`).
- The Store component itself was dissolved after a seven-agent deliberation
  (`backlog/store-deliberation.md`).

**Moving parts.**
- **Shipped, about 14.**
  - R2: the `analyzed` provenance register; the resolved-config record.
  - F2: the readlink walk; the collectible predicate.
  - I8: the five store invariants; cell ≠ run; pointer before `ensure`; hash only settled
    snapshots.
  - X2: pointers and symlinks; the tracked `summary.csv`.
- **IBT, about 4.**
  - R2: the optional `needs`; `program`/`run` (open).
  - F1: rule-derived questions.
  - I1: no dependency graph in the logic.
  - U: membership; layout.

---

### C14. Reclamation, GC and retention

**Shipped mechanism.**
- **In-log retention is full, with no GC.** That is "exactly the precondition `peek_terminal` /
  resume rely on" (design §4:68, §12.9:290).
- **Home-level collection is a recipe** (`store.md:234-272`):
  - mark-and-sweep with pointers as roots;
  - run offline;
  - selective prune by default;
  - a grace window via `last_activity`;
  - collectible iff no pointer ∧ no live episode ∧ no pinned nested home.
- **In-log compaction is a deliberation, not converged** (`backlog/in-log-compaction.md`).
- **Decision rule L1:** compaction quotients the log and breaks initiality
  (`backlog/protocol-algebra.md` §L1).

**if-built-today mechanism.**
- **"Reclamation is evolvable policy behind a fixed mechanism … one injected interface"**
  (`IBT/3-reclamation.md:5-11`).
  - "Reclamation must not depend on receiving a message."
- **Eviction is not retraction.** Reclamation owes a caching invariant: whatever is evicted must
  be re-derivable on demand (`:13-17`).
- **Tiers by re-derivability, not polarity** (`:18-33`; `IBT/decisions/0-substrate.md:42-46`):
  - a derived atom: evict freely;
  - a produced record whose producer lives: only at a price, and only if production is
    deterministic;
  - a produced record whose producer is gone: never.
- **Reachability is the reclamation layer's graph** (`IBT/3-questions.md:339`).

**Known defects and history (shipped).**
- **The WAL mtime lie** broke the GC's age check. Fixed by `last_activity`.
- **GC is not safe cross-host on its own**, because `live_episode` reads an unresolvable handle as
  live (`observer-clock.md:55-59`; `store.md:270-272`).
- **The custody bug** (C1).
- **Heartbeats are about half of all envelopes**, and a `serve()` log grows without bound
  (`in-log-compaction.md:14-35`).

**Moving parts.**
- **Shipped, about 8.**
  - F2: the collectible predicate; `last_activity`.
  - I5: full in-log retention; persistence requires a root; sweep offline; grace is a belt, never
    the reason; selective prune.
  - X1: the sweep.
- **IBT, about 5.**
  - I4: the caching invariant; re-derivability tiers; not dependent on any message; eviction ≠
    retraction.
  - X1: the injected interface.
  - U: the policy; the reachability graph.

---

### C15. Time and clocks

**Shipped mechanism.**
- **Three clocks** (design §11:274-276):
  - `seq` for order;
  - `step` for the worker's logical clock;
  - wall clock for real time.
  - Predicates are evaluated in the worker's tick, never against `seq`.
- **`t` is required** on `started`, `heartbeat`, `stopped`, `launched` and `terminated`. `nak` is
  undated by design. `value.t` is the data-plane clock and is present-nullable
  (`specs/observer-clock.md:84-123`).
- **The rules** (`:125-149`):
  - `seq` orders and `t` measures.
  - Staleness is a local inference.
  - Time never arbitrates a claim or a death verdict.
  - A required `t` is never fabricated.
- **The Watcher prefers witnessed arrivals** and seeds from `t` (`:153-207`).
- **Rejected designs** (`:242-276`):
  - `t` on every envelope;
  - a monotone clamp;
  - **a monotonic/stopwatch clock** ("no shared origin ⟹ staleness structurally unanswerable");
  - **wall time anchored to a stopwatch** ("`CLOCK_MONOTONIC` does not tick across suspend").
- **Three time anchorings coexist by design** (`backlog/index.md:233-237`):
  - `history` is epoch-anchored;
  - subscriptions are episode-scoped;
  - stops re-anchor per episode.
- **`ensure` uses the consumer's poll clock** (`ensure-until-condition.md:82-138`).

**if-built-today mechanism.** It separates three questions (`IBT/1-logic.md:295-307`):
- **Order** is the causal partial order, from message counters with acknowledgement: "The counter
  is the cursor."
- **Duration** is answered by a local monotonic clock.
- **Absolute time** "is needed for nothing here, and is the only one unobtainable."

Further:
- **The local stamp must be monotonic** (`:309-312`).
- **An observation literal carries "who observed, by whose clock"**, and comparing across observers
  is a report (`:290-293`). Example: `clock_skew(bob, t₁, t₂, δ)` (`:551-553`).
- **A lease is timed from local receipt** (`IBT/5-domain.md:209-214`).
- **Time readings of `every`/`until`** are firing schedules, or ranges only if the atom carries its
  time (`IBT/open.md:84-89`).

**Known defects and history (shipped).**
- **A decision reversed in the docs:**
  - `ensure-until-condition.md:113-116` calls `Heartbeat.t` "rejected, not staged", "a converged
    decision".
  - `observer-clock.md` later made it required (lifecycle-v0.4).
- **The dead run read as live; the WAL mtime; the epochless livelock** (C4, C12).
- **The time-lease re-anchor and the count refund** (C8).
- **`value.t` was redefined** from per-episode to absolute (`specs/memoizer.md:225-238`).
- **An evictor's `t` corrupts `last_activity`.** A run quiet for 480 s reads 5 s fresh
  (`backlog/cross-host-claim-gate.md:229-233`).

**Moving parts.**
- **Shipped, about 17.**
  - R2: `t` on the five dated topics; `value.t`.
  - F4: `last_activity`, the Watcher seed, `_epoch`, `_elapsed`.
  - I7: `seq` orders and `t` measures; staleness is local; time never arbitrates an irreversible
    act; never fabricate `t`; `nak` is undated; never schedule on `seq`; episode-local atoms are
    episode-scoped.
  - X4: the worker's wall clock; the observer's arrival clock; the consumer's poll clock; the
    server's `created_at`.
- **IBT, about 8.**
  - R1: dated observation literals.
  - I5: three separate questions; absolute time unused; monotonic local stamps; comparing
    observers is a report; observer and clock live in the literal.
  - X2: the local monotonic clock; message counters.

**Open questions.**
- Shipped's observer-clock spec exists so that a *cold third party* can date a run from the log
  alone. Its victims were the viewer, the Watcher's verdict and the GC's age (`observer-clock.md:43-59`).
  That needs a shared-origin wall clock, and shipped rejected monotonic clocks for exactly this.
- IBT says absolute time is needed for nothing, and lists cross-host liveness as unsolved. Its
  lease section accepts the cold-attach cost: "a scheduler that attaches after a dead asker's last
  renewal receives it as fresh" (`IBT/5-domain.md:212-214`).
- Whether IBT answers the cold-attach freshness question differently, or declines it, is not
  explicit.

---

### C16. Write authority, provenance, forgery and dispute

**Shipped mechanism.**
- **The enforced/recorded/never boundary** (`positioning.md:59-97`).
- **"Single-writer holds at the claiming instant only"** (`specs/write-authority.md`).
- **An author field is deferred.** A `request_id` prefix is the stopgap (design §12.8:289).
- **Launch-id correlation** on launcher records (`specs/launcher-record-identity.md`).
- **Visibility is filtering, not enforcement** (design §6:152).
- **The JSON schemas are "a conformance test, not a runtime gate"** (`layers.md:42-50`).
- **Proposed or deferred:**
  - `claim_seq` aim (`backlog/episode-aim.md`);
  - a designated eliminator, `lifecycle.evicted` (`backlog/claim-eviction.md`).

**if-built-today mechanism.**
- **"Enforcement. Still honour-system. This is why forgery defects survive."**
  (`IBT/README.md:181`; also `:96-100`). Identity-as-data is claimed to kill *attribution* defects.
- **Dispute** (`IBT/3-provenance.md:7-56`):
  - Objections are *derived* from grounds:
    `disputes(F) :- produced_by(F, S), miscalibrated(S), timing_sensitive(F)`.
  - Objections to objections need nothing extra.
  - Readjudication is free.
  - "Accept unless disputed" is a reader's decision; trust policy is per reader.
  - A schema can declare `disputes` derived-only, so that bare posts are rejected.
- **Provenance is not built.** It is "the single highest-leverage unbuilt thing here"
  (`IBT/open.md:12-15`).
- **`¬Q` has no author.** Authorship is wanted for positives too (`IBT/2-polarity.md:173-176`).
- **Who may declare a set closed** is a write-authority question (`IBT/2-polarity.md:224-226`).
- **Lease withdrawal is unforgeable**, by preimage (`IBT/5-domain.md:233-237`).
- **Types are checked at both ends**, sender and receiver (`IBT/1-logic.md:509-513`).
- **Speculation makes provenance a requirement**, and it must live in the record
  (`IBT/3-provenance.md:88-93`).

**Known defects and history (shipped).**
- **#32:** three refuted mechanisms (C2).
- **A consumer reclaim tool forges `stopped`** (`backlog/cross-host-claim-gate.md:352-371`).
- **The strength/relevance/authority decomposition is untested** (`:388-423`).
- **"A forger reads the aim in one call", measured** (`backlog/episode-aim.md:25-37`).
- **Attribution across multiple orchestrators is open** (`backlog/index.md:319-321`).

**Moving parts.**
- **Shipped, about 5.**
  - R1: `request_id` launch correlation.
  - I4: the enforced/recorded/never boundary; single-writer at the instant; veto, never authorise;
    visibility is not enforcement.
- **IBT, about 6.**
  - R2: `disputes`; user-declared grounds relations.
  - F1: the per-reader trust fold.
  - I3: honour system; objections need grounds when declared derived; readers may diverge on
    trust.
  - U2: the provenance mechanism; authority to declare closure.

---

### C17. Wire format and schema discipline

**Shipped mechanism.**
- **The envelope** is `{seq, topic, name?, request_id?, body}`, governed by a lift rule: "a field
  is in the envelope iff the substrate indexes/routes/filters on it" (design §4:45-58;
  `protocol/envelope-v0.2.schema.json`).
- **A closed set of ten topics** (`vocabulary/payloads.py:23-44`).
- **Per-convention schemas** with `additionalProperties: false`, each versioned independently:
  `subscription-v0.2`, `lifecycle-v0.4`, `launcher-v0.4`, `value-v0.2` (design §10).
- **Body fields are present-nullable; schedule slots are omittable** (design §7:165).
- **The Postgres body stays `text`**, for byte fidelity (`channel-postgres.md:67-69`).
- **The migration doctrine:** offline scripts, run to convergence, then deleted
  (`launcher-record-identity.md:138-151`).
- **`tests/test_schema.py`** validates what the implementation emits.

**if-built-today mechanism.**
- **A record is a constrained fact**, `A(x̄) :- c(x̄)`. Every variable must be ranged in the body,
  and a record that isn't is rejected on arrival (`IBT/0-substrate.md:20-28`).
- **Variables travel as `var(N)`**, numbered canonically by first occurrence. A ground prefix binds
  variables, and programs never handle the encoding directly (`IBT/0-substrate.md:64-82`).
- **Set-semantics dedup needs a canonical encoding** (`IBT/1-logic.md:528-531`).
- **Signatures are many-sorted and per functor, with no untyped escape** (`IBT/1-logic.md:482-518`).
  - The signature belongs to the program, is agreed at deployment, and is checked at both ends.
  - "If signatures were themselves data, the problem returns unchanged."
- **The polarity field is two-valued**, so `additionalProperties: false` can pin it
  (`IBT/2-polarity.md:469-473`).
- **Constraints need a canonical form** (`IBT/3-questions.md:357-360`).
- **Not specified:** the concrete serialization, envelope fields, and how signatures are versioned.

**Known defects and history (shipped).**
- **Convention bumps:**
  - lifecycle-v0.3 removed `hostname`.
  - launcher-v0.3 made `request_id` required; the spec's "no schema change" claim was wrong
    (`launcher-record-identity.md:97-106, 185-190`).
  - observer-clock v0.4 added `t`.
  - B′ removed `Stopped.reason`.
- **Stale reference:** `protocol/value-v0.2.schema.json`'s description still mentions
  `attach()/open_channel()`, which channel-locators removed.
- **App-minted topics are rejected** as namespace squatting (`store.md:286-293`).
- **A planned rename**, `subscription` → `control` (control-target D6, `control-target.md:406`).

**Moving parts.**
- **Shipped, about 12.**
  - R5: the envelope plus four convention schemas.
  - I6: the lift rule; closed topics with open names; `additionalProperties: false`;
    present-nullable fields; independent versioning; migrate, never accommodate.
  - X1: JSON plus `json_default`.
- **IBT, about 12.**
  - R3: the constrained fact; `var(N)`; the prefix binder.
  - I7: every variable ranged; canonical numbering; per-functor sorts; the signature agreed at
    deployment; two-valued polarity; canonical constraint form; no direct handling of the encoding.
  - X2: the arrival check; the record-building API.
  - U: the concrete encoding and its versioning.

---

### C18. Backends, transport and ordering assumptions

**Shipped mechanism.**
- **Three backends**, Memory, SQLite and Postgres, all conformance-tested (design §3).
- **The ordering contract:** `seq` is contiguous and 1-based, from a single sequencer per log
  (design §4:67).
- **Cursors belong to the caller**; the substrate keeps no per-reader state (design §4:66).
- **The premise: one authoritative sequencer per run** (design §14:315).
- **Postgres** gives cross-host access through one shared server, a single point of failure
  (`channel-postgres.md:298-307`).
- **SQLite on NFS** needs a single writer (C2).
- **Inbox, not yet designed:**
  - a read query layer, #15;
  - push notification, #16 (`backlog/index.md:83-91`).

**if-built-today mechanism.**
- **The store is a growing set, and merge is union.** "Unordered, duplicate-tolerant, loss-tolerant
  broadcast suffices … Not even causal order is required" (`IBT/5-domain.md:114-119`).
- **Delivery properties come free** (`IBT/1-logic.md:240-251`):
  - idempotent, so at-least-once is exactly-once;
  - commutative and associative;
  - a partial store is sound.
- **No central store.** The "global" store is the union of the local stores (`IBT/open.md:46-53`).
- **Whether demand should be the only interconnect is open** (`IBT/open.md:54-59`).
- **A library's jobs** are persistence, indexing and an oracle channel (`IBT/5-domain.md:160-174`).
- **No backend is specified.**

**Known defects and history (shipped).**
- **Dead ends:** log-forking and per-episode loglets. Measured: 12 of 12 `.read()` sites consume
  order (`dead_ends/per-episode-loglets.md`).
- **The partial-order gate:** four positional rules (`backlog/machine-partitioned-logs.md`).
- **Discharge-by-id is deferred** until there is a replicated log (`backlog/index.md:238-246`).
- **HA would re-admit `seq`**, which means "a different substrate" (`channel-postgres.md:293-294`).
- **J3:** DELETE-mode busy retry (`backlog/index.md:333-339`).

**Moving parts.**
- **Shipped, about 15.**
  - X4: three backends plus the optional lock capability.
  - The five operations: `send`, `read`, `latest`, `last_seq`, `close`.
  - I6: contiguous seq; a single sequencer; caller-owned cursors; thread-safe handles; values
    snapshotted at `send`; full retention.
- **IBT, about 6.**
  - I4: union merge; unordered delivery suffices; a partial store is sound; order is needed only
    for the claim.
  - X2: broadcast transport; persistence.
  - U3: the backend; the replication policy; where the claim's frontier lives.

**Open questions.** Shipped's L2 says the conventions are positional, and design §14 says the
causal regime "rewrites the conventions". IBT's mapping (`IBT/5-domain.md:128-157`) agrees that
"no fold ports as-is". The size of that rewrite is quantified only by IBT's own measurements
(see §4, claim 4).

---

### C19. The observer plane: folds, monotonicity and summaries

**Shipped mechanism.**
- **Pure folds** in `observables.py`: `peek_terminal`, `live_episode`, `latest_episode`,
  `progress`, `value_series`, `live_demand`, `undischarged_stops`, `last_activity`.
- **The tolerance split:** measurement folds skip junk; verdict folds raise (`observables.py:13-20`).
- **The membership test:** anything that needs a cursor or clock belongs to the Watcher.
- **Windowing at the Markov boundary** (design §14:317; `observables.py:221-229`).
- **No summaries or aggregation in the library.**
- **The verdict is a join taken at the edge** (protocol-algebra L3).

**if-built-today mechanism.**
- **Derivation reads only threshold up-sets:** `⊒{t}`, `⊒{f}`, `⊒{t,f}` (`IBT/2-polarity.md:333-335`).
- **`∅` cannot be read, and conflation is unavailable** (`:356-382`).
- **Reports may use negation but must not feed demand** (`IBT/1-logic.md:125-131`). The constraint
  binds on output, not on internal computation (`IBT/2-polarity.md:380-382`).
- **Aggregation is read-side, in three tiers** (`IBT/4-aggregation.md:47-124`):
  - join-homomorphisms;
  - monotone summaries;
  - reports.
  - The may-lift moves any summary into tier 2.
- **Contextuality** can arise from available-case analysis (`:49-58`).
- **Porting the folds:** "dual plus subtraction". Picking the natural dual is "where the danger is"
  (`IBT/5-domain.md:128-157`).

**Known defects and history (shipped).**
- **F7:** two private copies of one boundary rule led to public observables (`specs/observables.md`).
- **Unbounded folds** both slow down and poison. Measured: 3461 µs → 92 µs with windowing
  (design §14:317).
- **The `progress` splice** (C3).
- **The `window_closed` dead end.**
- **#17:** conflict as an observable. Pure-log conflict detection proved unreliable
  (`backlog/index.md:92-97`).

**Moving parts.**
- **Shipped, about 13.**
  - F8: the public folds.
  - I4: the tolerance split; the membership test; the Markov boundary; one home per rule (F7).
  - X1: the Watcher.
- **IBT, about 7**, plus the eight fold rewrites it costs (`IBT/5-domain.md:156`).
  - F3: threshold reads; the may-lift; summary tiers.
  - I4: derivation vs report; `∅` cannot be read; the constraint binds on output; idempotence is
    the gluing condition.

---

### C20. Process launching and orchestration helpers

**Shipped mechanism.**
- **Launchers:** the `Launcher`/`LaunchHandle` Protocols, `ThreadLauncher`, and `LocalLauncher`
  (Popen plus `reap`).
- **The handle grammar.**
- **Launcher records** with launch ids.
- **The deciders**, `relaunch_if_needed` and `ensure_served`.
- **`sweep`**: sequential, with resume-by-`peek_terminal` (`sweep.py:40-78`).
- **The `Watcher.broadcast` barrier**, with a patience cap (design §8-9).
- **"runstate never *originates* a spawn"** (`backlog/run-scoped-halt.md:103-107`). But it does
  schedule: see the layer-7 caveat (`layers.md:129-138`).

**if-built-today mechanism.**
- **Delegated:** "How it is supplied … is a scheduling policy, not part of this layer"
  (`IBT/3-questions.md:203-209`).
- **The buildable object** is "closer to **a Prolog with a durable fact base and a pid probe**"
  (`IBT/5-domain.md:172-174`).
- **There is no launcher, handle grammar, reaper, sweep or barrier.**

**Known defects and history (shipped).**
- **The `Launcher` Protocol can't be typed**, because the two `launch` signatures are disjoint
  (`backlog/launcher-protocol-typing.md`; `launcher.py:355-360`).
- **A reap discipline was added and then deleted** (C2).
- **`ThreadLauncher` can't terminate a thread.**
- **Watcher layering violations** (`layers.md:27-39`).
- **A time-keyed barrier is voided at the episode boundary**, so barriers should be step-keyed
  (`time-lease-boundary.md:157-163`).

**Moving parts.**
- **Shipped, about 13.**
  - R2: `launched`, `terminated`.
  - F3: the two deciders; the `sweep` resume.
  - I4: never `Watcher.add` an `ensure_served` handle; reap is mandatory; barriers are
    step-keyed; a bounded wait needs a patience cap.
  - X4: the two launchers; the handle grammar; the Watcher.
- **IBT, about 1.**
  - I1: the production gating triple.
  - U: everything else.

---

### C21. The artifact plane

- **Shipped:** "runstate gives no directory" (`run-episodes.md:128-132`). It is where a double-live
  worker's damage lands (`write-authority.md:174-175`). Checkpoint custody falls out of placement
  (`store.md:114-117`).
- **IBT:** "Checkpoints on a filesystem remain unmodelled, and remain where a double-live worker's
  real damage lands" (`IBT/README.md:179-180`).
- **Neither side models it.** Moving parts: zero on both sides.

---

## 4. Claims to check

Each item cites both ends. "Accurate" means I found it consistent with the shipped source.

1. **The calibration figure.**
   - IBT says: "**11 of 37 stops were discharged by a record the worker did not write, 6 of them
     malformed** — a late heartbeat from a dead episode read as current, a displaced worker's
     terminal read as the run's verdict, a halt swallowed because discharge was author- and
     body-blind. Position-derived identity does not fail rarely." (`IBT/README.md:90-94`)
   - The source is `observables.py:445-456`. The numbers are quoted correctly.
   - The measurement covers only *stop discharge*, the third example. The other two examples come
     from `backlog/episode-aim.md:51-57`, a prototype, not from that count.
   - The source adds: "No harm had landed — in every case the halt was already served exogenously."
   - The discharging records were third parties releasing stranded claims. A consumer tool forges
     `lifecycle.stopped` (`backlog/cross-host-claim-gate.md:217-228, 352-371`).
   - `episode-aim.md:20-39` says identity-as-data ("aim") does **not** close that forgery route.
     The IBT README also says forgery defects survive (`:96-100`).
   - So the number calibrates a defect class that, by shipped's own analysis, this commitment
     doesn't remove.

2. **"The shipped compare-and-swap *is* a unique constraint, `PRIMARY KEY (run_id, seq)`"**
   (`IBT/README.md:203-207`).
   - True for Postgres (`channel/postgres.py:44, 91-97`).
   - SQLite uses a single guarded INSERT over an autoincrement key (`channel/sqlite.py:207-245`).
   - Memory uses a lock (`channel/memory.py:50-57`).
   - Partially accurate.

3. **"Order is needed in exactly one place, the claim … Nothing in the semantics reads a sequence
   number."** (`IBT/5-domain.md:117-119`)
   - This is internal to IBT. Its own fold port reads seq: `discharged(C) :- stop(C), stopped(S), S > C`
     (`5-domain.md:134`).
   - Last-write-wins is "`argmax` over `seq`" (`4-aggregation.md:12`).
   - "A bounded reconnect still needs a cursor … The counter is the cursor" (`1-logic.md:221-223,
     298-300`).
   - Lease renewals need `N` to stay distinct, and are timed from the "latest renewal"
     (`5-domain.md:209-226`).
   - These may all be reports or transport rather than "semantics", but the boundary isn't drawn
     explicitly.

4. **"Measured across the whole of `observables.py`: 13 of 16 fold readings are non-monotone …
   `latest` … appears six times directly plus three `[-1]`/`reversed` and three `max(…)` in 551
   lines."** (`IBT/5-domain.md:128-131`)
   - The current file is 551 lines.
   - It has **7** direct `.latest(` calls (`observables.py:130, 150, 171, 174, 357, 457, 495`).
   - It has **3** `[-1]`/`reversed` (`:235, 240, 414`).
   - It has **2** `max(` calls in code (`:362, 501`); the other mentions are in docstrings.
   - Close, not exact; possibly a different commit.
   - "13 of 16", "13 of 13 duals monotone" and "9 of 9 reconstruct" have no artifact in the
     readable tree, so they can't be verified here.

5. **`peek_terminal`'s emptiness test was "measured to retract a published verdict when a later
   claim arrives"** (`IBT/5-domain.md:147-148`).
   - Accurate as behaviour. In shipped it is **designed semantics**: "a terminal stands until a new
     episode claims" (`observables.py:264-269`), and there is "No 'done forever' concept"
     (`run-episodes.md:41-45`).
   - Also "waking a run that had *completed* demotes its latest verdict" (`lazy-launch.md:55-57`).
   - The disagreement is over framing, not fact.

6. **"The `control.target` design was refuted by exactly this storm (R5: 373 spawns in 3 s)"**
   (`IBT/3-questions.md:176-178`). IBT gives "this" as a residual that can never empty.
   - `specs/control-target.md:144-167` attributes the storm to the no-progress guard being
     structurally dead for time targets (`time_seconds=float("inf")`) plus the `+1` overshoot, under
     two rival target writers.
   - `memoizer.py:456-461` attributes it to "a worker that halts on a target being met" (a clean
     exit at zero progress).
   - Related (relaunching forever), but a different cause. Not "exactly this".

7. **"The shipped `control.subscribe` … three concerns in one record … pairing answers to requests by
   log position — the positional answer fold"** (`IBT/3-questions.md:322-325`).
   - Accurate (`specs/service-worker.md:67-76`; `observables.py:377-425`).
   - It omits that a shipped subscription is also *leased demand that pins a service worker*
     (`service-worker.md:43-47`). IBT maps that role to the lease in `5-domain.md:200-249`.

8. **"Exhaustion arrives three ways in runstate today — `lifecycle.stopped`, `launcher.terminated`,
   and a pid probe — of which only the last is dependable … the probe abstains off-host."**
   (`IBT/5-domain.md:185-189`)
   - It omits heartbeat staleness, tier 4 (design §8:197; `watcher.py:280-293`).
   - It omits the Postgres episode lock, tier 3b: "definitive cross-host death detection", Watcher
     only (`specs/channel-postgres.md:146-155`; `watcher.py:85-116`).
   - The same applies to `IBT/README.md:177-178`, "it still abstains off-host". That is true of
     `resolve()` and the claim gate, but not of the Postgres observation path.

9. **"A subscription with an `until` is one durable record denoting a bounded region — durable across
   the producer's death … pinned by `test_relaunch_extends_one_series`"** (`IBT/5-domain.md:191-195`).
   - The test exists and matches the description (`tests/test_run_episodes.py:26-53`).
   - But only **step**-keyed schedules are durable across episodes. Any `time_seconds` or `count`
     atom makes the subscription episode-scoped (`specs/time-lease-boundary.md:24-85`;
     `schedule.py:231-264`).
   - Accurate for step-only schedules.

10. **"The bare subscribe is served by a poll of the register, `self._values.get(name)`"**
    (`IBT/5-domain.md:197-198`).
    - Accurate (`worker.py:484`).
    - Context: the memoiser path (`ensure`/`history`) reads `emit`-logged points, and
      "`ensure` never subscribes" (`specs/derived-runs.md:88-92`; `worker.py:203-237`).

11. **"runstate already has both kinds of demand: `relaunch_if_needed` … durable … `ensure_served` …
    leased"** (`IBT/5-domain.md:202-204`).
    - Accurate (`specs/lazy-launch.md:20-25`; `launcher.py:339-403`).

12. **"The known cost is the same bounded one runstate accepts in `time-lease-boundary.md`: a
    scheduler that attaches after a dead asker's last renewal receives it as fresh"**
    (`IBT/5-domain.md:212-214`).
    - `time-lease-boundary.md`'s accepted cost is a dead lease re-anchored at most once into its
      first possible drainer, giving ≤2 relaunches (`:89-108`). It is about worker episodes, not
      scheduler attach time.
    - The closer shipped analogue is the cold-attach problem in `specs/observer-clock.md:153-207`.
      Shipped fixed that by seeding from the record's wall-clock `t`, which IBT's
      local-receipt/monotonic lease doesn't have.
    - The analogy is loose.

13. **"The shipped `control.unsubscribe` needs its counter-must-follow-by-`seq` pairing only because
    the asker's `request_id` can be reused"** (`IBT/5-domain.md:239-241`).
    - Largely accurate: `service-worker.md:70-76` gives id reuse as the reason.
    - Same-id slot semantics also depend on it: pop-then-skip, `time-lease-boundary.md:31-41`.

14. **`ensure`'s loop condition is a threshold on retractable `progress`, plus a temporal-delta guard
    and a fixpoint guard** (`IBT/5-domain.md:178-183`).
    - Accurate (`observables.py:481-484`; `memoizer.py:45-63, 399-472`).

15. **The `progress` high-water-mark dual is "wrong, for the reason `progress`'s own docstring gives"**
    (`IBT/5-domain.md:150-154`).
    - Accurate (`observables.py:481-484`).

16. **Corpus figures in `IBT/5-domain.md:251-282` and `IBT/open.md:7`.**
    - The figures: 823/821 logs; 2.5M records; 0.34%; 0.072%; 1,714/1,719 status cells; 16 guard
      sites; 24 names; 2,743 stops; 1,140 terminated; 411 lacking `final_step`; 1,643 late values.
    - They come from consumer corpora that aren't in this repo. They can't be verified here.
    - IBT flags the 821-vs-823 discrepancy itself (`5-domain.md:282`).
    - A different census in `backlog/index.md:35-36` counts 1,129 `launcher.terminated` across
      2,531 logs, against IBT's 1,140 across 2,300 (`open.md:69-71`). They are probably different
      dates or corpora; unreconciled.

17. **The quote attributed to `prolog-query-layer.md`**, "pass the probe result in as a parameter
    rather than calling out" (`IBT/1-logic.md:284-286`).
    - Accurate (`backlog/prolog-query-layer.md:64`).

18. **Shipped's description of IBT is outdated.** `backlog/index.md:111-120` says IBT is
    "relational, demand-driven, identity in columns … The forged verdict, the claim cascade, the
    unaimed heartbeat, #39 and the whole `episode-aim` cluster all become a column or an FK. The
    claim becomes `CREATE UNIQUE INDEX … WHERE status='live'` … the halt dissolving into
    *withdrawing demand*." The current IBT docs differ on every point:
    - They describe a monotone store of polarised literals.
    - They withdraw the table-and-FK measurement: "taken against a table-and-FK schema this design
      no longer contains" (`IBT/decisions/0-substrate.md:38-40`).
    - They say "**The halt does not dissolve**" (`IBT/README.md:182-184`).
    - They say forgery defects (which #39's measured cases are) are not fixed (`IBT/README.md:96-100`).

19. **The README's link targets.**
    - `IBT/README.md:197-199` calls `../demand-driven-reads.md` stale. That is self-flagged and
      consistent with that file's SQL LEFT JOIN framing (`backlog/demand-driven-reads.md:14-25`).
    - `dead_ends/per-episode-loglets.md` points to `backlog/episode-correlation.md`, which does not
      exist (shipped-side drift, noted under C11).

20. **Shipped-internal drift that a reviewer might trip on.** These are claims about shipped by
    shipped.
    - **`Heartbeat.t`:** `specs/ensure-until-condition.md:113-116` says it was rejected as a
      converged decision. It is required in `lifecycle-v0.4` (`specs/observer-clock.md:84-99`).
    - **Episode rewind:** `specs/observables.md:114-119` says the orphaned branch drops out;
      `observables.py:535-541` says it does not.
    - **The value schema's description** still names `attach()/open_channel()`
      (`protocol/value-v0.2.schema.json`).
    - **The dead-ends index** still describes `history()` raising "stickily and forever"
      (`dead_ends/index.md`, failure-detector entry). That raise was deleted
      (`backlog/value-plane-divergence-resolution.md`). `cross-host-claim-gate.md:76-83` notes this.

21. **"Content-addressed placement … concluding absence from ownership is not [fine]"**
    (`IBT/1-logic.md:159-162`).
    - This is not a claim about shipped, but it reads directly on shipped practice. In shipped,
      `RunNotFound` at the content address is the "launch it" signal
      (`specs/channel-locators.md:113-116`), and "rid → exists" is a path stat (`store.md:341-342`).
    - Reviewers should decide whether that matters under shipped's one-home premise. The birth CAS
      arbitrates any resulting double spawn.

---

## 5. Things I could not determine

- **Whether IBT has a durable store at all.** It drops the central store (`open.md` 5) but lists
  persistence as a library job (`5-domain.md:164-165`). It specifies neither.
- **How IBT's single claim CAS coexists with no central store** (C2).
- **Who mints episode and attempt ids in IBT**, and whether doing so needs the claim (C3).
- **How a poster learns of rejection or refusal in IBT** (C10).
- **IBT's answer to cold third-party freshness** (C15).
- **Measurements cited by IBT for which no artifact exists in the readable tree** (§4, items 4 and
  16). They may live in the excluded review documents; I didn't look.
