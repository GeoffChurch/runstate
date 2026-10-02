# Open

**Layer:** across all layers; numbering preserved from the single-file draft. The dependency graph is in `README.md`.

1. **Whether an unbounded demand is ever covered.** Boundedness of the extent is the solver's at post time
   (`3-questions.md` §"Quantifiers live in questions"); what no post-time check reaches is whether an
   *external* producer will ever converge and post its `¬Q`. A producer halted short of convergence
   knows nothing about the rest and must post nothing about it, so the tail stays `∅` — this is the
   exhaustion question (`2-polarity.md` §"How a party comes to know a negative fact"), and it needs a shape that is not
   `¬Q`. What is settled is settled by witnesses: for a question that binds the value, the halted
   producer's prefix is decided, and only the tail is open.
2. **Provenance.** Not built. What it blocks meanwhile: diagnosing `{t,f}`, taint after a premise becomes
   disputed, and attributing a wrong `¬Q` — positive facts need it equally, so it is one mechanism. What it
   would unlock is the annotation branch of `README.md` §"What gets built on top", including the whole dispute story,
   so this is the single highest-leverage unbuilt thing here.
3. **Which constraint domain — *and which algorithm for it*.** Not separable: a generic bounds propagator
   on difference constraints ping-pongs where negative-cycle detection decides the same system in `O(V·E)`.
   For a step axis the answer is difference constraints with cycle detection. If strided demands are ever
   wanted, add congruences with CRT, and note the corpus has not been surveyed for what else it needs.
4. **Where the query language stops.** What constrains it is **pushdown**: the more expressive, the less
   runs where the data lives.

   **The answer-shape, if pushdown ever bites: ship programs as data.** A program is a content-addressed
   term, `program(h, Source)`, and a request to run it near the data is an ordinary question,
   `asked(run(h, Args))`. A generic producer that serves `run` executes it and posts its results as
   ordinary facts. Nothing below `5-domain.md` changes: the store accumulates the program, the request and
   the answers, monotonically, and identical programs with identical arguments are one question, so
   memoisation is free.

   **What it does not replace.** Arbitrary programs are opaque. The engine cannot certify a program's
   completeness, compute its residual, or detect that one program's answer covers another's —
   equivalence is undecidable, and only identical hashes coincide. Those are what the fixed question
   language of `3-questions.md` buys: a second asker of a covered question triggers nothing, and anyone
   can check settledness by a finite cover. Shipped programs are the escape hatch *beside* the questions,
   not a replacement for them; a program's completeness is its own testimony, posted as a negative tail
   like any producer's.

   **Two hazards.** A shipped aggregation computes a tier-3 summary (`4-aggregation.md`) over whatever its
   host holds at that moment, so it posts a **dated** report — *"the mean over what I held as of
   cursor c"* — which is a permanent fact about the past. Posted as undated fact it would be read as *the*
   mean, which is the contextuality hazard. Tier-1 and tier-2 summaries, may-lifts included, can be
   posted live and maintained incrementally. And running someone else's code is a far larger trust surface
   than reading their facts — termination, resources, side effects — and the logic has nothing to say
   about it. Prior art, recalled and unchecked: Webdamlog (Abiteboul et al.) delegates rules to remote
   peers; query versus data shipping in distributed databases; Unison's content-addressed code.
5. **No central store.** Each agent holds a lagged local copy and replicates preferentially what it
   demands; the "global" store is the union of the local ones. This follows from monotonicity and needs no
   coordination. The consequence: the memo check becomes **local**, so two agents demanding the same region
   without having replicated each other's answer both run the six-hour job. That is **single-spawn**, the
   one irreducibly coordinating requirement — and note it is *not* CALM that prices it: *"run iff no other
   agent is running this"* is a mutual-exclusion requirement, not a query, so the theorem does not speak
   to it. What CALM does say is that any *query* whose answer needs the participant roster is
   non-monotone; single-spawn needs the roster for a different reason, and needs it just as badly.
6. **Whether demand should be the only interconnect**, and not merely the only one that *means* anything.
   The semantics already reads the second way: an answer is truth restricted to the demand, so anything
   arriving unbidden is a cache warm-up with no semantic status, and an implementation may broadcast
   freely without the model noticing. Making it **architectural** — a local view receives *nothing* it did
   not ask for — would bound coordination by the number of live channels, and would forbid unsolicited
   broadcast, which is a real affordance. So the two readings should probably stay apart.
7. **What a conflict means, given that three different things produce one.** **Domain**: re-production
   jitter under a single-spawn violation — a *correct* report of a disagreement the system itself caused.
   **Valuation**: two posters genuinely disagreeing, or one over-claiming its own `¬Q`. Measured
   counterweight for the first: **0 of 3,165** numeric re-productions diverged on the real corpus — which
   measures that the consumers' hand-rolled guards are **working**, not that the hazard is absent; a
   census sees only harms detectable in a log, and is biased low in proportion to the machinery already
   preventing them.

   **The valuation case is now measured (2026-08-22), and the number is not the finding.** Across 2,300
   logs from the two consumer repos: **2,743** `lifecycle.stopped` records and **0** values at a step
   beyond the `final_step` their stop declared; **1,140** `launcher.terminated` records and **0**
   subsequent values or heartbeats. But the zero is structural rather than fortunate — **the deployed
   protocol has no `¬Q`**. The only closure claim is a worker's own `stopped`, so the two independent
   claimants that `{t,f}` represents cannot both exist; and the one *external* claim,
   `launcher.terminated`, is posted after reaping, when the worker is already gone. The census therefore
   shows the mechanism **inapplicable to this corpus** — a legitimate census conclusion — and says nothing
   about a design that adds `¬Q`, since `¬Q`'s absence is the whole explanation.

   Two incidental findings. **411 of 2,743 stops (15%) carry no `final_step`**, so they close nothing and
   delimit no region — which bears on whether settledness would be derivable in practice. And **1,643
   values do arrive after their stop**, every one of them at a step *within* the closed region: teardown
   telemetry (`status`, `phase: "saving"`). Under this design those are ordinary positive posts inside a
   settled region, not conflicts — but the first pass of this census counted them as violations, so the
   loose predicate *"any value after a stopped"* is recorded here as the wrong one.
8. **What `every` is** — a `∀` over a strided region, or a firing schedule. It splits. A stride anchored
   at `from` is a range for a question's free variable, and needs congruences in the constraint domain
   (Open 3). The shipped delta and time readings of `every` have no region reading — see
   `../memoizer-index-algebra.md`, where the delta reading is recorded as **non-monotone on a read path** — so
   they stay firing schedules, which is control. Of the shipped `until`, the step axis is a range, the time
   axis is a range only if the atom carries its time, and `count` is a lease rather than a question.
9. **Whether may-summaries are usable.** `4-aggregation.md` §"Summaries" sorts summaries into three tiers
   and gives the may-lift, which moves any summary into tier 2. Open, and measurable: whether may-summaries
   — or their interval coarsening — are narrow enough in practice to be useful, and whether strongly
   settled regions are large enough, often enough, to make them exhaustive. The corpus has not been asked.
10. **An admission rule for questions whose residual cannot empty** (`3-questions.md` §"The residual, assembled"): a free
    variable over an unbounded value sort, or a strong question over producers that do not vouch. Without
    one, a scheduler that relaunches on a non-empty residual relaunches forever.

