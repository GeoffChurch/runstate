# Spec: the required backend core, and a declared capability antichain

**Status:** PROPOSED, not converged. **Revision 2** — revision 1 proposed promoting the Postgres
advisory lock to a claim arbiter; that is refuted below and the proposal has changed shape. What
survives is the diagnosis that something is mislayered; what changed is *which* thing.

## What revision 1 got wrong, recorded so it is not re-proposed

Revision 1 argued that **writer-liveness** was mislayered and should be surrendered to the backend,
with the advisory lock arbitrating the claim where available. Three refutations, in ascending order
of severity:

1. **The repo already forbids the move, in the spec for the very backend it relied on.**
   `channel-postgres.md`: *"the advisory lock is a **liveness signal**, not a claim gate… **Pushing
   liveness into the claim path is the one thing that breaks this layering.**"* And
   `HANDOFF-2026-07-27` line 424 already answered the layer question: *"**Q1 (layer). Neither.**
   … relocating the inference does not fix a gate that needs definitive evidence."*
2. **P3's "definitive — never unknown" condition is unachievable**, and is already violated in
   shipped code. A worker inside a legitimately long step is reaped by `idle_session_timeout` and
   reads dead — a *false death verdict on a healthy run*, where today the same event causes only a
   revocable stale reading. `hold_episode` runs *after* the CAS, so a live claimed episode reads dead
   inside that window; `watcher.py` papers over it with `episode_grace = 5.0`, which is exactly the
   species of wall-clock mechanism the promotion was sold as eliminating. And the advisory key space
   is one flat unnamespaced `int8` shared by every application on the cluster — runstate does not own
   it and cannot.
3. **The proposed cheap fence does not fence.** Revision 1 conjectured that requiring the claim's
   lock in the writing session gives fencing for free. Measured: the naive form *corrupts the log* —
   a failed check inside the aggregate's `WHERE` filters every row, `MAX(seq)` goes NULL, and the
   insert lands at **seq = 1**, rewinding the frontier, which is the exact #32 failure it was added
   to prevent. Worse, correctly formulated it still cannot fence: **a released lock is
   indistinguishable from a lock never taken**, so it admits every stale writer at precisely the
   moment the false release it exists to survive occurs. *Fencing needs a monotone epoch; a lock is
   a boolean.*

## The diagnosis that survives

Something **is** mislayered, and it is not liveness. It is **write authority**.

Issue #32 states it exactly: *"The CAS guarantees at most one claimant **at the instant of
claiming**; nothing extends that to write authority over time."*

Safety is *one writer over time*. The current required backend contract delivers only the first
instant of it. Everything else — the eliminator, the epoch carrier debate, the staleness tier, the
handle-scheme question, eight refuted fixes — is an attempt to reconstruct, above the storage, a
guarantee the storage stopped providing one instant after the claim.

## The proposal

**Required of every backend.** Not a ladder — a floor. Safety must be uniform, so nothing here is
opt-in:

- **ordered contiguous append** — a total order per run;
- **compare-and-append** (`send(expected_seq=)`) — at most one claimant at the claiming instant;
- **epoch-fenced append** — *new* — the store rejects a write whose declared epoch is behind the
  run's latest `lifecycle.started`.

The third is the missing half of safety. Its arbiter is the claim's `seq`, which is already on the
log, already monotone, and already contiguous by the substrate's own guarantee. Measured cost:

| backend | cost | plan |
|---|---|---|
| postgres | **1.09×** | Index Only Scan Backward, 5 buffers, 0.004 ms |
| sqlite | **0.99×** (free) | SEARCH USING COVERING INDEX |
| memory | free | an integer compare inside the existing lock |

It needs no session, no connection binding, no wall clock, and no liveness oracle. It survives the
holder's death *because the arbiter is a log record, not substrate state* — the property that
`time-lease-boundary.md` establishes as the cure for standing state that cannot be re-derived. A
third-party writer that holds no claim simply omits `epoch=`, exactly as it already omits
`expected_seq=`.

**Declared capabilities — an antichain, not a ladder.** The repo already retired the ladder framing:
`channel-postgres.md` records that the Watcher's two probes are *"an **antichain** (incomparable
preconditions)"* and that *"the earlier 'unique minimum / composes for free' claim is **retired**."*
A single-host `flock` backend would have a liveness probe and fail cross-host visibility; a
cross-host backend may lack name indexing. These are incomparable, so they are named, not ranked:

- **`WriterLivenessProbe`** — may vote **dead only**, never grants a claim. This is exactly its
  current standing (`EpisodeProbe`, "never a claim arbiter"), and revision 2 **keeps it there**. It
  is a recovery-latency optimisation, not a safety mechanism.
- **`ChangeNotify`** — push in place of polling (#16). Genuinely storage-only; a consumer cannot
  synthesise it cleanly.
- **`QueryPushdown`** — windowed/filtered reads evaluated by the store (#15).
- **`NameIndexed`** — see below.

**Declaration must be real.** Today "tiers" live in a dict in `tests/conftest.py` keyed on fixture
parameter names; nothing under `runstate/` knows about them, and a third-party backend cannot
declare anything — it raises `KeyError`. A capability set belongs on the Channel class, with the
conformance suite gating on what the backend declares.

## Ship independently: the `(topic, name, seq)` index (#19)

Measured on a 200k-row sqlite log. The plan is `SEARCH USING INDEX idx_log_topic_seq (topic=?)` in
every case — `name` is a post-filter:

| | today | with `(topic, name, seq)` |
|---|---|---|
| hit | 5.2 µs | 22.2 µs |
| **miss** | **28,506 µs** | **10.1 µs** |
| named-but-early | 29,820 µs | 5.1 µs |

A ~3000× fix on the miss path, and it makes `SELECT DISTINCT name` a covering-index scan, closing
the deferred metric-name picker in the same change. Uncontroversial, unrelated to everything above,
and sitting in the open-issue list.

## What must not move down

`ensure`, the folds, the vocabulary and the condition algebra are substrate-independent, and are
where this library's value is. Revision 1 asserted this and then violated it: a liveness answer lives
in a connection table that no fold can read, no cold reader can replay, and no second-language
implementation can reproduce. The epoch fence has the opposite property by construction — its
arbiter is a record on the log.

## Corrected numbers

Revision 1 overstated its own case; the corrections are smaller but the argument survives them.

- `resolve()` is **12** code lines, not 32 (the module is 57; 32 counted docstrings).
- The consumer workaround is **517 raw / 227 executable**, of which ~**396 raw / 170 executable**
  would actually die. Two of the four named are misattributed: the repair tool's *first* documented
  cause was a torn WAL-on-NFS write, and `_terminal_since` fixes an episode-scoping gap in
  `peek_terminal` that is runstate's own. All of it is one consumer's; the other three contribute
  zero.
- The dissolution claim was too strong on #39 and #42: PR #41's scan names **two** third-party
  writers — a reclaim tool *and* a parent-process crash backstop. A liveness probe removes the first
  only, and the discharge fold is author-blind *and* body-blind regardless.
- Of 7–8 stranded claims in the corpus, **one** is foreign-host.

## Open questions

1. **Does the epoch fence belong in `Channel.send`'s signature or a capability?** It is proposed as
   required, which means a signature change on every backend. Is that the right cost?
2. **What is the epoch for a writer with no claim** — a third-party `control.stop`, an observer's
   `control.subscribe`? Proposed: omit it, as `expected_seq=` is omitted. Confirm nothing then needs
   fencing that should have it.
3. **Does the fence interact with the birth CAS?** The claim itself is a write; it cannot be fenced
   against an epoch that does not yet exist.
4. **Is `WriterLivenessProbe` worth keeping at all** given #44 (the probe is not database-scoped) and
   the flat key space? It may be a recovery optimisation with a correctness footgun attached.
5. **Should `ChangeNotify` and `QueryPushdown` be specified here or in their own issues?** They are
   named for completeness; neither is designed.

## What would make this wrong

If the epoch fence cannot be expressed on a backend without per-write cost that the value plane
cannot absorb, the required tier stays as it is and write authority remains unsolved — in which case
the honest position is that runstate provides single-writer *at the claiming instant only*, and says
so in the API docs rather than implying more.
