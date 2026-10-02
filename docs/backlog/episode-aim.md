# A lifecycle record that speaks for an episode must name it

**Status:** PROPOSED, **revision 2** — attacked as a unit, and three of revision 1's load-bearing
claims failed under construction. The mechanism survives and is the only thing on the table that
fixes the claim cascade. Its *case* needed rebuilding.

Revision 1 claimed aim closes #39's forgery route (it does not), specified a discharge predicate
that **regresses `stop-discharge.md` S3**, and co-gated this on `claim-eviction.md` on two arguments
that both fail. It also priced the change at `+18/−11`; corrected, it is **`+181/−24`**.

## The rule

> **Every `lifecycle.*` record that speaks for an episode carries `claim_seq` — the `seq` of the
> `lifecycle.started` it speaks for.** A record is **well-aimed** iff no `lifecycle.started` lies
> between its `claim_seq` and its own `seq` — equivalently, *its author was still the latest claimant
> when it wrote*. Readers attribute by that, never by position.

`value` is the deliberate exception (§7).

## What aim buys, exactly — and what it does not

**It buys non-transferability.** A record cannot speak for a claim that is not the one it named. That
closes the **misattribution** route: records that are *honest but late*.

**It does not buy forgery resistance, and cannot.** Aim is a consistency check between a record's
declared claim and its log position — **a forger controls both.** Measured:

```
operator halt pending: [3]
forger reads latest_episode(ch).seq = 1        <- one call, the whole cost of "aim" to a forger
forged stopped is WELL-AIMED: claim_seq=1
  -> any aim filter accepts it; the discharge fires exactly as today; live_episode -> None
```

This is not hypothetical. The one real forger in the corpus **already holds the value**:
`mycooc/scripts/reclaim_experiment.py:299` reads `claim_seq = ch.last_seq()` on the line after
`live_episode(ch)`. Migrating it to a stamped forgery is one keyword argument.

**So: #39's forgery route does not close here. It closes on the eviction half alone**, and revision
1's claim to the contrary is withdrawn.

## Why: the tier is inconsistent

The launcher and control tiers aim. The lifecycle tier aims only where it *answers a request* —
never where it **reports** or **eliminates** — so the two records that speak for an episode without
naming it, `stopped` and `heartbeat`, are exactly the two that get misattributed.
`_launcher_terminal` states the principle one tier up (*"Position cannot do this job"*), and
`../specs/write-authority.md` rev 4 withdrew the invariant positional pairing depended on without
the lifecycle tier being revisited.

## The defects it does fix — measured against a full prototype

| defect | with aim |
|---|---|
| forged verdict truncates `ensure` | `peek_terminal → None`, `progress → 0` — **fixed** |
| **the claim cascade** (a displaced worker's own honest dying breath releases the live successor's claim) | `C claimed: False` (was `True`) — **fixed** |
| unaimed heartbeat moves the frontier | `progress` stays 0, `last_activity` unmoved — **fixed** |

**The cascade is why this is not subsumed by `claim-eviction.md`.** Under eviction alone the reclaim
tool writes `evicted`, B legitimately claims, and **A's own honest `lifecycle.stopped` still lands
above B's claim and still releases it positionally.** The cascade is driven by an honest record, so
no third-party verb can reach it. The two mechanisms address different halves; no L2 minimality
problem.

## The predicate — "names the live claim" is WRONG and regresses S3

Revision 1 said *"a `stopped` that names no live claim discharges nothing."* Measured:

```
ep1 claim@1, control.stop@2, ep1's terminal, ep2 resumes
  literal rule ("names the live claim") : floor 0   <- the discharged stop reads PENDING again
  well-aimed rule                       : floor 4
  ep2 would re-honour the discharged halt under the literal rule: True
```

That is `../specs/stop-discharge.md` symptom 1 verbatim — `test_resumed_episode_ignores_prior_episodes_stop`,
the committed-RED test the discharge rule exists to keep green. **Use the well-aimed predicate.**

Consequence to state plainly: legitimate supersession between drain and dying breath leaves the stop
pending, and the successor re-honours it. That is `stop-discharge.md`'s declared
at-least-once-toward-an-idempotent-effect posture, so it is acceptable — but it is a semantic change.

## The folds — six sites, and the terminal selector fractures twice

`_episode_stopped`, `live_episode`, `progress` (heartbeat axis), `last_activity`,
`_discharge_floor`/`undischarged_stops`, **and `Watcher._note_heartbeat`** — which revision 1
omitted. Left unaimed, tier 4 reads a displaced worker's beacon as liveness while `last_activity`
reads stale: two liveness surfaces disagreeing.

**Fracture 1 — verdict loudness vs measurement tolerance.** One helper serves `peek_terminal`
(strict) and `progress` (tolerant) today because it only *positions*; parsing happens downstream.
Once selection reads the body it cannot serve both — aim-filter-first produced **36 measured
`DID NOT RAISE MalformedRecordError`**, the verdict plane silently ignoring a broken record, which
`observables.py` forbids. Split into `_episode_stopped` (tolerant) and `_verdict_stopped` (strict).

**Fracture 2 — append-only repairability.** A forward parse-the-whole-window shape bricks the channel
permanently and breaks `mycooc/scripts/repair_malformed_stopped.py`. `test_observables.py` pins this:
*"an append-only repair is the ONLY way to revive a channel bricked by a bad write … A fold that grew
a full-history parse would silently take the property away."* The selector must scan **newest-first**:
raise on a record naming *no* claim, skip one naming a *prior* claim, parse-and-return the first
naming *this* claim.

## Cost — the heartbeat fold as specified is unshippable

Revision 1 priced nothing. `latest(HEARTBEAT)` is O(1) and index-served; *"the latest heartbeat naming
this claim"* is a range read over **every beat of the episode**:

| beats | `progress` today | `progress` aimed |
|---|---|---|
| sqlite 10 000 | 11.0 µs | **23 368.6 µs (2124×)** |
| postgres 10 000 | 132.5 µs | **26 105.4 µs (197×)** |

Linear and unbounded. `progress` is per-tick under `ensure`; `last_activity` is per-run-per-frame in
`runstate-tui`. At 100 runs × 1 Hz that is 2.3 s/frame against a 1000 ms budget.

**Mandatory mitigation, in the spec and not left to the implementer: latest-then-verify.** Take
`latest(topic)`, check its `claim_seq`, range-scan only on a miss — **16.9 µs flat (sqlite), 177.7 µs
(postgres)**. The common case, a live worker beating for itself, never scans. Terminal folds are free
(`live_episode` 8.2 µs flat: it windows a topic with almost no records).

## The startless run — unanswered, and it breaks a consumer

Aim is undefined on a run that never claimed. Both genuine (c)-class test failures are this shape,
and it is live: `mycooc/run_experiment.py::resume_fanout` writes a stop on a never-started run, and
its own docstring says why — *"A stop staged on a run that never ran has nothing to discharge it…
Without this the request set is permanently unclaimable after a `--stop`."*

Under aim that stop is undischargeable by any third party. It still converges — the next episode
drains it, blips, and its well-aimed terminal discharges it — so the cost is **one mandatory wasted
episode per staged stop**, not permanent unclaimability. Either aim is optional-when-no-claim-exists
(which reopens "unstamped is strictly more powerful") or the consumer changes. **Decide before
building.**

## The suite — 15 real failures, not 48

Revision 1's 48 was counted against un-migrated fixtures. With a positional backfill — the one-time
offline pass `../specs/launcher-record-identity.md` shipped — it is **17 distinct, 2 harness
artifacts, 15 real**, and **no hangs**:

- **(a) encoding the old body shape — expected: 13.** Exact-body equality where `{'claim_seq': 1}` is
  the only diff, plus the v0.5 schema examples.
- **(b) incidental breakage: 0.**
- **(c) real consumers of positional attribution: 2** — both the startless-run shape above.

And the hang revision 1 attributed to the release half is the **heartbeat** axis: the first hang has
*no `lifecycle.stopped` at all*, only unstamped beats, so `progress` pins at `None` and `ensure`'s
poll-wait never terminates. With beats stamped, `test_memoizer` is 93/93 and `test_watcher` 65/65.
**The terminal half fails loudly**; only the beacon half fails silently.

## The co-gate is withdrawn

Revision 1 gated this on `claim-eviction.md` on two arguments, both now false: that aim closes #39
(it does not), and that *"once `stopped` requires aim, a third party needs a legal verb to release a
claim at all"* (it does not — the stamped forgery above drives `live_episode` to `None`).

**Decide `claim-eviction.md` on its own merits.** Its restated justification in that entry must be
corrected too.

## Migration — the objection revision 1 dropped, reinstated and corrected

Revision 1 headed this *"precedent exists and shipped"* and concluded *"already-broken logs stay
broken."* Both understate. The recorded objection was: a backfill *"can only be derived positionally
— i.e. from exactly the inference the field exists to replace — so migrated values are a guess
precisely in the case the field is for."* Measured on an un-migrated log:

```
UNSTAMPED beats: last_activity = 1000.0   (want 2002.0 -- reads 1000s STALE)
UNSTAMPED beats: progress      = None     (want 2)
```

So it is not *already-broken logs stay broken* — it is **every** log: unmigrated, the verdict plane
raises and the measurement plane goes blind; migrated, the stamps are a positional guess in exactly
the displacement case the field exists for. The precedent (a one-time offline pass over a quiescent
log) is still the right shape; the honesty is that it cannot be correct, only uniform.

## What it still does not fix

- **#39 entirely** — forgery route closes on eviction; the honest-worker route is `run-scoped-halt.md`.
- **The value plane** — declared *"a convergent merge (last-write-wins per cell), not a consistent
  snapshot."*
- **The artifact plane**, where a double-live worker's real damage lands.
- **Authentication.** Aim is checkable; authorship is not. *Name what you act on, not who you are.*

## The unbundling thread — sharpened, not subsumed

`lifecycle-stopped-unbundling.md` asked whether job 4 has a legitimate third-party author. Aim
answers it in the direction that makes the bundle **worse**: `resume_fanout` goes from *"forge all
five to get job 4"* to **cannot get job 4 at all** on its real target shape. Aim adds no record and no
eliminator, so there is no minimality problem — but that entry must be updated, not closed.

## Build order

1. **The `stopped` half.** Well-aimed predicate; split the selector strict/tolerant; scan
   newest-first.
2. **The `heartbeat` half**, only with latest-then-verify specified, and the fold list extended to
   `Watcher._note_heartbeat`.
3. **Answer the startless run** first — it is the one open design question and it has a live consumer.

## A trap worth recording

`uv run pytest` **in a git worktree resolves `runstate` from conda site-packages, not the worktree.**
A full classification run against the unmodified main repo looked clean. Set `PYTHONPATH` and assert
`runstate.__file__` in-process — the same shadowing trap this repo has hit repeatedly, in a new form.
