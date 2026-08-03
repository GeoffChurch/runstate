# Spec: backend capability tiers — surrender writer-liveness to the substrate

**Status:** PROPOSED, not converged. Supersedes the open half of
`../backlog/cross-host-claim-gate.md` if adopted.
**Origin:** eight consecutive attempts to answer "is this claim's holder still alive?" above the
storage layer, all refuted. The ninth observation is that the question is mislayered.

## The gap

`resolve()` is **32 code lines** of 2,271 in this library — 1.4%. Every open defect is downstream of
it:

| issue | what it is | reachable only when |
|---|---|---|
| #32 | a displaced worker forges a verdict, rewinds the frontier, revokes a live claim | the holder cannot be probed |
| #39 | a third-party release swallows a pending `control.stop` | ditto |
| #42 | a third-party release makes a dead run read fresh | ditto |
| — | the designated eliminator, and "who may write it, on what evidence" | ditto |
| — | the refuted heartbeat-staleness tier | ditto |
| — | the unruled handle-scheme ownership question | ditto |

They are not six problems. They are one problem, asked six ways: **the library tries to answer, from
a portable string, a question only the storage can answer** — because the storage is what holds the
writer's connection.

`resolve()` parses `local://host/pid` and asks the OS. Off-host it abstains, abstention reads as
alive, and a stranded claim is unreleasable. Everything above then becomes an attempt to synthesise,
from records and inference, a fact the substrate already has and is not being asked for.

The repo has said this twice, in its own words:

> the definitive cross-host oracle the refutation said the claim gate needs — it now exists, but
> **wired to the observation plane, not the claim plane**
> — `backlog/cross-host-claim-gate.md`

> the gap is a **backend** gap
> — `HANDOFF-2026-07-27-staleness-tier.md`

## The correction

Stop asking the library. **Declare the property, demand it of backends, and let the claim gate use
the strongest one available.**

This is a layering correction, not a backend choice. Postgres satisfies the property today with no
change to Postgres; a more careful sqlite backend could satisfy it too; a backend that cannot
declares so and degrades explicitly rather than silently.

### Why it was placed above the storage originally, and why that reason no longer binds

Because the claim had to work on **every** backend, so the arbiter had to be the one thing all
backends have — the CAS. That is sound, and it stays sound: *safety* remains uniform. What was
wrongly made uniform is *liveness*, which is not a safety property and does not need to be answered
identically everywhere. A backend that cannot answer it should be slower to recover, not incorrect.

## The tiers

**P1 — ordered append.** Every backend. A total order over a run's records.

**P2 — compare-and-append.** Every backend. `send(expected_seq=)`. This is the claim, and it is the
whole of *safety*: at most one claimant at the instant of claiming, on every backend, forever.
Nothing below may weaken it.

**P3 — writer-liveness oracle (opt-in).** "Is the holder of claim *S* still connected?" To be
admissible as a claim arbiter it must be:

1. **session-bound** — tied to the writer's own connection, not to any record the writer wrote;
2. **self-releasing** — released by the holder's death with no cleanup action by anyone;
3. **definitive** — answers true or false, never "unknown". A backend that may abstain does not have
   P3; it has nothing;
4. **globally visible** — every reader, on every host, gets the same answer for the same claim.

Postgres advisory locks satisfy all four. A local sqlite `flock` satisfies 1–3 and fails 4 across
hosts. `resolve()` satisfies none of them off-host, which is the whole story.

**P4 — write fencing (opt-in).** A superseded claimant's writes are **rejected by the store**. P3
alone is not sufficient: a lease can always false-release — a GC pause, a network blip — and a false
release under P3 admits a second writer to a live run, which an append-only log cannot undo. P4 is
what makes that survivable.

A conjectured cheap form, unverified: if a write requires holding the claim's lock **in the writing
session**, fencing is automatic and no token is needed. Whether that is implementable without
per-write cost is open (§ Open questions).

## Both mechanisms already exist

This spec adds no machinery. It uses two things already shipped:

- **Capability Protocols.** `channel/base.py` defines `EpisodeHolder` and `EpisodeProbe` as
  `runtime_checkable`, isinstance-dispatched, "split by VIEWPOINT so a pure observer's channel type
  never advertises a method it must not call." `PostgresChannel` implements both. The single change
  this spec proposes is to their **standing**: today they are a signal the Watcher consumes and
  "never a claim arbiter"; under P3 they arbitrate when present.
- **Tier-gated conformance.** `tests/test_concurrency.py` already gates on a declared backend tier
  (`@pytest.mark.tier("cross_process")` / `("in_process")`). P3 and P4 become two more tiers, and a
  backend's claim to satisfy them is pinned by tests it must pass rather than by documentation.

## What dissolves

With P3 present, a claim's holder is *observed*, never inferred:

- **#32, #39, #42** — all three require a claim whose holder cannot be probed. There is no stranded
  claim, so no third party ever needs to release one, so nothing is forged, swallowed, or dated.
- **The designated eliminator**, and with it "who may write it, on what evidence" — the hardest open
  question in the backlog. There is nothing to eliminate.
- **The heartbeat-staleness tier** — already refuted; now also unnecessary.
- **The handle-scheme ownership question** — `local://` remains a P3-less fallback, and nobody needs
  to teach it new schemes.
- Downstream, roughly **470 lines** of consumer workaround that exist only because the claim cannot
  be probed (a reclaim tool, a malformed-record repair tool, a synchronous-handle backstop, a
  terminal-since guard).

## What does not dissolve — state plainly

- **P3 without P4 still admits a second writer on a false release.** Smaller and rarer than today's
  failure, but the same *kind*. Do not present P3 as complete.
- **Side effects are not fenced.** Workers `rmtree` and rewrite shared artifact paths; the store
  cannot help. This is why the epoch-as-artifact-path-component idea survives independently of
  everything here.
- **A P3-less backend keeps every defect above.** They become documented properties of a degraded
  mode, not bugs — but they remain real for anyone on sqlite-over-NFS, which is the current
  production deployment.
- **`ensure`, the folds, the vocabulary and the condition algebra are untouched.** They are
  substrate-independent and are where the design's value is.

## What gets deleted

Only if a P3 backend becomes the supported deployment, and only then:

- `vocabulary/handle.py`'s probe (32 lines) and its callers' abstention handling;
- the heartbeat-staleness tier in `watcher.py`;
- the `local://` scheme grammar and the deferred multi-scheme registry;
- `backlog/cross-host-claim-gate.md`'s open half.

Everything else stays. This is a **subtraction** from runstate, which is the point: the library
stops trying to know something it structurally cannot.

## Open questions

1. **Is P4 implementable without per-write cost?** The "write requires the session's lock"
   conjecture is unverified. If it costs a round trip per append it is not viable on the value plane.
2. **Does a sqlite backend exist that satisfies P3?** Not over NFS — the repo already documents that
   POSIX byte-range locks there are unreliable and "single-writer-per-run is REQUIRED, not merely
   typical". Single-host, an `flock` held for the episode's life is plausible. Worth deciding whether
   to build, since it would keep the embeddable story intact for the local case.
3. **What happens at the boundary** — a run whose log lives on a P3 backend but whose worker cannot
   reach it? Does it refuse to claim, or fall back?
4. **Does the CAS remain the arbiter, with P3 only *permitting* a re-claim?** It should: P2 is
   safety, P3 is liveness. Spell out that a P3 "dead" answer never *grants* a claim, it only removes
   an obstacle to attempting one — the attempt is still a CAS that can lose.
5. **Does this reopen the workflow-engine question?** A consumer's own design chose to build its own
   run-graph substrate partly because an off-the-shelf engine's "lifecycle model would sit beside
   runstate's". If runstate's coordination moves into the backend, that argument weakens.

## What would make this wrong

If (2) shows no sqlite backend can satisfy P3 on a single host either, then "multiple backends" is
aspirational and the honest form of this spec is "runstate requires Postgres for correct
cross-host operation", which is a much larger claim about the project's identity and should be
argued as such rather than arrived at.
