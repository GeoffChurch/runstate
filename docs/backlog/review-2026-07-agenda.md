# The 2026-07 review agenda — deliberation ledger

**What this is:** the convergent output of the 2026-07 holistic review (stages 1–4 +
two adversarial passes), written up item-by-item for deliberation. Each item below
carries: what it is, the current state, how the change improves on it, the
amendments the adversarial passes already forced, and its open questions.

**Protocol:** items are deliberated one at a time and edited in place as the
discussion converges. `Status:` moves `PROPOSED → AGREED (date) → SHIPPED (commit)`.
A shipped item keeps a one-line tombstone here until the whole agenda closes, then
this file is pruned per the backlog convention (git carries the history). Ship
order below is the red-team's recommendation; deliberation order is the owner's.

---

## 1. Remove `Started.hostname` (lifecycle-convention bump + migration)

**Status:** SHIPPED 2026-07-10 — the bump in `de1238d`; migration converged in
one pass (1,810 dbs migrated, 85 clean, 0 skipped-live; independent scan: 1,898
started records, zero hostname keys; idempotent re-run all-clean), script
deleted per ruling. The rest of this entry is retained until the agenda closes.

**What it is.** Drop the `hostname` field from the `lifecycle.started` body: the
dataclass, the schema (a lifecycle-convention version bump), the emit site, and a
one-time migration script that strips the key from existing logs.

**Current state.** The reference worker has only ever emitted `hostname=None`
(`worker.py` hardcodes it); no reader anywhere — runstate, mycooc, translation —
consults it; and the handle string already carries the host (`local://host/pid`;
`resolve` is hostname-scoped through the handle alone). The field has never held
data on any of the ~1,900 real logs (verified empirically, stage 2).

**The improvement.** One dead coordinate removed from a frozen body — the basis
audit's cleanest independence violation (`hostname` ⊂ `handle`). Future location
needs belong in the handle *grammar* (the same reasoning that put the deferred
`?start=T` disambiguator there), so removal also closes the door on splitting one
concern across two fields. Shipping it alone and first rehearses the
bump-plus-mandatory-migration machinery on a trivial case before item 8 may need it.

**Forced amendments (red-team).** The migration must be quiescence-gated per db
(`live_episode(ch) is None` → migrate; live → skip, converge over repeated passes)
and idempotent (keyed on the `hostname` key's presence) — a blanket UPDATE pass
over live dbs can hit the DELETE-journal no-busy-retry gap (J3) and kill a healthy
worker with `database is locked`. The half-migrated world is benign at runtime
(nothing strictly parses `Started` off logs), so convergence-over-passes is safe.
The earlier "batch this bump with item 8's" lean is dead: conventions version on
independent timelines *by doctrine*, and item 8 needs no schema bump anyway.

**Open questions.** Where the migration script lives (a `scripts/` dir does not
exist yet); whether the consumer-repo migrations are run by the owner alongside or
shipped as a documented command.

---

## 2. `last_seq()` — the fifth substrate op, then the attach fix

**Status:** SHIPPED 2026-07-10 — rulings: named `last_seq` (the CAS's read half;
"head" is front/back-ambiguous); §4 gains the op-admission principle ("the
surface must be readable in every coordinate it requires callers to assert");
`retire()` does NOT adopt (its read is load-bearing — it drains what it reads).
Ship evidence: attach on the same 10⁶-envelope log the 3.4 s baseline was
measured on now takes **1.5 ms median** (5 trials), no gigabyte materialization;
conformance pins 0-on-empty / contiguity-agreement / assert-to-win;
`worker-attach-scale.md` executed and deleted. The rest of this entry is
retained until the agenda closes.

**What it is.** Add one substrate operation, `last_seq() -> int` — the log's
current last `seq`, `0` for an empty log — to the Channel surface, the design §4
contract, and the conformance suite. Then land the already-designed worker-attach
fix on top of it (`worker-attach-scale.md`): read `S = last_seq()` first, compute
the claim-read folds from topic-filtered reads capped caller-side at `seq <= S`,
claim with `expected_seq=S`.

**Current state.** Four ops. The CAS makes every claimant *assert* the head
(`expected_seq` is a head assertion; `0 = empty` is its base case), but nothing
can *read* it below O(N) — so `Worker.__init__` does an unfiltered `read()`:
measured ~3.4 s and ~0.8 GB transient on a 10⁶-envelope log with one control
record. `retire()`'s death-CAS plays the same game. A viewer has no cheap
"anything new?" watermark.

**The improvement.** Attach drops from seconds to milliseconds with the same-read
exactness preserved by assertion (CAS success at `S` proves the capped folds equal
the one-big-read folds). The surface stops being asymmetric — readable in every
coordinate it requires callers to assert (that principle, not a bespoke two-clause
criterion, is what gets written into §4 as the op-admission rule; it admits this
op and nothing else non-arbitrarily). Every backend serves it O(1) (`len(log)`;
`MAX(seq)` on the PK/autoincrement). Serendipity: it is exactly the incremental
viewer's per-run watermark — poll `last_seq()`, re-fold only on change — which the
TUI/viz plan needs anyway.

**Forced amendments (red-team).** Named `last_seq` not `head` ("head" reads as the
*front* of a log to half its audience); the conformance pin must state `0 = empty`
as the CAS base-case dual; `channel/base.py`'s "four pure data ops" prose (and one
stale "both backends") must be revised, not silently contradicted; the §4 note
should sanction the Watcher/viewer watermark use so it doesn't need a second
debate; the capping in the attach fix is caller-side (`read()` has no seq-ceiling
parameter) — an implementation note, cheap because the folded topics are rare.

**Open questions.** Whether `retire()` adopts it in the same change or later;
whether the memory backend's `read(after, limit)` O(after) scan is worth a note.

---

## 3. `Worker.emit(name, value)` — the typed broadcast-value verb

**Status:** SHIPPED 2026-07-10 — rulings: named `emit`; returns None (`last_seq`
is racy as "my record's seq" — the race-free need has no consumer; raw
`channel.send` returns the seq if one ever appears); `emit` ALSO updates the
`set` register (the two planes can never disagree about the current value);
no-step-to-stamp RAISES (stricter than the red-team's document-only amendment —
a fake default step of 0/-1 is schema-invalid or silently corrupts the
per-(name, step) folds; the design already chose null-over-fake-0 for the
stepless heartbeat). The `set`/`emit` docstrings carry the two-persona split
(observer-chosen vs worker-chosen cadence); design §13 gains the
inversion-of-control rejected-alternative from this item's deliberation. The
rest of this entry is retained until the agenda closes.

**What it is.** One new Worker method: emit a value point unconditionally —
`{value, step: <last tick step>, t: <now>}` on topic `value`, `request_id=None`
(the broadcast arm §6 already defines) — beside the existing demand-gated
`set()` + subscription path.

**Current state.** The wire defines the broadcast value but the reference worker
cannot produce it: `set()` is invisible to `ensure`/`history` unless someone
subscribed. Both consumers therefore hand-roll `channel.send(asdict(Value(...)),
...)` at ~13 sites with the same complaint verbatim — and every one of those
hand-rolled sends **bypasses the claim-loser muzzle**: a racing loser writes
values onto the winner's live log today.

**The improvement.** The single most-duplicated consumer wart collapses into a
verb that is *safer* than what it replaces: loser-gated (`_lost` → silent no-op,
like its siblings), step-stamped from the worker's own clock, and wrapped by the
same serialization error handling as `_service` (naming the metric on failure).
Orthogonality holds — register-sampled-on-demand vs point-emitted-now are two
concerns, and this is the split, not a duplication.

**Forced amendments (red-team).** The docstring must say a stepless emit
(`step=None`, e.g. from `serve()` or before the first tick) **permanently bricks
`history()` — hence `ensure` — for that name** (one conforming stepless point
raises forever on an append-only log), not the euphemism "falls outside the
domain". No `step=` override — the worker's clock stays canonical; raw
`channel.send` remains the caller-clocked path, and the pitch is scoped honestly:
this serves the `steps()`-driver pattern; mycooc's Worker-less emitter keeps raw
`send` as its main path, legitimately.

**Open questions.** The name (`emit` vs `record`); the return value (the `seq`,
or nothing).

---

## 4. Typed `ensure` exceptions

**Status:** SHIPPED 2026-07-11 — rulings: `RunFailedError(run_id, result)` /
`NoProgressError(run_id, progress=, until=)`, plain-`Exception` bases (the
subclass-RuntimeError continuity idea stayed dead — compat-speak), both
exported; context attributes per the principle *anything load-bearing in the
message becomes an attribute*. runstate's own catch sites migrated (README
killed-redrive recipe — which SIMPLIFIED, the recordless-None dance is gone
since the verdict rides the exception — and examples/redrive, whose
`resumable()` dropped its None-guard). Consumer migration note handed to the
owner (mycooc: two catch sites + tests; translation: unaffected). The rest of
this entry is retained until the agenda closes.

**What it is.** Replace `ensure`'s two branched-on `RuntimeError`s with types:
`RunFailedError` (carrying the `RunResult` observed at raise time) and
`NoProgressError`. Plain `Exception` bases, consistent with
`MalformedRecordError`. The caller-bug raises (`TypeError` on a None handle,
`ValueError` on count atoms / the null epoch) stay untyped by design.

**Current state.** Both failure verdicts raise bare `RuntimeError` whose *message
text is load-bearing*: mycooc branches on `"no progress" not in str(e)` — a
wording change in runstate breaks a consumer's control flow — and re-derives the
failure class afterwards via a fresh `peek_terminal` (which can even observe a
*different* terminal after a relaunch than the one that caused the raise).

**The improvement.** The message stops being API. mycooc's catch site collapses to
two except-clauses, and `RunFailedError.result` hands over the verdict captured at
raise time — strictly better than the re-read. The implementer's-guide raise table
gets two real entries.

**Forced amendments (red-team).** *Not* subclassing `RuntimeError`: the
"existing catches keep working" rationale is compat-speak under the no-warts
doctrine, and it would let mycooc's substring-match survive unmigrated forever.
Plain bases + migrate the consumers' catches (an afternoon, owner-side).

**Open questions.** Whether `NoProgressError` carries context attributes
(`progress`, the target `until`); whether a shared `RunstateError` base is worth
introducing now or only when a third member appears.

---

## 5. `undischarged_stops(channel)` — the stop fold's observer home

**Status:** SHIPPED 2026-07-11 — `list[Envelope]`, one `latest` + one
topic-filtered `read(after=floor)` (the discharge rule expressed as a cursor);
the two edges documented and test-pinned (pending ≠ due; naked stops
over-report until the next `stopped`); design §7's pairing-instance list now
names it as instance 1's public home beside `live_demand`'s instance 2. Riding
along: `_boundary_voided` → `boundary_voided` (the established cross-module
convention). The rest of this entry is retained until the agenda closes.

**What it is.** A stateless observable returning the `control.stop` envelopes not
yet discharged by a following `lifecycle.stopped` — the observer half of the
stop-discharge rule, mirroring `live_demand` (the subscribe fold's observer home).

**Current state.** Design §7 names exactly two positional rules; the subscribe
fold has a public observer home (`live_demand`), the stop fold does not — it lives
only in the worker's drain. mycooc re-implements it (`_undischarged_stop`) from
raw `latest` seq comparisons, load-bearing in its producer stop gate and exit
path. "Is there an unhonored stop?" is what any stop-button UI needs.

**The improvement.** One-rule-one-home (the repo's own F7 doctrine, the
`_boundary_voided` precedent): a single predicate consumed by both the worker's
`_discharge_floor` drain and the observer fold, so the two can never drift.
Returns `list[Envelope]` so the consumer's boolean falls out and the request-id
provenance is there for an attribution surface later.

**Forced amendments (red-team).** Two documented edges the observer fold cannot
avoid: **pending ≠ due** (a `{from: {step: 1000}}` stop is pending at step 500 but
not yet due — a gate-style consumer must not treat pending as actionable, on pain
of a livelock the worker-side rule can't produce), and **nak-divergence** (a
malformed stop is naked and never enters the worker's pending set, but nothing
discharges it observer-side until the next `stopped` — the fold conservatively
over-reports). Name `undischarged_stops`, not `pending_stops` — "pending" is the
worker's due-evaluated set, which this deliberately is not.

**Open questions.** None substantive.

---

## 6. `window_closed(progress, until)` — DROPPED (documented instead)

**Status:** DROPPED 2026-07-11 — cut the function, documented the fencepost.

The owner's surface-accretion worry landed on exactly this item, and an
independent helper-classification audit confirmed it: the `[0, N)` /
`progress + 1 >= N` fencepost is **shallow arithmetic on an already-correct
`progress()` value** — it does NOT trace to the append-only/multi-episode
staleness that earns every other observable, and BOTH consumers spelled it
right by hand (translation `keys.py:floor_ok`, mycooc inline `p >= req`) and
neither would adopt a helper. So it is sugar, not a footgun-preventer. The
memoizer already has the one internal home (`_window_step`); the beneficiaries
who needed the *rule* (a second-language implementer, the viewer) needed it
**written down**, not minted as API. Resolution: the fencepost is now
documented on `observables.progress` (its docstring) and in the
implementer's-guide backlog entry.

*Banked for future simplification passes (the audit's other soft spots — do NOT
re-flag as sugar):* `sweep` is the **batch-sweep persona's entry point** (run a
fixed variant set to completion, collect verdicts), parallel to `ensure`'s
memoized-target door — not sugar; the two consumers are both reuse-shaped so
they take the `ensure` door, and translation reuses `sweep`'s `Variant` +
`launch_producer` regardless. `pinned`/`broadcast`/`ensure_served` are the
service/leased-demand plane the basis audit (Q4) already ruled KEEP for a
future persona. The meta-lesson: **"unused by mycooc + translation" is weak
sugar-evidence** — both repos are the same (reuse) persona and speak for no
other.

---

## 7. The `stopped.reason` vocabulary recipe (a shape, no words)

**Status:** SHIPPED 2026-07-11 — a section in `../specs/completed-opt-in.md` (the
spec that removed `Stopped.reason`, closing the loop it opened — no new file).
Rulings: bless the SHAPE only (stepless `value` register), suggest `completion_reason`
as the conventional NAME (vocabulary stays the workload's — the one hair of opinion,
cheap coordination for a viewer), both safety rules in (episode-scoped read;
terminal-owns-done-ness / register-owns-why). mycooc's `_complete_from_channel`
PATIENCE-trusts-register-without-terminal surfaced to the owner as calibrated
observation (microsecond window — the rule bites a large register→terminal gap,
not their adjacent sends). The rest of this entry is retained until the agenda closes.

**What it is.** A short spec documenting the opt-in pattern for recording *why* a
run stopped — a value-plane register (one agreed name, e.g. mycooc's
`completion_reason`) written before the dying breath — without adding any wire
surface and without blessing any vocabulary.

**Current state.** `RunResult.outcome` is deliberately closed and `reason` is
tier-local; the worker's *semantic* reason ("converged", "patience",
"budget_exhausted") has no documented home. mycooc built the pattern unaided
through a recurring bug class: a completion-reason register with two writers
(including the force-kill path) and an episode-scoped reader.

**The improvement.** A fence around the closed enum: with a documented answer to
"how do I record why", pressure stops landing on the convention body (no
`Stopped.reason` resurrection, no `outcome` widening — workload words never enter
the protocol). The recipe must carry the two safety rules mycooc's scars teach:
**episode-scope the read** (a resumed run must not report the prior dispatch's
reason — mycooc learned this) and **pair the register with `peek_terminal`** (the
register is a *prophecy* about a death that hasn't happened; mycooc's
`_complete_from_channel` currently trusts it without a terminal check — a live bug
in that repo worth flagging to its owner when this ships).

**Forced amendments (red-team).** Bless the shape only — no word list; even
`converged`/`budget_exhausted` as *suggestions* is opinion creep. Home: a small
spec (it documents cross-repo interop of a name), not a README aside.

**Open questions.** Spec filename; whether the recipe also documents the
orchestrator-written variant (mycooc's force-kill path writes the register from
the other side — legitimate, since the value plane is author-agnostic).

---

## 8. Launcher-record identity (the forged-verdict fix)

**Status:** SHIPPED 2026-07-14 (`16c8ede`) → `../specs/launcher-record-identity.md`.
The spike the spec called for ran, settled §5, and **changed the design twice** —
both times because the code said so and the prose hadn't.

**What shipped:** one correlation id per launch, on the launcher's `launched` +
`terminated` **and re-emitted by the worker on its `lifecycle.started`** (ambient:
`RUNSTATE_LAUNCH_ID` / a ContextVar). `terminated` now asserts "*my launch* ended"
instead of the unknowable "the run is dead", and the verdict is anchored to the
**claimed** episode, paired by id. Both reproduced forgeries — the late reap and
the claim-loser — die by construction, window-free. Both tiers now obey one rule:
*a terminal stands until a new episode claims*.

**The spike's two corrections (each supersedes what this ledger said above):**
1. **§5's lean won, and paid a dividend nobody predicted.** Carrying the id to the
   worker's claim made the fold *launcher-agnostic* — which let the **reap
   discipline be DELETED** rather than extended (its conditional silence was a
   writer-side workaround for identity-less records; with identity, the writer
   stays honest and the reader attributes). The fix removed more machinery than it
   added. Old "B" was indeed unneeded; old "C′" was indeed not load-bearing.
2. **"No schema change" was WRONG** — and only writing the tests exposed it (a
   fixture holding an id-less launcher record kept passing while quietly producing
   *no verdict*). Legality (`request_id` was unconstrained) is not necessity (the
   fold now *depends* on it), and a dependency the schema doesn't pin is exactly
   the unstated invariant this review exists to eliminate. Hence **launcher-v0.3**:
   `request_id` required on both records; an unidentified death is malformed and
   the verdict plane raises rather than guess.

**Method note, worth keeping:** the spec explicitly refused to settle §5 by more
prose ("the fold formulation broke twice under pure reasoning") and mandated a
spike. Both of the above are things reasoning had gotten *wrong* and the
reproductions + tests got right within an hour. When a design breaks twice under
analysis, stop analyzing.

**One action outstanding (needs the owner):** the data migration.
`scripts/migrate_launcher_v0_3.py` is committed and verified end-to-end, but it
**writes to `~/src/translation`'s run logs** (1,129 of them carry id-less launcher
records) — a repo scoped READ-ONLY for this review, so it awaits explicit
authorization. mycooc needs nothing (685 logs, **zero** launcher records — it never
used runstate's launchers). Translation's logs all read correctly *today* (each ends
with a clean `stopped`, so the launcher tier is never consulted), but the first
`ensure`-**resume** of any of them under the new library raises `MalformedRecordError`
— verified on a copy. Until it runs, item 8 is shipped in-library but not converged
on disk; the script is deleted (per the migrate-then-delete discipline) only after.

---

## Considered and dropped

- **The `RunResult` "refinement" comment fix** — REFUTED by the shipped code: on
  the launcher tier `reason="exited"` spans `COMPLETED` *and* `ERRORED` (the
  outcome comes from `exit_code`), so reason neither determines nor is determined
  by outcome; the existing "orthogonal" comment is closer to true than the
  proposed fix. Optional precision edit if ever touched: "per-tier reason
  vocabularies; only the inference tier's (`probed_dead`/`heartbeat_stale`/
  `episode_lock_released` → `presumed_dead`) refines outcome."
- **Batching the item-1 schema bump with item 8's** — conventions version on
  independent timelines by doctrine; the batch never forms. *(Postscript: item 8
  DID need a schema bump after all — launcher-v0.3 — but the doctrine held: it
  shipped on its own timeline, three days after lifecycle-v0.3, batched with
  nothing.)*
- **`window_closed` in any form** — item 6, DROPPED 2026-07-11: the general
  channel-fold has count/time holes; even the reduced pure-data predicate is
  sugar (arithmetic on an already-correct `progress()`, both consumers cleared
  it by hand). Documented on `observables.progress` instead of minted as API.
- **An `ensure_terminal` helper** (send stop, await the terminal) — parked below
  the bar (one consumer, ~5 lines composable); if ever promoted, the canonical
  form generalizes `await_consumed` to the drain rule's full answer space, not a
  bespoke sibling.
