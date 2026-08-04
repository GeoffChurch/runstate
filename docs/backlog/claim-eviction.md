# `lifecycle.evicted` — a designated eliminator for the episode claim

**Status: DEFERRED — the design is sound; the purchase does not justify it yet.** Kept as a worked
design, because the day it *is* justified this is where to start. Not in `../specs/`: nothing here
ships.

**What the measurement changed.** The mechanics were prototyped twice and work — #39 and #42 both
flip. The *value* does not hold up:

- **#39 is real but confined.** Corpus scan over 1,933 openable logs in four repos: the harm fired
  **6 times, in 2 runs, from one experiment and one tool**. The whole corpus holds 49
  `control.stop` records. Nothing shows an operator was surprised.
- **#42 is display-only.** `runstate-tui/runstate_tui/fold.py:74` is the **one** production read of
  `last_activity` anywhere. It picks between `Status.live()` and `Status.stale()` — both absent from
  `types.py`'s `_STATUS_SEVERITY` (so both are `Severity.OK`) and from `pool.py`'s `_EVICT_KINDS`.
  It changes a string and a colour. In #42's own repro the branch is unreachable regardless: the
  forged `stopped` makes `peek_terminal` non-`None`, so the fold returns on the terminal arm first.
- **#32 loses its precondition, not its defect.** Unchanged from revision 2.
- **Atomicity was never a purchase.** `mycooc/scripts/reclaim_experiment.py:312` **already** writes
  with `expected_seq=claim_seq`. §4's atomicity contribution is deployed today.
- **The one adopting site gives something up.** That tool deliberately argues for the `preempted`
  projection (*"Setting `error` would project to ERRORED and mark the cell failed"*). Under
  `evicted`, `peek_terminal` returns `None` instead — harmless there, but a behaviour change to
  accept, not a free swap.

**Both defects close with ~6 lines in the consumer, using records legal today** — measured end to
end. In `reclaim_experiment.py`: stamp the sacct job End time instead of `time.time()` (**#42**);
and read `undischarged_stops` before the CAS, re-`send`ing each `control.stop` after it (**#39**).
No new topic, no schema bump, no fold case, no `live_episode` change. A stop landing after a worker
attaches is an ordinary live stop the drain takes on the next tick.

**Revive when** a *second independent consumer* needs it, or a third party appears that **cannot fix
its own writer**. Today's only beneficiary is one tool in one repo we own.

**The precedent bar, checked.** L2's decision rule admits the *type*, but this repo is **0-for-2** on
minting a reserved topic as a second eliminator — `lifecycle.expired` proposed and rejected twice
(L2's quotient argument; `../specs/service-worker.md`'s canonical-form argument). The
counter-precedent in this design's favour — the time-lease second eliminator — **added no topic**;
it reused the episode boundary. There is no precedent here for what this would do.

## The Postgres advisory lock as an eviction veto — REFUTED, do not re-propose

Floated as a cheaper answer to §4's cross-host blind spot: `PostgresChannel` implements
`EpisodeProbe.episode_alive`, which asks the **server** whether the episode's advisory lock is held —
unforgeable, and cross-host where `resolve()` abstains. Wire it into `live_episode` as a veto
(*refuse to release while the lock is held*), on the argument that vetoing is safe where the
previously-refuted arbiter form was not.

**It fails on the most ordinary path, measured.** `Worker.__exit__` does not close the channel
(`worker.py:145-151`), and `hold_episode` pins the lock to *that channel's connection*
(session-scoped, by design). So after a clean `stopped(completed=True)`, `episode_alive` is **still
True** until the process exits. Under the veto a **cleanly completed run reads LIVE** — a brand-new
false-alive wedging `relaunch_if_needed`, `ensure_served` and `ensure`.

The direction argument was incomplete rather than wrong: it reasoned only about the probe's *error*
modes, never the case where the probe is **correct** and the veto is still wrong. `episode_alive`
answers *"a session that once claimed this episode is still connected"* — strictly weaker than *"the
episode is live."* As a Watcher signal a stale True only delays a verdict; as a release gate it
**inverts a correct answer**. Same word, different object — structurally the error that produced
revision 1.

The narrow form (veto only the eviction branch) survives that but fails on its own terms: the
documented hazard that keeps a lock spuriously held — a hard partition, released only when TCP
keepalive fires — is *the same event that strands the claim*, so the veto refuses to evict exactly
the case the feature exists for, making Postgres **worse** than sqlite here. It is also
un-appealable, needs `isinstance(channel, EpisodeProbe)` inside `observables.py` (the layering
`../specs/channel-postgres.md` forbids by name), and costs **69.0 µs** against `live_episode`'s whole 95.0 µs.

**What survives:** the same probe in the *evictor's* hand rather than the fold — `evict_claim()`
consulting `episode_alive` before it writes. Advisory, retryable, overridable, bounded by the CAS,
and it keeps the lock out of `live_episode` entirely. That version is genuinely veto-never-arbiter.

---

Everything below is the design as it stood at revision 2, unchanged.

## 1. The problem

`lifecycle.stopped` does five jobs at once:

| # | job | read by |
|---|---|---|
| 1 | **releases the claim** | `observables.live_episode` |
| 2 | declares the verdict | `observables.peek_terminal` |
| 3 | reports the step frontier | `observables.progress` |
| 4 | discharges pending `control.stop`s | `Worker._discharge_floor`, `observables.undischarged_stops` |
| 5 | dates the run's freshness | `observables._DATED_TOPICS` → `last_activity` |

A third party — a reclaim tool, an operator, a dashboard — can legitimately establish only **(1)**.
It does not know the verdict, does not know the frontier, has no standing to answer the operator's
halt, and its own wall-clock is not the run's last activity. But releasing a stranded claim is the
only thing it wants, and `lifecycle.stopped` is the only record that does it. So it writes all five.

Every currently-open defect in this area is one of the four it should not be asserting:

- **#39** — the forged `stopped` raises `_discharge_floor` past a pending `control.stop`, silently
  destroying the operator's halt with no trace. (Job 4.)
- **#42** — the forged `stopped` carries the *evictor's* `t`, so `last_activity` reports a run dead
  for hours as fresh, forever. (Job 5.)
- **#32** — the reclaim must impersonate the worker to release the claim, which is what manufactures
  the displacement in the first place. (Jobs 2 and 3.)

## 2. The hardest question splits; only one half was ever hard

`cross-host-claim-gate.md` §4.2 calls **"who is allowed to write it, and on what evidence"** the
design's hardest open question, and §8.2's decomposition of it is stamped *"UNTESTED, attack before
use."* That question is what has blocked this three times.

**Revision 1 claimed it dissolves. It does not — it splits, and the halves have opposite answers.**

The dissolution argument ran: `../specs/write-authority.md` settles that the claim never conferred write
authority past its first instant, therefore a wrong eviction removes nothing that existed. The
premise is true. The inference is false, and the gap is the one that has now killed three designs in
a row: **it is a statement about the log, and the harm is off-log.** Consumers use the claim as the
mutual-exclusion token for resources runstate never sees.

Measured, and not hypothetically — through runstate's *own* composition. Evicting a live worker's
claim makes `launcher.relaunch_if_needed` spawn a **second live worker**: two `lifecycle.started`,
both with `_lost=False`, both writing, and through `ensure` a visibly spliced series with `t`
running backwards across adjacent steps. And `mycooc/scripts/gc_runs.py:88` decides a home is
collectible by `live_episode(ch) is not None` and then `rm`s its `*.pt` checkpoints.

The target consumer has already ruled on exactly this trade, from its own measurement
(`mycooc/CLAUDE.md`):

> There is **no staleness threshold**, deliberately. One was built, measured and rejected… Do not
> reintroduce one: **a false positive admits a second writer onto a live run, which is worse than
> the stranded claim it would fix.**

So the corrected claim: **a wrong eviction removes no *log* authority, because none existed — but it
authorizes a second writer to the world, and that is the cost.** Blast radius still justifies the
*record shape* (five assertions down to one). It does not justify evicting a live claim.

**What actually splits.** "Who may release a claim, on what evidence" was one question wearing two:

- **Releasing a stranded claim** needs no licence, because a stranded claim protects nothing. This
  is the entire motivating case, and the record handles it.
- **Releasing a live claim** needs a licence runstate cannot check — so the design **declines to do
  it at all**, mechanically (§4's probe veto), rather than leaving it to caller discipline.

That is `cross-host-claim-gate.md` §8.1's field-tested rule, which revision 1 failed to cite: **a
heuristic may VETO, never AUTHORISE.** The eviction record is the caller's evidence; the probe is
the library's veto. §8.2's *"stop trying to prove authority; record aim instead"* survives intact —
aim is what makes a wrong eviction attributable — but it was never a licence to overrule a
definitive probe.

## 3. The record

New reserved topic `lifecycle.evicted`, meaning **exactly one thing**: *this claim is released.*

```python
@dataclass(frozen=True)
class Evicted:
    """Releases an episode claim. Asserts NOTHING else -- no verdict, no
    frontier, no discharge, no freshness."""

    claim_seq: int   # the lifecycle.started this evicts -- the AIM
    evictor: str     # who did it: attribution, never authority
    reason: str      # free text; NO FOLD READS THIS
    TOPIC: ClassVar[str] = Topic.LIFECYCLE_EVICTED
```

**No `t`, deliberately.** `t` is the *worker's* self-reported wall-clock, and it exists so the
liveness plane can date a beacon. An evictor's clock is not the run's activity; carrying a `t` here
is precisely how #42 happens. Omitting the field makes the exclusion **structural** rather than a
rule someone can undo by editing `_DATED_TOPICS`. This follows `nak`, which the v0.4 schema already
records as *"deliberately left undated (nothing times it)."* Forensics are unaffected: the substrate
stamps `created_at` on every envelope.

**`reason` is free text and no fold reads it.** An evidence *taxonomy* (§8.2's strength / relevance
/ authority decomposition) is explicitly out of scope — it is the untested part, and nothing needs
it yet. If a fold ever wants to read this field, that is a new spec, not an edit to this one.

## 4. Aim and atomicity

**Aim.** `claim_seq` names the `lifecycle.started` being evicted. A reader checks that the named
claim *is* the claim currently in force. This makes an eviction **non-transferable**: an eviction of
claim@5 can never release claim@12, so a re-claimed run is not retro-evicted by an old record, and a
replayed one is inert.

**Atomicity.** The evictor writes with `send(expected_seq=)`, so the eviction lands on exactly the
tail it inspected or not at all. This closes the read-liveness-then-write window — mycooc's current
tool spans it with a 60-second `sacct` shell-out, during which the run can legitimately restart.

Together these make a wrong eviction *attributable and bounded* rather than anonymous and total —
the same move `../specs/launcher-record-identity.md` made for death records.

**The probe veto.** An eviction releases the claim only where the handle probe does **not** say
definitively alive. `resolve()` returns True (same-host, pid alive), False (same-host, dead), or
None (abstain — every foreign-host handle, `vocabulary/handle.py:45`). The veto fires only on True.

This is not a refinement; it is required. `tests/test_observables.py` pins the invariant and records
what happens without it:

> What nothing asserted is the direction that admits a SECOND WRITER. Reproduced 2026-07-28: a patch
> routing `live_episode` through the launcher tier passed the whole suite unchanged while revoking a
> live worker's claim, and a standing driver loop then span **201 spawns in 3s**.

Revision 1 placed the eviction check *above* `resolve()`, so the probe was never consulted — the
same shape, and the existing test would not have caught it (it exercises the launcher tier).

**The target use case is unaffected**: a stranded claim on another host resolves to None, not True,
so every case mycooc's reclaim tool handles still evicts. What the veto costs is a stranded
*same-host* claim whose pid has been reused — un-evictable, and strictly more conservative than
today.

## 5. Fold changes: one change, four non-changes

This is the design's whole economy, so it is worth being explicit that the non-changes are
structural rather than four separate patches that have to stay correct.

| fold | reads | change |
|---|---|---|
| `live_episode` (`observables.py:150`) | `latest(LIFECYCLE_STOPPED)` | **the one change** — also release on an aimed eviction |
| `peek_terminal` (`:171`) | `STOPPED`, `LAUNCHER_TERMINATED` | none — never sees the topic, so **no forged verdict** |
| `progress` / `_episode_stopped` | `STOPPED` | none — **no rewound frontier** |
| `undischarged_stops` (`:444`), `Worker._discharge_floor` (`worker.py:88`) | `STOPPED` | none — **#39 fixed** |
| `last_activity` via `_DATED_TOPICS` (`:326`) | five dated topics | none — **#42 fixed** |

The one change — **below** the probe, and a range read, both for reasons revision 1 got wrong:

```python
    probe = resolve(handle)
    if probe is False:
        return None
    if probe is not True:  #  a DEFINITIVE alive probe VETOES an eviction (§2, §4)
        if any(
            e.body.get("claim_seq") == started.seq
            for e in channel.read(after=started.seq, topics=[Topic.LIFECYCLE_EVICTED])
        ):
            return None
```

The release condition is the **aim**, not the position — though the range read makes that moot,
since `after=started.seq` already scopes it.

**Why a range read, not `latest()`.** Revision 1 used `latest()` and called the resulting
under-report "the conservative direction — a claim reads held." That was backwards. A *held* claim
**blocks relaunch**, so the failure is not a wasted poll:

```
correct eviction, then any later eviction with a stale or junk aim:
  live_episode        -> local://otherhost/...   a RELEASED claim reads HELD again
  relaunch_if_needed  -> REFUSED
  Worker(ch).claimed  -> False                   attaching workers lose and exit
  ensure(...)         -> hangs (memoizer.py: "No hang timeout")
```

The log is append-only, so that state is **permanent** — a run nobody can ever claim again. It is
also non-monotone: a release un-releases. The range read costs **+1.1 µs on sqlite and +7 µs on
Postgres** and is still one round trip. "Rejected for symmetry with the `latest(STOPPED)` line" does
not survive that price.

**Reach beyond the folds.** `live_episode` is not only read by observers — it gates `worker.py:108`
(claim or not), `launcher.py:353` `relaunch_if_needed`, `launcher.py:390` `ensure_served`, and
`memoizer.py:138`/`:468`. The double-spawn in §2 arrives through two of them. §5's economy is real
for the *verdict/freshness/discharge* planes; it was never a claim that the change is contained.

## 6. runstate must ship the producer

Two independent constraints force this, and one API satisfies both.

**Conformance.** `tests/test_schema.py` ends with `assert seen == ALL_RESERVED_TOPICS` — *"the
scenario must actually exercise the whole reserved vocabulary, else 'everything validated' is
hollow."* A reserved topic no library code path emits would be the first hole in that, and weakening
the assertion to accommodate it is exactly the wart the project bans.

**Adoption — and the honest count is 1 of 4.** "Fixes #39 and #42" holds only if consumers *stop*
writing `lifecycle.stopped`. Of mycooc's four forging sites, this design retires exactly one:

| site | can it stop forging? |
|---|---|
| `scripts/reclaim_experiment.py` | **yes** — the target, and 288 lines of it |
| `run_experiment.py` `resume_fanout` | no — it wants **stop discharge only**, which `evicted` explicitly refuses |
| `scripts/repair_malformed_stopped.py` | no — needs a parseable verdict record |
| `run_experiment.py` `_SyncHandle` | no, and correctly — it **earned** its verdict by observing the exit code |

`resume_fanout` is a second gap of the same shape (one job of five, wanting the discharge rather
than the release). This spec does not close it and should not pretend to; it is named here so the
next round starts from four jobs unbundled, not one.

**#42 is fixed upstream but not in the consumer.** mycooc's watchdog computes freshness with
`SELECT max(created_at)` over the raw log, **topic-agnostically**, so an undated eviction still
refreshes it there. Omitting `t` is still right — it fixes runstate's `last_activity` — but the
consumer needs its own change.

```python
def evict_claim(channel: Channel, *, evictor: str, reason: str) -> bool:
    """Release the current episode claim. True if an eviction landed; False if
    there was nothing to evict, or the log moved under us (re-read and decide).

    Performs AIM and ATOMICITY. The caller supplies the evidence and its name
    goes on the record -- authority is not provable from the log
    (write-authority.md), so what is offered instead is attribution.

    This does NOT let you evict a claim whose worker is provably alive: a
    definitive same-host probe VETOES the release (cross-host-claim-gate.md
    §8.1 -- a heuristic may veto, never authorise). Evicting a live claim would
    admit a SECOND WRITER to checkpoints and artifacts runstate cannot see,
    which is worse than the stranded claim it would fix.
    """
```

This composes `read` / `latest` / `send(expected_seq=)` and adds no substrate primitive, so
`protocol-algebra.md` **L1** (`send`/`read`/`latest`/CAS is *complete*) is untouched — L1 governs
the `Channel` surface, not the library's.

The `Worker` never writes it. A worker's own release is its dying breath, which legitimately does
assert all five jobs.

## 7. The two recorded rejections of a second eliminator

There are two, not one. Revision 1 answered only the weaker.

**L2's quotient argument — does not transfer, and in fact supports.** `protocol-algebra.md` records
that **no `lifecycle.expired` constructor exists**, because *"every consumer would immediately
quotient the two."* The rule bans a constructor consumers cannot distinguish; its own case is a
lease expiry versus a client rescind, which really are the same elimination by different authors.
Here **four of the five folds must distinguish**: `peek_terminal` no verdict, `progress` no
frontier, the discharge no answer, `_DATED_TOPICS` no date. That consumers cannot quotient these is
the point; that today they are forced to is the defect.

**`../specs/service-worker.md`'s canonical-form argument — this one lands, and is a real cost.** A shipped
spec rejects a `lifecycle.expired` event for different reasons:

> a second counter-record kind for one fact — the fold grows a case, the lifecycle schema bumps, and
> canonical form loses (one fact, one record).

All three prongs apply here, and this spec **admits all three elsewhere**: §5 is the fold growing a
case, §9 is the schema bump, and "one fact, one record" is straightforwardly lost. So this is not
rebutted — it is a priced cost. What buys it is that the fact being eliminated genuinely has **two
legitimate authors** asserting different things: the worker knows all five jobs, a third party knows
one. L2's case had one fact and one meaning; this has one fact and two meanings, and canonical form
was already lost the moment a third party had to forge.

**A precedent in this design's favour, which revision 1 missed.** `protocol-algebra.md` already
designates a *second eliminator* within L2's own scheme — *"time-referencing subscribe ↔
additionally the next episode boundary — a **second eliminator** for the time-leased case, so that
affine resource is in fact always consumed."* So a second eliminator is not categorically banned; it
is admitted where one eliminator leaves the resource unconsumed. That is exactly this case: a
stranded claim is a fact whose only eliminator cannot legitimately be written by the only party
present.

## 8. What this does not do

- **It does not extend write authority**, and does not claim to. #32's other half — a genuinely
  displaced worker that keeps writing — is settled as out of scope in `../specs/write-authority.md`.
- **It does not fix NFS.** The sqlite-on-NFS CAS can still admit two winners; that is a backend
  contract issue, addressed by deploying Postgres.
- **It grants nobody the right to evict.** Anyone who can append can already forge a `stopped`
  today. This changes what a wrong actor *asserts*, not who can act.
- **It is not a liveness signal.** Nothing about an eviction says the worker is dead.
- **It does not release a provably-live claim** (§4's veto), so it is not a preemption mechanism.
- **It does not unbundle stop discharge** from the dying breath, which is what `resume_fanout`
  actually needs (§6). One of the five jobs is separated here; the other three stay bundled.

## 9. Migration: none, and the reason is honest

Existing logs contain forged `lifecycle.stopped` records written by reclaim tools. **They cannot be
migrated**, because a forged `stopped` is byte-indistinguishable from a real one — the discharge is
author-blind *and* body-blind, as `undischarged_stops` now records. Nothing on the log identifies
which stops were forgeries.

So old logs keep their forged verdicts, forever, and there is **no** compatibility path that reads a
`stopped` as an eviction — that branch would be the exact wart the project bans. This is a
correctness limit, not a cost one, and it is the kind that survives "old logs do not constrain us."

Schema: `protocol/lifecycle-v0.4.schema.json` → **v0.5**, adding `lifecycle.evicted` to the topic
enum and its body, replacing v0.4 rather than accumulating beside it.

**That is a doc-wide edit, not a file rename.** Priced, because revision 1 undercounted it: **two**
conformance assertions, not one — `test_schema.py` and `test_implementers_guide.py`, the latter
requiring a valid `lifecycle.evicted` example in `../implementers-guide.md`. Plus
`test_public_api.py::test_public_surface_is_stable`, `test_api_doc_covers_the_public_surface`,
`../api.md`, and 11 `lifecycle-v0.4` references across `../api.md`, `../implementers-guide.md`,
`CHANGELOG.md`, `README.md`, `CLAUDE.md`, and `release-and-stability-contract.md`. And
`live_episode`'s own docstring, which currently states the *opposite* of what this ships (*"Only a
later `lifecycle.stopped` and a `resolve()`-dead handle release a claim"*).

**No consumer pins a runstate version.** All three import the working tree by ambient `sys.path`, so
a v0.4 → v0.5 bump has nothing to bump against: the flip is instantaneous and not opt-out-able. That
is an argument for landing the consumer changes in the same window, not for a compatibility shim.

## 10. The cost of the third read — MEASURED

`live_episode` goes from two reads (`latest(STARTED)`, `latest(STOPPED)`) to three, on a fold the
cockpit runs per run per tick. Measured against sqlite and a real Postgres, median of 400 calls,
with **no eviction on the log** — the common case, and the one that decides this, since it is a
`latest` on a topic with zero records:

| backend | delta | ratio |
|---|---|---|
| sqlite | **+1.7 … +2.0 µs** | 1.21–1.30× |
| postgres (unix socket) | **+33 … +48 µs** | 1.46–1.52× |

Ranges span two independent harnesses on the same machine; they agree on shape and differ on
absolute numbers, so quote the range. The second measurement makes the sqlite **ratio** worse than
first reported (1.28× rather than 1.21×) and Postgres **cheaper** (+33 µs rather than +48 µs); "81%
of each read is pure round trip" measured at 66% the second time. None of it changes the conclusion.

Add **+1.1 µs (sqlite) / +7 µs (Postgres)** for the range read §5 adopts over `latest()`. Still one
round trip.

**Flat in log size** from 3 to 20,001 records, on both backends and both harnesses — the read is
index-served, as the `(topic, seq)` index predicts.

The delta is **exactly one round trip**, and the decomposition says so rather than leaving it as an
inference. On Postgres: a bare `SELECT 1` is 37.0 µs, one `latest()` is 45.6 µs, two are 87.1 µs,
three are 132.6 µs. So **81% of each read is pure round trip** and the marginal cost of this design
is one RTT — not 48 µs. On a unix socket that is 48 µs; over the SSH tunnel to a cluster login node
that mycooc's deployment plans, it is the *tunnel's* RTT. At 100 runs and 1 Hz: +0.18 ms/frame on
sqlite and +4.8 ms/frame on local Postgres (both fine against a 1000 ms frame), but +500 ms/frame at
a 5 ms tunnel RTT, which is not.

**The mitigation makes this a net win, and it is #15.** One query returning latest-per-topic over a
topic *set* — the richer `read` that #15 proposes — answers all three topics in **73.4 µs**, which
is *cheaper than today's two separate reads at 87.1 µs*. So `live_episode` **with** the eviction
check, batched, is ~16% faster than `live_episode` **without** it today, and the gap widens with
RTT because it removes two round trips instead of adding one.

That is the honest conclusion: the third read is affordable everywhere runstate runs today
(sqlite, and Postgres on a socket), it is the tunnel deployment that would feel it, and the fix is
already an open issue that this design gives a concrete reason to take.

## 11. Still open

1. **Where `evict_claim` lives.** `observables.py` is folds-only today — every function in it reads.
   A writer may not belong there. Cheap to decide, but decide deliberately rather than by drift.
2. **Whether `evictor` should be structured** (a scheme, like handles are) rather than free text.
   YAGNI says free text; the handle precedent says otherwise. Nothing folds on it either way.
3. **Whether to sequence #15 first** if the tunnel deployment lands before this does.

## 12. Test plan

- **Fold isolation, one test per non-change** — after an eviction: `peek_terminal is None`,
  `progress` unchanged, `undischarged_stops` still returns the pending stop, `last_activity`
  unchanged. These four are the spec's actual claim and each should fail loudly if someone later
  adds the topic to a fold.
- **`live_episode` releases** on an aimed eviction, and **does not** on one aimed at a prior claim.
- **Non-transferability**: claim@5, evict aiming 5, new claim@7 → live again.
- **Atomicity**: a racing append between the evictor's read and its CAS makes `evict_claim` return
  False and land nothing.
- **The conformance scenario emits it**, keeping `seen == ALL_RESERVED_TOPICS` honest (§6) — via a
  stranded foreign claim plus a real `evict_claim` call, never a hand-written record.
- **Regression for #39 and #42 as filed**, written from the issue reproductions rather than from
  this spec, so they test the reported harm and not the intended design.
- **The probe veto, with a same-host live handle** — the direction `test_observables.py`'s existing
  test cannot reach, since it exercises the launcher tier. This is the test whose absence let
  revision 1 through, so it is the one that matters most.
- **The wedge, as a negative**: a correct eviction followed by a junk-aimed one must still read
  released, and `relaunch_if_needed` must still relaunch. Pins §5's range read against a future
  "optimisation" back to `latest()`.
- **No second live worker**: evicting a claim whose probe says alive must not let
  `relaunch_if_needed` spawn — asserted on spawn count, not on the fold, because the fold is not
  where the harm showed up.
