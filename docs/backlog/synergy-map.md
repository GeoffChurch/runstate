# Backlog synergy map & sequencing

**Status:** living tracker, opened 2026-06-07. The [index](index.md) is the flat
work list; this is the **dependency/synergy view** over it — which items unlock
which, scored against the orthonormal-basis rubric (`../../CLAUDE.md` → "Design
rigor"). Update the per-cluster **State** lines as threads land; retire a cluster
when its members all ship or move to `../dead_ends/`.

## Scoring principle

Weight **empirical validation** above inspection. mycooc — the first end-to-end
consumer ([mycooc-migration-audit](mycooc-migration-audit.md),
[mycooc-adoption](mycooc-adoption.md)) — is a *basis-completeness oracle*: every
workaround it was forced to write marks a missing basis vector (rubric §5 — the
absence-of-serendipity smell). An item a real consumer paid for outranks an item
justified only by design taste.

## The four clusters

### Cluster 1 — the on-demand producer (the keystone)

**State:** autonomous/sequence half shipped (memoizer + `ensure-until-condition`);
**the service half SHIPPED 2026-06-10** (`../specs/service-worker.md` —
`serve`/`retire`/`pinned`, the careful death, expiry counter-records, the
positional answer fold / `live_demand`; dogfood `examples/monitor/`).
Episode-scoped time-leases shipped 2026-06-11
(`../specs/time-lease-boundary.md` — the ghost-lease flap deleted by
construction; the waker needs no policy). Remaining: lazy-launch (the
relaunch decider — its demand fold exists and its hardest input is gone) and
the *function* producer (the second `ensure` implementer must be stepped and
memoizable — mycooc-analyze, not the pure service).

`ensure` today has exactly **one** producer (the autonomous/sequence worker), so
the "Producer Protocol" is a *basis of one* — unfalsifiable. The keystone is the
orthogonal **second** producer: on-demand/function, demand-driven not
target-driven. Building it makes a cluster of deferred items *real at once*:

- triangulates the named **Producer Protocol** (two implementers → observed, not
  guessed) — memoizer spec Decisions 5–6;
- gives the **index-filter-algebra** (`from`/`every`,
  [memoizer-index-algebra](memoizer-index-algebra.md)) its first reason to exist —
  a *function* worker genuinely skips computation for strided/random-access keys,
  where a sequence worker can only skip *emitting*; YAGNI until exactly this lands;
- is the validating use case for run-episodes' **service/lifeline policy**
  ([run-episodes](run-episodes.md): lazy-launch-on-subscribe + reap-at-zero);
- chains back to the bug: lazy-launch needs the §12.1 single-spawn guard, which
  needs the atomic CAS — **F1 → atomic claim → safe single-spawn → safe
  lazy-launch → service producer.** The most important *fix* and the most
  promising *new thread* are one thread. (F1 fixed 2026-06-07; the rest of the
  chain — single-spawn guard, lazy-launch, the producer — remains.)

Secondary members that complete the capability:

- [ensure-redrive-recoverable-terminations](ensure-redrive-recoverable-terminations.md)
  needs a discriminator between "recoverable stop" and "fatal error" — exactly
  what a **`lifecycle.stopped.reason` vocabulary recipe** supplies
  (resumable-`timed_out` vs fatal-`crashed`). Build them together.
- **channel-postgres** LISTEN/NOTIFY is the natural substrate for
  *wake-on-subscribe* (push beats polling for a lazy-launched worker).

**Payoff:** one coherent capability — on-demand compute / inference-server /
metric-server — out of ~6 deferred items, each making the others exercised
rather than speculative.

### Cluster 2 — `run_id` as the universal join key (Layer 4)

**State:** `run_id()` recipe specced (`../specs/run-id-recipe.md`); dedup already
a substrate affordance; the **Store** unbuilt.

Content-addressed identity (`run_id = h(inputs)`) is the single point where three
features meet from one decision:

- **dedup** — "has this run happened?" = `open_channel(run_id)` exists ∧
  `peek_terminal` terminal. *No new API.*
- **idempotent resume** — same inputs → same `run_id` → same log →
  [run-episodes](run-episodes.md) relaunch resumes from run-keyed state.
- **Store membership** — `run_id` is the Store's primary key; the many-to-many
  Run × Experiment is the one thing the per-run log structurally can't hold.

The **Store** ([mycooc-adoption](mycooc-adoption.md) is the validating use case)
is the relational index over these keys plus the membership the log discards —
which is *why* nothing else can supply it, and why the Hasher correctly collapsed
to a recipe (its only content is the workload-specific partition choice).
Cartesian sweep + reuse-skipping sit on top.

### Cluster 3 — the read-projection basis → derived tools

**State:** ✅ **COMPLETE.** F2/F3 + the cross-episode replay shipped 2026-06-09
([stop-discharge](../specs/stop-discharge.md)); the readers (F5–F8) shipped
2026-06-10 ([observables](../specs/observables.md): `value_series` / public
`progress` / `latest_episode` / `handle_pid`, with `liveness.py` absorbed into
the new `observables.py`). mycooc deletes its workarounds in one sweep (its
checklist: `mycooc/docs/backlog/infrastructure/runstate-adoption-sweep.md`).

F5/F6/F7 are **catamorphisms the log already determines** — `value_series` folds
`value` events, `progress` maxes the heartbeat/stopped axis, `current_episode` is
the latest `started`. Making consumers re-fold data the substrate already holds is
the missing-basis-vector smell. Reframed: these *complete the read-projection
basis*, they aren't ergonomic sugar.

- **F5/F6/F7** ([mycooc-migration-audit](mycooc-migration-audit.md)) — **shipped
  2026-06-10**: `value_series()` / public `progress()` / `latest_episode()`
  (the F7 sketch's `current_episode`, renamed — static over dynamic); F7 had
  wrapped a real stale-pid bug.
- **F8** — **shipped 2026-06-10**: `handle_pid()` owns the handle format in
  *one* function (with `resolve()` routed through it), ahead of the `?start=`
  disambiguator ([conventions-hygiene](conventions-hygiene.md) F9) that would
  have broken every consumer's `rsplit`.
- **F2 (+F3 + the cross-episode stale-stop replay)** — **shipped 2026-06-09 as
  one fix: [stop-discharge](../specs/stop-discharge.md)** (specced and
  implemented same day; the strict-xfail pin
  `test_resumed_episode_ignores_prior_episodes_stop` now passes).

**Payoff falls out:** the cli-status / cli-stop one-liners and the
[webapp-viewer](webapp-viewer.md) become trivial once these readers are public —
the webapp's "what it requires from the library" list is a subset of them. They
are also the substrate side of the eventual viewer-discovery protocol (Cluster 4).

### Cluster 4 — visualization (long-horizon, frozen)

**State:** unbuilt; **frozen** until Cluster 2 ships and a viewer audience exists.

[visualization-story](visualization-story.md): the Store answers "what runs
exist?", a **viewer-discovery protocol** answers "how do I subscribe?"
(`Watcher`'s `RunStatus`/`Running` already gestures at this fold), a data-plane
event protocol carries richer `value` types. The novel piece is *discovery*; the
event types (Histogram/Image/Tensor) are well-understood shapes and low-novelty.
Highest opinion-creep risk — gate hard.

## Two cross-cutting insights

**One drain rule unifies F2, F3, and the episode control-cursor** — specced and
**shipped** 2026-06-09 as [stop-discharge](../specs/stop-discharge.md), which
carries the rule and the refutation trail of this paragraph's earlier
"react-after-the-cursor" framing (the strict-xfail pin flipped to a passing
test). The synergy observation held in execution: correcting one type error (a
command-fact implemented with the `Subscription` type) closed the lost-stop
(F2), the clobber (F3), and the cross-episode replay in one stroke. (The generalizing lens — conventions as designated intro/elim pairs —
is now [protocol-algebra](protocol-algebra.md).)

**The mycooc audit is a basis-completeness oracle.** It is the rubric's
serendipity-absence test run by ground truth instead of by inspection: F5/F6/F7 =
missing read-projections, ensure-redrive = missing terminal-classification,
stopped.reason = missing why-vocabulary. Treat its findings as higher-confidence
than any inspection-only item.

## Most / least promising

**Most** (leverage ÷ cost, validation-weighted):

1. ~~**F1** — verified P0 *and* the prerequisite for Cluster 1.~~ **Fixed
   2026-06-07** (fix superseded 2026-06-09 by the atomic-by-construction form —
   mechanism in the audit's F1). Unblocked Cluster 1; the cheap Cluster 3 batch
   is next.
2. ~~**Cluster 3 batch**~~ — **done** (F2 half 2026-06-09, readers 2026-06-10);
   the mycooc deletion sweep validates it.
3. **Cluster 1** — the richest *design* payoff (keystone serendipity). **Now
   the top pickup.**
4. **Cluster 2** — the Layer-4 backbone; larger relational build; recipe specced.

**Least:**

- Reconfigure / Snapshot / Cleanup commands — bake workload opinion (fail the
  meta-constraint). Keep parked indefinitely, not "v0.2 if a use case emerges."
- **Pause / Resume** — *derivable* from run-episodes (a stop ends an episode) +
  idempotent relaunch (resume = a new episode). Fails Independence; don't build as
  separate commands.
- protocol-async-api — premature meta-layer.
- channel-redis — dominated by channel-postgres for the same cross-host use case,
  weaker durability (and here the log is the source of truth).
- Visualization data-plane event *types* — low novelty; lead with discovery, if/when.

## Sequencing

`F1` ✅ (fixed 2026-06-07; unblocked all) → **Cluster 3 batch** ✅ (complete:
F2/F3/stale-stop 2026-06-09 as [stop-discharge](../specs/stop-discharge.md),
the readers 2026-06-10 as [observables](../specs/observables.md); next mycooc
sweep deletes the workarounds) → **Cluster 1** (keystone — NOW NEXT) →
**Cluster 2** (Layer 4) → Cluster 4 stays frozen
until the Store lands. channel-postgres slots in *with* Cluster 1
(wake-on-subscribe); the CLI/webapp tools *after* Cluster 3's readers are public.

## Doc hygiene (v0.1→v0.2 staleness, fold in when touched)

- [webapp-viewer](webapp-viewer.md) — written against the dead v0.1 API
  (`messages` table, `direction='to_orchestrator'`, `control.send_stop`,
  `role="orchestrator"`, `iter_history()`). Rewrite to the topic-log substrate
  before picking up.
- [visualization-story](visualization-story.md) — cites
  `messages-v0.1.schema.json` as "current"; the schema stack is now per-convention
  v0.2.
- [run-episodes](run-episodes.md) "Built vs not" — lists episode-aware
  `peek_terminal`/liveness as not built; it shipped 2026-06-01 (per [index](index.md)).
