# Whose this already is

**Layer:** prior art, across all layers. The dependency graph is in `README.md`.

Placed early so that nothing later reads as an unearned discovery. Losing novelty is welcome: a design
that turns out to be a known-good combination is better founded than one that is new.

- **A store of ground literals of both polarities, with four statuses read off membership of `ℓ` and
  `¬ℓ` — that is 4QL's Definition 5** (Małuszyński & Szałas, JANCL 21(2), 2011), and chosen there for our
  reason: OWA over CWA, *"to start with a fully monotonic query language."* Negation in rule heads is
  their told-false. Two differences: 4QL **propagates** inconsistency through derivation where here it is
  inert, and 4QL's §6 adds a stratified nonmonotonic layer that re-derives CWA, which this design
  declines. **We take its monotone core and refuse its top layer, because coordination-freeness is worth
  more here than default reasoning.**
- **A logic of arbitrary joins, finite meets and nothing else, in the information order, with polarised
  atoms — that is a fragment of Jakl's** (thesis 2018 Ch. 6; Jakl, Jung & Pultr, MFPS 32, 2016), with
  soundness and completeness proved. Polarisation is a *theorem* there: absent consistency hypotheses the
  two polarities generate independent free frames. We add `∃` and regions, which are not in it.
- **A relation whose extent is infinite, held as a finite constraint formula — that is Kanellakis, Kuper
  & Revesz** (JCSS 51(1), 1995). *"A generalized k-tuple is a quantifier-free conjunction of
  constraints"*; a generalized relation is a finite set of them. Finitely-representable-as-a-finite-union
  is theirs. What they do not have is a *second, separately asserted* extent: one formula per relation,
  complement by operator.
- **An accumulating store of positive and negative facts with a per-atom presence test over the pair
  exists in a proof** — Ameloot, Ketsman, Neven & Zinn (TODS 40(4), 2016) Prop. 4.6, with deletion set to
  `∅` *"causing nodes to only accumulate facts"*, broadcasting absences that are *"accumulated at all
  nodes."*
- **A completeness claim scoped to a region, posted as data, and composable across sources — that is
  Darari, Nutt, Pirrò & Razniewski** (ISWC 2013). `Compl(P₁ | P₂)` is a pattern plus a condition — *"a data
  source contains all triples in a pattern `P₁` that satisfy a condition `P₂`"* — published as RDF via
  `hasComplStmt`/`hasPattern`/`hasCondition`, with an entailment operator over sets of them; and **Def. 16
  indexes each statement to a source**, so a federated query is complete *"if evaluated over the **union**
  of all sources in the federation."* Their motivating case is a real *"verified as complete"* mark on an
  IMDb page. Two differences. Theirs is **metadata about sources** — which is why the index is needed at
  all — where `¬Q` is a record in the same store, claiming about a region of the world rather than about
  one source's coverage of it. And their semantics is relative to an **ideal graph**, against which a
  statement is objectively true or false, where nothing here defines correctness against a world: a false
  `¬Q` is a false post, under the same non-enforcement as a false value. They also reach this repo's dating
  repair independently — §6 recommends *"temporal guards … 'complete for movies by Tarantino **in
  2010**'."*
- **Ask/tell over a monotone store is CCP** (Saraswat, Rinard & Panangaden, POPL '91), which made the
  storage decision first — *"a simple constraint system is just an information system with the
  consistency structure removed"* — and then went the other way, making `false` the **top**, explosive
  (*"the inconsistent store can answer any ask request"*) and identified with divergence.
- **A store holding both polarities with contradiction *retained* is the paraconsistent relational model**
  — Bagai & Sunderraman (IJCM 55(1–2), 1995) and Trân & Bagai (Information Systems 25(8), 2000): a pair
  `⟨R⁺, R⁻⟩`, *"we do not assume `R⁺` and `R⁻` to be mutually disjoint"*, and *"a particular tuple may be
  considered to be both in and out of a relation."* Their *"all known (or believed)
  negative information is stored explicitly"*, over extents that may be **infinite** — though represented
  by automata rather than constraints, and with no accumulation or multi-source union.
- **Free termination — *"can a node know its output is final without coordinating?"* — is Power, Koutris
  & Hellerstein** (ICDT 2025). That is settledness, and their opening complaint is this document's: CRDTs
  give coordination-free consistency but no local way to know everything has arrived, and *"what good is
  distributed state if you do not know when you can query it reliably?"* Their answer **derives**
  termination from the query's algebra. They have the told form too — §5.2's nullary `All()`, *"true if we
  know that all machines have sent all their local data"* — and note that *"updating `All` requires
  coordination between the nodes."*
- **The four values are Belnap's** (1977): the four subsets of `{t, f}`, read as told-true, told-false,
  told-neither, told-both.
- **Two polarities that no axiom connects are strong, or constructive, negation** in its paraconsistent
  form — N4 (Almukdad & Nelson, 1984), not Nelson's original N3, which has `~A → (A → B)`. Gelfond &
  Lifschitz's extended logic programs also treat `¬p` as another predicate, but their answer sets collapse
  on a complementary pair, so they are explosive where this design is not. The clauses by
  which a question's negative reading follows its connectives — the negative of `∃` is `∀` of the negative
  — are Nelson's.
- **Settledness of a question is query completeness** — Motro (TODS 1989), Levy (VLDB 1996), Razniewski &
  Nutt (VLDB 2011): whether a query's answer over the data at hand is its answer over all the data,
  established by completeness statements. A negative region is the completeness statement for an empty
  slice; Darari et al., above, is the federated form.
- **A question with a universal is a hereditary Harrop goal**, and its constraint form is HH(C) (Leach,
  Nieva & Rodríguez-Artalejo, TPLP 1(4), 2001). The difference is which `∀`. HH(C) keeps `∀` generic —
  one proof for a fresh variable. Settledness needs the `∀` decided instance by instance and discharged by
  a finite cover of the constraint domain, which is Maher's (1987) disjunction of answer constraints; HH(C)'s
  own Ex. 5.2 shows the generic rule failing exactly there.

**What is not in any of them**, stated narrowly. Falsity **asserted by an agent** as the primitive act:
4QL derives `¬p` into the negative extent by rule, and the paraconsistent model's own worked construction
populates it by **CWA** — the storage of a negative extent is theirs, an agent *positing* one is not.
Refusing the tombstone **chain**, so that *told-both* is representable at all.
Demand-driven production with the store as the cache — named as *open work* in 4QL.

Not the distribution property itself: **no party roster and no self-identity is Ameloot's**, and
is now stated three times over (Cor. 13; *Complete CALM* Remark 3, *"membership knowledge is the single
non-monotone input that renders all subsequent computation monotone"*; free termination §5.2). What is not
in any of them is a data model built so that **every readable predicate sits inside that class by
construction**, rather than a language in which one may or may not stay there.

