# Demand on lazy streams: one primitive for ensure, subscriptions, leases and stopping

**Status:** DIRECTION, untested (opened 2026-10-04 with the owner). It is the write side of
[demand-driven-reads](demand-driven-reads.md), which frames the same relation from the read side as
incremental tabling. It builds on layer 2's stream coordinate
([identity-in-records](identity-in-records.md) §2, decision 5) and on
[lineage-graph](lineage-graph.md). Everything here is a *framework prediction* unless marked otherwise.
Nothing is adopted.

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
| `stop` | withdrawing demand; an explicit abort stays as "withdraw all" |

**Precedent:** demand-driven build systems (Shake, Bazel), demand-driven incremental computation (Adapton),
pull-based functional reactive programming, and tabling with answer subsumption.

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

**This explains a symptom.** Layer 2 needed the interim rule "samples are not stream elements" because two
primitives write values of one name:
- `emit`, a stream at the worker's cadence;
- `set` plus a subscription, a sampled cell at the observer's cadence.

Under this foundation there is one primitive. Each name is a stream, either:
- **eager:** emitted every iteration;
- **offered:** computed only at the iterations someone demands.

A "sample" is a demanded element of an offered stream. As a result:
- it carries no `request_id`;
- it cannot double-count, because each name has one producer;
- it is memoized across demanders.

A demand whose progress condition refers to an offered stream it is itself demanding is circular, and is
rejected as ill-formed.

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

## Offered metrics

A worker may **offer** an expensive metric that is computed only when demanded. This generalizes today's
`set()` register.

- **The log then depends on its observers,** and that is sound: their demands are on the log, and each value
  that answers a demand names it, by its `request_id`.
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

- **Why it fails: atomicity.** A tick commit, a claim, a death and a stop each happen once per training
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
3. **Withdrawal is retraction,** which is not monotone. Leases handle it today; if-built-today's polarity
   design is the long-run answer.
4. **Reuse under nondeterminism needs the memo kept per lineage.**
5. **Scope.** This is an architecture. "The app comes first" (CLAUDE.md) warns against designing it for
   imagined consumers.

## The first probe (cheap, read-side, no protocol change)

Build `run.stream(name)[:n]` as a helper over the log plus `ensure`'s producer seam.

- **Prediction:** it expresses mycooc's and translation's current `ensure` and `history` uses with no loss.
- **Refutation:** a current use it cannot express, or one it expresses only with a predicate over values.
