# Decisions and retractions — `../prior-art.md`

What was tried in this layer, what was withdrawn, and why. Conventions in `README.md`.

## Prior art that narrowed the claim

Full verification ledger: `../../../if-built-today-citations.md`. In short, and welcomed rather than resisted:
the **store** is 4QL's Definition 5 and appears in a TODS proof (`R_notMsg`/`R_notMem`/`R_known`); the
**logic** is a fragment of Jakl's, with soundness and completeness proved; the **regions** are Kanellakis,
Kuper & Revesz's; **CCP** is the ask/tell model and dropped the consistency structure first, though it
then made `false` an explosive top; **Belnap** supplies the vocabulary verbatim.

What is not theirs: falsity **asserted** as the primitive act, refusing the tombstone **chain** so that
*told-both* survives, and demand-driven production with the store as cache. **Not** the distribution — no
party roster and no self-identity is Ameloot's, and is now published three times over (see the ledger).

### Scoped completeness — claimed and retracted the same day (2026-08-18)

Written into §"Whose this already is" on reading *Complete CALM* and the free-termination paper. Both use a
**global** completeness predicate — `All()`, *"true if we know that all machines have sent all their local
data"* — which they note costs coordination, where `¬Q` is regional and does not. Conclusion drawn:
*"scope is the entire difference."*

Refuted two hours later by Darari, Nutt, Pirrò & Razniewski (ISWC 2013), which has all of it: scoped
(`Compl(P₁ | P₂)`, *"complete for all movies directed by Tarantino"*), posted as machine-readable RDF,
composable by an entailment operator, multi-source with union semantics (Def. 16), and implemented.

**The shape of the error is this document's own subject.** *"Nobody scopes completeness"* is a `¬∃` claim,
and it was posted on **two witnesses rather than a cover** — precisely what §"Falsity is told" says nobody
may do. It was then refuted in the way the theory says such a claim is refutable: **negatively, by a
witness appearing** (§"An atom's status": an antitone question free-terminates only where a witness refutes
it). Review 7 had named Darari for this exact claim, and the novelty statement was written before the paper
it pointed at had been read.

Nothing about the framework changed — only a claim about its novelty.

