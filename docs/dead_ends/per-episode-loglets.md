# Dead end: one loglet per episode

**Status:** REFUTED 2026-08-05, measured. The third point on the log-splitting axis, written to be
killed, and it died on a dilemma neither of its neighbours faced. **What survives is real and is not
segmentation** — see *Where it goes instead*.

## What it proposed

Each episode writes into its own **loglet/segment**; a run's log is the ordered concatenation. The
substrate grows a generic `open_segment()` alongside `send`. Three claims:

1. `(episode, seq)` is a **total** order — episodes are creation-ordered where machine partitions
   are concurrent — so it dodges the partial-order gate holding `../backlog/machine-partitioned-logs.md`;
2. opening a segment is not an append, so it supplies the **fencing asymmetry**
   `../specs/write-authority.md` says runstate lacks;
3. it fixes the value plane's ordering defect structurally.

## Why it died: the dilemma

**Claim 2 is false in both possible implementations, and they are the only two.**

**Literal form — per-segment `seq`.** This destroys the birth CAS. Measured: two workers open
different segments, each compare-and-appends against *its own* frontier of 0, and **both claims
win**. A CAS arbitrates only writers who share a frontier. Not an implementation detail —
`channel/postgres.py` says it in the module docstring: *"`PRIMARY KEY (run_id, seq)` is the CAS
arbiter."* Adding `segment` to that key is exactly what breaks it. It is also log-forking's Dolt
refutation verbatim (`log-forking.md`): per-branch counters collide.

**Label form — global `seq`, `segment` as a field.** Then `open_segment()` allocates nothing the
claim's own `seq` does not already allocate. Measured side by side — same contract, same guarantee,
same failure mode:

```
send(lifecycle.started, expected_seq=last):  A -> epoch 1 | B stale -> None | B fresh -> epoch 2
open_segment(expected_max=...):              A -> epoch 1 | B stale -> None | B fresh -> epoch 2
```

`latest_episode(ch).seq` **is** the monotone epoch, and has been since run-episodes. The primitive
fails the rubric's Independence criterion outright — it is `send(expected_seq=)` + `last_seq()`
wearing a new name.

**And the fenced variant is worse than today.** Reject appends below the max segment, and an
anonymous third party calling `open_segment()` **permanently and silently mutes the live worker**
while adding **zero records to the log**. Today's forged claim at least writes a `lifecycle.started`
that `live_episode` and `peek_terminal` can read. That is rev-2 refutation 4 made *traceless*, and it
is a unilateral AUTHORISE, which `../backlog/cross-host-claim-gate.md` §8.1 forbids by name after
field testing.

**Why the asymmetry cannot be manufactured.** Kafka's `InitProducerId` is safe not because it is a
different *kind of operation* but because it is served by a different *kind of party* — a broker
that issues the epoch and enforces it on the write path. Renaming an append does not create a
broker. At the storage layer `open_segment()` **is** an append; the only delta is that the substrate
now knows what the record means, which `../specs/channel-postgres.md` forbids by name.

## The frozen design had already ruled

`../design-v0.2.md`, under the one-authoritative-sequencer premise:

> **The causal regime — spacelike writers, no global order — is a different protocol, not a later
> version of this one.** L2's folds are positional … so retrofitting causal ordering rewrites the
> conventions, not merely the substrate. Saying that is more useful than calling it deferred, which
> implies a roadmap it does not have.

## Three findings worth keeping

**The discriminator is worse than the gate it dodges.** A partial order is *incomparable* — you know
you cannot compare. `(segment, seq)` is a **total order that disagrees with append order**:
comparable, and silently wrong. "Total" was never the property the positional rules need; *agreement
with the order in which facts became true* is, and global `seq` has it by construction because it
**is** the single serialization point.

**3 of the 4 positional rules change meaning; one breaks.** Measured: a `control.stop` issued
between episodes into an older segment is **born already discharged** — consumed by a `stopped`
written before it in wall clock — so cross-episode control delivery silently drops the command. Two
rules change in the *right* direction, which is the tell: both are position misattributing a
displaced worker's record, and both are fixed by correlation without touching order.

**Blast radius, measured.** **12 of 12** `.read()` sites in `runstate/` consume order — there is no
order-agnostic read in the library; 290 of 393 tests depend on it transitively. `read(after=N)`
degrades from an integer-primary-key seek to a scan plus a temp B-tree, and
`protocol/envelope-v0.2.schema.json` pins `seq` as *"a per-log monotonic total order."*

## Where it goes instead — correlation, not segmentation

The defect the proposal found is **real and reproduced**: `history` and `value_series` resolve a
duplicate `(name, step)` by **global `seq`**, so a displaced worker writing later in wall clock than
the newer episode **wins the cell**, and `ensure` then returns a spliced series with no re-drive and
no error.

The proposal's own named cheap alternative — resolve by episode, read-side — **does not work**,
because read-side attribution is positional and the late write sits after the new claim. Measured:
still broken.

What does work, measured: **stamp the record with the writer's own claim `seq`** — which the worker
already holds — and resolve by that. Zero substrate change, zero reordering, no new primitive. This
is a pattern the repo already ships one tier up; `observables.py`'s launcher-death correlation:

> Position cannot do this job: a reap is a reader-side observation that lands arbitrarily late …
> Both forgeries die by construction here.

Live work: `../backlog/episode-correlation.md`.
