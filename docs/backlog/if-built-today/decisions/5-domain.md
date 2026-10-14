# Decisions and retractions — [`../5-domain.md`](../5-domain.md)

What was tried in this layer, what was withdrawn, and why. Conventions in [`README.md`](README.md).

The node schema came from runstate's layer-2 walkthrough: proposed in its review
([`../../../review-2026-10-04-commits-and-lineage.md`](../../../review-2026-10-04-commits-and-lineage.md) §9 C2), adopted at §12 item 11, resting on the
layer-2 decisions of items 2–5. Its drafts were reviewed independently before commit, on 2026-10-13 and
2026-10-14. The forms withdrawn along the way are grouped below by topic, so that checking whether
something was tried means reading one section.

## Keys and names

**Keyed by run and step** (withdrawn 2026-10-13). One relation for every metric, `at(R, S, M)`, with `R`
the content-addressed run id and `S` the step. What a producer might vouch for depended on determinism:

- **Deterministic given the run's inputs:** every launch of `r` produces the same values, so a producer may
  vouch per step — *"0.31, and no other loss at step 61"*.
- **Not deterministic** (GPU kernels, say): two honest schemas. Key by episode, `at(E, S, M)` with
  `episode(E, r)`, and have a rule compare launches. Or keep `r` and never vouch, so that only *"every step
  has a loss"* settles and a disagreement is two values at one step.

A step names a position in *some* history, and which history is not in the key. Runstate measured the
consequence: a claim names an episode, not a history, so a rollback inside one claim leaves two values
under one key ([`../../identity-in-records.md`](../../identity-in-records.md) §3; [`../../lineage-graph.md`](../../lineage-graph.md)). Keyed by node — one
execution of one step — every node may vouch for its values whatever the determinism. The episode-keyed
schema is refined rather than lost, since a node is finer than an episode, and the coarse schema survives
as a view, `at_step`, where two siblings at one depth still read as a domain conflict.

**The step as a user stream** (not adopted, 2026-10-13). C2 proposed keeping the step as a stream the user
writes. The step is depth instead, because depth is permanent per node and needs no writer's cooperation;
a user's own step label, where it differs from depth, is a metric like any other.

**`parent(N, P)` and `values(N, Ms)` as two relations** beside a posted `at(N, M)` (withdrawn 2026-10-13).
They split one event in two, so a reader could hold a node's parent link without its values, and `at`
overlapped `values` with no stated rule between them. One record per event replaced them.

**`P : Node | Run`** (withdrawn 2026-10-13), the root encoded by a case split. The run is the root node, at
depth 0, so every rule over `parent` is uniform.

**Nodes named by bare names** — a sequence number, or a minted id — **with the order within a claim left
out of the records** (withdrawn 2026-10-13). A reference then carried no kind, so *"extending an exit is
ill-sorted"* was a lookup in the store, not a check on the message alone; and under set semantics two
identical rewinds by one claim collapsed into one record, so the head could not be read. The names that
replaced them carry both: `c(E, K)` for claim `E`'s record `K`, `end(E)` for its exit.

**`claim(E, From)` with `E` left unspecified** (withdrawn 2026-10-13). Read as a per-run sequence number,
as runstate's `claim_seq` is, `E` collided across runs: two runs' first claims both named their first node
`c(1, 1)`, which then had two parents, so one run's completed exit finished the other's question. Read as
minted, no record carried the order among claims. `E` is minted, unique across runs, and
`claim(E, I, From)` carries the order as `I`, so a failed arbiter leaves a tie in `I` and never a collision
of names.

**Kept out on purpose: the run in the claim record,** `claim(E, R, I, From)`. It would route a commit to
its run in one lookup and turn a tie in `I` into a key conflict. But a record names its run only through
what it extends, and a claim-level `R` would be a second source for the same fact, free to disagree with
the first. That bug class is not unique to it — a later commit's parent or a rewind's target can also point
into another run — but one source per fact is the rule, and the head report can check every position with
`ancestor_or_self(r, ·)`, as the scheduler does before resuming.

## Completion

**The run-keyed completion tail** (withdrawn 2026-10-13). A producer that converged at step 743 posted
`told(at(r, S, M), neg) :- S > 743, is_metric(M)` — no metric of any kind after step 743, in any launch. It
is honest only if every launch is deterministic; otherwise a relaunch that runs longer falsifies it, as a
`{t,f}` nobody can retract. Completion is per lineage: a `completed` exit finishes its lineage
(`finished`), and nothing can extend an exit.

**Closure told as a negative over children,** `told(parent(C, n743), neg)` (withdrawn 2026-10-13). `C`
ranges over an open sort, so no producer knows it: [`../2-polarity.md`](../2-polarity.md) §"The threshold rule" allows a
region over an open sort only to a poster who knows the sort is closed. The breaking case is someone later
resuming from the final model. Closure is structural instead: an exit is its own relation, and naming one
as a parent is ill-sorted.

**Kept on purpose: `finished(H, A)` from any node.** The rule does not require `A` to be a run, and that is
deliberate: *"finish from this checkpoint"* is the same relation asked at another node, a stronger question
than *"finish the run"* since every witness of it is one of the run's, and it also bounds where a scheduler
may resume.

## The head

**The head as *"the newest claim's highest-`K` record"*** (withdrawn 2026-10-13). It left out the claim
record and the exit, so a claim with no commits or rewinds had no head, and a finished claim's head was its
next-to-last node. The head is the claim's exit, else its highest-`K` record's position, else its `From`,
as runstate's is.

## The trace

**The traced demand over steps** (withdrawn 2026-10-13). The worked example asked
`asked(∃V. at(r, S, loss(V))) :- S ≥ 1`, handed the producer the per-step residual `S ≥ 61`, and settled
the question with 743 positives and the run-keyed tail above. No relation carries the step under node keys,
so the question has to be about some lineage, and a question about *"the run's head"* changes subject when
the head moves. At a fixed head it is settled before anything is produced. At a moving head, a later claim
resuming from an earlier node unsettles it with nothing retracted: the region changed, which
[`../2-polarity.md`](../2-polarity.md) excludes by fixing a region when it is named. Settledness under a moving head is a
report, which is runstate's own split (a read at a named node is always settled; under the latest claim's
head it is a report). A first draft kept the old trace as shorthand for a head-relative view, and failed
the same way. The trace now asks for one finished lineage and reads the series along the witness.

**The evaluator's question as one posted record with `checkpoint(N)` in its body** (withdrawn 2026-10-13).
A posted record's body holds constraints only, and an extent that grows as checkpoint records arrive is not
one question. **Then as an asker's rule posting one question per checkpoint** (withdrawn 2026-10-14): each
posted question concerned one node, so the trace showed a work set rather than a residual. The trace names
the fifteen checkpoints in one question; the rule remains for an asker that wants each checkpoint scored as
it appears.

**The resume check as *"a checkpoint whose node record the scheduler holds"*** (withdrawn 2026-10-14). On a
loss-tolerant transport a node can arrive without an ancestor, and a lineage resumed from it never reaches
the run, so the question never settles and the scheduler relaunches forever. The check is
`ancestor_or_self(r, N)` in the scheduler's store, lineage-graph's rule G.

## Stops, forbids and withdrawal

**A stop scoped to every computation extending a node, discharged when everything in scope had honoured
it** (withdrawn 2026-10-13). It mixed halting a computation with forbidding further ones, and the common
case, stopping the run, then needed the roster to discharge. A stop names a claim, and forbidding is a
separate positive fact.

**A stop discharged when its claim honoured it** (withdrawn 2026-10-13). With no compare-and-swap at exit,
a stop posted as its claim exited was never discharged. A stop is spent once its claim has ended; honouring
answers the asker and discharges nothing.

**A forbid gating only claims that start or resume in its scope** (withdrawn 2026-10-13). A claim already
running there kept computing, so halting it needed a stop naming each running claim, the roster the split
was meant to remove. **Then gating every record in scope, permanently** (withdrawn 2026-10-13): read
literally it forbade the `preempted` exit it asked a running claim to write, and a mistaken forbid made a
content-addressed run unservable for good. The exit is exempt, since nothing extends it, and a forbid is
withdrawn by its poster's reveal.

**Stops withdrawn by name,** `withdrawn_stop(X)`, beside leases withdrawn by hash-reveal (withdrawn
2026-10-13). Anyone who could read a stop could withdraw it, un-halting what an operator halted. Stops and
forbids are named by a commitment, as leases are, and one reveal serves all three.

**The stop dual keyed by the stop's name alone,** `discharged(X)` (withdrawn 2026-10-14). Once a session
could share one secret across its stops, one claim's exit or refusal discharged, in an observer's fold, a
stop sent to another claim still running: README §"Two commitments"'s attribution defect, caused by a
shared name instead of a position. A stop is the pair of its name and its claim.

**One reveal recommended for a session's leases, stops and forbids together** (withdrawn 2026-10-14).
Withdrawing a lease asks for less, while withdrawing a stop or a forbid permits more, so a session's routine
closing reveal lifted an operator's forbid. The poster chooses the mix, and a forbid meant to outlive its
session takes a secret of its own.

**The stop dual's positional form** (superseded by runstate's log format 0.3.0). The worked dual in
§"The honest cost" was measured as `discharged(C) :- stop(C), stopped(S), S > C`, which pairs a stop with
whatever exit follows it in the log: pairing by position, which [`../README.md`](../README.md) §"Two commitments"
forbids. The 13-of-13 and 9-of-9 figures there were measured on that form and its siblings.
