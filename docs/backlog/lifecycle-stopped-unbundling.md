# `lifecycle.stopped` does five jobs; two parties now want one each

**The observation.** `lifecycle.stopped` releases the claim, declares the verdict, reports the step
frontier, discharges pending `control.stop`s, and dates the run's freshness. It is the only record
that does any of them, so anyone who wants *one* must assert all five.

`claim-eviction.md` unbundles job 1 (claim release) and argues that case at length. This
entry exists because a **second** party wants a **different** single job, which the eviction design
explicitly refuses to give it — and two instances is the point at which "one record per need" should
be checked against "unbundle the bundle."

## The second instance

mycooc's `run_experiment.py::resume_fanout` forges a `lifecycle.stopped` because it wants **job 4
only** — discharge the pending `control.stop`s so a resumed worker does not immediately stop again.
It does not want the claim released, has no verdict, and knows no frontier.

The eviction record cannot serve it, **by design and correctly**: refusing to discharge stops is
precisely what fixes #39 (a third-party reclaim must not silently destroy an operator's halt). The
property that fixes one consumer strands the other.

## The question this raises

Three shapes, and the choice is not obvious:

1. **One eliminator per job, minted on demand.** The eviction record, then a discharge record, then
   whatever the next consumer needs. Each is small and each is defensible on its own. Risk: five
   near-identical records and a fold that grows a case per record —
   `specs/service-worker.md`'s standing objection (*"a second counter-record kind for one fact — the
   fold grows a case, the lifecycle schema bumps, and canonical form loses"*) compounds each time.
2. **One record with an explicit job set.** A single third-party record naming which of the five it
   asserts. Fewer topics; but readers must branch on the set, and a record that asserts *nothing*
   becomes expressible, which is a new junk case.
3. **Stop at one and treat this as coincidence.** Two data points. Job 1 is genuinely special —
   it is the only one a third party can *ever* establish on its own — so unbundling it may not
   generalise at all.

## What would settle it

- **The corpus says wait.** Measured across 1,933 openable logs in four repos: the job-1 case
  (#39) fired **6 times, in 2 runs, from one experiment and one tool**, and the whole corpus holds
  49 `control.stop` records. `claim-eviction.md` is deferred on exactly that evidence. If the
  motivating instance is one tool we own, the second instance being *another* tool we own is not a
  pattern either — it is the same tool-shaped problem twice.
- **A third instance.** Whether a consumer ever wants job 2, 3, or 5 alone. If none does in
  practice, (3) is right and this entry should be deleted.
- **Whether job 4 has a legitimate third-party author at all.** Job 1 does — a stranded claim
  protects nothing, so releasing it needs no licence. Is that true of stop-discharge? Cancelling an
  operator's halt on their behalf is a *stronger* act than releasing a dead claim, and #39 exists
  because doing it accidentally is a real harm. `resume_fanout` may be asking for something it
  should not have, in which case the right answer is a change in mycooc, not a new record.

That last point is the one to resolve first: it is cheap, it is answerable by reading one consumer,
and if it lands the way it looks, this entry closes without a protocol change.

## Related

- `claim-eviction.md` — job 1, the design in flight
- `../specs/stop-discharge.md` — the positional rule job 4 implements
- `protocol-algebra.md` L2 — intro/eliminator discipline, and the standing bar for a new eliminator
