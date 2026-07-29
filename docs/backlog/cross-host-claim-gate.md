# Cross-host liveness for the claim gate

**Status:** DESIGN DELIBERATION (2026-07-16) — NOT CONVERGED; implementation is
owner-gated (a claim-model redesign: it graduates only through its own spec +
adversarial pass, per [`../specs/channel-postgres.md`](../specs/channel-postgres.md)
"Deferred"). This file elaborates the [index](index.md) "Cross-host liveness for
the claim gate" inline entry into a living document. It **prepares** an owner
ruling; it does not make one. No code changes accompany it.

---

## 1. The problem, precisely

`observables.live_episode` decides "is an episode live?" by reading the latest
`lifecycle.started`, checking no later `stopped`, and **resolving the handle**
(`runstate/observables.py:live_episode` → `vocabulary/handle.resolve`).
`resolve` is deliberately **hostname-scoped**: a `local://host/pid` handle for
**another host** returns `None` (abstain), because probing the local pid table
for a foreign pid would answer garbage (`handle.py:resolve`;
[`../specs/lazy-launch.md`](../specs/lazy-launch.md)). And `live_episode` treats
an abstaining probe as **live** (`if resolve(handle) is False: return None` —
only a *definitive* dead demotes it; `None` does not).

So on a foreign host, a `started` with no `stopped`/`terminated` following it and
an unresolvable handle reads **live forever**. If that foreign episode **crashed**
(no clean `stopped`, no reaper to write `terminated`), the run is wedged:

- **`ensure`'s producer gate blocks indefinitely.** `ensure` composes a
  foreign-episode handle whose `is_alive()` re-reads `live_episode`
  (`memoizer.py:_ForeignEpisode.is_alive`); with `live_episode` stuck live, the
  `while handle.is_alive(): sleep` loop in `ensure` never returns
  (`memoizer.py:ensure`). The reuse-by-`run_id` use case the whole
  cross-host effort validates is exactly where this bites.
- **`ensure_served` / `relaunch_if_needed` never wake a replacement.** Both
  return `None` (no spawn) when `live_episode(channel) is not None`
  (`launcher.py:ensure_served`, `relaunch_if_needed`), so the crashed foreign
  service is never re-woken.
- **The birth-CAS pre-check abstains too.** `Worker.__init__` checks
  `live_episode` before claiming; a would-be replacement on a third host reads
  the crashed episode as live and sets `_lost` without claiming
  (`worker.py.__init__`).

Two boundaries are **by contract, not by accident**:

- **The heartbeat ◊P detector is the floor for *observation*, not the claim.**
  A `Watcher` with a `heartbeat_timeout` catches this crashed episode as
  `presumed_dead` (`watcher.py` tier 4) — but that is a *reversible observation*
  (a dashboard verdict), never the *irreversible* authority to re-claim and
  re-run (§4.6 of the [implementer's guide](../implementers-guide.md); design §8).
- **sqlite-on-NFS stays conservative single-host by contract.** SQLite's POSIX
  byte-range locks are unreliable on many NFS mounts, so the birth-CAS can admit
  two winners; single-writer-per-run is **REQUIRED** on NFS
  (`sqlite.py:SqliteChannel` docstring). NFS is *out of contract* for
  multi-claimant cross-host, so it is not what a fix targets.

The "stuck" behavior is **safe** (no double-run, no corruption) but **wrong**
(a healthy replacement is refused a dead run's slot). The question is whether to
trade some of that safety for liveness, and how.

---

## 2. The refuted path (and why it stays refuted)

The tempting fix — **observe-then-claim / heartbeat-as-claim-detector** — is a
**dead end** ([`../dead_ends/failure-detector.md`](../dead_ends/failure-detector.md),
REFUTED 2026-06-24 by a four-angle red-team, verified against the code). Its
prescription: a claimant watches the foreign heartbeat tip on its own clock and
claims a silent incumbent via the birth-CAS, bounded by a per-tick supersession
check.

The root error, one line: **it used the *weakest* detector
(heartbeat-staleness inference) for the *highest-stakes* decision (the
authoritative episode claim), where being wrong is catastrophic.** The specific
diagnoses that keep it dead:

1. **Double-live *used to* permanently poison reuse — strictly worse than the
   bug it fixes.** A wrongly-suspected slow foreign worker, claimed over, then
   reviving, could emit a divergent `value` at the same step; at the time of the
   red-team, `history()` **raised** `divergent re-emission … reuse would be
   unsound` — **sticky** on the append-only log, so *every future*
   `ensure`/`history` raised forever. (This specific stickiness is what G1 has
   since removed — §3 — but the *double-live* it was reacting to is a separate,
   still-real hazard.)
2. **The hazard is intrinsic to cross-host claim-on-inference**, not to that one
   mechanism: cross-host you have only inference (staleness), which can be wrong.
   Any claim-on-inference fix reopens it.
3. **NFS irony**: observe-then-claim is a multi-claimant protocol, exactly what
   the sqlite-NFS caveat forbids; on the motivating deployment the CAS it leans
   on can admit two winners outright.
4. Galaxy-scale is a CLAUDE.md non-goal, and the "collapse three liveness paths"
   claim deletes ≈0 lines.

**What survives and is kept:** liveness is a *detector*, not truth; the claim
gate needs *definitive* evidence (a record, a sound same-host probe, or a
**connection-oriented backend's lock**) — not inference. The bug diagnosis
(`live_episode` goes blind off-host) is correct; the sound path below is where
the live work goes.

---

## 3. What has shipped since, changing the landscape

Three things landed after the dead-end that a spec now gets to build on:

- **The Postgres episode lock — a *definitive* cross-host liveness signal**
  ([`../specs/channel-postgres.md`](../specs/channel-postgres.md) "Liveness").
  After winning the birth-CAS, the worker's channel connection takes a
  **session-scoped** `pg_advisory_lock` keyed on the episode
  (`hashtextextended("{len}:{run_id}:{started_seq}", 0)` — §4.7 of the
  implementer's guide); worker death drops the connection → the lock
  auto-releases. An observer reads it read-only via `pg_locks`. **Today it is a
  *Watcher-consumed* signal** (`EpisodeProbe`, folded in `watcher.py` as tier 3b
  with a birth grace + a staleness floor-veto), **never a claim arbiter.** This
  is the "definitive cross-host oracle via the `resolve` seam" the refutation
  said the claim gate needs — it now exists, but wired to the *observation*
  plane, not the *claim* plane. Wiring it into the claim gate is the deferred
  **co-arbiter** (§4).
- **G1 — take-the-latest makes the value plane non-poisoning** (the **named
  prerequisite**;
  [`value-plane-divergence-resolution.md`](value-plane-divergence-resolution.md),
  SHIPPED 2026-06-27). `history` now **collapses take-the-latest by `seq`** and
  the sticky divergence raise is **deleted** (`memoizer.py:history`), so a
  residual double-live that emits a divergent `(name, step)` pair no longer
  *permanently* poisons reuse — the later record wins and reuse proceeds. This
  converts diagnosis #1's *irreversible* corruption into a *bounded, recoverable*
  one. G1 is explicitly the "value-plane robustness the cross-host claim gate
  needs" (its own status line says so).
- **Launch identity + dated records** (`launcher`-v0.3 →
  [`../specs/launcher-record-identity.md`](../specs/launcher-record-identity.md);
  `lifecycle`/`launcher`-v0.4 → [`../specs/observer-clock.md`](../specs/observer-clock.md)).
  Every launcher record now names its launch (a death can no longer forge a live
  episode's verdict), and every dated record carries a `t`. A claim-model
  redesign now has **correlation** (which episode a death belongs to) and a
  **freshness clock** to reason with — inputs the dead-end did not have.

Net: the *detector* the refutation demanded exists (the Postgres lock), and the
*value-plane blast radius* of a residual double-live is now bounded (G1). What
remains open is whether — and how — to let that detector arbitrate a **claim**,
which is a change to the claim model itself.

---

## 4. The two candidate designs (from channel-postgres "Deferred")

Both are named in [`../specs/channel-postgres.md`](../specs/channel-postgres.md)
"Deferred / rejected-for-now" as a **deliberate claim-model redesign, specced and
reviewed on its own**. Neither is endorsed here; each is analyzed against the
invariants it would touch.

### 4.1 Candidate A — the lock-before-CAS co-arbiter

Make the episode lock a **claim gate**: take it **before** the birth-CAS, so a
dead foreign run (lock auto-released) is re-claimable, and a live one is not.

What it touches:

- **`worker.py` claim logic.** The birth becomes lock-*then*-CAS instead of
  CAS-then-`hold_episode`. The lock must now be **run-keyed** (not
  episode-keyed): you cannot key on `started_seq` for an episode that does not
  exist yet. That is a different lock than the shipped episode lock, with a
  different lifecycle.
- **`retire`'s death-CAS.** Today the careful death CAS's the dying breath
  against the drained log (`worker.py:retire`), and the episode lock releases
  passively on connection close. A claim-gating lock needs an **explicit
  release-on-stop** ordered against the death-CAS — a new ordering hazard (release
  before the CAS commits and a racer claims mid-death; release after and a clean
  stopper holds the run longer than its episode).
- **`observables.live_episode`'s conservatism.** Today it abstains-live off-host
  by design. A co-arbiter would make the *lock* authoritative for Postgres,
  splitting `live_episode` into "backend has a definitive signal" vs "backend
  abstains" — the backend-agnostic fold stops being backend-agnostic for the
  claim.
- **run-episodes' invariant "the CAS is the guarantee"**
  ([`../specs/run-episodes.md`](../specs/run-episodes.md)). Today single-spawn is
  *the CAS, alone*; the lock is a signal. A co-arbiter makes the **lock** part of
  the guarantee — two arbiters where the design has one. This is the single
  biggest conceptual cost: the channel-postgres "one design principle" (claim =
  the uniform CAS; liveness = a poset the Watcher combines) is exactly what a
  co-arbiter breaks.
- **The documented Postgres hazards get *promoted* from annoyance to
  correctness bug.** The spec already documents: a **transaction-mode pooler**
  (pgbouncer) breaks the session lock (self-checked, raises); a **hard partition**
  releases the lock only when TCP keepalive fires (needs server-side
  `tcp_keepalives_*`); an **idle-reap** (`idle_session_timeout` / NAT drop) on a
  long-step worker false-releases the lock. Today those cost "a wasted verdict,
  never a double-live — the lock isn't a claim gate." As a **claim gate** each of
  them becomes a **double-claim** path: a false-release lets a second worker claim
  a live run. The idle-reap edge is the sharpest — a legitimately long single step
  (the dead-vs-busy ambiguity, design §8) would drop the lock and invite a
  double-claim of a perfectly healthy run.
- **The sqlite backend has no lock capability.** `SqliteChannel` is not an
  `EpisodeHolder`/`EpisodeProbe`. A co-arbiter that the claim path *depends* on
  either (a) leaves sqlite with the old CAS-only claim (a two-tier claim model —
  the guarantee differs by backend, which the uniform-claim principle forbids) or
  (b) forces every backend to grow a lock capability (opinion creep into the
  substrate). Neither is free.

### 4.2 Candidate B — the Watcher-driven force-claim

Leave the birth path alone; add a **separate, explicit** force-claim: a
Watcher/operator that has *definitively* observed a foreign episode dead (the
episode lock released past the birth grace → `presumed_dead`, `watcher.py` tier
3b) **records** that verdict and thereby unblocks re-claim.

What it touches:

- **A new terminal-plane record** ("this episode is reaped/evicted"), so that
  `live_episode` / `peek_terminal` see a *record*, not an inference — keeping the
  irreversible re-claim gated on a record-plane fact (observer-clock §4). This is
  more aligned with the design's grain (the player model: the only shared
  liveness facts are records an actor wrote by *acting* —
  `dead_ends/failure-detector.md` "what survives"). But it introduces a **new
  eliminator** for `lifecycle.started` beyond `stopped`/`terminated`, which the
  intro/elim discipline ([protocol-algebra](protocol-algebra.md) L2) says must be
  designated deliberately (a `lifecycle.evicted`? a `launcher.terminated` written
  by a non-parent? each has a cost).
- **Who is allowed to write it, and on what evidence.** The force-claim's
  authority is the Postgres lock (definitive) — but the record then lives on the
  log for backends that have no lock, so a reader on any backend trusts it. That
  is fine (it is a record), but the *writer* must have had the lock evidence, and
  nothing on the log proves that. This is the design's hardest open question for
  B (§6).
- **It still leaves sqlite-NFS out.** B's evidence is the Postgres lock, so
  sqlite-NFS stays conservative single-host (correctly — it is out of contract).
  B does **not** pretend to fix NFS, which is a point in its favor over any
  inference-based scheme.
- **`retire`/`worker.py` are largely untouched** — B does not change the birth
  or death CAS; a forced-claim run just has an extra terminal record before the
  new `started`. The blast radius is the observer/verdict plane, not the claim
  plane. This is B's main advantage: it does not make the lock a second arbiter.

### 4.3 The two, contrasted

- A puts the definitive signal **in the claim** (fewer records, but two arbiters,
  the hazards become double-claims, and sqlite splits off).
- B keeps **one arbiter** (the CAS) and adds a **record** the claim already knows
  how to read (the design's grain), at the cost of a new eliminator and an
  authority-to-write question.

Neither is needed for the **motivating** channel-postgres use case (a BO launches
a *fresh* trial; a dashboard *reads*) — which is why both are deferred. The
trigger is a genuine **cross-host auto-relaunch** need (re-claiming a dead foreign
run), which has not yet appeared.

---

## 5. The residual double-live under G1 (bounded corruption vs detection)

Any cross-host claim-liveness scheme that acts before a *definitive* death is
observed admits a window where two workers briefly co-run. G1 changes what that
costs:

- **Before G1:** a divergent `(name, step)` pair from the two live workers made
  `history` raise **forever** — irreversible, and on the reuse path. This was the
  fatal objection.
- **After G1:** the pair is resolved **take-the-latest by `seq`**
  (`memoizer.py:history`, matching `value_series`), so reuse proceeds with the
  later (continuing-branch) value. The corruption is **bounded** (one step's value
  may be from the wrong attempt for the overlap) and **recoverable** (a later
  correct emission supersedes it). G1's own reachability argument shows that for
  every divergence reachable *through `ensure`*, take-the-latest returns exactly
  the value an "authoritative attempt" rule would
  ([value-plane-divergence-resolution.md](value-plane-divergence-resolution.md)).

So the design tension sharpens to: **detection vs bounded corruption.** A
*definitive* signal (Candidate A/B via the Postgres lock) means **no** double-live
in the first place; if a scheme instead tolerates a bounded double-live (relying
on G1), it must argue the bound is acceptable **and** that no *silently-wrong*
case is reachable in contract (G1's residual: silently wrong only for a *direct*
`relaunch_if_needed` on a `completed` run re-running finished steps
non-deterministically, or NFS double-live — both out of contract). This is the
central owner tradeoff (§6).

---

## 6. The test surface a spec would need

Any spec here graduates only with its own adversarial pass; the test surface it
must cover:

- **Cross-host honesty caveat.** With one CI Postgres, the `cross_host` tier is
  *multi-client to one server, not multi-host* (channel-postgres "Tests"). The
  genuinely cross-host property is tested on one host by seeding a
  `local://OTHER-HOST/<pid>` handle (`resolve` abstains) and driving the lock
  from a separate connection. A claim-gate spec extends this: seed a **dead**
  foreign episode (lock released), assert the gate now permits a re-claim; seed a
  **live** one (lock held), assert it refuses.
- **The pooler / partition / idle-reap edges as double-claim tests** (for
  Candidate A): a transaction-mode pooler in the path must **fail loud**, never
  silently double-claim; a simulated idle-reap on a long-step worker must not let
  a second claimant in (or the spec must accept that it can, and say so).
- **The release-ordering race** (Candidate A): `retire`'s death-CAS vs the
  explicit lock release — a subscribe/claim racing the death must not be
  orphaned *and* must not double-claim.
- **The authority-to-write test** (Candidate B): a forced-claim record written
  *without* the lock evidence must not be trusted / must be impossible to forge
  from a backend that has no lock.
- **The G1 non-stickiness regression** (both): a residual double-live emitting a
  divergent pair must leave `history`/`ensure` **recoverable** (the pinned
  `test_history_collapses_re_emission_taking_the_latest`), never sticky.
- **sqlite-NFS stays refused** (both): the spec must assert it does **not**
  silently enable multi-claimant NFS.

---

## 7. Open questions for the owner

Nothing below is ruled; these are the decisions a spec must force.

1. **Is there a real cross-host auto-relaunch need yet?** Both candidates are
   deferred precisely because the motivating use case (fresh BO trials;
   read-only dashboards) does not need re-claiming a dead foreign run. Ship
   nothing until a concrete consumer needs it (the channel-postgres position).
2. **A vs B — put the definitive signal in the claim (A) or in a record (B)?**
   A is fewer records but two arbiters + the hazards-become-double-claims cost +
   the sqlite split; B keeps one arbiter and the design's record-grain at the
   cost of a new `started` eliminator and an authority-to-write question. B looks
   more aligned with "time never arbitrates an irreversible decision; gate it on
   a record-plane fact" (observer-clock §4) — but that is a recommendation to
   weigh, not a ruling.
3. **How much residual double-live is acceptable given G1?** If a scheme tolerates
   a bounded double-live rather than preventing it, the owner must accept the
   bound and confirm no silently-wrong case is reachable in contract (§5).
4. **What is the sqlite backend's story?** Explicitly two-tier (postgres gets
   cross-host auto-relaunch, sqlite stays single-host-by-contract), or force a
   lock capability onto every backend (opinion creep)? The uniform-claim
   principle pushes toward "two-tier, and say so loudly."
5. **Does the lock stay episode-keyed, or become run-keyed for A?** A
   claim-gating lock cannot key on a not-yet-existing `started_seq`; a run-keyed
   lock is a different object with a different lifecycle than the shipped episode
   lock — and a second interop constant a non-Python implementation must
   reproduce.

## 8. The trigger has fired (2026-07-29) — a field-built Candidate B, and a decomposition

**Fact, not proposal.** The revival trigger below is met. `GeoffChurch/mycooc` needs
cross-host re-claim and has built one: `scripts/reclaim_experiment.py` is Candidate B —
definitive out-of-band evidence, then a record — living outside the library because there
was nothing here to use. It cost ~20 GPU-hours before it existed (two jobs, 8h + 12h, zero
cells) and the failure recurred three times.

It deviates from §4.2 in two ways this entry never priced:

- **A third authority class.** §4.2 assumes the Postgres advisory lock as the evidence.
  mycooc is sqlite-on-NFS and its evidence is **scheduler accounting** (`sacct` terminal
  state). Neither the lock nor a probe; a fourth party's ledger.
- **No designated eliminator, so it impersonates.** §4.2:215 says a new eliminator "must be
  designated deliberately (a `lifecycle.evicted`? a `launcher.terminated` written by a
  non-parent?)". None was, so it writes `lifecycle.stopped` — the only eliminator that
  exists — i.e. it forges the worker's dying breath. That forgery is **forced, not sloppy**:
  `live_episode` reads only a later `stopped` and a `resolve()`-dead handle, so no death
  record on the launcher plane can release a claim, however well correlated (verified;
  now stated in `live_episode`'s docstring).

### 8.1 A field-tested rule: a heuristic may VETO, never AUTHORISE

mycooc's `correlation_refusal` gates on *(claim host ∈ job NodeList)* ∧ *(claim `t` ∈ job
window ± 300 s)*, failing closed on any missing fact. Its premise is right and belongs in
any spec here: **terminal-ness alone authorises nothing — it says a job died, not that THIS
claim was its doing.**

Its correlator is not sound, and its own logs are the disproof: 9 different-experiment
overlaps on shared nodes, the longest 3.75 h. A *(node × window)* rectangle is not
injective — backfill starts the next job on a freed node in seconds, a multi-node job's id
covers every one of its nodes, and short-name matching collapses `n07.clusterA` with
`n07.clusterB`. mycooc has accepted this and demoted the check to a **pre-filter that can
refuse but never authorise**. That rule generalises past SLURM and is the most durable
artefact this exchange produced.

### 8.2 Proposed decomposition of §7's hardest question — UNTESTED, attack before use

§4.2 calls "who may write it, and on what evidence" the design's hardest open question.
Three propositions are being conflated in it:

| | question | structure |
|---|---|---|
| **Strength** | how strongly does this show the run is dead? | the existing liveness **poset** (`channel-postgres.py`: "liveness = a poset the Watcher combines"); combines by **join** — take the strongest tier, fall back gracefully |
| **Relevance** | is this evidence about *this claim at all*? | an **admissibility gate upstream of** the poset; combines by **meet** — every fact must agree, a missing one refuses |
| **Authority** | may this party record it? | **not an order.** Nothing on the log can rank it; the log cannot prove a writer consulted `sacct` rather than guessing |

Consequences if the decomposition holds:

- **Relevance can only subtract**, which is why 8.1's veto/authorise rule is forced rather
  than chosen: establishing that evidence is *about* a claim says nothing about its
  strength. Admission grants standing, not weight.
- **Correlation must NOT become a fifth liveness tier.** A meet-combining gate placed inside
  a join-combining poset can be out-voted by a weaker tier — the same shape as the
  already-rejected "a record overrides a definitive probe" (pinned by
  `tests/test_observables.py::test_a_death_record_never_revokes_a_claim_whose_probe_says_alive`).
- **Stop trying to prove authority; record aim instead.** An eviction record can name the
  claim it evicts, and a reader can check the named claim is the claim present. That is not
  proof of good evidence, but it makes a wrong eviction *attributable* rather than anonymous
  — the same move `launcher-record-identity.md` made for deaths.
- **Add atomicity to aim.** `send(expected_seq=)` makes the eviction land on exactly the
  claim that was inspected, or not at all — closing the window between reading liveness and
  writing the release (mycooc's tool spans it with a 60 s `sacct` shell-out).

The two orders agree on one thing worth keeping: **⊥ never authorises an irreversible act.**
`resolve()` maps abstention to conservatively-alive; the relevance gate maps unknown to
refuse. Different orders, same discipline at the bottom.

*Status: 8.1 is field-tested and its disproof is measured. 8.2 is a framing produced in
review and NOT yet adversarially tested — in the same review five confident structural
arguments were falsified by measurement, including two of the reviewer's own. Treat it as a
decomposition to attack, not a ruling.*

## Revival trigger

Revisit when a concrete consumer needs **cross-host auto-relaunch** (re-claiming a
dead foreign run) — the named trigger in channel-postgres "Deferred." Until then
the shipped posture holds: the Postgres lock is a Watcher-consumed *observation*
signal, the CAS is the sole claim arbiter, and sqlite-NFS is conservative
single-host by contract.
