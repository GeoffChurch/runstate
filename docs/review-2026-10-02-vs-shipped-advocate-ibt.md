# Advocate for if-built-today (IBT), against shipped runstate

Built 2026-10-02 against `spec/episode-aim` @ `88ba806`. I read the map in full, all of
`docs/backlog/if-built-today/` (layers, `open.md`, `prior-art.md`, `decisions/`), and checked
every shipped citation that an argument below rests on. Excluded files were not read. One grep
printed lines from excluded `docs/review-*` files as a side effect; nothing below uses them.

Paths: `IBT/x.md:L` = `docs/backlog/if-built-today/x.md`; `specs/`, `backlog/`, `dead_ends/` are
under `docs/`; code paths are under `runstate/`.

**The position in one paragraph.** Most of IBT's real wins against shipped come from three
commitments, and none of them needs the monotone-store rewrite: **identity in the record** (C3,
C11), **one relation per fact, with a stated test for third-party authorship** (C23), and
**typing each record on arrival** (C22). Shipped's own trajectory already points at all three. It
has adopted identity one tier at a time, it has deferred an unbundling record, and its folds
refuse malformed records rather than reject them. IBT starts where shipped is heading. What
*only* IBT's rewrite buys is a further set of capabilities: told negative regions, quantified
questions with a residual, a no-merge value plane that tolerates replicas, dispute, and summary
tiers. No consumer has asked for most of them. On the other side, IBT is honestly worse wherever
it does not compete: the claim, liveness, acknowledgement, cold-attach time, the encoding, and
launching. A migration would have to import shipped's mechanisms there wholesale. The owner's
choice is therefore less "IBT or shipped" than "adopt IBT's three commitments inside shipped, or
pay for the rewrite to get the capabilities as well".

---

## 1. Verdict table

| # | concern | verdict (IBT vs shipped) | conf. | one-line reason |
|---|---|---|---|---|
| C1 | Run identity, placement | alternative (worse on layout) | high | Same content-addressed identity, and IBT cites shipped's recipe. IBT has no layout and no existence test. |
| C2 | Claim, single-spawn | **worse** | high | IBT names single-spawn the one coordinated act, then borrows shipped's CAS by reference. The claim record and where the coordinator lives are unspecified. |
| C3 | Episodes, attribution | **improves** | med-high | Identity-as-data removes the positional-attribution defect class, which shipped is retiring one tier at a time and at real cost. |
| C4 | Liveness, failure detection | **worse** | high | IBT states this as unsolved. Shipped has a 4-tier detector, the Postgres lock and cold seeding. |
| C5 | Completion, negative info | alternative, plus new (ibt) | medium | `completed`+`final_step` is the domain instance of a `¬Q` tail. IBT generalises it but has no exhaustion record shape (Open 1). |
| C6 | Terminal verdict | alternative | medium | Both treat the verdict as a join taken at the edge (L3 is IBT's "report"). IBT has no vocabulary, which is cheap to port. |
| C7a | Stop: withdrawing your own demand | **improves** | medium | Lapse plus a revealed secret: unforgeable and needs no ordering. Shipped's own `run-scoped-halt` puts a halt's scope at the demand. |
| C7b | Stop: an operator halting a run | **worse** | high | In IBT the stop is undefined and the operator halt unsolved. Shipped's stop works per episode; its known gaps are #39 and the empty durable-ceiling cell. |
| C8 | Demand, questions, condition algebra | new (ibt) + alternative | med-low | Quantified questions with a residual fix `every`'s non-monotone replay. Cost: a term index, a CLP solver and a coverage checker. No consumer subscribes. |
| C9 | Leases, lazy launch | improves (lease) / worse (launch) | low-med | A lease on the question, timed by the scheduler, deletes five positional rules. Written today, never attacked; the scheduler is unspecified. |
| C10 | Ack and refusal | **worse** | high | IBT covers `malformed` (checked by the sender) and `unsatisfiable` (`¬Q`). It lacks a registration watermark, `unsupported`, and rejection feedback. |
| C11 | Value plane | **improves** | medium | With no merge, a displaced writer cannot win a cell and episodes cannot splice silently. Typed metrics reject a misspelling. Unspecified: which trajectory `ensure` returns. |
| C12 | Memoisation (`ensure`, `history`) | alternative | medium | The per-instance residual generalises read-first plus emit-only-missing. The guards (no-progress, admission) are unspecified, and they are shipped's bug site. |
| C13 | Derived runs, relational | not comparable (mostly) | medium | Shipped's are filesystem recipes. IBT's rule-derived questions are a demand mechanism. Layout is unaddressed. |
| C14 | Reclamation, GC | alternative | medium | IBT's re-derivability tiers are sound policy. Shipped's home-level GC recipe is concrete and used. Both retain records fully. |
| C15 | Time and clocks | **worse** (doc) | med-high | "Absolute time needed for nothing" contradicts shipped's measured cold-observer need. Small fix. |
| C16 | Authority, provenance, dispute | new (ibt) | medium | Dispute without retraction is designed; provenance is unbuilt. Enforcement is equal (none on either side). |
| C17 | Wire format, schema discipline | worse (unbuilt) / improves (checkability) | medium | No encoding or versioning yet. Its closed-set fields are checkable where shipped's `name`, `value: Any` are open. |
| C18 | Backends, transport, ordering | new (ibt) / worse (unbuilt) | medium | IBT tolerates lossy unordered replication everywhere except the claim. Shipped needs one sequencer. Nobody has asked for HA. |
| C19 | Observer plane, summaries | improves (poisoning) / new (summaries) | med-low | Identity-scoped reads replace Markov-boundary windowing as the guard against poisoning. Summary tiers and the may-lift are unasked. |
| C20 | Launching, orchestration | new (shipped) | high | IBT delegates launching. Translation uses `LocalLauncher`. |
| C21 | Artifact plane | not comparable (neither) | high | Neither side models it. |
| **C22** | **Malformed records, repairability** (added) | **improves** | med-high | IBT types records on arrival. Shipped's substrate is body-opaque and its schemas are tests only, so bad records brick folds and need a repair tool. |
| **C23** | **One record, five jobs** (added) | **improves** | medium | IBT keeps exhaustion, falsity, verdict and observation as separate relations, with an authorship test. Shipped's bundle forces four consumer forging sites. |

---

## 2. Per-concern arguments

### C1. Run identity, naming, placement — alternative (worse on layout), high

- **[doc]** IBT takes shipped's identity unchanged: *"`R` is a content-addressed run id … the
  pattern is runstate's, the choice of inputs the user's"* (`IBT/5-domain.md:38-39`). The key
  granularity rule (`IBT/2-polarity.md:186-193`) restates `run-id-recipe.md`'s target-exclusion
  discipline as a general principle: a key is a claim about what determines the value. That is a
  sharper statement of why mycooc's dirty-vs-clean hash bug was a bug. It is not a new mechanism.
- **[machinery]** Shipped has a working layout: `runs/<rid[:2]>/<rid>/`, cell pointers, nested
  derived homes (`specs/store.md:73-126, 299-305`), and the locator split
  (`specs/channel-locators.md:26-61`). IBT has none of it (`IBT/open.md:46-53` says only "each
  agent holds a lagged local copy"). This is debt, not simplicity.
- **[elegance]** IBT's *"concluding absence from ownership is not [fine]"*
  (`IBT/1-logic.md:159-162`) correctly classifies shipped's `RunNotFound`-means-launch-it. Under
  shipped's one-home premise, though, the birth CAS arbitrates any double spawn that follows, so
  the classification costs shipped nothing today.
- **Would change it:** IBT specifying a layout, or a consumer needing more than one home per run.

### C2. The claim, single-spawn, CAS — **worse**, high

I concede this concern.
- **[machinery]** Shipped has about 11 parts here, implemented and tested on three backends:
  the CAS, the death CAS, the loser-never-acts invariant, and the pre-check ordering
  (`worker.py:63-135, 284-327`). IBT has *"Order is needed in exactly one place, the
  **claim**, where `send(expected_seq=)` is a compare-and-swap"* (`IBT/5-domain.md:117-119`). That
  borrows the mechanism by reference. The claim record, the loser discipline and the death CAS
  are not specified. IBT's low count here is absence, not simplicity.
- **Where the map overstates the contradiction.** `IBT/open.md:46-53` ("No central store") is
  what the semantics *tolerates*, not what it *requires*: "This follows from monotonicity and
  needs no coordination." IBT also states the intended architecture for the one coordinated act:
  *"coordination can always be factored into a membership authority plus an ordering service …
  membership is configured once; everything downstream is actually coordination-free"*
  (`IBT/1-logic.md:214-219, 225-233`). `IBT/README.md:203-207` hints at the shape: a keyed
  write-once register, i.e. a unique constraint on a *semantic* key rather than on a dense
  position. So the coherent reading is replicate everything, and put claims on one coordinator.
  It is unspecified, but not self-contradictory.
- **[elegance], small and real.** On the semantic key, only claims contend
  (`IBT/README.md:206-207`). On shipped's dense `seq`, every append contends. `run-scoped-halt.md:82-89`
  measures the side effect: an interposed halt makes the claim CAS fail, retry and *"claim
  straight past"*. A total order is not mutual exclusion. That favours neither side's safety.
- **[defect]** IBT changes none of shipped's claim-side defects: the cross-host wedge, the 20
  GPU-hours (`backlog/cross-host-claim-gate.md:352-371`), #32's eleven refuted fixes
  (`specs/write-authority.md:15-19`), or SQLite-on-NFS. IBT says so: *"`specs/write-authority.md`
  — unchanged by any of this"* (`IBT/README.md:203`).
- **The fix is structural but known.** IBT needs a claims-only coordinator with shipped's
  semantics: CAS or unique key, loser-never-acts, and a careful death. That is importing shipped's
  C2 nearly verbatim, plus a decision on where it lives (a Postgres instance is the obvious
  candidate, and it is a single point of failure IBT should name).
- **Would change it:** IBT specifying the claim relation and its coordinator.

### C3. Episodes, restart, attribution — **improves**, medium-high

This is IBT's central win. It rests on shipped's own record.
- **[defect], the class.** Shipped's attribution defects are all *position standing in for
  identity*:
  - cross-episode stop replay (`specs/stop-discharge.md:18-21`);
  - leases and count budgets resurrected per episode (`specs/time-lease-boundary.md:12-22,
    62-85`);
  - late-reap and claim-loser forged verdicts (`specs/launcher-record-identity.md:12-28`);
  - the `progress` splice that made `ensure` return a cross-episode series "as complete"
    (`observables.py:470-474`);
  - a post-terminal `emit` overwriting a successor's cell (`worker.py:219-226`);
  - the claim cascade and the unaimed heartbeat (`backlog/episode-aim.md:51-57`).

  Under `heartbeat(E2, 500)` (`IBT/README.md:81-88`), a query about E2 cannot unify with E1's
  records. There is nothing to infer, so none of these has a place to occur.
- **[defect], shipped already concedes the direction, three times.**
  1. `run-episodes.md:30-32` declined explicit episode ids *"if provenance/correlation ever needs
     them; no scoped consumer does"*. Correlation then turned out to be needed for launcher deaths
     (`specs/launcher-record-identity.md`, shipped), for lifecycle records
     (`backlog/episode-aim.md`, proposed), and for values (`dead_ends/per-episode-loglets.md:83-101`:
     *"fixed by **correlation, not segmentation** … stamp the record with the writer's own claim
     `seq`"*). The revival trigger has fired.
  2. `backlog/protocol-algebra.md:93-98` (L2, "the commutativity upgrade"): keying the
     intro/elim pairs *"turns Γ into a join-semilattice — the CRDT / multi-writer /
     replicated-log direction."* That is IBT, named by shipped's own algebra.
  3. `specs/lazy-launch.md:106-122`: adding launch identity **deleted** a mechanism. The reap
     discipline was *"a writer-side workaround for identity-less records, and it is
     **deleted**: with identity, the writer stays honest and attribution is the reader's job."*
     That is IBT's commitment in shipped's words, with the deletion it predicts.
- **[machinery], what porting costs inside shipped's positional core.** Episode-aim, revision 2,
  shows the price:
  - **Size:** `+181/−24` (`episode-aim.md:8-9`).
  - **Speed:** the aimed heartbeat fold is 2124× slower on SQLite and needs a mandatory
    latest-then-verify mitigation (`:103-118`).
  - **Selector split:** the verdict selector must split strict and tolerant and scan
    newest-first (`:83-101`).
  - **Startless runs:** a stop staged on a never-started run is unanswered, and it breaks a live
    consumer (`:121-132`).
  - **Migration:** a backfill that *"cannot be correct, only uniform"* (`:171-174`).
  - **Still positional:** "well-aimed" is defined as *"no `lifecycle.started` lies between its
    `claim_seq` and its own `seq`"* (`:13-16`).

  IBT pays the identity cost once, at the start, with no positional conjunct and no backfill.
  *(Speculation, marked: the 2124× is an artefact of indexing by `(topic, seq)` rather than by
  episode. IBT's per-functor term index would key `heartbeat(E, _)` on `E`. Untested.)*
- **[defect], why shipped could not take the cheaper alternatives.** `stop-discharge.md:155-176`
  rejects A3 (a correlated ack) and A4 (sender-declared scope) partly because *"episodes are
  deliberately implicit … there is no episode-id to address"*. The positional discharge, and
  with it #39's author-blindness, follows from that choice.
- **Debt, stated.**
  - *"Which episode is current"* is an argmax, which IBT classes as a report
    (`IBT/4-aggregation.md:12`). It is unspecified.
  - Resume and extend semantics are unspecified.
  - Minting is cheaper than the map says. `IBT/4-aggregation.md:14` lists coordination-free
    occurrence naming (by content, random id or per-actor prefix). The natural episode id is the
    claim's own `seq`, which is exactly what episode-aim's `claim_seq` is. So the fix is small:
    the episode id is the claim's id, minted by the claim of C2.
- **[unbuilt]** Low risk for this concern. Identity-in-the-record is the best-understood
  mechanism in either design, and shipped has prototyped it (episode-aim's full prototype, the
  launcher ids in production).
- **[consumer]** mycooc's 785 `started` records carry zero launch ids (`CLAUDE.md`, "Count before
  designing"). A launcher-minted id can never speak for its claims. An id minted by the claim
  (IBT's, or episode-aim's) can.
- **Portable, and I say so.** `IBT/README.md:96-97`: *"identity-as-data would work in a plain
  mutable database."* This verdict argues for the principle. It argues for migration only to the
  extent that the episode-aim costs above are what shipped would keep paying, tier by tier.
- **Would change it:** an episode-aim implementation in shipped that lands without the positional
  conjunct or the backfill guess. That would show the port is cheap.

### C4. Liveness and failure detection — **worse**, high

I concede this concern.
- **[machinery]** Shipped has about 21 parts: four tiers plus the 3b Postgres lock; `resolve()`
  scoped to the hostname; `live_episode` conservatively treating abstention as live;
  `last_activity`; and Watcher seeding from `t` (`watcher.py:85-116, 243-295`;
  `specs/observer-clock.md:153-207`). IBT has *"**Cross-host liveness.** You still need a handle
  and a probe"* (`IBT/README.md:177-178`) and principles for dating observations
  (`IBT/1-logic.md:258-293`). IBT's U items (the cascade, the heartbeat, thresholds, cross-host
  death) would rebuild most of shipped's 21 parts.
- **[doc]** The map is right that IBT undercounts shipped. *"Exhaustion arrives three ways … the
  probe **abstains off-host**"* (`IBT/5-domain.md:185-189`) omits tier 4 and the Postgres lock,
  which is *"definitive cross-host death detection"* in the Watcher
  (`specs/channel-postgres.md:146-155`). It is true of `resolve()` and the claim gate, not of
  observation.
- **What IBT keeps that is correct, [elegance].** *"That abstention must not become a stored
  verdict"* (`IBT/README.md:178`) restates shipped's own field rule, *"a heuristic may VETO,
  never AUTHORISE"* (`backlog/cross-host-claim-gate.md:373-386`). The rule is the same on both
  sides; only shipped has the machinery.
- **Would change it:** IBT importing shipped's detector as the core's probe, with tier outputs
  posted as dated observation literals. *(Untested; the shape fits `IBT/1-logic.md:284-293`.)*

### C5. Completion and negative information — alternative, plus new (ibt), medium

- **[defect]** Shipped's completion history is three discoveries that exhaustion is not falsity:
  1. the `completed`-by-default footgun (`specs/completed-opt-in.md:11-21`);
  2. *"no way to express 'the producer finished before `up_to`'"*
     (`specs/preempted-vs-completed.md:8-22`);
  3. a launcher's exit 0 read as `COMPLETED` (runstate#30; `observables.py:299-323`).

  IBT states the distinction as a first principle, with a test: *"could a third party post this
  knowing only that the process died? For **exhaustion** yes … For **falsity** no"*
  (`IBT/2-polarity.md:200-205`). In shipped's terms that test *is* `worker_completed`.
- **[doc]** The map's *"there is no record that asserts absence over a region"* is too strong.
  `stopped{completed=True, final_step=N}` is a negative tail of one fixed shape: "nothing beyond
  N". IBT's own census calls it *"the only closure claim"* (`IBT/open.md:72-73`). What shipped
  lacks is a *general* `¬Q`: any region, any author, and not voided by the next claim (which
  shipped's tail is, `specs/lazy-launch.md:55-57`).
- **[machinery]** IBT has a gap shipped does not. *"Exhaustion … needs a shape that is not
  `¬Q`"* (`IBT/open.md:5-11`). Shipped has that shape: `completed=False` with no error means
  "this episode stopped, nothing claimed about the rest".
- **New (ibt).** Region-general `¬Q`, settledness by a finite cover, and solver-posted negatives.
  **[consumer]** Only the tail instance has been asked for (the `ensure` gap above), and shipped
  serves it. Nothing in the shipped specs or consumers asks for the rest.
- **Would change it:** a consumer needing a negative outside the run's tail, such as a solver, a
  third-party closure, or a per-metric cutoff.

### C6. The terminal verdict — alternative, medium

- **[elegance]** The concept is the same on both sides. IBT's *"the verdict as a join of two
  partial observers — and it is a report"* (`IBT/5-domain.md:14-15`) is shipped's L3, *"join
  only at the verdict"* (`backlog/protocol-algebra.md:117-128`).
- **[defect]** Shipped's verdict defects were attribution (the late reap and the claim loser,
  fixed by launcher ids) and malformed records. IBT removes both classes: the first by C3, the
  second by C22.
- **[machinery]** IBT has no outcome vocabulary, projection or tiering. Porting shipped's closed
  `Outcome` as a report is mechanical.
- **[doc]** *"measured to **retract a published verdict**"* (`IBT/5-domain.md:147-148`) frames
  as a defect what shipped designs on purpose: "terminal stands until a new episode claims"
  (`specs/run-episodes.md:34-45`). If the verdict is keyed by episode, `verdict(E, …)` never
  retracts. The run-level "latest verdict" is a report and may change. The two sides disagree in
  framing, not in substance. IBT should drop the word "retract".
- **Would change it:** nothing at the level of a concern. This is a port.

### C7. Stopping — split into two rows

**C7a. Withdrawing your own demand — improves, medium.**
- **[machinery]** IBT has lapse plus `withdrawn(k)` with `hash(k) = C`: unforgeable on a public
  store, needs no ordering, and askers cannot withdraw each other (`IBT/5-domain.md:231-249`).
  Shipped's equivalents are `control.unsubscribe` plus the positional answer fold. Those need
  pairing by `seq` precisely *"because the asker's `request_id` can be reused"*
  (`specs/service-worker.md:70-76`, confirmed). Any reader can also forge them, since the
  `request_id` is in every record.
- **[defect], and shipped's own analysis agrees on scope.** `backlog/run-scoped-halt.md:90-94`
  says *"The halt's natural scope is the **demand**, not the run — which argues the fact does not
  want the log at all."* IBT's model *is* demand-scoped.
- **[unbuilt]** The mechanism was committed today (`88ba806`) and has never been attacked.

**C7b. An operator halting a run someone else drives — worse, high.**
- IBT: *"The halt does not dissolve … That needs a write and an authority rule, and always did"*
  (`IBT/README.md:182-184`). The `stop` relation is undefined (`IBT/3-questions.md:194-197`).
- Shipped has a working stop: an episode-scoped request with a positional discharge, latched,
  OR-joined and exactly-once across a down run (`specs/stop-discharge.md:44-132`), with three
  converging reviews behind it. Its known defects are #39 (author- and body-blind discharge) and
  the empty durable-ceiling cell (`specs/control-target.md:169-184`), both open.
- **What IBT would need.** A native stop, `stop(R, C)` discharged by `honoured(E, C)`, needs an
  acknowledgement relation. That is shipped's rejected A3, and it is the same gap as C10. It is a
  small fix in mechanism. The authority rule for halting *someone else's* demand is a policy
  question on both sides.
- **Would change it:** IBT specifying `stop` and `honoured`, or the owner deciding that halts are
  always demand withdrawals by the demander.

### C8. Demand: subscriptions, questions, condition algebra — new (ibt) + alternative, medium-low

- **[defect]** Shipped's `history` is non-monotone on a public read path today:
  `{0,11,20} → [0,11]` but `{0,10,11,20} → [0,10,20]` (`backlog/memoizer-index-algebra.md:35-50`).
  It *"cannot fire through `ensure` only because `ensure` hardcodes … `every: {step: 1}`"*. The
  repair that file names is IBT's: *"anchor the stride … difference constraints **plus
  congruences**"* (`:52-61`), which is `IBT/open.md:84-89`. On this specific defect IBT improves.
- **[machinery]** Counts are near parity (about 16 against about 15), but the parity is
  misleading in IBT's favour. IBT's X3 — a per-functor term index that pattern-walks, a
  constraint solver in the read path, and a coverage checker — are each a substantial component.
  IBT calls them *"the honest headline cost … everything else here is downstream of being
  willing to build it"* (`IBT/5-domain.md:121-126`). Shipped's condition algebra is a
  dependency-free 350-line module (`layers.md:49-52`).
- **New (ibt).** Per-variable `∃`/`∀` (*"some config that passes on every seed"*), the residual,
  settledness as the stream terminator, and rules that derive questions
  (`IBT/3-questions.md:5-34, 211-232`).
- **[consumer]** No consumer has ever sent `control.subscribe` (owner memory,
  `runstate-holistic-review.md`, verified 2026-07-16). The used demand path is `ensure`
  (C12). That ranks priority only. Both consumers are the same reuse persona.
- **[unbuilt]** High. The questions layer was replaced on 2026-09-30 after *"five days of
  dialectic and two rounds of two-reviewer checks"* (`IBT/decisions/3-questions.md:81`). Six
  representations were withdrawn on the way (`:84-122`).
- **Would change it:** a consumer needing an `∀` question or overlapping partial demands; or the
  coverage checker built and measured.

### C9. Leases, lazy launch, "still wanted" — improves (lease) / worse (launching), low-medium

- **[defect] → [machinery], mechanism for mechanism.** Shipped's lease lives in the *worker's*
  memory. That single fact produced:
  - the ghost-lease flap (`specs/time-lease-boundary.md:12-22`);
  - count budgets refunded per episode (`:62-85`);
  - expired leases resurrected per episode (`specs/service-worker.md:59-66`).

  The repairs are five rules, all pairing by position:
  1. expiry counter-records with emit-then-delete (`service-worker.md:78-84`);
  2. the positional answer fold (`:67-76`);
  3. episode-boundary voiding (`time-lease-boundary.md:24-41`);
  4. pop-then-skip (`:35-41`);
  5. `count` reclassified as episode-local (`:62-85`).

  IBT puts the lease on the *question*, `asked(Q, lease(C, N, D))`. The *scheduler* times it from
  local receipt, and `withdrawn(k)` withdraws it (`IBT/5-domain.md:200-249`). Episode boundaries
  never touch it, so none of the five rules has anything to repair.
- **[machinery], correcting the map.** The map shows about 9 against about 10, but shipped's
  lease machinery is mostly counted under C8. Comparing lease to lease:
  - **Shipped, about 14:** time-`until` plus renewal, the expiry record, emit-then-delete, the
    answer fold, boundary voiding, pop-then-skip, count as episode-local, `live_demand`,
    `pinned`, the `retire` death CAS, `ensure_served`, mandatory `reap`, ref-count-exact, and
    "acceptance ≠ will-serve".
  - **IBT, about 6, plus 2 unspecified:** the lease record, `withdrawn`, local-receipt timing, a
    monotonic clock, hash with a secret, and expiry as the backstop; unspecified are the
    scheduler and the admission rule.
- **[machinery], where it is worse.** IBT has no launcher, waker or reap
  (`IBT/3-questions.md:203-209`; see C20). Shipped's `retire()` death CAS exists because demand
  races a worker's death. Under IBT the scheduler reads demand from the store and relaunches, so
  the *orphan* cannot occur. A wasted spawn can, and that returns to C2. *(Argued, not tested.)*
- **[unbuilt]** High. The lease design was committed **today** (`88ba806`) and never attacked.
  The map's claim 12 is right that the analogy to `time-lease-boundary.md` is loose (C15).
- **[consumer]** No consumer uses leases (`control.subscribe` has no call sites).
- **Would change it:** an adversarial pass on the lease section; a consumer with a service
  workload.

### C10. Request acknowledgement and refusal — **worse**, high

- **[doc], correcting the map.** IBT covers more than reject-on-arrival:
  - `unsatisfiable` is a `¬Q` over the demand's region, *"the **affirmable** form of 'this demand
    cannot be satisfied'"* (`IBT/decisions/3-questions.md:59-62`);
  - `malformed` is caught by the sender before it posts (*"caught twice: locally before anything
    is sent, and again on receipt"*, `IBT/1-logic.md:509-513`).
- **[machinery], what is missing.** The `consumed_seq` registration watermark, `unsupported`
  ("no producer serves this shape", which in IBT is an unsettled question forever), feedback to
  a poster whose record a *receiver* rejected, and `await_consumed`. IBT names the need: *"liveness
  still needs an acknowledgement"* (`IBT/1-logic.md:221-223`).
- The fix is medium: an ack relation, which C7b needs anyway.
- **Would change it:** IBT specifying the ack relation.

### C11. The value plane — **improves**, medium

- **[defect]** Shipped declares its value read a *"convergent merge … a caller can receive a
  series no single execution produced"* (`observables.py:535-542`). A displaced writer *"wins
  the cell, and `ensure` then returns a spliced series with no re-drive and no error"*
  (`dead_ends/per-episode-loglets.md:85-88`). G1's soundness argument rests on *"Episodes are
  sequential (single-writer-per-run)"* (`backlog/value-plane-divergence-resolution.md:56-60`).
  `specs/write-authority.md` rev 4 withdrew that premise: single-writer holds *"at the claiming
  instant only"*. `episode-aim.md:48` notes the same gap for lifecycle records; nobody has noted
  it for G1.
- **[elegance] → [defect] removed.** IBT does not merge. `p(a,1)` and `p(a,2)` are two atoms
  (`IBT/0-substrate.md:14-18`). Keyed by episode where production is nondeterministic, `at(E, S, M)`
  (`IBT/5-domain.md:43-50`), two lineages are two sets of facts. The splice cannot be committed
  silently: choosing a lineage is an explicit report.
- **[elegance], from the owner's own principle.** One relation, typed per metric:
  `at(R, S, M)` with `M = loss(Float) | …`. A misspelt metric is an undeclared constructor,
  rejected on arrival (`IBT/5-domain.md:25-36`). Shipped's envelope `name` is an open string and
  `value` is `Any`, so a typo is a new series. That is the global rule *"never access a closed set
  as if it were open"*, applied to the value plane. The cost is honest: adding a metric means
  redeploying observers (`IBT/5-domain.md:35-36`).
- **Debt.** Which trajectory `ensure` returns under episode-keyed values is a report that IBT
  does not specify. The map is right that this is open.
- **[consumer]** Corpus figures (1,714 of 1,719 divergent cells are `status`; 16 hand-rolled guard
  sites) are IBT's own and cannot be verified here. They rank priority only.
- **Would change it:** an `ensure` returning spliced series turning out never to matter to a
  consumer; or IBT's trajectory report proving as ad hoc as take-the-latest.

### C12. Memoisation (`ensure`, `history`) — alternative, medium

- **[machinery], what IBT generalises.** Its per-instance residual handed to the producer
  (`IBT/3-questions.md:143-171`) is the general form of three shipped mechanisms:
  - `ensure`'s read-first;
  - the half-open window;
  - derived runs' *"emit-only-missing: before sending, read the channel's existing names … and
    skip those present"* (`specs/derived-runs.md:93-98`).

  Overlapping demands run only the difference. Containment needs no detection
  (`IBT/3-questions.md:180-183`).
- **[defect], where shipped is stronger.** Shipped's `ensure` bug site is the termination guard:
  - the false raise on the time axis;
  - the guard being structurally dead for time targets;
  - the epochless livelock (about 97k re-drives);
  - the `None`-gated hang (`memoizer.py:428-461`; `specs/ensure-until-condition.md:127-134,
    179-210`).

  IBT relocates the guards to the non-monotone core (`IBT/5-domain.md:178-183`). It names the
  admission rule as Open 10 (`IBT/open.md:94-96`) and specifies neither. A relocated guard is
  still an unspecified guard.
- **[doc]** The map's claim 6 is correct, and I concede it. The 373-spawn storm came from a dead
  no-progress guard plus the `+1` overshoot under rival target writers
  (`specs/control-target.md:144-167`), not from a residual that could never empty. The failure
  *shape* is the same (a relaunch loop the guard should stop); the cause is different. IBT should
  say "a storm of this shape". The useful point survives: IBT's Open 10 and shipped's no-progress
  guard are the same unsolved problem.
- **[consumer]** Both consumers use `ensure`, so this concern carries the most consumer weight
  of any demand concern.
- **Would change it:** IBT specifying admission and the no-progress test.

### C13. Derived runs and the relational layer — not comparable (mostly), medium

Shipped's layer is filesystem recipes plus one helper (`specs/store.md:25-71`): placement,
pointers, mark-and-sweep, and a provenance register. IBT's rule-derived questions
(`asked(∃V. loss(K, V)) :- asked(∃W. report(K, W))`, `IBT/3-questions.md:28-31`) are a demand
mechanism. **[elegance]** That is where IBT is better. Dispatch by derived question, with the
residual, subsumes "`ensure` the parent, then compute". But IBT has nothing for membership,
layout or enumeration. **Would change it:** IBT specifying layout.

### C14. Reclamation, GC, retention — alternative, medium

- **[elegance]** IBT's tiers by re-derivability (`IBT/3-reclamation.md:18-33`) are a sounder
  criterion than recency. *"Reclamation must not depend on receiving a message"* (`:9-11`) agrees
  with shipped's offline sweep.
- **[machinery]** Shipped's home-level GC (`specs/store.md:234-272`) is concrete and used.
- Both keep in-log retention full. IBT cannot evict heartbeats either: a produced record whose
  producer is gone is "unrecoverable", so never evicted. Shipped's 50%-heartbeat growth
  (`backlog/in-log-compaction.md:14-35`) is unsolved on both sides.
- **Would change it:** IBT classifying heartbeats as control or transport rather than store.

### C15. Time and clocks — **worse** (doc), medium-high

I concede this concern.
- **[doc]** IBT writes that absolute time *"is needed for nothing here"* (`IBT/1-logic.md:303`).
  Shipped measured the opposite: a cold third party needs a shared-origin clock. Five runs dead
  12–21 days read as live, and the GC had no age to gate on (`specs/observer-clock.md:12-30`).
  IBT's lease adopts the *"original liveness design"* by name (`IBT/5-domain.md:211-212`), i.e.
  the receipt-time model that observer-clock amended.
- **The fix is small, and IBT's own rules allow it.** Literals carry *"who observed, by whose
  clock, and any comparison across observers is a report"* (`IBT/1-logic.md:290-293`). Reports may
  do anything except feed demand. So "how stale is this run, by its own `t`?" is expressible as a
  report. IBT needs to put a wall-clock reading in the dated literal and delete the "needed for
  nothing" sentence.
- **[elegance]** IBT's split of order, duration and absolute time (`IBT/1-logic.md:295-307`) is
  clean. It matches shipped's rule *"`seq` orders, `t` measures"* (`observer-clock.md:98-99`).
- **Would change it:** IBT adopting dated beacons as reports.

### C16. Write authority, provenance, forgery, dispute — new (ibt), medium

- **[elegance]** Dispute is derived from grounds; objections to objections cost nothing;
  readjudication is free where deletion is irreversible (`IBT/3-provenance.md:7-56`). It needs
  no retraction.
- **[doc]** Enforcement is equal: none on either side (`IBT/README.md:181`;
  `positioning.md:60-97`). Lease withdrawal by preimage (C7a) is IBT's one unforgeable act.
- **[consumer]** Shipped's own open list asks for provenance: the author field
  (design §12.8), multi-orchestrator attribution (`backlog/index.md:319-321`), and *"Attribution
  + a fork-surface … a forensic affordance no consumer has asked for"*
  (`value-plane-divergence-resolution.md:86-88`). Nothing in shipped asks for dispute.
- **[unbuilt]** *"Provenance … the single highest-leverage unbuilt thing here"*
  (`IBT/open.md:12-15`). Dispute without provenance is anonymous.
- **Would change it:** a consumer needing to reject one producer's records retroactively.

### C17. Wire format and schema discipline — worse (unbuilt) / improves (checkability), medium

- **[unbuilt]** Shipped has a JSON Schema stack with `additionalProperties: false`, independent
  versioning, and conformance tests. IBT has a record grammar and no encoding or versioning
  (`IBT/0-substrate.md:64-82`).
- **[elegance]** Where IBT specifies, it is more checkable:
  - polarity is a two-valued field that `additionalProperties: false` can pin
    (`IBT/2-polarity.md:469-473`);
  - variables are `var(N)` numbered canonically, so renamed variants deduplicate
    (`IBT/0-substrate.md:70-75`);
  - sorts are per functor with no untyped escape (`IBT/1-logic.md:482-518`).
- Shipped's schemas are *"a **conformance test, not a runtime gate** — nothing under
  `runstate/` imports `jsonschema`"* (`layers.md:42-47`). That is the root of C22.
- **Would change it:** IBT choosing an encoding.

### C18. Backends, transport, ordering — new (ibt) / worse (unbuilt), medium

- **New (ibt).** Merge is union, so unordered, duplicated, lossy delivery is sound everywhere
  except the claim (`IBT/1-logic.md:240-251`; `IBT/5-domain.md:114-119`). Shipped requires one
  sequencer per run. HA *"re-admits a `seq` on failover → … a different substrate — not a config
  flag"* (`specs/channel-postgres.md:293-294`), and the design calls the causal regime *"a
  different protocol"* (design §14).
- **[defect]** Shipped's 12 of 12 `.read()` sites consume order (`dead_ends/per-episode-loglets.md:78-81`).
  That is not a defect in its regime, but it is the measure of what IBT does not depend on.
- **[consumer]** Nobody has asked for HA or the causal regime. Cross-host is served by one Postgres.
- **[unbuilt]** IBT specifies no backend.
- **Would change it:** a consumer needing multi-home or offline-merge operation.

### C19. The observer plane — improves (poisoning) / new (summaries), medium-low

- **[defect] → [machinery].** Shipped's Markov-boundary windowing exists because *"a malformed
  record from a dead past poison[s] the live present permanently"* (design §14;
  `observables.py:221-229`). Under IBT, past episodes' records do not unify with a query about
  this episode (C3), and malformed records never enter (C22). The guard does not need writing.
- **[elegance]** Derivation reads only up-sets (`IBT/2-polarity.md:333-364`), so a reader
  cannot write a fold that conflates `∅` with false. Shipped relies on rules and review for that
  (the F7 class, `specs/observables.md`).
- **[defect], against IBT.** *"Picking the dual is where the danger is"*: the natural monotone
  `progress` reintroduces the splice (`IBT/5-domain.md:150-157`). Porting is eight non-mechanical
  rewrites by IBT's own count.
- **New (ibt).** The three summary tiers, the may-lift, and the contextuality foreclosure
  (`IBT/4-aggregation.md:47-124`). No consumer has asked; shipped's `runstate-tui` reads no value
  folds (`run-scoped-halt.md:95-99`).
- **[unbuilt]** IBT's "13 of 16 non-monotone" and "9 of 9 reconstruct" have no artifact in the
  tree. The `latest` count is close (7 direct, 3 positional, 2 `max`; the map's check is correct).
- **Would change it:** the fold-port measurements published.

### C20. Launching and orchestration helpers — new (shipped), high

IBT delegates launching (`IBT/3-questions.md:203-209`). **[consumer]** Translation spawns through
`LocalLauncher` (1,131 `launcher.launched`, owner memory). mycooc does not use it. Shipped's
launchers carry their own history: an untypeable Protocol, and a reap discipline added then
deleted. No IBT counterpart.

### C21. The artifact plane — not comparable (neither), high

Both sides say it is where a double-live worker's damage lands, and neither models it
(`IBT/README.md:179-180`; `specs/write-authority.md:174-175`).

### C22 (added). Malformed records and append-only repairability — **improves**, medium-high

The map scatters this across C6, C17 and C19. It deserves its own row, because it is a
mechanism-for-mechanism removal.
- **[defect]** Shipped's substrate never parses the body, and its schemas are tests only
  (`layers.md:42-47`). A malformed `lifecycle.stopped` therefore lands, and:
  - it discharges every pending stop, since discharge is body-blind;
  - it makes `peek_terminal` raise `MalformedRecordError` (`observables.py:445-456`: 6 of 11
    foreign discharges were malformed);
  - it bricks the verdict plane until an append-only repair, which is *"the ONLY way to revive a
    channel bricked by a bad write, and a downstream repair tool exists"*
    (`tests/test_observables.py:725-728`; `mycooc/scripts/repair_malformed_stopped.py`, cited at
    `backlog/claim-eviction.md:290`).

  Episode-aim's selector had to fracture into strict and tolerant halves and scan newest-first
  to keep this repairability (`episode-aim.md:90-101`).
- **The IBT construct.** A type error *"is a property of the message alone … rejecting it is
  order-independent"*. It is checked by the sender and again on receipt (`IBT/1-logic.md:509-513`;
  `IBT/0-substrate.md:25-28`). The record never enters the store, so it cannot discharge,
  poison, or need repair.
- **Honest limits.**
  - A well-typed lie still lands. That is forgery (C16).
  - Rejection feedback to the poster is the C10 gap.
  - Shipped *could* add a runtime gate at the convention layer, but not at the substrate without
    breaking opinion-freeness (`specs/channel-postgres.md`, "convention knowledge … never the
    substrate"). IBT's signature belongs to the program, so it has no such conflict.
- **Would change it:** a shipped design for convention-level validation on write that a raw
  `send` cannot bypass.

### C23 (added). One record, five jobs — **improves**, medium

- **[defect] [consumer]** *"`lifecycle.stopped` releases the claim, declares the verdict, reports
  the step frontier, discharges pending `control.stop`s, and dates the run's freshness … anyone
  who wants one must assert all five"* (`backlog/lifecycle-stopped-unbundling.md:3-5`). mycooc
  forges it at four sites (`backlog/claim-eviction.md:286-291`):
  - `reclaim_experiment.py`, about 288 lines, where the forgery is *"forced, not sloppy"*
    (`cross-host-claim-gate.md:368`);
  - `resume_fanout`, which wants discharge only;
  - `repair_malformed_stopped.py`;
  - `_SyncHandle`, which earned its verdict from an exit code.

  #39 (11 of 37 stops) and #42 (freshness corrupted, `cross-host-claim-gate.md:229-233`) both
  come from the bundle. Shipped's remedy, a designated eliminator, is a 470-line design deferred
  on adoption (`claim-eviction.md:1-40`).
- **The IBT construct.** IBT's principles already split four of the five jobs:
  - **claim release ≈ exhaustion.** A fact about a process, legitimately postable by a third party
    (`IBT/2-polarity.md:200-205`).
  - **frontier ≈ the producer's `¬Q` tail.** Only the producer may post it, because only it knows.
  - **verdict = a report.** Derived, never posted (`IBT/5-domain.md:14-15`).
  - **freshness = a dated observation literal.** It carries its observer (`IBT/1-logic.md:290-293`).

  So `_SyncHandle` becomes a legal exhaustion post. The reclaim tool posts exhaustion on `sacct`
  evidence and discharges no stop. The repair tool is unnecessary (C22).
- **The honest gap.** The fifth job, discharge, is undefined because the stop is (C7b). The
  authority question — may this party post exhaustion, on what evidence — is unchanged
  (`cross-host-claim-gate.md:388-423`). IBT's test (*"knowing only that the process died"*)
  answers the *kind* of evidence, not who may post it.
- **Portable.** Shipped could unbundle too. This verdict credits IBT's principles; it does not
  show the rewrite is required.
- **Would change it:** shipped landing `lifecycle.evicted` plus a discharge-only record without
  the minimality problem `lifecycle-stopped-unbundling.md:24-36` fears.

---

## 3. Corrections to the map

1. **Claim 1 (the calibration figure).** The map concludes the number *"calibrates a defect
   class that, by shipped's own analysis, this commitment doesn't remove."* That is half right.
   - **Right:** IBT's README credits the number to *identity-as-data*, which is the wrong
     commitment for it. Episode-aim correctly shows aim does not close it.
   - **Wrong:** IBT's other commitments do remove most of it. 6 of the 11 were malformed, and
     arrival typing rejects those (C22). The rest were honest third parties forced to forge the
     only eliminator (`cross-host-claim-gate.md:368`). Separating exhaustion from the stop's
     effect removes that (C23). Shipped's own analysis agrees: a designated eliminator *"fixes it
     with zero change to the discharge fold"* (`cross-host-claim-gate.md:223-228`).
   - **What survives:** deliberate forgery, which IBT concedes (`IBT/README.md:96-100`).
2. **C2 and §5, "how the single claim CAS coexists with no central store".** It is not a
   contradiction. "No central store" is a tolerance (`IBT/open.md:46-53`). The intended
   architecture is a coordinator for the one coordinated act
   (`IBT/1-logic.md:214-219, 225-233`, Thm. 4). It is unspecified, not incoherent.
3. **C3, "minting unspecified".** IBT's own coordination-free occurrence naming
   (`IBT/4-aggregation.md:14`) covers minting. What needs the claim is *which episode is
   current*, and the natural id is the claim's `seq` (episode-aim's `claim_seq`). Small fix.
4. **C3, "explicit episode ids were declined".** True as written, but the decline carried a
   revival trigger (*"if provenance/correlation ever needs them"*, `run-episodes.md:30-32`). That
   trigger has fired three times: launcher ids, episode-aim, and per-episode-loglets' *"fixed by
   correlation"*.
5. **C5, "no record asserts absence over a region".** `stopped{completed=True, final_step=N}` is
   a fixed-shape negative tail. IBT calls it *"the only closure claim"* (`IBT/open.md:72-73`).
   Shipped lacks *general* `¬Q`, not all of it.
6. **C9 moving parts.** The map compares about 9 against about 10, but shipped's lease machinery
   is mostly counted under C8. Lease to lease it is about 14 against about 6 plus 2 unspecified.
7. **C8 moving parts.** Count parity understates IBT's cost. Its three external mechanisms are
   each large components, by IBT's own account (`IBT/5-domain.md:121-126`).
8. **C10.** IBT covers `unsatisfiable` (as `¬Q`, `IBT/decisions/3-questions.md:59-62`) and
   `malformed` (checked by the sender, `IBT/1-logic.md:509-513`). It is not only reject-on-arrival.
9. **C11, G1.** The map lists G1 as a fix with no caveat. Its soundness premise, *"Episodes are
   sequential (single-writer-per-run)"* (`value-plane-divergence-resolution.md:56-57`), is the
   invariant `write-authority.md` rev 4 withdrew. `episode-aim.md:48` notes that withdrawal for
   lifecycle records only.
10. **C15.** IBT cites observer-clock as *"runstate's original liveness design"*
    (`IBT/5-domain.md:211-212`). It chose the pre-amendment model deliberately, not by
    misreading. And IBT's report layer *can* express cold freshness (`IBT/1-logic.md:290-293`).
    The defect is the "needed for nothing" sentence, not a missing capability.
11. **Unbuilt weight, missing from the map.** The IBT documents are moving. 78 commits have
    touched them since August, ten since 2026-09-25. The lease and domain-schema sections were
    written today (`88ba806`, `775ce0d`). The questions layer was replaced on 2026-09-30. Shipped
    `runstate/` has 5 commits since August.
12. **Claims I accept.**
    - **Claim 6 (the storm).** Same shape, different cause.
    - **Claim 8.** IBT undercounts shipped's liveness: tier 4 and the Postgres lock.
    - **Claim 3 ("nothing reads a seq").** Partly right. IBT-native derivation reads no `seq`.
      The fold port does, reports may, and the cursor is transport. IBT avoids `seq` for the stop
      only because the stop is undefined. A native discharge needs an ack relation (C7b, C10).
      IBT should write "nothing in *derivation* reads a sequence number."
    - **Claim 18.** `backlog/index.md:111-120` describes a superseded IBT.

---

## 4. Summary

**Where IBT genuinely wins.**
- **C3, C11, C19 (identity in the record).** Shipped's largest defect class is attribution by
  position, and shipped is retiring it tier by tier:
  - launcher ids have shipped, and they deleted a mechanism;
  - aim is proposed at `+181/−24`, with a 2124× fold cost, an unanswered startless-run case and
    a backfill that is a guess;
  - value correlation is named and unwritten.

  Shipped's own algebra names IBT's direction (`protocol-algebra.md:93-98`).
- **C22 (typing on arrival).** It removes malformed-record bricking, the repair tool, and part of
  the reason for Markov-boundary windowing.
- **C23 (one relation per fact).** It splits four of `lifecycle.stopped`'s five jobs, which is
  what mycooc's four forging sites and the deferred eviction design are reaching for.
- **C7a and C9 (demand-scoped leases and withdrawal).** They delete five positional lease rules,
  and they put the halt at the scope shipped's own `run-scoped-halt` names. Confidence is low:
  this design is a day old.
- **New capabilities nobody has asked for yet.** Quantified questions, the residual, general
  `¬Q`, dispute, summary tiers, and replication tolerance.

**Where shipped is genuinely stronger.**
- **C2 (claim), C4 (liveness), C10 (ack), C7b (operator halt).** IBT borrows, leaves unsolved,
  or leaves undefined. A migration imports shipped wholesale here.
- **C15 (cold-attach time).** IBT's text is wrong; the fix is small.
- **C17, C18, C20.** Encoding, backends, conformance and launchers exist only in shipped.
- **C12.** Shipped's `ensure` is specified, used by both consumers and hardened. IBT's residual is
  cleaner, but it relocates the guards that are `ensure`'s actual bug site without specifying
  them.
- **The whole [unbuilt] asymmetry.** About 1000 tests and two consumers, against documents whose
  demand layer was rebuilt this week.

**Where the owner's decision matters most.**
1. **Identity in the record (C3, C23).** Adopt it inside shipped (finish episode-aim, value
   correlation, an eviction record), or take it as IBT's starting point. Both sides agree it is
   right; they differ on whether a positional core makes the port too expensive. Episode-aim's
   measured costs are the evidence.
2. **The read-side engine (C8, C12).** Is a term index, a CLP solver and a coverage checker worth
   quantified questions and the residual, when the only used demand path is `ensure`? This is
   the single largest cost in IBT and the one that is not portable.
3. **The regime (C18).** If multi-home or replicated operation is never a target, the CALM
   machinery buys nothing the consumers need. IBT's case then reduces to point 1 plus C22, and
   those are portable.
4. **The claim and liveness (C2, C4).** IBT has no answer. Any adoption must name where claims
   live, presumably on a coordinator with shipped's CAS semantics, and import the detector.
5. **The scope of a halt (C7).** Episode (shipped's stop), run (the consumer's reading,
   `run-scoped-halt.md:39-48`), or demand (IBT, and shipped's own §4.4). This decides whether the
   empty durable-ceiling cell is filled by a new verb or by withdrawal plus authority.
