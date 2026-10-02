# Provenance: objections without retraction

**Layer:** depends on [`0-substrate.md`](0-substrate.md), [`1-logic.md`](1-logic.md), [`2-polarity.md`](2-polarity.md). The dependency graph is in [`README.md`](README.md).
The dispute mechanism itself uses no polarity and survives a reader who rejects it; upholding, diagnosing
`{t,f}` and speculation use the statuses.

## Retraction's effect, without retraction

The largest apparent sacrifice is that nothing can be taken back, so one bad producer would poison the
store permanently. It does not, and the mechanism needs nothing the base lacks. **Objections are derived**,
from grounds that are ordinary posted facts:

```
disputes(F) :- produced_by(F, S), miscalibrated(S), timing_sensitive(F).
```

`disputes/1` takes a **posted record** as a term — `F : Fact`, the sort of records — so an objection
names exactly what was posted. Where a schema declares polarity, a record includes its polarity, and one
can dispute a claim that something is *false* as readily as a claim that it is true. `disputes` is itself
a relation, so objecting to an objection, `disputes(disputes(F))`, needs nothing added. Who objected is
provenance on the `disputes` record rather than an argument, so an objection is worth something only where
provenance exists — the edge drawn in [`README.md`](README.md) §"What gets built on top". And because a record may be a
region, an objection is **region-scoped exactly as any record is**: `disputes(verdict(x, O)) :-
outcome(O)` rejects every claimed verdict for `x`, and a rule bodied on `produced_by(F, bob)` rejects a
producer.

What follows:

**It recovers retraction's effect without retracting.** What is monotone is the evidence and every
reading of it that only climbs: `disputes` records only accumulate, with no roster and no message to
anybody; per fact, the pair *(grounds for, objections)* only climbs; and a policy phrased as an allow-list
— *use records from sources I trust* — is monotone too. What is not monotone is the **decision** *accept
unless disputed*, which a later objection withdraws; and a question settled from records that decision
later excludes becomes unsettled. The decision is a reader's and sits outside ([`1-logic.md`](1-logic.md) §"What stays
outside"). Nothing in the store was destroyed to get it.

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

**Where polarity is declared, upholding is `told(disputes(F), neg)`**, so the polarization branch applies to the annotation branch
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
wrong. So *"a partial store is sound, never wrong"* ([`1-logic.md`](1-logic.md) §"CALM") survives intact. The hypothesis's own state is
read by machinery already here: pending is `∅`, confirmed `⊒{t}`, refuted `⊒{f}`.

**What it costs is that facts stop being autonomous.** Every other record here means what it says on its
own; a speculative derivation means what it says *given* something still open. Two consequences follow.
Provenance becomes a **requirement** rather than an enhancement — and it must live **in the record**, since
under partial replication ([`open.md`](open.md)) a conclusion that outran its premises would be a bare assertion
again, which is [`README.md`](README.md) §"Two commitments" applied to derivations rather than to posted records. And the remaining cost is
ordinary waste: work done under an antecedent that fails is work done.

**So blocking is the default for being simpler, not for being safer.** A blocked guard needs no provenance
and wastes nothing; speculation buys progress under uncertainty and pays for it in bookkeeping.

**A third way, unworked: ask hypothetically instead of posting.** Hereditary Harrop logic allows
implication goals, `D ⊃ G` — *"supposing `D`, is `G` decided?"* — whose hypothesis lives only inside the
proof of that one question and is never told. That is speculation with nothing posted, so neither
provenance nor trust policy is needed to undo it. Questions here do not take implication goals; this is
the door if speculation is ever wanted.

