# Demand on lazy streams: one primitive for ensure, subscriptions, leases and stopping

**Status:** DIRECTION, untested (opened 2026-10-04 with the owner). It is the write side of
[demand-driven-reads](demand-driven-reads.md), which frames the same relation from the read side as
incremental tabling. It builds on layer 2's stream coordinate
([identity-in-records](identity-in-records.md) §2, decision 5) and on
[lineage-graph](lineage-graph.md). Everything here is a *framework prediction* unless marked otherwise.
Nothing is adopted. Corrected 2026-10-14 where it misstated if-built-today and layer 2
([review](../review-2026-10-04-commits-and-lineage.md) §9 C1); it now cites if-built-today's sections
rather than paraphrasing them.

## The model

- **A run is a set of lazy, memoized streams,** one per value name, read relative to a head. A stream's
  coordinate is the element's index along the lineage (layer 2, option S).
- **A consumer demands a prefix.** It asks for a prefix of a stream, or of a **derived** stream, which is an
  expression shipped to the worker ([programmable-subscriptions](programmable-subscriptions.md)).
- **Satisfied from the log when possible.** If the prefix is already materialized on the log, the demand is
  met from there. The log is the memo table, and every consumer shares it.
- **Otherwise demand forces the producer.** It is launched, or resumed from a checkpoint, and runs until the
  demand is met, while consumption blocks.
- **A worker runs while demand is outstanding, and retires when none is left.**

```python
loss = run.stream("loss")
loss[:1000]                                        # blocks; forces the worker only as far as needed
run.stream("loss").every(10).take_while(gt(0.1))   # shipped as data; evaluated worker-side
```

## What it unifies

| Today | As demand |
|---|---|
| `ensure(until=…)` (read first, produce on miss) | demand a prefix |
| `history` | demand an already-materialized prefix |
| subscriptions and the from/every/until algebra | ongoing demand of a derived stream |
| lazy launch and leases | outstanding demand keeps the producer alive |
| `steps(total)` versus `serve()` | a finite demand versus an open-ended one: one loop, not two drivers |
| early stopping ("until loss < x") | a demand that ends when its predicate holds, evaluated at the worker |
| `stop` | not demand: a stop is a told fact naming what it halts (below) |

**Precedent:** demand-driven build systems (Shake, Bazel), demand-driven incremental computation (Adapton),
pull-based functional reactive programming, and tabling with answer subsumption.

**A stop is not withdrawn demand.** An operator's stop is a told fact, which needs an authority rule when
the demand it halts is someone else's (if-built-today [README](if-built-today/README.md) §"What it does NOT
solve"); halting a claim and forbidding further computation are separate facts there
([5-domain](if-built-today/5-domain.md) §"Halting and withdrawing"). What withdraws demand is its asker's own
record, `control.withdraw` in layer 2 ([review](../review-2026-10-04-commits-and-lineage.md) §12, item 10b).

## The semantic foundation: lattices with threshold reads

*Framework claim, untested in runstate. The cited results are established.*

**The idea.** Every shared value only ever *grows* in a lattice, and every read is a **threshold read**:
"block until at least this much is known". Such reads are deterministic under any scheduling and any network
delay.
- **Sources:** LVars (Kuper and Newton) are lattice variables with threshold reads. Bloom^L (Conway et al.)
  and CALM (Hellerstein and Alvaro) are the same idea in distributed logic programming.
- **Why it matters here.** Being deterministic under any delay is exactly the property the slow, inconsistent
  regime needs.

**How it maps onto runstate:**
- **A stream's prefix is a lattice element.** It is ordered by "is a prefix of", and its least upper bound
  (join) is the longer of two compatible prefixes. Layer 2's commits and lineage
  ([`../specs/commits-and-lineage.md`](../specs/commits-and-lineage.md)) are what make that ordering
  well defined.
- **Reads become threshold reads:**
  - `ensure(loss, 1000)` is a threshold read on the prefix length;
  - a demand is a registered threshold;
  - a stop is a threshold on a derived lattice value.
- **A register is modelled as its history.** A register's "current value" read is *non-monotone*: its answer
  changes as the register is overwritten. The foundation says to model the register as its history, a
  stream, and read only thresholds of it.

**This explained a symptom, since resolved.** Layer 2's first draft needed the interim rule "samples are not
stream elements", because two primitives wrote values of one name: `emit`, a stream at the worker's cadence,
and `set` plus a subscription, a sampled cell at the observer's cadence. The resolution adopted is a duality,
not one kind of stream ([`../specs/commits-and-lineage.md`](../specs/commits-and-lineage.md) §2; review
§12, items 6–8):
- **A source** is a name the worker declares on its claim and exposes to be sampled on demand. Its samples
  are keyed by their node and never counted, because how many exist depends on who asked.
- **An eager stream** is every other name, with an element whenever the worker emits it.
- **You ask for sources, and measure by eager streams.** A sample carries no `request_id`; the commit that
  holds it names the demands it answered. Circular demand can no longer be expressed.

**The most general form is relational.** This is the [if-built-today](if-built-today/README.md) and
[demand-driven-reads](demand-driven-reads.md) direction.
- A run's streams are relations in a monotone fact store.
- Demands are queries, which may span runs.
- Tuples not yet computed are produced by launching workers.

Sweeps, hyperparameter search and bandits are then demand over a mostly unmaterialized relation, and
single-run streams are the everyday special case.

**The order this suggests:**
1. a monotone foundation of lattices with threshold reads;
2. relational demand over it;
3. streams as the common case.

## Status of the pieces (2026-10-04)

- **The data model is adopted into layer 2:** sources and eager streams; samples keyed by node, with the
  commit naming the demands it answered; and settledness, giving a threshold read with `Complete`, `Pending`
  or `SettledShort`. See [`../specs/commits-and-lineage.md`](../specs/commits-and-lineage.md) §2 and §5.
- **The control plane is the next layer: demand as `asked` facts.**
  [if-built-today](if-built-today/3-questions.md) already designs it, in §"A question is a tell of
  `asked`", §"Quantifiers live in questions, and nowhere else", §"Demand subsumption", §"The residual,
  assembled" and §"Demand is control". It would replace subscriptions, and bring `ensure` and lazy launch
  under one primitive.
- **What it leaves unsolved:** an operator withdrawing *someone else's* demand still needs a write and an
  authority rule (if-built-today [README](if-built-today/README.md) §"What it does NOT solve";
  [authenticated-records](authenticated-records.md)).
- **On the wire, a trigger is an explicit record.** Binding a variable shared across processes is itself
  a message. Open lists with attributed variables, as in Mozart/Oz's distributed by-need dataflow
  variables or the streams of concurrent logic programming, are the client binding.

## Offered metrics

A worker may **offer** an expensive metric that is computed only when demanded. Layer 2 ships the simplest
case: a declared source sampled as it stands, which is the identity delegate (review §12, "D5 reframed"). A
metric derived on demand from a source is a derived stream, named by the demand that creates it
([programmable-subscriptions](programmable-subscriptions.md)).

- **The log then depends on its observers,** and that is sound: their demands are on the log, and the commit
  that holds a sample names the demands it answered.
- **The contract is a durable record of an interaction, not a cache of a pure function** (owner,
  2026-10-04). The log records what the worker produced, given what was demanded of it. Reuse means "you
  get what happened", which is what `ensure` already returns.
- **The protocol claims no purity it cannot enforce.** GPU nondeterminism already made "same config, same
  trajectory" an idealization.
- **Reproducibility becomes advice, not a rule.** A user who wants a trajectory that does not depend on who
  watched keeps demanded computations free of side effects: no RNG advance, no BatchNorm update, no cache
  warming.
- **Content-addressed run ids** (`../specs/run-id-recipe.md`) must state their assumption: observers'
  demands count as inputs wherever they perturb.
- **Undemanded history is gone.** A past prefix nobody demanded comes back only by re-running from a
  checkpoint. Under nondeterminism, that re-run is a different lineage.

## Derived providers

An expensive or separable metric is best computed by a **derived provider**: an evaluator that demands the
trainer's checkpointed nodes, computes on its own log, and names the trainer node each value describes. This
is [`../specs/derived-runs.md`](../specs/derived-runs.md) with lineage names. It removes both costs of
offered metrics:

- **No perturbation.** The trainer never runs the evaluation, so its own log needs no advice to stay
  independent of its observers.
- **No lost history.** Any checkpointed node can be evaluated later, on the trainer's *same* lineage,
  because the evaluator reads that node's saved state rather than re-running training.

## One log per run, many streams

The rejected alternative was one log per metric: "producers" in one process, linked by a naming convention.

- **Why it fails: atomicity.** A commit, a claim, a death and a stop each happen once per training
  state. Separate logs share no sequencer, so no write updates two of them at once. Three things go wrong:
  - a crash between the writes leaves one metric committed and another not;
  - a takeover can win one log's claim and lose another's;
  - a stop must reach N logs.
- **Measured evidence that metrics share their computation:** mycooc's analysis is one ~191 MB load
  followed by ~8 cheap analyses, so the derived-runs key is the snapshot, not (snapshot, metric).
- **The interface is preserved.** A shipped program takes one stream expression. A program over several
  metrics joins on the shared lineage index inside one log.

## Hard parts

1. **Shipped combinators must be data,** in a restricted, total language. Python lambdas stay observer-side,
   over prefixes already materialized.
2. **A worker advances all its streams together.** Forcing one stream ticks the whole loop, and the memo lets
   other consumers benefit.
3. **Withdrawal is not retraction.** if-built-today withdraws demand with ordinary positive facts, a lapsed
   lease or a withdrawal record ([5-domain](if-built-today/5-domain.md) §"Still wanted: leases" and
   §"Halting and withdrawing"), and layer 2's `control.withdraw` has the same shape.
4. **Reuse under nondeterminism needs the memo kept per lineage.**
5. **Scope.** This is an architecture. "The app comes first" (CLAUDE.md) warns against designing it for
   imagined consumers.

## The first probe (cheap, read-side, no protocol change)

Build `run.stream(name)[:n]` as a helper over the log plus `ensure`'s producer seam.

- **Prediction:** it expresses mycooc's and translation's current `ensure` and `history` uses with no loss.
- **Refutation:** a current use it cannot express, or one it expresses only with a predicate over values.
