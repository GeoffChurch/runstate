# Review of `specs/commits-and-lineage.md` (draft): proposed fixes for everything

**Status:** PROPOSAL, 2026-10-04, awaiting the owner's decisions. Nothing here is applied to the spec yet.

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

### C1. Correct runstate's own docs (part of this fix round)

**`demand-streams.md` misstates if-built-today in four places (CM):**
- **"Demand subsumption merges overlapping demands."** if-built-today gets subsumption free from an empty
  residual, and *refuses* merging, because anti-unification over-approximates (`3-questions.md` §181–184,
  §305–311).
- **"Withdrawal is retraction; polarity is the answer."** `asked` is unpolarised. "Still wanted" is
  control. Withdrawal is a lapse plus a positive `withdrawn(k)`: ordinary facts (`5-domain.md` §232–235).
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

### C2. Edits to if-built-today itself (proposed; the owner approves before any commit)

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

### C3. New backlog rows (runstate adopting from if-built-today)

- **Hash-reveal withdrawal** (`5-domain.md` §205–254): subscribe carries a commitment, and unsubscribe reveals
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
  force from its commit onward along the lineage) and derived streams named by their demand go to the
  programmable-subscriptions backlog. Observers change demand, not sources.
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
