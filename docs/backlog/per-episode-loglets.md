# One loglet per episode — PROPOSAL TO BE KILLED

**Status:** new, unattacked, and deliberately written to be refuted. It is a third point on an axis
whose other two are already settled — `../dead_ends/log-forking.md` (fork on resumption: REFUTED)
and `machine-partitioned-logs.md` (partition by writing machine: open, gated) — and it appears to
dodge the objection that killed the first and the gate that holds the second. That is exactly why it
should be attacked rather than adopted.

## The proposal

Each **episode** writes into its own **loglet**. A run's log is the ordered concatenation of its
loglets. The substrate grows a generic segment primitive — roughly `open_segment() -> id` alongside
`send(segment, …)` — and the convention layer opens one segment per episode.

## Why it is not the two refuted/gated neighbours

1. **Episodes are ordered; machines are not.** This is the discriminator. Two machine partitions are
   *concurrent* — no natural order between them, which is why `machine-partitioned-logs.md`'s gate is
   whether a **partial** order still carries a protocol whose intro/eliminator pairs are defined
   *positionally*. Episodes have a creation order by construction, so `(episode, seq)` is a **total**
   order lexicographically, and the gate does not apply. Per-episode is also *finer* than
   per-machine and subsumes it: each episode runs on one machine.
2. **It supplies the fencing asymmetry — allegedly.** `../specs/write-authority.md` records that
   fencing is unavailable because acquiring the claim *is an append*, so there is no un-fenceable
   acquisition operation to hang a token on. Opening a segment is a different *kind* of operation
   from appending to one — the `InitProducerId` / ZooKeeper-session shape. **§"Where it probably
   breaks" (1) is why this may be an illusion.**
3. **It would fix the value-plane splice structurally.** Today `history`'s take-the-latest resolves a
   duplicate `(name, step)` by **global `seq`**, so a displaced worker in episode 3 writing later in
   wall-clock than episode 4 **wins the cell** — the wrong one. Under `(episode, seq)`, episode 4
   wins by construction. That is #32's value-plane harm fixed by representation rather than policy.

And a property worth naming: **loglets do not fence, they segregate.** A displaced worker's writes
still land, in a now-superseded loglet. That is what `write-authority.md` concluded such writes
*should* be — *"honest records of what it did, never assertions of authority"* — made structural
instead of documentary.

## Where it probably breaks — attack in this order

1. **The asymmetry may be fake, and this is the crux.** Kafka's `InitProducerId` is safe because a
   **broker** — a trusted third party — issues the epoch. runstate has no broker; the substrate is a
   library over a file. If any party may call `open_segment()`, then any party may open segment 5
   and seize the epoch, which is the birth-CAS problem verbatim. Making segment creation itself
   compare-and-swapped just *is* the birth CAS, relocated. **If this holds, the proposal buys nothing
   on fencing and must justify itself on (3) alone.**
2. **It was already declined.** `../specs/run-episodes.md` Non-goals: *"Explicit episode-ids; a
   'done/sealed' marker; retention/GC."* And `observables.latest_episode`'s docstring says the
   boundary rule is *"named in the one place that changes if explicit episode markers ever land"* —
   so the repo anticipated this and chose read-side derivation deliberately. Recover that reason
   before overturning it; it may already be decisive.
3. **Opinion-freeness.** "A loglet per episode" puts episode knowledge in the substrate, which
   `../specs/channel-postgres.md` forbids by name: *"convention knowledge … stays in the worker,
   never the substrate."* The escape is a *generic* segment primitive the convention layer chooses to
   use per-episode — but is that genuinely opinion-free, or does it smuggle episodes in under a
   neutral word?
4. **L1.** A new substrate operation must justify itself the way the CAS did — contract and
   conformance tests included (`protocol-algebra.md` L1: `send`/`read`/`latest`/CAS is *complete*).
5. **Do the four positional rules survive lexicographic order?** They are defined by *"a standing
   fact's eliminator must follow it by `seq`."* Under segments, a record appended to loglet 3 *after*
   loglet 4 exists sorts *before* loglet 4's records. So a `control.stop` written late into a dead
   episode would be discharged by a `stopped` written earlier in wall-clock. Plausibly correct — the
   stop is in a dead episode — but it is a semantic change to every positional rule and none of them
   has been checked.
6. **Every fold changes.** `channel.read()` returns records in `seq` order today; ordering by
   `(segment, seq)` touches every consumer of that order, in three backends plus every fold.
7. **Migration.** Existing single-sequence logs are one implicit segment — probably cheap, but
   unmeasured, and `latest_episode`'s derivation would have to agree with the explicit markers on
   every existing log or the two disagree silently.

## What would make it wrong

If (1) holds — no trusted issuer, so segment creation is as forgeable as an append — then the
fencing claim collapses and this is a large substrate change bought entirely by (3), the value-plane
ordering fix. At that point compare it honestly against the far cheaper alternative: make
`history`'s take-the-latest resolve by *episode* rather than by global `seq`, which is a read-side
fold change with no substrate involvement at all.

That comparison is the one to run first. If the cheap read-side fix gets (3), the proposal has to
survive on (2) alone — and (2) is the part most likely to be an illusion.
