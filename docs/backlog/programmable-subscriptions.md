# Programmable subscriptions: shipping expressive programs to workers

**Status:** DIRECTION, untested (opened 2026-10-04 with the owner). It is the program half of
[demand-streams](demand-streams.md). Nothing is adopted.

## The idea

A subscription is already a program shipped to the worker: the condition algebra, evaluated at each tick.
This entry asks how expressive that program may be.

The goal is to leave observers unhampered:
- they can aggregate at the source, so only results cross the network;
- they can write elaborate stopping and skipping conditions, beyond `until` and `every`.

## Why it matters when communication is slow

- **Fewer bytes.** Reductions at the source send only results. DTrace is the model: probes expose data, and
  scripts aggregate it in place.
- **No round trips.** A decision made at the source costs no round trips. "Observe, decide, command" costs
  one full round trip per decision.
- **CALM** (Hellerstein and Alvaro, *consistency as logical monotonicity*) says a program whose output only
  grows with its input needs no coordination. A non-monotone program needs to know its input is complete.
  Most rich stopping conditions are non-monotone: "no improvement in 5 evaluations", "plateaued".
  - Evaluated by a remote observer, such a condition needs coordination.
  - Evaluated at the worker, it does not, because the worker holds the complete committed prefix of its own
    lineage. It is the only writer of its own stream.
  - *That application of CALM is a framework prediction, untested. CALM itself is established.*

## The ladder of expressiveness

| Rung | Example | Worker safety | Replays over the log |
|---|---|---|---|
| 1. today's algebra | from / every / until | ✓ | ✓ |
| 2. + aggregations and value predicates | windowed mean, quantiles, "until loss < x" | ✓ | ✓ if its inputs are logged |
| 3. a total, deterministic language: definite clauses (Datalog) with stratified negation and aggregation | plateau detection, stops over several conditions | ✓ always terminates | ✓ if its inputs are logged |
| 4. sandboxed general code (Wasm with fuel metering) | anything | ✓ if fuel and memory are bounded | ✓ only if deterministic |
| 5. arbitrary Python | anything | ✗ can stall or crash a GPU job | ✗ |

**Rung 3 is the fragment [if-built-today](if-built-today/README.md) already chose** (see
[definite-clause-maximality](definite-clause-maximality.md)), so the same logic would apply everywhere.

## Constraints that choose the rung

- **Never harm the worker.** Every program needs bounded time and memory per tick, isolation from training
  state, and contained failure.
- **Determinism,** for replay and for independence from arrival order.
- **Two classes of program.**
  - **Over logged values:** `history` and `ensure` can replay them.
  - **Over unlogged worker data:** they cannot be replayed. This is the bandwidth case, such as reducing a
    large array the worker `set`s into a histogram. Only the logged output survives.

## Relation to the condition algebra and `ensure`

- **Target conditions belong here,** as shipped programs, and not in the schedule algebra. `control.target`
  died because it designed a fixed wire surface for an imagined consumer.
- **`ensure` still needs a coordinate that only increases:** the stream index (layer 2, option S).

## Before building

Name a real consumer friction first: a worker-side reduction or stop that some consumer actually needs.
Until then, this stays a direction.
