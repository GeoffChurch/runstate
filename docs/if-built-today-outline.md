# Rewrite outline — `backlog/if-built-today.md`

**Untracked working note, 2026-08-15.** React to the *shape* before the rewrite is spent. Companion to
`handoff-if-built-today.md` (what is outstanding) and `if-built-today-citations.md` (what is verified).

Current doc: **1,660 lines**. Target: **1,050–1,150** plus a companion `decisions.md` — *not* 900. The
arithmetic is at the bottom; an earlier version of this outline was off by about 5×.

## The spine — and the bridge it was missing

One claim, and everything else is support or consequence:

> **Two polarised relations, positive geometric logic, monotone accumulation — therefore coordination-free.
> Falsity is told rather than inferred, which is what keeps it in that class.**

**But §1 is a *different* thesis, and the document's only measurement supports that one.** *"11 of 37 stops
discharged by a record the worker did not write"* supports **identity-as-data** — a schema decision that
depends on none of the above. A reader who accepts the whole spine still has no reason to believe it buys
the headline. Eight reviews and twenty-five corrections missed this because each worked inside one spine.

**The joining argument is true and is nowhere in the document:**

> **A store that cannot retract cannot re-attribute.** The mis-aimed heartbeat, the displaced worker's
> terminal, the blindly discharged stop are records that would be *fixable* under update and are
> *permanent* under append-only. So monotone accumulation is exactly what makes attribution-by-position
> fatal, and identity-as-data is not one repair among several — it is the only one available.

That paragraph goes in §1. Without it, demote §1 to *"what prompted this"* and let the spine carry the
document alone.

Three structural facts carry it, and each is now backed rather than argued:

1. **The layer boundary is the CALM boundary.** Everything non-monotone — the liveness probe, the temporal
   delta, the fixpoint test, closure-derived absence — sits *below* the logic and crosses up as a
   timestamped fact. The logic has needed no change under a week of pressure; each challenger was
   reclassified, not accommodated.
2. **Falsity asserted, not inferred**, is what puts the design at `F₀ = A₀ = M` rather than in the weaker
   policy-aware classes. Its cost — a party must *know* — is real and is paid by naming the routes to
   knowing.
3. **Refusing to collapse** is the differentiator, and it is citable against three named alternatives:
   Scott's information systems give an inconsistent token set **no state**; CCP **removes** the
   consistency structure and makes `false` an explosive top; tombstones are a **chain**, so *told-both* is
   unrepresentable.

## Structure

| § | content | fate |
|---|---|---|
| 1 | **The headline** — attribution defects die, forgery does not; the corpus measurement | keep, unchanged |
| 2 | **Whose this already is** | **NEW, and early** |
| 3 | **The model** — post `Q` or `¬Q`; two polarised relations; set semantics; no cells | keep, compress |
| 4 | **The logic** — positive geometric logic; the threshold rule and its five instances | merge two sections |
| 5 | **Falsity is told, and how a party comes to know it** | **restructured** |
| 6 | **CALM** — corrected against the proof | rewritten, keep |
| 7 | **Two layers, and what crosses** | **promoted** from §10 |
| 8 | **Demand** — quantifier-in-the-posting; subsumption; the demand language | keep, compress |
| 9 | **Types** — per functor, structural | keep, compress |
| 10 | **What is checked, what is the requester's** — incl. eviction vs retraction | keep |
| 11 | **What survives from runstate** / **What it does not solve** / **The honest cost** | keep |
| 12 | **Open** | keep, re-numbered |
| 13 | **Related, and citations** | expand from the ledger |

**Three current sections had no row and account for 315 lines (19%).** Added:

| § | content | fate |
|---|---|---|
| 4b | §"Where the rules come from: an atom's status" (174 lines) | **reduce to conclusions + the up-set enumeration.** Keep `∅`/`⊒{t}`/`⊒{f}` as objects — §6's *"`∅` is unanswerable without a coordination round"* needs them, and so does the unwritability of `= {f}`. Compress the *four-valued-logic voice*, keep the extent pair |
| 6b | §"No functional dependency, and why" (124 lines) | **keep — it holds the largest measurement** (823 logs, 2.5M records, 0.34% / 0.072%) and **`conflicted` as an affirmable predicate**, which the handoff calls *the* unclaimed contribution. Reduce to conclusions + measurements; the powerdomain/completion apparatus goes to `dead_ends/` |
| 10b | §"What the substrate is for" (17 lines) | fold into §10; four jobs are three |

**And one section that does not exist and should: "the residual, assembled."** The production path is
currently four hops across four sections — the residual is the `∅`-region; `∅` is the one status not
readable; `∅` is unanswerable without coordination; the escape is that it is local, best-effort and never
output. **Compressing the Belnap framing to a sentence severs that chain.** It is the design's most
load-bearing multi-hop argument and it is nowhere stated in one place.

### §2 — "Whose this already is" (new, and the credibility play)

Placed second, before any claim, so nothing later reads as an unearned discovery:

- **The store is 4QL's.** Definition 5 — an interpretation is a set of ground *literals*, four values
  derived from membership of `ℓ` and `¬ℓ`. Negation in rule heads is told-false. OWA over CWA chosen *to
  keep the base monotone*, their words.
- **The logic is a fragment of Jakl's** (thesis Ch. 6 / JJP MFPS 2016): arbitrary joins, finite meets,
  four constants, nothing else; the information order primitive and the truth order *definable* from it;
  polarised atoms a **theorem** there. Soundness and completeness proved.
- **The regions are KKR's** — a generalized tuple is a quantifier-free conjunction of constraints; a
  generalized relation a finite set of them. "Finitely representable = a finite union of
  constraint-described regions" is theirs.
- **The accumulate-both-polarities store exists in a proof** — TODS Prop. 4.6's `R_notMsg` / `R_notMem` /
  `R_known`, broadcast peer-to-peer, never deleted, per-atom presence test over the pair.
- **CCP is the ask/tell model**, and made the storage decision first — but *inverted*: it drops the
  consistency structure and then makes `false` the explosive top, identified with divergence.
- **What is ours**, stated narrowly: falsity **asserted** as the primitive act (nobody in either lineage
  exhibits an agent doing this); refusing the tombstone chain so *told-both* survives; demand-driven with
  the store as cache; and the distribution — `F₀ = A₀ = M`, **no party roster and no self-identity**.

### §5 — restructured: the routes to knowing

`¬Q` is the primitive. **How a party comes to know one is a list**, and this is where the closure
machinery returns — reclassified, not reinstated:

- a **converged producer** — the run ended at 400, so nothing at 500;
- a **solver** proving a region unsatisfiable;
- a **closed producer set** — everyone who could produce here is done and no more will appear. Costs an
  `All` query, hence a coordination round, **priced exactly** by Cor. 17 ∘ Cor. 13. Paid **once**, scoped
  per-functor, and the conclusion is posted as an ordinary `¬Q` that everything downstream reads
  coordination-free.

That third route is NAF-like in effect and positive in form — every premise is a posted fact, and the
non-monotonicity concentrates in one attributable claim. It belongs on §7's shelf beside the pid probe.

**And this section should name the design's characteristic manoeuvre, once, so three others stop
re-deriving it:**

> **Make the assumption explicit, post it, scope it, pay for it once.**

Four instances, arrived at separately: producer-set closure as a route to `¬Q`; **signature agreement**;
**sort closure** (below); and the original CWA toggle — which was this same move applied at the wrong
granularity, which is why it failed rather than why the move is wrong.

### §7 — promoted, because it turned out to be the load-bearing structural insight

The core makes observations the logic cannot; the logic derives over them. What crosses up: an OS probe,
a clock, a temporal delta, a fixpoint test, and closure-derived absence — each **timestamped, as a fact
about the past**, carrying who observed and by whose clock. **The boundary coincides with the CALM
boundary**, which is why it has held.

## Deletions

⚠️ **Three of these are not safe as stated — something later leans on each.**

| what | why | where it goes |
|---|---|---|
| §"Why merging is declined" — **~71 lines** | 64 of them price an option the first six delete | `dead_ends/`. ⚠️ **Carry one clause**: the clash-test measurement is scoped to **linear** terms and this is the only place that is established. `f(X,X)` vs `f(a,b)` is positionwise compatible and does not unify — without the qualifier the measurement becomes an unqualified claim that is false |
| Containment compaction | no consumer on either path | delete — **safe**, the doc already names its surviving home |
| The sheaf paragraph | refuted and recorded | ⚠️ **its conclusion is cited by Open #6.** *"The semantics is **therefore** defined as if demand were the only channel"* is the sheaf paragraph's payoff; re-anchor it to the demand-gating argument first, then delete |
| The residual as an "exception" | it is control, not a negation-bearing message | ⚠️ **§CALM leans on it** — *"That is the line §'Two exceptions' already draws"* — and leans **wrongly**, since that section draws the reports-may-not-feed-demand line, not internal-vs-output. Both need rewriting, not just the target |
| The Belnap **framing** | one value unobservable, other three are conjunctions of two presence checks; measured, nothing is lost | one sentence as an *observable*, vocabulary cited to Belnap |
| Continuous carriers, 13 lines | it is a theorem, not an analogy — `⇈c` is the basis, `↑c` coincides in the algebraic case | one sentence |
| Four names for one object | call table / registry / work set / cursor are one structure | name once |
| The residual as an "exception" | it is control, not a negation-bearing message | §"Two exceptions" becomes one |

## Corrections to carry (all committed, none yet reflected in structure)

`∀`-positive is writable, so not a gap · covering ≠ deciding · settledness needs a fixed extent · the
covering asymmetry is informational, not logical · `¬Q` is a polarised atom, not a negation · CALM is
model-relative, existential over placements, and constrains queries not operators · LCW's differentiator
is that **nothing is retracted and the entailment lapses silently** · `∅` is unanswerable without
coordination, and the constraint binds on **output**.

## ⚠️ §5's third route is a DESIGN CHANGE, not a restructuring

The closed-producer-set route must be **argued**, not slipped in as editorial. Four passages currently
say the opposite, one of which anticipates it by name:

- *"**Exhaustion never enters the derivation layer, and keeping it out is load-bearing rather than tidy**
  … Give it a predicate and somebody will quantify over it — *'every producer for `Q` is done, and no
  atom of `Q` is `{t}`, therefore…'* — which is the unanimity move again."* **That sentence is route 3.**
- The per-functor scopes and the **write-authority** worry were deleted together with the toggle; route 3
  restores scoping and says nothing about who may declare a set closed.
- *"exactly one such query left — single-spawn"* becomes two.
- §7's cross-up list says *"**Exhaustion** is not a fifth entry"*; the outline adds a fifth.

**The defence is available and must be written:** the `All` query happens **below** the logic, and only
the `¬Q` crosses up — so no predicate exists for a rule to quantify over. But note the measured stake:
unanimity-derived falsity **descends 8 times in 360 extensions** where union descends 0, so a wrong
closure claim produces a false `¬Q` **systematically**, not by carelessness. Put this under design
changes, with the write-authority question reopened.

## Coherence fixes to land during the rewrite, not before

Four passages currently contradict something else in the document. Fix in place, do not patch first —
the doc is already over-patched.

1. **The sheaf paragraph** is alive while another section says the topology is inert and `dead_ends/`
   records it refuted. Its `↑p` is over *partial terms*, which the point-set statement rules out. Broken
   on both readings.
2. **"`Q` and `¬Q` are two unrelated relations, and no rule connects them"** is contradicted four lines
   later (*"what it does instead is derive an ordinary atom"*) and 250 lines earlier — a body may claim
   `⊒{t,f}`, which **is** reading both polarities of one atom. Fix: *"no **axiom** connects them."* And the
   *ex falso* argument given is the wrong one; the right one — `⊥ ⊢ B` needs a schematic head, and a
   definite clause's head is a specific atom — is in the handoff and not the doc.
3. **"bounded"** survives in three places after being retracted, one of them **citing the retracting
   section as authority**.
4. **"There is no `demand_p` relation"** vs the internalised demand relation. Both can be true — the ban
   is on *gating* — but nothing says so. And the internalised relation ranges over `Open`, which is a
   sort the doc calls unbuilt.

Lower tier, all with quotes and line numbers in the review: *closure over an extent* reinstated as a
positive result; Open #4 already answered in the body; *"the citation is outstanding"* 177 lines after it
is given; "four hazards" over a two-row table; "four jobs" against its own body; **atom** meaning both a
point and a record; **cell** used three times after being abolished, once about *this* design; one stale
`:NNN` reference; six `§"…"` pointers aimed at bold leads rather than headers, and one aimed at
`CLAUDE.md`; two corpus sizes (823 vs 821) unreconciled; four unreconcilable counts in §"The honest cost";
and §"Terminology hazard" holding one entry while **five** collisions are live — *membership*, *finite*,
*closed*, *union*, *linear*. Note the rewrite **worsens** *closed*, since §5 reintroduces "closure".

## The arithmetic, which the first version of this outline got wrong

| | lines |
|---|---|
| current | 1,660 |
| named deletions | −127 |
| named additions | +85 |
| **after every named change** | **~1,618** |

The named changes deliver **7% of a 45% reduction**. Two levers are needed and neither was listed:

- **The revision-history voice.** 29 sites narrate their own correction (*"an earlier draft…"*, *"had it
  backwards"*). Stating conclusions without the retractions saves ~90–120 lines **and** is why the
  document reads as unstable. But the retraction narrative is what stops re-proposal, so it moves to a
  companion **`decisions.md`** rather than being deleted.
- **The 315 unmapped lines**, reduced to conclusions + measurements, with the apparatus relocated.

Even both leave ~290 short of 900. **Hence the revised target: 1,050–1,150 plus `decisions.md`.**

## Open questions the outline does not settle

1. ~~**Disequality.**~~ **RESOLVED — and it becomes a rule, in §9 (Types).** Disequality is a
   *constraint-domain* predicate, never a logical connective, so it was never in the geometric layer.
   What the `H`/`M` distinction tracks is that `≠` is not **homomorphism-preserved** (a homomorphism may
   identify two constants), which is the doc's own *"two unbound variables may yet be identified"*
   arriving a third time. And it is a **type-level CWA**: `≠` is licensed only by the Unique Name
   Assumption, which is literally one of the three components of Reiter's CWA formalisation (Denecker
   Def. 2, verified). So:

   > **Disequality over a *closed* sort is sugar for a finite `∨` of equalities — positive, geometric,
   > homomorphism-preserved, free. Over an *open* sort it is a genuine primitive and costs `H → M`.**

   Marking a sort closed is a **signature-level** fact (sorts are program-level), so it rides the
   existing deployment channel rather than needing a runtime `All` query — landing beside design change 8,
   same channel, same failure mode if two agents disagree. And the one place `≠` looked forced — the
   residual over the unbounded step axis — **decomposes into intervals** (`1..4 ∨ 6..8 ∨ 10..∞`), which
   are `≤`/`>`, not disequalities. If that decomposition always works we stay in `H` and never pay. The
   doc's own `all_different` example is already the closed case.
2. Whether §2's honesty is better as a section or threaded through. A section is easier to write and
   easier to attack; threading is harder to skim past.
3. Whether **option 3** (polarity as a closed key column) goes in this rewrite or waits.
4. Review 7's remaining retraction list — deliberately unchecked, because roughly half of it lives in
   text this outline deletes. **Verify only the survivors, after the shape is agreed.**
