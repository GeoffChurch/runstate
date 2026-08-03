# Spec: write authority belongs to the Worker, not the substrate

**Status:** PROPOSED. **Revision 3.** Revisions 1 and 2 both proposed moving something *into* the
substrate; both are refuted below. The diagnosis survived all three revisions and the remedy got
smaller each time — it is now about five lines, in the library.

## The diagnosis (unchanged across three revisions)

Issue #32, in its own words: *"The CAS guarantees at most one claimant **at the instant of
claiming**; nothing extends that to write authority over time."*

Safety is *one writer over time*. The claim CAS delivers only its first instant. Eight refuted
fixes — the eliminator's authority question, the epoch-carrier debate, the staleness tier, the
handle-scheme question — are attempts to reconstruct a guarantee that stops one instant after the
claim.

## Revision 1: promote the advisory lock to a claim arbiter — REFUTED

`channel-postgres.md` forbids it in the spec for the backend revision 1 relied on: *"**Pushing
liveness into the claim path is the one thing that breaks this layering**."* And "definitive, never
unknown" is unachievable — a worker inside a long step is reaped by `idle_session_timeout` and reads
dead, a *false death verdict on a healthy run*.

## Revision 2: epoch-fenced append in the required tier — REFUTED, five ways

The cost claims held (sqlite 1.008×, postgres 1.044×, memory free, all re-measured). Cost was never
the objection.

1. **A floor with an opt-out is not a floor.** A writer omitting `epoch=` reproduces #32
   byte-for-byte. Revision 2 said "nothing here is opt-in" and, three lines later, "a third-party
   writer simply omits `epoch=`." The "the CAS is equally opt-in" defence fails: the CAS has **two**
   sites, both inside the library, on writes whose entire purpose is arbitration — partial CAS is
   coherent because the sites that use it are the sites that *have a contest*.
2. **It guards the log, not the run's outputs.** Measured: with the fence, the log says B owns the
   run and A contributed nothing, while the **checkpoint on disk reads `A@step5`**. The fence makes
   the log *disagree with the artifact*, where today it at least records the interleaving.
3. **The residue is a zombie.** A's writes are silently dropped but `A.claimed=True`,
   `A._lost=False`. Not "a wasted spawn" — a worker that still believes it is authoritative.
4. **The birth CAS cannot be fenced, and it is the write that moves the fence.** Any actor that can
   append can land a `lifecycle.started` and seize the epoch. Measured *new* harm: a forged claim
   **permanently and silently mutes the live worker** — it ran seven more steps and contributed zero
   records, with a ghost holding the claim and no eliminator possible. Today that forgery is
   survivable. And `retire()`'s death-CAS is mutually exclusive with a fence, so the blessed
   careful-death path still forges `preempted` through the library's own API.
5. **It breaks opinion-freeness and fails L1.** The fence makes the substrate route on
   `topic == 'lifecycle.started'`; `channel-postgres.md` records that *"convention knowledge … stays
   in the worker, never the substrate."* And `protocol-algebra.md` L1: *"`send` / `read` / `latest` /
   CAS is **complete**; anything else proposed for the Channel surface gets reclassified or rejected
   by this rule"* — a new atomic transition *"must justify itself the way the CAS did, contract and
   conformance tests included."* Revision 2 was 148 lines of prose.

Adjacent prior art, worth reading before proposing this again: `stop-discharge.md` records
**"A2 — episode-start fencing … the first-proposed fix — REFUTED"**. That refutation targets the
*stop-discharge* problem rather than write authority, and its closing line — fencing answers *"who
may act"* — is arguably a point in the fence's favour for *this* problem. It is cited here as
context, not as the refutation.

## Revision 3: detect displacement in the Worker

The fence tried to stop a displaced worker's *writes*. The cheaper and more complete move is to stop
the *worker*.

`Worker._lost` is assigned in exactly one place — inside the attach loop — so a worker displaced
**after** claiming can never learn it. That is the actual gap, and it is in the library.

**The change:** on the read that `tick()` already performs, compare `latest_episode().seq` to
`self._started_seq`. If it moved, this worker has been displaced: set `_lost`, stop.

- **Cost: 4.08 µs** on a 200k-record log — one indexed seek, the *same* seek the fence's subquery
  performed. Paid **per tick**, not per write.
- **It yields detection, not silent dropping.** The worker stops — which also stops it writing the
  checkpoint the fence could never protect (refutation 2 above).
- **No substrate change, no signature change, no capability, no schema.** It respects the layering
  that both earlier revisions violated.
- It does not need to be enforced against a hostile writer, because it is not enforcement — it is a
  worker learning a fact about itself that the log already carries.

**What it does not do:** stop a worker that never ticks again, or one that ignores `_lost`. Write
authority against a *hostile* writer remains unsolved, and on this evidence should stay that way —
see the exit below.

## Still standing, independent of all of the above

- **The designated eliminator** (`cross-host-claim-gate.md` §4.2, undated per #42), with **aim +
  `expected_seq`** per §8.2. Fixes #39 with zero change to the discharge fold, fixes #42, and removes
  the forced forgery that creates #32's precondition. Already the live thread.
- **Declaration must be real.** "Tiers" live in a dict in `tests/conftest.py` keyed on fixture
  parameter names; nothing under `runstate/` knows about capabilities, so a third-party backend
  cannot declare one. This stands on its own.
- **The `(topic, name, seq)` index (#19).** `name` is a post-filter today; the miss path measures
  ~3000× better with the index, and `SELECT DISTINCT name` becomes a covering scan.

## The exit, if revision 3 is also refuted

Document that runstate provides single-writer **at the claiming instant only**, and say so in
`docs/api.md` rather than implying more. One honest line beats a floor with an opt-out.

## Note on baselines

Two independent reviews measured different suite totals (1020/1 vs 980/3) against nominally the same
tree. Unexplained. Anyone quoting a suite number should say which commit and whether
`RUNSTATE_TEST_PG_DSN` was set — without it ~220–240 tests skip, including every test of the CAS
under real cross-process contention.
