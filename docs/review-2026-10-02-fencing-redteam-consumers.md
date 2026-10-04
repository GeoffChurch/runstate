# Red team: the fencing lead, from real writers and real failure scenarios

Target: `fencing-lead.md` ("commands out of the worker's stream"). Angle: who actually writes a run's
log, from where, and what goes wrong. Sources: `runstate/*.py` at HEAD of
`docs/vs-shipped-and-prior-art`; consumer code in `GeoffChurch/mycooc`, `translation`, `runstate-tui`
(read only); real consumer logs (read-only `immutable=1` opens). Scratch code:
`scratchpad/fencing-consumers/{bench_cas,today_vs_lead,census,census2}.py`.

## Verdict

**The lead is dead as specified. Its mechanism survives, and works better without the split.**

- **The stream split is very likely dominated.** The split exists to stop "other parties' records"
  from making the worker's `expected_seq` stale. In the real consumer logs, other parties' records
  are 0.0032% of mycooc's records and 0% of translation's. The staleness that actually occurs comes
  from the **worker process's own raw `value` sends**. The split leaves those in the worker stream,
  so it removes the rare cause and keeps the common one (measured: 99.9–100% of Worker writes would
  fail the CAS). Meanwhile it breaks every rule that pairs records by position across streams,
  including the careful death's CAS. One axis is unmeasured, so this is "likely dominated", not
  "dominated" (see E9).
- **The mechanism survives with named fixes:** make every *Worker-mediated* append a CAS on the head
  the Worker last knew. On failure, read the tail and **classify** it. The Worker is displaced only if
  a *foreign `lifecycle.started`* appears. Otherwise it adopts the new head and retries. This works on
  today's single log with no stream split, and it is a real improvement on revision 3: detection
  happens inside the write, on every exit path, and the read it needs is bounded.
- It does **not** answer revision 2's refutation 1 in the field. No consumer ever calls
  `Worker.emit`. Every consumer `value` is a raw `channel.send`, as the library itself advises for
  stepless points. It does not answer refutations 2 or 4 either. Refutation 4 is inherent.

## Census: who writes a run's log

Every row is a writer found in shipped code or consumer code. "Stream" is what the lead's topic
partition forces on it.

| writer | where | topics | today | stream under the lead | needs the CAS? |
|---|---|---|---|---|---|
| the Worker | `worker.py` | started, heartbeat, stopped, emit/`_service` values | started/death CAS; the rest unconditional | worker | yes (the lead's floor) |
| the Worker, as an answerer | `worker.py` `_nak`, `_service` expiry | `lifecycle.nak`, `control.unsubscribe` | unconditional | unassigned by the lead; either choice breaks something (F8) | — |
| helper in the worker's process, **same handle** | mycooc `runstate_emit.py:52,69,101` (metrics, `status`, `completion_reason`); `main.py:310` config; `analyze_run.py:1394`; translation `workers.py:32,47,66,84,122`, `ignition/workers.py:155` | `value` | raw `send` (by design: "value emission must be unconditional"; worker.py `emit` docstring: stepless points "use raw `channel.send` deliberately") | worker | the lead requires it; **0 consumer sites comply** |
| helper in the worker's process, **separate handle** | mycooc `graph_adapter.record_provenance` (fresh `create_channel`, called from `main.py:1105` just before `w.stopped`) | `value` | raw | worker | yes; it has no access to the Worker's head |
| orchestrator, on the worker's behalf | mycooc `emit_completion_reason` from `_send_stop_and_wait` (worker hung but **alive**); Recipe-4 birth record `analyze_run.py:1523` (before the claim) | `value` | raw | worker by topic, or control by writer: the lead is ambiguous | — |
| orchestrator crash backstop | mycooc `_SyncHandle` (`run_experiment.py:675`) | `lifecycle.stopped` | **raw** | worker (`live_episode`/`peek_terminal` read it there) | yes |
| staged-stop discharger | mycooc `resume_fanout` (`run_experiment.py:3957`), on never-started runs | `lifecycle.stopped` | raw | worker | yes |
| repair tool | mycooc `scripts/repair_malformed_stopped.py:53` | `lifecycle.stopped` | raw | worker | yes |
| reclaim (evictor) | mycooc `scripts/reclaim_experiment.py:326` | `lifecycle.stopped` | **CAS** (`claim_seq = ch.last_seq()`) | the lead says control ("third-party reports"); `live_episode` needs worker (F3) | yes |
| launcher | `launcher.py` | `launcher.*` | raw | control | no |
| operator / driver / Watcher | mycooc `stop_fanout`, `_send_stop_and_wait`; `Watcher.broadcast` | `control.*` | raw | control | no, except against `retire` (F6) |
| rival claimant / forger | `Worker.__init__`, hand-written | started | CAS / raw | worker | yes |

**Measured on the real logs** (`census.py`, `census2.py`; files, not paths):

- **mycooc, 1,224 run logs.**
  - Record counts: `value` 4,315,563 · heartbeat 207,116 · stopped 1,731 · started 1,723 ·
    `control.*` **145** (0.0032%, in 114 runs) · `launcher.*` 0.
  - **100.0%** of the 208,802 Worker-mediated writes inside an episode follow a non-Worker record
    (about 20 raw values per tick).
  - 45 of the 1,731 `stopped` records land where no episode is open: third-party or duplicate
    terminals.
- **translation, 1,221 run logs.**
  - Record counts: `value` 1,010,405 = heartbeat 1,010,405 · `control.*` **0** · `launcher.*` 2,285.
  - **99.9%** of Worker-mediated writes follow a raw value.

This census bounds *applicability*, which is a structural fact about how these consumers write. It
does not bound how often anything goes wrong. It does not refute anything on frequency grounds.

## Findings

| # | flaw | breaking case | severity | inherent or fixable |
|---|---|---|---|---|
| F1 | **"Head moved" ≠ "displaced."** The worker stream (partitioned by topic) has at least five writer classes, so the CAS failing does not mean a rival claimed. | translation R1: `channel.send(value)` → `tick` → heartbeat `CAS(head_A)` → `None`. Under the literal lead the worker stops itself at step 0. The same happens on 100% (mycooc) and 99.9% (translation) of Worker writes in the real logs; the synthetic bench misfires on 2000/2000 steps. mycooc additionally: `record_provenance` (separate handle) runs just before `w.stopped(completed=…)`, so **every run's completion verdict is lost**. | **fatal** as stated | fixable: classify on CAS failure (read the tail after the tracked head; displaced iff a foreign `started` appears; else adopt the head and retry). Measured 1.35–1.47× per step on sqlite, a µs-scale cost. This fix concedes F2. |
| F2 | **Revision 2's refutation 1 ("a floor with an opt-out") is not answered.** "Every worker-stream write goes through the library's CAS" is false in the field. | 0 calls to `Worker.emit`/`set` across mycooc and translation; about 11 raw `value` send sites. After F1's fix, a displaced A's raw values still land in B's window and win cells under take-the-latest. The splice that `history`/`ensure` return is unchanged for these consumers. | major | partly fixable: a CAS-ing register API (`emit` cannot take `step=None`; mycooc needs `status`/`config`/`completion_reason`/provenance registers) plus migrating both consumers. The public unconditional `send` remains an opt-out; that is inherent to an opinion-free substrate. |
| F3 | **The lead routes third-party reports to the control stream, but `live_episode` reads `stopped` from the worker stream.** | The reclaim tool's `stopped` lands in the control stream, and `live_episode` never sees it. **Stranded foreign-host claims can never be released.** That is the wedge that cost about 20 GPU-hours before `reclaim_experiment.py` existed. The alternative, folds reading `stopped` from both streams, needs a cross-stream order that does not exist. | **fatal** as stated | fixable by *aim*: control-stream eviction or verdict records carry the `claim_seq` they speak about, a reference by value rather than by position. This means absorbing `episode-aim.md` plus `claim-eviction.md`. |
| F4 | **A wrongful eviction of a live, silent worker becomes irrevocable** if evictions go on the worker stream and any foreign record counts as displacement. | E4 (run): A is inside a long single step; the reclaim CAS lands (A wrote nothing in the window); A finishes; `w.stopped(completed=True)`. **Today: COMPLETED** (A's later stopped wins). **Lead: CAS `None`, verdict PREEMPTED**, then a re-run: hours of GPU, and artifacts overwritten by the re-run. This is reachable: the reclaim correlator is a non-injective rectangle (9 overlaps, up to 3.75 h, `cross-host-claim-gate.md` §8.1), and translation and `analyze_run` write nothing for the whole job. | major | fixable: only a foreign **`started`** displaces. Evictions go on the control stream with aim, or, on a single log, the classifier ignores a foreign `stopped`; the evictee's own terminal for claim X then supersedes an eviction of X. |
| F5 | **A forged or mistaken claim whose handle cannot be resolved strands a healthy run.** | E5 (run): `started(handle=local://other-host/…)` lands mid-run. **Today:** A finishes and its stopped releases the phantom → COMPLETED, not live. **Lead:** A is muted, writes no terminal, `peek_terminal` returns None, and `live_episode` reads the phantom as live **forever**: the SLURM wedge. A realistic path: a mistaken claimant C that is then walltime-killed, which mycooc's own docstring calls "the normal ending". When the claimant is real and runs, the lead is **better** than today (no A/C double-live, no cascade). | major | **inherent** to any fence; this is revision 2's refutation 4, which the lead concedes. Mitigation: the muted worker appends a control-stream `displaced` report naming the claim that displaced it. That is attributable, and it is evidence a reclaim can use. |
| F6 | **`retire()`'s death-CAS cannot see a racing subscribe.** The race is with the control stream; the CAS is on the worker stream. | A service drains control up to c and sees no subs. A client's `subscribe` lands at control c+1. The client's `ensure_served` reads `live_episode` as live, so it does not wake anyone. W's stopped is `CAS(worker head)` → **succeeds** → the subscribe is orphaned. `service-worker.md`: "Episodes are CAS-claimed at both ends; the log cannot lose a message in either gap" becomes false. This is `per-episode-loglets.md`'s rule ("a CAS arbitrates only writers who share a frontier") applied to the **death**, not the birth. | major | fixable at cost: a two-phase death (CAS a retire marker on the control stream, then append to the worker stream, and readers honour the marker), or no split. |
| F7 | **The displacement signal has to reach consumer control flow, and both obvious shapes do harm.** | **Return style** (`tick` → True, like `_lost`): mycooc `training.py:1369` logs "[Preempt] control.stop received" and calls `_maybe_checkpoint(force=True)`, then `main.py:938` forces a second checkpoint into the shared `output_dir`; translation R5's post-loop `store.put` writes a **truncated** `per_sentence` over B's. These are revision 3's refutation 5, reproduced. **Raise style:** the child exits non-zero, and mycooc's `_SyncHandle` backstop (`returncode≠0 ∧ no stopped since since_seq`) appends a **raw `errored` stopped on top of B**, which forges ERRORED and releases B's claim (the cascade, now driven by the orchestrator). | major | fixable: a distinct `Displaced` exception, plus consumer migration (the backstop must aim at its own child's claim). The lead picks neither shape. |
| F8 | **The Worker is a writer on both sides of the split.** Naks and expiry `unsubscribe`s answer control records. | On the worker stream: the answer fold (`_answers`, `live_demand`, "an answer never reaches a later same-id subscribe") is cross-stream, so a re-subscribe after a nak is either answered forever or never. On the control stream: they are unfenced, and a displaced A's time-lease expiry `unsubscribe` (A registered earlier, so it expires earlier) pops B's live registration. `_drain_control` runs **before** the tick's first worker-stream write. | minor | fixable: keep answers on the control stream, and put the CAS'd heartbeat before the drain. That delays the preempt ack (`consumed_seq`) by one tick, and mycooc's `await_consumed` stop path depends on that ack. |
| F9 | **Positional folds ported naively across streams.** | E3 (run): A stop sent **after** the last `stopped` reads as discharged (`ctl#1` < `wrk#40`). Worker streams dwarf control streams (mycooc ≈ 30,000:1), so this happens to essentially every stop on any run with history: **born discharged**, and mycooc's `next_claimable`/`stop_fanout` gate silently stops gating. Also affected: `boundary_voided` (time leases resurrected or voided at random); the `_launcher_terminal` window (dropping it re-opens whole-history poisoning); `await_consumed`'s refused-by-death (`record.seq > seq`); runstate-tui `fold.py:179` `s.seq > episode_seq` (the cockpit hides every pending stop); mycooc `_terminal_since` and `read(after=since_seq)`. | **fatal** if ported naively | fixable: cross-stream watermarks carried **by value**. `Heartbeat.consumed_seq` already is one; `started`/`stopped` would need a control watermark and answers the seq they answer. Each is a convention version bump. (This is the specs adversary's terrain; listed for the consumer sites.) |
| F10 | **On mycooc's deployment, sqlite on NFS with DELETE journalling, the CAS itself is documented as unreliable cross-host.** | The displacements in #32 come from mycooc's SLURM reclaim, so the fence gives no guarantee exactly where the motivating harm lives. | minor in scope (out of contract today) | inherent to the backend. Empirical: E8. |
| F11 | **Two writers inside one worker, and indeterminate CASes.** | A heartbeat thread (the natural fix for translation's hour-long silent step) racing the main thread's `emit`. Or Postgres commits A's heartbeat and the connection drops before the ack; the retry `CAS(H)` returns `None` against A's own record. Both are misread as displacement under the literal lead. | minor | fixable: a per-Worker lock around (head, CAS, update), plus F1's classifier, which treats A's own heartbeat as not-a-`started`. The Worker is not thread-safe today in any case. |
| F12 | **A run becomes two substrate logs.** "No new substrate op" holds only that way. | `RunNotFound` ("a run is its records"): mycooc `stop_fanout` stages a stop on a never-started run, leaving an empty worker stream. GC has to remove two files. Every consumer handle (`open_cell_channel`, `attach_channel(rid)`, `RUNSTATE_RUN_ID`) becomes a pair. Migration means **renumbering seq in every log** and rewriting each historical `consumed_seq`. Postgres lock keys hash `(run_id, started_seq)`, so they change. | major (cost, not soundness) | fixable. Expensive enough that, under the global no-legacy rule, it should be priced and put to the owner before any build. |

### Readers (angle 5), calibrated

Torn reads are **not new**. Today's folds already make several non-atomic reads (`latest_episode`,
then a windowed read), so a verdict can already combine two moments. Two things are new:

- **(a) F9's cross-stream positional rules.** These are wrong at every moment, not just torn ones.
- **(b) The loss of the consistent cut.** Today a reader *can* cap all reads at one `last_seq()`, as
  the Worker's HEAD-FIRST attach does. Across two streams, no read establishes which control records
  preceded which worker records.

What that loses in practice is forensics. runstate-tui's log view (`detail.py`, one cursor) and
`Watcher.iter_events` lose the one interleaving an operator uses to answer "did my stop land before
the worker's last beat?". After the split, only `consumed_seq` and wall clocks answer it, and
`observer-clock.md` says time never arbitrates.

## Scenario walk-through (angle 3): what each shape gains

| shape | log plane under the lead (with F1's fix) | artifact plane | loud ↔ silent |
|---|---|---|---|
| #32 / a displaced, honest worker using the Worker loop | **gains.** A's heartbeats stop landing, so `progress` and tier-4 liveness stop being driven by A (the harm `observables.progress` names). A's `stopped(completed)` fails its CAS: **no forged COMPLETED and no claim cascade, on every exit path.** This answers revision 3's refutation 3. | unchanged | **Silent → loud** for A (it gets `None`). **Loud → silent** for the log: A's interleaving disappears while A's artifacts remain (revision 2's refutation 2). |
| translation R2/R3/ignition (`steps(total=1)`, the whole job inside the body) | the verdict is fixed. The body's raw value still lands. | **unchanged**: every `store.put` happens before the first worker-stream write | as above |
| translation R5 (post-loop `store.put`) | the verdict is fixed | **return style: worse** (a truncated overwrite). **Raise style: better** (no put). | depends on F7 |
| mycooc training (raw values, then tick) | heartbeat and verdict are fixed; raw `status`/metrics still land | return style: a forced checkpoint with a false "[Preempt]". Raise style: none, but the `_SyncHandle` backstop forges ERRORED on B. | backstop path: silent COMPLETED → loud ERRORED |
| wrongful eviction of a live, silent worker | **worse** (F4): the true completion is lost | a re-run overwrites | silent self-heal → silent waste |
| forged or mistaken claim, unresolvable handle | **worse** (F5): stranded | — | completes today → wedged |
| mistaken claimant that actually runs | **better**: no double-live, no cascade | A's writes stop at A's next tick instead of continuing to its end | — |

## What the lead gets right

- **Detection inside the write is the right shape.** On the protected path, the bad record *does
  not land*, which revision 3 could not do ("the bad write lands in the same tick, before any check
  fires"). There is no exit path that skips detection: `stopped()` and `retire()` both write through
  it, which answers revision 3's refutation 3. There is no per-tick read in the clean case, and the
  tail read after a failure is bounded, which answers refutation 7.
- **It closes the honest-worker cascade** (episode-aim's headline defect) by prevention instead of
  read-side attribution. It does so for every Worker-mediated record, `stopped` and heartbeat alike.
- **The zero-cost claim holds.** Measured clean CAS: memory 1.00×, sqlite/WAL 1.12×, sqlite/DELETE
  1.07×. With the classifier firing every step (the real consumer shape): 1.35–1.47×. All of this
  is µs per step against steps of seconds. On Postgres the CAS is the same statement shape as the
  unconditional send. Cost was never the objection, as in revision 2.
- **The substrate stays untouched**, and its relationship to `per-episode-loglets.md` is right *for
  the birth*: every claimant shares one frontier.
- **The reclaim CAS becomes more precise.** With control traffic removed, only worker activity
  refuses it.

## The single-log variant (the salvage)

Keep one log. Make every Worker append a CAS on the head the Worker last knew. On `None`, read
`after=head` and check for a `lifecycle.started` with a seq above the Worker's own claim. If there is
one, the Worker is displaced: raise `Displaced`, with a separate `_displaced` flag so that `claimed`,
`stop_pending` and `_lost`'s ordering invariant keep their contracts. If there is not, adopt the
head and retry. `retire()` keeps its read-based `expected_seq` discipline.

This keeps F1's fix, F4's fix (a foreign `stopped` is not displacement) and every positional fold.
It needs no migration (F12) and has no F6, F8 or F9. It still inherits F2, F5, F7, F10 and F11, and
the artifact plane. Its cost in the consumer shapes equals the measured `lead_mixed`, because that
bench *is* this variant: one channel, with a raw co-writer.

The only axis on which the split might win is CAS-retry rate under heavy control traffic, which is
unmeasured (E9). So the split is **not yet shown dominated, but is the burden-bearer**.

## Experiments

**Run** (scratch, against `MemoryChannel` and `SqliteChannel`):

- **E1** `bench_cas.py`:
  - Literal-lead false displacement: **2000/2000** steps when a raw value send precedes each tick.
  - Cost:

    | backend | clean CAS | classifier firing every step |
    |---|---|---|
    | memory | 1.00× | 6.2× (`MemoryChannel.read` is a list scan) |
    | sqlite/WAL | 1.12× | 1.47× |
    | sqlite/DELETE | 1.07× | 1.35× |

- **E-census** `census.py`, `census2.py`: the numbers above (mycooc 100.0%, translation 99.9%;
  control share 0.0032% and 0%).
- **E3** `today_vs_lead.py`: born-discharged stop under naive cross-stream discharge
  (`pending=0`).
- **E4**: wrongful eviction of a live, silent worker. Today COMPLETED; lead PREEMPTED, with A's
  completion CAS returning `None`.
- **E5**: forged claim with a foreign handle. Today COMPLETED and not live; lead verdict None and
  `live_episode` returns the phantom.

**To run before any build:**

- **E6, the retire orphan.** Two `MemoryChannel`s (worker, control) and a `serve()` worker. Inject a
  `control.subscribe` between `retire()`'s drain and its death append. Assert:
  `live_demand(control) != []`, `live_episode(worker) is None`, and no further worker start.
  Then repeat with the two-phase death and assert the orphan closes.
- **E7, F7's consumer interaction.** Displace A mid-run with B. Then:
  - (a) Return style: count fake-store puts after displacement and assert whether R5's post-loop
    put writes fewer than N records.
  - (b) Raise style: drive mycooc's `_SyncHandle(rc=1, since_seq=pre-dispatch head)` and assert that
    its stopped lands above B's `started` and that `peek_terminal` returns ERRORED with
    `live_episode` None while B runs.
  - (c) Repeat (b) with the backstop aimed at its child's claim.
- **E8, NFS (empirical, needs the cluster).** Use sqlite with `RUNSTATE_SQLITE_JOURNAL_MODE=DELETE`
  on mycooc's NFS mount.
  - Host 1 loops `send(heartbeat, expected_seq=tracked_head)` every 100 ms. Host 2 makes one
    `send(started, expected_seq=last_seq())`.
  - Assert host 1's next CAS returns `None` within one period. Assert no seq is duplicated, and that
    `PRAGMA integrity_check` returns `ok`.
  - Run 1000 trials. Report dual-winner, missed-fence and corruption counts.
- **E9, the axis that could save the split.** A subscribe-heavy `serve()` worker: k subscribes per
  second from m clients, with k·m from 1 to 1000. Measure Worker CAS failures and classifier reads
  per tick, single log against split. If single-log retries stay sub-ms at realistic k·m, the split
  is dominated and should be deleted (and recorded as a dead end with these numbers).
- **E10, the F4 fix.** In the single-log variant, with the classifier ignoring a foreign `stopped`,
  replay E4 and assert COMPLETED. Then add a claimant C after the eviction and assert A raises
  `Displaced` at its next write and that C's verdict stands.
