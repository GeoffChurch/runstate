# The layers

What runstate is internally, and — the practical payoff — **where a new thing goes**.
Companion to `positioning.md`, which answers the same question from outside.

## The stack

| | layer | contents | note |
|---|---|---|---|
| **L0** | **Substrate** | `send` / `read` / `latest` / `last_seq`, and CAS via `send(expected_seq=)` | the only **enforced** layer |
| **L1** | **Content** | `value` — `(name, step) → body`. Open, app-owned `name` axis, no obligation | |
| **L2** | **Selection** | the condition algebra — `Condition`, `satisfied`, `Subscription`, `history` | **a library, not a protocol** |
| **L3** | **Identity** | `lifecycle.started` / `stopped` — the claim, episodes | where *who produced it* enters |
| **L4** | **Detection** | `lifecycle.heartbeat`, `launcher.launched` / `terminated` | exists because L3 can fail to arrive |
| **L5** | **Control** | `control.stop` (linear), `control.subscribe` / `unsubscribe` (affine), `lifecycle.nak` | addressed to a producer ⇒ presupposes L3 |
| **L6** | **Materialization** | `ensure` — *"I want this content; produce what is missing"* | demand-first; composes L2 + L3 |
| **L7** | **Planning** | dependency graph, scheduling policy | **not runstate.** The consumer's (mycooc's rungraph) |

## Three properties worth defending

**L0 is the only thing enforced.** Appends are atomic, the order is total, and a CAS admits exactly
one winner at a seq. Everything above is *recorded* — true only if the writer was honest. Most
design errors in this repo's history are a layer-above-zero fact mistaken for an L0 guarantee;
`positioning.md` keeps the running list.

**L2 is a library and should stay one.** `vocabulary/schedule.py` imports *nothing* — not `Channel`,
not `Topic`, nothing from runstate at all. It defines no records and carries no obligations. It is
reusable by anything slicing any indexed data, and the memoizer replays schedules over logged values
without reading a single `control.subscribe` record. (Its home under `vocabulary/` is a slight
misnomer: it is not vocabulary, it is algebra.)

**L4 exists entirely because L3 can fail to arrive.** A worker that is SIGKILLed writes no
`stopped`. Every detection primitive is a hedge against that missing self-report, which is why there
are *two independent witnesses* — `lifecycle.*` is the self-report, `launcher.*` the external one,
and `protocol-algebra.md` L3 keeps them as independent partial observers joined only at the verdict.
This is the cleanest justification for that part of the reserved set: each member is there because
something can fail to arrive.

## The seam: time couples information to process

L2 is beneath L3 — by import graph, not aspiration — with exactly **one** coupling, and it is
instructive:

- **Step-indexed selection is intrinsic to the information.** Step 400 means the same thing
  regardless of which process computed it, or how many attempts it took.
- **Time-indexed selection is not.** It needs the run epoch, which is `lifecycle.started.t` — a fact
  about a *process*. `memoizer._epoch` is the whole coupling; step-only schedules never touch it.

So if you are looking for the line between "the information" and "the run that produced it," it is
the clock.

## Is subscription more primitive than lifecycle?

Half yes, and the true half already ships.

- **Demand-as-algebra is beneath lifecycle** (L2). Already realized.
- **Demand-as-request is above it** (L5), and structurally must be: a subscription is *addressed to
  a producer*, so publishing one presupposes something to receive it.

Push the information-first view all the way and you land on a **content-addressed build system**:
`ensure` is `make`, the content-addressed run id is the hash, reuse is a cache hit. Much of that is
already absorbed — `specs/run-id-recipe.md` makes identity content-derived, and
`backlog/store-deliberation.md` records the prior art converging the same way (Iceberg/Delta:
facts-in-band plus a derived index plus a catalog that only enumerates roots).

What a build system adds beyond that is **L7** — a declarative dependency graph and the ability to
*plan* production. That belongs to the consumer, because planning is scheduling and scheduling is
not runstate's. Whether a *graph* protocol is generic enough to bless is genuinely open; unlike a
halt, it is not enforcement, so the usual objection does not apply.

## Where does a new thing go?

The decision procedure, in order. Most proposals die at step 1 or 2.

1. **Does it need to be *enforced*, or only *recorded*?** Enforcement is not available above L0, and
   L0 is complete (`protocol-algebra.md` L1: `send`/`read`/`latest`/CAS). If your thing only works
   when someone obeys it, it belongs to whatever spawns the workers — not here.
2. **Can a tool that knows nothing about the project act on it?** If no, it goes on the open `name`
   axis (L1) as a value or a register, and the reserved vocabulary does not grow. This is the test
   the closed set actually encodes, and it is why the set is small: *generic* things are rare, not
   because control is special. "Switch the optimizer to TPE" is control-shaped and fails this test.
3. **Does it carry an obligation — must someone eventually discharge it?** If yes, it needs a
   designated eliminator and a declared multiplicity (`protocol-algebra.md` L2), and it cannot live
   in the value plane, which is defined as the no-obligation case. If no, a value register is the
   blessed shape (`specs/completed-opt-in.md` has the worked recipe).
4. **Is it push or pull?** Push is linear — it must be consumed exactly once (`control.stop` ↔ the
   next `stopped`). Pull is affine — it may stand forever unconsumed (`control.subscribe` ↔
   `unsubscribe`). The split is not stipulated; it falls out of what each verb means.
5. **Can more than one party write it?** Then *you* own the arbitration. `send(expected_seq=)` is
   available to your protocols, not just runstate's. Skipping this is what refuted
   `specs/control-target.md`: measured, **373 spawns in 3 seconds**, and *"not 'last writer sets the
   goal' — the writer with the fastest poll loop wins."*

## Building protocols on values

The open axis is the extension mechanism, and it is sufficient — a documented convention over
`value` records is a complete protocol. Two shipped examples: the completion-reason register
(`specs/completed-opt-in.md`) and mycooc's `input_provenance`.

Practical notes:

- **A command is a fact about desired state.** "Switch to TPE" becomes "`requested_optimizer` is
  TPE" — a register the worker reads and reconciles against. The value plane carries control with no
  command semantics needed.
- **Registers are stepless.** `step: null` keeps them out of the step-indexed metric folds
  (`_value_points` skips them), so they do not pollute `value_series` or name enumeration. Note
  `history(ch, <register-name>, …)` **raises** — a register is not a series.
- **Obligations are yours.** If your fact needs clearing, nothing in runstate will notice that it
  has not been. That is not a gap to be filled by declaration — a declaration would not clear it
  either — it is the same boundary as everywhere else here.
