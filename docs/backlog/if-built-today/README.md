# If built today: a monotone store of polarised literals

**Status:** a design in its own right, not a migration plan. It is written standalone — the question is
whether *this* is a good design, not whether it beats what exists.

**What it is for.** Several agents, spread over hosts, cooperating on work that takes hours and may die
partway: producing values, asking each other for values, and having to agree about what has been produced
and what never will be.

**The regime is a ratio.** Morton (2017) names it the **slow inconsistent regime**: *slow* — analysis
happens on the same timescale on which information is collected and transmitted; *inconsistent* — agents
*"because analysis is slow never reach consensus,"* and may hold irreconcilably different views. The test
is checkable and scale-free. Four hosts running six-hour jobs are in it for the same reason star systems
are: the answer is wanted while the thing it is about is still changing. Only the constant differs, and
the interstellar case is merely where the ratio is impossible to argue with.

The hard part is not the producing. It is that the data is elsewhere, messages are slow and lossy, agents
come and go, and everybody must still reach the same answer without stopping to confer.

**The layers, one file each.** This file holds what spans all of them; each layer is its own document, so
it can be reviewed and discussed alone.

| file | layer | depends on |
|---|---|---|
| `substrate.md` | records, variables, the one operation, what the substrate must provide | — |
| `logic.md` | definite clauses over constraints; CALM; the monotone layer and its non-monotone core | substrate |
| `polarity.md` | told falsity, the four statuses, the threshold rule, settledness | substrate, logic |
| `questions.md` | `asked`, quantifiers in questions, the residual, demand as control | substrate, logic, polarity |
| `provenance.md` | objections without retraction; speculation | substrate, logic; meets polarity only at diagnosing `{t,f}` |
| `aggregation.md` | orders as reads; summaries, and what glues | substrate, logic, polarity, questions |
| `domain.md` | one worked instance — runs, metrics, steps — and the cost against today's runstate | all of the above |
| `prior-art.md` | whose this already is | — |
| `open.md` | what is open, across layers | — |

**The rule: a layer cites only its ancestors.** A reference from a layer to a layer above it is a defect
— a lower layer leaning on an upper one — and is findable by grep.

**Companions.** `decisions/` records, per layer, what was tried and withdrawn, so these files can state
conclusions. `../../if-built-today-citations.md` is the verification ledger — it marks each citation
**CONFIRMED** (read in primary source), **UNVERIFIED**, or **UNOBTAINED**, and anything here that is not
CONFIRMED there should be read as unchecked. `../../dead_ends/topological-framings.md` records three
refuted framings so there is not a fourth.

## Two commitments, and everything follows from them

**Nothing is ever retracted.** Every record is permanent — not because a log is a convenient
implementation, but because *retraction is what costs coordination*, and there is a theorem saying so
(`logic.md` §"CALM"). Monotonicity is the load-bearing property; append-only is one way to get it and not the only
one.

**And a second theorem, about a different loss.** CALM prices retraction in coordination: you may have it
if you pay. The *inverse curse theorem* prices it in something unbuyable — if every state can be undone,
**no query ever knows it is finished**. (Power, Koutris & Hellerstein, ICDT 2025, Thm. 18: if every state
is invertible and `Q` is not constant, `Q` has no free-termination state.) The proof is three lines. From
an undoable state you can reach the state that undoes everything, and from there anything at all — so
nothing observed now constrains what is observed later, and every answer stays provisional forever. Their
own summary: the value of invertibility (DBSP, DBToaster) and the value of coordination-free monotonicity
(CALM, CRDTs) *"appear mutually exclusive."*

**Which makes this commitment a precondition for the rest of the design, not only its distribution story.**
Settledness, the threshold rule, the residual, a memo check that ever stops — every one is a claim that
*nothing more is coming*. Under retraction none of them can exist. The two theorems price the same refusal
and buy different halves: CALM buys soundness, never emitting a wrong answer; this buys completeness,
knowing there is no further answer to wait for.

**Identity is data, never position.** *"Which thing does this record belong to?"* is answered by an
argument inside the record, never by where it sits in an order. `heartbeat(episode2, 500)`, not *"the
heartbeat after the second `started`"* — so there is nothing to infer, because a query about episode 2
cannot match episode 1's records; the arguments do not unify.

**The second commitment is forced by the first.** Under permanence a mis-aimed record can never be
corrected, only supplemented by a correction that itself has to be trusted. So whatever a reader needs to
know about a record has to be **in the record, at write time**. Position is exactly the thing that is not.

**Calibration, since the second commitment sounds cheap.** It is the one place this design's problem class
has been measured, in an existing system with the opposite convention: **11 of 37 stops were discharged by
a record the worker did not write, 6 of them malformed** — a late heartbeat from a dead episode read as
current, a displaced worker's terminal read as the run's verdict, a halt swallowed because discharge was
author- and body-blind. Position-derived identity does not fail rarely.

Two things that measurement does **not** do. It does not support the rest of this document — identity-as-
data would work in a plain mutable database, and nothing here is measured against a system that exists.
And it does not touch **forgery**: a forger posting `stopped(episode2, completed)` makes a well-typed post
the store accepts, exactly as *"cooperative, no enforcement"* intends. Attribution defects die under these
commitments; forgery defects do not, and no representation changes that.

**And it forecloses a failure no census could have found.** *Contextuality* is the situation
where several agents each hold a coherent view, every pair agrees wherever they overlap, and no global
picture produces them all — formally identical to Bell's theorem, under a dictionary that is forced rather
than analogical (compatible family ↔ no-signalling, global section ↔ hidden-variable model). It is
undetectable by any agent, since every local and every pairwise check passes. Morton (2017) shows it arises
from thoroughly ordinary causes: from missing data alone, and from stale reads under snapshot isolation
alone. **It cannot arise at this layer.** Gluing indexed rows is a join on identity, so the glue is
determined rather than guessed, and his Prop. 6.4 constructs it for any compatible family; the obstruction
lives one level up, where restriction *"necessarily involves summing over indices"* (Def. 6.8) and nothing
records which row contributed what. Of his own versioning example: *"conflicts are resolved by version
numbers. Forgetting the version numbers, we get disagreement on indexed overlaps."* Contextuality requires
forgetting the discriminator; this commitment is the refusal to. **The foreclosure reaches exactly as far as
identity stays attached** — a consumer that aggregates performs the forgetting map itself, which is §Open.

## What makes the answer worth having

Three properties, stated as properties rather than as taste. Each is checkable, and each survives
translation into another language — which matters, since the point of a protocol is that somebody
reimplements it.

**Unsound things are unwritable, not forbidden.** Negation is not banned by discipline; it is absent.
*"This atom is false and nothing says otherwise"* is not against the rules — it has no syntax. The
alternative to unwritability is code review.

**The restrictions coincide.** Finite `∧` with arbitrary `∨` and affirmable-in-finite-time are one fact in
two vocabularies — the second is the standard justification for the first, so they are not independent
confirmations — and **monotone-iff-coordination-free is a genuinely separate route to the same
constraint set**, arrived at from distribution rather than from observability. Two routes agreeing is
weaker than three would be and still worth having: it is the difference between a constraint set that was
chosen and one that was met twice.

**A checker can enforce it.** The discipline is a property of the language, so a tool can hold it. That
is the static-checkability principle applied to a language rather than to a field access.


## What gets built on top

The base is **definite clauses over an unspecified universe**, and nothing else. Everything else this
document treats as part of the design is a construction above it. Drawing the dependencies is worth more
than listing them, because it shows what a reader who rejects one construction still keeps.

There is also a second, independent reason the line sits where it does, found after it was drawn: the
constructions below it are exactly the ones whose **meaning varies with the universe the user inhabits** —
polarity, equality, the four-value reading and settledness all mean something else (or nothing) in a
regime where an arbiter is affordable and a refuted branch can be pruned. The audit and the criterion are
in `../substrate-parametrically.md`; by §"What makes the answer worth having"'s own standard, a boundary met
twice rather than chosen.

```
definite clauses and asked questions, parametric over the universe
├── polarization — the relation told(A, P), declared in the signature
│   ├── told falsity (¬Q)
│   ├── settledness → the threshold rule → its five instances
│   └── conflict {t,f} → the Belnap reading
├── annotation — provenance
│   ├── dispute → readjudication
│   └── trust policy
└── aggregation — summaries over a demand region
```

**The two upper branches are independent, and that is why the graph is worth drawing.** Provenance
annotates derivations and combines them; no step of it mentions a polarity, and neither does dispute nor
trust. So the answer to *"nothing can ever be taken back"* — below — survives a reader who rejects
polarization outright, which is this design's most contestable choice. Two edges cross: diagnosing a
`{t,f}` consumes provenance, and objecting to a polarity assignment needs both.

**Polarization is a two-element marker set, not a commitment to two-valuedness.** Polarity is declared
schema and the substrate never sees it (`polarity.md` §"Polarity is schema, not substrate"), so a richer marker set is
*more declared values of `Pol`* and the base does not move. A third marker — `undecidable`, say — is told
rather than inferred, exactly as `polarity.md` §"Falsity is told" requires. Belnap is therefore not
*the* logic here but **the reading of the two-marker instance**; over a larger set the reader supplies
whatever lattice they like, and nothing underneath changes.

## What it does NOT solve

- **Cross-host liveness.** You still need a handle and a probe, and it still abstains off-host — and that
  abstention must not become a stored verdict.
- **The artifact plane.** Checkpoints on a filesystem remain unmodelled, and remain where a double-live
  worker's real damage lands.
- **Enforcement.** Still honour-system. This is why forgery defects survive.
- **The halt does not dissolve.** Self-withdrawal is free — scope a posted demand to its subscription and
  disconnecting ends it. But the measured case is an **operator** stopping a run a **scheduler** relaunches,
  i.e. withdrawing *someone else's* demand. That needs a write and an authority rule, and always did.
- **Diagnosing a conflict.** `{t,f}` is affirmable; telling a genuine disagreement from an over-claimed
  region is not, without provenance nobody has built.

## Related

- `decisions/` — what was tried and withdrawn, and why, one file per layer.
- `../../if-built-today-citations.md` — the verification ledger; every citation marked CONFIRMED there
  has been read in primary source.
- `../../dead_ends/topological-framings.md` — sheaves, formal topology, d-frames: three framings, one cause.
- `../definite-clause-maximality.md` — whether this fragment is forced rather than chosen. A third route
  (preservation under homomorphisms) that would answer §"What makes the answer worth having"'s own
  concession that *"two routes agreeing is weaker than three would be"*. Open; two papers unread.
- `../demand-driven-reads.md` — the consumer-facing target. **Stale**: it still describes a
  LEFT-JOIN-over-a-grid, `?` as a value, `read`/`force` as verbs, and LISTEN/NOTIFY. Its §5a taxonomy
  survives.
- `../prolog-query-layer.md` §3 — the measured answer-subsumption results, reinterpreted: the defect is an
  exact claim on an unsettled term, and it does not arise here because nothing aggregates at write time.
- `../memoizer-index-algebra.md` — the emission filter, and why exposing it is not additive.
- `../../specs/write-authority.md` — unchanged by any of this. Consensus number does not separate a unique
  constraint from `send(expected_seq=)`: a keyed row that stays readable is a write-once register, not a
  test-and-set — every loser can read who won — and the shipped compare-and-swap *is* a unique constraint,
  `PRIMARY KEY (run_id, seq)`. What differs is the **key**: a dense position that every append contends on,
  against a semantic key contended only by inserts of the same key.
- `../../layers.md`, `../../positioning.md` — where this sits.
