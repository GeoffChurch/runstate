# Spec: `lifecycle.evicted` — a designated eliminator for the episode claim

**Status:** PROPOSED. Retires #39 and #42 by construction, and removes #32's *precondition*.
Depends on `write-authority.md` having settled what a claim is — without that, this design is not
decidable (§2).

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

## 2. Why this is decidable now, and was not before

`cross-host-claim-gate.md` §4.2 calls **"who is allowed to write it, and on what evidence"** the
design's hardest open question, and §8.2's decomposition of it is stamped *"UNTESTED, attack before
use."* That question is what has blocked this three times.

It dissolves. The question only had force while the claim looked like it conferred **write
authority**: evicting a live worker's claim would be *taking something away*, and taking something
away demands a licence the log cannot check. `write-authority.md` settles that the claim never
conferred authority past its first instant — the CAS gives one claimant *at the instant of
claiming*, and nothing extends it. So a wrong eviction removes nothing that existed.

What a wrong eviction actually costs is therefore **blast radius**, not authority, and blast radius
is exactly what this record shrinks: today a wrong release asserts five things, and after this it
asserts one. §8.2 reached the same place from the other end — *"Stop trying to prove authority;
record aim instead"* — but could not justify it. This is the missing warrant.

**Consequence, stated plainly because it looks like a bug:** `evict_claim` will happily evict a
*live* worker. That is correct and intended. The worker keeps running and keeps writing, and its
records remain honest reports of what it did. Nothing was revoked, because there was never a
revocable grant. Anyone who wants single-writer-over-time gets it from whatever spawns the workers.

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
the same move `launcher-record-identity.md` made for death records.

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

The one change:

```python
    evicted = channel.latest(Topic.LIFECYCLE_EVICTED)
    if evicted is not None and evicted.body.get("claim_seq") == started.seq:
        return None
```

Placed beside the existing `stopped.seq > started.seq` release. Note the release condition is the
**aim**, not the position: `claim_seq == started.seq` already implies the eviction followed the
claim, since nothing can aim at a seq that does not exist yet.

**Known under-report.** `latest()` sees only the newest eviction. Two evictions after one claim,
where the newest carries a stale or junk aim, will read as *not evicted* even though a correct
eviction sits below it. This is the conservative direction — a claim reads held — and matches the
discipline that abstention reads as alive and ⊥ never authorises an irreversible act. The exact
alternative (`read(after=started.seq, topics=[EVICTED])`) is one range read returning 0–1 rows in
every real case; it is rejected here only for symmetry with the `latest(STOPPED)` line beside it,
and should be revisited if a malformed multi-eviction sequence is ever observed.

## 6. runstate must ship the producer

Two independent constraints force this, and one API satisfies both.

**Conformance.** `tests/test_schema.py` ends with `assert seen == ALL_RESERVED_TOPICS` — *"the
scenario must actually exercise the whole reserved vocabulary, else 'everything validated' is
hollow."* A reserved topic no library code path emits would be the first hole in that, and weakening
the assertion to accommodate it is exactly the wart the project bans.

**Adoption.** "Fixes #39 and #42" holds only if consumers *stop* writing `lifecycle.stopped`. If the
deployed reclaim script keeps forging — simpler, working, already written — nothing improves and
this is a protocol change wearing a coordination change's clothes.

```python
def evict_claim(channel: Channel, *, evictor: str, reason: str) -> bool:
    """Release the current episode claim. True if an eviction landed; False if
    there was no live claim to evict, or the log moved under us (re-read and
    decide again).

    Performs AIM and ATOMICITY. Does NOT judge liveness -- that is the caller's
    evidence and the caller's name on the record. This function cannot tell a
    stranded claim from a healthy one, and does not try: authority is not
    provable from the log (write-authority.md), so what is offered instead is
    attribution.
    """
```

This composes `read` / `latest` / `send(expected_seq=)` and adds no substrate primitive, so
`protocol-algebra.md` **L1** (`send`/`read`/`latest`/CAS is *complete*) is untouched — L1 governs
the `Channel` surface, not the library's.

The `Worker` never writes it. A worker's own release is its dying breath, which legitimately does
assert all five jobs.

## 7. The L2 precedent — checked, and it points the other way

`protocol-algebra.md` L2 records that **no `lifecycle.expired` constructor exists**, because *"every
consumer would immediately quotient the two, so the minimal generator set is the initial
vocabulary."* That is a standing argument against adding a second eliminator, and it was flagged
twice as unanswered.

It does not transfer — it *supports*. The rule bans a constructor consumers cannot distinguish. Its
own case is a lease expiry versus a client rescind, which really are the same elimination applied by
different authors, so a separate spelling buys nothing. Here **four of the five folds must
distinguish them**: `peek_terminal` must read no verdict, `progress` no frontier, the discharge no
answer, `_DATED_TOPICS` no date. That consumers *cannot* quotient these is the entire point, and the
fact that today they are forced to is the defect.

## 8. What this does not do

- **It does not extend write authority**, and does not claim to. #32's other half — a genuinely
  displaced worker that keeps writing — is settled as out of scope in `write-authority.md`.
- **It does not fix NFS.** The sqlite-on-NFS CAS can still admit two winners; that is a backend
  contract issue, addressed by deploying Postgres.
- **It grants nobody the right to evict.** Anyone who can append can already forge a `stopped`
  today. This changes what a wrong actor *asserts*, not who can act.
- **It is not a liveness signal.** Nothing about an eviction says the worker is dead.

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

## 10. Open, and to be measured before building

1. **`live_episode` gains a third read** (`latest_episode` + `latest(STOPPED)` + `latest(EVICTED)`),
   and it is a per-run-per-tick fold in the cockpit. **Unmeasured.** Two cost claims in this area
   have already shipped into docs while being wrong; measure this one against a real Postgres before
   the spec is called done, and record the number here.
2. **Where `evict_claim` lives.** `observables.py` is folds-only today — every function in it reads.
   A writer may not belong there. Cheap to decide, but decide deliberately rather than by drift.
3. **Whether `evictor` should be structured** (a scheme, like handles are) rather than free text.
   YAGNI says free text; the handle precedent says otherwise. Nothing folds on it either way.

## 11. Test plan

- **Fold isolation, one test per non-change** — after an eviction: `peek_terminal is None`,
  `progress` unchanged, `undischarged_stops` still returns the pending stop, `last_activity`
  unchanged. These four are the spec's actual claim and each should fail loudly if someone later
  adds the topic to a fold.
- **`live_episode` releases** on an aimed eviction, and **does not** on one aimed at a prior claim.
- **Non-transferability**: claim@5, evict aiming 5, new claim@7 → live again.
- **Atomicity**: a racing append between the evictor's read and its CAS makes `evict_claim` return
  False and land nothing.
- **The conformance scenario emits it**, keeping `seen == ALL_RESERVED_TOPICS` honest (§6).
- **Regression for #39 and #42 as filed**, written from the issue reproductions rather than from
  this spec, so they test the reported harm and not the intended design.
