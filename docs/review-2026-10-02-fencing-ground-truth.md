# Ground truth: single-log fenced worker writes

The fencing lead's surviving variant was implemented, and the existing suite was run against it
both ways. It was then measured against seven targeted scenarios (T1–T7) plus two extra checks
(T6x, T9), each before (master) and after (spike).

- **Worktree:** `/home/gchurchill/src/runstate/.claude/worktrees/agent-a87471df76bd920b2`
- **Branch:** `spike/fenced-worker-writes`, two local commits on master@72d9c3f. Not pushed, no PR.
  - `8afc89b` implementation
  - `e545afc` tests
- **Harness:** `scratchpad/fencing-gt/`. Each script is run as `python <script> master|spike`.
  - `master/` is a byte copy of `runstate/` at 72d9c3f.
  - `gt.py` asserts which package was imported.
  - Postgres was a throwaway server at `/tmp/rs-pgsock-spike:55437`.
- **Environment caveat:** sqlite ran on tmpfs, so fsync costs and NFS (mycooc's deployment) are
  not represented.

## 1. The change

```
$ git diff --stat master...spike/fenced-worker-writes
 runstate/worker.py          | 123 +++++++++++++++++++++++++++++++++++++-------
 tests/test_fenced_writes.py |  90 ++++++++++++++++++++++++++++++++
 2 files changed, 194 insertions(+), 19 deletions(-)
```

`worker.py` is the only library file touched. About 45 of its 104 added lines are code; the rest
are docstrings and comments. Nothing changed in the substrate, the schemas, the backends,
`observables` or the folds.

- **`_head`**: the last seq this worker has *classified*. It is set to the claim seq when the
  claim CAS wins.
- **`_append()`**, the fenced append: `send(expected_seq=_head)`. On `None`, it classifies
  HEAD-FIRST: `last = last_seq()`, then `read(after=_head, topics=[lifecycle.started])`.
  - Any started above the worker's own claim latches `_displaced`. The write lands nothing and
    returns `None`.
  - Otherwise `_head = last` and the CAS retries. The CAS remains the arbiter: a rival that lands
    between the read and the retry refuses the retry.
- **Writes routed through `_append`:** heartbeat, `emit` / served `value`, `_nak`, the expiry
  `control.unsubscribe`, and `stopped()`.
- **`retire()`** keeps its read-based `expected_seq`. It does **not** use `_head`, because a
  refused append advances `_head` over records it classified but never drained. It adds one check:
  a rival claim in the tail it already reads means displaced, return True. It also returns True if
  an answer's fenced append displaced the worker mid-drain.
- **`_displaced` is a separate flag**, with a public `displaced` property. `claimed` stays True,
  `_lost` is untouched, and the ORDER-IS-LOAD-BEARING invariant is intact.
- **Surfacing:** the spike implements style (b): `tick()` and `stop_pending` return True when
  displaced, and `displaced` disambiguates. §3 / T6 measures all three styles and recommends (a).
  The switch is two lines: remove `_displaced` from `tick`'s early return and from
  `_stop_decision`.
- **Raw `channel.send` stays unfenced**, as specified.

## 2. Suite results

| tree | no DSN | with Postgres DSN |
|---|---|---|
| master @72d9c3f | 814 passed, 224 skipped | 1037 passed, 1 skipped |
| spike, implementation only (`8afc89b`) | **814 passed, 224 skipped** | **1037 passed, 1 skipped** |
| spike + new tests (`e545afc`) | 829 passed, 229 skipped | 1057 passed, 1 skipped |

The skip counts on the last row are the 5 new Postgres cases skipping without a DSN. `mypy
--strict` and `black` are clean, and the repo pre-commit gate passed on both commits.

**Zero failures, so there is nothing to explain.** That is itself a finding. A probe plugin
(`fence_probe.py`) counted 1,267 fenced appends across the suite. It found 22 refusals in 18 tests,
all benign and correctly classified: retire/nak races, `test_pinned_states`,
`test_unsubscribe_stops_emissions`, `test_a_claim_losers_clean_exit…`. It found **0 tests in which
a rival claim was ever seen**.

- No existing test exercises displacement. This re-confirms write-authority.md's observation.
- Nothing pins the old cascade or forgery behaviour, either on purpose or by accident.

The new `tests/test_fenced_writes.py` has 5 tests × 4 backends. Run against master, all 20 fail:

- 3 discriminate on behaviour (displacement, the displaced service's death, retire seeing the
  rival).
- 2 are classifier regression guards (raw co-writers, release ≠ displacement) that fail on master
  only because `displaced` does not exist there.

## 3. T1–T7

All deterministic tests (T1, T3, T4, T5a/b/d) gave **identical results on memory, sqlite-WAL,
sqlite-DELETE and Postgres**. Rows report that common result.

| test | before (master) | after (spike) | evidence |
|---|---|---|---|
| **T1** #32: a reclaim releases live A, B claims and resumes, A and B interleave, A calls the post-loop `stopped(completed=True)` | A lands **14** records above B's claim (7 heartbeats, 6 values, 1 stopped); **15** if the claim lands at the start of A's body. **6–7** checkpoint writes after B's claim. Verdict **COMPLETED while B is live**, and `live_episode` → None: B's claim is released (the cascade). With B 2× faster, A wins **4/7** loss cells and the checkpoint ends **A@9**. | A lands **0** records. Checkpoint writes: **0**, or **1** when the claim lands at the start of a body (the one-body bound). Verdict **None while B is live**, `live_episode` = B, **0/7** cells, checkpoint **B@9**, final COMPLETED (B's). `A.displaced=True`, `A.claimed=True`. | `t1_displacement.py` |
| **T2** no false displacement: 20 raw same-handle values per tick; a concurrent orchestrator (subscribe, malformed subscribe, unsubscribe, `launcher.launched`) in a thread or forked process; a separate-handle value right before `stopped` | n/a (no fence) | **0 false displacements in 700 runs** (thread ×4 backends, process ×3, 100 each, ~780 records per run); **700/700 COMPLETED**. Refused CAS per fenced write 0.26–0.34. Max retries inside one write: 1 (memory), 2 (WAL), 3–4 (DELETE), 4 (Postgres). Wall per run equals master's within noise (e.g. Postgres thread 145 vs 144 ms). | `t2_no_false_displacement.py`, `t2_spike2.out`, `t2_master.out` |
| **T3** third-party release while alive (no new started) | T3a single-step and T3b multi-step: COMPLETED, not live, a fresh claim is possible. A's records after the release land (3 / 8). T3c, release then claimant C: A lands **2** records above C's claim; verdict **COMPLETED while C is live**; C's claim released. | T3a/T3b: **COMPLETED**, `displaced=False`, A's records still land (3 / 8), as before. T3c: A lands **0**, verdict **None while C is live**, C live, final COMPLETED (C's). | `t3_t4_release_and_mistaken_claim.py` |
| **T4** mistaken claim, `handle=local://some-other-host/4242`, while A is alive | T4a, the claimant never runs: A's stopped releases the phantom. **COMPLETED**, not live, fresh claim possible. T4b, the claimant runs: A forges **COMPLETED while C is live** and `live_episode` → None (cascade). | T4a: **verdict None; `live_episode` = the phantom forever (resolve abstains on a foreign host); a fresh claim is impossible on all 4 backends**. Watcher → `presumed_dead/heartbeat_stale` after its timeout. This is the wedge. T4b: A lands 0, `live_episode` = C, final COMPLETED (C's). | same script |
| **T5** retire race | T5a, subscribe injected right before the death-CAS: CAS refused, re-drain, served, 0 orphans. T5b, injected before retire's nak: 0 orphans. T5c, concurrent client: **0 orphans / 800 trials**. T5d, displaced *service* retires: A **serves B's subscription `b1`**, heartbeats, and its careful death lands **`preempted` above B's claim** (8 records), releasing B's claim. | T5a/T5b/T5c: **0 orphans** (800 concurrent trials). T5b shows 1 refused CAS: the fenced nak advanced `_head` over the undrained subscribe, and the read-based `observed` still caught it. T5d: A lands **0**, B's verdict and claim stand. Negative control: the orphan detector catches a planted orphan (1/1). | `t5_retire_race.py`, `t5_master.out`, `t5_spike.out`, `t5d_dump.py` |
| **T6** surfacing (a) silent, (b) `tick` → True, (c) raise | see the T6 table below | see the T6 table below | `t6_surfacing.py`, `t6x_raise_swallowed.py` |
| **T7** cost, per write (p50) | unconditional: memory 2.9 µs, WAL 9.5 µs, DELETE 24.8 µs, Postgres 50.4 µs | Clean CAS: **1.01× / 1.16× / 0.97× / 1.07×**. Refusal path firing on every write: **11.4× (memory, O(N) list scan) / 1.88× / 1.75× / 3.78× (Postgres 226 vs 60 µs)**. | `t7_cost.py A` |
| T7, per tick, Worker level | clean shape (emit + tick): Postgres 145 µs. mycooc shape (20 raw values + tick): WAL 28, DELETE 49, Postgres 554, memory 736 µs. | Clean: Postgres 159 µs. mycooc: **WAL 47 (+19), DELETE 79 (+30), Postgres 783 (+229), memory 923 (+187) µs**. | `t7_cost.py B` |
| T7b, concurrent foreign writer (red-team 2's E9) | No starvation at any rate. Postgres ticks in 2 s: 2348 / 1953 / 1452 / 1259 at 2k / 4k / 8k / max foreign/s. | **Graceful up to ~4k/s** on Postgres (≈1 refusal per write, streak ≤ 22, max tick 7.7 ms). **Collapses from ~8k/s**: 645 ticks, streak 287; with 2×8k/s, 87 vs 1015 ticks (−91%) and 66 refusals per write. **Starves under a tight-loop writer** (streak 9,545 = 1.46 s, the writer's whole lifetime). Memory starves too (6 vs 230 ticks; an unbounded stress run reached 7 GB and never finished). **sqlite is not affected** (max streak 2): its file lock serializes the writers. | `t8_contention.py` |

### T6: surfacing styles against the consumers' loop shapes

All T6 rows run on memory; the other tests show the backends agree.

- **Shapes:**
  - S1 = translation R2/R3: `for _ in w.steps(total=1): <whole job>`.
  - S2 = translation R5: post-loop `store.put(per_sentence)`.
  - S3 = mycooc training: manual `if w.tick(): "[Preempt]…"; checkpoint(force)`, then main's
    forced checkpoint.
- **Displacement causes:**
  - release: a reclaim's `stopped`, then B claims.
  - mistaken: a foreign-host claim with no `stopped`.
- **Metric codes:**
  - log = A's Worker records above B's claim.
  - raw = A's raw `channel.send` values above B's claim.
  - art = A's artifact writes after B's claim.

| shape | master | (a) silent | (b) `tick` → True (the spike as committed) | (c) raise |
|---|---|---|---|---|
| S1 | log 2, raw 6, art 6. **Forged COMPLETED while B is live; B's claim released.** | log 0, raw 6, art 6. Verdict None, B live. | log 0, raw 6, art 6. Same as (a). | log 0, raw 6, art 6, rc=1. **mistaken cause: the mycooc-style backstop (`rc≠0 ∧ no stopped since dispatch`) forges ERRORED over live B and releases its claim.** |
| S2 | log 8, raw 6, art 1. B finishes first → `('A',10)` overwrites B's result. | log 0, raw 6, art 1. B first → `('A',10)`: a complete overwrite, as master. | log 0, raw 0, art 1. B first → **`('A',4)`: a truncated overwrite** (rev3 refutation 5, reproduced). | log 0, raw 0, **art 0**; B first → `('B',10)` stands. Backstop forges ERRORED in the mistaken cause. |
| S3 | log 10, raw 40, art 3. B first → `A@11`. | log 0, raw 40, art 3. B first → `A@11`, as master. | log 0, raw 0, art 2, plus a **false "[Preempt] control.stop received"** log. B first → **`A@3`: the shared checkpoint regresses from B@11 to A's step 3.** | log 0, raw 0, **art 0**; B first → `B@11` stands. Backstop forges ERRORED in the mistaken cause. |

- **T6x, a raise-style hazard:** with `Displaced(Exception)`, displacement first detected by a
  fenced nak inside `_handle_control` is **swallowed** by `_drain_control`'s
  `except Exception: self._nak(...)`. The tick then returns True, so raise style silently degrades
  to (b). A `BaseException` subclass escapes.
- **T9, an indeterminate CAS** (commit, then a lost ack that raises): the next append is refused
  by the worker's own record, the classifier adopts it, there is **no false displacement**, and
  the verdict is COMPLETED, on all 4 backends.

## 4. The red-teams' findings against the measurements

### Confirmed

| finding | what measured it |
|---|---|
| **"CAS failure ≠ displaced", and classify-on-failure fixes it** (specs F5, consumers F1) | Classify-on-failure gives 0/700 false displacements under both consumers' co-writer shapes, including the separate-handle write before `stopped` (T2). |
| **The variant needs no split, and every positional fold survives it** | Suite unchanged at 1037/1. No fold code touched. T5 shows 0 orphans, so the death-CAS still arbitrates. |
| **Specs F6 / consumers F2: revision 2 refutation 1 (the opt-out) is not answered** | A displaced A's **raw** values still land: 6 per job in S1, 40 in S3 under (a). Only `emit`'s cells are protected (T1: 4/7 → 0/7). |
| **Revision 2 refutation 2 / revision 3 refutations 1–2: the artifact plane is unprotected** | S1: 6 artifact writes in every mode. In multi-step shapes the bound is ≤1 body under (b)/(c) (T1). Under (a) and master it is unbounded. |
| **Consumers F4, the fix** ("only a foreign started displaces") | T3a/b: COMPLETED stands after a wrongful eviction. T3c (= E10): a claimant arriving after the eviction displaces A and its verdict stands. |
| **Consumers F5 / revision 2 refutation 4 (birth half): a mistaken or forged claim mutes the live worker** | T4a: a **new wedge path** on all 4 backends. The incumbent's completion no longer releases a phantom claim, and the claim gate refuses every relaunch. |
| **Consumers F7 / specs F13: both obvious surfacing shapes harm consumers** | Level style (b): truncated overwrite, checkpoint regression to A@3, false "[Preempt]". Raise style (c): the backstop forges ERRORED, but only when no `stopped` landed since dispatch (the mistaken cause; not the release cause, and not when B has already stopped). |
| **Revision 3 refutation 6: loud → silent** | A forged started now leaves zero terminal records (T4a). |
| **Cost claims** | Clean CAS ≈1.0–1.16× per write. The refusal path is now measured on Postgres (3.78× per write, +229 µs per tick in the mycooc shape), which neither red-team had. |

### Answered (by the variant, measured)

| finding | evidence |
|---|---|
| **Revision 3 refutation 3: a forged COMPLETED on every exit path** | Closed for the reference Worker on all exit paths tested: post-loop `stopped` (T1, T3c, T4b) and `retire` (T5d). |
| **Revision 2 refutation 4, death half** | Master's careful death forges `preempted` over B (T5d). The spike does not. |
| **Revision 2 refutation 3: the zombie** | `displaced` latches, `claimed` stays True, and the worker knows. |
| **Revision 3 refutation 4: broken invariants** | A separate flag; the suite is unchanged. |
| **Revision 3 refutation 7: per-tick read growth** | There is no per-tick read. The classify read is bounded to `(head, last]` and index-served on sqlite and Postgres. |
| **Specs F3 / consumers F8 in single-log form: answers to control facts** | On master, the displaced A **serves and naks B's control** (T5d dump: `value[b1]`, and `nak` of B's request). Fenced answers land nothing. |

### Refuted or moot

- The split-specific findings are moot for the variant: specs F1–F4 and F8–F11; consumers F3, F6,
  F9 and F12.
- Specs F7(b) wanted a reclaim `stopped` to fence a stranded-but-alive worker at once. Consumers F4
  wants the opposite. The two red-teams conflict here. The variant follows F4, and T3 shows it
  costs nothing relative to master.

### New, found by measurement

- **N1 — starvation (a liveness bug, not a frequency question).** The client-side fence is
  lock-free, not wait-free. A concurrent foreign writer whose inter-arrival time is shorter than
  the classify cycle starves the worker's write. On Postgres that cycle is CAS + `last_seq` + read
  + CAS (T7b: graceful to ~4k/s, collapses from ~8k/s, starves under a tight loop). Memory starves
  at modest rates because `MemoryChannel.read` is O(N). sqlite is immune. Revision 2's in-substrate
  fence was one statement and would not have this problem. Today's consumers write raw values on
  the worker's *own* thread, which cannot starve it (T2 max streak 4). But a DDP-rank or
  logging-thread co-writer could.
- **N2 — raise style is swallowed by `except Exception`** in `_drain_control` (T6x).
- **N3 — surfacing (a) never measured worse than master on any T6 axis.** It is strictly better
  on the log plane and identical on artifacts, rc, backstop and compute. (b) is the only style that
  *regresses* artifact content relative to master.

### Open

- **Consumers F10 / E8:** sqlite CAS on NFS with DELETE journalling. Not reproducible on tmpfs.
- **Consumers F11:** a second thread inside one Worker. The Worker is not thread-safe today;
  untested.
- **Unmeasured idea, Postgres:** halving the classify window with one unfiltered
  `read(after=head)`, which yields head and rivals in one statement.

## 5. Verdict: **adopt, with named conditions**

**For.** The variant closes issue #32's headline harms for every write the reference Worker makes,
on all four backends, with no change to the substrate, schemas, backends or folds. It is about 45
lines of code.

- The forged COMPLETED and preempted verdicts over a live successor are gone, along with the claim
  cascade.
- Heartbeat and progress pollution, `emit`'s cell splice, and a displaced service hijacking its
  successor's subscriptions are gone too.
- False positives: 0 in 700 concurrent runs that use the consumers' real co-writer shapes.
- Cost: invisible at the run level at realistic rates.

It answers revision 2's refutations 3, 5 and 4 (death half), and revision 3's refutations 3, 4
and 7, on their own terms.

**Conditions** (each is a named gap that the measurements show, not a matter of taste):

1. **Ship surfacing (a), not the spike's (b).** The fence should change what *lands*, not the
   worker's control flow. Expose `displaced` so that consumers can opt in to an early exit. That
   exit must be clean (rc 0) and must skip post-loop artifact writes; otherwise mycooc's backstop
   forges ERRORED (c), or a truncated or regressed artifact overwrites the successor's (b). If a
   raise is ever offered, it must not be an `Exception` subclass (N2).
2. **Bound the retry loop and state its exhaustion behaviour (N1).** The substrate precedent is
   `_SEND_RETRY_BOUND`: a bound, then a raise. Measure the bound under T7b. Until that is done,
   document the limit: a worker sharing its run with a sustained concurrent writer above ~4k
   records/s (Postgres) or on the memory backend can be starved.
3. **Pair it with a remedy for the T4a wedge.** The variant removes the incumbent's accidental
   release of a phantom foreign-host claim. The designated eliminator (`cross-host-claim-gate.md`
   §4.2, already "the live thread" in write-authority.md) or the reclaim recipe must be the stated
   way out, in the same change.
4. **Rewrite `docs/specs/write-authority.md` and `docs/api.md` as revision 5.** The guarantee
   becomes: "the reference Worker never lands a record above a rival claim." Revision 2's
   refutations 1 and 2, and the birth half of 4, still stand and must be said:
   - raw `channel.send` is unfenced, and 0 of the consumers' ~13 value-write sites go through the
     Worker;
   - artifacts are unprotected;
   - a forged claim mutes silently.

   The census bounds where this protection reaches today. It does not refute the gap.
5. **Keep the new tests, and add a contention test for condition 2.** No test exercised
   displacement before this spike.
