# Ground truth: time-triggered claims over the fenced worker

**Hypothesis under test.** Suppose (a) every Worker log write is fenced (the previous spike), and (b) checkpoints are
never overwritten: each episode writes under its own key, and resume picks a checkpoint the fenced log vouches for.
Then a mistaken claim can corrupt nothing and can only waste compute. If so, "time never arbitrates a claim" can
soften to "time may trigger a claim; nothing a mistaken claim does can corrupt."

- **Worktree:** `/home/gchurchill/src/runstate/.claude/worktrees/agent-a9b175d3c9b046b06`
- **Branch:** `spike/time-triggered-claims`, two local commits on `spike/fenced-worker-writes`@e545afc. It has not
  been pushed and there is no PR.
  - `62c0319` is the implementation.
  - `3009cd6` is the tests.
- **Harness:** `scratchpad/ttc/`.
  - `prev/` is a byte copy of `runstate/` from the previous spike.
  - `h.py` and `shapes.py` are shared code.
  - Each `e*.py` script has a matching `e*.out` file.
- **Postgres:** a throwaway server at `/tmp/rs-ttcs:55441`, stopped and deleted afterwards.
- **Environment caveats:**
  - sqlite ran on tmpfs, so NFS is not represented. mycooc deploys there.
  - Time is simulated: a fake clock in the deterministic runs, and 0.04 s steps against a 0.6 s threshold in the
    SIGSTOP runs.

## 1. The change

```
$ git diff --stat spike/fenced-worker-writes...spike/time-triggered-claims
 runstate/checkpoints.py             | 117 +++++++  (65 code lines)
 runstate/takeover.py                |  66 ++++   (30 code lines)
 runstate/worker.py                  |  37 +++-   (~14 code lines)
 tests/test_time_triggered_claims.py | 144 ++++++++  (6 tests x 4 backends)
 4 files changed, 362 insertions(+), 2 deletions(-)
```

Nothing changed in the substrate, the schemas, the backends or the observables.

**Worker.** The change adds a Strategy and an accessor; the claim CAS and its ordering are untouched.

- **`ClaimGate`:** a Protocol with `admits(channel) -> bool`. It is injected as `Worker(..., gate=)`, and the default
  `NoLiveEpisode` is today's `live_episode(ch) is None`. The gate is asked inside the claim loop after the head is
  read. It decides only whether to *attempt* the claim; the CAS at that head still arbitrates.
- **`Worker.episode`:** the claim seq.

**`takeover.StaleTakeover(stale_after, now)`** is a gate.

- It admits when no episode is live. It also admits when the live episode's last self-report is older than the
  threshold, **whether or not its handle resolves**. This is the deliberate breach of observer-clock §4.
- "Last self-report" is `episode_last_beat`, the maximum of `started.t` and the `t` of a heartbeat after it.
- It deliberately does **not** use `last_activity`. A launcher's `launcher.launched` record for the claimant itself
  is dated, so it would veto the claimant's own takeover. ThreadLauncher writes one.
- Junk clocks fail closed.

**`checkpoints`** is a recipe beside the Worker.

- **Writer:** `EpisodeCheckpoints(root, channel, episode).save(step, write)` reads the head `X`, writes to a temp
  file, and publishes atomically to `<root>/<episode>/<step>.<X>`. It never touches another episode's files.
- **Key = the claim seq, not the launch id.**
  - Every claim has a seq. A hand-run worker's claim carries no launch id; CLAUDE.md's census found 0 launch ids on
    785 of one consumer's claims.
  - The seq is unique per run (the CAS) and ordered.
  - It is the window watermark that every fold already uses, so a third party can derive it from the log.
- **Reader, `resume_point(channel, root)`: the rule as finally used (R\*).** A checkpoint `<E>/<s>.<X>` is
  *vouched* if a `lifecycle.heartbeat` lands in E's window (from E's claim to the next claim) at a seq greater than
  `X`. E's own `lifecycle.stopped` with `final_step >= s` also vouches.
- **Why that rule is sound:**
  - That record was appended after the publish, in program order.
  - It landed, so no rival claim existed when the checkpoint was written.
  - Under the fence, only E can land a heartbeat in E's window.
  - A third party's release names no `final_step`, so it vouches nothing (this is tested).
- **Choice:** resume takes the **latest episode with a vouched checkpoint**, then its highest vouched step.

**How R\* differs from the proposed rule (R0)**, and why. R0 was: "the highest s such that E has a landed heartbeat
or value at step s and `ckpt/E/s` exists."

1. **Values never vouch.** A raw `channel.send` value is unfenced and indistinguishable on the log from an `emit`.
   And a value precedes the save in program order.
2. **The voucher must land after the checkpoint was published,** not merely "at step s". R0 vouches a checkpoint
   written *after* displacement whenever the step's value or beat landed before it. This happens in tick-then-save
   shapes and whenever the freeze falls between the value and the save.
3. **Prefer the newest lineage over a higher step from an older episode.**

E2b measures R0 failing: a mismatched cell in the final series on 4/4 backends, including the fenced `emit` series.
R\* gives 0.

## 2. E1–E6

The deterministic runs use a fake clock. They were run on memory, sqlite-WAL, sqlite-DELETE and Postgres, and all
four backends gave **byte-identical results in every configuration** (checked per row). "Lineage mismatch" works as
follows:

- Training is made visibly non-deterministic: each step records which episode computed it.
- A cell is mismatched when the series value at step s was produced by a different lineage from the model the reader
  gets.
- Under deterministic training these cells would be equal in value. Under GPU non-determinism they are silent
  corruption.

| test | before | after | evidence |
|---|---|---|---|
| **E1** the wedge: a phantom foreign claim after A's tick(5); the phantom never runs | **prev spike:** A is fenced (0 records above the phantom) and exits. A fresh claim is refused at +10 s, +31 s and +3600 s, and `live_episode` = the phantom forever, on 4/4 backends. | Refused at +10 s (still fresh). **Claims at +31 s**. C resumes at step 6: A@5 is vouched, and A's post-displacement A@6 is skipped. C runs to 20: verdict **COMPLETED**, `live_episode` None, 20/20 cells, **0 lineage mismatches** for both `emit` and raw, on 4/4 backends. | `e1_wedge.py`, `e1_prev.out`, `e1_ttc.out` |
| E1 through `ensure` (the reuse path) | — | **Default producer** (`launch_producer` → `relaunch_if_needed` + `foreign_episode`, with StaleTakeover only in the worker): **`ensure` has not returned after 5 s, and never would**. Both deciders gate on `live_episode`, so nothing is spawned and the foreign handle stays "alive". **Gated producer** (the *same* StaleTakeover instance at the birth, the spawn decision and the foreign handle): returns in **0.61–0.64 s** with 20 points, on 4/4 backends. | `e1_ensure.py`, `e1_ensure.out` |
| **E2** DBOS B2's shape: A is SIGSTOPped at about step 7; B takes over by staleness and finishes 30; A is SIGCONTed; then a later `ensure(..., until={'step': 40})` | **prev spike as is:** no takeover is possible, so the run is blocked while A is frozen. If A were dead on another host, this is E1's wedge. **prev + takeover + in-place checkpoint:** the checkpoint **regressed 29 → 7 in 21/21 rounds** (7 per backend × WAL / DELETE / Postgres). The later `ensure` **recomputed 22 steps (8..29)** and returned a series with 1 mismatched cell. The deterministic matrix gives the same: regression to 6 in 4/36 configurations, 92 steps recomputed in total. | **Episode-keyed:** resume state 29 in 21/21 rounds; `ensure` resumed at 30; **0 steps recomputed, 0 mismatches**. Deterministic: 0/36 regressed. | `e23_sigstop.py`, `e23_sigstop_b.out`, `e23_false_death.py`, `e23.out` |
| E2, surfacing (a) (A keeps computing) | **in place:** the model ends at step 29 **but on A's lineage**, while `ensure` reads B's fenced `loss`, so **`ensure` returns 23 cells that do not describe the model** (9/9 rounds, and 9/9 again with E3's mid-run wake). | **Episode-keyed:** 0 (18/18). | `e23_sigstop_a.out` |
| **E2b** resume rule: A is frozen after its step-6 values; B takes over, emits step 6, and is killed before saving; A wakes and saves step 6 after displacement; D takes over | **in place:** D resumes at 7 from A's step-6 model. **1 mismatched cell** in `loss` *and* `m0`, and tagged reading cannot fix it. **R0 (as proposed):** identical. A's landed step-6 value vouches A's post-displacement checkpoint. | **R\*:** D resumes at 6, **0 mismatches**, on 4/4 backends. | `e2b_resume_rule.py`, `e2b.out` |
| **E3** false death: A is alive but frozen longer than the threshold. Freeze points: trained / valued / saved. Unfreeze: before B runs / B mid-run / B done. Surfacing (b) / (a). Raw sends unfenced / fenced. 36 configurations × 4 backends, plus SIGSTOP. | n/a in the previous spike: nothing can take over, so a false death costs nothing and recovering from a real one is impossible. **Takeover + in-place checkpoint:** resume state regressed in 4/36; 16/36 have a mismatched `emit` series, up to 24 cells; the model is on A's lineage in every surfacing-(a) run where A finishes after B. | **Takeover + episode-keyed, R\*:** 0 fenced records above B's claim in 36/36. **No verdict forged while B is live**, and the final verdict is B's, in 36/36. Resume state = 29 and the later resume starts at 30 in 36/36. **`emit` series: 0 mismatches in 36/36.** **Raw-send series: mismatches in 8/36** — 1 cell when A's frozen step lands after B passed it; 23–24 cells under (a). Positional episode keying gives the **same 8/36**. Tagged keying gives **0/36**; fenced raw sends give **0/18**. **Waste:** B redoes 1 step; A computes 0 extra steps under (b) and 22–23 under (a); A leaves 1 (b) or 23 (a) dead checkpoint files, never chosen. SIGSTOP agrees in all 60 episode-keyed rounds (42 under (b), 18 under (a)). | `e23.out`, `e23_sigstop_*.out` |
| E3, clock skew (threshold 30 s) | — | **Incumbent clock 40 s slow, worker healthy:** falsely taken over within 4.7 s on 4/4 backends. It costs 1 redone step and no corruption. **Incumbent clock 3600 s fast, worker dead:** the takeover fires **3630 s** after death. | `e3_skew.py`, `e3_skew.out` |
| **E4** racing takeovers | — | **8 threads × 200 trials:** exactly 1 winner in 200/200 on each of 4 backends. **6 OS processes × 30 trials:** 1 winner in 30/30 on each of WAL, DELETE and Postgres. **Incumbent wakes while a claimant races:** 800 trials, **0 violations**. B.claimed always equals A.displaced, and the latest claim is the owner. Splits: memory B-won 198 / A-kept 2; WAL A-kept 199 / B-won 1; DELETE A-kept 198 / B-won 2; Postgres A-kept 200. A beat that lands between the gate's read and the CAS refuses the claim; this is also pinned by a test. | `e4_race.py`, `e4.out` |
| E4, harness artefact (disclosed) | — | A first process run (a 2-step job) showed 2–6 "winners" on sqlite. `e4c_check.py` classified all 30 multi-winner trials as **sequential**: each claim followed the previous winner's `stopped`, which the default gate also allows. The run was redone with a live winner: 1/1. | `e4c_check.out` |
| **E5** consumer shapes under E3's false death; see §3 | — | §3 | `e5_consumers.py`, `e5.out` |
| **E5r** translation, routine false death: no freeze; a 10-unit × 10 s job beats only at its single tick; threshold 30 s; a claimant every 5 s | — | **Livelock.** 1000 s gave **29 episodes, 0 completions, 26 displaced exits, 276 unit-computations for a 10-unit job**, up to 4 workers running at once, and no vouched artefact. The same job ticking per unit (`steps(total=10)`) gave **0 takeovers and completed**. | `e5.out` |
| **E6** full suite | prev spike: 829 passed / 229 skipped; 1057 / 1 with the DSN | **847 passed / 235 skipped; 1081 passed / 1 skipped with the DSN.** The difference is exactly the 6 new tests × 4 backends. No existing test changed. `black` and `mypy --strict` are clean; the pre-commit gate passed on both commits. | — |

### E5 detail (mycooc and translation shapes)

**mycooc shape.** 20 raw sends per tick and no `emit`. A periodic save every 3 steps before the tick, then the
pattern `if w.tick(): "[Preempt]"; save(forced); break`. After the loop, a final metric through a **separate handle**,
a forced save, and `stopped`. 24 configurations × 4 backends, all identical:

- **Every configuration:**
  - A's separate-handle write lands in B's window (1/1).
  - A logs a **false "[Preempt] control.stop received"**, from surfacing (b) (24/24).
  - A makes 2 post-displacement saves.
  - The verdict is COMPLETED, B's.
- **In place:** the model regressed to 7 or 8 in 4/12. The raw series has up to 23 mismatched cells.
- **Episode-keyed:**
  - The model is 29 in 12/12.
  - The raw `m0` series has 1 mismatched cell in 4/12 under latest and positional reading, and 0 tagged.
  - `final_acc` has 1 mismatched cell (A's) in 12/12 under latest and positional reading, and 0 tagged.
  - **`latest('value', name='final_acc')` returns A's metric in every configuration where A wakes after B finishes
    (4/12).** This is how a consumer reads a final metric.

**translation shape.** One step: 10 units, each an artefact plus a raw *stepless* `hyp` value, then
`stopped(completed=True)`. A freezes after unit 4.

- **In place:** **5/10 units of the final artefact are A's** whenever A wakes after B has started (2/3 timings). That
  is a mixed output that no single execution produced.
- **Episode-keyed:** 0/10 units are A's, in 3/3 timings.
- **Every configuration:**
  - A's 5 remaining units are computed after the takeover: waste of half the job.
  - A's 5 `hyp` records land in B's window.
  - Tagged reading keeps 15 records: B's 10 plus A's legitimate 5 from before the takeover. The job's output still
    has to be scoped to the verdict's episode.

## 3. What a mistaken takeover costs, per plane

| plane | waste | corruption | evidence |
|---|---|---|---|
| **Fenced log** (heartbeat, `emit`, served values, nak, `stopped`) | none | **none.** 0 records above the successor's claim in every deterministic, SIGSTOP and consumer run | E1, E3, E5 |
| **Verdict** | none | **none.** Never forged while the successor is live; the final verdict is always the successor's | E1, E3, E5 |
| **Raw `channel.send` values, same handle** | records land and are never read correctly | **Yes, a splice.** The take-the-latest series gets A's values: 1 cell under (b), 23–24 under (a), in 8/36 configurations. **Positional episode keying does not fix it** (identical 8/36): A's records sit inside B's window. **Fixed by** tagging (the writer stamps its episode; the reader keeps a record only if its tag names its window's owner): 0/36. **Also fixed by** fencing the send: 0/18. | E3, E5m |
| **Raw values through a separate handle** (mycooc's final metric) | — | **Yes.** It lands in B's window in 24/24. `latest(name)` returns A's metric in 4/12. Tagging fixes the series. A fence would have to be keyed on (channel, episode), because a separate handle has no Worker. | E5m |
| **Raw stepless values** (translation `hyp`) | — | **Yes**, until scoped. A's 5 land in B's window. A reader needs a tag *and* the verdict's episode. | E5t |
| **Artefacts / checkpoints, in place** | — | **Yes.** Regression 29 → 7 with 22 steps recomputed (21/21). The model on A's lineage while `ensure` returns B's series (9/9). A mixed translation output (5/10). | E2, E5 |
| **Artefacts / checkpoints, episode-keyed, R\*** | 1 redone step at K=1 (A's unvouched step). In general: back to A's last vouched checkpoint. For a single-step job: the whole job. Plus dead files (1 under (b), 23 under (a)) that need GC. | **none**, in every configuration | E2, E3, E5 |
| **The same, read with R0** | — | **Yes**: 1 mismatched cell (E2b) | E2b |
| **Compute** | Detection latency (the threshold). Then A's post-takeover work: ≤ the rest of one body under (b), but **one body is the whole remaining job in translation's shape** (5/10 units); all remaining steps under (a) (22–23 of 30). | — | E3, E5t |
| **Liveness** (not corruption) | If any beat gap exceeds the threshold — a single-step job, startup between the claim and the first tick, a long save or eval — false deaths become **routine**, and a run with a persistent claimant **livelocks**: 0 completions in 1000 s, 27.6× the job's compute | — | E5r |
| **Clocks** | A slow incumbent clock causes false takeovers of healthy workers (waste). A fast one delays recovery by the skew (+3600 s). | — | E3-skew |
| **Logs** | — | A false "[Preempt] control.stop received" from surfacing (b): misleading, not data | E5m |

## 4. What consumers would have to change

1. **Inject one gate at three sites, not one.**
   - The Worker's birth.
   - The spawn decision: replace `relaunch_if_needed`/`ensure_served`'s `live_episode` check with `gate.admits`.
   - The foreign wait: `foreign_episode.is_alive = not gate.admits`.

   Otherwise `ensure` stays wedged on the reuse path even with a takeover-capable worker (E1-ensure).
2. **Episode-key every off-log artefact, and read it through the vouched rule.** That covers checkpoints, the final
   model and translation's `store.put` output. "Overwrite in place" anywhere reintroduces regression and mixing
   (E2, E5). Dead episode directories need a GC.
3. **Every value write must be fenced, or stamped with its episode and read tag-aware.** That includes raw
   `channel.send`, separate-handle writes and stepless records. Read-time positional keying is measured
   insufficient. The previous report found 0 of the consumers' ~13 value-write sites go through the Worker.
   - **The fence is the cleaner option.** It needs a public fenced send, and a fence keyed on (channel, episode) for
     separate handles.
   - **Tagging needs** a value-v0.3 field (or an envelope author) and tag-aware `value_series`, `history` and
     `latest`.
4. **Beat more often than the threshold in every phase.** That means ticking per unit in single-step jobs, and either
   a short claim-to-first-tick window or a threshold above startup time. A beat thread would make heartbeats mean
   "process alive" rather than "loop progressing", which reopens the wedge for hung-but-alive workers.
5. **On displacement, exit cleanly.** Skip post-loop writes and the false "[Preempt]" path. This is the previous
   report's condition 1; with item 2 those writes are harmless, but they are still waste.
6. **Clocks synced well inside the threshold, or a witnessed staleness instead.** The witnessed form is skew-immune,
   but each claimant must then observe for the full threshold before claiming.

## 5. Verdict: **holds with named conditions**

**What holds, measured.** The fence plus episode-keyed artefacts read through R\* make a mistaken takeover
**waste-only**:

- on the fenced log, the verdict and the artefact plane;
- on 4/4 backends;
- across 36 deterministic configurations, 60 episode-keyed SIGSTOP rounds and both consumer shapes.

What follows from that:

- **The cross-host wedge dissolves.** E1 recovers in threshold + run time.
- **The CAS still arbitrates.** Exactly 1 winner in 890 of 890 claimant races, and 0 violations in 800 races
  against a waking incumbent, whose beat vetoes the claim in flight.
- **DBOS B2's regression is gone.** 21/21 rounds regressed in place and 0/21 with episode keys.

**Where the hypothesis as stated fails:**

- **Raw value writes are unfenced,** whether same-handle, separate-handle or stepless. They splice the series a reader
  gets, and `latest(name)` returns the displaced worker's metric. Keying values by episode **at read time alone does
  not fix it**: the displaced writer's records are positionally inside the successor's window. It is fixed only at
  write time, by a fence or an episode tag.
- **The resume rule as proposed (R0) fails** (E2b). R\* holds.

So the rule can soften to "time may trigger a claim" only together with a contract on **every writer**: *each write
is either fenced on its claim, or keyed by its episode and read through the log's vouching.* The library can meet that
contract for its own writes and can ship the tools. It cannot enforce it on consumer code that calls
`channel.send` or writes files.

**The named conditions:**

1. Raw and separate-handle value writes are fenced or episode-tagged, and readers honour the tag.
2. All artefacts are episode-keyed and read with R\*.
3. Beat interval < threshold in every phase, or false deaths become routine and runs can livelock (E5r).
4. Clock skew is well under the threshold, or staleness is witnessed. Skew shifts the threshold, in both directions.
5. The gate is injected at the birth, the spawn decision and the foreign wait (E1-ensure).
6. A linearizable CAS. sqlite-on-NFS — mycooc's deployment — is untested here and stays out of contract. The takeover
   would turn a safe wedge there into a possible double claim, against which the CAS-based fence is no defence.

**Relative to the dead end's objection #1** (double-live poisons reuse): the planes under conditions 1–2 are no longer
poisoned at all, beyond G1's bounded take-the-latest. The raw-value plane is still corrupted silently under
non-deterministic training unless condition 1 holds.
