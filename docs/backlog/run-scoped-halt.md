# A halt that survives an episode boundary

**The finding:** `control.stop` is an **episode-scoped request**; at least one consumer reads it as
a **run-scoped halt**. They agree until an episode ends. This is not a third-party problem, and
issue #39's framing ("a third-party reclaim silently discharges…") describes one route to it rather
than its cause.

## The measurement

No forgery, no reclaim tool, no third party — an operator halts a run and an ordinary live worker
honours it:

```
operator sends control.stop   -> undischarged=[1]
worker honours it, stops      -> undischarged=[]

mycooc next_claimable would consider this run claimable: True
and a new episode claims it successfully:                 True
```

The discharge is **correct**. `../specs/stop-discharge.md` designates `lifecycle.stopped` as the
stop's *effect*; the stop was answered, so it is spent. The run then restarts.

## Why the discharge rule is right, and must not be touched

The obvious "fix" — let a stop survive the boundary — is the bug the discharge rule was built to
remove. `../specs/stop-discharge.md`, symptom 1, with a committed-RED test
(`tests/test_worker.py::test_resumed_episode_ignores_prior_episodes_stop`):

> A `control.stop` that halted episode 1 is re-drained by a resumed episode 2, re-armed, and honored
> again — the resume dies at its first step.

Three independent design reviews converged on the discharge fold, and two of them independently
refuted the first-proposed alternative. **Do not reopen this.** The gap is not in the rule; it is
that there is no record for the *other* thing a user might mean.

## The mismatch, precisely

| | question | scope |
|---|---|---|
| runstate | "was this stop request answered?" | the **episode** |
| the consumer | "should this run be running?" | the **run** |

`mycooc/rungraph/ports.py` — `stopped(rid)` is documented as *"Does this run carry an undischarged
stop?"*, and `rungraph/state.py::next_claimable` uses it as one of four scheduling predicates. That
is the run-level question, answered with the episode-level fact.

## What this rules out

- **Patching the consumer's reclaim tool** (read `undischarged_stops`, re-`send` them after the
  release) is the wrong layer: it hand-maintains run-level state across a boundary the protocol
  deliberately clears, at one of several places that boundary occurs. It would leave the
  ordinary-worker route above untouched.
- **`lifecycle.evicted`** (`claim-eviction.md`) would not have fixed this either. It refuses to
  discharge, so it closes the third-party route while the honest-worker route still discharges and
  the run still restarts. Recorded because #39 was one of the two defects that design was justified
  by — it was aimed at the wrong plane.

## The fork

**(a) The consumer owns the halt.** Scheduling policy is the consumer's domain; runstate models runs
and episodes, not what ought to be scheduled. A `halted` set in the rungraph answers the run-level
question directly, and nothing needs re-issuing because the halt was never in the log to be eaten.

**(b) runstate gains a run-scoped halt** — a standing `control.halt` with an explicit eliminator,
distinct from the one-shot `control.stop`, and excluded from the discharge fold by construction.

**The deciding question is concrete:** *must an operator be able to halt a run from a machine that
does not have the consumer's scheduling state?* If yes, (a) fails — the log is the surface reachable
from everywhere, and a consumer-local flag is not reachable from a compute node. Given the
cluster/workstation split, this looks like a yes, but it has not been confirmed with the consumer.

## The bar, for (b)

`claim-eviction.md` was deferred with an explicit revival trigger: *a second independent consumer,
or a third party that cannot fix its own writer.* A run-scoped halt clears the second clause on its
face — the loss happens with no third party at all, so there is no writer to fix. That is a stronger
warrant than the eviction ever had, and it is why this entry exists rather than being folded into
that one.

It does **not** get a free pass on the rest: `protocol-algebra.md` L2 wants a declared multiplicity
and an eliminator, and `../specs/service-worker.md`'s canonical-form objection applies to any new
control verb. Answer the deciding question first; those are only worth arguing if it comes back yes.

## Related

- `../specs/stop-discharge.md` — the shipped rule, and why it is right
- `claim-eviction.md` — deferred; would not have fixed this
- `lifecycle-stopped-unbundling.md` — the adjacent "one record, five jobs" thread. Note this entry
  is **not** an instance of it: the problem here is a missing *scope*, not a bundled job.
