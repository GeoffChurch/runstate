# runstate v0.2 — Design

**Status:** converged design, **implemented in v0.2** (rev 4 — implementation pass), and brought to **log format 0.4.0** (2026-10-30: `specs/commits-and-lineage.md`, values committed to a lineage of nodes). Substrate + the four conventions + orchestration helpers + the JSON Schema stack are built and tested. The schemas are written and frozen; the two once-held bodies are pinned — `lifecycle.heartbeat` = `{claim_seq, consumed_seq, t}` (liveness and the watermark only, since `lifecycle`-`v0.6` moved progress onto the `lifecycle.commit` node) and `supervisor.launched.status` = `running`. Remaining §12 items are consciously **deferred** (annotated there), not blocking.
**Date:** 2026-05-29.
**Supersedes:** v0.1 (`design-v0.1.md`). Full redesign.
**Decision trail:** the dialectic and rejected alternatives that produced this live in `design-v0.2-exploration.md`. This doc is the destination.

---

## 1. What this is

runstate is a protocol for **cooperative bidirectional control of a long-running scientific worker**, plus a reference Python implementation. It's a stack of thin layers: an **opinion-free transport substrate** (a per-run topic log) with **opt-in conventions** on top (cooperative-control, subscription, lifecycle, supervisor). The value is the conventions; the substrate is generic enough to back with an off-the-shelf log (NATS JetStream, Kafka, Redis Streams).

## 2. Entities & layers

```
backend          — storage engine: SQLite + Memory + Postgres; NATS JetStream / Kafka / Redis (later)
substrate        — a per-run TOPIC LOG over a backend. Opaque body; truly opinion-free.        [§4]
conventions      — opt-in protocols the substrate is blind to but carries:
  cooperative-control — content categories + the per-role subscription protocol                 [§5]
  subscription        — control.* bodies, the scheduling condition-algebra over counts             [§6]
  lifecycle           — lifecycle.* well-known events, commits and lineage, worker-owned termination [§7]
  supervisor          — supervisor.* names, the liveness handle, death reports, the failure detector [§8]
orchestration    — reference helpers (Launcher, Watcher, the deciders, ensure)                     [§9]
```

**Actors** (roles are *convention*, not substrate — the substrate is author-agnostic):
- **worker** — the workload side of a run. *Consumes* `control.*`; *produces* `lifecycle.*` and user `value`s. One OS process by default; a multi-process workload (DDP, multi-actor) is a convention (a body-level participant id), so "worker" = the workload side, not literally one process.
- **orchestrator** — *produces* `control.*`; *consumes* `value`s, `lifecycle.*`, `supervisor.*`.
- **observer** — read-only; *consumes* the log, produces nothing. **Invisible to the worker.**
- **launcher** — spawns the worker, records its liveness handle; *produces* `supervisor.*`. (Writing a death report, `supervisor.died`, is an act, not a role: any party holding evidence of a death may write one, an observer included, §8.)

**The schema stack** (there is no single "the schema"):
- **Envelope schema** — the substrate's wire contract (§4). Fixed envelope fields + an **opaque body**. Freezable on its own; constrains neither `topic` semantics nor body shapes.
- **Per-convention schemas** — subscription / lifecycle / supervisor / value each strictly pin their own well-known bodies (`additionalProperties: false`), each frozen and versioned on its own timeline (§10).

## 3. Backend

The substrate's storage. Three backends ship: a durable **SQLite** one (stdlib, embedded, zero-dependency; a single-file `log` table with an autoincrement `seq` is already a retained, sequenced log), an in-process **Memory** one (a shared list, for in-proc orchestration and tests), and — post-v0.2 — the cross-host **Postgres** one (`specs/channel-postgres.md`: one shared `log` table keyed `(run_id, seq)`, the primary key as the CAS arbiter). All pass the same conformance suite. SQLite deployment: WAL is the default journal mode (with a 5 s `busy_timeout`); WAL needs shared memory a network filesystem cannot back, so an NFS-homed deployment exports `RUNSTATE_SQLITE_JOURNAL_MODE=DELETE` (the NFS-safe rollback journal). Further topic-log backends remain possible — e.g. NATS JetStream: subjects map to `topic.name`, stream sequence to `seq`, durable/ephemeral consumers to caller-owned cursors, `last_per_subject` to the `latest` projection.

## 4. Substrate: a per-run topic log

The substrate is a per-run, append-only **log of envelopes** addressed by an opaque **topic**. It has no notion of "worker," "direction," subscriptions, or any message shape. Its one near-zero opinion: messages carry a routing key, a correlation id, and an opaque body.

### Envelope

```python
@dataclass class Envelope:
    seq: int                  # substrate-assigned; the per-log total order — contiguous, 1-based (one sequencer per log)
    topic: str                # PROTOCOL routing key — a CLOSED, protocol-owned vocabulary
    name: str | None          # APPLICATION identifier — OPEN, app-owned (e.g. "train.loss"); None for fixed protocol topics
    request_id: str | None    # correlation + visibility; None = unaddressed / broadcast
    body: dict                # opaque to the substrate — a convention interprets it
```

The split of `topic` (closed, protocol-owned) from `name` (open, app-owned) is load-bearing: it makes `topic` a **finite, inspectable vocabulary** and keeps application identifiers out of it, so there is **no reserved-vs-user collision** to resolve (a user metric named `lifecycle` is `{topic: value, name: "lifecycle"}`, distinct from `{topic: lifecycle.*}`) — no sigil, no reserved-root scheme needed.

**Lift-rule** (what earns an envelope field): a field is in the envelope iff the substrate indexes/routes/filters on it — `topic` (routing, `latest`), `name` (the latest/by-metric dimension), `request_id` (correlation + visibility). `name` is its own field rather than a `topic` sub-level because it differs in *ownership/openness* (closed protocol vs open app) — which is exactly what merging them would have collided.

### Contract

- **Append + opaque body.** `send` appends and returns its `seq`. The substrate never parses `body`.
- **Value-snapshot at `send`; reads return copies.** The body is snapshotted when `send` returns — mutating the dict afterwards, or a returned `Envelope.body`, never alters the log. A body that isn't JSON-serializable fails **at `send`**, naming the problem — never later, on an innocent reader.
- **Compare-and-append (CAS).** `send(..., expected_seq=S)` appends **iff** the log's last `seq` is `S` (`0` = empty log) and returns the new `seq`; otherwise it returns `None` — the claim is *provably lost* (the log moved). The check-and-append is **one critical section across handles and processes**. A backend may raise only when the outcome is *indeterminate* (e.g. a competing writer wedged past the backend's wait bound), never to report a loss. Opinion-free (it reads a `seq`, never a body); it is the standard optimistic-concurrency primitive of log stores (NATS expected-last-seq, EventStore `expectedVersion`). The single-claim guards (run-episodes self-claim, §12.1) rest on it.
- **Every handle is thread-safe.** One Channel handle may be shared across threads (the ThreadLauncher topology — one instance held by both the worker and the Watcher): under mixed traffic through one handle — CAS claims interleaved with plain sends — every acknowledged send is durably in the log and at most one CAS wins; each backend serializes its own connection use internally.
- **Caller-owned cursors.** A reader owns its cursor (a `seq` position passed back on the next read). The substrate keeps **no per-reader state and no registry of who is reading** — N readers each see every matching envelope. (Whole-run retention follows: the substrate needn't know its readers to decide what to keep.) Crash-resume = persist the `seq`; otherwise re-read from `0`. Start position is just the initial cursor.
- **A per-log total order is the contract**: `seq` is one **contiguous, 1-based** sequence — exactly `1..N`, no gaps, across all topics — assigned by the log's single sequencer (SQLite's autoincrement; Postgres's `(run_id, seq)` key; the Memory list); the CAS's `0 = empty log` is its base case, and the conformance suite pins contiguity on every backend. The conventions depend on it — the claim CAS asserts the head across all topics, a claim's `seq` is its rank, and the causal window after a claim spans topics (§7) — so a backend offering only per-topic FIFO cannot host them.
- **A reader that has seen seq W has seen every seq below W; contiguity alone does not guarantee it.** A read that returns seq W returns every seq below W that matches it, and a `last_seq()` of W promises the same of the next read. A sequence allocator once broke this 30,780 times while leaving a contiguous log (`docs/review-2026-10-04-commits-and-lineage.md` §11.2, D9), so a check of the finished log cannot see it. Every caller-owned cursor depends on it, since a cursor past a seq not yet visible skips that seq for good. The conformance suite pins it on every backend up to its tier (`tests/test_concurrency.py`, `test_interop_substrate_contract_across_threads` / `_across_processes`): writers append while a reader takes `w = last_seq()`, reads the tail after its cursor, and finds it contiguous and reaching `w`.
- **Retention** until the run's channel is explicitly cleaned up. GC policy is open (§12).

### Surface

```python
send(body: dict, *, topic: str, name: str | None = None, request_id: str | None = None,
     expected_seq: int | None = None) -> int | None   # append; compare-and-append when expected_seq is given
read(after: int = 0, *, topics=None, name=None, request_ids=None, limit=None) -> list[Envelope]
    # topics: exact set or prefix/wildcard patterns (e.g. ["control.>"]) — opaque string matching
latest(topic: str, name: str | None = None) -> Envelope | None        # most recent on a (topic, name)
last_seq() -> int                                                     # the log's last seq (0 = empty) — the CAS's read half
close()
```

The pattern grammar, in full: the **only** wildcard is a trailing `.>` — `"control.>"` matches every topic starting with `control.` — and everything before it is literal (metacharacters inert); no bare `">"`, no mid-string wildcards. `latest` takes exact topics only, no patterns.

`latest` is a substrate primitive (not just a `read` helper) on backend-optimization grounds (SQLite indexed `ORDER BY seq DESC LIMIT 1`; NATS `last_per_subject`). Its well-definedness requires a **single writer per (topic, name)** (the common case) or a single sequencer.

`last_seq` (added 2026-07-10) is admitted on the stronger ground — the **op-admission principle**: *the surface must be readable in every coordinate it requires callers to assert.* The CAS makes every claimant assert the head (`expected_seq`; `0` = empty is its base case) while nothing could read it below O(N); `last_seq` is that coordinate's read, O(1) on every backend (`len(log)`; `MAX(seq)` on the key), and every real log store exposes it (Kafka end offsets, NATS last-seq, EventStore head). Its two sanctioned consumer classes: CAS claimants (the Worker's head-first attach — folds from topic-filtered reads capped at the asserted head, same-read fusion preserved by assertion) and the incremental reader's has-anything-new watermark (a viewer polls `last_seq`, re-folds on change). Nothing else passes the principle: `count(topic)`, `first()`, `read_range` — no caller is required to assert those.

### Log format

`attach_channel` and `create_channel` check a log's **format** when they open it: a log's format is part of its address (`<root>/v<format>/<rid>.db`; a schema per format on Postgres), and a log of any format but the release's own raises `LogFormatMismatch` / `LogFormatMissing` rather than being read. Logs move between formats by `runstate migrate`. Specified in `specs/log-formats.md`.

### Read projections

Stateful-communication shapes are **queries over the one log**: register/latest = `latest`; flag/terminal-fact = existence; queue = a single consumer persisting its cursor; bounded window = last-*k* by `seq`; tail = a cursor read. Per-(topic,name) compaction is a *semantic* choice (makes the register the retained object), not free GC — deferred, chosen with eyes open.

## 5. Cooperative-control convention (foundational)

The thinnest, most fundamental convention — the one structural opinion that makes runstate a *control* protocol. **It is not a binary in the namespace.** The namespace is **content-typed**, and each category has a fixed producer-role and consumer-set *by convention*:

| Category (reserved `topic`s) | Produced by | Consumed by |
|---|---|---|
| `control.subscribe` / `control.withdraw` / `control.stop` | orchestrator (any asker) | worker |
| `lifecycle.*` (started/commit/heartbeat/rewind/stopped/bound/nak) | worker | observers |
| `supervisor.*` (launched/terminated/died) | launcher; `died` by any party with evidence of a death | observers |
| `value` (user metrics; `name` distinguishes them) | worker | observers |

Since `subscription`-`v0.4` the control category is written by askers only. The one completion case 0.3.0 documented (`specs/service-worker.md`), the worker appending `control.unsubscribe` when a registration expired (the *expiry counter-record*), is retired with `control.unsubscribe` itself: expiry is a fact at a node, named in the `expired` of the commit or exit where the schedule ended (§7), and the asker's cancellation is `control.withdraw`, which spends a stop or a subscription by name (`specs/commits-and-lineage.md` §3).

Each role is then just a **subscription map** (the protocol):
- **worker** reads `control.>`; produces `lifecycle.*` and `value`s.
- **orchestrator** writes `control.*`; reads `lifecycle.>`, `supervisor.>`, and the `value`s it wants.
- **observer** reads those; produces nothing.
- **launcher** writes `supervisor.*`; any party may write a `supervisor.died` for a death it holds evidence of.

So **"the worker is the consumer of `control`" is an emergent fact of the protocol, not a reified worker-vs-everyone axis.** This is why `direction` is gone (the supervisor→observer flow never involves the worker, so a worker-centric binary couldn't classify it; content categories do, cleanly). Finer routing *within* the value category is `name` (the stream or source), never the category. A multi-process workload tags a body-level participant id; the substrate and categories are unchanged. `run_id` = one channel = one bipartite worker/outside log; an *experiment* of N runs is N channels coordinated at a higher layer (§9), never one N-party channel.

## 6. Subscription convention

The pull/push vocabulary. Message kind is the `topic` itself — `control.subscribe` vs `control.withdraw` vs `control.stop` vs `value` are distinct topics, so **there is no separate `kind` discriminator** (the closed `topic` vocabulary is the discriminator). Pinned by `subscription`-`v0.4` and `value`-`v0.3` (log format 0.4.0, `specs/commits-and-lineage.md` §3).

```python
# control.subscribe — name = the source sampled, or null for pure demand; request_id names the subscription; body = a schedule
{ "from"?: Condition, "every"?: Condition, "until"?: Condition }   # each slot OMITTABLE, never null — absence is semantic (fire-now / one-shot / never-expire); see the §7 notation note. A pure demand takes only an `until`.

# control.withdraw — request_id = the stop or subscription the asker withdraws; body = { "t": float }
# control.stop     — body = { "from"?: Condition }  (one-shot: at most a `from`; default = stop now; the request half of a request/outcome pair — §7); request_id required
# (control.unsubscribe, subscription-v0.3's, is retired: its two jobs went to control.withdraw, the asker's, and a node's `expired`, the worker's)

# value (worker → observers) — name = the eager stream or source; request_id = null (a node's `fired` names the subscriptions a sample answers)
{ "value": Any, "t": float | null }  # value=app data; t=absolute wall-clock secs. No `step`: a value belongs to the node whose `values` names its seq (§7)
```

### The scheduling condition-algebra

A subscription **fires at `from`** (default: the next node), **repeats every `every`** (if present — *absent ⟹ one-shot*), and **expires per `until`** (if present; else open for a recurring sub). `from`/`every`/`until` are each a **Condition** over the worker's coordinates: the lineage counts (`Commits()`, the iterations, and each eager stream's elements), wall-time, and the fire count. Subscriptions are evaluated at each `commit()` and at an exit that did not discard its iteration, never at a `heartbeat()`, with the counts taken at the node being written (`specs/commits-and-lineage.md` §3, "Evaluation").

```
Coord        := {commits: N} | {stream: X, n: N} | {time_seconds: S}   # from / every; n ≥ 0 (a 0 holds at once)
UntilTerm    := Coord | {count: C}                            # until also admits count
Condition[T] := T | {any: [Condition[T], …≥1]} | {all: [Condition[T], …≥1]}   # fully recursive
#   from, every : Condition[Coord]      until : Condition[UntilTerm]
#   (the schema cannot express Condition[T] generically; it monomorphizes the two
#    instances as $defs `Condition` and `UntilCondition`)
#   (count grammatical only in `until` — structurally, via the per-slot term type)
```
- **`any` = whichever crosses first** (min / OR); **`all` = whichever crosses last** (max / AND).
- Clocks per slot: `from`/`until` are *absolute* (a count on the lineage up to and including the node being written, seconds since registration, total fires); `every` is *deltas since the last fire* (the delta rule, never rounded to a multiple), whose lineage baseline is the count at the newest node on the lineage whose `fired` names the subscription, rebuilt at every position change.
- **Lease or durable.** A schedule with any `time_seconds` or `count` atom is a **lease**: bound to the claim that registers it (`lifecycle.bound`), its clock and fire count the claim's, and void once that claim has ended. Every other schedule, whose atoms are all lineage counts, is **durable**: live per lineage. A stop is a request about the process, live by name. Those are the three request scopes (`specs/commits-and-lineage.md` §2, "Requests").
- **`until` is evaluated first**, at the same node — the boundary coordinate itself produces no fire (the window is half-open) — whereas a **count**-`until` expires *on* the fire that spends the budget: it counts the firings up to and including the node, and the node of the nth firing names it in `expired`. An `until` that already holds at registration expires at the next node, firing nothing.
- The algebra is freely associative; **no normal form** — equivalent encodings (`any[a, any[b,c]] ≡ any[a,b,c]`, dominated thresholds) are *behaviorally inert* (the boolean eval is associativity-invariant), and runstate never compares/dedups/hashes conditions, so canonicalization would buy nothing. (Optional soft `maxDepth` as a pure resource guard.)

Examples: `{}` = fire once now · `{from: {commits: N}}` = fire once at the Nth commit · `{every: {any: [{commits: 10}, {time_seconds: 60}]}}` = every 10 commits or 60 s, whichever first, forever (a lease, for its time atom) · `{every: {commits: 1}, until: {stream: "loss", n: 5000}}` = every commit until 5000 `loss` elements (durable).

Helper sugar (Tenacity-style operators — `now()`, `at(...)`, `every(...)`; `|` → `any`, `&` → `all`) was proposed and never built: the wire `Condition` dicts are the surface.

### Correlation, visibility, acks

- `request_id` (envelope) **names a request**: a `control.subscribe`, a `control.stop`, or the request a `control.withdraw` spends. Request ids are **one namespace**, never reused: an id an answer has spent (an exit's `honored`, a withdrawal, a refusal, or a node's `expired`) is dead on arrival, and a missing or empty id is **nameless**, refused `malformed` once with a `nak` whose `request_id` is null. A `value` carries no `request_id`: an asker reads the samples answering its subscription by name, at the nodes whose `fired` names its id (`answers`, or the held `Answers` fold). The worker reads all `control.*` regardless.
- **No registration ack, no per-request receipt.** "Did my request land?" = the claim's **consumption watermark**: the `consumed_seq` its commits and heartbeats carry — the seq of the newest `control.*` record its drains have handled, a skipped one included, which a drain moves past a record only once what that record needed (a `bound`, a `nak`) has landed. **Failure** is `lifecycle.nak` (envelope `request_id` = the offending request; body `{reason, message}`, `reason ∈ {malformed, unsatisfiable, unsupported}`), written by compare-and-append at the tail the drain read, so a death report that lands first fences the worker before it refuses anything. `unsatisfiable` refuses, against the declaration of the claim evaluating it: a subscription targeting a name that claim does not declare a source; a progress atom naming a declared source (in a schedule, or a stop's `from`); and an **empty window** (`from ⟹ until` on one coordinate, an absent `from` read as `from` 0). `malformed` refuses a body that does not conform, a pure demand with a `from`, an `every` or a `count` atom, and a re-send of a live id that changes its target or its class (lease or durable); `unsupported` an unknown `control` verb. One bad message is naked and spent, never fatal. The other never-fire signals: the worker's **exit** (it stopped first); a node's **`expired`** (the schedule ended there, `until` met or a one-shot fired); and a **void lease**, bound to a claim that has ended. "Did my request land?" is **answer-first, by name**: `await_consumed(ch, seq)` returns a `Nak` (refused), `None` (accepted: a watermark of a claim reaches it, or an answer names it — a node's `fired` or `expired`, an exit's `honored`), or the terminal `RunResult` (the latest claim ended by its own exit above the request without taking it, or the head is closed, so nothing will). A request above an ending exit, or above the last watermark of a claim that death reports ended, waits for the next claim. It raises `ValueError` up front where `None` would be a false "accepted": no record at `seq`, a record that is not a subscribe or a stop (a `control.withdraw` needs no worker), a nameless request, a spent id, or a void lease (`specs/commits-and-lineage.md` §5). The autonomous worker's plain `stopped()` registers no subscription on its way out: a subscribe racing it stays live for the next claim.

**Reference worker loop** (each `commit()`; `specs/commits-and-lineage.md` §4). 0.3.0's `tick(step)` became `commit()`, with no argument: the log holds the position.
1. **Drain.** Read the death reports naming the claim first (a worker released by one fences itself and writes nothing more), then every `control.*` record up to the drain's W: a spent id is skipped silently, a nameless one refused once; a subscribe is registered or refused (a lease's `lifecycle.bound` and every `nak` CAS-appended at the tail the drain last read, re-draining on a lost CAS); a stop joins the **pending set**; a `control.withdraw` spends what it names, its body never read. A subscription registered in this drain may fire in this commit.
2. **Evaluate** subscriptions at the node being written, its pending values included, and write **one** sample per source whose subscriptions fired (a source with no value yet does not fire).
3. **Write the `lifecycle.commit`**, with `values` (the pending values, then the samples), `fired`, `expired`, `parent` (the claim's position before it) and `consumed_seq`. Then run the save policy (§6 of the spec; a save that raises propagates, the node standing). A `commit()` that raises before its node lands tears the iteration, as `discard()` does.
4. **Return the stop decision** — the pending set's `any`-join, a monotone *level* (§7), each stop's `from` evaluated at each node and each `heartbeat()`. The driver (`steps(until)` toward a `Target`, or `serve()` while demand is live) then writes the exit. `heartbeat()` drains and writes liveness only, inside a slow iteration; `rewind(target)` restates the position; `discard()` declares the open iteration torn. (A *service* worker exits at zero live demand via the careful death — `retire()`, specs/service-worker.md — an explicit opt-in by verb, never a default.)

## 7. Lifecycle convention

Well-known **outbound** events (`worker → observers`), reserved `lifecycle.*`.

(Notation: in a **worker-authored body** (`lifecycle.*`, `value`), `field?` means *present but nullable* — the worker always sends the key, possibly `null` (e.g. a commit's `parent`, null at the first node after a fresh start or a rewind to the root) — whereas at the **envelope** level `name?`/`request_id?` are genuinely *omittable-or-null*. The schemas pin this: nullable-present body fields are `required` with a `["…","null"]` type. The one deliberate carve-out from present-nullable: the orchestrator-authored **schedule slots** (§6 `from`/`every`/`until`) are *omittable and non-nullable* — absence is itself the semantics (fire-now / one-shot / never-expire), not a missing measurement, and `{"from": null}` is schema-invalid.)

| Topic | Semantics |
|---|---|
| `lifecycle.started` | The **claim**, pushed on attach. Body `{handle, t, resume_from, sources}` — *who* computes (the worker self-reports its **liveness handle**, §8), *where* it starts (`resume_from`: an extendable node of this run, or null for no node of it), and which names it exposes as **sources**, sampled on demand. Its `seq` is the claim's **name**: every record that speaks for the claim carries it as `claim_seq`. (A `hostname` field was removed in `lifecycle`-`v0.3`, 2026-07-10.) |
| `lifecycle.commit` | A **lineage node**, one per `commit()` (new in `lifecycle`-`v0.6`). Body `{claim_seq, consumed_seq, parent, values, fired, expired, t}`: the node it extends, the value seqs it commits, the subscriptions that fired and expired there, and the drain watermark. |
| `lifecycle.rewind` | Restates the claim's position, never who (new in `lifecycle`-`v0.6`). Body `{claim_seq, resume_from, t}`. |
| `lifecycle.stopped` | The worker's **exit**, its claim's last node, by the dying-breath CAS; at most one per claim. Body `{completed, error, claim_seq, honored, parent, values, fired, expired, t}`. `completed=True` is the worker's opt-in claim of intrinsic completion, and closes its lineage; a non-completed exit is extendable unless it discarded its open iteration (`values: null`, every error exit), which makes it **torn**. `honored` is the set of stops it honoured (below). A crashed worker writes nothing — absence ≠ alive (§8): a death report ends its claim. |
| `lifecycle.heartbeat` | **Pushed beacon** (`request_id=None`), liveness for inside a slow iteration; **not a node**. Body `{claim_seq, consumed_seq, t}` — serves **liveness** and the **registration watermark** (§6), for the claim `claim_seq` names. `step` is removed (`lifecycle`-`v0.6`): progress is the lineage's `Commits()` count. `t` is the emitter's wall-clock (`specs/observer-clock.md`); the Watcher's staleness runs on its own arrival clock, which a new `started`, `commit`, `heartbeat` or `rewind` naming the latest claim resets. |
| `lifecycle.bound` | Body `{claim_seq}`; envelope `request_id` = a lease. CAS-appended by the worker's drain before it registers that lease, binding it to the claim `claim_seq` names (§6; `specs/time-lease-boundary.md`). |
| `lifecycle.nak` | Negative ack (§6); body `{reason, message}`, `reason ∈ {malformed, unsatisfiable, unsupported}` (syntactic / semantic / unknown-verb), envelope `request_id` = the offending request, or null for a nameless one. CAS-appended by the drain, as `bound` is. |

**Commits and lineage** (log format 0.4.0; `specs/commits-and-lineage.md` §2). ***A commit is a lineage node: it names the node it extends and the values it commits. A lineage is the chain of nodes from a head, by `parent`. A stream's elements are its values committed along a lineage.*** A **node** is a `lifecycle.commit` or an exit, named by its own seq. A value belongs to the node whose `values` names its seq; one no node names (pending at a rewind, a discard or an error exit, or a register's raw send) is outside every lineage. A node is **closed** iff it is a completed exit, **torn** iff it is an exit that discarded its open iteration (`values: null`), and **extendable** otherwise; `parent` and `resume_from` name only extendable nodes. One record per event: `commit()` writes one node per iteration, so **`Commits()` counts iterations** ("step" means this count everywhere, never depth); `heartbeat()` is not a node; a `rewind` restates the position and keeps the claim, its leases and its liveness. A claim's **position** is left by its newest position-bearing record (a commit leaves itself; a `started` or a `rewind` leaves its `resume_from`), and its **head** is its readable exit, if any, else its position. Every read that depends on a branch takes a required `head` Strategy (`LatestClaimHead()`, or `AtNode(x)` for a fixed node), and a read is **settled** iff its subject cannot grow: always under `AtNode`, and under `LatestClaimHead` iff the head is closed. **Membership** is by the committing claim's declaration: a name it declared in `started.sources` is a source, sampled at the nodes where a subscription fires; every other name is an eager stream, an element whenever emitted. The worker marks a torn iteration with `discard()`, from its own flow; a `commit()` that raises before its node lands tears the iteration the same way, since a raised write's outcome may be unknown.

**Worker-owned termination.** Stopping is always the worker's decision — intrinsic completion, data-dependent stops, and commanded stops (`control.stop`) all feed one stop check. The orchestrator never *removes* a worker, and no third party forges its exit: a death is reported from outside, by `supervisor.died` (§8). (`control.stop` "landed" = the watermark; its *effect* = the exit whose `honored` names it; there is no separate stop receipt.)

**The stop request/outcome pair & the unifying drain rule** (`specs/stop-discharge.md`; 0.4.0: `specs/commits-and-lineage.md` §2, §4). A `control.stop` is the *request half* of a request/outcome pair, named by its `request_id` (required since `subscription`-`v0.3`): **pending until an exit names it in `honored`, a `control.withdraw` names it, or a `nak` names its id**. An exit's `honored` is a **set**, read by name: every stop pending at the exit, due or not, and every stop whose own condition held at a node or a heartbeat of the claim, even one withdrawn since — so a withdrawal after `commit()` or `heartbeat()` has returned True is too late. A discharged stop is history (provenance for "which stops did this exit answer?"), never again input. Within a claim the pending stops are a **set of monotone predicates**; the stop decision is their `any`-join — a *level* that latches for the rest of the claim, a rewind included, never a consumed-once pulse (`Worker.stop_pending` exposes the same predicate as a side-effect-free poll). **The dying breath is a compare-and-append over a fully read control tail**: the worker reads the head, takes in every stop up to it, and appends its `stopped` with `expected_seq` at that head, re-reading (without evaluating again) whenever the CAS loses. So a stop that lands before the breath is named in it, and one that lands after it stays pending for the next claim; none is discharged unseen. This instantiates the **unifying drain rule**: *every control fact is live until a record names it spent, and the worker folds the log applying them.* A subscribe is spent by a withdrawal or a `nak`, and its expiry is its class's: a lease's on any node of its claim, a durable subscription's on the head's lineage only; a stop is spent by an exit that honours it, a withdrawal or a `nak`. Both verbs re-derive across claims: a stop sent while the run is *down* — even before any worker exists — is answered by the next claim, immediately and **exactly once**; one honoured on one branch is not re-armed on another, since a stop is a request about the process.

**Reference by name** (`specs/reference-by-name.md`; extended by `specs/commits-and-lineage.md`), the design's one pairing rule, stated here once: ***a record that answers, ends or concerns another names it, by an identity that is never reused. It is never related to it by log position.*** The instances: a `control.stop` ↔ the exit whose `honored` lists its `request_id`, or a withdrawal or nak bearing it (public observer homes: `observables.undischarged_stops`, and its incremental forms `PendingStops` / `Watcher.pending_stops`); a `control.subscribe` ↔ the withdrawal or nak bearing its `request_id`, or a node's `expired` (the **answer fold**; once any answer names an id the id is **spent**, wherever the answer sits; public homes `observables.live_demand` and `LiveDemand`); a **lease** ↔ additionally the `lifecycle.bound` naming the claim that registered it (void once that claim has ended — `specs/time-lease-boundary.md`); a commit, heartbeat, rewind, exit or death report ↔ the claim it speaks for, by `claim_seq`; a node ↔ its parent and the values it commits, by seq. **What stays ordered, and why:** *order among claims* — the claim CAS totally orders claims, so a claim's `seq` is both its name and its rank, and the current claim is the latest; *each writer's own order* — a claim's position is left by its newest position-bearing record, and a re-sent request's latest schedule wins; and *the window after a claim*, which is causal, not attribution. No rule compares positions across writers. Within those constraints every named read gives the same answer whatever order records arrive in (`tests/test_order_independence.py`). A claim's end is told from two viewpoints, which no longer conflict (§8): its exit is at most one per claim, by the dying-breath CAS, and its death reports are all kept. A sample is deliberately *not* an answer record: the node's `fired` names the subscriptions it answers, so the answer set stays schedule-independent and the fold body-light. (The formal version belongs to `docs/backlog/protocol-algebra.md`.)

**Lifelines** (the *service worker* — specced: `docs/specs/service-worker.md`; 0.4.0: `specs/commits-and-lineage.md` §4). The kernel: the service worker **counts its live subscriptions exactly and stops at zero** — ref-count-exact, **no grace windows** (demand is enumerable log records, so the timeouts other systems are forced into don't apply; the log buffers demand across relaunch). The observer-vs-lifeline distinction *is* read-vs-subscribe — a passive observer *reads* (invisible, never pins); a client *subscribes* (counted, **pins**, because it's asking the service to produce). A subscription is **live while it is not spent** (by a withdrawal, a refusal or its expiry) and, for a lease, not void; a **pure demand** (a subscribe with a null `name`) never fires and is live until its `until` holds at a node, so it keeps a `serve()` worker alive for its lifetime. Keepalive/orphan: clients subscribe with a time-bounded `until={time_seconds: N}` and refresh; a crash ⟹ expiry, named in the next node's `expired`, and the count drops (reuses the scheduling algebra; the lease-shaped hysteresis is *client-chosen*, never a server knob) — and when the *worker* dies before noticing the lapse, the lease does not resurrect: a lease is bound to the claim that registered it (`lifecycle.bound`) and void once that claim has ended, so a dead client's lease is served by at most one claim, ever. The death is **careful**: `retire()` drains every control verb and compare-and-appends its exit against the drained log, returning False if any subscription is live after either read, so a subscribe racing the death is never orphaned. Its exit is extendable, so a woken successor that holds no state continues from it. The policy is **opt-in by verb** (`serve()` / `retire()`; one worker primitive, two demand durabilities — the launch contract is durable demand, subscriptions are demand by their class): an autonomous worker ignores all of this *by construction* — no term couples its life to observation.

## 8. Supervisor convention + the liveness detector

Reserved `supervisor.*` (renamed from `launcher.*` by `supervisor`-`v0.5`), written from outside the worker — by the spawner/reaper, or by any party reporting a death it holds evidence of. A Layer-3 / process-level concern, not substrate or lifecycle.

| Topic | Body |
|---|---|
| `supervisor.launched` | `{handle, status, t}` — spawn-intent + the worker's liveness **handle**. (It is the launcher's *observation*, never the claim — the lazy-launch race is arbitrated by the worker's birth-CAS; `specs/lazy-launch.md`.) |
| `supervisor.terminated` | `{reason: "exited" | "killed", exit_code?, signal?, t}` — the *manner* of a launch's end; only a `wait()`ing parent can produce it. It names a launch, and is the verdict only of a run nothing claimed. |
| `supervisor.died` | `{claim_seq, outcome, evidence, t}` — the **death report** (new in `supervisor`-`v0.5`): names a claim, `outcome ∈ {preempted, errored, killed, lost}` (a death is never a completion), `evidence` a non-empty string no fold parses (`reap: exit 1`, `probe: local://host/pid`, `session: …`, `slurm:TIMEOUT`). Envelope `request_id` null. |

**Launch identity (launcher-v0.3; kept by `supervisor`-`v0.5`).** `launched` and `terminated` **must** carry the envelope's `request_id`: one id per launch, minted by the launcher, and **re-emitted by the worker on its `lifecycle.started`** (ambiently — `RUNSTATE_LAUNCH_ID`, beside the other `attach` variables; null iff nobody launched the worker). So a launch, the claim answering it, and the death ending it name the same thing. Without it, `terminated` asserts the unknowable *"the run is dead"* rather than *"my launch ended"* (`specs/launcher-record-identity.md`). Here `request_id` is **correlation only**: it never scopes visibility. A launcher whose reap finds the claim naming its launch without an exit writes the `died` beside its `terminated`, and a Watcher holding a launch's handle reads that launch's claims.

**How a claim ended: two viewpoints, and `ended`** (`specs/commits-and-lineage.md` §2, §3). The worker's exit is its **self-report**; a death report is a **third party's**, naming the claim and carrying its evidence. A claim has **ended** once its exit or a death report naming it is on the log — read by *presence*, so a record ends the claim its `claim_seq` names even when the rest of its body is unreadable, and the predicate never raises. This one predicate serves every fold: the claim gate (`live_episode`), lease voiding, the verdict and the Watcher. The **verdict** is the claim's self-report if one is readable, else the death reports naming it, all kept, their outcomes a set (less `lost` beside another); a run nothing ever claimed takes its launch's `terminated`. **Evidence, never inference:** a reap (exit code or signal), a same-host probe that finds the claim's process gone, an accounting record, and a backend's **session probe** (Postgres: the session that wrote the claim ended) are evidence; staleness, silence and a cross-host absence are not. Evidence without a cause (a dead probe, an exit 0, a thread that returned) reports `lost`, and only while the claim is held. **A read never writes**: `report_if_dead(ch)` is the call that turns a probe into a record, and the library's actors (the claim path, the deciders, `ensure`) call it before they act. A worker that reads a death report naming its own claim is **released**: it fences itself and writes nothing more. Since nothing probes on a read, a same-host crash that nobody reports reads as held until someone reports it.

**The handle** is a portable, scheme-tagged token: `local://host/pid`, `slurm://jobid`, `k8s://ns/pod`, `ray://actor`. (A pid-reuse disambiguator — `local://host/pid?start=T` — is deferred, and the reference parser does not yet accept it: `docs/backlog/conventions-hygiene.md` F9.) Resolving it (`kill -0`, `squeue -j`) answers liveness **actor-independently** — robust even if the launcher is gone, cross-host where the scheme resolves. It obsoletes a `.worker.pid` file (the handle lives in the log). **Single source of truth:** the worker self-reports via `lifecycle.started`; `supervisor.launched` carries the spawn-intent + the launcher's known handle.

**Liveness is a layered, opt-in failure detector** — none of it substrate-owned (presence is *emitted messages*, never substrate state; a mutable TTL'd *lease* is deliberately avoided). Best-to-worst:
1. **The exit** — the claim's `lifecycle.stopped` exists (its self-report).
2. **A death report** — `supervisor.died` naming the claim, from a reap, a probe, a session probe or accounting; and for a run nothing claimed, its launch's `supervisor.terminated`.
3. **Probe** — resolve the handle for the *fact* of death (actor-independent), or ask the backend's session probe whether the claim's session ended. The Watcher reports these as inferences (`probed_dead`, `session_ended`), writing nothing; `report_if_dead` records them.
4. **Staleness** — no `started`, `commit`, `heartbeat` or `rewind` naming the latest claim witnessed within a threshold ⟹ crashed/hung (`heartbeat_stale`). The universal floor. Because the beacons are loop-driven, staleness catches *hangs* (not just crashes) — but for the same reason a worker in a legitimately **long single iteration** (a 20-min epoch, a giant batch) stops beaconing and looks dead unless it calls `heartbeat()` inside it. So the threshold must exceed the worker's *max* inter-beacon gap, which the reader often can't know a priori, and progress-staleness doesn't help (the commit count is frozen *because the iteration is in progress*). This is the irreducible **dead-vs-busy** ambiguity: the threshold is a per-workload tuning (`Watcher(heartbeat_timeout=)`, required).

Three reference configurations: **(a)** floor only · **(b)** + handle (observers self-probe) · **(c)** + handle + reaper (a daemon/stay-attached launcher probes/reaps once and writes the result).

**Spawn vs watch/reap split.** `launch()` does the irreducible job — spawn + emit handle — and returns (all a cluster scheduler permits; fire-and-forget). Watching/reaping is a *separable* role. `terminate()` resolves the handle and kills (`kill`/`scancel`/`kubectl`), not via a parent relationship.

**Defaults (opinion-free ≠ batteries-not-included):** the reference worker loop commits and beacons; the reference `LocalLauncher` writes a `local://` handle and reaps (`reap()`/`wait`/`poll`/`__exit__`), emitting `terminated` for whatever its own child did, and a `died` beside it when the claim naming its launch has the child's handle and no readable exit (`specs/launcher-record-identity.md`). It kills (`SIGKILL`) an orphan of its own, a child whose claim a death report ended, through the process handle it holds, never by pid. `ThreadLauncher` writes the `died` when its thread finishes with no exit. `resolve` is hostname-scoped: another host's `local://` handle is *not locally resolvable* (None → the staleness floor), never a false verdict from the wrong pid table. Every tier is removable.

## 9. Orchestration helpers (Layer 3)

Reference tooling; assumes the conventions (a worker that opts out composes its own loop from `send`/`read`/`latest` + the liveness tiers).

```python
class Launcher(Protocol[T]):                                 # contravariant in its target record
    def launch(self, run_id, target: T) -> LaunchHandle: ...  # ThreadLauncher: Launcher[ThreadTarget]; LocalLauncher: Launcher[Command]
    def create_channel(self, run_id) -> Channel: ...         # birth/open the run for the launch flow
    def attach_channel(self, run_id) -> Channel: ...         # existing-only; RunNotFound is the deciders' "not launched yet" signal (specs/channel-locators.md)

class LaunchHandle(Protocol):                                # concrete per launcher (thread / subprocess)
    run_id: str; channel: Channel; launch_id: str              # the launch's correlation id (§8)
    handle: str                                              # portable liveness/terminate token (§8)
    def is_alive(self) -> bool: ...                          # writes nothing (a read never writes)
    def wait(self, timeout=None) -> int | None: ...          # block until done (+reap, writing terminated and any died); None for a thread
    def terminate(self) -> None: ...                         # force-kill where the substrate allows

class Watcher:                                               # Watcher(*, heartbeat_timeout: float | None, ...): required
    def add(self, handle: LaunchHandle) -> None: ...
    def observe(self, run_id: str, channel: Channel) -> None: ...   # handle-free: late-attach / observe-only (§12)
    def iter_events(self, timeout=None) -> Iterator[tuple[str, Envelope]]: ...   # the stream (deltas)
    def poll(self, run_id) -> RunStatus: ...                 # the fold (Running | RunResult), non-blocking
    def wait(self, run_id, *, on_event=None, timeout=None) -> RunResult: ...
    def wait_all(self, *, on_event=None, timeout=None) -> dict[str, RunStatus]: ...   # total over tracked runs
    def broadcast(self, name: str | None, schedule, *, request_id=None) -> str: ...   # shared request_id; the cross-run barrier; None = pure demand
    def pending_stops(self, run_id) -> list[Envelope]: ...    # undischarged_stops, held

# A run's current status: still-running (a live snapshot) or a terminal verdict.
# The Running arm carries watcher-unique state (beacon_age = the gradient toward
# presumed-dead) not on the raw event stream, so poll is lossless rather than
# returning Optional[RunResult] (None-as-pending). peek_terminal stays Optional —
# the record plane is stateless and can't populate Running.
RunStatus = Running | RunResult
@dataclass class Running:
    head: Head; commits: int; beacon_age: float              # done == False; the newest claim's head and count(Commits(), head)

@dataclass class RunResult:                                  # done == True
    outcomes: frozenset[Outcome]   # CLOSED, never empty: completed | preempted | errored | killed | lost | presumed_dead
    tier: Tier                     # CLOSED: the viewpoint — exit | died | terminated | probed_dead | heartbeat_stale | session_ended
    reports: tuple[DeathReport, ...]; node: int | None; claim_seq: int | None
    malformed: tuple[int, ...]; error: str | None; run_id: str | None
    # No `success`: a pure projection of `outcomes` that would bake one contested
    # policy into the producer. Consumers apply their own (`ensure` fails on any
    # outcome in Outcome.failures()). `outcomes` (normalized, cross-tier) and `tier`
    # (the viewpoint the verdict rests on) are orthogonal. Under death reports the
    # outcomes are the set of the reports' outcomes, less `lost` beside another, and
    # `reports` carries their evidence; under an exit, `node` is the exit node.
    # 0.3.0's verbatim `reason` string became the closed `tier`, and `final_step`
    # became `node` (specs/commits-and-lineage.md §5).

# peek_terminal is the RECORD-based verdict (an exit, death reports, or an unclaimed
# run's terminated); the Watcher adds the INFERENCE-based tiers (probe, session, staleness).
def peek_terminal(channel) -> RunResult | None:            # None while the newest claim is held
def ensure(producer, target: Target) -> Reached | Unreachable: ...   # read-first / produce-on-miss (specs/memoizer.md)
def relaunch_if_needed(launcher: Launcher[T], run_id, target: T) -> LaunchHandle | None: ...
def ensure_served(launcher: Launcher[T], run_id, target: T, *, demand: LiveDemand) -> LaunchHandle | None: ...
```

**The stateless observables** (`observables.py`; spec: `docs/specs/observables.md`, and for log format 0.4.0 `specs/commits-and-lineage.md` §5). Pure, body-aware folds `log → derived view` — the questions an observer can ask without disturbing the run (the read side of §7's read-vs-subscribe line): `peek_terminal` (above), `live_episode` (the newest claim's handle unless that claim has ended; it never probes), `latest_episode` (the episode-boundary rule — the latest `lifecycle.started`, live or ended; its `seq` is the claim's name and window watermark for `read(after=…)`), `live_demand` and `undischarged_stops` (the answer folds' public homes, §7), `last_activity`, and the **lineage reads**, each taking a required `head`: `prefix_len` (`count(x, head)` for an eager stream or `Commits()`), `prefix` (the threshold read: `Complete`, `Pending` or `SettledShort`), `series`, `last_element`, `aligned`, `answers` and `streams`. A polled path holds the incremental folds (`HeadFold`, `PrefixLen`, `LastElement`, `Answers`, `LiveDemand`, `PendingStops`), each O(new records) per poll and equal to its pure read after every record. 0.3.0's `progress` and `value_series` are removed: the first was `prefix_len` under a 0-based label, and the second's (name, step) merge could splice branches, which a lineage cannot. Observe *statelessly* there; watch *statefully* here — the `Watcher` adds the one non-log-derivable input, arrival time. (The substrate *projects*, §4, body-opaque; the conventions *observe*, body-aware.)

**Cross-run synchronization.** `Watcher.broadcast("loss", {"from": {"commits": 100}})` fans one subscription across all tracked runs (one shared `request_id`; the `run_id` disambiguates responses; `name=None` broadcasts pure demand). It's the primary cross-run mechanism — *no Experiment class*. It is a **pure synchronization**: it blocks until every *live* run reaches the point, so a slow-but-healthy run **legitimately delays it, unbounded by design** (that is what "synchronize" means). Each run resolves to exactly one of: **fires** (a node's `fired` names the id); **`nak`** (refused — e.g. `loss` is not a source that run declares); **an exit** (stopped before reaching it — excluded); **heartbeat-stale** (crashed/hung — excluded). A **bounded-latency** caller MUST additionally supply a **patience cap** — a wall-clock deadline after which the barrier returns *partial* results (still-running slow runs reported as pending). So the cap is *optional for a pure sync* but *required for a bounded wait*. (The never-fire causes → handlers: refused → `nak`; stops-early → the exit; too-slow-for-my-patience → the cap; and a **time-keyed barrier subscription is a lease**, bound to the claim that registered it, so on a run that resumes it is void for the new claim (`specs/time-lease-boundary.md`) — **broadcast schedules should be keyed by lineage counts** (`{commits: n}`, `{stream, n}`), which are durable; a boundary-aware re-broadcasting Watcher is a backlog note.)

## 10. The schema stack (freeze story)

No single schema:
- **Envelope schema** — `{seq, topic, name?, request_id?, body}` with `body: object` (opaque). Close to freezable; constrains neither `topic` semantics nor body shapes.
- **Convention schemas** — each strictly pins its own well-known bodies (`additionalProperties: false`), independently versioned. Adding a field to a well-known body is a deliberate convention-version bump. **User `value` bodies** pin only the *wrapper* (`{value, t}`, `t` nullable); the `value` payload is `Any` JSON-serializable value — a sender-side `json_default` hook (on `create_channel`/`attach_channel`/`current_channel`) coerces exotic types (numpy scalars, tensors), and an unserializable value with no hook fails fast at emission (naming the metric), rather than silently dropping it. Validation is opt-in/layered: the substrate never validates; "opt-in convention" means "you needn't emit `lifecycle.*`, but if you do, conform."

This is the cut that dissolves "blocking for the schema": the convention decisions block specific *convention* schemas, downstream — not the envelope. **Freeze status (versions current as of 2026-10-30):** the stack is written and frozen in `protocol/` — `envelope-v0.2` plus `value`-`v0.3`, `subscription`-`v0.4`, `supervisor`-`v0.5` and `lifecycle`-`v0.6` (`specs/commits-and-lineage.md` §3: `lifecycle.commit` and `lifecycle.rewind` new, `resume_from` and `sources` on `started`, the exit a node, `step` removed from the heartbeat and `value`, `control.withdraw` new and `control.unsubscribe` retired, the `{commits: n}` and `{stream, n}` atoms replacing `{step: N}`, and `launcher.*` renamed `supervisor.*` with the new `supervisor.died`), each `additionalProperties: false`. Earlier bumps: `Started.hostname` removed 2026-07-10; launch-id correlation, `specs/launcher-record-identity.md`; a required `t` on the dated bodies, `specs/observer-clock.md`; and reference by name, `specs/reference-by-name.md`. The 0.3.0 schemas left `protocol/` with the cutover and are kept as the migration's test fixtures (`tests/fixtures/log-format-0.3.0/`). The logs these records live in are **log format 0.4.0** (§4, `specs/log-formats.md`). The two once-held bodies are pinned: `lifecycle.heartbeat` = `{claim_seq, consumed_seq, t}` (`consumed_seq` = the drain watermark, §6) and `supervisor.launched.status` = `enum ["running"]` (room to widen via a convention-version bump). `tests/test_schema.py` validates that the messages the implementation emits conform.

## 11. Three clocks

`seq` = the substrate's transport order (the per-log total order, §4). The **lineage counts** = the worker's logical clock: `Commits()` (the iterations) and each eager stream's elements, counted along the lineage at a node, never a body field (0.3.0's `step` field is gone; `specs/commits-and-lineage.md` §2). wall-clock = real time. **All scheduling predicates evaluate at the worker's nodes (and a stop's at its heartbeats too), against the lineage counts and wall-clock, never `seq`.** (`consumed_seq` on the commit and the heartbeat is the drain watermark — not a fourth clock; §12 explains its scope.) `latest` orders by `seq`, = "most recent emitted" only for a single writer per (topic, name); a lineage read orders by the lineage instead.

## 12. Open questions — implementation-plan items

The six convention decisions are settled (see revision history). Status tags below are as of the rev-4 implementation pass. **None change the wire *envelope***; the items still open touch operational mechanics, not the frozen schemas. The *deferred* items below are mirrored as discoverable work in `docs/backlog/index.md`.

1. **Lazy-launch double-spawn race** — *[CLOSED 2026-06-11 by `specs/lazy-launch.md`]* `ensure_served` (the leased-demand decider beside `relaunch_if_needed`), the foreign-claim-scoped reap discipline (*deleted 2026-07-14 — superseded by launch identity, `specs/launcher-record-identity.md`*), hostname-scoped `resolve`, the `_lost` guard on explicit `stopped()`, and the mandatory per-cycle `reap()` in the activator recipe. Historical text follows. — *was: [deferred; its inputs now exist]* `launcher.launched.status` is pinned to `running`, but lazy-launch-on-first-`control` itself is **not** built: `launch()` is explicit (you spawn, then send control). The relaunch decider's demand fold is now log-derivable (`observables.live_demand`, specs/service-worker.md), the ghost-lease flap is bounded by construction (`specs/time-lease-boundary.md` — no waker flap policy needed; the one surviving constraint is no `ensure` over stepless services), and the worker-side claim (birth-CAS) and the careful death (death-CAS) bracket the race. The follow-on spec owns it.
2. **`send_request` ↔ `Channel` seam** — *[dissolved with #1]* the decider is caller-invoked (`ensure_served`), never channel-wrapping; the locators (`create_channel`/`attach_channel`) stay plain channels.
3. **`Watcher.observe`** — *[done]* `observe(run_id, channel)` is the handle-free / late-attach / observe-only path; `add(handle)` is the handle path that also enables the probe tier.
4. **`broadcast` returns the assigned `request_id`** — *[done]* `Watcher.broadcast(name, schedule)` returns the shared id; pass `request_id=` to reuse one (cancel-the-lot reachable).
5. **Cursor-persistence mechanics** — *[consumer-side decided out of scope (2026-06-01); worker-side deferred]*. **Consumer side:** the substrate owns durability + `seq`, not offset-tracking — caller-owned cursors (§3). The consumers we ship are *state-deriving* (`Watcher`, `peek_terminal`, `live_episode` fold the durable log to current state), so they re-derive on restart and need *no* cursor; an exactly-once *event-processing* consumer persists its own offset and resumes via `read(after=last_seq)` (~5 lines). We ship the primitive (durable log + `seq` + `read(after=)`), not the policy — same shape as the `run_id` recipe. (Holds because runstate is *fan-out*; competing-consumer / work-queue offset coordination is a different substrate, also out of scope.) **Worker side (still deferred, now bounded):** the worker's in-memory control cursor has no crash-replay persistence; the at-least-once / at-most-once boundary is now *bounded* by the expiry records (since log format 0.4.0 a node's `expired`; specs/service-worker.md) plus the lease binding (`specs/time-lease-boundary.md`) — a consumed one-shot or lapsed lease is never replayed once its record landed, and a lease that escaped its record is void for every episode but the one it is bound to (served by at most one episode, globally); a `count`-`until`'s partial fire-history still resets on replay. Within a process, registration precedes the heartbeat, so `consumed_seq` advances only after registration. The cross-episode shape is **settled (2026-06-09)**: the unifying drain rule (§7; `specs/stop-discharge.md`) re-derives both verbs from `seq 0` and lets counter-records expire them — superseding the earlier state-vs-event framing. What this item retains is pure *efficiency*: resuming the drain from a persisted cursor instead of refolding, orthogonal to correctness. *(Scale evidence, 2026-07-10 — measured: the framing survives for the refold itself — the topic-filtered control re-drain is index-served, ~2 ms on a 10⁶-envelope log — but the dominant resume term was elsewhere: `Worker.__init__` computed its claim-read folds from an **unfiltered** `read()`, ~3.4 s + ~0.8 GB transient at 10⁶. Closed same day: `last_seq()` (§4) + the head-first capped attach — measured 3,400 ms → 1.5 ms on the same log.)*
6. **▸ `consumed_seq` scoping** — *[done for the shipped backends]* the commit's and heartbeat's `consumed_seq` is implemented and frozen (`lifecycle`-`v0.6`: heartbeat = `{claim_seq, consumed_seq, t}`); it is the seq of the newest `control.*` record the claim's drains have handled. On SQLite/Memory/Postgres `seq` is globally ordered per run, so the scalar is well-defined. The per-subject-backend subtlety (a single watermark needs inbound-`control` single-ordering) re-surfaces only when a multi-subject backend (NATS) lands.
7. **▸ Multi-orchestrator support** — *[handled by the drain model]* the worker **drains** `control.>` (processes every command after its cursor), so it never relies on `latest(control.*)` for "was a stop requested?" — multiple orchestrators' commands all take effect. The remaining piece is *attribution* under multiple writers, which is #8. (`peek_terminal`'s `latest` calls are on worker/launcher-written topics, each single-writer.)
8. **Author / provenance** — *[deferred; stopgap available]* nothing routes on author, so by the lift-rule it's not an envelope field. `request_id` is an opaque string in the implementation, so the `"webui:<unique>"` prefix stopgap works today. A real field waits on provenance/authz becoming load-bearing.
9. **Writer-serialization + GC/retention** — *[partial; home-level GC recipe'd 2026-06-11]* writer-serialization is handled (`MemoryChannel` takes a shared lock for concurrent in-process writers; `SqliteChannel` relies on autoincrement + SQLite's own locking). Retention is **full, no GC** *within a log* — which is exactly the precondition `peek_terminal` / resume rely on. **Home-level** collection (delete/prune a whole run's directory) is now a consumer recipe — `specs/store.md` Recipe 3: pointer-rooted mark-and-sweep, gated on `live_episode`, selective-prune default — and never truncates a live log; in-log retention/compaction remains future work.

## 13. Rejected alternatives

(Diagnoses in `design-v0.2-exploration.md`.)

- **Queue substrate (consume-once).** Breaks multi-observer. → a log with caller-owned cursors.
- **`direction` as a substrate primitive (binary `to_worker`/`to_orchestrator`).** Smuggled a role ontology into a "role-free" substrate and couldn't classify the launcher→observer flow. → **content-typed topics + a per-role subscription protocol**; the worker/outside split is emergent, not a namespace axis.
- **A single monolithic message schema.** → the schema stack (§10).
- **A merged `topic`+`name` key (or `topic` carrying user identifiers).** Created a reserved-vs-user collision needing a sigil/root. → **split fields**: `topic` closed/protocol, `name` open/app; collision impossible by construction.
- **A `kind` body discriminator.** Redundant once topics are content-typed. → the `topic` *is* the discriminator.
- **Per-request registration ack (`lifecycle.ack`) and per-request stop receipts.** *Relocated, not eliminated* — "did my request land?" is answered by the heartbeat **`consumed_seq` watermark** + `nak`, amortizing all pending acks into one piggybacked monotonic scalar instead of N per-request messages. (Don't drop `consumed_seq` from a minimal heartbeat thinking "we don't do acks" — it *is* the ack.) Barrier backstop = heartbeat staleness + an optional patience cap (§9).
- **Heartbeat-as-lifeline / requested heartbeat.** Re-created the accidental-pin trap. → heartbeat is a **push beacon** (unsubscribable); lifelines = service-worker **subscription ref-count** (observer-vs-lifeline = read-vs-subscribe).
- **A substrate liveness *lease*** (mutable TTL'd presence). → liveness as emitted messages + observer-side probing.
- **A normal form for the condition algebra.** Canonicalization buys nothing here (we never compare/hash conditions). → accept inert algebraic redundancy; keep the algebra fully recursive.
- **A separate `stop_at` name; `now`/`step:N` as `when` primitives.** → `control.stop` + `from`; one-shot = omit `every`; `now`/`at` are helper sugar.
- **FileChannel / a file backend.** Fan-out scales badly. → SQLite-only; topic-log backends (NATS/…) later.
- **An engine-owned loop (inversion of control — the RunEngine/durable-execution shape).** At scale it converges on a log internally anyway (durable execution = persisted event history + deterministic replay), so the log is the foundation and IoC a programming-model veneer; the layering is one-directional — an opt-in driver can own a user's loop *on* the substrate, but the opinion-free log can never be recovered *from* an engine — and it is totalizing at adoption (workloads rewritten into the engine's vocabulary, against an ecosystem of user-owned training loops). → the cooperative worker: the user keeps the loop; `steps()`'s generator boundary is the deliberate midpoint (the library gets control exactly at safe points); rich interruption (pause/rewind/suspenders) stays an opt-in Layer-3 driver if its use cases ever land (§14, the Pause/Resume backlog item carries the prior art).

## 14. Scope: v0.2 vs later

**v0.2 ships (and now does):** the substrate (Memory + SQLite topic log) + the four conventions + the orchestration helpers (launchers, Watcher, the deciders, `ensure`) + the frozen schema stack. (Shipped since: the cross-host **Postgres** backend, `specs/channel-postgres.md`; log format 0.4.0, `specs/commits-and-lineage.md`. `sweep`, the sequential multi-run helper, was deleted 2026-10-30: a workload helper no consumer called, `dead_ends/sweep.md`.) The §12 items left open (cursor-persistence/crash-replay efficiency, multi-orchestrator attribution, in-log retention/compaction) are non-blocking operational refinements, not protocol gaps (lazy-launch closed 2026-06-11, `specs/lazy-launch.md`; home-level GC recipe'd same day, §12.9).
**Layer 4 — DISSOLVED (2026-06-11, `specs/store.md`):** the once-planned **Store** (relational run/experiment metadata; many-to-many membership; the cross-run reuse-by-hash query) ships as recipes over the existing basis, not a component: the rid is the run's *address* (content-addressed placement — reuse-by-hash dissolves into `ensure` against the one home), membership is the cell pointer + the consumer's tracked tabulated overview, provenance is a backward record on the derived run's own log, and any index is a derived, rebuildable, never-authoritative cache. (The once-planned "Hasher" had already re-scoped to a `run_id()` recipe: content-addressable identity is a substrate affordance via caller-chosen `run_id`.)
**Long-term:** richer data-plane Progress + a viewer-discovery protocol — its *own* protocol in a **separate project** on runstate (never this repo's `protocol/`), distinct from this control protocol. Compose, don't conflate.

**Premise — one authoritative sequencer per run.** A run's log has exactly one sequencer: `seq` is contiguous `1..N` assigned at a single serialization point (§4), and every backend is conformance-tested for it. Replication is permitted only where it preserves that — a consensus log qualifies; a divergent multi-home or eventually-consistent store does not. The scope is **per run**: the number of runs and their distribution is unbounded, and cross-host is served by many hosts writing to *one* home (the Postgres backend), never by two homes that can disagree. **The causal regime — spacelike writers, no global order — is a different protocol, not a later version of this one.** Reference by name (§7) made L2's stop and subscription folds order-independent, but not the episodes: a claim's name is the arbiter's `seq`, so order among claims still needs one sequencer, and a terminal stands until a later claim. Retrofitting causal ordering therefore still rewrites the episode conventions, not merely the substrate. Saying that is more useful than calling it deferred, which implies a roadmap it does not have.

**Consequence — the claim is a Markov boundary.** Given the latest `lifecycle.started`, everything before it is conditionally independent of that claim's liveness and verdict: the claim *screens off* the prior history. So a claim-scoped fold reads only the suffix at or after the claim; only a fold whose question **is** the whole log (a series replay) may scan it, and such a fold never belongs on a polled path. The design implements this wherever it was noticed — the current heartbeat and the claim's verdict read only after the claim, and `ended` is a presence test that never raises, so a malformed record in an old claim's window cannot poison the present — and paid for it where it was not: an unbounded fold both slows as a run ages *and* lets a malformed record from a dead past poison the live present permanently, because an append-only log cannot retract it. One cause, two failure modes (measured 2026-07-29: the launcher tier at 3461 us → 92 us with 1000 prior deaths, and unrepairable → repairable). Run-scoped questions are not exceptions: the answer folds genuinely ask about the whole log (an answer can sit anywhere, and a stop sent before any claim is pending), so the boundary does not apply to them, and the held folds (`PendingStops`, `LiveDemand`) serve them to a polled path incrementally. A lineage read is bounded by its own lineage, walked backward from the head in windows.

## Revision history

- 2026-10-30: **Commits and lineage; log format 0.4.0** (`specs/commits-and-lineage.md`). §6: the condition algebra counts lineage coordinates (`{commits: n}`, `{stream, n}`) in place of `{step: N}`; a schedule is a lease (a time or count atom, per claim) or durable (per lineage), and a stop is by name; `control.withdraw` spends a request and `control.unsubscribe` is retired; request ids are one namespace, and a missing or empty id is nameless; the reference loop is `commit()`, `heartbeat()`, `rewind()` and the exit, a `commit()` that raises tearing its iteration. §7: commits are lineage nodes (`lifecycle.commit`, `lifecycle.rewind`; the exit a node, closed, torn or extendable); `honored` is a set; the lifelines kernel reads "live while not spent". §8: `launcher.*` is renamed `supervisor.*`, and `supervisor.died` is the death report; a claim's end is told from two viewpoints, and the presence-based `ended` serves every fold; evidence, never inference. §9: `Launcher[T]` over target records, `Running(head, commits, beacon_age)`, `RunResult(outcomes, tier, …)`, the lineage reads and held folds; `progress`, `value_series` and `sweep` are gone. §10: `lifecycle`-`v0.6`, `subscription`-`v0.4`, `supervisor`-`v0.5`, `value`-`v0.3`. §11: the logical clock is the lineage counts. §14: the Markov paragraph restated for `ended`. (§4's substrate-contract clause landed earlier the same week.)
- 2026-10-03: **Reference by name; log format 0.3.0** (`specs/reference-by-name.md`). §7's pairing-by-`seq` rule — *a standing fact's eliminator must follow it by `seq`* — becomes reference by name: a record that answers, ends or concerns another names it, by an identity that is never reused. A stop is discharged by a `stopped` whose new `honored` list names it, or by a nak, and the dying breath is a compare-and-append over a fully read control tail; a subscribe's `request_id` is spent once any answer names it; an episode-local subscription is bound to its registering episode by the new `lifecycle.bound` record, replacing the recordless boundary void; heartbeats and `stopped`s name their claim (`claim_seq`). What stays ordered: the claims (by the claim CAS), each writer's own records, and the causal window after a claim. §6: `await_consumed` raises `ValueError` on a spent id or an empty `seq`; the drain skips spent ids and voided leases by name. §9: `progress` is the current episode's frontier; `Watcher.pending_stops`. §10: `lifecycle`-`v0.5`, `subscription`-`v0.3`. §14: the premise and the Markov paragraph restated for names. The package is 0.3.0.dev0, logs are format 0.3.0, and `runstate migrate` carries 0.2.0 logs forward.
- 2026-10-03: **Log formats (stage 1) implemented** (`specs/log-formats.md`). §4 gains the opening-time format check: logs live at `<root>/v<format>/<rid>.db` (a schema per format on Postgres), the locators raise on a newer, older or legacy-unversioned log, and `runstate migrate` carries logs forward through retained, chained, sealing steps. No wire-body change.
- 2026-10-02: **Drift corrections, no design change.** Header and §10 brought to the current schema
  versions (`lifecycle`-`v0.4`, `launcher`-`v0.4`; the heartbeat body is `{step, consumed_seq, t}`
  since `specs/observer-clock.md`). §9's `value_series` no longer claims an episode rewind yields the
  as-resumed trajectory: the abandoned branch survives at cells the resume never revisited, as the
  code's docstring says (`observables.py`, `value_series`).
- 2026-06-11 (rev 11): **Layer 4 dissolved (§12.9, §14; specs/store.md).**
  The Store ships as recipes over the existing basis — rid-as-address
  (content-addressed placement; reuse-by-hash = `ensure` against the one
  home, arbitrated by the shipped birth-CAS), membership = cell pointers +
  the consumer's tracked overview, provenance = the child's birth record,
  index = derived/rebuildable/never-authoritative — plus one library
  helper by the F7 doctrine (`foreign_episode`, the producer gate's
  foreign half; the `extend` seam contract revised to
  liveness-handle-always, and `ensure`'s no-progress guard own-spawn-
  scoped). Home-level GC = pointer-rooted mark-and-sweep (recipe).
- 2026-06-11 (rev 10): **Lazy-launch (§8/§9/§12.1–2; specs/lazy-launch.md).**
  `ensure_served` — the leased-demand decider beside `relaunch_if_needed`
  (two demand durabilities, two deciders): live demand ∧ no live episode ⟹
  launch; caller-invoked (the demander's presence is the keepalive AND the
  waker), with the standing activator as a composition whose per-cycle
  `reap()` is load-bearing (zombies read alive to `kill -0`). Wasted spawns
  stay accepted, their funerals disciplined: the foreign-claim-scoped reap
  rule (skip `terminated` iff exit 0 ∧ never-claimed-after-own-`launched` ∧
  a foreign claim follows) keeps loser corpses off the verdict plane while
  null workers and startup crashes keep theirs; explicit `stopped()` gains
  the loser guard; `resolve` becomes hostname-scoped (a pre-existing
  false-dead off-host that could double-claim). ThreadLauncher named
  degenerate for multi-waker use. Closes §12.1/§12.2.
- 2026-06-11 (rev 9): **Episode-scoped time-leases (§6/§7/§9/§12;
  specs/time-lease-boundary.md).** The one piece of standing state that did
  not re-derive from the log — a lease's elapsed countdown — is dissolved
  rather than persisted: a time-referencing subscribe is discharged by the
  next episode boundary (pairing-by-`seq`'s fourth instance; the boundary
  `started` is the counter-record, recordless by design), with pop-then-skip
  preserving slot semantics. Re-anchor ≤ 1 globally; the dead-client ghost
  costs ≤ 2 relaunches by construction, deleting the waker's entire flap
  policy (backoff/give-up/cadence knobs all rejected as wrong-layer). Five
  never-fire causes, five handlers; acceptance ≠ will-serve documented;
  broadcast barriers steered step-keyed. One shared predicate
  (`schedule.references_time` + the voided-check in `observables`) serves the
  worker and `live_demand`, whose body-untouched purity claim is amended
  (shape-peeking, value-blind). Stops deliberately keep their re-anchor
  (at-least-once is their spec'd posture; no flap is reachable).
- 2026-06-10 (rev 8): **The service worker (§5/§6/§7/§12; specs/service-worker.md).**
  One worker primitive, two demand durabilities (durable = the launch
  contract; leased = subscriptions); the lifeline kernel is ref-count-exact
  with no grace windows, opt-in **by verb** (`serve()`/`retire()`), never a
  default or a flag. New protocol behaviors: **expiry counter-records** (the
  worker completes the subscribe/unsubscribe pair — §5 gains the documented
  completion case; the fourth never-fire handler) under the **positional
  answer fold** (a subscribe is live until an unsubscribe-or-nak *follows* it
  by `seq`; public home `observables.live_demand`); the **careful death**
  (`retire()` — the dying breath is CAS'd against the drained log, episodes
  claimed at both ends); *registered ⟺ fire-possible* enforced (never-recur
  `every` expires; count atoms outside `until` nak `malformed`);
  `await_consumed` is answer-first with the full answer-space codomain
  (`Nak` | terminal `RunResult` | `None`). §7 names the **pairing-by-`seq`
  rule** once (stop/stopped; subscribe/answer; episode terminality). The
  refuted alternatives (lazy-as-primitive, reap-as-condition-data,
  property+recipe-only, a constructor flag) are recorded in the spec.
- 2026-06-10 (rev 7): **The stateless observables (§9)** — `liveness.py` grew
  into `observables.py` (`docs/specs/observables.md`): the stateless observer
  plane (pure body-aware folds; observe-statelessly vs watch-statefully as the
  Layer-3 boundary), adding `latest_episode` (the episode-boundary rule, named
  once), public `progress` (the memoizer's private fold, `None` for absence),
  `value_series` (the §4 register projection lifted to the (name, step) plane),
  and `vocabulary.handle_pid` (one parse site for the handle grammar, with
  `resolve` routed through it). Completes the mycooc audit's F5–F8
  read-projection batch; the consumer's hand-rolled helpers delete in one sweep.
- 2026-06-09 (rev 6): **Folded the stop request/outcome pair + the unifying drain rule into §6/§7** (from `specs/stop-discharge.md`, implemented this date). A `control.stop` is pending until the next `lifecycle.stopped` that follows it by `seq` — its designated *effect*, now also its *discharge* (broadcast: one `stopped` answers every pending stop); the in-episode decision is the pending set's monotone `any`-join (a level, not a consumed-once pulse; `Worker.stop_pending` is the side-effect-free poll). One drain rule covers both control verbs — *every control fact is live until its counter-record* (`unsubscribe` rescinds; `stopped` discharges) — closing the mycooc audit's F2 (lost edge) and F3 (clobber) and the cross-episode stop replay in one re-typing; §12.5's cross-episode question is settled (cursor persistence stays an efficiency item). Executable companions: the S1–S4, crash-edge, and pre-staged stop tests in `tests/test_worker.py`.
- 2026-06-09 (rev 5): **Folded the substrate CAS into §4.** `send(expected_seq=)` (shipped 2026-06-01 with `specs/run-episodes.md` but never folded back here) added to the Surface and given its normative Contract bullet — the concurrency trichotomy settled by the working-tree review: check-and-append is one critical section across handles *and* processes; `None` = provably lost; raise = indeterminate backend fault, never a loss. Executable companions: the conformance race / shared-handle / wedged-writer / moved-log tests in `tests/test_channel.py`.
- 2026-05-30 (rev 4): **Implementation pass.** Built the substrate (Memory + SQLite, conformance-tested over both), the four conventions, the reference `Worker`, and the orchestration helpers (`ThreadLauncher` / `LocalLauncher` + Protocols, `Watcher`, `sweep`), and **wrote + froze the JSON Schema stack**. Reconciliations folded back here: the two held bodies are pinned (heartbeat `{step, consumed_seq}`, `launcher.launched.status` = `running`); §12 annotated with status — #3 (`observe`), #4 (`broadcast` returns id), #6 (`consumed_seq` for the shipped backends) **done**, #7 **handled by the drain model**, #9 **partial** (writer-serialization yes, GC no); #1/#2 (lazy-launch + its channel seam), #5 (cursor-persistence / crash-replay), #8 (author field) **deferred**, non-blocking. Two design refinements surfaced during build and were adopted: `RunResult` dropped `success` for a closed `outcome` + verbatim `reason`; `Watcher.poll`/`wait_all` return `RunStatus = Running | RunResult` (the `Running` arm carries the watcher-unique `beacon_age`), making the fold lossless instead of `Optional[RunResult]`.
- 2026-05-29 (rev 3): **Folded a three-agent review** (calibrated red-team ×2 + an unprimed independent re-derivation that *converged* on the substrate, scheduling, liveness, and cross-run shape — validating the bones). Fixes: the cross-run barrier is a **pure synchronization** (a slow-but-healthy run legitimately delays it, unbounded; bounded-latency callers supply a **patience cap** returning partial results — corrected the "no timeout needed for correctness" overclaim); `nak`-on-unsatisfiable is **static-only** (dynamic never-fire → `stopped`, slow → patience cap; stepless workers `nak` step-conditions); `consumed_seq` scoped to the **inbound `control` order** (well-defined on per-topic-seq backends; gates freezing the heartbeat body) and published **after** durable registration; **register-before-reap** made a normative loop invariant; documented the tick-beacon vs legitimate-long-step tension; per-slot threshold types (`count` only in `until`, structurally); §13 ack relabelled **relocated, not eliminated**; new open items — multi-orchestrator vs `latest(control.*)`, and author/provenance (deferred, with a `request_id`-prefix stopgap). **Envelope freezable; heartbeat body held pending `consumed_seq` scoping.**
- 2026-05-29 (rev 2): **Folded the six resolved convention decisions.** #6 split-field envelope `{seq, topic, name?, request_id?, body}` with a closed content-typed `topic` vocabulary and an open app `name` (dissolves the reserved-vs-user collision); #3 dissolved (`topic` discriminates — no `kind`); #5 the recursive `from`/`every`/`until` condition-algebra over (step, time, count) with `any`/`all`, no normal form (one-shot = omit `every`); #1 no registration ack / stop receipt (heartbeat `consumed_seq` watermark + `nak`-on-unsatisfiable; barrier backstop = heartbeat staleness, no separate timeout); #2 heartbeat = tick-driven push beacon `{step?, consumed_seq}`, lifelines = service-worker subscription ref-count (no reserved name); #4 opaque envelope body / strict-pinned convention bodies / `Any` user payload. Replaced the in/out binary with content-typed categories + the per-role subscription protocol. Remaining open items are implementation-plan only (§12).
- 2026-05-29 (rev 1): Clean rewrite from the converged architecture (topic-log substrate + conventions). Supersedes `design-v0.2-exploration.md` (the full decision trail).
