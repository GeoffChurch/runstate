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

## Offered metrics

A worker may **offer** an expensive metric that is computed only when demanded. This generalizes today's
`set()` register.

- **The log then depends on its observers,** and that is sound: their demands are on the log, and each tick's
  `answered` names the demand each value answers. The log records the joint computation of worker and
  observers, not everything the worker *could* have produced.
- **Demanded computations must not perturb the worker's state.** Evaluation that advances the RNG, updates
  BatchNorm statistics or warms a cache would make the mandatory trajectory depend on who was watching.
  The protocol cannot enforce this; the recipe must require it.
- **Undemanded history is gone.** A past prefix nobody demanded comes back only by re-running from a
  checkpoint. Under nondeterminism, that re-run is a different lineage.

## Derived providers

An expensive or separable metric is best computed by a **derived provider**: an evaluator that demands the
trainer's checkpointed nodes, computes on its own log, and names the trainer node each value describes. This
is [`../specs/derived-runs.md`](../specs/derived-runs.md) with lineage names. It removes both costs of
offered metrics:

- **No perturbation.** The trainer never runs the evaluation.
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
