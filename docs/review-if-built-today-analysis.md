# Analysis of the `if-built-today` review

**Untracked working note, 2026-08-07.** Companion to `review-if-built-today-verbatim.md`, which
holds the reviewer's report unedited. This file is my assessment of it. **Nothing has been changed
in the design documents yet** — the owner asked to discuss before editing.

---

## 0. Context, for a reader arriving cold

**`runstate`** is a Python library: an append-only per-run log (sqlite / Postgres / in-memory
backends) plus read-side folds. Its contribution is *a run as a durable identity that outlives the
processes executing it* — attempt 4 resumes from step 400 and is the same run as attempts 1–3.
`docs/positioning.md` and `docs/layers.md` are the orientation documents.

**What was under review.** A long design conversation produced two forward-looking entries:

- **`docs/backlog/demand-driven-reads.md`** — the interface a Bayesian optimiser or bandit actually
  wants: query a mostly-unmaterialised relation `(config, step, metric) → value`, where querying an
  absent region *causes it to be produced*, and results stream as they arrive.
- **`docs/backlog/if-built-today.md`** — what runstate would be if rebuilt from scratch today: a
  **relational schema** rather than a log, with episode identity as a **column** rather than derived
  from log position; `read` / `force` / `push` as the interface; demand as a derived relation
  (magic sets); and **monotonicity** — not append-only — as the load-bearing property.

**Why it was reviewed.** It was produced in a single conversational pass, and the immediately
preceding designs in this repo were refuted, three of them by material already in the repo. The
reviewer was told to prototype and measure, that "this does not work" was an expected outcome, and
to edit nothing.

**Provenance of the ideas**, since it matters for who should adjudicate what:

| idea | origin |
|---|---|
| relational schema; episode id as a column; the defect→column table | mine |
| "every mutation must be a join" | mine |
| the magic-sets internalisation, guard-as-rule-body | mine (answering "what would level 2 look like?") |
| demand-driven / residual-to-handler / `?` sentinel / forcing rounds | the owner |
| two semilattices, split into two tables; "one semilattice per table" | the owner |
| `settled` = coatomicity rather than ACC; freeze as an ordinary write | the owner |

---

## 1. The verdict, and what I verified myself

**Reviewer's verdict:** the headline is wrong; the stated first invariant is falsified by shipped
code; the value plane is over-determined into unimplementability.

I independently verified the two findings that most implicate what I wrote:

- **`progress` deliberately DECREASES.** `runstate/observables.py`, verbatim: *"It follows that this
  value may DECREASE across an episode boundary… **A monotone watermark here would re-open the
  splice it just closed.**"* So "every mutation must be a join", which I called *"the first
  invariant to write down"*, is already false of shipped code — deliberately, in the fold the design
  most needs to reproduce.
- **The doc really does assert two incompatible value lattices.** `if-built-today.md:28`
  ("flat-ish, ACC") and `:182` ("pairwise incomparable") vs `:96` ("a join with a later element
  under the LWW order").

I did not re-run the reviewer's prototypes; everything else below is its measurement, and where I
disagree I say so.

---

## 2. Findings I accept — these change the design

**(a) Only 1 of 3 defects dies, and the split is principled.** Measured under the schema:
the *displaced worker's own terminal read as the run's verdict* dies (the episode FK genuinely kills
it); the *claim cascade* and the *forged verdict truncating reuse* survive unchanged.

The pattern: **attribution defects die; forgery defects do not.** My headline conflated them, and
two of my three examples were forgery. Since the design's own rules say enforcement is unchanged
(`:71`, `:82`), every forgery in the reviewer's tests is a *legal join* under my own invariant. The
column moves *where a release is written*, never *who may write it*.

**(b) The claim mechanism is a net regression, not merely "not the CAS."**

- Crashed holder → the `live` row persists → successor gets a unique violation → **stranded
  forever.** Today `resolve()` probes the dead pid and the successor claims. I asserted this strands
  the run "exactly as the old design did"; it does not — the old design recovers.
- The release `UPDATE`, replayed once (timeout / reconnect / at-least-once), **released a successor
  that had already claimed.** `send(expected_seq=)` cannot have that bug: a replayed append with a
  stale expectation is *provably* lost and returns `None`.
- Formally it is **test-and-set** (consensus number 2), not compare-and-swap (∞) — a distinction
  `docs/layers.md` already cites by name. It also loses the normative `None`-vs-raise distinction
  (provable loss vs indeterminate fault) that every backend conformance test pins.

**(c) "Every mutation must be a join" needs an exemption it does not have.** See §1. The rule and
`progress` cannot both stand as written.

**(d) The two monotonicity fixes do not compose.** Round-indexed demand and recursive demand were
designed separately and never checked together. Measured: a demand withdrawn at round 2 spawned a
sub-demand at round 3 *for a round already past*. And the dilemma is sharp — **the `T` column must
be both a clock the evaluator advances and data a handler joins against later, and it cannot be
both.** Under the doc's own definition of activity, withdrawal never works at all; the repair kills
the dependency graph the design says it most needs to allow.

**(e) The grid is not a finiteness witness** for either motivating consumer. An unbounded step axis
is syntactically range-restricted and still diverges — range-restriction is a syntactic test over a
relation *asserted* finite, it does not make one finite. And a bandit's grid derives from the values
it is meant to bound (`grid ← value ← demand ← grid`, one SCC). It works only for a fixed sweep,
i.e. the case that never needed demand-driven evaluation.

**(f) Magic sets: the category error is real, but not the one I guessed.** Production-triggered-by-
demand does *not* by itself void the semantics (measured). It breaks because **over-filling** —
which `demand-driven-reads.md` says is *forced*, since `loss@100` needs 1–99 — puts facts in the
store the transformed program cannot derive (|model| = 1, |store| = 5). So `value` must be an EDB
table read directly, and the demand rules are then *"a side-effecting scheduler wearing a Datalog
costume."* That removes the theorem I was leaning on, though not the mechanism.

**(g) Opaque key vs indexable range: 275×.** GIN on JSONB serves *containment*, not *order*, and the
design's central read is a **range** over an axis. A btree per key field fixes it — but that
requires knowing the axes, which `:42` ("a domain-supplied record, not fixed axes") forbids. These
two are in direct tension and the doc claims both.

**(h) Replayable reads have no replacement.** A table storing the join destroys the divergent pair,
which is exactly what `demand-driven-reads.md`'s own "identity (no compression)" lattice and
"are there incomparable elements?" row need. **The two companion documents ask the substrate for
opposite things.** And `LISTEN`/`NOTIFY` measured lossy across a reconnect, where `read(after=seq)`
is replayable with a caller-owned cursor — and `Watcher`'s replay-from-0 is a documented public
guarantee, not an implementation detail.

**(i) Several unlisted drops**, of which the most concrete: `Heartbeat.consumed_seq` is a *public
wire field* in the JSON schema; `lifecycle.stopped` is read by four rules with different scoping
that one `status` column must preserve; and `MemoryChannel` is 118 lines whereas the proposal needs
partial unique indexes, JSONB, GIN and recursive CTEs — so `layers.md`'s layer 0, *"the only layer
whose guarantees survive an uncooperative writer"*, does not survive.

---

## 3. Where I would push back

**Finding 7 ("one semilattice per table is not satisfiable") may confirm the rule rather than refute
it.** The survey found ~20 stored things: 2 genuinely LWW, **7 wanting "must be identical /
write-once"**, 3 wanting set-union, 2 with no join.

But *"must be identical"* **is** a semilattice — flat, with ⊤ on conflict. So those 7 share one
join and want one table. Set-union is another. That is *more tables*, which is exactly what "any
number of tables" permits; the reviewer's own note that the 7 "already hand-roll a read-before-write
guard — 4 spellings of one missing primitive" reads as evidence *for* naming the join once, not
against.

What survives from that finding, and is real: the **2 with no join at all**, and the holistic
aggregates (median, percentile rank, argmax-with-tiebreak, permutation test) that need the free
multiset. The `input_provenance` case is the sharpest — its reader's whole purpose is to report keys
where the set is *non-singleton*, so a collapsing join makes a documented safeguard silently vacuous.
Though note even that is expressible as the powerset (free) semilattice, which the design already
lists.

**The flat-vs-LWW contradiction is real in the document, and probably repairable in the design.** We
settled in conversation on **lex(revision, value) with value flat** — one lattice, two components —
and that never reached the page. So the criticism of the text is correct. What may survive: lex over
revisions has infinite ascending chains, hence no coatoms, hence `settled` never fires — *unless*
settledness comes from **freezing**, which is a value-level fact independent of revision. I believe
that repairs it; the doc does not say so, so the reviewer was right to hit it.

**One clean hit on me.** I justified freeze-determinism with "one handler owns a cell via the claim."
Finding 1 shows the claim does not deliver that, so ⊤ is the only real protection. The determinism
claim holds; my reason for it was wrong.

---

## 4. What I think survives, and is worth keeping

- **The attribution fix is real and narrow.** A terminal↔episode FK kills a genuine shipped bug.
- **A `discharged_by` FK would kill a second** — and the corpus measures the harm: **11 of 37 stops
  discharged by a record the worker did not write, 6 of them malformed**. That is `#39`.
- **"Monotonicity, not append-only" is correct as a reframing**, independent of the schema.
- **Freeze-as-write is confluent** — LVish's determinism goal, achieved, and better than LVish's own
  metalogical freeze.
- **Herbrand coatomicity = groundness holds**, decidable by traversal. The over-reach was "perfect
  GC": a per-cell settledness test does not decide a query over a *range*, so GC inherits the grid
  problem.
- **A point in the design's favour the doc never made** (the reviewer's, worth keeping):
  `observables.py:203-229` already separates windowing (positional) from attribution (by
  `request_id`), and its *stated* reason for keeping the positional window is malformed-record
  poisoning — *"an append-only log cannot retract it."* **A mutable schema removes that reason.**
  That is the closest thing in the repo to a worked example of the rewrite actually paying.

---

## 5. Open questions for the owner

1. **Rewrite the doc around the narrow claim, or keep it and append the refutations?** The narrow
   claim — *positional attribution is a real and fixable defect class; forgery is not touched by
   schemas* — is defensible and useful. The general cure is not.
2. **Is the finding-7 pushback right**, or was "one semilattice per table" meant more strictly than
   I transcribed it?
3. **Does the lex(revision, flat) resolution match what was intended?** It was agreed verbally and
   never written down, so the doc currently contradicts itself.
4. Two items remain queued from earlier and unaddressed: the **sentinel-default-claim check**, and
   whether `episode-aim.md` (PR #51) should be built, since the review's finding (a) bears directly
   on it — aim fixes attribution, which is exactly the class that *does* respond to this treatment.

---

## 6. Reproduction

Branch `spec/episode-aim` at `7d2b70e`; nothing was modified. The reviewer's prototypes are in the
session scratchpad (`attack1.py`, `attack2.py`, `attack7.py`, `attack8.py`, `rs_cascade.py`,
`rs_crash.py`, `rs_b2.py`, and XSB programs `ms1.P`, `ms2.P`, `ms45.P`). Tooling available:
SWI-Prolog 10.0.0 at `~/miniconda3/envs/swipl/bin/swipl`, XSB 5.0 at `~/opt/XSB/bin/xsb`, Postgres
binaries at `~/miniconda3/bin`.

Baseline suite: **814 passed, 224 skipped** (no `RUNSTATE_TEST_PG_DSN`).
