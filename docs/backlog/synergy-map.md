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
construction; the waker needs no policy). Lazy-launch shipped 2026-06-11
(`../specs/lazy-launch.md` — Cluster 1's service half is now COMPLETE
end-to-end: demand → wake → serve → lapse → retire → re-wake, dogfooded
twice over in `examples/monitor/`). The *function* producer **resolved by
DISSOLUTION 2026-06-11** (`../specs/derived-runs.md`: key = the analyzed
snapshot; a derived run is the existing autonomous worker, one step long,
behind the existing `ensure` — zero new library surface, pinned executable by
`test_derived_run_dissolution_pin`; the index algebra demoted to dormant with
its trigger written in). **Cluster 1 is CLOSED and fully wired** — the keystone
chain's last link forged by showing it was never missing, and the mycooc
wiring landed 2026-06-11 (cached-by-default `analyze_run.py`, byte-identical
to direct; the spec records the whole arc). Cluster 2 followed the same day —
**dissolved** (`../specs/store.md`) **and its mycooc wiring executed** (Phase
7, four gated stages); the parked residue (ensure-redrive +
`stopped.reason`) remains available as riders.

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
  promising *new thread* are one thread. (Every link but the last is now
  forged: F1 2026-06-07, the guard 2026-06-01, the service worker + leases
  2026-06-10/11, lazy-launch 2026-06-11 — the producer is all that remains.)

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

**State:** ✅ **DISSOLVED 2026-06-11** (`../specs/store.md`; trail:
[store-deliberation](store-deliberation.md)). The join key did MORE work
than the May framing predicted — enough to dissolve the component built on
it.

Content-addressed identity (`run_id = h(inputs)`) is the single point where
the cluster's features meet, and under content-addressed *placement* (the
rid is also the run's address) they all fall out of one decision:

- **dedup** — "has this run happened?" = the home exists. *No new API.*
- **idempotent resume** — same inputs → same `run_id` → same log →
  [run-episodes](run-episodes.md) relaunch resumes from run-keyed state.
- **reuse** — dissolves into `ensure` against the one home (partial runs
  extend; concurrent demand converges via the birth-CAS).
- **membership** — the May claim ("the one thing the per-run log
  structurally can't hold") settled differently: it never needed a log OR
  a Store — the cell pointer carries the current binding, the consumer's
  tracked overview the archival roster.

The Hasher's collapse to a recipe stands (its only content is the
workload-specific partition choice). Cartesian sweep stays app-side;
reuse-skipping dissolved.

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
[cockpit](cockpit.md) become trivial once these readers are public — the viewer's
"what it requires from the library" list is a subset of them. They are also the
substrate side of the eventual viewer-discovery protocol (Cluster 4). *(Amended
2026-07-16: the cockpit's converged design predicts the discovery half is **not**
runstate's — the app owns the layout adapters — so Cluster 4's substrate side may be
smaller than this line assumes. The build decides.)*

### Cluster 4 — visualization (long-horizon, frozen)

**State:** unbuilt; **frozen** until a viewer audience exists (the old
"until Cluster 2 ships" half of the gate is satisfied by dissolution).

[visualization-story](visualization-story.md): "what runs exist?" is now
answered by the dissolved relational layer (`../specs/store.md`: list the
root set, follow pointers and birth records — and that same need is Recipe
4's promotion trigger, the moment the provenance record gets a schema); a
**viewer-discovery protocol** answers "how do I subscribe?" (`Watcher`'s
`RunStatus`/`Running` already gestures at this fold), a data-plane event
protocol carries richer `value` types. The novel piece is *discovery*; the
event types (Histogram/Image/Tensor) are well-understood shapes and
low-novelty. Highest opinion-creep risk — gate hard.

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
3. ~~**Cluster 1**~~ — **done 2026-06-11** (the richest *design* payoff; the
   keystone resolved by dissolution and the mycooc wiring landed).
4. ~~**Cluster 2**~~ — **dissolved 2026-06-11** (`../specs/store.md`: recipes
   + one helper, not a relational build; the wiring executed same day as mycooc Phase 7).

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
the readers 2026-06-10 as [observables](../specs/observables.md); the mycooc
sweep deleted the workarounds) → **Cluster 1** ✅ (keystone, dissolved + wired
2026-06-11) → **Cluster 2** ✅ (dissolved 2026-06-11, `../specs/store.md`) →
**the mycooc wiring** ✅ (executed 2026-06-11 as Phase 7 — the cell/run
split, runner-as-worker, riders, GC; four gated stages) →
Cluster 4 stays frozen until a viewer audience exists (its Store dependency
is satisfied by dissolution). channel-postgres slots in on its own merits
(wake-on-subscribe; the materialized-view future the store spec names); the
CLI/webapp tools after Cluster 3's readers are public.

## Doc hygiene (v0.1→v0.2 staleness, fold in when touched)

- ~~webapp-viewer~~ — **RESOLVED 2026-07-16 by deletion.** It was written against the
  dead v0.1 API (`messages` table, `direction='to_orchestrator'`, `control.send_stop`,
  `role="orchestrator"`, `iter_history()`) and is superseded by [cockpit](cockpit.md).
  *Worth noting how this went: the staleness was correctly catalogued here and the file
  was left in place for weeks — so the next reader to pick "the viewer" up would have
  started from a design for a protocol that no longer exists. A hygiene note is not a
  fix; it only pays off if someone touches the file.*
- [visualization-story](visualization-story.md) — cites
  `messages-v0.1.schema.json` as "current"; the schema stack is now per-convention
  v0.2.
- [run-episodes](run-episodes.md) "Built vs not" — lists episode-aware
  `peek_terminal`/liveness as not built; it shipped 2026-06-01 (per [index](index.md)).
