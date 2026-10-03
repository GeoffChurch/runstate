# Handoff — `docs/backlog/if-built-today/`

**Untracked working note, rewritten 2026-09-30.** Point-in-time: delete it when consumed. Branch
`spec/episode-aim` was **merged to master as #51 on 2026-10-02**, by merge commit `72d9c3f`, so every
hash below is on master. (A brief rebase merge was undone by force-push; #52, which re-pointed
citations at the rebased hashes, was closed unmerged.) Work continues on `docs/vs-shipped-and-prior-art`. Maintained **as we go**: an item is struck or rewritten the
moment it settles.

**Line numbers drift; every site below is also given a phrase to `grep` for.** Since `e2d3435` (2026-10-01)
the doc is a directory, `docs/backlog/if-built-today/`, one file per layer, with `decisions/` split the
same way. Grep it with `grep -rn '<phrase>' docs/backlog/if-built-today/`. Items below are tagged by layer.

## The layer split, and what follows it

- **Step 1, the pure move: DONE** (`e2d3435`). Verbatim by sorted-line diff.
- **Step 2: DONE** (`36b5f07`). Ancestors-only holds by script; lower layers use `p(K, V)`; every corpus
  figure is in `5-domain.md` §"What the measurements say". New `3-reclamation.md`. Settledness split:
  set-of-atoms in `2-polarity.md`, question-level in `3-questions.md`. `3-provenance.md` depends on polarity
  (stated honestly); its dispute mechanism alone does not.
  - **Not neutralised, deliberately:** `README.md` keeps its runstate-flavoured motivation (the 11-of-37
    measurement, `heartbeat(episode2, 500)`); it is the root and is cited by all. Revisit if a reviewer
    finds it leaks into a layer.
  - **For step 3, from `review-5`** (scratch, to be deleted): the TUI accepting a free-text metric name
    that was never emitted is the one genuine open-namespace case. It bears on per-functor vs
    name-as-data metrics.
- **Depth prefixes: DONE** (`4e37a59`). Files are `0-substrate` … `5-domain`; siblings share a number.
- **Step 3: DONE**, each edit discussed and agreed one at a time:
  - `a27cb89` — the three tiers of summary (join-homomorphisms / monotone / report), distributive gluing
    stated in `2-polarity.md`, and the **may-lift** as a construction in `4-aggregation.md`; the trust
    *decision*, not the evidence, is non-monotone.
  - `8e3e41a` — D1: production gated on asked ∧ unsettled ∧ still wanted, the last being control. Lease
    policy in `5-domain.md`, timed from local receipt. The two choices left open there are now decided
    (see the next item).
  - Leases decided: **one relation**, `asked(Q, lease(C, N, D))`, with `asked(Q)` its projection; the
    records are ordinary facts and *still wanted* is the scheduler's reading. **Early withdrawal by
    reveal**: `C = hash(k)` for a random secret `k`, and `withdrawn(k)` withdraws. It is unforgeable on
    a public store, needs no ordering since `C` is never reused, keeps askers apart with no roster, and
    lets the asker choose the granularity. Lapse stays as the crash backstop. `N` only keeps renewals
    distinct.
  - `aff0f27` — Open 4: shipped programs as content-addressed data, beside the question language rather
    than replacing it.
  - `3e99899` — D2: rules may derive `asked` records, and matching respects scope (no mode rule). The
    wire format is one section in `0-substrate.md`. Agents may be written in any language; definite
    clauses are what the design can check.
  - `775ce0d` — the domain schema: `at(R, S, M)` with a declared `Metric` sum; `r` is a content-addressed
    run id, assumed deterministic in the example (episode keys as the alternative). Runtime schema
    sharing is out of scope.
- **Step 4: DONE** (`72ef3cc`) — 206 links, all resolving. Out of scope and still backticked: references
  between other docs outside the tree (e.g. the ledger's `dead_ends/…`, `definite-clause-maximality.md`).
- **Leases decided** (`88ba806`), recorded under step 3 above.
- **Step 5, comparison with shipped runstate: IN PROGRESS** (2026-10-02). Per concern, one verdict:
  improves / alternative / worse / new. Elegance counts as evidence (Occam; fewer unknown failure
  modes), labelled as such and weighed beside concrete defect traces. Method: one neutral mapper, which
  has not seen the trail (its map is `vs-shipped-map.md` in the session scratchpad), then two advocates in
  parallel, one per side, then reconciliation with the owner one concern at a time. The output is a
  tracked `if-built-today/vs-shipped.md`, or a rewrite of `5-domain.md` §"Today's runstate, mapped".
  - **Map and both advocates DONE.** Scratchpad files: `vs-shipped-map.md`, `vs-shipped-advocate-shipped.md`,
    `vs-shipped-advocate-ibt.md`.
  - **Agreed by both advocates:**
    - *improves*: C3 identity as data, C9 lease withdrawal, C11 no merge in the value plane;
    - *worse*: C2 claim, C4 liveness, C10 ack/refusal, C15 time, C7 operator halt, C20 launching.
    - Most of the "worse" verdicts are things left unspecified. C15 is a real conflict.
  - **Verified by me:**
    - C15. `specs/observer-clock.md` §1 and §7 reject arrival-time and monotonic clocks for cold
      attach. `5-domain.md`'s lease text cites that spec as precedent for the design it replaced.
    - G1's soundness premise ("episodes are sequential") was narrowed by `write-authority.md` rev 4,
      which declares the interleaved series as a known cost.
  - **Next:** walk the reconciliation with the owner, one concern at a time.
  - **Item 1 (factual fixes) DONE**: 14 corrections, `5256352`.
- **Step 5b, prior-art survey: DONE 2026-10-02.** Four families (durable execution, event-sourced
  actors, ML trial stores, asset orchestrators), scored against the 15-concern rubric.
  - **Reports**, untracked in `docs/`: `review-2026-10-02-prior-art-*.md`, plus the comparison map and
    both advocates as `review-2026-10-02-vs-shipped-*.md`.
  - **Verdict:** `positioning.md`'s "does not exist elsewhere" is false as worded. What survives is
    the *combination*. No system adopts cleanly for both consumers.
  - **Verified by me:** DBOS fences every step write with an ownership token checked under a row lock
    (`dbos/_sys_db.py` `_check_owner_txn`, commit `03fb5c9`). That is `write-authority.md`'s
    "distinct acquisition operation" alternative, working in the same SQLite/Postgres shape.
  - **Pending owner decisions:** the `positioning.md` rewrite; the fencing lead ("commands out of
    the worker's stream"); then resume the walk-through at item 2 (C15, time).
- **Owner's decision list, taken one at a time (2026-10-02):**
  1. **`positioning.md` rewrite: DONE**, `f6b6a31`, on `docs/vs-shipped-and-prior-art`.
  2. **Design doc DRAFTED** (`080c4f1`): `docs/backlog/identity-in-records.md`, with an index entry.
     - **Five layers in dependency order:** names on control and lifecycle records; names on values;
       episode-keyed artefacts with R*; time as a trigger with witnessed staleness; fenced writes,
       possibly optional.
     - **Values:** the owner raised encoding efficiency. Measured: a stamp as JSON text adds +22% to
       the median mycooc row, an integer column about +3%. Run-length-encoded names on a second channel
       were rejected as positional; batching inside a record and columnar storage are the options.
     - **Next:** owner review of the doc. Then, in order: layer 1 to specs; the value-encoding
       experiment; R* tested without the fence, which decides layer 5.
  - **Episode-aim marked SUPERSEDED** (`b1a9a9f`), with a note that it might be condensed into
    layer 1's design.
  - **Layer 1 SPECIFIED (2026-10-03, `4752d41`):** `docs/specs/log-formats.md` plus
    `docs/specs/reference-by-name.md`, designed section by section with the owner.
    - **Owner decisions:**
      - the renewing-client gap is accepted (a client helper could shrink it; not built);
      - incremental `Watcher.pending_stops` is built now;
      - log formats are **address-based**: `<root>/v<release>/<rid>.db`, and a schema per format on
        Postgres. The format is outermost so each format owns its layout. It is named by the release
        that introduced it, never inferred and never optional;
      - migration copies, then seals the old log;
      - legacy logs are onboarded by a manual `mv` that the error message instructs; there is no
        `--from` flag.
    - **Specs approved.** The plan is written: `docs/plans/2026-10-03-log-formats-and-reference-by-name.md`
      (`537e1de`), 13 tasks in 2 stages. It also corrected spec §5: the incremental pending-stops form
      keeps the spent ids too.
    - **Next:** owner review of the plan, then subagent-driven execution, the owner's standing preference.
  - **Consumers PINNED (2026-10-03)** instead of migrated, at `72d9c3f`:
    - **mycooc** `36167c11`: the pin in `pyproject.toml` and `requirements-pinned.txt`, installed into
      `cooc`. The cluster sync now `git archive`s the pin instead of rsyncing the working tree;
      `watch_run.sh` no longer sets `PYTHONPATH`; a test keeps the two pin lines identical.
    - **translation** `4963064`: the pin, installed into `base`.
    - **runstate-tui** `aeec073`: `rev` pin, re-locked from `ba26e50` to `72d9c3f`; all gates green.
    - **runstate dev** moved to the repo-local env `./.conda`, which the pre-commit hook prefers
      (`dd44f78`; it replaces the named env from `236d7b2`). pytest also gets
      `pythonpath = ["."]`.
    - The design doc's conditions are revised: migrate per consumer at upgrade, plus a loud
      old-format check.
  3. **Wiki: DECLINED.** System-specific findings stay in runstate.
  4. **`machine-partitioned-logs.md` note: DONE** (§"The gate, run in miniature").
- **Reference by name** (the owner's term; "pairing" was the special case): a record names what it
  answers, ends or concerns, rather than being related to it by position.
  - **Spike DONE: adopt with conditions.** `spike/reference-by-name` from master, pushed with no PR.
    Report: `docs/review-2026-10-02-reference-by-name.md`.
  - **The change:** library and schemas +455/−225, plus a backfill script and 19 new tests (×4
    backends). Committed with `--no-verify`, because 36 pre-existing tests fail by design and were
    deliberately left unedited.
  - **Schemas:**
    - lifecycle-v0.5: `claim_seq` on heartbeat and stopped; `honoured` stop ids on stopped; new
      `lifecycle.bound`, which is lease option (a).
    - subscription-v0.3: stops require a `request_id`.
  - **Order independence:** the named stop, progress and live-episode folds never moved under 2,000×40
    permutations; master's moved in 8–40%.
  - **Backfill on copies of 2,569 real logs:** 2,562 come out identical, and all 7 differences are
    positional bugs that the names fix.
  - **Conditions:**
    - migrate everything in one step; unmigrated heartbeats make `ensure` and `await_consumed` hang;
    - **consumer stop writers must mint request_ids**, which is a consumer-repo change needing owner
      authorisation;
    - accept the gap a renewing client sees between a crash and its next renewal;
    - an incremental `undischarged_stops` (7.5–10 ms at 2,000 stops);
    - rewrite design §7 and the stop-discharge, service-worker and time-lease specs.
- **In flight (2026-10-02):**
  - **Fencing lead: both red-teams DONE** (`docs/review-2026-10-02-fencing-*.md`, untracked).
    - **The two-stream split is dead:** cross-stream pairing by position, the death CAS guarding one
      stream only, and "a moved head means displaced" being false.
    - **Survivor:** single-log fenced worker writes. Every Worker append is a CAS; on refusal the worker
      classifies the tail, and only a foreign `started` displaces it.
    - **Verified:** consumers never call `Worker.emit`; translation has six raw `channel.send` value
      writes.
    - **Ground truth DONE: adopt with five conditions** (`docs/review-2026-10-02-fencing-ground-truth.md`).
      - **Where:** branch `spike/fenced-worker-writes`, pushed with no PR. The change is `worker.py`
        +104/−19, plus 20 new tests.
      - **Suite:** 1057/1 with Postgres.
      - **Verified by me:** the new tests pass on the spike and fail on master (15 of 15 non-PG).
      - **T1, #32:** the displaced worker lands 0 records, against 14–15 on master.
      - **T2:** 0 false displacements in 700 runs.
      - **Costs:**
        - **T4:** a mistaken claim by a claimant that never runs now **wedges** the run (master completes).
        - **N1:** no progress guarantee; Postgres starves above ~8k foreign records/s.
        - **T6:** `tick()` returning True harms consumer artifacts.
      - **Conditions:**
        1. Surface displacement silently, plus a `displaced` property.
        2. A bounded retry.
        3. Pair it with the eliminator or reclaim recipe for T4.
        4. `write-authority.md` revision 5 and `api.md`.
        5. A contention test.
  - **Time-triggered claims: DONE, holds with named conditions.**
    - **Where:** `spike/time-triggered-claims`, pushed with no PR, on top of `spike/fenced-worker-writes`.
      Report: `docs/review-2026-10-02-time-triggered-claims.md`.
    - **The change:** a `ClaimGate` Strategy, `StaleTakeover`, and episode-keyed checkpoints. About 110
      lines of library code. Suite 1081/1 with Postgres; I re-ran it without Postgres: 847/235.
    - **E1:** the wedge recovers. **E2:** checkpoint regressions go from 21/21 to 0/21. **E4:** exactly
      one winner in 890/890 races.
    - **E3, a false death:** costs only waste on the log, the verdict and the artifacts. Raw `send`
      values still splice in 8/36 configurations.
    - **The resume rule I proposed (R0) failed.** R* uses heartbeat or `stopped` vouching inside the
      episode's window after the publish, and prefers the latest vouched episode.
    - **Conditions:**
      - every writer is fenced or episode-tagged;
      - one gate is used at three sites;
      - heartbeat gaps stay below the threshold (translation's one-tick job livelocked: 29 episodes,
        0 completions);
      - clocks are synced;
      - not on sqlite over NFS, which is untested; a broken CAS there would turn the wedge into a
        double claim.
  - **DBOS falsification prototype: DONE.** The hypothesis **fails as stated; a weaker form holds with
    gaps.** Report and code: `docs/review-2026-10-02-dbos-prototype/`, untracked.
    - **Setup:** dbos 3.2.0, a SQLite system database, and a 197-line layer.
    - **Results:** B1, B3, B5, B6 and B7 PASS; B2, B4, B8 and B9 PARTIAL; B3b FAIL (a DBOS upgrade
      strands PENDING runs).
    - **Why it fails as stated:**
      - A DBOS workflow id cannot be the run identity: inputs freeze, and replay re-takes recorded
        decisions. So the layer rebuilt episodes, meaning new ids per episode plus stitching, and
        stop-forwarding, which needs raw SQL on DBOS internals.
      - DBOS has no liveness, and its recovery steals live runs on a shared executor id. So the layer
        rebuilt the heartbeat, the staleness rule and the self-claim.
      - Its fencing does not reach the checkpoint file. A displaced writer regressed the checkpoint
        30→6 in 7 of 7 runs.
  - **Owner's counterfactual:** of the five combination properties, (1), (2) and (5) are negotiable.
    My analysis:
    - (5) has little leverage either way.
    - Dropping (2) lets Temporal or Restate substitute, and the cross-host wedge exists because no
      service plus "time never arbitrates a claim" leaves no trusted clock.
    - (1c), the log as the source of truth for current state, is the expensive part. (1b),
      extend-after-complete, is a real mycooc need.
- **Then: the careful review of the whole stack**, aimed by the comparison's verdicts. Absorbs the
  remaining small items (A3, A5/S2, A6, A8, Open 10) and the per-layer fresh-eyes pass, with each
  reviewer told to read only its layer and that layer's ancestors.

## Where to start

1. This file.
2. `backlog/if-built-today/decisions/3-questions.md` §"Record scope, `asked`, and quantifiers in questions" — the whole trail
   of the 2026-09-25 → 09-30 rewrite, including what was weighed and not taken. Read it before proposing
   anything about the demand layer.
3. `docs/review-fresh-eyes-2026-08-25.md` — older findings; Part 5 (refuted) and Part 6 (survived) still
   bind, **except** Part 5's verdict that `¬(Q₀ ∖ E)` "settles the region correctly", now reversed, and
   Part 1b's "obliged `¬(Q₀∖E)`", now withdrawn.

## What landed (2026-09-25 → 09-30)

- `3c90cd3` — uncontested corrections: D2 (settledness ≠ free termination), "strictly inside" dropped,
  "one fact / two routes", the six up-sets named, restriction is `⊗`, residual's emptiness is settledness,
  the consensus-number framing, a contradicted novelty bullet, dangling references.
- `d64e53d` — **the demand layer**: record-scoped variables; polarity as `told(A, P)`, opt-in; valuation below
  quantification; every record a range-restricted constrained fact; questions as `asked` records with
  explicit per-variable quantifiers; settledness as query completeness in two strengths; vouching and the
  key-granularity trade-off; the traced demand made honest (run key, 743 + 1, "no more steps"); Open 10
  (admission); prior art queued in the ledger as UNVERIFIED.
- `5e7fa9e` — the running example converges at 743 everywhere.

Resolved by those and **struck**: A1, A2, A4 (settledness is affirmable by certificate, not derivable),
A7 (P1), A9 (disputes derived-only is a schema declaration), P3, P5, "exactly the six", restriction, 400 vs
743, the per-demand-holes census question.

## Pending

### A. Design-level, in rough priority order

**A10 — fresh eyes: DONE 2026-09-30** (two readers, neither shown the trail). Clear corrections folded in
`687d418` and `55f8289`. Two items need a decision:

- **D1 — what gates production: the permanent `asked` record, or live interest in it?** The doc says both:
  *"a demand, once recorded, is never withdrawn"* and *"gates a producer's rule"*, against *"recomputing
  the work set from the surviving subscriptions"* and *"self-withdrawal… disconnecting ends it"*. Read
  literally, a querier that disconnects leaves an `asked` record with a non-empty residual, and the
  scheduler relaunches forever. **Proposed:** `asked` stays permanent, as the record that something was
  asked and for routing; the *scheduler* acts on `asked` records whose asker still holds a lease. That is
  control, per §"Demand is control". Self-withdrawal is a lapsed lease; an operator stopping someone else's
  demand is the authority question that §"What it does NOT solve" already names.
- **D2 — how a question is encoded so that rules stay first-order.** A question holds binders as a term.
  Magic propagation (`asked(∃V. loss(r,V,S)) :- asked(∃W. report(r,S,W))`) then needs a head variable that
  occurs in no body literal. One-way matching can bind a body variable to a subterm containing a bound
  variable, which is scope extrusion. Avoiding that needs higher-order pattern matching or de Bruijn
  renumbering, i.e. reflection costs. **Proposed** (logic reader): split a question into a **ground
  quantifier prefix** plus a **matrix whose variables are ordinary record-scoped object variables**, e.g.
  `asked([exists(1)], metric(r, loss, var(1), var(2))) :- var(2) ≥ 1`. Add a mode rule: a rule binds
  variables only at free positions, and builds a derived `asked` head's prefix from ground data. That is
  magic templates with ground adornments, and it is first-order. It also gives the arrival check its
  exemption: bound variables are named in the prefix, not ranged by the body. Note that the prefix is
  `Out` returning, as ground data, but with the v3 semantics: any order, `∃` and `∀`.

Remaining reader findings, judgment rather than defect: about eight "an earlier draft…" asides re-argue
dead models that the decisions file already holds; trim to one-line pointers.
`grep 'When holes in posted terms'` justifies a live rule with a measurement taken under the abandoned
model.

**A3 (D4) — exhaustion as a predicate.** Stream termination is now settledness (folded). What remains: the
doc already reads exhaustion in rule bodies (`stopped(Episode, Outcome)` in the `Fact` sum;
`grep 'discharged(C) :-'`) while the decisions file refuses it a predicate (`grep 'a name is an
invitation'`). The safety argument "the language cannot quantify over it" was **refuted**:
`told(metric(R,loss,V,S), neg) :- stopped(E,R,halted,K), S > K` is a legal, monotone, false clause.
Proposed fix, keeping *unwritable, not forbidden*: a **sort rule** — no `neg`-valued head may read a
literal of process sort. Then exhaustion can be an ordinary dated literal read by control. Pairs with S2.

**A5 — `ensure` is not an exception (verified); queued as S2.** The table's *"may feed demand?"* column is
the error (`grep 'may feed demand'`): the residual already feeds scheduling, the bandit's argmax chooses
the next demand, and different demand only changes which correct subset is produced. Relabel: a report may
never be the **content of a published fact**; it may feed control. `ensure` = residual + dated no-progress
fact + a return condition that is settledness. Also: `grep 'compares two moments'` contradicts the doc's
own repair, *date the observation*. Consumer note from the item-6 check: `ensure` completes on a step
watermark, not per-step witnesses; `history` imposes last-write-wins (the FD) and needs an explicit
multi-witness policy.

**A8 — "no fresh element is ever minted" is false for this fragment. Unverified.** `grep 'no fresh element
is ever minted'`, `grep 'quiescence argument'`. Function symbols and constraint arithmetic
(`expected(S1) :- expected(S), S1 = S+1`) grow terms with atomic heads, so quiescence is per finite demand,
and the stated reason for excluding `∃` in heads no longer separates it from what the fragment admits.
Needs a decision on what then excludes `∃` in heads.

**A6 — rubric findings, remaining.** Argued, unverified: the constraint domain as an undeclared global with
a step-axis answer; the signature-global `Fact` sort (now the sort of `told` terms). Verified:
`grep 'Order is needed in exactly one place'` misses the **death-CAS** in `retire()`
(`runstate/worker.py:322`), whose precondition — no subscribe after the final drain — is absence over an
open key set. Under this design it may be unnecessary (durable demand + a relaunch decider), but the
sentence must say which.

**A11 — Open 10's admission rule** (`grep 'Some residuals never empty'`). Undesigned. Check the no-progress
guard against value-constrained "until" questions (`../specs/control-target.md` R5).

### B. Doc-level

- **821 vs 823 logs** (`grep '82[13] real logs'`) — needs the data, not a guess.
- **Structural suggestions** (judgment): move §"Whose this already is" after §"The language"; promote the
  "sound, never wrong" paragraphs ahead of §CALM; split §"The model" and §"The threshold rule"; a terms
  block, glossary to the front. Most defects in these rounds were propagation failures; one canonical
  statement per concept would shrink that surface. The roster point is restated about six times.

### C. Ledger (`docs/if-built-today-citations.md`)

- **Read and confirm the four entries queued 2026-09-30** (HH(C), Maher, Nelson / Gelfond–Lifschitz, query
  completeness). The doc already cites them in §"Whose this already is"; Maher's title is uncertain.
- **Belnap 1977 has no entry**, yet is quoted twice verbatim with a section locator. Same for Reiter,
  Vickers/Smyth, Oz/Mozart, CHR, Nix/Bazel/Shake and Hellerstein 2010.
- Makowsky: *"§5 unread — restate it precisely before relying on it"*, and the doc relies on it
  (`grep 'Skolemising keeps the least model'`).
- About eleven quoted strings the ledger records differently or not at all (findings Part 4).
- Strike the stale `## UNOBTAINED` block for Bagai & Sunderraman 1995, also marked CONFIRMED.

### D. Carried over

- **Wiki sync.** Not yet filed: the free-instance principle, conflation-is-CWA, asserted vs witnessed, and
  from this round — **a fresh name does not keep an equality local; transitivity through it equates ambient
  terms**; **cross-record sharing under two bindings forces fabrication, possible worlds, or an arbiter**;
  **asking must claim nothing, or the question satisfies itself**; **a key is a claim about what determines
  the value, and its granularity trades honest vouching against visible disagreement**; **"production"
  and "control" split the quantifier question**. Write them as cross-project statements.
- **Consumer census.** Would `lmax` serve the heartbeat plane? Are *strongly* settled regions large enough
  often enough for settled-only aggregation (§Open 9)? A census bounds applicability and cost, never
  soundness.

### E. Simplification queue

- **S2** — *reports may feed control, never be the content of a published fact*, plus A3's sort rule.
- **S3** — delete, don't repair: the Stone-duality framing (`grep 'That is Stone duality'`). The doc
  concedes the space is discrete. Unverified judgment.
- **S4** — semantic keys for the birth claim, `claim(run, after(prev_episode))`: verified for two
  relaunchers and a relauncher that missed a death. Not sequence-free everywhere: the death-CAS (A6) and
  per-link cursors remain.
- **Door, recorded in the doc:** hypothetical questions (HH implication goals) as speculation with nothing
  posted (`grep 'ask hypothetically instead of posting'`).

### F. Housekeeping

- Delete the consumed scratch: `docs/review-{2,3,4,5,6,8}-*-verbatim.md`,
  `docs/review-if-built-today-{analysis,verbatim}.md`, `docs/if-built-today-outline.md`, and
  `dawkins.html` once the Dawkins analogy is filed or dropped. **Keep `review-7-prior-art-verbatim.md`**,
  which the ledger cites.
- Delete `docs/review-fresh-eyes-2026-08-25.md` once A, B and C are consumed.

## Settled in discussion, deliberately not folded in

"Push vs pull" is **not** a semantic choice. The least model is the same either way, derived atoms are an
evictable cache, and forward chaining over this fragment does **not** terminate on its own, because function
symbols let terms grow. Demand facts are what bound the recursion. (A8 is where this contradicts the doc.)

**Scope rule (user, 2026-09-29):** floating-point, interval and real-valued representation are the user's
concern — reals are only ever finitely observed — and the design takes no position on them.

## Ground rules that outlive any session

Commit frequently and push freely (private repo). Consumer repos are read-only without authorization.
Handoffs and scratch stay untracked by decision. Citations enter the doc only via the ledger. Never post a
novelty claim on a sample. Keep a superseded idea only where someone would relitigate it, and argue
against it in the same breath. **Check which sense of "homomorphism" is in play before arguing about
preservation**, and **which scope a variable has before arguing about equality**. And **run the
two-reviewer check before folding a design change**: in this round it overturned three of my own
recommendations (headless goals, `Out` as a list with a fixed prefix, the HH-`∀` analogy), each within a day
of proposing it.
