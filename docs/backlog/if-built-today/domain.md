# The domain: runs, metrics and steps

**Layer:** one worked instance, depending on every layer. The dependency graph is in `README.md`.

## What the problem domain forces, whatever the design

These are not inherited from an incumbent; they are constraints the setting imposes, and any design for it
will contain something playing each role. They are listed because it is easy to mistake them for choices.

- **A run is a durable identity that outlives its processes.** The unit somebody asks about is not a
  process — it survives the death and relaunch of every process that ever served it.
- **Content-addressed identity.** If a run is named by what it computes, the name is also the cache key,
  and two agents asking for the same thing ask under the same name without arranging to.
- **The verdict as a join of two partial observers** — and it is a *report*, which is why it may use the
  narrowing reading that derivation may not.
- **Cooperative, no enforcement.**
- **`never` as a fact rather than a status** — it survives as `¬Q` over a singleton region. What nobody
  posts is the **status**, which is read, not written.
- **Status cycles; values do not.** `running → OOM → running` cannot live in a monotone order, so the
  attempt index goes in the term and the cycle lives in the *sequence of attempts*.
- **Structure goes in the key, not the value.**

## One demand, traced

Everything up to here has been argued rather than shown. Here is a single demand from posting to
settlement, so the rest has something to be about. Nothing in it is new — every step is a mechanism one of
the surrounding sections defends.

**A querier posts one record.** It wants the loss at every step of run `r`, for as long as the run lasts:

```
asked(∃V. metric(r, loss, V, S)) :- S ≥ 1.
```

`S` is free, so the question ranges over every step — integers, since the signature sorts `S` as a step;
`V` is bound, so each step wants *a* loss. The extent is **unbounded**. That is fine; what must be finite is the eventual cover, not the region.

**A producer is handed the residual, not the question.** Steps 1–60 already have a loss in the store, so
what reaches it is `S ≥ 61` — the question minus what is decided. Nobody detects that the first sixty were
subsumed; they simply contribute nothing.

**It produces, and the querier reads a threshold.** `metric(r, loss, 0.31, 61)` is posted. The querier's
read is `⊒{t}` on that atom, affirmed the only way anything is affirmed here: by exhibiting the record.

**At step 900 the querier sees `∅`, and may conclude nothing from it.** Nothing has been told about that
step. That is not *"there is no loss at step 900"* — it is *"nobody has said."* The querier may confirm it
has **left** `∅` the instant any record arrives, and may never confirm it is **in** it.

**The run converges at 743, and the producer says so.** It posts one negative region,
`told(metric(r, loss, V, S), neg) :- float(V), S > 743`. **That single record decides infinitely many
atoms** — and it is *told*, not inferred. Nobody derived it from the absence of anything: the producer
knew, and said.

**Now the question is settled.** Every step `S ≥ 1` is decided — a witness for each of the 743 produced
steps, and a refutation of every loss at every step beyond. The cover is finite (743 positives and one negative) though the
region is not, which is exactly what makes an unbounded demand dischargeable. The querier's *"tell me when
there are no more"* fires here, and not before — and it means no more **steps**. Because the question bound
`V`, it never asked whether a step has *other* losses; a later second value at step 61 is another answer,
not a contradiction.

**A second querier posts the identical question, and nothing runs.** Identical questions are one record,
and its residual is empty, so there is no work to hand anyone and no producer to launch. The store was the
cache; no cache was built. A querier asking the *stronger* question — `metric(r, loss, V, S)` with `V`
free, every step's set of losses complete — would find it unsettled, and it stays so unless the producer
vouches, at each step, that its loss is the only one (`polarity.md` §"The rule for posting").

**And if two producers disagree.** Two of them posting different losses at step 61 produce two *atoms*,
both `⊒{t}` — a **domain** conflict, invisible unless somebody wrote a rule saying loss is functional in
the step. A **valuation** conflict is a different thing: it takes a negative covering `metric(r, loss, 0.31,
61)` — a per-key complement from the other producer, say — and then that one atom reads `{t,f}`:
affirmable, inert, and poisoning nothing around it. Which of the two a disagreement becomes is decided by
the key's granularity and whether producers vouch — a schema's choice.

## The honest cost

A **rewrite, not a refactor**, with consumers on the current API. It trades a design whose failure modes
are intimately known for one whose failure modes would have to be learned. And storage grows wherever the
order is partial, since there the only sound completion is the free one.

**And the artifact is larger in one direction and smaller in the other.** Today's runstate is an *ordered*
topic log with typed conventions on top — a message protocol, and a thin one on purpose.

**The transport this design needs is weaker than that.** The store is a growing **set** of ground terms and
merge is union, so unordered, duplicate-tolerant, loss-tolerant broadcast suffices: a reader holding one
agent's posts and not another's simply has a smaller store, which is sound. Not even causal order is
required — missing a cause costs completeness, never correctness. Order is needed in exactly one place, the
**claim**, where `send(expected_seq=)` is a compare-and-swap; and that is single-spawn, which §CALM already
concedes as the one coordinated act. Nothing in the semantics reads a sequence number.

**What is larger is the read side.** A per-functor term index that pattern-walks rather than key-looks-up,
a constraint solver in the read path, and a **coverage checker** that decides settledness and the residual
by finding a finite cover — incomplete checkers are safe, costing only spurious relaunches. (An earlier
draft also owed cross-host naming for the holes in posted terms; variables scoped to records removed it.)
That is the honest headline cost, and it dwarfs the fold rewrites below. It is also the thing a reader should weigh
first, because everything else here is downstream of being willing to build it.

**No fold ports as-is.** Measured across the whole of `observables.py`: 13 of 16 fold readings are
non-monotone, and the operator responsible is `latest` = `argmax(seq)`, which appears six times directly
plus three `[-1]`/`reversed` and three `max(…)` in 551 lines. Each becomes **dual plus subtraction** — the
monotone half derived inside, one complementation performed outside:

```
inside   discharged(C) :- stop(C), stopped(S), S > C.
outside  unhandled = stops − discharged
```

**The inside half is confirmed.** 13 of 13 duals measured monotone, and **9 of 9 folds reconstruct
exactly** across every scenario tested; the worked example reproduces the shipped `undischarged_stops`
verbatim, one subtraction.

**The outside half is "a subtraction" for 5 of those 9**, and the residuals differ in kind:

| residual | folds | what it is |
|---|---|---|
| subtraction only | `latest_episode`, `_episode_stopped`, `undischarged_stops`, `value_series`, `live_episode` | the clean case |
| + an **aggregate** over a non-`seq` order | `progress`, `last_activity` | a `max` over the survivors — and `last_activity`'s is over `t`, non-monotone against `seq` |
| + an **emptiness test** on an already-complemented set | `_launcher_terminal`, `peek_terminal`, the four-state projection | `¬∃ started`, which no finite observation can affirm — measured to **retract a published verdict** when a later claim arrives |

**And picking the dual is where the danger is.** The obvious dual of `progress` is the high-water mark
`reached(K) :- heartbeat(_,S), K ≤ S` — and it is **wrong**, for the reason `progress`'s own docstring
gives: *"a monotone watermark here would re-open the splice it just closed."* After an episode rewind it
reports the old frontier; `ensure`'s window test then passes and returns a **spliced series as complete**.
`last_activity` has the same trap one axis over.

So this is eight rewrites, none mechanical, and at least one where the *natural* dual silently
reintroduces a bug the fold exists to prevent.

