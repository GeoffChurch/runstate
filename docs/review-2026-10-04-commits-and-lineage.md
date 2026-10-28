# Review of `specs/commits-and-lineage.md` (draft): proposed fixes for everything

**Status:** DECIDED and APPLIED. Proposed 2026-10-04; the owner's decisions are §12 (2026-10-05 to
2026-10-14), and [`specs/commits-and-lineage.md`](specs/commits-and-lineage.md) revision 2 applies them
(2026-10-14). The spec is current wherever the two differ; this review keeps the evidence and the
alternatives rejected.

**What was reviewed:** the draft layer-2 spec (log format 0.4.0) on PR #59. Seven independent reviewers each
took one lens, and each had to give a concrete breaking case for every finding:

| Tag | Lens | Method |
|---|---|---|
| L1 | Basis and consistency (the design rubric, contradictions, defaults) | reading |
| L2 | Names and order (hidden positional dependence) | every causal reordering of small histories, enumerated |
| L3 | Adversarial execution histories | the spec's rules modelled in a script, and histories run |
| L4 | Consumers (mycooc, translation, runstate-tui) | their code read, and a census of about 2,568 real logs |
| L5 | The 0.3.0 → 0.4.0 migration | §7 implemented literally and run on the whole corpus |
| L6 | Implementer precision and cost | read; benchmarks on SQLite, Postgres and memory |
| CM | Convergence with if-built-today | read, both directions |

The reviewers' scratch scripts are in the session scratchpad (`specreview/lens*/`). They are throwaway.

**How the findings were verified:**
- Every finding listed as a decision was reached independently by two to five lenses.
- Most are **measured**: corpus counts, or histories that were run. One is **argued**: D9's claim that a
  log's records become visible in seq order.
- Two lenses independently got the same count for mycooc's emit-after-tick order: 4,214,206 values.

**The verdict:**
- **The core holds.** Commits and `parent`, read through a head chosen by claim order, produced no splice
  and no hole. That held under displacement, crashes and visibility lag, and under every causal reordering
  tried.
- **translation migrates exactly.**
- **The draft is not ready.** Its failures cluster into nine design decisions (§1–§2), about forty
  precision pins (§3), a cost gap (§4), and a migration that cannot be implemented as written (§5).

---

## 1. The decisions at a glance

| # | Decision | Reverses | Found by |
|---|---|---|---|
| D1 | Split the heartbeat by event: `tick()` writes a **commit** record, a lineage node; `beat()` writes a **heartbeat**, liveness only | decision 1 (one merged record) | L1, L3, L6 |
| D2 | The **claim** names its resume point: `Worker(…, resume_from=)`, recorded on `lifecycle.started`. A rewind writes an empty commit at once. No fallback to an older claim's commits | decision 3 (`resume_from` on `steps()` only) | L1–L5 |
| D3 | Settledness belongs to the head Strategy and the newest claim: permanent under `AtNode`, a report under `LatestClaimHead`. A closed node is a sink. Two terminals join without depending on order | decision 15 (in part) | L1, L2, L3, L6, CM |
| D4 | The commit names the subscriptions that fired (`fired`), so `answers()` is a join, not a replay | decision 14 ("answers derived by replay") | L1, L2, L3, L6 |
| D5 | Stream kinds are **declared** when the Worker is built, and recorded on `lifecycle.started` | decision 14 ("the first use fixes the kind") | L1, L3, CM |
| D6 | Offered elements are keyed by their committing node. Index reads, progress atoms and `ensure(n)` are eager-only, so the circularity rule disappears | decisions 9 and 14 (in part) | L1, L3, CM |
| D7 | A commit always follows what it names, everywhere. The migration moves commits; consumers emit before they tick | none; it closes a gap | L4, L5 |
| D8 | Every exit that is not an error commits its pending values. An error inside an iteration commits nothing | decision 11 (completed exits only) | L3, L4 |
| D9 | Reads are bounded on polled paths: incremental forms, plus a statement of which reads may be polled. Completeness becomes a check of well-formedness | none; it closes a gap | L1, L6 |
| — | 0.3.0 → 0.4.0 is **the last migration allowed to renumber**, because from 0.4.0 checkpoints hold seqs | — | L1 |

### The wire format after D1–D9

| Record | Body | Notes |
|---|---|---|
| `lifecycle.started` | `{handle, t, resume_from, offered}` | `resume_from`: seq, or null for a fresh start (D2). `offered`: the declared offered names (D5) |
| `lifecycle.commit` (new) | `{claim_seq, consumed_seq, parent, commits, fired, t}` | one per `tick()`; a lineage node (D1) |
| `lifecycle.heartbeat` | `{claim_seq, consumed_seq, t}` | one per `beat()`; not a node (D1) |
| `lifecycle.stopped` | `{completed, error, claim_seq, honored, final_commit, t}` | `final_commit` replaces `final_step` (D3, D8) |
| `lifecycle.bound`, `lifecycle.nak` | unchanged | — |
| `value` (value-v0.3) | `{value, t}` | the envelope `name` is the stream, and `request_id` is null |
| subscription-v0.4 | progress atom `{stream, n}`, on **eager** streams only (D6) | replaces the step atom |

---

## 2. The decisions in detail

### D1: one record per event (a commit, or a heartbeat)

**The problem.** Decision 1 merged commit, liveness, acknowledgement and lineage node into one record, on
the argument that they are "always written together". `beat()` refutes that: a liveness beat inside a slow
step is a liveness event with no commit. Three measured consequences:
- **Lineage grows with wall time.** At one beat every 5 s, an hour-long step adds 720 nodes (L6).
- **A `beat()` looks the same as a tick that commits nothing.** Yet the spec's own rules depend on telling
  them apart: subscriptions are "never evaluated at a `beat()`", and there is "one element per demanded
  iteration". `answers()` replay picked up the wrong registration point because of this (L3 P4 ii).
- **`parent` can name a mid-iteration state.** After a `beat()`, `parent` is "the previous heartbeat", and
  an error after beats leaves `final_beat` naming a mid-iteration node (L1).

**The fix.**
- `tick()` writes `lifecycle.commit {claim_seq, consumed_seq, parent, commits, fired, t}`.
- `beat()` writes `lifecycle.heartbeat {claim_seq, consumed_seq, t}`.
- **Liveness** is the newest of either record, by its `t` against the observer's own clock.
- **`parent` chains commits only.** Lineage depth counts iterations, so the "depth inflates" objection to
  decision 13 disappears.

**What it costs.** One more record type. Each event still writes exactly one record, so a tick is not doubled.

### D2: the claim names its resume point

**The problem.**
- **The resume point never reaches mycooc.** `resume_from` exists only on `steps()`, but mycooc drives
  `tick()` from inside its trainer (training.py:1369), and `serve()` takes no resume point. Every resumed
  episode therefore silently becomes a fresh root: 250 resumed claims in 97 logs, and 27 rewinds in 18
  logs (L4). That is exactly the silent fresh start decision 3 was written to make loud.
- **`LatestClaimHead`'s fallback is a regression.** "The newest claim *that has written one*" falls back to
  an older claim's beats, which is the stale-beat leak layer 1 fixed. Example: A is preempted at 150, and
  B claims and resumes from node 100. Before B's first commit, `prefix(loss, 120)` reads Complete off A's
  branch; afterwards it reads Pending (L1, L2, L3, L5).
  - **Corpus count, corrected in §11:** 25 mycooc runs and 1 empty translation run end on a latest claim
    that never beat, and 8 of them have earlier history. L5's first count of 105–111 was an artifact of
    comparing seqs before and after renumbering.
- **A rewind followed by an error.** If the worker errors or is killed before the rewound branch's first
  commit, reads follow the abandoned branch (L3 P2a).

**The fix.**
- **`Worker(channel, *, resume_from)` is required and keyword-only.** It takes a checkpoint object, or an
  explicit `None` for a fresh start. It is written on `lifecycle.started` as `resume_from` (a seq, or null).
  It covers `steps()`, `serve()` and a direct `tick()` alike. `steps()` loses its `resume_from`
  parameter.
- **`Worker.rewind(checkpoint)`** writes an empty commit at once, with `parent` set to the checkpoint's
  node. It rebuilds the Worker's per-stream counters and subscription baselines from the lineage at that
  node.
- **The head of a claim with no commit yet is its `resume_from` node,** or the empty lineage for a fresh
  start. It is never an older claim's commit. `LatestClaimHead` is then "the newest claim, and its newest
  commit, otherwise its resume node".

### D3: settledness belongs to the head Strategy and the newest claim

**The problem.** As drafted, a lineage is settled when a completed exit names its `final_beat`. Four ways
that goes wrong:
- **Forged settledness (L3 P1).** A is frozen at 5, and B claims, resuming from A@5. A wakes, runs to 10 and
  completes before B's first commit. `prefix(loss, 20)` returns `SettledShort(10)` while `peek_terminal`
  says Running(B). If B then errors before committing, `SettledShort` stands forever, and `ensure` never
  re-drives the run. That is the forged-verdict truncation layer 1 removed.
- **A settled read becomes unsettled.** A later "train more" claim resuming from the final node turns
  `SettledShort` into `Complete` (L1, L2, L3).
- **`AtNode` waits forever.** `AtNode` on any node that is not a final one returns `Pending` forever, which
  contradicts "never waits forever" (L1, L3).
- **Two terminals naming one claim are resolved newest-first.** A worker's completed `stopped` and a
  concurrent third-party release both name the claim. Either can land last, so the verdict, and with it
  settledness, depends on order (L2, measured on master).

**The fix.**
- **`AtNode(x)`:** the lineage ending at `x` is fixed, so its outcomes are permanent. A missing element is
  `SettledShort`.
- **`LatestClaimHead`:** the read is settled if and only if the newest claim's own terminal is a completed
  exit whose `final_commit` is the head. The outcome is a **report, valid as of the newest claim**, and is
  listed in the coordination budget (§9, C1). A newer claim with no commit yet reads `Pending` at its resume
  node, by D2.
- **A closed node is a sink.**
  - A completed exit always writes a closing commit, even an empty one (D8), and the driver never
    checkpoints it.
  - The Worker refuses `resume_from` naming a `final_commit`.
  - "Train more" resumes from the last real checkpoint, an ancestor, which starts a new branch.
- **Two terminals join without depending on order.** Over a claim's terminals, a completed exit with a
  `final_commit` dominates a release. This replaces newest-wins for these reads; `peek_terminal` uses the
  same join.
- **Shapes:**
  - `prefix` returns `Complete(first n elements, node)`, `Pending(have: int, node)` or
    `SettledShort(all elements, node)`, where `node` is the head it evaluated;
  - `ensure` returns `Complete | SettledShort`.

### D4: the commit names the subscriptions that fired

**The problem.** Deriving `answers()` by replaying a subscription's schedule cannot be done faithfully:
- **The log does not determine it (L2).** In one history, 220 physically possible executions wrote
  byte-identical records, and the true answers differed. Replay gave one answer for all 220.
- **The only witness is positional.** The only record of when a subscription was registered is
  `consumed_seq`, which compares two writers' positions (layer 1 measured it order-dependent in 276 of
  1,327 scenarios).
- **Time atoms** are evaluated at the worker's own instants, which are not on the log.
- **Five measured disagreements** between replay and what the worker actually fired: rewind, beat-time
  registration, resume, the two readings of `every`, and pending elements (L3 P4).

**The fix.**
- The commit carries `fired: [request_id…]`, the subscriptions that fired at that tick, eager or offered.
- `answers(ch, request_id, *, head)` is the elements committed at the nodes whose `fired` names the
  request: the sample for an offered stream, the emitted elements for an eager one.
- This is reference by name, it costs one short list per commit, and it still cannot double-count.
- The spec states that **no 0.4.0 read uses `consumed_seq`** except `await_consumed`'s watermark, and that
  one is listed in the coordination budget.

### D5: stream kinds are declared

**The problem.** "The first `emit` or `set` fixes the kind" fails when a subscription on a name arrives
before its first use, which happens after every resume, since kinds were fixed per episode. Every reading
fails (L3 P4 viii):
- sampling without fixing the kind gives two producers;
- fixing the name as offered lets an observer crash the worker, at its own `emit`;
- skipping the subscription loses its answers.

The kind also appears on no record, so the interop rule cannot be witnessed (L1). if-built-today's
signature rule says the same thing (`1-logic.md` §484–513: never inferred from the first post) (CM).

**The fix.**
- `Worker(…, offered=[names])` is required and keyword-only; an explicit `[]` is fine. Every other name is
  eager.
- The list is written on `lifecycle.started` as `offered`, so the kind is a fact on the wire, checkable per
  claim.
- `set` on an undeclared name raises, and `emit` on a declared one raises.

### D6: offered elements are keyed by node; progress is eager

**The problem.**
- **An offered stream's index depends on who asked.** It counts the iterations at which someone demanded
  it, so its coordinate depends on who asked and when. if-built-today's questions need atoms whose identity
  does not depend on the asker (`3-questions.md` §65–70), so the planned questions layer could not use
  them (CM).
- **The circularity refusal misses mutual demand.** It catches only a self-loop. X demanded with progress
  on Y, plus Y demanded with progress on X, never fires and is never refused. That pins a `serve()` worker
  forever, against service-worker.md:48 ("registered ⟺ a future firing is possible") (L1).
- **`ensure` on an offered stream demands nothing,** so it loops on NoProgressError (L1).

**The fix.**
- **Offered elements are read by their committing node,** through `aligned` and `answers`. A list in
  lineage order is still available, but nothing reads it by index.
- **Progress atoms, `prefix`, `progress` and `ensure(n)` name eager streams only.** A progress atom naming
  an offered stream is refused with `nak` reason `"malformed"` and an explaining message.
- **Circular demand becomes impossible,** so the special refusal is deleted.

### D7: a commit always follows what it names

**The problem.**
- **mycooc ticks before it emits** (main.py:923 sends its metrics after `on_step`'s tick at
  training.py:1369). So 4,214,206 of its 4,469,093 values, 94.3%, land after their own step's heartbeat
  (L4 and L5, the same count). Natively, each step's metrics would commit to the *next* node. Migrated, the
  heartbeat would name values written after it, which no 0.4.0 Worker could write. That breaks the
  causal-order premise behind the order-independence suite (L5 F2).

**The fix.**
- **Natively, the Worker satisfies it by construction:** pending values precede the commit, and
  `commit_external` validates that the seq is lower.
- **Readers reject a violation:** a commit naming a seq at or above its own is a `MalformedRecordError`.
- **The migration moves each commit** to just after its last committed value. It is renumbering anyway,
  and this needs §5's rule M4.
- **The consumer checklist gains "emit before you tick"** (§6).

### D8: every non-error exit commits

**The problem.** The draft defines only the completed exit and the error inside an iteration. 735 of
mycooc's 1,631 worker-written stops are clean but not completed: max-steps chunks, disk pressure and
commanded stops. 37,750 values sit between a claim's last heartbeat and its stop, and post-loop values on a
preempted exit are undefined (L3, L4).

**The fix.**
- A completed, preempted or commanded-stop exit commits its pending values. A completed exit writes a
  closing commit even when nothing is pending (D3).
- An error **inside an iteration** commits nothing.
- `final_commit` is the claim's newest commit; otherwise its resume node; otherwise null for a fresh start
  that never committed.
- **"Inside an iteration"** is defined for workers that call `tick()` themselves: after the first `emit`
  since the last commit, and before the next `tick()`.
- `stopped()` takes no `final_commit` argument. A caller-supplied one would be a lineage lie.

### D9: bounded reads, and completeness as well-formedness

**The problem.**
- **Polled walks are slow.** `progress`, `prefix` and the completeness check walk the whole lineage, and
  they run on polled paths: `ensure`'s 10 ms loop, the TUI and the cockpit. Measured on SQLite: 10 ms at
  1,000 nodes, 100 ms at 10,000, 1.1 s at 100,000; 0.7 s at 10,000 on Postgres (L6).
- **One bad record poisons everything.** A bad commit in a dead ancestor makes every future read raise (L1).
- **"Waits, or raises" was never decided.**

**The fix.**
- **Completeness is well-formedness.** *Argued:* a log's seqs are contiguous and its appends are
  serialized, so a reader that sees a commit sees everything below it. So:
  - reads take `W = last_seq()` first and read nothing above it;
  - a `parent` or a commit naming a seq at or above its own, a `parent` that is not a commit, or a commit
    naming a record that is not a value raises `MalformedRecordError(seq, topic, detail)`;
  - reads never wait; `ensure` loops on `prefix_len`.
- **Incremental forms.** A Watcher-held `_StreamPrefix`, twin of `_PendingStops`:
  - **state:** a cursor over commits, a cursor over values of S, the set of S's value seqs, and
    `count[node] = count[parent] + |commits ∩ S|`;
  - **cost:** O(new records) per poll;
  - **test:** "incremental equals pure after every step".

  Similar forms exist for the head, and a delta form of `aligned` for live charts returns
  `(truncate_to_depth, appended_rows)`, because a head flip rewrites rows already shown.
- **The pure walk** uses windowed bulk reads backward from the head (about 5 µs a row), not point reads
  (65 µs a node on Postgres).
- **Split counting from materializing:** `prefix_len` is the polled count; `prefix` materializes elements,
  one shot.
- **Which reads may be polled** is stated in the spec:
  - **Polled:** `prefix_len`, `progress`, the head, `peek_terminal`, `live_episode`, staleness,
    `pending_stops`.
  - **One-shot:** `series`, `prefix`, `aligned`, `history`, `answers`.
- **The memory backend** gains a per-topic index, because its `latest` is O(N) today: 1.2 ms at 210,000
  records.

---

## 3. Precision pins, by spec section

These were the main ambiguities, where two careful implementers would have built different things. Mostly
from L6, with others where marked.

**§2, the rule**
- **The coordinate.** `count(S, node)` is the number of S elements on the lineage up to **and including**
  `node`.
  - `progress` is `count` at the head.
  - A progress atom fires at the node where `count` crosses its threshold, between the parent and that node.
  - `aligned`'s x is `count` of the progress stream at the node.
  - An element's index is its 0-based position.
  - `ensure(stream, n)` returns the first `n` elements.

  This changes 0-based step labels to 1-based counts, which the consumer checklist covers (§6).
- **Order within a node** is the order of the `commits` array: the writer's order, which reordering cannot
  move (L2 F5). Two `emit`s of one name in one iteration are two elements.
- **Membership** is by the envelope (topic `value`, the name), never by whether the body is valid. That
  keeps tolerant readers' indices aligned with the worker's counters.
- **A seq committed twice along one lineage** is malformed. Readers raise; the Worker prevents it (L3 F8).

**§3, schemas**
- **lifecycle-v0.6 commit:**
  - required `[claim_seq, consumed_seq, parent, commits, fired, t]`;
  - `parent`: an integer ≥ 1, or null;
  - `commits`: an array of integers ≥ 1, unique, with order significant;
  - `fired`: an array of strings, unique;
  - stated in prose, since a schema cannot check it: every `commits` seq is lower than the commit's own.
- **heartbeat:** required `[claim_seq, consumed_seq, t]`.
- **stopped:** required `[completed, error, claim_seq, honored, final_commit, t]`, with "completed ⇒
  `final_commit` is an integer".
- **started:** gains `resume_from` (an integer ≥ 1, or null) and `offered` (an array of unique, non-empty
  strings).
- **value-v0.3:**
  - required `[value, t]`, with `t` nullable as in v0.2: translation writes `t: null`;
  - the envelope `name` is a required, non-null string, and `request_id` is null.
- **subscription-v0.4:**
  - the atom is `{stream, n}`, both required, with `stream` at least 1 character, `n` from 1 to 2⁵³−1, and
    `additionalProperties: false`;
  - `control.subscribe`'s envelope `name` is required;
  - Python's `_malformed_condition` must accept the two-key atom.
- **`Nak.reason`:** no new value (D6 uses `"malformed"`).
- **`commits` has no `maxItems`.** Emitting without ever ticking builds one large commit. Document it.

**§3, evaluation: the `every` rule**
- **The delta rule,** which today's code implements: fire when
  `count(now) − count(at the last firing) ≥ n`.
- **The baseline is the count at that firing,** not rounded to a multiple.
- **Each tick is evaluated once,** `until` first, with no interpolation inside a jump.
- **Delete the draft's "crosses a multiple" sentence.** It was a second rule (L3 P4 vi, L6).

**§4, the Worker. `tick()`'s order:**
1. drain control; a subscription registered in this drain may fire in this tick;
2. evaluate subscriptions;
3. write one sample per offered stream that fired;
4. write the commit, with `commits` (pending values plus samples) and `fired`;
5. write expiry unsubscribes **after** the commit, so a crash cannot strand a one-shot subscription with its
   answer lost (L3 F4);
6. run the driver's checkpoint save;
7. return the stop decision.

**§4, the rest of the Worker:**
- **`beat()`** drains control and writes a heartbeat. It evaluates nothing and commits nothing.
- **`commit_external(seq)`** raises `ValueError` unless the record at `seq` on this channel is a `value`
  with a non-null name and a null `request_id`, `seq > claim_seq`, and the seq is neither pending nor
  already committed in this episode. It costs one point read, and authorship stays unprovable (honor
  system) (L3 F8, L6).
- **The fingerprint guard is deleted** (L1). It had no field on the wire, and construction already prevents
  lineage lies. It moves to the backlog as an idea for custom loops.

**§5, reads**
- **`aligned`:**
  - a cell holding two or more elements of one name is a list;
  - an absent name is `None`;
  - rows share an x where the progress stream is sparser than the metric;
  - x is 0 before the first progress element.
- **`streams(ch, *, head)`** lists the eager and offered names on the lineage, from `started.offered` and the
  committed names. mycooc enumerates 23 dynamic names (L4 F9).
- **Elements are typed:** `Element(node: int, value: Any, t: float | None)`. `series`, `prefix` and
  `answers` return them (L4 F10).
- **A progress selector `Commits()`** counts commit depth, so it counts iterations under D1. It serves the
  TUI's "step N" column and `--objective None` (L4 F8).
- **A third-party `stopped`:** `final_commit` is null. A release names no node of its own, and under D3 it
  never settles anything.
- **Sites that break, now listed** (they were hidden in the draft's "Unchanged" list):
  - `Watcher._note_heartbeat`, `_heartbeat_seed` and `await_consumed` parse `Heartbeat(**body)`, which
    raises TypeError on the new bodies;
  - `RunResult.final_step` becomes `final_commit`;
  - `Running.step` becomes `Running.head` plus the `Commits()` count (L1, L6).

**§6, the checkpoint recipe**
- **Choosing a checkpoint:** `OnHeadLineage` walks once from the head, O(L + K). `MostProgress` is a lookup
  in the `count` map.
- **Driver position** (`total`'s count) lives in the checkpoint object, not on the log.
- **A closed node cannot be resumed from** (D3).

---

## 4. Cost summary, measured by L6

| Operation | Cost | Polled? | Form |
|---|---|---|---|
| head (`LatestClaimHead`) | SQLite ~10 µs, Postgres ~100 µs, memory O(N) | yes | incremental, O(new records) |
| `progress`, `prefix_len` | pure: O(L·c + V_S) | yes | `_StreamPrefix` |
| `prefix`, `series`, `history`, `answers` | O(L·c + V_S) | no | one-shot |
| `aligned` | O(L·c + ΣV) | no; a live chart uses the delta form | delta form |
| completeness | O(1) under D9 | yes | well-formedness |
| `tick` | O(new control + pending + atoms) with per-stream counters | — | counters seeded at resume or rewind |
| `commit_external` | one point read | — | — |

In the table, L is lineage length, c the commits per node, and V_S the number of values of stream S.

---

## 5. Migration (§7): fixes

**Correction, from the alternatives round (§11):**
- **The figures below are superseded.** They came from L5's harness in its parent-by-step mode, which
  implements none of M4, M5 or D2's head.
- **What an implementation of M1–M11 as written gives on mycooc:**
  - P1 fails on 37 heartbeats;
  - P3 differs in 1,191 of 1,348 runs;
  - five classes of difference appear that this section never names.
- **§11 has M5 rewritten, the added rule M12, and figures re-measured under the rewrite.**

Measured by L5 on the corpus, after the shipped 0.2.0 → 0.3.0 step. translation passes everything as written.
On mycooc the rules conflict.

**The rules, revised:**
- **M1. Parent.**
  - A claim's later commits take the claim's previous commit.
  - A claim's first commit takes, **in claim order**, the newest earlier claim holding a commit with old
    step s−1, and that claim's last such commit. Otherwise null.
  - That node is also written as the migrated `started.resume_from` (D2).
  - **Why:** the draft's "the latest earlier heartbeat one step behind" compares positions across writers.
    It picked a displaced worker's branch in 9 of 21 reorderings, which turns a splice into a permanent name
    (L2 F2). And synthesizing a step-0 heartbeat made 235 resumed claims fresh roots; in 8 runs the whole
    prior lineage became invisible (L5 F1).
  - **Measured:** P1 fails on 0 of 214,415 heartbeats, and P2 on 0 of 4,465,024 values.
- **M2. The step rule wins.** A value with an integer step is committed by its claim's commit carrying that
  old step, even when it also follows the claim's last heartbeat. Otherwise 28,827 last-step metrics move,
  and `final_commit` lands one step late (L5 F3).
- **M3. Stepless values** are committed by the next commit of their claim. These are registers: `config`,
  `completion_reason`, `status` and `input_provenance`, 1,843 (run, name) pairs, not "metric names"
  (L4 F13, L5).
- **M4. Values after their claim's `stopped` stay uncommitted.** There are 1,886 of them, all teardown
  `status`. This prevents a `final_commit` landing after its own `stopped` (547 cases), and it is the
  prerequisite for D7's moves (L5 F4). if-built-today records the same 1,643 teardown values as ordinary
  positives (CM).
- **M5. Synthesize a commit only between two real heartbeats of the same claim** (a skipped tick), or at or
  below a completed exit's last step. Never trail one past the last real heartbeat on an errored, preempted
  or killed exit (L5 F6: 273 claims; L1 F7).
- **M6. Move each commit** to just after its last committed value, then renumber (D7).
- **M7. The rename list is derived from the schemas,** not written by hand:
  - `claim_seq`, `parent`, `commits`, `final_commit`, `resume_from`, `bound`'s `claim_seq`;
  - and **`consumed_seq`**, which the draft omitted: 963 heartbeats would point at the wrong record (L5 F5).
  - `stop@N` request ids stay as they are, since they are strings, and that is documented.
- **M8. Synthetic commits** take `t` from the last value they commit, `consumed_seq` from the previous real
  heartbeat (renamed), and `fired: []` (L5 F8).
- **M9. Refusals.** Each happens before sealing:
  - any subscription (0 in the corpus);
  - a `control.stop` whose `from` holds a step atom (0);
  - two heartbeats of one step in one claim, an in-episode rewind (0).
- **M10. A malformed `stopped`** (the 7 `{reason, honored, claim_seq}` bodies from the reclaim tool) is copied
  as it is, following the 0.2.0 step's precedent (L5 F8).
- **M11. Stepless heartbeats** (1,114) become commits with no step to compare. P1 excludes them.

**The properties to test:**
- **P1:** for heartbeats with an integer step, commit depth equals the old step.
- **P2:** every value with a step is committed by its claim's commit carrying that step.
- **P3:** reads equal the old reads, except for **named** classes of difference. Measured, under M1–M11:
  - (a) convergent-merge cells beyond the head's lineage: 6,529;
  - (b) cells at steps the lineage revisited without that name: 360;
  - (c) new elements from stepless registers, now committed: 3,135;
  - (d) nodes holding several `status` values: about 2,900 nodes, 7,608 values;
  - (e) 57 changed `status` cells, explained per cell in the step's tests.

**Golden logs:**
- the L2 displacement-parent history;
- the L5 step-0 loading `status`;
- a skipped tick;
- post-loop values;
- values after `stopped`;
- errored and preempted exits;
- a malformed `stopped`;
- a stepless heartbeat.

**The last renumbering.** State in log-formats §6 that 0.3.0 → 0.4.0 is the last step that may renumber.
From 0.4.0 on, checkpoints hold seqs.

---

## 6. The consumer upgrade (§8): additions

From L4's inventory, with file and line references:
- **mycooc: name the resume point on `Worker(...)`** (D2): main.py:277; training.py:1369; main.py:933
  `start_step=`.
- **mycooc: emit before the tick** (D7): main.py:923 sends after training.py:1369, and 94.3% of values are
  affected. Add a conformance test.
- **mycooc: every exit commits** (D8): 735 clean exits that were not completed.
- **progress is a count, not a 0-based label** (§3):
  - mycooc `p >= req` at run_experiment.py:1102 and :1109;
  - mycooc `best_step` at channel_read.py:168, against training.py:455 and the CSV pinned by
    tests/migration/test_phase6a;
  - translation keys.py:77 (`progress+1 >= n`);
  - the TUI's "step N".
- **Delete the emit-only-missing guards** (the lineage supersedes them): translation workers.py:28, which
  raises KeyError on `body["step"]`; mycooc analyze_run.py:1391–1393 and :1534, which silently loses keys.
- **Third-party stop writers** write `final_commit: null`: run_experiment.py:675 and :3957,
  scripts/reclaim_experiment.py:326, scripts/repair_malformed_stopped.py:33.
- **Registers stay raw** (correcting the draft's "send values through the Worker"):
  - `status`, `completion_reason`, `config`, `input_provenance` and `analyzed` are read raw;
  - the "done" `status` written after `w.stopped` (main.py:1063) stays a raw send. Through `emit`, the
    post-terminal latch at worker.py:238 would drop it silently;
  - the orchestrator's `completion_reason` (run_experiment.py:3866, :3877) stays raw;
  - the real separate-handle write is the `input_provenance` register (graph_adapter.py:213–217), not a
    "final metric".
- **`value_series` becomes `streams()` plus `aligned` or `series`:** mycooc channel_read.py:151 (about 10
  sites); summarize_outputs.py:88.
- **Element type:** translation runner.py:33 and orchestrator.py:36–37, :69; mycooc analyze_run.py:1532–1534.
- **translation has no checkpoint object;** its state is the log plus the ArtifactStore. Document
  `resume_from=None` plus re-emitting. Its tests at tests/test_workers.py:40–75 pin resume.
- **Every early return before the tick:** training.py:1017 (the NaN sentinel), :1256, :1287, :1313, :1319,
  :1334 and :1350. The draft cited three; the fix is a `finally`.
- **Declare offered names** (D5). No consumer calls `w.set` today, so all of them declare `offered=[]`.
- **runstate-tui:**
  - use `Commits()` for its frontier;
  - add a bounded, non-blocking "latest element on the head's lineage" read;
  - fix the `step>N` filter (table.py:161–169);
  - fix "@ step" (fold.py:114, detail.py:40, format.py:60);
  - make `latest` lineage-aware (fold.py:111);
  - handle `--objective None` (env.py:41).

---

## 7. Tests (§10): additions

- **Resume:** on a Worker that drives `tick()` itself (D2); a rewind writes an empty commit; the head of a
  claim with no commit is its resume node.
- **Settledness (D3):**
  - a displaced worker's completed exit does not settle;
  - "train more" is refused at a `final_commit`;
  - two terminals join regardless of order;
  - `AtNode` is permanent.
- **Answers (D4):** `answers` via `fired`, including time atoms, a rewind and a resume.
- **Kinds and offered streams (D5, D6):**
  - declared kinds, with misuse raising;
  - offered elements keyed by node;
  - a progress atom on an offered stream is refused.
- **Commit order (D7):** a commit naming a later seq is malformed.
- **Exits (D8):** every exit kind; `final_commit` in each.
- **Bounded reads (D9):** incremental equals pure after every step; polled reads stay within a bound as L
  grows.
- **Rules and ordering:**
  - `every`'s delta rule under batching;
  - the coordinate's convention;
  - order within a node;
  - `commit_external` validation;
  - a crash between an expiry and its commit.
- **The order-independence harness:**
  - rename `consumed_seq`, `parent`, `commits`, `final_commit` and `resume_from`, not only `claim_seq`;
  - cover `answers`, order within a node, and the two-terminal case. As it stands, the harness cannot see L2's
    finding 1.
- **The migration:** the golden logs and the properties of §5.

---

## 8. Drift and doc fixes

- **The spec header** cites decisions 1–13; it relies on 1–15, and will rely on the decisions that follow
  this review (L1).
- **identity-in-records decision 4** still says samples carry a `request_id`. Mark it superseded (L1, CM).
- **Decision 12 versus §7:** decision 12 translates step-atom subscriptions, while §7 refuses them. Align to
  refusal (L1).
- **`final_commit`'s justification.** Under D1 it is "the claim's last commit". It is kept on the record
  because a reader under visibility lag knows what to wait for (L1).
- **The spec's claim that its data model "is what that layer demands"** (C&L:357–360) becomes true only with
  D5 and D6 (CM).

---

## 9. Convergence with if-built-today

### C1. Correct runstate's own docs (applied 2026-10-14)

**`demand-streams.md` misstates if-built-today in four places (CM):**
- **"Demand subsumption merges overlapping demands."** if-built-today gets subsumption free from an empty
  residual, and *refuses* merging, because anti-unification over-approximates (`3-questions.md` §181–184,
  §305–311).
- **"Withdrawal is retraction; polarity is the answer."** `asked` is unpolarised. "Still wanted" is
  control. Withdrawal is a lapse plus a positive `withdrawn(k)`: ordinary facts (`5-domain.md` §"Still wanted: leases").
- **"A stop is withdrawn demand."** An operator's stop is a told fact that needs authority (README §189–191).
- **Its interim-rule note** becomes moot under D5 and D6.

**`if-built-today-carryover.md` becomes `if-built-today-convergence.md`,** with the map. Corrections:
- **"Identity adopted"** holds only under one sequencer.
- **"Settledness adopted"** is overstated until D3.
- **"Objections partly adopted" is wrong.** Nothing contests anything, and newest-wins between terminals is
  retraction at read time. D3's join fixes one instance.
- **The non-monotone-core list gains:**
  - the claim gate's `live_episode is None`;
  - the probe tiers;
  - `ensure`'s no-progress and fixpoint guards;
  - the subtracting folds;
  - `await_consumed`'s "accepted";
  - terminal resolution;
  - `last_activity`;
  - the sequencer on every append;
  - `LatestClaimHead` outcomes.

  Register reads move off the list: sampling is computation that outputs a monotone fact.
- **Rows it lacks:** hash-reveal withdrawal, monotonic clocks, declared kinds (D5), reclamation tiers, and
  vouching as commit, which layer 2 already adopted without saying so.

### C2. Edits to if-built-today itself (adopted at §12, item 11)

The direction these follow is if-built-today taking what runstate has measured:
- **Re-key `5-domain.md`'s values** from `at(R,S,M)` and `at(E,S,M)` to `at(N,M)` with `parent(N,P)`,
  `commits(N,V)` as per-node vouching, and the step as a user stream. Evidence: a claim names an episode, not
  a history (identity-in-records §3, rewind probe), and the lineage-graph tests.
- **Make the completion tail per lineage,** and record that a run-keyed tail is honest only for
  deterministic re-runs, as if-built-today's own rule says.
- **Scope stops:** a stop names what it stops (lineage-graph test 2: 20 of 120 runs computed past a stop).
- **The claim order carries the observer plane** (lineage-graph: verdict and progress varied in 52–85 of 120
  without it).
- **Strike the stale positional text** that layer 1 removed: README §90–99 ("the mechanism is live"),
  `3-questions.md` §323–326, and `5-domain.md` §135–137 and §243–246.

### C3. New backlog rows (runstate adopting from if-built-today; applied 2026-10-14)

- **Hash-reveal withdrawal** (`5-domain.md` §"Still wanted: leases"): subscribe carries a commitment, and unsubscribe reveals
  it. It is unforgeable, needs no ordering, and one asker cannot withdraw another's demand.
- **`time.monotonic` for durations** (`1-logic.md` §309–312). The witnessed-staleness measurement supports it.
- **if-built-today's reclamation tiers** for collecting abandoned branches and unvouched checkpoints.
- **Dispute records** naming what they contest, instead of resolving by newest-wins.
- **Keep identity attached in cross-run summaries:** cockpit cells keep run, node and stream, and
  latest-wins and argmax are labelled as summaries that drop identity.
- **In time:** minted node and episode ids for a regime with no central store (layer 6). The map rates seqs
  as names as a conflict there.

---

## 10. Left open after this review

- **Authority for an operator stopping someone else's demand** (authenticated-records; hash-reveal covers only
  self-withdrawal).
- **Layer 6:** minted ids, verdicts relative to a head, a stable head choice, and stop scope without an arbiter.
- **The encoding experiment** (bytes, including `commits` and `fired`) on the real corpus.
- **A runtime guard for custom loops that bypass the checkpoint recipe** (was the fingerprint guard).

---

## 11. After the alternatives round (2026-10-04)

Six further reviewers each took one cluster of fixes and looked for a compelling alternative:
- D1 and D8;
- D2 and D3;
- D4 to D6;
- D7 and D9;
- the migration;
- the pins in §3 and the convergence actions C1–C3.

Each one:
- generated at least two genuinely different alternatives;
- held each alternative to the same failure cases as the fix it would replace;
- probed or measured where that was cheap: models of 20,000 random histories, the real corpus, and
  Postgres and SQLite under concurrency.

An alternative counted as compelling only if it fixed the same failures and won on some axis without losing
on correctness.

**The outcome:**
- **No decision was overturned.** D1, D2, D4, D5, D6, D7 and D9 are kept, most with high confidence.
- **Two decisions grew a better mechanism:** D3's terminal join, and D8's exit commit.
- **Several gaps closed:** the progress atom, the membership rule, and the migration's M5.
- **The migration's published figures were wrong** (corrected in §5).

### 11.1 New decisions for the owner

| # | Decision | Recommendation | Why |
|---|---|---|---|
| E1 | **Fold the exit commit into `stopped`.** `stopped` carries `{parent, commits}` and is itself the exit node, replacing a separate closing commit. `commits` holds the pending values unless the exit is an error; a third party writes nulls. `final_commit` goes; the exit node is the `stopped`. | **Adopt** (the reviewer was about 65% confident) | It applies D1's rule, one record per event, to the exit. Its gains: <ul><li>no kill between two appends leaves a post-loop commit under a killed verdict;</li><li>`Commits()` counts iterations exactly;</li><li>D3's sink falls out of "a parent must be a commit";</li><li>the migration needs **no** synthetic closing commits. Otherwise it needs 736 for mycooc and 1,139 for translation, and translation's logs would have to be renumbered.</li></ul>The cost: it reverses decision 11's "no commits on `stopped`", and readers must treat a `stopped` as a possible head. |
| E2 | **Classify membership by the committing claim.** A committed value is an element of its stream if and only if its committing claim did not declare its name offered. The stricter alternative: offered anywhere on the lineage means offered for the whole lineage. | **Per committing claim** | Kinds are declared per claim, but a lineage spans claims, and offered samples share the eager envelope. Without this, a demand-dependent count leaks silently into an eager stream. Going per claim lets code change a name's kind across resumes. |
| E3 | **Add a `{commits: n}` progress atom,** counting iteration commits. | **Adopt** (raised independently by two reviewers) | Under D6, a worker that emits nothing eager has no every-iteration condition, and that covers 4 of the 6 in-repo examples. The atom is sound: commits grow regardless of demand, so it cannot be circular. And with D1 plus E1, commit depth equals iterations. It also gives `ensure`'s `{step: N}` an exact translation. |
| E4 | **A rewind inside one claim.** Either it is a re-claim with `resume_from`, or it stays in-claim with a marker. | **A re-claim** | The corpus has 0 in-claim rewinds. A re-claim needs no marker record, keeps `Commits()` exact, and covers a kill right after the rewind. Its costs: episode-local leases are voided, and clients renew them as they do anyway; and the claim CAS runs once per rewind. The in-claim form stays on the frontier for cost and for lease continuity; it would need a marker that `Commits()` excludes. |
| E5 | **Retire the name `progress`.** The new number is a 1-based count, against today's 0-based step label. Keep one read, `prefix_len`. | **Adopt** | A new name makes the shift visible at every call site that must change (mycooc `p >= req` becomes `p > req`; translation `progress+1 >= n` becomes `prefix_len >= n`). And today `progress` and `prefix_len` are the same number, which the design rubric's independence test rejects. |

### 11.2 Revised recommendations (no reversals)

- **D1 is kept, with two pins.**
  - **Liveness follows observer-clock.md §5:** witnessed arrival, not the record's `t`. A new seq on either
    topic naming the latest claim resets the witnessed clock. The acknowledgement is the `consumed_seq` of
    the newer of the two records.
  - **mycooc's 1,114 stepless 0.3.0 heartbeats are beats.** Every one sits in the 150 stepless-only claims,
    after only `status` values. So they migrate as `lifecycle.heartbeat`, and M11 disappears.
  - **Dropping the beat altogether was measured out.** mycooc's 129 silences longer than 1,800 s (up to
    10,031 s) all fall inside those claims' phases, so its Watcher would presume them dead.
- **D2 is kept.**
  - **Modelled across 696,000 reads:** 0 forged settledness, 0 stale-branch reads and 0 dark reads. The
    draft's fallback gave 84,343 forged and 81,253 stale.
  - **Add:** the Worker validates `resume_from` with one point read (it must name a commit of this run).
  - **Add:** the checkpoint recipe asserts at load that `loaded.node == started.resume_from`. mycooc loads
    from a mutable `checkpoint_last.pt` after it claims.
- **D3 is kept, with its guarantee strengthened and its join made total.**
  - **The guarantee:** with the sink plus the join, the (outcome, node) pair a read returns is **permanent
    under `LatestClaimHead` too**. Only the choice of head is a report. Modelled across 741,000 reads: 0
    flips, against 14,063 with newest-wins and 36,874 without the sink.
  - **The non-monotone list** says "`LatestClaimHead`'s choice of head", not "its outcomes". That resolves
    the convergence map's conflict on permanence instead of budgeting it.
  - **The join is a total rank:** completed > errored > preempted, then a non-null node over a null one, and
    malformed bodies are skipped. Six real claims carry terminals of two kinds. In one, a completed run of
    600 steps got a release 13 hours later, and master's `peek_terminal` reads it as preempted today.
  - **The test invariant:** `settled(h)` holds if and only if some completed exit names `h`. In the model it
    equals the definition with 0 divergences.
  - **The sink is kept,** and costs nothing in the corpus.
- **D4 is kept** (confidence about 0.7).
  - **Measured on the hard case:** replay was right in 142 of 172 executions, `fired` in 172 of 172.
  - **Tagging samples with `request_id` is also correct.** It loses by putting routing on the fact, against
    if-built-today and design §7.
  - **Add:** an incremental form of `answers` for live subscribers, which the examples' `on_event` pattern
    needs.
  - **Pin:** a one-shot subscription on an eager stream, fired at a node with no element, answers `[]` and is
    spent.
- **D5 is kept,** with E2 as its membership pin.
- **D6 is kept,** with E3.
  - **Refusal reason:** refuse a progress atom on an offered stream with `unsatisfiable`, not `malformed`.
    That is design §6's precedent, and `malformed` is reserved for a body that does not conform.
  - **§6 gains the in-repo examples.** All six use `set` plus a subscription. reuse and redrive call `ensure`
    on a sampled `loss` and must switch to `emit`.
- **D7 is kept, with a stronger rationale.**
  - **The real failure:** emitting after the tick, combined with the checkpoint recipe, saves node s before
    step s's metrics exist. A crash then loses them for good, which is the spec's own §1 failure, not merely
    an off-by-one.
  - **Measured:** moving all 214,415 stepped mycooc commits gives 0 order inversions and 0 parents after
    their children, and no move crosses a `stopped`.
  - **Why move the commits, not the values** (corrected in the walkthrough: migration cost is no
    tiebreaker). A commit asserts that its values are complete, which is true only once the last value
    exists. Moving the commit keeps every record at an honest position: the values stay where they were
    produced, and the commit lands at the first moment its assertion held. Moving the values would instead
    place them before a beat that preceded their computation, against their own `t`.
  - **Pin:** M3's "next commit" is read in 0.3.0 order, before the moves; the two readings differ on 3
    values.
  - **Optional:** a `with w.iteration():` scope.
- **D8:**
  - **Delete "inside an iteration".** It is vacuous: it equals "something is pending".
  - **Replace it with a contract.** A non-error exit asserts that its pending values are complete; to discard
    a torn iteration, raise.
  - **The rule itself stands,** with E1 as its mechanism.
- **D9 is kept and refined.**
  - **Make the premise a substrate-contract clause** in design-v0.2 §4 and the backend checklist, with a
    concurrent "read W, then read the tail" conformance test for each backend, by tier.
    - **Measured to hold:** Postgres with 12 writers, 0 of 17,585 checks failing; slow committers, 0 of
      138,495; SQLite across processes, 0 of 188,787.
    - **Why make it a clause:** a sequence allocator broke it 30,780 times while still leaving a contiguous
      log, so contiguity alone does not guarantee it. Layer 1's cursors already depend on it without saying
      so.
  - **Make the incremental fold public and standalone,** like `pending_stops`. `ensure`'s loop has no Watcher,
    and the TUI may use public API only.
  - **Add** a latest-element read, which the TUI polls.
  - **Drop** the delta form of `aligned`: no consumer polls `aligned`.
  - **Resolve the head once per poll** into one value (node, settled, W) that both reads take.
  - **State the criterion for a polled read:** cost independent of lineage length and log age, given held
    state.
  - **Scope the well-formedness check:** structure on every commit walked; membership only for the records a
    read resolves.
  - **Memory backend:** a tail read is a slice (seq = index + 1), plus a per-topic index.
  - **Counts stored on each commit are rejected:** +24% bytes on mycooc, for a cold-start gain only.
- **§3 pins:**
  - **`every`'s delta baseline lives on the lineage:** the count at the newest commit whose `fired` names the
    request. It is rebuilt at resume and at rewind, otherwise a rewind to 50 delays the next firing from 51
    to 101.
  - **At resume and rewind, the Worker rebuilds fire counts from `fired` and writes any missing expiry
    before it evaluates.** In the model: the old order loses the answer, the new order with a naive resume
    answers twice, and with the rebuild it answers exactly once.
  - **Order within a node** follows the `commits` array, and **membership** is decided by the envelope (about
    7× cheaper). A malformed body raises when its value is read, never when it is counted.
  - **`aligned` cells are uniform `tuple[Element, …]`.** `None | value | list` is ambiguous when a value is
    itself a JSON array.
  - **`Element` gains `seq`.**
  - **`streams()` returns names only.**
  - **`commit_external` refuses a name declared offered.**
  - **The fingerprint guard's deletion is kept.** The backlog records its form with nothing on the wire, and a
    live trigger: mycooc saves 3 of 4 checkpoints before the tick that would name their node (training.py
    :1332, :1346, :1354 against :1369), so §6 gains "save via `tick(checkpoint=save)`".
- **C1 is kept.** Cite if-built-today's sections rather than paraphrasing them: four paraphrases produced four
  drifts.
- **C2 is refined.**
  - Adopt node keys, with N an opaque node name: a seq under one sequencer, minted in layer 6.
  - Keep `at(R,S,M)` as a derived view relative to a head, and put it on the non-monotone list.
  - Lineage as a mere annotation leaves `at(R,S,M)` non-functional (8 of 36 splices).
- **C3:** hash-reveal waits.
  - **Bundling it now saves no migration:** the corpus holds 0 subscribes and 0 unsubscribes.
  - **Its ordering argument is stale:** 0.3.0 already forbids reusing a request id.
  - **Revival trigger:** a second independent asker on shared runs, or authenticated-records being taken up.
  - **A self-report versus external-report split for releases** (`lifecycle.released`) joins the backlog with
    it.

### 11.3 The migration, revised

*The figures below predate the implementation round, which replaced them with measured ones (§12, item 16).*

- **M1 gains a rule for claims with no commit.** A claim whose first commit has no step, or that never
  commits, takes the newest earlier claim's newest commit as its parent. It is an inference, labelled as such.
  Without it, 7 runs read empty (8,449 cells), because a retry that crashed while loading is recorded as a
  fresh start.
- **M4 is kept, and its effect named:** the teardown "done" status no longer wins its cell in 1,204 cells.
- **M5 is rewritten.** The text as written failed three ways:
  - it took in the step-0 loading statuses (1,469 P1 failures);
  - it contradicted D8 on preempted exits (317 cells);
  - it dropped skipped ticks that a later claim had resumed from (all 37 P1 failures).

  **The rewrite** synthesizes a step commit only in three places:
  - for a skipped tick between two real beats;
  - for a trailing step at or below a clean exit's last step;
  - for a trailing step s, when a later claim's first real beat is s+1.

  Two further rules: leading values below a claim's first real beat go to its first commit, and the exit's
  pending values go to the exit node (E1).
- **M6 is kept.** Appending instead of renumbering would add about 1.23M records and leave shapes no 0.4.0
  writer produces.
- **M9's alternative, refusing runs that need synthetic commits, is out.** It would strand 60% of mycooc runs.
- **M11 disappears.** Stepless heartbeats migrate as heartbeats (D1 pin).
- **P2** carries its two by-design exceptions: leading values, and values held by the exit node.
- **P3 becomes per-cell attribution.** Every differing cell must trace to a named rule, and an unattributed
  cell fails the test.
- **Measured under the rewrite:**
  - P1 fails on 0 of 214,415 heartbeats;
  - 0 commits name a later value, 0 parents follow their child, and 0 exit nodes follow their own `stopped`;
  - the named classes:
    - 3,937 cells beyond the head's lineage;
    - 94 cells at steps the lineage revisited;
    - 3,896 stepless values now committed (before the D1 pin, which removes up to 1,780 of them);
    - 2,725 nodes holding several values;
    - 60 loading cells;
    - 1,205 cells from M4;
    - 70 cells from error exits.
  - **Still unattributed:** 39 cells in one run (625dda74…) plus 2 added cells. They need a decision or a
    golden log.
  - **These figures predate E1 and the D1 pin,** and must be re-measured from an implementation of the final
    rules.
- **The only renumbering step.** Reword: 0.2.0 → 0.3.0 only appended, so this is the only step that
  renumbers. The runner **enforces** it: every later step must keep each input record's seq and topic.
- **§6 gains a one-time conversion** of mycooc's step-holding `checkpoint_last` files to nodes, after the log
  migration (L1 #8, which the first draft dropped).

---

## 12. Walkthrough decisions (with the owner, 2026-10-05)

Answers are recorded as they settle, each with what it knocks out.

**Group A (accepted):** 0.1 (D7), 0.2 (D9's visibility premise becomes a substrate-contract clause with a
conformance test), 0.3 (D9's bounded polled reads).
- **The owner's correction:** "moving the values means 4.2M moves" is not an argument, because migration
  cost never breaks a tie. D7's reason is the end state (§11.2, D7).

**Group B (accepted):** 0.4 (order within a commit follows the `commits` list), 0.5 (membership by
envelope; a malformed body raises on read, never on count), 0.6 (`Element(node, seq, value, t)`; `aligned`
cells are always tuples; `streams()` returns names only).
- **The owner asked why the offered set must be known up front.** That reopens D5's framing; see item 6.

**D5 reframed (the owner's questions, accepted):**
- **The real requirement** is that a name's kind is set *by the worker, recorded on the log, before the name
  is used*. Declaring on the claim is just the simplest way to meet it.
- **What gets declared is the worker's _sources_:** what it exposes to delegates, such as a register,
  weights or gradients. Derived metrics are not declared.
- **A delegate's input never reaches the log.** It runs inside the worker, and only its outputs are logged.
  Derived streams are named by the demand that creates them, and today's offered register is the identity
  delegate.
- **Sources can change at runtime, by the worker only.** Dynamic declaration (a worker-written record, in
  force from its record onward *within its claim*; corrected at item 6, since "along the lineage" was
  wrong across a rewind) and derived streams named by their demand go to the programmable-subscriptions
  backlog. Observers change demand, not sources.
- **E2 becomes "the declaration in force at the committing node".** It is identical to per-claim
  declaration today, and it generalizes. This adjusts items 6 and 7.

**Group C (accepted):**
- **0.7:** `every` uses the delta rule, with its baseline read from the lineage and rebuilt at resume and
  rewind.
- **0.8:** `tick()`'s order, with expiries after the commit and fire counts rebuilt at resume and rewind.
- **0.9:** `commit_external`'s checks, including that the name is not a declared source.
- **0.10:** the fingerprint guard is deleted. It moves to the backlog as a recipe needing nothing on the
  wire; mycooc's upgrade gains "save via `tick(checkpoint=save)`".

**Group D (accepted):**
- **0.11 (C1):** correct demand-streams' three misstatements and the carryover's overclaims. Cite
  if-built-today rather than paraphrase it, and turn the carryover into `if-built-today-convergence.md`.
- **0.12 (C3):** the backlog rows, each with its revival trigger:
  - hash-reveal withdrawal;
  - monotonic durations;
  - reclamation tiers;
  - dispute records;
  - identity kept in summaries;
  - a separate `lifecycle.released`;
  - dynamic sources and demand-named derived streams;
  - the fingerprint recipe;
  - minted ids (layer 6).

**Group E (accepted):**
- **The rules:** 0.13 (M2, the step label wins), 0.14 (M3, stepless values go to the next commit in 0.3.0
  order), 0.15 (M4, values after `stopped` stay uncommitted; its effect is named), 0.16 (M6, move and
  renumber: appending would leave shapes no 0.4.0 writer produces), 0.17 (M7, the rename list comes from
  the schemas), 0.18 (M8, the fields of a synthetic commit), 0.19 (M10, a malformed exit is copied as-is;
  its refinement waits for item 5).
- **The tests:** 0.20 (P1; P2 with its exceptions; P3 as per-cell attribution).
- **The rule about renumbering:** 0.21 (the only renumbering step, enforced by the runner).
- **The forced batch is complete.**

**Item 1: D1 adopted, with renames (the owner, for lexical economy):**
- **The methods are named after the records they write,** as `stopped()` already is: `commit()` writes
  `lifecycle.commit`, and `heartbeat()` writes `lifecycle.heartbeat`. "Tick" and "beat" leave the vocabulary.
  - **Why the rename helps:** like E5, it forces every call site to be examined for D7's "emit before you
    commit": mycooc's 9 `tick(` calls and the 2 in the examples.
- **`commit_external(seq)` becomes `include(seq)`.** It adds an outside value to the *next* commit, which
  under the drivers the driver writes, so the old name would misread as "commit now".
- **The commit's field `commits` becomes `values`,** so "commits" no longer means both a list of value seqs
  and E3's count of commits:
  `lifecycle.commit {claim_seq, consumed_seq, parent, values, fired, t}`.
- **A heartbeat on the value plane was considered and rejected:**
  - it must name its claim at arrival, which values do not;
  - it carries the acknowledgement;
  - it would need a reserved metric name;
  - committed, it puts wall-clock-rate elements into the lineage's data.
- **Liveness is already opt-in on both sides:** the writer calls `heartbeat()` or not, and the observer
  sets `heartbeat_timeout` or not.
- **A worker-declared liveness promise** (the threshold belongs with the run, not each observer) is under
  discussion as a backlog entry; see the next entry.

**Liveness promises go to the backlog** ([liveness-promise](backlog/liveness-promise.md)), in the
owner's form:
- **Every liveness record carries `next_within`,** a duration timed from witnessed arrival. `null` means
  no promise.
- **This is better than one promise on the claim:** it varies by phase, and needs no background beating
  thread.
- **A cadence stream on the value plane was rejected:** it has no claim at arrival, and would be
  unattributed during exactly the window it governs.
- **It is not in layer 2.** It adds cleanly later, because `null` truthfully migrates older records.

**Item 2: E1 adopted. `stopped` is the exit node.**
- **The record:** `lifecycle.stopped {completed, error, claim_seq, honored, parent, values, fired, t}`.
  - **`parent`:** the claim's newest commit, else its resume point, else null.
  - **`values`:** the pending values, or `[]` on an error exit. A third party writes `parent: null,
    values: null`, meaning not a node.
  - **`fired`:** kept so the exit can still answer a subscription on its final values.
- **What goes:** `final_commit`, and the migration's synthetic closing commits.
- **What follows:**
  - `{commits: n}` counts iterations exactly;
  - D3's finished node is structural, because `parent` and `resume_from` must name a `lifecycle.commit`;
  - settled if and only if the head is a completed `stopped`.
- **Considered and rejected: the exit as a commit with a final marker.**
  - **`next_within: null` or infinity cannot mark the end:** null already means "alive, no promise".
  - **A body flag hides terminality from routing:** `peek_terminal` and the Watcher would parse commit
    bodies.
  - **Ordinary commits would carry null verdict fields,** and `{commits: n}` would have to parse bodies.
  - **The topic is the final marker.**
- **The shared type is made explicit:**
  - **In the schemas,** `parent`, `values` and `fired` each reference one shared definition (per property,
    not via `allOf`, which does not compose with `additionalProperties: false`).
  - **In the vocabulary,** a `Node` Protocol is satisfied by both `Commit` and `Stopped`, and lineage walks
    take a `Node`.

**Item 3: D2 adopted.**
- **The resume point:** `Worker(channel, *, resume_from)` is required and recorded on `started`.
- **The head of a commitless claim** is its resume node.
- **Two checks:** the Worker validates the named commit at construction, and the recipe asserts it at load.
- **The owner asked whether mycooc should use `steps()` instead. No:**
  - mycooc's aligner owns its loop and calls a per-step callback (mycooc main.py:714, :922). Calling
    `commit()` from a callback is a first-class driver, as in Lightning, the HF Trainer and Keras.
  - Failure 2 holds under any driver.
  - The resume point must be on the claim, which is written when the Worker is entered, before any driver
    runs. So a driver argument is too late whichever driver is used.
- **mycooc's upgrade gains two items:**
  - choose the checkpoint before claiming (read its node, then claim, then load, then assert);
  - move `_emitter.step` before the commit (main.py:922–923, D7).

**Item 4: a rewind is its own record, (C). It replaces both E4's re-claim and the drafted in-claim
commit marker.**
- **The diagnosis (from the owner's question about delegates).** The episode did two jobs:
  - *who* is computing: the witness, given exclusive ownership by the claim CAS, which holds liveness,
    leases and process-memory state;
  - *where* it is computing from: the position.

  A rewind changes only *where*.
  - **The re-claim forced *who* to change too.** Hence its costs: void leases, `serve()` retiring at zero
    demand, a relaunch race window, and a stop pin.
  - **The drafted marker put *where* in a commit.** Hence the over-count.
- **The records:**
  - **The claim states *who*, plus where it starts.** It must, or failure 2 returns.
  - **Commits advance the position.**
  - **`w.rewind(checkpoint)` writes `lifecycle.rewind {claim_seq, resume_from, t}`,** which restates the
    position only. The field name is reused from `started`.
- **The head:** the newest claim's newest position-bearing record: a commit, a rewind, the claim itself,
  or its `stopped` (under E1 a worker's `stopped` is the exit node; corrected at item 5).
- **One rebuild trigger:** "the position was declared", by a claim or a rewind. It covers 0.8's rebuild at
  resume and at rewind.
- **Pins:**
  - **The target** is any commit of the run, validated as `resume_from` is, with the same load assertion.
  - **Pending values at a rewind are discarded.** They stay on the log outside every lineage, as M4 does;
    commit first to keep them.
  - **A pending stop stays pending** and is honoured at the next commit or exit, with no special rule.
  - **The rewind record witnesses liveness,** as commits and heartbeats do (liveness-promise updated).
- **Against the rubric:**
  - *independence*: a rewind cannot be derived without changing ownership;
  - *orthogonality*: each record carries one concern.
- **The earlier objection was backwards.** "The marker carries what a claim carries, minus the claim" is
  the claim's factorization into its two concerns, not redundancy.
- **The state classes** ((i) lineage folds, rebuilt at any position change; (ii) wall time, which survives
  a rewind and dies with the witness; (iii) unlogged-source folds, reset at any position change) are
  recorded in [programmable-subscriptions](backlog/programmable-subscriptions.md). State with no declared
  class is treated as (iii), which reproduces the re-claim's behaviour, so (C) weakly dominates.
- **Removed from the tracked list:** time-lease continuity across an in-process rewind. (C) provides it.
- **`serve()` explained for the record.** It is the driver for leased demand: it runs while live
  subscriptions exist and retires at zero (examples/monitor). Leases (time or count atoms) are bound to
  their episode by `lifecycle.bound` (time-lease-boundary.md).

**Item 5: D3 adopted in a refined form.**
- **Settledness is a property of a node.**
  - **A node is closed if and only if it is a completed exit.** By E1 and item 4, nothing extends it.
  - **A read is settled if and only if its subject cannot grow:** always under `AtNode(x)`, and under
    `LatestClaimHead` if and only if the head is closed.
  - **The "newest claim" clause is dropped.** Item 4's head rule already confines the head to the newest
    claim.
  - **Permanence is trivial,** because a node's kind never changes.
  - **Test invariant:** settled if and only if the head is a completed `stopped`.
- **Verdicts separate the two viewpoints by record,** replacing the outcome-kind rank as the primary rule.
  - **`lifecycle.stopped` is the worker's self-report only.** There is at most one per claim, by the
    dying-breath CAS, and under E1 it is always a node. E1's `values: null` case is removed.
  - **A third party's death report names the claim** and carries its evidence (for example
    `slurm:TIMEOUT`). It sits on the external plane beside `launcher.terminated`, which names a launch;
    its name is still to settle. This is the backlog's `lifecycle.released`, corrected to the external
    plane and moved into layer 2.
  - **The verdict is the readable self-report if one exists, else the external reports.** That is the
    precedence `peek_terminal` already applies between `stopped` and `launcher.terminated`
    (observables.py:346).
  - **An unreadable self-report counts as absent.** It is reported, not raised.
  - **A fixed rank survives only among disagreeing external reports.** There are none in the corpus.
- **The evidence (mycooc):**
  - **The reclaim tool is a reaper.** It reads SLURM accounting, and writes "the `lifecycle.stopped` record
    the killed runner would have emitted" (scripts/reclaim_experiment.py:21), because no external record
    can name a claim: mycooc's claims carry no launch ids.
  - **`repair_malformed_stopped.py` exists only because a malformed terminal raises forever.**
  - **The six corpus conflicts** (d5763f completed and 94a78 errored, then released; four malformed, then
    repaired) all resolve as the rank did, now for a stated reason.
- **mycooc's upgrade gains two items:**
  - the reclaim tool writes the external report instead of `stopped`;
  - the repair script is deleted.
- **Item 10's migration census:** reclassify 0.3.0's third-party stops.
  - **By structure:** a second `stopped` for an already-stopped claim is external.
  - **A lone release for a stranded claim** needs more evidence.
  - **A stop that cannot be classified** stays as written, which repeats the original misattribution and
    adds none.

**Item 6: D5 and E2 adopted, with the source framing.**
- **The declaration:** `Worker(…, sources=[names])` is required and keyword-only, and is recorded on
  `lifecycle.started {…, sources}`.
- **Misuse raises:** `set` on an undeclared name, `emit` on a source, and `include` of a source.
- **Membership:** a committed value is an element if and only if the claim that committed it did not
  declare the name a source. It costs one point read per claim, cacheable.
- **Why this is natural (who/where):**
  - sources are a property of the witness, so they go on the claim;
  - membership asks which witness committed the value, so it is decided by name;
  - a name may change kind between claims (code edited, then resumed).
- **Correction:** a future dynamic declaration is in force within its claim, from its record onward (a
  witness property, like class (ii)). It is not in force "along the lineage", which fails across a
  rewind: the process still exposes the source after rewinding past the declaration.
- **Rejected:**
  - first use fixes the kind;
  - one kind for the whole run, which needs a run-level arbiter and refuses code changes;
  - a kind flag on each value, which is derivable and costs bytes on every value.

**Item 7: D6 and E3 adopted.**
- **A source's samples are keyed by their committing node,** read through `aligned` and `answers`. Their
  identity (name, node) does not depend on the asker; their existence does.
- **Progress atoms, `prefix` and `ensure(n)` name eager streams only.** A progress atom naming a source
  gets a `nak` of reason `unsatisfiable`. The circularity check is deleted.
- **`{commits: n}` is the lineage's own clock.** It is a position coordinate (class (i)), durable across
  claims, and takes over `step`'s place as the run-absolute atom in time-lease-boundary.
- **The atom set:** time, firing count, `{stream, n}` on eager streams, and `{commits: n}`.
- **Pin:** a subscription is judged against the declaration of the claim evaluating it. A progress atom on
  a name that claim declares a source gets `unsatisfiable`, keeping "registered ⟺ a future firing is
  possible" for `serve()`. The request id is spent, so the asker resubscribes if the kind changes back.
- **The examples:**
  - four switch `every {step: 1}` to `{commits: 1}`;
  - reuse and redrive switch `loss` to `emit`.

**Item 8: D4 adopted, with the source/eager duality.**
- **The wart (the owner's question about ergonomics).** As drafted, a subscription could *target* an eager
  stream, which merges two acts:
  - asking for a value that exists only on demand (a source), where firing immediately is natural;
  - selecting from values that exist regardless (eager), where firing on events is natural.

  The `[]` first answers and the `until {count: 2}` idiom were the symptoms. Worse, a one-shot for "the
  next `eval_acc`" would be answered `[]` and spent, silently. Today's code avoided this by accident:
  `emit` also updates the register (worker.py:247).
- **The duality:**
  - **Subscription targets are sources,** which are sampled, or nothing, which is pure demand (a keepalive;
    worker.py:547 already allows it). Targeting an eager name gets a `nak` of `unsatisfiable`: "eager
    streams are read, not subscribed".
  - **Progress atoms are eager streams or `{commits: n}`** (item 7).
  - In short: you ask for sources and measure by eager streams. This matches if-built-today (asking applies
    to facts that exist on demand) and the lazy-stream direction (demanding an existing element is a read).
- **What follows:**
  - **`fired`** names source subscriptions only; time atoms, the registration point, resume and rewind
    still need it.
  - **`answers()`** returns samples, one per firing.
  - **The immediate first firing stays,** now natural everywhere.
  - **Live observers of eager data read the log incrementally,** via `Watcher.iter_events` and D9's fold,
    with no latency penalty.
- **Pins:**
  - **An incremental `answers`** for live subscribers.
  - **A source with no value yet does not fire.** The subscription waits for the first `set`, as reading
    an unbound dataflow variable blocks; today it answers `None`, at worker.py:547.
  - **The `[]` pin and the idiom are dropped.**
- **Rejected:**
  - replaying the schedule;
  - tagging samples with request ids (routing on the fact; cannot cover eager streams);
  - a separate `answered` record per firing (two records for one event).
- **Applicability:** the corpus has 0 subscriptions, and the in-repo examples already target sources.
- **Against if-built-today (the owner's question):** the duality is its own demand/subscription split.
  It is stricter in two places: a `nak` where if-built-today leaves a question unanswered (mild, kept for
  `serve()`), and one kind per name (real but latent, inherited from option S's indices; converges via
  C2's node keys). Both are recorded in if-built-today-carryover.md, which becomes the convergence map per
  0.11.

**Item 9: E5 adopted.**
- **Every progress number is a count:** elements or commits along the lineage up to the head. Empty is 0,
  with no sentinel, and a target n is reached if and only if count ≥ n.
- **Elements are accessed from 0,** so counts and indices are `len` and `range(N)` of one convention.
- **One read, `prefix_len(ch, X, *, head)`,** where X is a stream or `Commits()`.
- **`progress` is retired, not redefined.** Rubric 1 (it was the same number as `prefix_len`), and a new
  name breaks every unedited call site loudly instead of leaving a silent off-by-one.
- **The rest:** `aligned`'s x-axis is the count at each node, and `Running.step` becomes `Running.head`
  plus its commit count.
- **Evidence for the change:**
  - today's fencepost paragraph (observables.py:579);
  - the memoizer's -1 sentinel;
  - translation's mirror has drifted (keys.py:75, `floor_ok(None, 0)` is False).

**Item 10: the migration's remaining rules (adopted).**
- **(a) A commitless claim's `resume_from`** is the newest earlier claim's newest commit, labelled as an
  inference. `null` would make 7 runs read empty (8,449 cells), and mycooc's `checkpoint_last.pt` is a
  consumer's file, while the generic migration uses the log alone.
- **(b) M5 as rewritten** (§11.3). A step commit is synthesized only:
  - for a tick skipped between two real beats;
  - for a trailing step at or below a clean exit's last step;
  - for a trailing step s, when a later claim's first real beat is s+1.

  Leading values go to the claim's first commit.
- **(c) A worker-written 0.3.0 `stopped` becomes the exit node (E1):**
  - `parent` is the newest commit, else the resume point;
  - `values` are those between the last beat and the stop, or `[]` on an error exit;
  - `fired` is `[]`;
  - `final_step` is dropped.
- **(d) Third-party stops are reclassified (item 5).**
  - **mycooc's four writers:**
    1. the runner's hard-crash backstop (run_experiment.py:689, which copies `final_step=progress(...)`;
       hence d5763f's step 600);
    2. the "clear staged stop" path (run_experiment.py:3926);
    3. the reclaim tool;
    4. the repair script.
  - **Classification:**
    - a second `stopped` for an already-stopped claim is external;
    - writer 1's error text, "subprocess exited N with no terminal record", is external;
    - the rest goes to a census in the implementation round.
  - **The fallback for an unclassifiable lone stop** is a self-report that commits nothing. It never
    vouches for a possibly torn iteration, and errs towards committing too little, as M4 does.
- **(e) In the implementation round:** implement the final rules, re-measure P1–P3 with per-cell
  attribution, run (d)'s census, and resolve the 41 cells in 625dda74. Each must get a named rule or a
  golden log, never a tolerance.
- **Already gone:** M11 (D1's pin), M12 (E1), and any migration of subscriptions or in-claim rewinds (none
  exist).
- **mycooc's upgrade:**
  - writers 1 and 3 write the claim-named external death report;
  - writer 2 withdraws its stops (see the next entry);
  - writer 4 is deleted;
  - the `checkpoint_last` files are converted from steps to nodes.
- **The owner pulled withdrawal into layer 2,** rather than leaving writer 2 as a known gap.

**Item 10b: withdrawal, pulled into layer 2 (adopted).**
- **The finding:** `control.unsubscribe` mixed two viewpoints. Askers wrote it to withdraw a subscription,
  and the worker wrote it as the expiry record (design-v0.2 §107). Stops had no withdrawal at all, so
  mycooc's runner forged a `stopped` (writer 2).
- **The split by viewpoint:**
  - **`control.withdraw`** is the asker's record (`request_id` = the request; body `{t}`), for stops and
    subscriptions alike.
  - **`lifecycle.expired`** is the worker's record (`request_id` = the subscription; body
    `{claim_seq, t}`), written after the commit at which the subscription ended.
  - **`control.unsubscribe` is retired.**
- **The answer fold, by name and order-free:**
  - a subscription is spent by `expired`, `withdraw` or `nak`;
  - a stop is spent by `stopped.honored`, `withdraw` or `nak`.
- **Expiry is not a field on the commit,** because the polled "is R spent?" fold must route on the envelope
  `request_id` (as in item 2).
- **Pins:**
  - **A withdrawal after `commit()` has returned True for a stop is too late.** The `stopped` honours only
    stops still pending, and the outcome is preempted.
  - **A withdrawn lease is spent, and so void.**
  - **Honor-system authority, as before.** Hash-reveal (C3) stays backlogged with no reserved field.
- **The migration:**
  - there is nothing to split, since the corpus has 0 subscribes and 0 unsubscribes;
  - a 0.3.0 `stopped` that names no claim becomes one `control.withdraw` for each stop it honours. That
    writer 2's records name no claim joins (d)'s census.
- **mycooc's writer 2** writes one `control.withdraw` for each id in `undischarged_stops`.

**Item 11: C2, carried into if-built-today (adopted).**
- **How:** seven drafts and seven independent reviews: four rounds of fixes, a scoped check and two
  holistic reads. The forms withdrawn along the way are recorded by topic in
  [if-built-today/decisions/5-domain.md](backlog/if-built-today/decisions/5-domain.md).
- **The C2 list, as adopted:**
  - **values keyed by node,** one record per event: `commit(c(E, K), P, Ms)` and
    `exit(end(E), P, Ms, O, Hs)`, with `claim(E, I, From)` and `rewind(E, K, To)`. The run is the root
    node, at depth 0, and every node vouches for its values, honestly whatever the determinism;
  - **completion per lineage:** `finished(H, A)` from a completed exit replaces the run-keyed tail;
  - **stops scoped:** a stop names a claim;
  - **the claim order carries the observer plane,** and the head is the one report for lineage reads;
  - **the stale positional text struck:** README's calibration, 3-questions' positional pairing, the
    `discharged` example and the `unsubscribe` claim;
  - **mapped:** verdicts by viewpoint, the per-node dual of progress, withdrawal and the source/eager
    split;
  - **noted:** the liveness promise as a lease run the other way.
- **Beyond the C2 list, each approved during the reviews:**
  - **the step is depth,** not a user stream; a user's own step label is a metric;
  - **node names carry claim, order and kind,** so "an exit cannot be extended" is a check on the message
    alone;
  - **the claim order `I` is data;** without an arbiter it is a Lamport clock (`open.md` 5);
  - **a forbid,** `forbidden(F, N)`: forbidding further computation, separate from a stop, halting the
    claims running in its scope and needing no roster. It is if-built-today's own; layer 2 has none;
  - **stops and forbids named by hash commitments** and withdrawn by reveal, as leases are, with a
    `withdrawn` record always ground;
  - **`refused` and `died`** in if-built-today's vocabulary, and a stop discharged once its claim has
    ended;
  - **layout:** a new §"Halting and withdrawing", and §"Runstate, mapped" (formerly "Today's runstate,
    mapped") with shipped and decided kept apart.
- **For the spec round:** whether runstate's backlog gains a forbid row, and what its revival trigger is.
  Hash-reveal withdrawal is C3's row; for runstate it would reach stops through item 10b's
  `control.withdraw`.

**Item 12: the external-report plane is `supervisor.*`, and the forbid joins run-scoped-halt (adopted).**
- **The plane is a viewpoint, not a role.** [design-v0.3-exploration](design-v0.3-exploration.md) §7 defines it
  as "an external observer's report", whose writer must outlive the worker: "a supervisor writing about a
  *child*". `launcher` named the first such survivor runstate had. The death report is the record that shows
  the gap: its usual writer, mycooc's reclaim tool reading SLURM accounting, launched nothing.
- **The records,** renamed with the convention's bump:
  - `supervisor.launched` and `supervisor.terminated`, changed only in prefix;
  - `supervisor.died {claim_seq, outcome, evidence, t}`, item 5's death report. `outcome` is `preempted`,
    `errored` or `killed`, since a death is never a completion; `evidence` is free text no fold reads (for
    example `slurm:TIMEOUT`); `t` is the reporter's clock when it saw the death, as on `terminated`.
- **What a record names is not the plane's business.** `launched` and `terminated` name a launch, and `died`
  names a claim. Prefixes already mix aims (`lifecycle.nak` names a request as well as a claim); what a prefix
  fixes is the viewpoint. The launch id stays required on the first two so that a death can be attributed;
  `died` is attributed by `claim_seq`.
- **Unchanged:** the Python `Launcher` classes keep their names, since they launch; so does the launch id
  (`RUNSTATE_LAUNCH_ID`).
- **Rejected:**
  - keeping `launcher.*`: it names one survivor, not the viewpoint;
  - `reaper.died`: one viewpoint split across two prefixes;
  - `lifecycle.died`: it breaks item 5's rule that `lifecycle.*` is the worker's own word, which the
    verdict's precedence routes on;
  - `external.*`: exact, but defined by negation.
- **The forbid goes into [run-scoped-halt](backlog/run-scoped-halt.md),** not a new row. That entry is R6's
  durable ceiling, an open need waiting on "the write path first", and if-built-today's forbid is a write
  path. It answers the entry's objections 1–3: a forbid is a positive record by its poster, needs no arbiter,
  and gates commits as well as claims, so a claim that lands after it is halted at its first commit; only its
  poster's withdrawal ends it. Objection 4 (one run id shared across experiments, so a halt's natural scope
  is the demand) and the authority rule stay open, and the trigger stays the entry's existing need.

**Item 13: the drafting round's three open questions, decided for the long term; the registers (adopted).**
- **How:** three independent reviews of revision 2 (rubric, precision, fidelity) raised three questions the
  walkthrough had not settled. The owner asked for the right long-term design, accepting more machinery if the
  design is more natural. Proposals were tested by two adversaries, one arguing from first principles and the
  alternatives, one building breaking histories and running them on today's code. Both kept each proposal's
  core and broke a sub-claim of each; the decisions below are the proposals with their fixes.
- **Q1. A death report ends the claim it names, and the evidence is gathered by writers.**
  - **One predicate:** a claim has ended once its own `lifecycle.stopped` or a `supervisor.died` naming it is
    on the log. The claim gate, lease voiding, the verdict and the Watcher all use it, and nothing else. No
    fold calls `resolve()`, and no fold matches a `supervisor.terminated` to a claim.
  - **Writers report deaths,** through one library helper that appends with a CAS against the tail it
    inspected, and refuses when the claim's handle probes alive unless forced. `LocalLauncher.reap` writes
    `died` when the process it reaped is the claimant; a claimant that finds the held claim's same-host handle
    dead reports it before claiming; mycooc's reclaim tool reports from SLURM accounting. Observers never
    write it: the Watcher's inference tiers stay display-only.
  - **`terminated` never ends a claim and never enters a claimed run's verdict.** It stays the verdict of a
    run nothing claimed, and forensics. That deletes the case where a submit-time `sbatch` exit 0 outranked a
    death report and read as COMPLETED, and the last rule comparing a supervisor's position with a worker's.
  - **The Worker fences itself:** its drain reads `supervisor.died`, and one naming its own claim latches it.
    It then honours no stop, commits nothing and writes nothing more.
  - **Evidence:** "a mistaken release costs waste, not corruption" was false. Run on today's code, a worker
    released by mistake took the operator's stop, so no later claim honoured it; and mycooc's four checkpoint
    files, written one after another, would interleave two writers, which §6's load assertion cannot see. With
    the latch, a mistaken release costs at most the iteration in flight. The reader-side probe veto was inert
    for the consumer that motivates the record (its targets are foreign-host, where the probe returns `None`).
  - **Left open:** pid reuse fools the helper's probe while the reusing process lives; the `?start=T` handle
    disambiguator (conventions-hygiene F9) closes it.
- **Q2. Leases live per claim, durable requests per lineage, and the launch decision reads demand by name.**
  - **Firing and expiry are facts at a node:** commits and exits carry `fired` and `expired`, written in one
    record. `lifecycle.expired` (item 10b) is retired, and with it the crash window between a node and its
    expiry.
  - **A lease** is a subscription with any time or count atom. As in 0.3.0 it is bound to its claim at drain,
    its firing state is the claim's (kept across a rewind), and it is void once the claim has ended.
    Rebuilding count from the lineage was rejected: a service that dies at its first firing was relaunched
    once under the count lease, and forever without it (run on today's code).
  - **A durable subscription,** whose atoms are all lineage coordinates, is served per lineage: the Worker
    re-arms it on any lineage that has not expired it, and rebuilds its counts from `fired`. Its answers are
    per lineage.
  - **Demand is by name:** a request is spent once any node expires it, or a `withdraw` or `nak` names it, and
    nothing is demanded of a run whose head is closed. A fresh claim that crashes before its first commit
    cannot revive answered demand, which a head-relative read let it do, looping relaunches.
  - **A worker that holds no state resumes from the head,** so a stateless service does not re-serve every
    request that expired on an older branch. Q3 lets that head be a retire exit.
  - **The boundary:** a request whose answers are lineage data is per lineage; a stop, a request about the
    process, is by name.
- **Q3. Closed means completed; extendable means the exit's iteration is whole.**
  - **The exit records whether its open iteration is whole:** `values` is the committed pending values, or
    null when a torn iteration was discarded. Every error exit writes null, and so does a non-error exit after
    `w.discard()`. This is the rubric review's B1: how a claim ended and whether its iteration is whole were
    one bit. It amends item 2's "an error exit writes `values: []`".
  - **A node is closed iff it is a completed exit, and extendable iff it is a commit or an exit that is
    neither completed nor torn.** `parent`, `resume_from` and a rewind's target name an extendable node, and
    the checkpoint recipe saves after an exit only when it is extendable.
  - **Evidence:** extending a torn exit duplicated values mid-lineage (15 `params` elements for 12 commits);
    with a bare discard, the recipe saved state half an iteration ahead of its node. Revision 2's "an exit is
    never a parent" made a chunk's post-loop values invisible to every later claim, so `ensure` on them never
    completed.
  - **"Step" is the `Commits()` count everywhere,** not depth, since a lineage may now pass through an exit.
    if-built-today's step is depth; a follow-up there.
- **Q4. Registers stay outside every lineage, in the migration as natively:** M3 is dropped. The migration
  no longer produces a shape no 0.4.0 writer produces, and registers are read by
  [completed-opt-in](specs/completed-opt-in.md)'s register recipe.

**Item 14: the owner's pass over the spec's drafting choices (2026-10-19 to 2026-10-23; adopted).** Recorded
as each settled; none is open.
- **`honored` names every stop whose condition held at a node of the claim, even one withdrawn since**
  (approved). It amends a pin in item 10b's proposal: under it, a `withdraw` that landed between a stop's
  decision and the exit gave a `preempted` exit naming no stop, and one that landed a seq later named it,
  for the same worker doing the same thing.
- **An `until` that already holds at registration expires at the next node** (approved), where 0.3.0
  refused it. Whether it holds depends on the position, which a rewind moves: an expiry is a fact on one
  lineage, a refusal a fact on none. Read at commit 99 or at 101, the same request now ends the same way.
- **Pure demand has a lifetime, not a schedule** (adopted; amends item 8). The draft had `fired` name pure
  demand, to rebuild its `every` baseline after a rewind. That was circular: the baseline decides only when
  a pure demand next fires, and its firing changes nothing but `fired`. No reader uses the entry: `answers`
  is empty either way, the demand fold reads `expired`, `withdraw` and `nak`, a lease's count is the
  claim's, and `await_consumed` has the watermark.
  - **The principle:** a firing takes a sample. A pure demand takes none, so it never fires, and `fired`
    needs no exception.
  - **Its lifetime** is layer 2's "live while not spent": until its `until` holds at a node, or a
    withdrawal or refusal names it; a lease is also void with its claim. With no `until`, it lasts until
    withdrawn, as an `every` with no `until` does in 0.3.0.
  - **A `from`, an `every` or a `{count: n}` atom on a pure demand** is refused as `malformed`. In 0.3.0's
    algebra, `every`'s period changed nothing there: it was the switch between a one-shot demand and a
    lasting one, and "live until X" had many spellings. Accepting the fields and ignoring them was
    rejected: it would silently turn 0.3.0's `{from: F}`, "live until F", into "live forever".
  - **Lost:** the firing-budget lease (`every E` with `until {count: n}`), a horizon relative to
    registration. The asker writes the absolute target instead, which is durable and survives a relaunch.
  - **Against if-built-today:** a demand there has a lifetime, its lease, and a subscription adds routing;
    a pure demand is now that bare demand.
- **A claim already reported dead may be reported again** (approved). Under first-report-wins the verdict
  would depend on timing: a claimant, which reports only while the claim is held, tends to arrive first and
  knows least, and the launcher's reap, which knows the exit code or signal, often arrives second.
- **The verdict keeps every death report: its outcomes are a set** (adopted, the owner's proposal; it
  replaces the drafted order errored, killed, preempted).
  - **Why nothing is chosen:** the reports do not compete. They describe a death at different levels, and
    several can be true at once: SLURM delivers a preemption by a signal. A single outcome is a projection
    of the set, so it is a policy, and a policy belongs to the consumer: the argument that keeps a `success`
    bool out of `RunResult`, one level up.
  - **What follows for free:** union is order-free and idempotent, so the protocol needs no precedence among
    outcomes, and the order comes off the interop list. The set only grows as reports arrive, so a test such
    as "some report says errored" stays true once true, and one such as "only preempted" visibly may not.
  - **`lost`,** a new outcome, is what a claimant's dead probe writes: it saw the process gone and nothing of
    how. The drafted probe wrote `killed`, a guess that outranked SLURM's `preempted` under the order. In the
    verdict, `lost` is dropped beside any other outcome, since it says only what every death report says, and
    alone it is a failure, as `presumed_dead` is.
  - **`RunResult`** takes `outcomes: frozenset[Outcome]`, one element under every other viewpoint, and
    `reports`, the death reports with their evidence. Consumers test membership: `ERRORED in outcomes`, or
    `outcomes & Outcome.failures()`; `ensure` and `sweep` state their rules so.
  - **Left open:** a report cannot contest another; dispute records stay backlogged, with their revival
    trigger.
- **Two death-report writers beyond item 13's list** (approved): `ThreadLauncher`, when its thread finishes
  with no exit, since a thread shares its launcher's pid and no claimant's probe can see it gone, and so it
  passes `force=True`; and `ensure` on each poll while it waits on a claim, since a layer-2 claim is held
  until a record ends it and a custom producer's re-drive would otherwise loop on a dead foreign claim.
- **Who writes a death report is a matter of acts and evidence, not of kinds of party** (the owner: observer,
  writer and the rest are roles, and one entity may hold several). It replaces the drafted "observers never
  write one":
  - **evidence, never inference:** a reap, a same-host dead probe and an accounting record are evidence;
    staleness, silence and a cross-host absence are not, since a report would fence a slow, live worker;
  - **evidence without a cause reports only while the claim is held:** a probe's `lost` adds nothing once
    anything has ended it, while a reap or an accounting record adds a cause;
  - **a read never writes:** `peek_terminal`, `Watcher.poll`, the folds and every `is_alive()` stay pure,
    and a party that watches and wants its probe recorded calls the helper. A reap is the one exception:
    `waitpid` yields the status once, so whoever reaps records it there.
  - **So the drafted foreign-episode writer moves:** `is_alive()` writing on every poll was a query with a
    side effect. `report_if_dead(ch)` (working name), the probe's report, is called explicitly by the
    library's actors: the claim path, `relaunch_if_needed` and `ensure_served` before acting, and `ensure`
    on each poll.
- **What a reap and a finished thread report** (approved, amending the draft's exit 0 as `errored`): a signal
  is `killed`, a nonzero exit code or a raise `errored`, and an exit 0 or a thread's return `lost`, which
  reports only while the claim is held.
  - **Why exit 0 is `lost`:** it says the process ended by its own choice, and nothing of why it left no
    exit. `errored` asserts an error the process denied.
  - **The fence makes this case:** a worker released by a mistaken report sees its stop, writes nothing, and
    exits 0. As `errored`, its reap turned a mistaken `preempted` into {preempted, errored}, and `ensure`
    raised on a preemption; as `lost`, the reap adds nothing to an ended claim. `terminated` still records
    the exit 0 against the launch. mycooc's hard-crash backstop already writes nothing for an exit 0
    (run_experiment.py:718).
  - **A signal stays `killed`:** mycooc's backstop calls one `errored`, because in its deployment a signal
    that reaches it is a crash. The library's reap knows only that a signal ended the process.
- **`RunResult.reason` becomes `tier: Tier`** (approved): a closed enum naming the viewpoint the verdict rests
  on. `reason` repeated the outcome under a self-report and under `terminated`, and under death reports would
  have been the constant `"died"`. Only the Watcher's inference tiers said anything new. No consumer reads
  it, and its one reader in runstate, `worker_completed`, telling a worker's completion from a reaped exit 0,
  loses that reading in layer 2 and becomes "the head is closed" (spec §5).
- **`last_activity` dates no `died` and no `terminated`** (approved): it asks when the run itself last did
  something, and a report's `t` is when its writer saw an end (#42). Dating a report by the death fails for
  a probe, which cannot know when it was. mycooc's reclaim tool, which dates its forged `stopped` by SLURM's
  end time to work around #42, no longer needs to.
- **An unreadable record that ends a claim counts as `lost`** (approved, replacing the drafted
  `presumed_dead` for a claim only unreadable records ended): it says only that the claim ended, which is all
  `lost` says, so alone it is the verdict and beside a readable report it drops, and the special case goes.
  `presumed_dead` keeps one meaning, the Watcher's inference about a claim the log has not ended.
  - **Nothing is lost:** the record stays on the log, and `RunResult.malformed` names its seq.
  - **Reading more of it was rejected:** a lenient parse is a second reader that another implementation need
    not share, so one log could get two verdicts, and reading a malformed exit as completed would close its
    lineage for good. Recording what happened stays an ordinary act: a death report from anyone with
    evidence, or, for a completion, a relaunch that writes a valid exit. Only the claim's own exit closes a
    lineage.
- **Cluster 2 of the drafting choices: subscriptions, leases and stops** (approved).
  - **A re-send may change the schedule, not the target or the class:** a re-send may change what only the
    worker reads, never what readers read. `answers` reads the target, and voiding reads the class; letting
    either change would make every reader find which send was in force at each node. The refusal spends the
    id, since its `nak` names the original too: the unsoundness reference-by-name measured, accepted here
    because the asker contradicted itself. Renewal by a fresh id and a withdrawal was rejected, at about three
    records per renewal for lazy-launch's keepalives.
  - **An accepted re-send registers afresh what the claim holds:** a lease's time atoms count from the
    re-send, its fire count returns to 0 and its time baseline is dropped, as in 0.3.0, which is how a lease
    is renewed. What the lineage holds stays. The draft left this unsaid.
  - **A stop's `from` is evaluated at heartbeats too,** with the position's counts and the heartbeat's
    clock, and the decision stays latched. Evaluating only at nodes was a seam: a stop with no `from` already
    landed at a heartbeat, while `from {time_seconds: 0}`, or a time `from` come due inside a slow iteration,
    waited for the next commit. Lineage counts change only at nodes, so only time atoms and stops drained
    at the heartbeat decide differently. `honored` names a stop that held at a heartbeat, by decision (1).
  - **A lease binding is written by CAS,** re-draining on a lost CAS. A binding that landed after an unread
    death report would void the request for good, where a racing commit costs one iteration. Ignoring such a
    binding by a reader rule was rejected: it depends on position, so `live_demand` would depend on order.
  - **A lineage atom's baseline is read on the lineage, for a lease too** (amending item 13's "its firing
    state is the claim's" for lineage baselines only). The draft clamped a lease's baseline to a rewind's
    target, which counts from a position no firing happened at: with `from {commits: 50}, every {commits:
    10}`, fired at 50, 60 and 70 and rewound to 45, it fired at 55, 65, and so on, where the lineage gives
    50, 60, and so on. Item 13's reason for keeping a lease's state on the claim was the fire count, which a
    relaunch loop would otherwise refund; the clock, fire count and expiry stay the claim's.
  - **Every threshold takes n ≥ 0** (amending §3's schema list, which had `{stream, n}` from 1, and the
    draft's n ≥ 1 for `{commits: n}`). The draft argued that a 0 only re-spells an absent `from` or an
    `until` that holds. That missed `every`, where a 0 fires at every evaluation, the exit included: an
    `every {commits: 1}` that fired at the last commit does not fire at the exit, so a source `set` after the
    loop went unsampled, and only a lease could say it, with `every {time_seconds: 0}`. Re-spelling is no
    reason to refuse in a free algebra, which `{any: [a, a]}` already shows. One minimum serves all four
    atoms, as one served 0.3.0's three.
- **Cluster 3 of the drafting choices: the Worker's and `ensure`'s API.**
  - **`ensure` returns `Reached(node)` or `Unreachable(closed_at)`** (approved: "a different field name on a
    different type"). Both stay values, not exceptions: a target the run completed short of, as by early
    stopping, is an answer about the run, unlike the failure exits. The drafted `Unreachable(node)` shared
    `.node` with `Reached`, so `ensure(...).node` type-checked on the union and a run that ended at 80 read as
    one that reached 100, a truncation no log shows. With the field named apart, the checker makes the caller
    narrow first. `prefix` gets the same fix: `elements` only on `Complete`, and `partial` on `Pending` and
    `SettledShort`, where D3's `Pending(have, node)` had once kept them apart.
  - **After `discard()`, `commit()` and `heartbeat()` write nothing and return True** (approved). The state
    in memory is part of an iteration ahead of the position, so nothing after it can commit honestly. A
    return of True ends every driver, and a callback host that calls its hook once more is not crashed by a
    raise. `emit`, `set`, `include` and `rewind` raise, as the worker's own misuse, unlike the fence's silent
    no-ops for a release the worker cannot see. Added: `heartbeat()` does not drain, since a lease it bound
    would be voided at once by the exit, which registers nothing.
  - **Checkpoint saves are a required `save=` Strategy, the `SavePolicy` Protocol** (approved). The Worker,
    not the loop, runs the save: only it sees the instant a node lands, before the caller's code changes
    state, and only it knows whether the exit it wrote is extendable. Saving before the commit is the lag
    lineage-graph test 1 measured, and saving after a torn exit writes a checkpoint ahead of its node. Added:
    the draft named `NoSave` and `Every` but no interface, which made them a closed set; with
    `after(node, *, commits, exit)` the Worker fixes when a save may happen, and the policy only whether, so
    a wall-time cadence is the caller's own few lines.
  - **One `Target(x, n)` serves `steps()`, `ensure` and the producer seam** (approved, unchanged). One target
    crosses `ensure` → `extend` → `steps(until)` with no conversion for the two sides to disagree in, and its
    constructor checks `n` and the kind of `x` once. `Commits()` is a marker type, which no stream name can
    collide with. `prefix` keeps `(stream, n)`, since taking a `Target` would need a runtime refusal of
    `Commits()`.
  - **`resume_from: NodeRef | LatestClaimHead | None`, and `rewind(target: NodeRef | None)`** (approved).
    The draft took a checkpoint or any head Strategy, and refused a resolved `Head` at runtime: a `Head` is a
    `HeadStrategy` too, and a `LatestClaimHead` resolved before the claim may miss a commit made in between,
    so a woken service would branch from an older node and re-serve what the newer branch expired (item 13).
    The refusal was also inconsistent, since `AtNode(x)` names a fixed node just as a stale `Head` at x does.
    Added: the draft's `Checkpoint` Protocol had one member, `.node: int`, so any object with an integer
    `.node` satisfied it; its name claimed "state saved here" and it checked "names a node". (The approval
    cited `Reached`, `Complete`, `Pending` and `SettledShort` as such objects. The holistic read below found
    each one's `.node` is `int | None`, since a head may be the empty lineage, so none of them is one; the
    decision stands on the rest.) A nominal checkpoint would check nothing the Worker reads, which is only
    `.node`; the mistake it would catch, loading one checkpoint and naming another node, the load assertion
    already catches; and it would make consumers inherit from runstate. So the Protocol is renamed for what it
    checks, `NodeRef`. A checkpoint is one, `AtNode(x)` is one, and so a stateless worker's fixed node is not
    an extra case but the checkpoint case without state. It is rare at a claim's start (branching from before
    a faulty version's commits), but `rewind` needs it, since a stateless worker that rewinds has no
    checkpoint. A `Head` fails to type-check, since its node may be `None`, and the runtime refusal goes.
    `choose` becomes generic in the caller's checkpoint type, which `-> Checkpoint | None` had lost.
- **Cluster 4 of the drafting choices: migration.**
  - **M1 parents a resumed claim's first commit on the commit, even where an extendable exit follows it**
    (approved, unchanged). A parent names where the resumed state came from, and every 0.3.0 checkpoint was
    saved at a step, never after an exit. Nothing in 0.3.0 asserted that a clean exit sat between iterations
    either, since `discard()` is new. The shape, a one-node side branch at each preemption, is one a native
    log has when a claim resumes from a commit's checkpoint rather than the exit's, and no read of values,
    counts or the verdict differs, since a migrated exit holds no values and fires nothing. One rule serves
    every kind of exit, where parenting on the exit needs a case for the preempted one.
  - **M5's third case synthesizes a trailing step s only in the claim M1 will choose** for a later claim
    whose first real beat is s+1: the newest claim before it to reach step s, by a commit or by trailing
    values (approved). The draft's "any later claim" was right against "only the next": a retry that crashed
    while loading beats nothing, and looking only at it would leave the claim after it parentless. Added: "any
    later claim" also synthesized where M1 would not use the commit. A crashes with step 5's values; B
    resumes from 4 and beats 5; C starts at 6. The draft synthesized A's step 5, though M1 parents C on B's,
    so a commit nothing continues asserted that a crashed iteration finished, on evidence that points at B.
    No read of the head changes. P2 names the exception, which it lacked already for a crashed claim nobody
    resumed after, and the records' "every integer-step value" sentence says the same.
  - **A third party's `stopped` that ends its claim becomes a `died`, mapped as drafted; a later one becomes
    a `died` only when its `error` is set, and otherwise only its withdrawals** (approved). The draft's
    mapping matches what each of mycooc's writers documented for the stop that ends a claim: the backstop's
    `errored`, or preempted for the runner's own kill, and the reclaim tool's preempted walltime kill, which
    M10 keeps for its 7 bodies. But the writers of later stops, the repair script and the "clear staged stop"
    path, wrote a default body and saw no death, and the verdict keeps every report's outcome: a crash
    reported by the backstop and then cleared would read `{errored, preempted}`, which a consumer testing
    for `preempted` relaunches. The rule mirrors the live one above, that evidence without a cause reports
    only while the claim is held: once it has ended, only a report with a cause is written. A third party's
    `completed: true` stays `preempted`, now because a death is never a completion, since the draft's
    reason, that the claim's own exit carries the verdict, held only for later stops.
  - **A claim that 0.3.0 saw ended only by its launch's `terminated` gets a `died` from it when the launch's
    handle is the claim's, compared whole** (approved). In 0.4.0 no fold matches a `terminated` to a claim,
    so without the report such a claim never ends: its verdict is lost, and it still looks held, so no
    worker on another host can claim the run. The report mirrors the live reap's mapping, and it attributes
    a reap, which is evidence, as a live launcher does. A wrapper's exit says nothing of its worker, so a
    launch whose handle differs gets none. Added: the draft compared pids; the whole `local://host/pid` is
    string equality, which is simpler, and stricter, since two hosts can share a pid. A ThreadLauncher's
    launch still matches, since it carries its worker's own handle.
- **The holistic read** (2026-10-23), one independent reader over the whole spec after these edits, found 18
  seams. Most were stale text, cross-references and terms, fixed as found: `{commits: 0}` was still a
  negative schema case; a completed exit after `discard()` was counted among the exits that evaluate;
  `store.md`'s ripple still had the foreign episode reporting; "void" was used for "spent"; "held" was never
  defined. Four fixes go beyond wording, for the owner to confirm:
  - **`Reached.node` is `int | None`,** `None` only where a target of 0 meets the empty lineage, and the
    `prefix` outcomes' `node` is the head's, `None` on the empty lineage. Every threshold takes n ≥ 0, so
    `ensure` can be reached at a head with no node.
  - **`report_death` returns `None` for a `lost` report on a claim that has ended.** The rule that evidence
    without a cause reports only while the claim is held had no place that enforced it; the helper checks it
    at the W of its CAS, for every caller.
  - **The live `LocalLauncher` attributes by the whole handle,** as the migration now does, so one rule
    serves both.
  - **The claim path calls `report_if_dead`** where it restated the probe, whose "otherwise" read as the
    opposite of what was meant.

**Item 15: GeoffChurch/mycooc's stage-3 decisions, ruled here (2026-10-23; all ruled).** mycooc's Postgres
plan (`docs/plans/2026-10-21-postgres-staged.md` there) sends its decisions 12a–d and 14 to this session.
Recorded as each settled.
- **12a, approving this spec: after the migration implementation round** (approved). That round comes next:
  it implements §7's rules, re-measures P1–P4 on mycooc's logs, runs item 10(d)'s census of lone stops, and
  resolves the 41 cells of 625dda74. Then the owner approves revision 2 as a whole, and #59 merges. Approving
  the live design now and leaving the migration to the round was rejected: on 2026-10-04 working through
  mycooc's logs found a gap in the live design itself, values written after the loop that nothing committed,
  which is why the exit node holds pending values. So the two are not judged apart. mycooc loses little,
  since its stage 3a needs layer 2 implemented, not only approved.
- **12c, where a run's log leaves Postgres: when its latest claim has `ended`** (approved), the predicate
  every fold uses and `runstate migrate` refuses a live run by. A line at `completed` would keep every run
  stopped at its budget, `preempted` by design, in Postgres for good. mycooc may move a log out and re-import
  it, since the tier is its own, with one condition: **every write to an archived run re-imports first, not
  only a claim.** The compare-and-append and the plain append both compute `MAX(seq)` over the run's rows in
  Postgres, so a `--stop`, a reclaim's death report or a launcher record written to an archived run would land
  at seq 1 and start a second log, unseen by readers of the file. The general form, Postgres keeping every row
  with the file a snapshot that every writer retires before writing, goes to the backlog
  (`backlog/log-tiering.md`) until a second consumer wants a tier. Per-episode logs as storage were weighed
  and set aside there: sound only with the frontier kept in Postgres, and every reader would stitch files to
  rows.
- **12d, running jobs depending on the coordination server: accepted** (approved). With runs' logs on
  `PostgresChannel`, an outage of the server ends every running job's session: its next write raises, and the
  iteration in flight is lost. It is never a correctness failure, with no double claim and no committed record
  lost while `synchronous_commit` and `fsync` are on, and nothing hides it, since buffering through an outage
  would be a silent reconnect. The worker cannot write its own exit either, so its claim stays held until a
  report ends it. The long-term shape is recorded in `backlog/outage-as-pause.md`: a worker re-claims after a
  lost session, ending its own old claim on session evidence and resuming from its last landed node, so an
  outage costs a pause. It is decided with 12b, which it needs. The server's placement is the consumer's:
  where its failure coincides with one the jobs already suffer, as mycooc's login node did before it lost on
  disk, CPU and the admins' consent.
- **12b, a lost Postgres session as evidence for a death report: yes, in a corrected form** (approved).
  Attacked by two independent adversaries, on the protocol and on Postgres's semantics, and tested against a
  real server (PostgreSQL 18.4, psycopg 3.3.6, 294 trials over termination, client and backend kills, fast and
  immediate restarts, a lost commit acknowledgement and `idle_session_timeout`): no lock ever read released
  while its session could still write, and no row of that session became visible afterwards. By the source, an
  ending session aborts its unfinished write before it releases its locks, and a commit cannot be interrupted.
  The form adopted, against mycooc's proposal: (1) a fresh random key per connection, taken by try-lock before
  its first write, since a key derived from the run and the claim's seq is shared by every racing claimant and
  turns the lock into a claim gate; (2) no wire field: the backend stamps each row with its writer's key,
  qualified by the server's identity, and the probe asks whether the claim's writer still holds it; (3) every
  record naming a claim travels on the claim's session, never re-established, so a value sent through another
  handle can land after the report but nothing can commit it; (4) the channel sets `synchronous_commit = on`
  and refuses `fsync = off`, after the experiment lost 14 to 2,386 already-visible rows in 40 of 40 crash
  trials with it off and reused their seqs, and the probe reads only the server that wrote the stamp; (5)
  `lost` says the claim ended without an exit, not that the process is gone, so session evidence outranks a
  live same-host pid, and a launcher there kills the orphan rather than launch beside it; (6) accounting stays
  beside it, since the lock carries no cause and a walltime kill alone would read `lost`, a failure, where
  sacct says `preempted`, so mycooc's step deleting `reclaim` is wrong; (7) mycooc's gate that no checkpoint
  follows the report is too strong, since a save runs right after a commit lands, so it is at most the
  iteration in flight, with checkpoints written by temp file and rename. Taking the lock before the first
  write also fixes a live bug: `hold_episode` runs after the claim CAS (`worker.py:144`), so a pooler
  self-check that raises there strands a claim with no exit. Not measured: release time under a true network
  partition, which needs root. The spec's open known limit becomes the session-probe rule (§3), and
  `channel-postgres.md` takes the rest at implementation.
- **14, automating `reclaim` before 3c: yes, from accounting, and permanently** (approved). After 12b the
  session probe reports `lost` with no cause, so an accounting reporter is what turns a walltime kill into
  `preempted`: it is the permanent reporter of a death's cause, not an interim. `squeue` is only its trigger.
  "No longer listed" says that a job ended, not how, and a finished job leaves `squeue` within minutes. Before
  3a there is no `lost` to write, and a `stopped` with no error reads `preempted`, a cause the evidence lacks.
  So `sacct` decides, through the mapping `reclaim` already uses. The job is matched to its claim by name:
  mycooc exports `RUNSTATE_LAUNCH_ID="$SLURM_JOB_ID:$SLURM_RESTART_COUNT"` (`scripts/cluster.py:138`), and a
  claim carries its launch id, once a count of recent claims confirms its pin writes it, since the July census
  found none. `correlation_refusal` remains for older claims. It is the SLURM counterpart of `LocalLauncher`'s
  reap, which §3 allows a custom producer's launcher, and it covers every SLURM launch. It writes a `stopped`
  naming the claim at 0.3.0, and `supervisor.died` through `report_death` after 3a.

**Item 16: the migration implementation round (2026-10-23 to 2026-10-28; approved at 12a).** Item 15's ruling
12a waited on this round. It implemented §7 as the retained step `runstate/migrations/v0_3_0_to_v0_4_0.py`,
with its engine and facts modules; the mechanism every step shares for what a log leaves open
(`runstate/migrations/ambiguity.py`; `specs/log-formats.md` §6, "Ambiguity"); and the pure 0.4.0 reads
(`runstate/lineage.py`). It then ran the step on every runstate log the consumers hold: translation's and
mycooc's, since bookgen's 160 `.db` files and runstate-tui's hold none. The step is not registered:
`LOG_FORMAT` stays 0.3.0 until layer 2's last commit (log-formats §2, rule 3). The plan is
`plans/2026-10-23-migration-round.md`; the branch is `impl/migration-round`, 113 commits on #59's head through
`21835e6`.
- **How the round went.**
  - Stage 2 encoded §7's hand-written rules (M1–M12), and the local corpora ran clean under them (`05004ff`).
  - Stating which of those rules were inferences gave fifteen ambiguities. Five rounds of prose review of them
    did not converge: the Important findings of the four re-reviews ran 2, 2, 2, then 6.
  - The owner froze the clause text (2026-10-24). A model of conforming 0.3.0 writers was built
    (`tests/migration_sim/`, `2cc4a41`), and three architectures were designed against it, judged twice and
    critiqued: Stage 6 (`plans/2026-10-23-migration-round.md`, "Stage 6").
  - The winner replaced the fifteen rules with one candidate engine (`d5b87d1`), and §7 was rewritten from it
    (`bfa5c6c`). The step was optimised once more (below), and the corpus re-run on it (`8ccf4a8`).
- **The owner's decisions, in order.**
  - **c′, a backstop that ends no claim (2026-10-23).** The runner's backstop, "subprocess exited N with no
    terminal record", when it names no claim or one already ended, becomes a `supervisor.terminated` under a
    minted launch id `backstop@<seq>`: exit N, or signal −N where N is negative, with its `t`, in its place.
    Its honoured stops become withdrawals. A first backstop naming a held claim stays a release, a `died
    errored`. Since the next decision it is content of mycooc's classifier, not of the step (§7, "What moved
    out of the generic step").
  - **No default guess (2026-10-24).** A run whose migration needs an inference is refused unless the consumer
    states a fact about its writers or names a chooser for that kind of question: "an easy place to make big
    mistakes (even despite replayability)". One mechanism serves every step, so every ambiguous migration has
    the same ergonomics, and the manifest records every choice with the run.
    - **Judged against any conforming writer** of the input format, under that format's conventions, which are
      what its schemas and its design write and nothing wider (also 2026-10-24: "do not widen").
    - **So runstate ships generic choosers only.** The rules that read one consumer's texts left the step for
      mycooc's classifier: the backstop's error text (c′), the reclaim tool's malformed body (M10), and the
      lone-stop rule's 60 s clause.
    - **Each step judges the format it reads** (also 2026-10-24). Names in a 0.3.0 log are taken at their
      word, and reading 0.2.0's records by position became a chooser of the 0.2.0 → 0.3.0 step. A step's
      questions read only that step's input log (D3). Applying D3, the controller ruled that a later step
      receives an earlier step's choices from the root's manifest, a ruling that rests on the owner's
      approval of the manifest and of D3. So the 0.3.0 → 0.4.0 step learns from the manifest that a log was
      born in 0.2.0, and reads the names the earlier step filled as its inference, not their writer's word
      (§7, "Names an earlier step filled"). The final review (2026-10-28, I1) found the converse unsound:
      no entry was read as a birth in 0.3.0, though a run copied between roots, or migrated by a runstate
      that wrote no manifest, has none. Birth is now read from the manifest or the store's trace of the log
      in 0.2.0, else from the consumer's statement, and an unstated one is refused.
  - **Stage 6, the architecture, and the target (2026-10-25; approved with the critique's six named
    changes).**
    - **The target** is the conforming 0.4.0 lineage that records the 0.3.0 history's own events: which
      iterations finished, and where; what each claim loaded; which stops each worker read, so its drains
      follow the 0.3.0 watermarks; and who reported each death (§7, "The target").
    - **One forward-backward pass** over the log finds every 0.4.0 lineage of a conforming 0.3.0 history that
      writes the log and satisfies the consumer's statements. Between claims it carries only a declared state:
      the checkpoint's candidates, the promises, and the workers still alive.
    - **Where candidates differ, a fork** is reported per claim in a projection (`end`, `span`, `origin`;
      later `writer` and `end-time`), and decided by a chooser, from the last claim back, the candidates
      re-pruned after each answer. The order is a stated rule.
    - **Consumers state facts per writer version,** with a classifier that names a record's writer where its
      content can (§7, "The writer facts"). An unstated fact refuses only where its value would decide an
      output; a stated one the log contradicts refuses the run, naming it.
    - **The prose is the referee** (§2–§4 and §7's encoding rules). The simulator is a second, independent
      encoding, and the oracle holds the engine to it.
    - **The contest:** both judges ranked the per-claim factoring (B) over a migration defined by a writer
      model (C) and the fifteen rules repaired (A), 25 / 21 / 16 and 24 / 21 / 15. The two were not fully
      independent: judge 2 read the ledger's summary of judge 1 before scoring, and said so. The adopted
      design is B's factoring with C's writer facts.
  - **Consumers decide what happened; runstate decides only how 0.4.0 writes it down (2026-10-25).** Otherwise
    a migrated log's records would mean different things depending on who migrated it, 0.3.0's ambiguity
    brought back by the migration. The proposed table of conventions for runstate to rule dissolved into this:
    whether a landed save finishes a cut iteration became a writer fact (`cut-save`); when an undated
    iteration ended became a fork (`end-time`); and the two rows about writing became encoding rules: E1, a
    migrated death report says it was migrated, and E2 below.
  - **Records stay where 0.3.0 put them (2026-10-25), E2.** No stop moves; the native drains read what lies
    before them, and each exit's `honored` follows from them. Its cost: a migrated exit's `honored` can differ
    from its 0.3.0 list. The recorded lists differ at once, the stop fold in cases 1 and 5, and running
    behaviour only if the run is resumed (§7, "What the cost changes"). The owner accepted it for two
    cases (2026-10-25): an exit that lists only the stops its ticks read, and a tick-first loop's stop cleared
    between an iteration's end and the next tick. The controller's rulings generalised the second and extended
    the same mechanism to three more, a stop answered after the claim, a stop an earlier migrated exit
    discharged, and an exit whose list the 0.2.0 step inferred, and told the owner. §7 names the five (E2,
    *Its cost*); ruling 12a confirms them (below).
  - **Divergences (2026-10-25).** A rule under which a read of the migrated log answers differently from the
    same read of the input is declared by name, detected where it fires, refused until `--accept` names it,
    and recorded in the manifest. Corrections count. The 0.3.0 → 0.4.0 step declares twelve (§7, "The
    divergences"), the 0.2.0 → 0.3.0 step six.
  - **The test economy (2026-10-26).** The oracle's exhaustive families are marked and leave the hook; CI runs
    them in a job filtered by the paths they test, and on demand. The mutation test decides which families
    stay: 60 mutants, every one caught. No formal tool.
  - **Ask mycooc for its writers' facts (2026-10-26).** The owner asked why these were runstate's business,
    and had the mycooc session asked. Its answers (2026-10-26 and 2026-10-27, from its code, `main.log` and
    `sacct`) are the facts of the measurement's configuration:
    - all 31 reclaim releases were of dead workers: 25 FAILED, 4 CANCELLED, 2 TIMEOUT, none NODE_FAIL;
    - three later claims were fresh restarts, two after a prune had deleted the checkpoint and one from
      another output directory;
    - a save at k holds pass k's result, and a load from it resumes at k + 1 (`resume` `after`); a claim's
      clock reads 0 before its first pass (`loading` `zero`); no pass skips (`stride` `next`); and no rewind
      mid-loop reloads a save (`rewinds` empty).
  - **Optimise the end-time stage before the step freezes (2026-10-27).** The profile showed the step
    quadratic in commits: one pass over the whole graph per commit, three times. Each act is now read off its
    window (`365f42c`, `98ed57d`, `8ccf4a8`; §7, "The end-time fork", *Its cost*). The output is
    byte-identical on 220 real logs (1,967 components digested); the slowest profiled log fell from 57.2 s to
    6.0 s in the runner, and the fitted exponent of time in commits from 1.87 to 1.05.
- **The questions held for this write-up are all dissolved,** so none needs a ruling now.
  - **Q1, where a beatless claim stands:** a `span` fork, or a stated fact.
  - **Q5, the lone-stop rule:** mycooc's classifier, by the decision of 2026-10-24.
  - **M2, whether a worker's beat after a third party's release still commits:** no. A release of a live
    worker fences it (§4), and the engine commits none of its later passes (§7, `end`'s fence point).
  - **The provisional M1 candidacy and M6 anchor rulings** were Stage 2's rules, and went with them.
- **The corpus.**
  - **What ran:** every log, from its legacy address through the shipped 0.2.0 → 0.3.0 step and the new one,
    each in a forked child under a 4 GB address space. The engine is `8ccf4a8`. The full queue ran under the
    runner `scripts/migration_round.py` at `cdde565`; the undecided runs and the wide samples were re-run at
    `2ae322c`, which kept their census and forks, and `21835e6` changed no recorded field.
  - **The configuration:** mycooc's facts as it answered them (256 worker versions), proposed to mycooc and
    kept out of its tree; translation's, with `resume` stated both ways, which it never reaches. The choosers:
    `record-claim=positional`, `stop-honored=positional` and `lease-bound=first-claim` for the 0.2.0 step, and
    `end-time=lower`. `span` and `origin` were left unresolved, so a run that forks on either counts as
    undecided.

    | | translation | mycooc store | mycooc mirror | mycooc archive |
    |---|---|---|---|---|
    | runs | 1,221 | 4,953 | 5,088 | 41 |
    | migrated | 1,218 | 4,882 | 4,982 | 40 |
    | undecided: `span`, some also `origin` | 3 | 69 | 106 | 1 |
    | refused | 0 | 2 | 0 | 0 |

  - **The mirror** is an older local copy of mostly the same runs, and the store copy, frozen for the round,
    decides where they differ. Deduplicated, mycooc has 5,971 runs.
  - **Clean in every run that reached the transform:** 0 crashes, 0 rows the 0.4.0 schemas reject, 0 lineage
    reads that raise or disagree with the series (P1), and 0 violations of §7's "Also".
  - **The undecided runs are exactly revision 2's ambiguous runs** (Task 19's configuration on `9c788a9`), in
    every corpus: the engine's later changes moved none. Of them, 17 store and 12 mirror runs were live by
    0.3.0, which `runstate migrate` refuses anyway; the store's 2 refused runs read live too, 19 in all.
- **The figures, in place of §11.3's.**
  - **The reads (P4).** Every series cell, verdict and stop fold the migration changes is attributed to a
    divergence that fired there, and none is unattributed, in any corpus.
    - Translation: 0 of 1,010,403 cells differ, and no divergence fires.
    - mycooc: 854,979 cells differ in the store, 849,937 in the mirror and 3,172 in the archive. All are
      `register-value`, mycooc's `status` register, one per commit, which leaves every lineage as item 13's Q4
      decided, except 8 store and 16 mirror cells of `off-head-value`.
    - Verdicts differ in 27 runs, all by `third-party-note` (below); stop folds differ in none.
  - **The divergences that fire,** in runs / sites:

    | divergence | store | mirror | archive |
    |---|---|---|---|
    | `register-value` | 4,876 / 880,066 | 4,943 / 876,178 | 16 / 3,257 |
    | `third-party-note` | 13 / 13 | 105 / 120 | 0 |
    | `third-party-release` | 10 / 10 | 9 / 9 | 0 |
    | `off-head-value` | 4 / 1,916 | 5 / 2,662 | 0 |
    | `unheld-value` | 1 / 13 | 1 / 13 | 0 |
    | `stale-beat` (the 0.2.0 step's) | 0 | 6 / 6 | 1 / 1 |

    These cover the runs that reached the transform. The undecided runs stopped at detection, and may fire
    more once their forks are chosen.
  - **`third-party-note` changes 27 verdicts,** each from a `stopped` that ends no claim, which 0.3.0 read as
    the run's end. In 6 store runs, logs with no claim at all (a stop, a `stopped`, a stop), `preempted`
    becomes no verdict. In 21 mirror runs, a claim's completed exit followed by a stop and a second `stopped`,
    `preempted` becomes `completed`: the claim's own exit.
  - **What §11.3 counted is gone.** Its named classes (cells beyond the head's lineage, revisited steps,
    loading cells, M4's 1,204, error exits) and the 7 runs that a null M1 read as empty (8,449 cells) belonged
    to the hand-written rules. Under the engine, each read the migration changes is a declared divergence,
    accepted by name. M6's figure stands, since the step still renumbers: appending would add about 1.23M
    records.
  - **The refusals:** 58e1ab75 and ed5960af in the store, whose `started` is attach-shaped, with no `t`. They
    are pre-0.2.0 logs (2026-06-16, before `4729fcd`), outside every step's domain, which the onboarding check
    (heartbeats carry `t`) already excludes. There was no horizon, budget or "no history" refusal anywhere
    under mycooc's facts.
- **The census and its rule.** The runner counts, over the candidates detection finds, the shapes that a
  chooser or a fact decides; this is evidence a refusal count cannot give.
  - **Resumes at the loaded step.** A review found that the reference Worker's documented resume,
    `steps(start=k)` after loading "checkpoint step k", migrated silently with the wrong `resume_from`. **The
    rule** (2026-10-27): what a save labelled k holds, and where a load from it resumes, is the consumer's,
    the writer fact `resume` (`after`, `at`); stated both ways, the claim's origin forks (`11ff610`). **The
    census** counts such claims directly, over detection's candidates. Under mycooc's answer, `after`, there
    are none in any corpus, by construction: a load of k under `after` runs k + 1 first, so the 0 is no
    evidence. Stated both ways, which ran only on samples (300 store, 300 mirror and all 41 archive runs), the
    census finds 6 claims in 5 runs: store 25e831b1, 5cde93c0, d78b72f1 twice and ed34ca78, and mirror
    3ba2b399. Three of those runs, d78b72f1, 5cde93c0 and 3ba2b399, migrate under mycooc's answers; under `at`
    they would become undecided on `origin`. So mycooc's statement of `resume` decides them. A test pins the
    count (`test_the_census_counts_a_resume_at_the_loaded_step_under_any_reading`).
  - **End times.** In every migrated run, each end-time fork is a commit no record dates, at exactly one
    place: only its time is open, which `lower` answers (863,546 sites in 4,945 store runs, the undecided
    runs' forks included). A commit two candidates place apart occurs only in undecided runs, 6 store and 36
    mirror. No run holds a rewind.
  - **Item 10(d)'s lone stops** (Task 13; 5,129 local mycooc logs). Of 5,412 lone stops, 5,085 are the
    worker's own exits, 295 its exception exits, 24 the reclaim tool's (`t` before `created_at`) and 8 the
    runner's backstop. Every class has exactly one writer. The rule that reads them is mycooc's classifier.
- **Each unattributed difference, resolved: Task 14 closed at 0.** "625dda74" is run d78b72f1…: §11.3's id was
  cut from its middle. Under mycooc's facts, with each release classified by its job's `sacct` state, it
  migrates to one lineage, claim 1 to step 181, then claim 4188 at 182 and claim 4215 at 183. Its 184
  differing cells are all `register-value`. The golden `beatless_full_step` and a simulator history pin its
  shape. No unattributed difference remains in any corpus, so Task 14 needed no commit.
- **The cost.**
  - **In the runner** (`8ccf4a8`): 2.93 s a store run on average (2.85 s without 664f8795's 411.5 s), 2.73 s a
    mirror run, 0.6 s a translation run. The step itself is about 0.9 s of that; the rest is the runner's own
    checks. The time is linear in commits.
  - **A measured `runstate migrate`** (2026-10-28) over 300 store runs, seeded from the 4,882 migrated, none
    live (`random.Random(15)`; a mean of 4,085 rows against the store's 4,040).
    - **Laid out as mycooc lays them.** A run's home is its channel root (mycooc's `run_experiment.py` sets
      `RUNSTATE_CHANNEL_ROOT` to it), so each home was onboarded by the loop `LogFormatMissing` prints, then
      migrated by `runstate migrate <home> --to 0.4.0`, one home at a time.
    - **The step registered in process,** as `tests/test_migrate.py` registers its toy steps: format 0.4.0's
      layout added to `FORMATS`, the step appended to `STEPS`, then the CLI's own `main`. Nothing else
      changes. The harness is three lines before `cli.main`: `formats.FORMATS["0.4.0"] =
      DirectoryLayout("0.4.0")`, `migrations.STEPS = (*migrations.STEPS, V0_3_0_to_V0_4_0())`, and timers
      around `migrations._detect` and `SqliteStore.migrate_one`. The shipped CLI does not register the step
      yet, so this is the only way to reach 0.4.0 today.
    - **The settings:** mycooc's facts; the round's choosers, above; and every divergence that fired in Task
      23 accepted. These settings are measurement settings only. Each choice and each acceptance is mycooc's.
      The flags, exactly: `--to 0.4.0 --assume mycooc:CONFIGURATION --resolve record-claim=positional
      --resolve stop-honored=positional --resolve lease-bound=first-claim --resolve end-time=lower`,
      `--accept` for `stale-beat`, `naked-stop`, `ended-lease`, `unhonored-stop`, `unbound-lease`,
      `register-value`, `third-party-note`, `third-party-release`, `off-head-value` and `unheld-value`, and
      `--cap 3000 --budget 5000000 --run-on 2`, each process under a 4 GB address space. The harness, the
      subset and the raw timings stayed in the session's scratch space; this item and the Task 15 report hold
      the figures.
    - **The result:** 300 of 300 migrated, 0.2.0 → 0.3.0 → 0.4.0, every old log sealed, every new one written,
      exit 0. A run took 0.90 s of wall time on average (median 0.82, p99 2.5, max 2.9 s), interpreter start
      included. Detection took 0.73 s of it, and the migrate pass 0.05 s, since it reuses the dry run's
      transform. Peak RSS was 47 MB at the median and 79 MB at most. Onboarding all 300 took 2.3 s.
    - **One root for all 300,** as a consumer with a single root would run it: one onboarding loop and one
      invocation, whose one detection pass covers every run. The logs it wrote are the per-home ones, row for
      row, but it took 503 s (1.68 s a run: detection 243 s, the migrate pass 260 s) at a peak RSS of 691 MB.
      The memo that lets the migrate pass reuse detection's transforms holds 1,000,000 output rows across both
      steps. That is about 120 of these runs, inferred: about 8,400 output rows a run over the two steps, and
      the migrate pass's 260 s fits 120 runs at 0.05 s and 180 at about 1.4 s, two transforms each. So the
      rest were transformed twice more, before and after the seal. mycooc's layout never fills it.
    - **Per corpus,** scaling each run's runner time by the measured ratio (0.31; p10–p90 0.30–0.34): the
      store's 4,882 migrated runs about 4,400 s (4,100–4,800 s), 1.2–1.3 h; the mirror's 4,982 about 4,300 s
      (4,100–4,700 s); the archive under 20 s. Scaling by rows instead gives the same within 1%. This is Task
      23's estimate, 1.2–2 h, now measured at its lower end.
    - **Not included:** the undecided runs, which wait on mycooc's choices, and the file system. The copies
      sat on tmpfs, where mycooc's logs sit on NFS, so reading and writing them cost almost nothing here.
    - **The manifest** grows by about 195 KB a run, almost all of it the description of mycooc's 256 worker
      versions, written into every 0.3.0 → 0.4.0 entry: about 1 GB over the store, one file per home.
  - **664f8795,** the costliest log (4,017 records, 670 claims), migrated alone under provisional choosers,
    `span=fewest` and `origin=newest`, with every divergence of the step accepted. It is a cost bound, not a
    choice: the choice stays mycooc's.
    - `runstate migrate` first refused it as live: its last claim, on ai06 since 2026-08-10, never ended.
    - With that check overridden in the harness, for the measurement only, it migrated in 819 s at a peak RSS
      of 1.8 GB. Detection took all of it: Task 23 measured 411 s for finding the candidates alone, and the
      dry run's transform took the rest. The migrate pass took 0.05 s.
    - Its manifest entry records `span` readings for 669 claims and an `origin` reading for 1, and only
      `register-value` fired (2,677 sites). Nothing reached the cap or the budget.
- **What mycooc decides.** Consumers decide what happened, so each of these is mycooc's, and the round's
  settings only measured them.
  1. **Its facts** (`--assume`): the round's statement of its answers, moved into its tree, with its release
     table, its prune-era saves and its restart flag.
  2. **The 0.2.0 step's choosers,** which the round set to `record-claim=positional` (65 store and 116 mirror
     runs fork), `stop-honored=positional` (26 store, 159 mirror, 1 archive) and `lease-bound=first-claim`
     (none fork).
  3. **`end-time`,** asked in nearly every run; mycooc's own stated chooser is `lower`.
  4. **`span`, and `origin` where it forks,** for the 147 undecided runs that are not live: 52 store, 94
     mirror, 1 archive. These are the structurally hard runs, the same logs as before Stage 6, and they may
     fire divergences not listed above.
  5. **The accepts:** `register-value`, `third-party-note` (with its 27 verdict changes),
     `third-party-release`, `off-head-value` and `unheld-value`, and from the 0.2.0 step `stale-beat`, or
     leaving those runs unmigrated.
  6. **58e1ab75 and ed5960af:** repair their `started`, or leave them.
  7. **664f8795:** its `span` and `origin`, at the cost above, once its stranded claim is released.
  8. **Its live runs:** 17 undecided store runs (19 counting the 2 refused) and 12 mirror runs read live by
     0.3.0, and `runstate migrate` refuses each until its claim ends. At least one is stranded rather than
     running: 664f8795's last claim, on ai06 since 2026-08-10, never ended, and a foreign handle reads live.
     mycooc releases such a claim (its reclaim tool, from `sacct`) before migrating it.
- **Confirmed by the owner at ruling 12a (2026-10-28).** Each went further than any earlier owner ruling.
  - **No writer tag on `started`.** The owner asked (2026-10-25) why a `started` should not name its writer
    now. The 0.4.0 audit found no writer habit that a 0.4.0 reader depends on and that 0.4.0 cannot record, so
    nothing needs a tag before ship, and it recommended against adding one: a consumer can already commit its
    version as a value. The plan went ahead without it. Its one wording finding, `resume_from: null` also
    covering state from outside the run, was fixed (`4ce721e`, `ffe373a`).
  - **E2's cost in five cases,** where the owner had accepted two (above). All five are accepted.
- **The final whole-branch review (2026-10-28)** found 0 Critical, 2 Important and 10 Minor; one fix round
  closed every finding (`8d1297b` to `fb885a9`), and its re-review's one new Minor was fixed (`fc813f8`). The
  three items the measured migrate had left open for it:
  - **The shipped wiring is untested end to end.** It stays so by design: format 0.4.0's layout in `FORMATS`,
    its opening checks (`log-formats.md` §4) and the step in `STEPS` are written with layer 2's last commit;
    the measurement registered the step in process. The review confirmed nothing claims the CLI reaches 0.4.0.
  - **The manifest's size.** Every entry repeated the whole description of the consumer's facts, about 195 KB
    a run for mycooc. An entry now cites the description by its digest, and the root holds it once
    (`log-formats.md` §6): a mycooc entry went from 206,261 bytes to 23,658.
  - **The memo bound.** Past it, under one root past about 120 of mycooc's runs, a run was transformed twice
    more. The transform after the seal now takes the dry run's output, so it is transformed once more.
  - **Birth (I1).** A log the manifest was silent about read as born in 0.3.0. Birth is now read from the
    manifest or the store's traces of the log in 0.2.0, or else stated by the consumer, and refused unstated
    (§7, "Names an earlier step filled").
- **What runstate decided** is only how 0.4.0 writes the history down, the same for every consumer: E1 and E2,
  §7's "Records", the generic choosers, the refusals, and the limits set at the command line and recorded with
  each run (cap 3,000 states a layer, budget 5,000,000 summed, run-on 2).
- **Ruling 12a (the owner, 2026-10-28): revision 2 approved.** Its migration is implemented and measured, and
  every read it changes on real logs is a declared divergence. Its live layer (§3–§6) is not implemented. The
  step registers, and `LOG_FORMAT` moves to 0.4.0, with layer 2's last commit. With the final whole-branch
  review closed, #59 merges and then this round's branch, each by merge commit.
