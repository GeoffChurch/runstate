# A halt that survives an episode boundary

**The finding:** `control.stop` is an **episode-scoped request**; at least one consumer reads it as a
**run-scoped halt**. They agree until an episode ends. This is not a third-party problem, and issue
#39's original framing ("a third-party reclaim silently discharges…") named one route to it rather
than its cause.

**The proposed resolution:** a **recipe, not a protocol verb** — runstate owns where the fact lives
and how it is ordered and observed; the consumer owns what it means and what to do about it. §4.

## 1. The measurement

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

## 2. The discharge rule is right and must not be reopened

Letting a stop survive the boundary is the bug the rule was built to remove —
`../specs/stop-discharge.md` symptom 1, with a committed-RED test
(`tests/test_worker.py::test_resumed_episode_ignores_prior_episodes_stop`):

> A `control.stop` that halted episode 1 is re-drained by a resumed episode 2, re-armed, and honored
> again — the resume dies at its first step.

Three independent design reviews converged on the discharge fold. The gap is not in the rule; it is
that nothing records the *other* thing a user might mean.

## 3. The boundary: what is runstate's, and what is not

| | question | scope |
|---|---|---|
| runstate | "was this stop request answered?" | the **episode** |
| the consumer | "should this run be running?" | the **run** |

`mycooc/rungraph/ports.py` documents `stopped(rid)` as *"Does this run carry an undischarged
stop?"*, and `rungraph/state.py::next_claimable` uses it as a scheduling predicate — the run-level
question answered with the episode-level fact.

**The policy is not runstate's, and that is checkable rather than a matter of taste.** runstate never
spawns on its own initiative: `memoizer.ensure` calls `producer.extend(until)`, and `Producer` is a
**seam** the consumer implements; `launcher.relaunch_if_needed` takes the launcher as an argument and
is "a launcher-agnostic, best-effort single-spawn guard." Every spawn is the consumer's. So *should
this run be scheduled* is always the consumer's question, and a `control.halt` verb would put
scheduling policy into the protocol.

**But the fact still wants the log.** Of the four properties a halt needs, only one is policy:

| property | a consumer-local marker | the log |
|---|---|---|
| reachable from any machine | works (NFS / Postgres) | works |
| **ordered against the claim** | ✗ a marker and a `lifecycle.started` race with no arbiter | ✓ one total order |
| **observable by tools that speak runstate** | ✗ the cockpit would have to learn the consumer's format | ✓ |
| decide not to schedule | ✓ **the consumer's** | ✗ |

## 4. The recipe (proposed — attack before use)

Modelled directly on the shipped **completion-reason register**
(`../specs/completed-opt-in.md` §"Recipe: the completion-reason register") — *"blessing the SHAPE
only (no vocabulary — workload words never enter the protocol)."*

- **Shape.** A stepless `value` record: `topic="value"`, a conventional name of the consumer's
  choosing, body `{value: {...}, step: null, t: now}`. `step=null` keeps it out of the step-indexed
  metric folds — it is a register, latest-by-`seq`, not a series point. **No wire change**: the
  substrate already carries arbitrary `value` bodies.
- **Writer.** Anyone who can append — an operator, a dashboard, the scheduler. The value plane is
  author-agnostic by design, which is the same property the completion-reason recipe relies on.
- **Eliminator.** Last-write-wins: append the cleared state. A standing fact with an explicit
  clear, not an implicit expiry.
- **Reader.** The consumer's scheduler, as one predicate among its four.

**What runstate owes: nothing mechanical.** The register is already run-scoped
(`latest(VALUE, name=…)` is not episode-scoped), already survives boundaries because no fold consumes
a register, already reachable and observable, and #19 made exactly that read index-served. This
costs a documented recipe — no topic, no schema bump, no fold case, no conformance change. That is
the bar `claim-eviction.md` failed.

## 5. The load-bearing divergence — attack this first

The existing recipe's **Rule 1 is "episode-scope the read"**: *"Read only the register after
`latest_episode().seq`, else a resumed run reports the prior dispatch's reason before it re-emits.
(mycooc learned this; it is not optional.)"*

**This recipe deliberately does the opposite**, because run-scoped persistence is the entire point.
That is either the correct distinction — a *reason* is per-episode, a *halt* is per-run — or it is
the same hazard the existing rule was written in blood to prevent, wearing a new name. It has not
been tested either way, and it decides the whole design.

Adjacent, and to be probed with it:

1. **Is "value" the right plane for a non-measurement?** A halt is not something the run measured.
   It would surface in name enumeration and in any value-plane sweep. `faithful-representation`
   says do not reuse a near category when the reuse is lossy — is this reuse lossy?
2. **Rule 2's analogue.** The existing recipe warns that the register is a *prophecy*, and that
   done-ness must come from the terminal, never the register. What is the equivalent trap here — is
   there a reader who would treat "halted" as "stopped"?
3. **Does it actually race-free?** Being in the log gives one order, but nothing CASes the halt
   against a claim. Can a halt and a claim interleave such that a worker starts anyway?
4. **Who clears it, and does that need the same care as who sets it?** A forgotten halt from three
   episodes ago is a standing fact by construction — feature or trap?
5. **Does the cockpit actually gain anything?** The observability argument above is asserted, not
   verified. Would it display this without a change?

## 6. What this rules out

- **Patching the consumer's reclaim tool** (re-issuing the stops it swallows) is the wrong layer: it
  hand-maintains run-level state across a boundary the protocol deliberately clears, at one of
  several places that boundary occurs, and leaves the ordinary-worker route untouched.
- **`lifecycle.evicted`** (`claim-eviction.md`) would not have fixed this. It refuses to discharge,
  closing the third-party route while the honest route still discharges and the run restarts. #39 was
  one of the two defects that design was justified by — it was aimed at the wrong plane.
- **A `control.halt` verb.** Scheduling policy in the protocol; see §3.

## 7. Related

- `../specs/stop-discharge.md` — the shipped rule, and why it is right
- `../specs/completed-opt-in.md` — the recipe this one copies, including the rule it inverts
- `claim-eviction.md` — deferred; would not have fixed this
- `lifecycle-stopped-unbundling.md` — adjacent, but **not** the same thread: that is a bundled job,
  this is a missing scope
