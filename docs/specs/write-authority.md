# Spec: single-writer holds at the claiming instant only

**Status:** SETTLED, **revision 4** — as a *scope statement*, not a mechanism. Three revisions
proposed three mechanisms to extend write authority past the claim; all three are refuted below,
each by evidence already in this repo. The conclusion is the exit revision 3 wrote for itself:
say what the CAS actually guarantees, in `docs/api.md`, and stop reconstructing the rest.

Renamed from `backend-capability-tiers.md`, which stopped describing this document at revision 2.

## The diagnosis (unchanged across four revisions)

Issue #32, in its own words: *"The CAS guarantees at most one claimant **at the instant of
claiming**; nothing extends that to write authority over time."*

Safety is *one writer over time*. The claim CAS delivers only its first instant. Eleven refuted
fixes — the eliminator's authority question, the epoch-carrier debate, the staleness tier, the
handle-scheme question, and the three below — are attempts to reconstruct a guarantee that stops one
instant after the claim. The diagnosis is correct and is **not** the thing in dispute; every
revision agreed on it and differed only on the remedy.

## Revision 1: promote the advisory lock to a claim arbiter — REFUTED

`channel-postgres.md` forbids it in the spec for the backend revision 1 relied on: *"**Pushing
liveness into the claim path is the one thing that breaks this layering**."* And "definitive, never
unknown" is unachievable — a worker inside a long step is reaped by `idle_session_timeout` and reads
dead, a *false death verdict on a healthy run*.

## Revision 2: epoch-fenced append in the required tier — REFUTED, five ways

The cost claims held (sqlite 1.008×, postgres 1.044×, memory free, all re-measured). Cost was never
the objection.

1. **A floor with an opt-out is not a floor.** A writer omitting `epoch=` reproduces #32
   byte-for-byte. The "the CAS is equally opt-in" defence fails: the CAS has **two** sites, both
   inside the library, on writes whose entire purpose is arbitration — partial CAS is coherent
   because the sites that use it are the sites that *have a contest*.
2. **It guards the log, not the run's outputs.** Measured: with the fence, the log says B owns the
   run and A contributed nothing, while the **checkpoint on disk reads `A@step5`**. The fence makes
   the log *disagree with the artifact*, where today it at least records the interleaving.
3. **The residue is a zombie.** A's writes are silently dropped but `A.claimed=True`,
   `A._lost=False` — a worker that still believes it is authoritative.
4. **The birth CAS cannot be fenced, and it is the write that moves the fence.** Measured *new*
   harm: a forged claim **permanently and silently mutes the live worker**. And `retire()`'s
   death-CAS is mutually exclusive with a fence, so the blessed careful-death path still forges
   `preempted` through the library's own API.
5. **It breaks opinion-freeness and fails L1.** The fence makes the substrate route on
   `topic == 'lifecycle.started'`; `channel-postgres.md`: *"convention knowledge … stays in the
   worker, never the substrate."* And `protocol-algebra.md` L1: *"`send` / `read` / `latest` / CAS
   is **complete**."*

## Revision 3: detect displacement in the Worker — REFUTED

**It was already here, already named, and already refuted.**
`dead_ends/failure-detector.md` calls this mechanism the **"per-tick supersession self-check"** and
records it REFUTED 2026-06-24 by a four-angle red-team. It was refuted there as the *bound* on
observe-then-claim rather than as a standalone remedy, so the reuse is not automatic — but two of
its six points transfer verbatim, and both are the load-bearing ones:

> The supersession check bounds *process* liveness but not *log corruption* — the bad write lands in
> the same tick, before any check fires.

> The per-tick supersession check imposes a mandatory liveness burden on every worker (against "a
> worker composes its own loop").

Measured independently against a five-line patch (folded into `_drain_control`'s read as
`topics=["control.>", "lifecycle.started"]`, dispatched by topic; `mypy --strict` clean, suite
unchanged at 1019/1 — which itself means *no existing test exercises displacement at all*):

1. **It stops the worker one step body too late, and the checkpoint is inside that body.**
   `steps()` yields → the body runs (compute, `emit`, checkpoint) → *then* ticks. One checkpoint
   write always lands after displacement. The claim that it "also stops it writing the checkpoint
   the fence could never protect" was **false**. The bound is exactly one step body: 5 → 1 bodies,
   16 → 1 records, checkpoint `A@7` → `A@3`; on real Postgres, A survived 2.48 s → 0.17 s.
   That is a real improvement in the log plane, and it is not the plane the harm lives on.
2. **The unprotected shape is the majority shape.** `translation` R2/R3, `translation/ignition`, and
   `mycooc/analyze_run.py` are all `for _ in w.steps(total=1)` with the **entire job** — minutes to
   hours, every `store.put` — inside the one body. Measured: rev3 changes *nothing* about their
   artifacts. This is refutation 2 of revision 2, now true of revision 3.
3. **The forged verdict survives on both documented exit paths.** `retire()` and a post-loop
   `stopped()` gate on `_lost` but never *detect*; detection lives only in `tick()`. So the
   minimal-example idiom — `w.stopped(completed=True)` after the loop, used by every `translation`
   worker and `mycooc/main.py` — still forges **COMPLETED**, which is #32's headline harm. Rev3
   catches it in #32's script only because that script happens to `tick` on the line before.
   `retire()`'s tail read is *unfiltered*: the displacing `lifecycle.started` is literally in the
   list it reads, and is discarded because the loop routes only on `topic.startswith("control.")`.
4. **It breaks a named, off-repo-depended-on invariant, unpriced.** `worker.py`: *"ORDER IS
   LOAD-BEARING, and a consumer depends on it off-repo … a loser has no claim of its own on the
   log."* Rev3 adds a second silent-exit path that **does** leave a claim. Reusing `_lost` also
   falsifies `claimed`'s docstring ("True if this worker won the episode claim") and destroys the
   `tick` / `stop_pending` disambiguator, which both docstrings name as the way to tell a lost claim
   from a commanded stop.
5. **It creates two measured new harms in consumer code.** `mycooc/training.py`'s
   `if self._worker.tick(step):` branch logs `"[Preempt] control.stop received"` and calls
   `_maybe_checkpoint(force=True)` — rev3 *causes* a forced write into the shared `output_dir` with
   a false diagnosis, reopening the artifact race `mycooc/tests/test_claim_guard.py` exists to
   prevent, *inside* the guard. And `translation` R5's `store.put` sits after the loop, so rev3
   turns a complete 10-record overwrite into a silent 3-record one.
6. **It turns a loud kill into a silent one.** Forging `lifecycle.started` is not new power — any
   appender could already stop any worker with `control.stop`. The delta is the trace: `control.stop`
   leaves a `lifecycle.stopped` and a `preempted` verdict; a forged `started` under rev3 leaves
   **zero** terminal records, `claimed=False`, and reachable only through the staleness window.
7. **Unbounded read growth.** `_cursor` must not advance past lifecycle records (correctly — it is
   published as `consumed_seq`, frozen by `design-v0.2.md` §6/§12.6, and the suite catches the naive
   variant with 8 failures). So every tick re-transfers every `lifecycle.started` forever:
   1.00× at 1 episode, 1.48× at 100, **1.84× at 200**, on a run with no control traffic at all —
   exactly the memoizer / autonomous-extend shape the episode model exists for.

One point genuinely in revision 3's favour is also already upstream, in the same dead-end doc's
*What survives*: the only shared liveness facts are records an actor wrote by **acting** —
`stopped` / `terminated` / **new `started`**. Reading a rival's `started` is the right *kind* of
signal. It is the mechanism built on it that does not pay.

## The resolution

**Write it down instead of reconstructing it.** `docs/api.md` states that runstate provides
single-writer **at the claiming instant only**, and that a displaced worker's later writes are
honest records of what it did, not assertions of authority. One honest line beats a floor with an
opt-out, a fence the birth CAS can move, and a detector that fires after the write.

This is a *scope* answer to the question three revisions asked as a *mechanism* question — including
the one the investigation opened with, "should more move to the backend?" The answer is that the
boundary is in the right place and the documented guarantee was in the wrong one.

**And remove the precondition.** The displacements in #32 are all manufactured by a third party
having to hand-write a `lifecycle.stopped` — asserting a verdict, a frontier, a discharge and a
freshness date it cannot know — because releasing a claim is the only one of the five it actually
means. The designated eliminator (below) makes that release legal and stops the forgery at its
source. It does not extend write authority and does not claim to.

**If displacement detection is ever revisited**, the five lines are not the shape. It needs a
separate `_displaced` flag (so `claimed` / `stop_pending` / `__exit__` / `retire` keep their
contracts and callers can tell the two apart), the same check in `retire()` and `stopped()`, and a
second cursor so the read does not grow with episode count. That is no longer five lines, and it
still would not reach the artifact plane for the single-step workers that dominate the consumers.

## Still standing, independent of all of the above

- **The designated eliminator** (`cross-host-claim-gate.md` §4.2, undated per #42), with **aim +
  `expected_seq`** per §8.2. Fixes #39 with zero change to the discharge fold, fixes #42, and removes
  the forced forgery above. Already the live thread, and the owner's recorded direction on #32.
- **Declaration must be real.** "Tiers" live in a dict in `tests/conftest.py` keyed on fixture
  parameter names; nothing under `runstate/` knows about capabilities, so a third-party backend
  cannot declare one. This stands on its own — and is the only surviving fragment of this file's
  original title.
- **The `(topic, name, seq)` index (#19).** `name` is a post-filter today; the miss path measures
  ~3000× better with the index, and `SELECT DISTINCT name` becomes a covering scan.

## Note on baselines — resolved

Two reviews measured 1020/1 and 980/3 against nominally the same tree. **It was a commit
difference, not an environment one**: the suite grew 974 → 982 → 986 → 996 → 1010 → 1020 that week,
and every ~980 figure is a pre-#35 tree. Both reviewers had `RUNSTATE_TEST_PG_DSN` set (without it
the skip count is 221, not 1 or 3); the 2 extra skips are most likely `test_concurrency.py`'s
`cross_process` racer skipping where `fork` is unavailable — i.e. a macOS run. Anyone quoting a
suite number should still say which commit and whether the DSN was set.
