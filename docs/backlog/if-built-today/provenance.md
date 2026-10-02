# Provenance: objections without retraction

**Layer:** depends on `substrate.md`, `logic.md`; meets `polarity.md` only at diagnosing `{t,f}`. The dependency graph is in `README.md`.

## Retraction's effect, without retraction

The largest apparent sacrifice is that nothing can be taken back, so one bad producer would poison the
store permanently. It does not, and the mechanism needs nothing the base lacks. **Objections are derived**,
from grounds that are ordinary posted facts:

```
disputes(F) :- produced_by(F, S), miscalibrated(S), timing_sensitive(F).
```

`disputes/1` takes a **told record** as a term — `F : Fact`, where `Fact` is the sort of `told(A, P)`
terms — so an objection names a valued fact, polarity included: one can dispute a claim that something is
*false* as readily as a claim that it is true. `disputes` is itself an atom, so objecting to an objection,
`disputes(told(disputes(F), pos))`, needs nothing added. Who objected is provenance on the `disputes`
record rather than an argument, so an objection is worth something only where provenance exists — the
edge drawn above. And because a record may be a region, an objection is **region-scoped exactly as any
tell is**: `told(disputes(told(stopped(episode2, O), pos)), pos) :- outcome(O)` rejects every claimed
outcome for that episode, and a rule bodied on `produced_by(F, bob)` rejects a producer.

What follows:

**It recovers retraction's effect without retracting.** The *evidence* is monotone — `disputes` records
only accumulate, with no roster and no message to anybody. The *fold* is not: a reader accepting `f` unless
it is disputed withdraws its acceptance when a dispute arrives, and a question settled from records a
policy later excludes becomes unsettled. That fold is a reader's decision and sits outside (§"What stays
outside"); what is gained is that nothing in the store was destroyed to get it.

**It is strictly better than deletion, not equivalent with extra steps.** A derived `disputes(f)` is
permanent like everything else, so an objection cannot be un-derived — but nothing was ever removed. `f` is
still there, counter-grounds are postable, and a reader seeing both folds differently. Under real deletion
an erroneous deletion destroys `f` and no later discovery recovers it. **Readjudication is free here and
impossible there.**

**You cannot object without grounds — if the schema says so.** Nothing stops a bare post of `disputes(f)`;
what makes objections derived-only is a signature declaring `disputes` a derived relation, and then a
receiver rejects a posted one on the message alone, as it rejects a type error. With that declaration,
reasons enter the shared record and bare suspicion — *"I simply do not trust Bob"* — is holdable as policy
but not postable as fact. Grounds are only as good as the facts they cite, and `distrust(bob)` is a fact
anyone can post; the constraint moves suspicion into the open rather than forbidding it.

**Upholding is `told(disputes(F), neg)`**, so the polarization branch applies to the annotation branch
unchanged: conflicting objections get `{t,f}` and the Belnap reading with no new machinery.

**And this is the one place readers legitimately diverge.** Evidence is shared and permanent; the *fold*
from evidence to acceptance is per-reader, so two readers with different policies reach different answers.
That is a genuine exception to everything-converges, and the right one — trust is a policy, not a fact.

## Speculation, which needs nothing new and is not recommended

Blocking is not the only way to handle a branch whose guard is undecided. An agent may **speculate**:
suppose that an unknown is not `a`, proceed down the else branch, and let a later resolution to `a` produce
a contradiction. Two things are owed. The supposition cannot be posted as a fact — the agent does not know
it, and *post what you know* forbids it — so it is posted as what it is, a fact about supposing:
`supposed(dif(best_of(r), a))`. And with variables scoped to records the unknown is named by its
definition, `best_of(r)`, so the contradiction comes from a rule relating `supposed(dif(…))` to a later
`best(r, a)`. With no functional dependency `best_of(r)` may resolve to several values, and the supposition
is contradicted if any of them is `a`. Nothing else has to be added.

| what it needs | where it already is |
|---|---|
| `supposed(dif(best_of(r), a))` as an ordinary post | a declared relation, like any other |
| a contradiction that does not explode | `{t,f}`, affirmable and **inert** |
| finding what the contradiction poisoned | provenance |
| declining the poisoned derivations | the trust policy above |

**And two decisions taken separately turn out to need each other.** Because conflict is **inert** here —
4QL's `i`-propagation is declined — a violated `dif` does not automatically taint what was derived from it,
so provenance is what locates the damage. Propagating conflict would over-poison; no provenance would
under-poison, silently. Precise poisoning needs exactly that pair, and neither was chosen for this.

There is no backtracking to simulate, because nothing is undone. What speculation produces is
**abandonment with a permanent record**: the derivations stay, marked.

**Its cost is not soundness.** A speculative derivation is not a false assertion but a **contingent** one —
true given its recorded hypotheses — and an implication whose antecedent turns out false is *vacuous*, not
wrong. So *"a partial store is sound, never wrong"* (§CALM) survives intact. The hypothesis's own state is
read by machinery already here: pending is `∅`, confirmed `⊒{t}`, refuted `⊒{f}`.

**What it costs is that facts stop being autonomous.** Every other record here means what it says on its
own; a speculative derivation means what it says *given* something still open. Two consequences follow.
Provenance becomes a **requirement** rather than an enhancement — and it must live **in the record**, since
under partial replication (§Open) a conclusion that outran its premises would be a bare assertion again,
which is `README.md` §"Two commitments" applied to derivations rather than to episodes. And the remaining cost is
ordinary waste: work done under an antecedent that fails is work done.

**So blocking is the default for being simpler, not for being safer.** A blocked guard needs no provenance
and wastes nothing; speculation buys progress under uncertainty and pays for it in bookkeeping.

**A third way, unworked: ask hypothetically instead of posting.** Hereditary Harrop logic allows
implication goals, `D ⊃ G` — *"supposing `D`, is `G` decided?"* — whose hypothesis lives only inside the
proof of that one question and is never told. That is speculation with nothing posted, so neither
provenance nor trust policy is needed to undo it. Questions here do not take implication goals; this is
the door if speculation is ever wanted.

