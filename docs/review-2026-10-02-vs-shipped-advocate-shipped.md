# Shipped runstate vs if-built-today: the case for shipped

Advocate's brief for **shipped runstate**. Built 2026-10-02 against branch `spec/episode-aim`, HEAD
`88ba806`. Every verdict is stated **from if-built-today's (IBT's) point of view, relative to
shipped**, as the rubric asks.

**What I read.**
- The neutral map, in full.
- All of `docs/backlog/if-built-today/`, including `decisions/`.
- `docs/design-v0.2.md`, in full.
- The shipped specs, dead ends, backlogs and code paths cited below. I re-read each one I rely on
  rather than trusting the map.
- I did not read the excluded files.

**Two ground rules I applied.**
- **Migration cost is not counted.** The owner's no-warts rule says migration cost is never a
  tiebreaker. "IBT is a rewrite" (`IBT/5-domain.md:107`) is therefore **not** used anywhere below.
- **Unbuilt risk is counted separately**, as the rubric allows. It is the risk of failure modes
  that only building reveals. In this repo that risk has a measurable base rate (see §4).
- Following `CLAUDE.md` §"But a census bounds APPLICABILITY and COST — never soundness", consumer
  counts are used only for **priority and applicability**, never to refute a design.

**Notation.** Paths follow the map: `IBT/x.md:L`, `design §N:L`, and `specs/`, `backlog/` and
`dead_ends/` are under `docs/`.

---

## 1. Verdict table

| # | concern | verdict (IBT vs shipped) | conf. | one-line reason |
|---|---|---|---|---|
| C1 | Run identity, naming, placement | **worse** | medium | IBT borrows shipped's identity recipe, but has no placement, home or existence test. Shipped's placement recipe was earned by marker rot and the custody bug. |
| C2 | Claim / single-spawn / CAS | **worse** | high | IBT uses the shipped CAS "by reference", yet drops the central store a CAS needs. Its claim record, how episodes are minted, and where the shared frontier lives are all unspecified. |
| C3 | Episodes and attribution | **improves** | medium | "Identity is data" is the right principle, and shipped's own defect history proves it. Shipped already applies it on two tiers and has a costed proposal for the third, and IBT prices none of that prototype's costs. |
| C4 | Liveness and failure detection | **worse** | high | IBT lists cross-host liveness as unsolved, with no cascade and no heartbeat. Shipped has four tiers plus a definitive Postgres lock that IBT's description overlooks. |
| C5 | Completion and negative information | **alternative** | medium | IBT's told `¬Q` is more expressive, but it covers only intrinsic completion. The commonest terminal, preemption, is IBT's Open 1. Shipped's `completed` bit already separates exhaustion from falsity. |
| C6 | Terminal verdict | **worse** | medium | IBT has no vocabulary or projection. The "retracted verdict" it criticises is shipped's designed semantics, under another name. |
| C7 | Stopping a run | **worse** | high | IBT leaves the operator halt unsolved and `stop` undefined. Shipped's stop is exactly-once across episodes and can be sent before any worker exists, which identity-as-data cannot yet address. |
| C8 | Demand, subscriptions, condition algebra | **alternative** | medium | IBT's question language is more expressive, but it costs a CLP solver, a coverage checker and a term index, and it has no observer-cadence sampling. Neither side's version is used by any consumer. |
| C9 | Leases, lazy launch, "still wanted" | **improves** | medium | IBT's lease (renew by re-asking; withdraw by revealing a committed secret) removes positional pairing and several shipped mechanisms. The scheduler it relies on is unspecified. |
| C10 | Request acknowledgement and refusal | **worse** | high | Shipped has the `consumed_seq` watermark plus `nak`. IBT has nothing beyond reject-on-arrival, while saying itself that liveness "still needs an acknowledgement". |
| C11 | Value plane | **improves** | medium | Nothing merges, and keying by episode makes every value attributable. Shipped's last-write-wins can return "a series no single execution produced", and a displaced writer wins cells (reproduced, unfixed). |
| C12 | Memoisation (`ensure`, `history`) | **worse** | medium | The "store as cache" is elegant, but IBT delegates both guards that `ensure` needs, adds an unsolved relaunch-forever risk, and loses single-spawn deduplication. |
| C13 | Derived runs and the relational layer | **not comparable** | medium | Shipped's recipes answer where runs live and who roots them. IBT's rules answer how demand propagates, which is the declarative graph shipped deliberately declines. |
| C14 | Reclamation, GC, retention | **alternative** | medium | Both retain every produced record of a dead producer. IBT states the invariant more crisply; shipped has the working recipe. |
| C15 | Time and clocks | **worse** | high | IBT's "absolute time is needed for nothing" plus "monotonic local stamps" re-creates the defect `observer-clock.md` fixed: dead runs painted live for 12–21 days. IBT cites that spec's *replaced* design as its precedent. |
| C16 | Write authority, provenance, dispute | **new (ibt)** | medium | Dispute and readjudication are new. They depend on provenance, which IBT says is unbuilt. Enforcement is identical on both sides, and no consumer has asked. |
| C17 | Wire format and schema discipline | **worse** | medium | IBT has a grammar only: no encoding, no versioning, and canonicalisation obligations it has not met. Its typed metrics are a genuine plus. |
| C18a | Backends, persistence, conformance | **worse** | high | IBT has no backend, no persistence spec and no conformance suite. Shipped has three backends and the "file beside the run" bet that its consumers rely on. |
| C18b | Order-free transport (new split) | **new (ibt)** | medium | Unordered, lossy delivery suffices for all but the claim. But "nothing reads seq" is contradicted by IBT's own fold port, and no consumer is in that regime. |
| C19 | Observer plane: folds and summaries | **alternative** | medium | IBT's split between derivation and report, and its summary tiers, are conceptual gains. By IBT's own measure, eight non-mechanical fold rewrites follow, one with a trap. |
| C20 | Launching and orchestration helpers | **new (shipped)** | high | Launchers, the handle grammar, launch-id correlation, reaping, the deciders, `sweep` and `broadcast`. IBT delegates all of them. translation uses them. |
| C21 | Artifact plane | **not comparable** (tie) | high | Both sides explicitly leave it out, in nearly the same words. |
| C22 | Generic third-party reading (new) | **worse** | medium | IBT's signature is agreed at deployment and checked on receipt, so a viewer cannot read a metric it was not deployed with. Shipped's open `name` serves a code-free viewer, which is the planned cockpit. |
| C23 | Worker programming model (new) | **new (shipped)** | medium | Safe points, `steps`/`serve`/`tick`/`retire`, the `set`/`emit` split, and the stop level. IBT says nothing about the producer's loop, and this is where the consumers' call sites are. |

**Tally (IBT vs shipped).**
- worse: 11 (C1, C2, C4, C6, C7, C10, C12, C15, C17, C18a, C22)
- improves: 3 (C3, C9, C11)
- alternative: 4 (C5, C8, C14, C19)
- new (ibt): 2 (C16, C18b)
- new (shipped): 2 (C20, C23)
- not comparable: 2 (C13, C21)

Most of the "worse" verdicts are **specification debt**, not refutation. They would move to
alternative if IBT specified the missing mechanism without breaking its commitments.

The exceptions are verdicts where IBT's *stated* position conflicts with a measured shipped lesson:
- **C15** (time);
- **C2** (no central store, yet a CAS);
- **C7** (the stop addressed before any episode exists).

---

## 2. The concerns

### C1. Run identity, naming and placement — **worse** (medium)

**The identity concept is shared.**
- **[doc]** IBT takes shipped's identity recipe by reference: "`R` is a content-addressed run id …
  the pattern is runstate's, the choice of inputs the user's" (`IBT/5-domain.md:38-39`). Nothing to
  compare there.
- **Credit to IBT.** It states sharply when a key is honest: key by episode, or never vouch, for
  nondeterministic runs (`IBT/5-domain.md:43-50`). Shipped states the same hazard only as a
  precondition: target-independence, "the one place extend can silently corrupt reuse"
  (`specs/run-episodes.md` Decision 4).

**Placement is earned, and IBT has none.**
- **[defect]** Shipped's placement recipe exists because of real incidents (`specs/store.md:64-91`):
  - the completion-time `.run_id` marker rotted to 3/2052 coverage;
  - `rm -rf E1` destroyed a run that E2 depended on.
- **[machinery]** IBT has no layout, so it has neither those bugs nor their fix. Yet it calls
  persistence a library job: "a store dies with its process" (`IBT/5-domain.md:164-165`). The
  custody question returns the moment it is built.

**IBT's rule against concluding absence makes its own existence test unspecified, without
indicting shipped.**
- IBT says "concluding absence from ownership is not [fine]" (`IBT/1-logic.md:159-162`).
- Shipped does exactly that: `RunNotFound` at the content address means "launch it"
  (`specs/channel-locators.md:113-116`).
- Under shipped's one-home premise (design §14:315) the inference is sound. The address is the
  only home, and a false "not launched" costs at most a wasted spawn that loses the birth CAS.

**What would change it:** IBT specifying where a run's records persist and how existence is
decided. With that, this becomes alternative.

### C2. The claim — **worse** (high on specification; the guarantee would be identical if adopted)

**IBT's claim is shipped's CAS, by reference.**
- **[machinery]** "Order is needed in exactly one place, the **claim**, where `send(expected_seq=)`
  is a compare-and-swap" (`IBT/5-domain.md:117-119`). The README calls `specs/write-authority.md`
  "unchanged by any of this" (`IBT/README.md:203-207`).
- **[defect]** Every piece shipped adds around the CAS is earned:
  - **Pre-check before the claim.** "ORDER IS LOAD-BEARING, and a consumer depends on it off-repo"
    (`worker.py:108-114`).
  - **The loser never acts.** A loser's reaped `terminated{0}` forged the run's verdict
    (`specs/launcher-record-identity.md:20-28`), and `stopped()` lacked the loser guard
    (`specs/lazy-launch.md:129-135`).
  - **The death CAS.** "Episodes are CAS-claimed at both ends", so a subscribe racing the death is
    never orphaned (design §7:178).
  - **Claim before allocating.** "The loser of a race must not OOM the victor off the GPU"
    (`specs/lazy-launch.md`, "Prologue guidance").
  - IBT specifies none of these.

**IBT contradicts itself on where the claim lives.**
- **[doc]** IBT drops the central store: "Each agent holds a lagged local copy … the 'global'
  store is the union" (`IBT/open.md:46-53`). Yet it keeps `send(expected_seq=)`.
- A CAS needs a shared frontier. Measured in shipped: with per-segment `seq`, "**both claims
  win** … A CAS arbitrates only writers who share a frontier" (`dead_ends/per-episode-loglets.md`).
- So IBT needs at least a per-run single arbiter for the claim, which is shipped's §14 premise
  scoped to one topic. It never says so.
- The README hints at a claim keyed on a semantic value rather than `seq` (`IBT/README.md:206-207`).
  That is still one arbiter per key. And "attempt n+1" must first read attempt n, an argmax, so the
  contention is the same.

**[unbuilt]** The map's list stands: the claim record's shape, episode minting, the roster and the
frontier's location are all open.

**Concession.** Shipped's guarantee is narrow: "single-writer holds at the claiming instant only"
(`specs/write-authority.md`, rev 4). Three mechanisms to widen it were refuted, so neither side is
stronger *on the guarantee*. IBT inherits the guarantee and the residual harm alike, since both
say the artifact plane is where a double-live worker's damage lands.

**What would change it:** IBT naming where the claim's frontier lives and what the claim record
is. Then this is alternative.

### C3. Episodes and attribution — **improves** (medium)

**I concede the principle.** IBT's second commitment is right, and shipped's record is the
evidence.

- **[defect]** Position-derived attribution has a long measured defect list:
  - forged verdicts from a late reap and from a claim-race loser
    (`specs/launcher-record-identity.md:12-28`);
  - the `progress` splice, where "`ensure` return[ed] a series spliced across both episodes **as
    complete**" (`observables.py:470-474`);
  - a post-terminal `emit` overwriting the successor's cell, measured as 101.0 replaced by −999.0
    (`worker.py:219-226`);
  - the claim cascade and the unaimed heartbeat (`backlog/episode-aim.md` table);
  - #39's author-blind discharge (`observables.py:445-456`).
- **Shipped's own diagnosis converges on IBT's principle.**
  - `dead_ends/per-episode-loglets.md` concludes "correlation, not segmentation".
  - `_launcher_terminal` says "Position cannot do this job" (`observables.py:202-210`).
  - IBT makes this a commitment from day one, where shipped retrofits it tier by tier.
- **[elegance]** IBT has one rule for every record kind. Shipped has three tiers attributed three
  ways:
  - launcher records by launch id (launcher-v0.3+);
  - control records by `request_id`;
  - lifecycle `stopped` and `heartbeat` by position.

**IBT has not priced what shipped's prototype of this principle found.**
- **[unbuilt]** `backlog/episode-aim.md` (revision 2) *is* identity-as-data for the lifecycle tier.
  Building it found:
  1. **Fold cost.** "The latest heartbeat naming this claim" is a range read: 2124× slower on
     SQLite at 10k beats. Only latest-then-verify rescues it (`:103-118`). IBT's
     `heartbeat(episode2, 500)` has the same read shape, and IBT says nothing about it.
  2. **The startless run.** A stop staged on a never-started run has no episode to name. It breaks
     a live consumer, `mycooc/run_experiment.py::resume_fanout` (`:121-132`). IBT inherits this and
     does not mention it (C7).
  3. **A selector fracture.** Strict verdict selection and tolerant measurement selection can no
     longer share one helper: 36 measured `DID NOT RAISE` (`:76-82`).
  4. **Migration.** A backfill "cannot be correct, only uniform" (`:172-174`). (Migration cost is
     excluded; this is listed only because it shows the information is unrecoverable.)
- IBT's `heartbeat(episode2, 500)` presupposes that `episode2` was minted and that a reader can
  find the *current* episode.
  - If the minted id is the claim's `seq`, IBT's commitment *is* episode-aim.
  - If it is random, "which episode is current" is an argmax, which IBT classes as a tier-3 report
    (`IBT/4-aggregation.md:12`).
  - Either way the read path is unspecified.

**The headline calibration measures a defect class this commitment does not fix.**
- **[consumer]** The 11 of 37 figure (`IBT/README.md:90-94`) counts mostly third-party claim
  releases: the reclaim tool forging `lifecycle.stopped` (`backlog/cross-host-claim-gate.md:217-228`).
- Episode-aim measured that aim does not close that route: "a forger reads the aim in one call"
  (`:20-37`). IBT's README concedes the same (`:96-100`).
- So the figure is evidence for the *principle* only through the other two examples, which come
  from a prototype, not from the corpus count.

**Shipped can absorb the principle without a redesign.**
- Episode-aim revision 2 costs `+181/−24` and needs no substrate change.
- Launch-id correlation already shipped (launcher-v0.3).
- The improvement is real, but it is not specific to IBT's vehicle.

**What would change it:** shipping episode-aim plus a claim-seq stamp on values. Then this is
alternative.

### C4. Liveness and failure detection — **worse** (high)

**IBT leaves liveness unsolved.**
- "**Cross-host liveness.** You still need a handle and a probe, and it still abstains off-host"
  (`IBT/README.md:177-178`).
- No heartbeat schema, staleness rule or detector cascade is specified.

**Shipped has a full detector, and IBT's description of it is wrong.**
- **[machinery]** Shipped has a four-tier, opt-in detector (design §8:193-199;
  `watcher.py:243-295`). It includes tier 3b, the Postgres episode advisory lock: "**definitive
  cross-host death detection**" (`specs/channel-postgres.md:152`; `watcher.py:85-116`).
- **[doc]** So IBT's "it still abstains off-host" is false for shipped's Postgres observation path.
- IBT's other description, "Exhaustion arrives three ways … only the last is dependable"
  (`IBT/5-domain.md:185-189`), omits both heartbeat staleness and the lock (map, claim 8; confirmed).

**Each tier is earned by an incident.**
- **[defect]**
  - **Zombies read alive to `kill -0`.** Hence the mandatory per-cycle `reap()`
    (`specs/lazy-launch.md:81-87`).
  - **`resolve()` probed the local pid table for any host's handle.** That is a false dead that
    could double-claim, hence the hostname scoping (design rev 10; `specs/lazy-launch.md`).
  - **Five mycooc runs dead 12–21 days were painted live** (`specs/observer-clock.md:43-46`).

**IBT's principles are already shipped's rules, so they bring no conceptual gain here.**
- "Abstention must not become a stored verdict" is shipped's "**⊥ never authorises an
  irreversible act**" (`backlog/cross-host-claim-gate.md` §8.2).
- It is also shipped's "Time never arbitrates a claim or a death verdict"
  (`specs/observer-clock.md:138`).
- And `live_episode`'s docstring has "only a later `lifecycle.stopped` and a `resolve()`-dead
  handle release a claim" (`observables.py:138-146`).

**Concession.** Shipped's cross-host *claim gate* is unconverged
(`backlog/cross-host-claim-gate.md` status line).
- A consumer built `reclaim_experiment.py` at a cost of about 20 GPU-hours (`:357-358`).
- IBT does not fix that either.
- Both sides leave the claim gate open; only shipped has the observation tiers.

**What would change it:** IBT specifying a detector for its regime. That would be a heartbeat-like
record plus a staleness rule a cold party can apply, which runs straight into C15.

### C5. Completion and negative information — **alternative** (medium)

**IBT's gains are real.**
- A told negative region settles an unbounded question with a finite cover
  (`IBT/5-domain.md:79-89`).
- Questions with an explicit `∀` (`IBT/3-questions.md:217-221`).
- Shipped has no `¬Q`. IBT's census (`IBT/open.md:71-76`) agrees with the schema set in
  `protocol/`.

**But IBT's `¬Q` covers only the uncommon terminal.**
- **[doc]** A producer halted short of convergence "knows nothing about the rest and must post
  nothing about it … this is the exhaustion question, and it needs a shape that is not `¬Q`"
  (`IBT/open.md:5-11`).
- Preempted and chunked runs are the common terminal: `ensure` re-drives `preempted` by design
  (`memoizer.py:369-385`). For them IBT has **no record shape**.
- Shipped's `lifecycle.stopped{completed=False, final_step}` is exactly that shape.

**Shipped already encodes IBT's distinction between exhaustion and falsity, and the encoding was
earned.**
- **[defect]** IBT's test is "could a third party post this knowing only that the process died? …
  exhaustion yes … falsity no" (`IBT/2-polarity.md:200-205`). Shipped encodes it twice:
  - `completed` is the worker's opt-in claim of intrinsic completion; otherwise the halt is
    `preempted` (`specs/completed-opt-in.md`).
  - `worker_completed` separates the worker's claim from a launcher's exit 0
    (`observables.py:299-323`).
- Both were earned:
  - the completed-by-default footgun silently truncated `ensure` (`specs/completed-opt-in.md:11-21`);
  - runstate#30, an exit 0 read as COMPLETED (`specs/preempted-vs-completed.md:47-52`).

**Credit to IBT.** With keying by episode, `at(E, S, M)`, a preempted episode *can* honestly post
its own negative tail, because that episode will never write again. That is a clean account, but it
is the C3/C11 keying choice, not a C5 mechanism.

**[consumer]** `ensure` needs only prefix settledness ("is `[0, N)` covered?"), which `progress`
plus the `completed` bit provide. No consumer has asked for unbounded or non-prefix settledness.

**What would change it:** IBT specifying the exhaustion record. Or a consumer that needs
negative information over regions.

### C6. The terminal verdict — **worse** (medium)

**IBT has a principle but no vocabulary.**
- The verdict is "a join of two partial observers … a *report*" (`IBT/5-domain.md:14-15`).
- There is no vocabulary, projection or tiering. `verdict(Outcome, FinalStep)` appears only as an
  illustration (`IBT/1-logic.md:440-441`).

**Shipped's vocabulary was built from defects.**
- **[defect]** The closed `Outcome` enum has no `success` bool (design §9:240-253;
  `observables.py:71-114`).
- It came from vocabulary repairs:
  - `success` was removed;
  - `stopped` became `preempted`;
  - `Stopped.reason` was removed.
- `worker_completed` exists because an `sbatch` "exits 0 at *submit* time"
  (`observables.py:313-318`).
- Verdict folds raise `MalformedRecordError` rather than guess.

**IBT's criticism of shipped's verdict describes the same thing in different words.**
- **[doc]** IBT says `peek_terminal`'s emptiness test was "measured to **retract a published
  verdict** when a later claim arrives" (`IBT/5-domain.md:147-148`).
- In shipped that is designed: "a terminal stands until a new episode claims"
  (`observables.py:264-269`), and there is "No 'done forever' concept"
  (`specs/run-episodes.md:41-45`).
- In IBT the per-episode verdict would be permanent, and "the run's current verdict" would be an
  argmax report. That is the same thing said differently.

**What would change it:** IBT specifying its verdict vocabulary and projection. Then this is
alternative.

### C7. Stopping a run — **worse** (high)

**IBT has no operator stop.**
- "**The halt does not dissolve** … That needs a write and an authority rule, and always did"
  (`IBT/README.md:182-184`).
- A `stop` relation is mentioned (`IBT/3-questions.md:194-197`) but never defined.
- IBT's only discharge mechanics are a port of *shipped's* fold, and that port compares `seq`:
  `discharged(C) :- stop(C), stopped(S), S > C` (`IBT/5-domain.md:134`).

**Shipped's stop discharge was converged independently three times.**
- **[defect]** It fixed S1 (the lost edge), S2 (poisoned replay), S3 (a resume dying at its first
  step) and S4 (the clobber). Three independent reviews converged on it, and two of them
  independently refuted episode-start fencing (`specs/stop-discharge.md:1-34`).

**IBT inherits episode-aim's startless-run problem here.**
- **[unbuilt]** Shipped's S2 is a stop sent while the run is down. It is honoured exactly once by
  the next episode (`specs/stop-discharge.md`, scenario matrix). The reason given: "the durable
  channel's defining idiom is addressing a run before any worker exists."
- Under identity-as-data, a stop must name what it stops. Before any episode exists there is
  nothing to name. This is exactly episode-aim's startless run, and it has a live consumer
  (`backlog/episode-aim.md:121-132`).
- A run-scoped stop is the *durable ceiling*, which is a different semantics and is unsolved on
  both sides (`backlog/run-scoped-halt.md`; `specs/control-target.md` R6).

**Concession.** Shipped's stop has known defects:
- discharge is author-blind and body-blind (#39);
- the durable-ceiling cell is empty;
- `lifecycle.stopped` does five jobs, so a consumer forges it to get one
  (`backlog/lifecycle-stopped-unbundling.md:3-20`).
- **[elegance]** IBT's one-relation-per-concern style would unbundle those jobs by construction.
  But it specifies none of the five replacements.
- IBT's self-withdrawal is a real gain; it is credited under C9.

**What would change it:** IBT defining `stop` and its discharge without comparing positions, and
saying what a stop addressed to a never-started run names.

### C8. Demand, subscriptions and the condition algebra — **alternative** (medium)

**For IBT.**
- **[elegance]** IBT has:
  - quantified questions with per-variable binders;
  - settledness read straight off statuses;
  - rule-derived questions (`IBT/3-questions.md:5-55`).
- It avoids shipped's positional answer fold, which exists only because a `request_id` can be
  reused (`specs/service-worker.md:67-76`).
- **[defect]** Shipped's algebra has scars:
  - a delta `every` is non-monotone when replayed (`backlog/memoizer-index-algebra.md:35-66`);
  - time atoms make a subscription episode-scoped (`specs/time-lease-boundary.md`);
  - count budgets were refunded per episode;
  - the empty-window check punts on an `any` in `from` (design §6:153).

**For shipped.**
- **[machinery]** IBT's read side is its own "honest headline cost … it dwarfs the fold rewrites"
  (`IBT/5-domain.md:121-126`): a per-functor term index, a CLP solver in the read path, and a
  coverage checker.
- **[unbuilt]**
  - The constraint domain and its algorithm are open (`IBT/open.md:16-19`).
  - Constraints need a canonical form, "or … a producer rule … never fires … which costs liveness"
    (`IBT/3-questions.md:357-360`).
  - The admission rule is open (`IBT/open.md:94-96`).
- The map's counts (about 16 against about 15) are roughly level. IBT's three external mechanisms
  are each a substantial component.
- **New (shipped): observer-cadence sampling.** A subscription samples the `set` register, so a
  worker reports a value only when someone is watching (`worker.py:196-201`).
  - IBT puts the delta and time readings of `every` outside its model, as "firing schedules, which
    is control" (`IBT/open.md:84-89`).
  - It names this exact gap itself (`IBT/5-domain.md:197-198`).

**[consumer]** No consumer has ever sent `control.subscribe` (owner auto-memory, verified
2026-07-16; not recorded in the repo). This is priority information only:
- the comparison barely touches current consumers;
- it refutes neither side.

**What would change it:** a consumer adopting subscriptions or quantified demand, or IBT
specifying its constraint domain and admission rule.

### C9. Leases, lazy launch and "still wanted" — **improves** (medium)

**I concede the lease design is better.**
- IBT's lease (`IBT/5-domain.md:200-249`):
  - a renewal is the same question asked again;
  - an asker withdraws by revealing a secret whose hash it committed in the lease;
  - withdrawal is therefore order-free, cannot be forged, and cannot cancel another asker's
    lease.
- **[machinery]** Shipped's leasing needs all of the following:
  - expiry counter-records;
  - the positional answer fold;
  - the void at an episode boundary, with pop-then-skip;
  - re-anchoring at most once;
  - the bound of at most two relaunches;
  - `retire()`'s death CAS;
  - exact ref-counting;
  - register-before-reap
  - (`specs/service-worker.md`; `specs/time-lease-boundary.md`; design §7:178).
- The map counts these in C8 and C9 separately. Taken together shipped has about 15 moving parts,
  against IBT's about 10 plus 2 unspecified (correction 11).
- **[defect]** Each shipped piece is a scar:
  - the ghost-lease relaunch loop;
  - resurrected leases;
  - count refunds;
  - duplicate naks.
- IBT's design avoids the conditions that produced them.
- The death CAS exists because registration state lives *in the worker*. IBT moves "still wanted"
  to a scheduler, so a renewal racing a producer's exit costs a relaunch, not an orphaned
  registration.

**Caveats.**
- **[unbuilt]** The scheduler that reads leases is unspecified: "a scheduling policy, not part of
  this layer" (`IBT/3-questions.md:203-209`).
  - Shipped's waker lessons will recur there: mandatory reaping of zombies, the demander as the
    waker, ThreadLauncher's degeneracy (`specs/lazy-launch.md:60-140`).
  - So does the "undemanded bootstrap" that refuted "lazy as the primitive"
    (`specs/service-worker.md:268-272`). A bare-spawned producer has no `asked` record.
- Durable demand as a lease with no end inherits Open 10, relaunching forever.
- IBT's accepted cold-attach cost (`IBT/5-domain.md:212-214`): a scheduler that attaches late reads
  a dead asker's last renewal as fresh. That is comparable to shipped's bound of two relaunches.
  But IBT's analogy to `time-lease-boundary.md` is loose (map, claim 12).
- **[consumer]** No consumer uses leased demand (owner memory).

**What would change it:** specifying the scheduler and finding that it needs as much machinery as
shipped's `ensure_served` plus the activator recipe.

### C10. Request acknowledgement and refusal — **worse** (high)

**Shipped has acknowledgement and refusal.**
- **[machinery]** The heartbeat's `consumed_seq` is a registration watermark. With `nak` and the
  answer-first `await_consumed`, it is "relocated, not eliminated" (design §6:153, §13:301).
- **[defect]** Earned by:
  - duplicate naks per episode;
  - the deadlock on a retire win, before `await_consumed` became answer-first
    (`specs/service-worker.md:332-337`).

**IBT has only reject-on-arrival, and its regime needs acknowledgement more.**
- Reject-on-arrival is by checks on the message alone. How a poster learns it was rejected is
  unspecified.
- IBT says itself that "a lost demand and a slow handler leave a querier in byte-identical states,
  so *liveness* still needs an acknowledgement" (`IBT/1-logic.md:221-223`).
- It also says "admission control still needs an explicit mechanism" (`IBT/3-questions.md:370-371`).
- On the lossy, unordered transport IBT targets, the acknowledgement matters *more* than on
  shipped's ordered log.

**What would change it:** IBT specifying an acknowledgement and refusal record.

### C11. The value plane — **improves** (medium)

**I concede the value plane.**
- **[defect]** Shipped's last-write-wins per `(name, step)` is "a **convergent merge** … not a
  consistent snapshot … a caller can receive a series no single execution produced"
  (`observables.py:535-542`).
- A displaced writer wins cells, and `ensure` returns the spliced series. This was reproduced
  (`dead_ends/per-episode-loglets.md`, "Where it goes instead") and is not fixed.
- Episode-aim deliberately excludes `value` ("`value` is the deliberate exception";
  `backlog/episode-aim.md` §"What it still does not fix").
- In IBT nothing merges (`IBT/0-substrate.md:14-18`), and keying by episode makes every value
  attributable to one execution. A disagreement stays readable instead of being silently
  resolved.
- Typed per-metric constructors reject a misspelt metric on arrival (`IBT/5-domain.md:23-36`). That
  is the owner's own static-checkability rule.

**Shipped's counter, which narrows but does not reverse the verdict.**
- G1's reachability argument (`backlog/value-plane-divergence-resolution.md:36-77`) shows that for
  every divergence reachable *through `ensure`* in contract, take-the-latest returns the value an
  authoritative-attempt rule would.
- The silently-wrong residue is out of contract (`backlog/cross-host-claim-gate.md` §5).
- IBT still needs a report to pick *one* trajectory for `ensure`, and does not say which (map, C11
  open question).

**What would change it:** shipped stamping value records with the writer's claim `seq`, the remedy
`dead_ends/per-episode-loglets.md` measured. Then this is alternative.

The cost to generic readers of IBT's typed signature is split out as C22.

### C12. Memoisation (`ensure`, `history`) — **worse** (medium)

**`ensure` is what the consumers actually call.**
- **[consumer]** `ensure` and `history` are the consumer-facing path (translation's `ensure_run`;
  mycooc's own subprocess producer, per owner memory).
- **[defect]** Its guards are scar tissue (`memoizer.py:399-472`):
  - `RunFailedError`;
  - `RecordlessExitError`, from runstate#30;
  - `NoProgressError`, made own-spawn-scoped, claim-aware and axis-aware after the claim-window
    spurious raise (`specs/store.md:223-232`), the time-axis false raise, and the R5 storm. The
    comment carries the invariant "a worker that exits CLEANLY has made progress".
  - Not a guard but a seam contract: `extend` must return a handle, never `None`, after a hang
    (`specs/store.md:169-178`).

**IBT keeps the elegant part and delegates the hard part.**
- **[elegance]** "The store was the cache" plus a per-instance residual (`IBT/5-domain.md:91-93`;
  `IBT/3-questions.md:143-171`) is genuinely clean. It also handles overlapping demands that do not
  nest.
- But on prefix windows, the only shape any consumer uses, shipped's `extend(until)` plus resume
  from a checkpoint *is* "produce the residual suffix".
- **[unbuilt]** IBT delegates both guards that `ensure` needs, the temporal delta and the fixpoint,
  to its non-monotone core, unspecified (`IBT/5-domain.md:178-183`).
- It names a relaunch-forever hazard it does not solve (`IBT/3-questions.md:173-178`;
  `IBT/open.md:94-96`).

**IBT gives up single-spawn deduplication.**
- **[machinery]** Two overlapping concurrent demands "can each produce before either sees the
  other's output" (`IBT/3-questions.md:180-183`).
- In shipped, `foreign_episode` makes the second `ensure` wait on the first one's episode
  (`memoizer.py:102-148`).

**What would change it:** IBT specifying both guards and an admission rule.

### C13. Derived runs and the relational layer — **not comparable** (medium)

**They answer different questions.**
- Shipped's store recipes answer *where runs live and who roots them*
  (`specs/store.md:25-71, 234-347`):
  - placement;
  - pointers as GC roots;
  - the provenance register;
  - the rule that an index is a pure cache.
- IBT's rule-derived questions (`IBT/3-questions.md:28-31`) answer *how demand propagates*.
- That is the line shipped deliberately does not cross: "the **declarative graph** — dependencies
  *between* runs" (`layers.md:136-138`).
- IBT does not address layout. Shipped does not do declarative demand.
- No consumer has asked for declarative demand derivation.

### C14. Reclamation, GC and retention — **alternative** (medium)

**Both keep the records that matter.**
- IBT never evicts a produced record whose producer is gone (`IBT/3-reclamation.md:19-23`). That is
  every value a dead worker wrote, which is shipped's full in-log retention (design §12.9:290).
- IBT's freely evictable tier is derived atoms. Shipped stores none: its folds are computed on
  read.

**Where each is ahead.**
- IBT states the invariant more crisply ("eviction, never retraction"; tiers by re-derivability).
- Shipped has the working home-level recipe and its fixes: the WAL mtime lie, solved by
  `last_activity` (`specs/observer-clock.md:49-54`).
- **Both have the same open issue.** Heartbeats are about half of all envelopes, and in-log
  compaction is unconverged (`backlog/in-log-compaction.md`).

### C15. Time and clocks — **worse** (high)

This is shipped's sharpest case, because IBT's stated position re-creates a measured, fixed
defect.

**The defect.**
- **[defect]** The original runstate design put the liveness clock in the observer: "the `Watcher`
  knows when a beacon arrived because *it was there*".
- `specs/observer-clock.md:22` records the result: "It collapses for anyone who attaches later."
- Victim 1 was a wrong verdict: five mycooc runs, dead 12–21 days, painted live
  (`specs/observer-clock.md:43-46`).
- Victims 2 and 3 were a viewer that cannot exist and a GC grace window with no age (`:47-59`).
- Fixed by a required wall-clock `t` on the five dated records (lifecycle-v0.4 and launcher-v0.4)
  and by `last_activity`.

**IBT's position is the replaced design.**
- "Absolute time — *when, in UTC?* — is needed for nothing here" (`IBT/1-logic.md:303`).
- "The local stamp must come from a monotonic source" (`IBT/1-logic.md:309-312`).
- The lease is timed "from local receipt, on its own monotonic clock … matching runstate's original
  liveness design, where the Watcher knows when a beacon arrived 'because it was there'
  ([`observer-clock.md`])" (`IBT/5-domain.md:209-212`).
- **[doc]** IBT cites as precedent the very design the cited spec documents as broken and replaced.

**Shipped rejected exactly these clocks, with reasons.**
- `specs/observer-clock.md:271-276` lists among the "Four clock designs, dead in every proposal":
  - "a **monotonic/stopwatch clock** (no shared origin ⟹ staleness structurally unanswerable)";
  - "**wall anchored to a stopwatch** (`CLOCK_MONOTONIC` does not tick across suspend ⟹ a slept
    machine's beacon reads fresh — the very bug)".

**Where the two agree, and where they do not.**
- IBT's rule that a comparison across observers is a report is compatible with shipped's:
  - "`seq` orders, `t` measures";
  - "Staleness … is a LOCAL inference";
  - "Time never arbitrates a claim" (`specs/observer-clock.md:125-149`).
- The disagreement is narrow and decisive: shipped needs a stamp with a *shared origin* so that a
  cold third party can compute an age, and IBT says that stamp is needed for nothing.

**[consumer]** The cockpit is the next project (`CLAUDE.md`, scope snapshot). Its status and
freshness columns are victim 2.

**What would change it:** IBT admitting a wall-clock observation literal on its beacon-equivalent,
as a dated fact about the past that its own §"Two layers" would permit. Then this is alternative.

### C16. Write authority, provenance, forgery and dispute — **new (ibt)** (medium)

**What is new in IBT.**
- Derived objections, objections to objections, and readjudication without retraction
  (`IBT/3-provenance.md:7-56`).
- Shipped has nothing comparable.

**Why it is not usable yet.**
- **[unbuilt]** "An objection is worth something only where provenance exists"
  (`IBT/3-provenance.md:20-22`).
- Provenance is "the single highest-leverage unbuilt thing here" (`IBT/open.md:12-15`).

**What does not change.**
- Enforcement is identical on both sides:
  - IBT: "Still honour-system" (`IBT/README.md:181`);
  - shipped: `positioning.md`, "Enforced" (atomic appends, total order, one CAS winner).
- **[consumer]** The consumers' real forgery problem, the reclaim tool and #39, is untouched by
  IBT, as IBT says (`IBT/README.md:96-100`).
- No consumer has asked for dispute.
- IBT's unforgeable withdrawal is credited under C9.

### C17. Wire format and schema discipline — **worse** (medium)

**Shipped has a concrete, exercised format.**
- **[machinery]**
  - a concrete JSON Schema stack with `additionalProperties: false`;
  - present-nullable fields;
  - conventions versioned independently;
  - `tests/test_schema.py`.
- **[defect]** The migration doctrine has been exercised:
  - `lifecycle` v0.3 and v0.4;
  - `launcher` v0.3 and v0.4;
  - the removal of `Stopped.reason` (B′).

**IBT has a grammar and obligations it has not met.**
- **[unbuilt]** IBT has a record grammar only (`IBT/0-substrate.md:64-82`).
- Unspecified: the concrete encoding, the envelope, and how signatures are versioned.
- It must canonicalise the encoding for set-semantics deduplication (`IBT/1-logic.md:528-531`) and
  canonicalise constraints (`IBT/3-questions.md:357-360`).
- Cross-language canonicalisation of floats is a known-hard obligation. IBT's own conflict example
  is `0.30000000000000004` against `0.3` (`IBT/5-domain.md:267`).

**Credit to IBT.**
- Typed metrics.
- Polarity as a two-valued field that `additionalProperties: false` can pin
  (`IBT/2-polarity.md:469-473`).

### C18a. Backends, persistence and conformance — **worse** (high)

- **[machinery]** Shipped has:
  - three backends that pass one conformance suite;
  - `seq` that is contiguous and 1-based;
  - a CAS that is one critical section across processes (design §3–4).
- Its positioning bet is "the log is a file beside the run … readable in ten years with `sqlite3`"
  (`positioning.md`, "The bet").
- **[consumer]** mycooc runs SQLite on NFS. Both consumers live with one home per run.
- **[unbuilt]** IBT specifies no backend and no persistence, though it calls persistence the first
  library job (`IBT/5-domain.md:164-165`).

### C18b. Order-free transport (new split) — **new (ibt)** (medium)

**The gain is real.**
- "Unordered, duplicate-tolerant, loss-tolerant broadcast suffices" for everything except the
  claim (`IBT/5-domain.md:114-119`).
- That is a genuine theoretical gain.

**But it is overstated.**
- **[doc]** "Nothing in the semantics reads a sequence number" is contradicted by IBT itself:
  - its own fold port (`IBT/5-domain.md:134`);
  - last-write-wins as "`argmax` over `seq`" (`IBT/4-aggregation.md:12`);
  - "the counter is the cursor" (`IBT/1-logic.md:298-300`).
- The claim needs a shared arbiter in any case (C2).

**Applicability and demand.**
- **[consumer]** No consumer is in this regime.
- Shipped scoped it out deliberately: "the causal regime … is a different protocol, not a later
  version of this one" (design §14:315).
- There is latent interest in shipped's backlog:
  - `backlog/machine-partitioned-logs.md`, whose partial-order gate is exactly what
    identity-as-data would answer;
  - discharge-by-id, deferred until a replicated log exists (`backlog/index.md:238-246`).

### C19. The observer plane: folds and summaries — **alternative** (medium)

**Shipped.**
- Eight public folds.
- The tolerance split: measurement folds skip junk, verdict folds raise.
- Windowing at the Markov boundary, measured: 3461 µs → 92 µs, and a log that was unrepairable
  becomes repairable (design §14:317).

**IBT.**
- **[elegance]** The split between derivation and report, threshold reads, and summary tiers with
  the may-lift are conceptual gains. Contextuality is a real hazard nobody in shipped has named.
- **[machinery]** By IBT's own measurement: "no fold ports as-is … eight rewrites, none mechanical,
  and at least one where the *natural* dual silently reintroduces a bug" (`IBT/5-domain.md:128-157`).
- **[consumer]** No consumer aggregates across agents.

### C20. Launching and orchestration helpers — **new (shipped)** (high)

**What shipped has.**
- **[machinery]**
  - launchers;
  - the handle grammar;
  - launch-id correlation;
  - the honest-corpse reaping rule;
  - `relaunch_if_needed` and `ensure_served`;
  - `sweep`;
  - the `broadcast` barrier with a patience cap.
- IBT delegates all of it: "a scheduling policy, not part of this layer" (`IBT/3-questions.md:209`).

**Who uses it.**
- **[consumer]** translation uses the launchers: all 1,131 `launcher.launched` records in the
  2026-07 census are translation's, each with a `request_id` (owner memory; `backlog/index.md:31-36`).
- mycooc spawns its own processes. The loss bites translation.

**Concession.** The `Launcher` Protocol cannot be typed, because the two `launch` signatures are
disjoint (`backlog/launcher-protocol-typing.md`). That is a shipped wart.

### C21. The artifact plane — **not comparable** (tie, high)

**Both sides leave it out, in nearly the same words.**
- Shipped: "runstate gives no directory" (`specs/run-episodes.md`, Non-goals). It is "where a
  double-live worker's actual damage lands" (`specs/write-authority.md`, end).
- IBT: "Checkpoints on a filesystem remain unmodelled, and remain where a double-live worker's real
  damage lands" (`IBT/README.md:179-180`).

### C22. Generic third-party reading (new) — **worse** (medium)

**Shipped serves a viewer that shares no code with the producer.**
- `topic` is closed and protocol-owned; `name` is open and app-owned (design §4:50-58).
- `runstate-tui` "shares no code with the producer — only the log format" (`positioning.md`).
- The cockpit, the next project, is built on that (`CLAUDE.md`).

**IBT's signature blocks such a viewer.**
- The signature "belongs to the **program** … Agreeing the signature is deployment, not runtime"
  (`IBT/1-logic.md:484-518`).
- It is checked on receipt "because … a peer's message is never trusted".
- Sharing schemas at runtime "is out of scope" (`IBT/5-domain.md:35-36`).
- "If signatures were themselves data, the problem returns unchanged" (`IBT/1-logic.md:515-516`).
- So a viewer deployed before a new metric rejects it, and adding a metric means redeploying every
  agent.

**Concessions.**
- IBT's census finds the name set stable: 24 names, no sort drift, and no new names in the second
  half (`IBT/5-domain.md:271-280`).
- The owner's static-checkability rule favours constructors for known sets.
- IBT considered and declined a middle path that keeps the name as data:
  `metric(Name, Float, Step)` (`IBT/5-domain.md:273-280`).

**What would change it:** IBT keeping metric names as data in a sorted relation, or a runtime
channel for signatures.

### C23. Worker programming model (new) — **new (shipped)** (medium)

**What shipped has.**
- Safe points.
- `steps(start, total)`, `serve()`, `tick()` and `retire()`.
- The `set`/`emit` split.
- `stop_pending` as a level.
- Guidance to claim before allocating.

**What IBT says.** "An agent may be written in anything that can build and read terms and post
records" (`IBT/1-logic.md:13-18`). There is no convention for a cooperative stop at a safe point, or
for resuming from a checkpoint to extend a run.

**[consumer]** This is where the consumers' call sites are:
- mycooc drives `w.tick(...)` directly (owner memory);
- translation workers are `for _ in w.steps(total=1)` (`specs/write-authority.md`, revision 3,
  refutation 2).

---

## 3. Corrections to the map

**Shipped-side doc drift the map missed.**

1. **`design-v0.2.md` is itself stale on bodies the map treats as settled.**
   - §7's table (`:169-171`) says `started` is `{handle, attached_at?}` and the heartbeat has "No
     embedded timestamp", and §8's table omits `t` (`:184-187`). But `protocol/lifecycle-v0.4` and
     `protocol/launcher-v0.4` require `t`.
   - §10 (`:272`) still says `lifecycle-v0.3` is frozen.
   - §9 (`:262`) says an episode rewind "resolves to the as-resumed trajectory". That contradicts
     `observables.py:535-541`.
   - The map lists only `specs/observables.md` and `specs/ensure-until-condition.md` for these
     drifts. The design doc, which `CLAUDE.md` names as a source of truth, carries them too.
     **[doc]** against shipped.

**Map open questions that are actually answered.**

2. **C15's open question is not open.** The map says whether IBT answers cold-attach freshness "is
   not explicit". It is:
   - IBT declines absolute time (`IBT/1-logic.md:303`);
   - it requires monotonic stamps (`:309-312`);
   - it cites `observer-clock.md`'s *replaced* arrival-clock design as its model
     (`IBT/5-domain.md:209-212`).
   - `observer-clock.md:271-276` rejected monotonic clocks for exactly this. It is a direct
     conflict, not an ambiguity.

**Map characterisations that need qualifying.**

3. **C5's summary-table entry for IBT, "mechanism (told falsity, settledness)", overstates
   coverage.** `¬Q` covers intrinsic completion only. Preemption, shipped's commonest terminal, is
   Open 1 and unspecified. Shipped's `completed` bit already encodes exhaustion ≠ falsity.
4. **C7 misses the stop sent before any episode exists** (`specs/stop-discharge.md` S2, the
   "blip"). Under identity-as-data this is episode-aim's "startless run"
   (`backlog/episode-aim.md:121-132`), with a live consumer.
5. **C3 does not carry episode-aim's prototype costs onto IBT.** The costs are:
   - the heartbeat range read, 2124× slower without latest-then-verify;
   - the strict/tolerant selector fracture;
   - the startless run;
   - a backfill that "cannot be correct".

   They are costs of IBT's identity commitment too, and should appear as IBT U-items. A realistic
   IBT count for C3 is about 9 plus unknowns, not about 5.
6. **§1, "Semantics that tolerate replication with no central store", needs a qualifier.**
   - The claim needs a shared arbiter (C2).
   - Lease renewals need `N` to stay distinct.
   - IBT's own fold port reads `seq`.

   So the order-free property holds for most records, not all.

**Missing consumer facts.**

7. These are from the owner's auto-memory, verified 2026-07-16, and are not recorded in the repo:
   - **No consumer has ever sent `control.subscribe`.** The subscription and leased-demand plane
     has zero consumer call sites. This bears on the priority of C8 and C9.
   - **mycooc never uses runstate's launchers; translation does.** This bears on C20.

**Smaller corrections.**

8. **`CLAUDE.md`'s test count has drifted.** It says "~700 tests" in one place and "781/215 …
   1020/1" in another. `pytest --collect-only` collects **1038** today, from 397 test functions.
   Minor, but the map quotes "about 1020".
9. **The map's IBT-side caveat on C2 is right but incomplete.** `IBT/README.md:203-207` sketches a
   claim keyed on a semantic value: "a semantic key contended only by inserts of the same key". It
   is still a single arbiter per key.
10. **Verified as the map states.**
    - The counts in claim 4: 7 / 3 / 2 in `observables.py`.
    - The missing `backlog/episode-correlation.md`.
    - `backlog/index.md`'s stale description of IBT.
    - The stale entry in `dead_ends/index.md`.
11. **The C9 counts compare unlike things.** The map's shipped count (about 9) excludes the
    lease-boundary machinery it counted in C8: `boundary_voided`, pop-then-skip, the re-anchor
    bound and expiry counter-records. For a lease-to-lease comparison, shipped is about 15 against
    IBT's about 10 plus 2 unspecified. This **supports** IBT's "improves" on C9.

---

## 4. Overall summary

**The unbuilt-risk base rate in this repo.** Designs here routinely lose load-bearing claims when
built or attacked:
- episode-aim revision 1 lost three load-bearing claims, and its cost was underpriced tenfold
  (`+18/−11` became `+181/−24`);
- the lazy-launch draft was refuted three ways;
- `control.target` was refuted in a day (R5, 373 spawns in 3 s);
- observer-clock needed a fourth review to catch a seed that silently did nothing;
- write-authority saw three mechanisms refuted.

IBT is still moving.
- Its demand layer was rewritten on 2026-09-30.
- Its lease, domain-schema and rule-derived-question sections were committed **today**, 2026-10-02
  (git log).
- Its `decisions/` folder records five reversals in the demand layer alone.

So its unpriced costs are real. I weight this **moderately**, not as a trump. Where IBT's design
argument stands on its own (C3, C9, C11), I concede it despite this.

### Where shipped is genuinely stronger

- **The control plane.**
  - C7: an operator stop with exactly-once semantics across episodes, which can be sent before any
    worker exists.
  - C10: acknowledgement and refusal.
  - C23: the worker loop.
  - IBT cannot express the first and leaves the others unspecified. Shipped's scope is "cooperative
    bidirectional control", and this is it.
- **Liveness and time (C4, C15).**
  - A layered detector with a definitive cross-host lock.
  - Records with wall-clock dates, so a cold third party can judge freshness.
  - IBT's stated time position re-creates a defect shipped measured and fixed.
- **Everything needed to build it.**
  - C2: a fully specified claim.
  - C6: a verdict vocabulary.
  - C12: `ensure`'s guards.
  - C17: a concrete wire format.
  - C18a: backends.
  - C20: launchers.
  - C22: open metric names for generic viewers.
  - Most of these are **specification debt** on IBT's side, not refutations.

### Where if-built-today genuinely wins

- **C3, identity is data.** The principle is right, and shipped's own record (forged verdicts, the
  splice, the cascade, "correlation, not segmentation") is the evidence. The win is the principle,
  not IBT's vehicle: shipped is already retrofitting it.
- **C11, the value plane.** Nothing merges and every value is attributable. That fixes a
  silently-wrong case shipped has reproduced and deliberately left open.
- **C9, leases.** Withdrawal by revealing a committed secret is simpler, order-free and
  unforgeable. It replaces a stack of scar-tissue mechanisms.
- **New expressiveness (C5, C8, C16, C18b).** Told negative regions, quantified questions,
  dispute, and an order-free transport. Each is real. None has a consumer or spec ask today, and a
  census cannot refute any of them.

### The five concerns where the owner's decision matters most

1. **C3 + C11: identity as data. Retrofit or rewrite?** The defect evidence supports the
   *principle*, not the vehicle. Shipped has launch ids shipped, `claim_seq` aim proposed with a
   priced prototype, and a measured remedy for values. The open sub-question is the startless run,
   and it needs answering under either design.
2. **C15: is cold third-party freshness in scope?** If yes (the cockpit says so), IBT's "absolute
   time is needed for nothing" must be reversed. That reversal admits a wall-clock dated literal,
   which IBT's own "Two layers" rule permits.
3. **C18b + C2: the regime.** Does the owner want the slow inconsistent regime (no central store)?
   - If not, IBT's distinctive payoff buys current consumers nothing, and design §14 already says
     that regime is a different protocol.
   - If yes, IBT still needs a per-run arbiter for the claim. Its no-central-store story must say
     where that arbiter lives.
4. **C7 + C10: the control plane.** IBT replaces "stop" with "withdraw your own demand". That
   covers the asker's half only. An operator halt, an acknowledgement and refusal must be designed
   from scratch, and the stop sent before any episode exists is the hard case.
5. **C8 + C12: the read engine.** Is a term index, a CLP solver and a coverage checker (IBT's
   "headline cost") worth it?
   - Today no consumer sends a subscription, and `ensure`'s prefix-window memo already does
     "produce the residual" for the only shape consumers use.
   - The question to decide is the deferred relaunch-forever guard. IBT names it as Open 10;
     shipped has it, scarred, in `memoizer.py:422-472`.
