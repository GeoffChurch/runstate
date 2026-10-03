# Red team: the fencing lead, checked against the repo's own specs, folds, invariants and dead ends

Target: `scratchpad/fencing-lead.md`, "commands out of the worker's stream". Scratch experiments are in
`scratchpad/fencing-specs/exp.py`. They import the checkout's `runstate`, which the script asserts,
and the repo was not modified.

## Verdict: dead as a split. The core idea survives without the split.

The lead bundles two ideas. Only one of them carries weight.

- **(A) Make every worker write a CAS**, so that a displaced worker's next write fails, its records
  never land, and it finds out. This is the right shape for the log plane.
- **(B) Split the log into a worker stream and a control stream**, so that A's `expected_seq` is not
  made stale by other writers. This part is unsound against shipped invariants. It also turns out to
  be unnecessary: A works on the existing single log with no split. Each worker write is
  `send(expected_seq=own_head)`. On `None`, the worker reads the delta and counts itself displaced
  only if a `lifecycle.started` intervened; otherwise it advances its head and retries. On both
  Memory and SQLite this landed none of the displaced worker's later writes (experiment C), with
  zero false displacements from interleaved control traffic or raw value sends. Overhead in a crude
  harness was about 1.1–1.25× per write, flat in foreign traffic up to 1 foreign record per 10
  writes. All four pairing rules, `retire()`, the schemas, locators and backends are untouched.

The split fails on the dilemma that killed `per-episode-loglets.md`, now at the **death** rather
than the birth.

- **Literal form** (two logs, independent `seq`): this needs no new substrate operation, as the
  lead says. But three of design §7's four pairing rules, and the launcher tier's claim window, now
  compare positions across two sequencers. `retire()`'s CAS can arbitrate only one of its two
  contests. Design §14 already ruled on this: *"one authoritative sequencer per run … the causal
  regime … is a different protocol, not a later version of this one."*
- **Label form** (one log with a global `seq`, plus a per-stream CAS): this keeps order. But the
  CAS that fences is "append iff the last record in scope S has seq k". With S =
  `lifecycle.started`, that is revision 2's epoch fence as a new atomic transition (L1 rule b),
  which the lead says it avoids.

What remains of the lead is (A) on the single log. That is revision 2's fence moved client-side, and
it must still answer revision 2's refutations 1, 2 and 4 and revision 3's refutations 2, 4, 5 and 6
on their own terms (see the scorecard below).

## Findings

The breaking cases name records as `W#n` (worker stream) and `C#n` (control stream). "Naive" means
today's code run against the split unchanged: both seqs are `int`, so every comparison type-checks.

| # | Flaw | Breaking case | Severity | Inherent or fixable |
|---|---|---|---|---|
| F1 | **The stop ↔ stopped pairing (§7 instance 1) crosses the split** | Ep1's `stopped` is at `W#5000`. The operator sends `control.stop` while the run is down, at `C#3`. Ep2's `_discharge_floor` is 5000, and `3 < 5000`, so the stop is skipped silently: S2's "honored exactly once" becomes "dropped", and `undischarged_stops` reads `C` after 5000, which returns `[]`. If the magnitudes are reversed (`C` outpaces `W`), a stop already honored by ep1 re-arms in every later episode. That is stop-discharge symptom 1, the committed-RED test, returning. | **Fatal** | Inherent to the literal form. The candidate tiebreaks: raw ints are silently wrong; wall-clock `t` is ruled out by observer-clock's "four clock designs, dead"; a stamp means `Stopped` gains a control watermark. A stamp is a lifecycle-v0.5 bump and a semantics change: stops that land after the last drain survive into the next episode. That is §14's causal rewrite. |
| F2 | **Time-lease ↔ episode boundary (instance 3) crosses the split** | A service with heavy client churn, so `C` outpaces `W`. A lease lands at `C#5000` during ep1. Ep2 claims at `W#300` and registers the lease (the one allowed re-anchor). Ep2 dies, and ep3 claims at `W#600`. `boundary_voided(5000, [1,300,600], 600)` is False, so the lease registers again. `live_demand` uses the same comparison and stays live, so `ensure_served` re-wakes the service forever. time-lease-boundary's "≤2 relaunches by construction" is gone. That is exactly the ghost flap that spec deleted. | **Major–fatal** | Inherent to the literal form. Fix: `Started` carries the control head it saw at claim time (a schema bump). The ordering is then partial, with bounded slop when a subscribe lands between the claimant's read and its CAS. |
| F3 | **The worker's answers to control facts have no consistent home.** These are `lifecycle.nak` and the expiry `control.unsubscribe`. The lead assigns neither. | If they go in `W` (fenced), the answer fold (instance 2) compares `W` naks with `C` subscribes (`worker.py:380`, `live_demand`). If they go in `C`, they are unfenced. Case: B resumed from an older checkpoint, and displaced A is ahead in step. A's next `_service` passes `until:{step:N}` for request R, so A writes `control.unsubscribe(R)` to `C` *before* its heartbeat CAS fails. B's drain pops R, the client's subscription is cut short, and `live_demand` reports it answered. The lead's claim that the displaced worker's "later records never land" is false here. | **Major** | Inherent. The split is by author, but L2 pairs by fact. The worker is the designated eliminator of control facts (§5: it "complet[es] the subscribe/unsubscribe pair exactly as `lifecycle.stopped` completes `control.stop`"). An answer must be ordered in `C` and fenced in `W`, and one CAS covers one stream. With `C` routing, the damage is bounded to one tick after displacement. |
| F4 | **`retire()`'s death-CAS can arbitrate only one frontier** | Experiment A: the worker drains `C` (empty) and reads `W`'s head. A client's subscribe races into `C`. The `stopped` CAS on `W` **wins**, and the subscribe is orphaned (never serviced, never naked). This is exactly the row service-worker.md's death-CAS exists to close ("subscribe lands between final drain and dying breath → death-CAS loses"). If the death CASes on `C` instead, it is unfenced against displacement. This is revision 2 refutation 4 ("the death-CAS is mutually exclusive with a fence") in new form. per-episode-loglets' "a CAS arbitrates only writers who share a frontier" holds at the birth, which the lead checked, and fails at the death, which it did not. | **Fatal** (service workers) | Fixable only with a two-head CAS ("append iff `W` head = w and `C` head = c"). That is a new atomic transition under L1 rule (b), the "fifth op" the lead claims not to need. It would be cheap on all three backends (one transaction), but it is an operation. |
| F5 | **"CAS failure ⟹ displaced" is false whenever anyone else writes `W`** | Census (structural applicability, not frequency): **0** `Worker.emit` call sites in `translation` or `mycooc`. **Every** worker value write is a raw `channel.send(topic="value")`: 6 sites in translation (`workers.py`, `ignition/workers.py`) and 7 in mycooc (`runstate_emit.py`, `analyze_run.py`, `main.py`, `graph_adapter.py`). `emit`'s own docstring sends stepless points to raw `send`. Experiment B: one raw value send moves `W`'s head, and the worker's heartbeat CAS returns `None` with no `started` in the delta. Concretely, translation's `hyp_worker` raw-sends at step 0, its tick "detects displacement", it exits, and `stopped(completed=True)`'s CAS fails too. There is no terminal, so `relaunch_if_needed`/`ensure` relaunch, and the same thing repeats: an infinite relaunch of a correct job. Rolling upgrade: an old-code displaced worker writes plainly, and the new-code live worker self-displaces. The fence kills the legitimate worker and keeps the zombie. | **Fatal** (as stated) | Fixable by classify-on-failure (read the delta; displaced only if a `started` intervened). But that fix works on the single log and removes the split's reason to exist. Revision 2 refutation 1 is made **worse**: an opt-out writer no longer just escapes the fence, it kills conforming co-writers. |
| F6 | **The value-plane benefit does not reach the consumers** | With raw sends left unconditional, 0 of about 13 consumer value-write sites are fenced. The splice from per-episode-loglets ("a displaced worker writing later in wall clock wins the cell") persists for every consumer. mycooc's `emit_completion_reason` has two callers, the worker and the orchestrator, so one latest-by-seq register now spans two streams with no defined "latest". | **Major** | Fixable: a fenced stepless-value Worker API plus consumer migration. Orchestrator-written registers stay third-party and need a cross-stream rule. |
| F7 | **Third-party release records routed to `C`, as the lead says** | (a) `live_episode`/`_episode_stopped` (`observables.py:151,175`) would compare a `C` stopped with a `W` started. (b) The release does not move `W`'s head, so a stranded-but-alive worker is not fenced by the eviction, only later by the successor's claim. Routed to `W` with CAS instead, the reclaim tool's existing `expected_seq` write (`mycooc/scripts/reclaim_experiment.py`) would fence the stranded worker at once. That is a real gain, and the lead's partition gives it away. | Minor–major | Fixable: route release/eviction records to `W`. |
| F8 | **The launcher tier's claim window crosses the split** | `_launcher_terminal` reads `read(after=started.seq, topics=[terminated])`. That is the Markov-boundary window measured 2026-07-29 (3461→92 µs; unrepairable→repairable). Naively, with a claim at `W#5000` and `C` holding only dozens of records, the read returns `[]`. A SIGKILLed worker's reaped `terminated` is never found, so there is no KILLED verdict. With the magnitudes reversed, the window admits old poison again. Correlation by launch id still attributes correctly (credit). | **Major** | Fixable: `Started` carries the control head at claim (a schema bump). |
| F9 | **`await_consumed`'s refused-by-death check crosses the split** | `watcher.py:502`, `record.seq > seq`. The run is stopped at `W#5000`. A client subscribes at `C#4` to wake the service and calls `await_consumed(seq=4)`. `5000 > 4`, so the **old** terminal comes back as "refused-by-death" and the client gives up on a run being woken for it. (`examples/vqe` keeps `sub_seq` for exactly this call.) The watermark half survives, because `consumed_seq` is already a control coordinate (credit). | **Major** | Inherent to the literal form. Fix: a stamp. |
| F10 | **Locators, discovery and backend cost** | `attach_channel` is existing-only, but pre-staged demand lives only in `C`. `ensure_served` attaching `W` gets `RunNotFound`, reads it as "no demand", and never wakes. SQLite is one `{run_id}.db` per run: a second file breaks consumer globs and GC (`gc_runs.py`), and a second table is a migration. Postgres is one dedicated connection per `PostgresChannel`, so two per run per process: a TUI observing 100 runs needs 200 connections against a default `max_connections` of 100. No change for the advisory lock (keyed `(run_id, started_seq)` and `started` stays in `W`) or the PK arbiter (credit). | Minor–major | Fixable: a multiplexed channel (a substrate API change), or locators that return a stream pair. |
| F11 | **Silent wrongness at scale** | At least 11 sites in `runstate/` compare or window a seq from one proposed stream against the other: `observables.py` 151, 175, 236, 421, 459; `worker.py` 82, 89/418, 380, 387, 300–322; `watcher.py` 471, 502, 412. All are `int` vs `int`, and `mypy --strict` passes on every one. This is per-episode-loglets' "a total order that disagrees with append order: comparable, and silently wrong", twice over. | **Major** (process) | Fixable precondition: a `NewType` per stream seq, so every cross-stream comparison becomes a type error (the "let the checker audit" rule). |
| F12 | **A mandatory per-write burden on every worker implementation** | failure-detector #6 ("a mandatory liveness burden on every worker") applies more strongly here. Every compose-your-own-loop or other-language worker must track `W`'s head, CAS every write and interpret `None`. A non-conforming one now breaks conforming co-writers (F5), where today it only fails to protect itself. | Minor–major | Partly inherent. Classify-on-failure turns the harm back into opt-out. |
| F13 | **How displacement is surfaced is unspecified** | If it is surfaced through `tick() → True` or `_lost`, revision 3 refutations 4 and 5 come back verbatim: the `claimed` docstring and the ORDER-IS-LOAD-BEARING invariant break, and `mycooc/training.py`'s `if tick(): … _maybe_checkpoint(force=True)` forces a write into the shared `output_dir` with a false "control.stop" diagnosis. | Minor (a gap in the spec) | Fixable: a separate `_displaced` flag (write-authority's own "if revisited" note) and no overloading of `tick`. |
| F14 | **The cross-stream order of `iter_events` is lost** | `Watcher._drain` uses one cursor over one log. With two cursors, `on_event` can see `launcher.terminated` before the run's last `value`s. | Minor | Fixable (two cursors; the order is cosmetic). |

## The lead's revision-2 and revision-3 claims, checked

**Revision 2's five refutations**

| | Lead's claim | Finding |
|---|---|---|
| 1. Floor with an opt-out | Answered | **Worse.** The substrate still accepts a plain `send` on `W`, and 100% of consumer value writes are plain (F5). Under "None ⟹ displaced", an opted-out co-writer kills the live worker. |
| 2. Artifact plane | Conceded | Unchanged. In consumer single-step bodies, `store.put` comes before any write the library sees, and their value writes are raw sends the library never touches. Revision 3 refutation 2 transfers verbatim. |
| 3. Zombie | Answered | **Partly.** The library learns, but only on a library write. User code learns only if displacement is surfaced (F13). A raw `channel.send` that returns `None` to user code that ignores it is still a zombie. |
| 4. The birth CAS moves the fence; forged claims mute the live worker | Conceded | Unchanged for the birth. **Worse at the death**: the death-CAS versus fence conflict reappears as F4. |
| 5. Opinion-freeness / L1 | Answered | **Moved, not answered.** The literal form is L1-clean but breaks §14's one-sequencer premise (F1–F4, F8, F9). The label form is a new atomic transition. |

**Revision 3's seven refutations** (the lead's equivalents)

| | Finding |
|---|---|
| #1 One step body too late | Better for log writes the library performs. Unchanged for artifacts. |
| #2 The majority single-step shape | Unchanged (F5/F6). |
| #3 Forged COMPLETED from the post-loop `stopped()` | **Answered.** `stopped()` becomes a CAS, which fixes #32's headline harm. Only if F5 is fixed. |
| #4 / #5 Invariant and consumer harm | Open (F13). |
| #6 Loud kill becomes silent | Unchanged. A forged `started` now produces a worker that exits with zero terminal records. |
| #7 Unbounded read growth | **Answered.** The CAS failure is the detector, so there is no per-tick read. |

**failure-detector.md.** Its "the bad write lands in the same tick, before any check fires" is
**answered** for the log plane. Its point 6 (a mandatory burden) applies more strongly (F12).

## What the lead gets right

- **The birth CAS stays sound.** Every claimant shares `W`'s frontier, so per-episode-loglets'
  "both claims win" does not apply. The lead's guess was correct.
- **The registration watermark survives.** `consumed_seq` was scoped to the inbound control order
  from the start (§6, §11, §12.6 anticipate per-subject backends). The heartbeat's watermark and
  `await_consumed`'s acceptance half carry over unchanged. Within-`C` subscribe/unsubscribe pairing
  is intact. The launcher tier's attribution is by launch id, not position, so it survives (its
  window does not, F8).
- **CAS-on-every-worker-write is the right log-plane mechanism.** It closes the forged-COMPLETED
  path. It closes episode-aim's **claim cascade** (A's honest late `stopped` cannot land above B's
  claim) *without* aim's 2124× heartbeat-fold cost. If the worker's writes are fenced, position is
  truthful again, and `latest(heartbeat)` is the live claimant's by construction. That is genuine
  serendipity. But the single-log variant delivers all of it too.
- **It costs nothing at L1 in the literal form.** On Postgres, a derived `run_id` gets its own
  `(run_id, seq)` space with no DDL, and the advisory-lock key is untouched.
- **The prior-art observation is real but imperfect** (from general knowledge, not verified here).
  Event-sourced journals hold only the entity's events because commands are *messages*, not durable
  journal facts. runstate's commands must be durable and ordered against the worker's records: the
  pre-staged idiom, §12.7, S2. Where such systems do take durable command input, the entity records
  the input offset it consumed. That is a cross-stream stamp, which is exactly §14's causal regime.

## Logical vs empirical

**Settled by reasoning:** F1, F2, F3, F4, F7, F8, F9 and F11 follow from the code and spec text cited
(experiment A demonstrates F4). F5 is logical once the census establishes that the consumers' value
writes are raw sends. That is an applicability fact, the kind of census the repo's CLAUDE.md
sanctions.

**Open, and needing measurement:** the one axis on which the split could sit on the frontier is
retry cost on **Postgres** under heavy foreign traffic. There, a classify-on-failure retry is an
extra round trip, and the SQLite figure does not transfer.

## Experiments

**Done** (`scratchpad/fencing-specs/exp.py`, Memory and SQLite):

- **A.** Literal split: the death CAS on `W` wins against a subscribe raced into `C`, leaving the
  subscribe orphaned (F4).
- **B.** Literal split: one raw value send makes the live worker's heartbeat CAS fail with no
  `started` in the delta (F5).
- **C.** Single log, CAS plus classify-on-failure: after B's claim, zero records from A land (value
  and `stopped` both refused); one benign retry each for an interleaved subscribe and a raw value.
- **D.** SQLite cost: unconditional about 11 µs/write; fenced about 12–15.6 µs/write (1.11–1.25×
  across three runs). Roughly flat for foreign records at never, 1-in-100 and 1-in-10 writes. This
  is a crude Python harness; revision 2 measured 1.008× for its fence on SQLite.

**Proposed:**

1. **Postgres retry cost** (decides whether (B) has any frontier axis). A service worker with N
   keepalive clients each refreshing (unsubscribe plus subscribe) every T s, N ∈ {1, 10, 100},
   T ∈ {0.5, 5}. Measure worker-write p50/p99 and retries per write for unconditional writes,
   single-log CAS+classify, and the literal split. Only if CAS+classify's p99 is materially worse
   does (B) have an axis. Even then, (B) is only admissible after F1–F4 are fixed by the causal
   rewrite.
2. **The pairing rules as oracles.** Build a two-`MemoryChannel` composite routing by the lead's
   partition. Parametrize the tiebreak: raw int, `t` with injected ±2 s skew, and control-head
   stamps on `Started`/`Stopped`. Run the existing S1–S4, crash-edge and pre-staged stop tests
   (`tests/test_worker.py`), `tests/test_service_worker.py` (retire race, answer fold, live
   demand), the time-lease ghost walkthrough, and `await_consumed`. Count the red tests per tiebreak.
   The prediction: raw ints and `t` go red on F1, F2 and F9; stamps go green except where stamp
   semantics deliberately differ (late stops survive). The retire race stays red under every
   tiebreak without a two-head CAS.
3. **Consumer replay.** Run translation's `hyp_worker` / `embed_ref_worker` bodies (raw-send shape)
   against a prototype Worker under "None ⟹ displaced". Expect an exit at step 0 and no terminal.
   Under classify-on-failure, expect completion. Then displace mid-body and confirm the `store.put`
   artifacts still land, which is the artifact plane revision 2 #2 and revision 3 #2 describe.
4. **#32's script** under the single-log CAS+classify variant. Assert zero post-claim records from
   the displaced worker, `peek_terminal` not forged to COMPLETED, and the checkpoint still written.
   This states precisely which plane the remaining kernel protects.
