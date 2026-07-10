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

**Status:** PROPOSED

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

**Status:** PROPOSED

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

**Status:** PROPOSED

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

## 6. `window_closed(progress, until)` — the fencepost's one public home

**Status:** PROPOSED · reduced scope (the red-team killed the general form)

**What it is.** A pure-data predicate in `vocabulary/schedule.py`, beside
`satisfied`: given a step frontier (`progress`, possibly None) and a **step-only**
`until`, is the half-open `[0, N)` window closed? Time and count atoms are
rejected loudly (time needs a caller's clock — a channel-shaped fold would flip a
*dead* run's window closed just by the observer waiting; count needs fire history
and is inexpressible in principle).

**Current state.** The `+1` fencepost (`_window_step = progress + 1`) is a private
convention in `memoizer.py` that consumers must mirror to ask "did this run reach
its target?": translation's `keys.py:floor_ok` mirrors it verbatim (with a comment
citing the private), and mycooc spells it inline.

**The improvement.** The coordinate convention gets one home; the memoizer
consumes it internally so the spelling can't drift; the Rust implementer and the
viewer (per-run done-vs-target from the `progress` they already poll) get it for
free. Deliberately *not* a general Condition evaluator — the reduced scope is the
honest one.

**Open questions.** Whether translation's deliberately runstate-import-free
`keys.py` would even adopt it (probably not — fine; the home exists for everyone
else), which slightly weakens the dedup story; the red-team's minimalist would cut
this item entirely if one had to go.

---

## 7. The `stopped.reason` vocabulary recipe (a shape, no words)

**Status:** PROPOSED

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

## 8. Launcher-record identity (the forged-verdict fix) — fast-tracked spec, then implementation

**Status:** PROPOSED · the design converged through two red-team passes; see the
amended `launcher-record-identity.md` for full detail

**What it is.** Two composing halves. **(i) The log-ordered void rule** in
`peek_terminal`'s launcher tier: a `terminated` is void if any *worker-authored*
record (`lifecycle.*`, `value`) **follows it by seq** — a late reap contradicted
by the live run's own subsequent records cannot stand as its verdict. A pure fold:
no probe, stays on the observables plane, self-heals within one beacon. **(ii)
Correlation ids**: a launcher stamps its `launched` and `terminated` with one
envelope `request_id`; the tier pairs a terminated to its *own* launched and
scopes the pair to its episode — this is what fixes *dead-log attribution*
(post-hoc, nothing left alive to write records; only correlation carries which
episode a manner-of-death belongs to). Plus: ThreadLauncher loser-suppression
(clean exit + a live foreign episode → suppress the `Terminated` write), and a
one-time migration stamping synthetic correlation ids into old logs by replaying
today's positional pairing once, offline — so no id-less dual path survives.

**Current state.** Reproduced on both reference launchers: a `terminated` landing
after a relaunch's opener defeats the latest-vs-latest pairing and the **live,
beaconing run reads as `completed` (or `killed`)** — `ensure` returns a truncated
series silently; `sweep` raises a spurious failure. **translation is exposed
today**: its concurrent `drive_block` shells over ThreadLauncher can store a
forged truncated result under a content-addressed rid — permanent, silent cache
corruption in exactly the concurrency its key layer was designed for.

**The improvement.** The one shipped wrong-verdict class closes, in both its live
form (the void rule — all three reproduced variants) and its post-hoc form
(correlation). The memoizer.md single-dispatcher caution retires.

**Forced amendments (red-team, second pass).** The guard must be **log-ordered,
never probe-based**: a `resolve()`-based "definitively live" check would let pid
reuse void *true* verdicts — a dead run reads live forever, `ensure`'s foreign
gate waits on a recycled pid indefinitely, a wedged sweep — operationally worse
than the forgery. (`EpisodeProbe`, connection-bound and genuinely definitive,
remains admissible where present.) Correlation alone is NOT sufficient for the
live variants (a loser's pair is internally clean — its own launched is the
newest opener), which is why the void rule is load-bearing. §6's
correlation-vs-visibility wording gets one clarifying line (launcher ids are
correlation-only).

**Open questions.** The void rule's exact record set (worker-authored only, or
any following record?); the one-beat residue's documentation; whether the spec
also conditions `ensure`'s failure/completed branches or lets the fixed
`peek_terminal` carry everything (likely the latter — one home).

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
  independent timelines by doctrine, and item 8 needs no schema bump; the batch
  never forms.
- **A general `window_closed(channel, until, now=...)` fold** — see item 6; the
  count/time holes make the general form dishonest.
- **An `ensure_terminal` helper** (send stop, await the terminal) — parked below
  the bar (one consumer, ~5 lines composable); if ever promoted, the canonical
  form generalizes `await_consumed` to the drain rule's full answer space, not a
  bespoke sibling.
