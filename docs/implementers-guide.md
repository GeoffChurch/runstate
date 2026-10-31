# Protocol-implementer's guide — writing a non-Python runstate

**Status:** DERIVED, not authoritative. Dated 2026-10-30; current to log format
0.4.0 ([`specs/commits-and-lineage.md`](specs/commits-and-lineage.md), revision 2).
This guide restates the wire contract for someone building a **second
implementation** (Rust, Go, TypeScript, …). It is a reading of the binding
artifacts, never the tiebreaker. It was written from the spec and the schemas
alone, then checked against the reference code. The reference modules it names
are at their final paths; until the layer-2 cutover they are staged under
`runstate/_next/`, and their tests under `tests/test_next_*.py`.

**Authority order** (when this guide disagrees with any of them, they win):

1. The **JSON Schema stack** in [`../protocol/`](../protocol/) — the wire format:
   `envelope-v0.2` plus the four 0.4.0 conventions, `lifecycle-v0.6`,
   `subscription-v0.4`, `supervisor-v0.5` and `value-v0.3`, each
   `additionalProperties: false` and independently versioned.
2. [`specs/commits-and-lineage.md`](specs/commits-and-lineage.md) — the 0.4.0
   semantics, cited here as "spec §N". Beside it,
   [`design-v0.2.md`](design-v0.2.md) for the substrate (its §4) and the
   shipped [`specs/`](specs/) for scopes it does not restate.
3. The reference **code** in [`../runstate/`](../runstate/) and tests in
   [`../tests/`](../tests/).

[`overview.md`](overview.md), [`guide.md`](guide.md), and [`api.md`](api.md) sit
below all three (a tour, a how-to, a Python-surface reference); this guide is a
sibling of those — the language-neutral reference. Every rule below cites its
authoritative source; verify against the tree, not against this prose.

A note on what "the protocol" is: runstate is **not** a service you connect to.
It is a **shared append-only log** (a SQLite file, a Postgres table, an
in-process list) plus **rules for the records you write into it**. "Speaking the
protocol" means writing conforming envelopes and folding the log the way this
guide describes. There is no server process, no handshake, no session.

---

## 1. Two tiers of conformance

The design is two layers (design §2), and you may implement either:

- **Substrate-conformant** — you implement the **envelope log** and its five
  operations (§2 below). You can now carry *any* body; you route and index on
  the envelope and never parse the body. A pure log backend (a new storage
  engine, a bridge to NATS/Kafka/Redis Streams) is substrate-conformant and
  stops here. It passes the same conformance suite the Python backends pass
  ([`tests/test_channel.py`](../tests/test_channel.py), and the substrate
  contract's tests in [`tests/test_concurrency.py`](../tests/test_concurrency.py)).
- **Speaks-the-conventions** — on top of a conforming log, you also produce and
  consume the opt-in typed bodies (`control.*`, `lifecycle.*`, `supervisor.*`,
  `value`) pinned by the convention schemas. A worker, an orchestrator, a
  launcher, a supervisor or an observer each speaks *some* of the conventions.

The conventions are **opt-in and independent** (design §5, §10). The minimal
useful client is small: a dashboard whose only job is a stop button implements
**the envelope + one topic** — it appends one `control.stop` record (§3.5) and
reads the `lifecycle.stopped` whose `honored` names it to see the effect. It
never touches `value`, `supervisor.*`, subscriptions, or the condition algebra.
Conversely, a worker that opts out of the conventions entirely composes its own
loop from raw `send`/`read`/`latest` — the substrate imposes no message shape.

Interop is **by wire records, not by API**. Your implementation and the Python
reference interoperate iff they agree on (a) the envelope, (b) the storage
layout of a shared backend (§5), and (c) the convention bodies you both touch,
**and the rules for folding them** (§3, §4). Nothing else is shared — no code,
no RPC, no run-time negotiation.

---

## 2. The substrate contract (design §4)

A channel is a handle on **one run's** append-only log of envelopes. The
envelope (design §4; [`envelope-v0.2.schema.json`](../protocol/envelope-v0.2.schema.json),
unchanged in 0.4.0; `runstate/channel/envelope.py`):

```
seq        : integer >= 1     substrate-assigned total order (see below)
topic      : string, len>=1   CLOSED protocol-owned routing key
name       : string|null      OPEN app-owned identifier (a stream, a source)
request_id : string|null      correlation + visibility scope
body       : object           opaque to the substrate
```

`seq`/`topic`/`body` are required; `name`/`request_id` are present-or-null but
**never the empty string** (`minLength: 1` on each — the schema rejects `""`).

### 2.1 The five operations and their exact semantics

The surface (design §4 "Surface"; `runstate/channel/base.py`):

```
send(body, *, topic, name=None, request_id=None, expected_seq=None) -> int | None
read(after=0, *, topics=None, name=None, request_ids=None, limit=None) -> [Envelope]
latest(topic, name=None) -> Envelope | None
last_seq() -> int
close() -> None
```

- **`send` — append + opaque body.** Appends one envelope, returns its `seq`.
  The substrate **never parses `body`**. The body is **value-snapshotted at
  `send`**: mutating the passed object afterward (or a returned envelope's body)
  never alters the log (design §4 "Value-snapshot at `send`"). A body that is
  **not serializable fails at `send`**, naming the problem — never later, on an
  innocent reader. (The reference coerces exotic scalars via a sender-side
  `json_default` hook and otherwise raises; your implementation must fail the
  *append*, not silently drop or corrupt.)

- **`send(expected_seq=S)` — compare-and-append (CAS).** Appends **iff** the
  log's last `seq` is exactly `S` (with `S == 0` meaning the empty log), and
  returns the new `seq`. The **CAS trichotomy is load-bearing and normative**
  (design §4 "Compare-and-append"):
  - returns the new `seq` → **won**;
  - returns **`None`** → the claim is **provably lost** (the log moved past
    `S`);
  - **raises** → the outcome is **indeterminate** (a backend fault, e.g. a
    competing writer wedged past the backend's wait bound).
  A backend **must never** synthesize `None` (a "lost" verdict) out of an
  indeterminate fault. The check-and-append is **one critical section across
  handles and processes**. It is the **only** non-fold operation in the surface
  ([`backlog/protocol-algebra.md`](backlog/protocol-algebra.md) L1), and in
  0.4.0 it carries more than the claim: the claim, the exit, every
  `lifecycle.bound` and `lifecycle.nak` a drain writes, and every death report
  are CAS-appended at the tail their writer last read (§3.8, §3.10).

- **`read` — cursored, non-destructive.** Returns envelopes with `seq > after`,
  in `seq` order, filtered by `topics` (§2.3), `name` (exact), `request_ids`,
  and `limit`. **Cursors are caller-owned**: the substrate keeps **no per-reader
  state and no registry of who is reading** (design §4 "Caller-owned cursors").
  - `request_ids` scopes **visibility**: a reader passing `[r1, r2]` sees
    records whose `request_id ∈ {null} ∪ {r1, r2}`. An **empty**
    `request_ids=[]` means "only the broadcasts" (`request_id IS NULL`).
    Visibility is **read-side filtering, not enforcement** (design §6).

- **`latest(topic, name=None)` — the register/most-recent projection.** The most
  recent envelope on a `(topic, name)`, or `None`. Exact topics only, no
  patterns (§2.3).

- **`last_seq()` — the CAS's read half, and every read's W.** The log's last
  `seq` (`0` = empty), O(1) on every backend. Every 0.4.0 read takes it first
  (§2.2).

- **`close()`** — release *this handle's* resources. Does **not** delete the
  log, stop the worker, or affect any other handle on the run. On Postgres it
  also ends the handle's session, which the session probe reads (§4.7).

### 2.2 The `seq` contract, and the substrate contract

`seq` is **one contiguous, 1-based sequence — exactly `1..N`, no gaps, across
all topics** — assigned by the log's single sequencer (design §4 "A per-log
total order is the contract"). `last_seq() == N`, and the CAS base case is
`expected_seq=0`.

**The substrate contract** (design §4; spec §5) is stronger than contiguity:

> **A reader that has seen seq W has seen every seq below W.** A read that
> returns seq W returns every seq below W that matches it, and a `last_seq()` of
> W promises the same of the next read.

A finished log can be contiguous and still have broken it: a sequence allocator
that publishes seq W+1 before W is visible broke it 30,780 times while leaving a
contiguous log (review §11.2, D9). So a check of the finished log cannot see a
violation; the conformance test is concurrent — writers append while a reader
takes `w = last_seq()`, reads the tail after its cursor, and finds it contiguous
and reaching `w` (`test_interop_substrate_contract_across_threads` and
`_across_processes`, by tier). Every caller-owned cursor depends on it, since a
cursor past a seq not yet visible skips that seq for good.

Two rules follow for every 0.4.0 read:
- **Reads take `W = last_seq()` first** and read nothing above it.
- **Reads never wait.** Completeness is well-formedness (§4.3): a record that
  names a seq at or below W can rely on that seq being there.

The conventions compare positions *across* topics only through "a record
follows what it names" and the CAS (§3.2), so a backend offering only per-topic
FIFO **cannot host the conventions**.

Assign `seq` from a single serialization point. SQLite uses `INTEGER PRIMARY KEY
AUTOINCREMENT`; Postgres uses `MAX(seq)+1` under `PRIMARY KEY (run_id, seq)` (a
global `SERIAL` would gap on rollback and is wrong — §5.3); the in-memory
backend uses `len(log)+1`, and reads a tail as a slice (seq = index + 1) with a
per-topic index for `latest` (spec §5).

### 2.3 The topic-pattern grammar (in full)

The **only** wildcard is a **trailing `.>`** (design §4 "The pattern grammar, in
full"):

- `"control.>"` matches every topic **starting with** `"control."` — the text
  before `.>` is a **literal prefix**.
- **Metacharacters in the prefix are inert.** A topic containing `*`, `?`, `[`,
  `%`, `_` matches only itself: SQLite escapes `*?[` into single-character GLOB
  classes, Postgres escapes `%`, `_`, `\` for `LIKE`. Your backend must
  neutralize its own matcher's metacharacters.
- **Matching is case-sensitive.**
- There is **no bare `">"`**, and **no mid-string wildcard**.
- **`latest` takes exact topics only** — no patterns.
- `read(topics=[])` (an explicit empty list) returns **nothing** — distinct
  from `topics=None` (no filter).

### 2.4 Thread-safety and retention

- **Every handle is thread-safe** (design §4). One handle may be shared across
  threads; every acknowledged send is durably in the log and at most one CAS
  wins.
- **Retention is full** until the run's channel is explicitly cleaned up
  (design §4). There is **no in-log GC or compaction**: a lineage walk, the
  verdict and the answer fold all read records from the whole log
  ([`backlog/in-log-compaction.md`](backlog/in-log-compaction.md)).

---

## 3. The conventions (log format 0.4.0)

Opt-in typed bodies over the substrate. Wire versions for log format 0.4.0
(spec §3): envelope at **v0.2** (unchanged); lifecycle at **v0.6**;
subscription at **v0.4**; supervisor at **v0.5** (renamed from `launcher-v0.4`);
value at **v0.3**. Each schema is `additionalProperties: false` and
independently versioned; adding a field is a deliberate version bump.

**The one rule** (spec §2), from which most of this section follows:

> **A commit is a lineage node: it names the node it extends and the values it
> commits. A lineage is the chain of nodes from a head, by `parent`. A stream's
> elements are its values committed along a lineage. The claim states who
> computes and from which node; a rewind restates the node; the exit is the
> claim's last node. A completed exit closes its lineage, and nothing extends an
> exit that discarded a torn iteration.**

### 3.0 Notation: present-nullable vs omittable-slot

Two distinct "optional" shapes; the schemas pin the difference and your types
must too:

- **Present-nullable** (every `lifecycle.*`, `supervisor.*` and `value` body
  field): the key is always present, its value possibly `null` or `[]`. The
  schema marks it `required`. Example: a fresh claim writes
  `"resume_from": null` and `"sources": []`, never omits them.
- **Omittable-slot** (the schedule slots `from`/`every`/`until` of a
  `control.subscribe`, and `from` of a `control.stop`): the key is *omittable
  and non-nullable* — **absence is itself the semantics** (fire at once /
  one-shot / never expire), and `{"from": null}` is **schema-invalid**.
- At the **envelope** level, `name`/`request_id` are *omittable-or-null* for
  the substrate, but every convention below states which it requires present.

### 3.1 The topics table

The reserved topics (spec §3). The substrate routes on `topic`/`name`/
`request_id` and never parses `body`.

| topic | envelope `name` | envelope `request_id` | body | produced by | schema |
|---|---|---|---|---|---|
| `control.subscribe` | the source, or null (pure demand) | the subscription | schedule `{from?, every?, until?}` | asker | `subscription-v0.4` |
| `control.stop` | null | the stop | `{from?}` | asker | `subscription-v0.4` |
| `control.withdraw` | null | the request withdrawn | `{t}` | asker | `subscription-v0.4` |
| `lifecycle.started` | null | the launch id, or null | `{handle, t, resume_from, sources}` | worker | `lifecycle-v0.6` |
| `lifecycle.commit` | null | null | `{claim_seq, consumed_seq, parent, values, fired, expired, t}` | worker | `lifecycle-v0.6` |
| `lifecycle.heartbeat` | null | null | `{claim_seq, consumed_seq, t}` | worker | `lifecycle-v0.6` |
| `lifecycle.rewind` | null | null | `{claim_seq, resume_from, t}` | worker | `lifecycle-v0.6` |
| `lifecycle.stopped` | null | null | `{completed, error, claim_seq, honored, parent, values, fired, expired, t}` | worker | `lifecycle-v0.6` |
| `lifecycle.bound` | null | the lease | `{claim_seq}` | worker | `lifecycle-v0.6` |
| `lifecycle.nak` | null | the request refused, or null | `{reason, message}` | worker | `lifecycle-v0.6` |
| `supervisor.launched` | — | the launch id | `{handle, status, t}` | launcher | `supervisor-v0.5` |
| `supervisor.terminated` | — | the launch id | `{reason, exit_code, signal, t}` | launcher | `supervisor-v0.5` |
| `supervisor.died` | — | null | `{claim_seq, outcome, evidence, t}` | any party with evidence | `supervisor-v0.5` |
| `value` | the stream or source | null | `{value, t}` | worker | `value-v0.3` |

(— : the schema does not constrain it; write `null`.)

**Retired in 0.4.0:** `control.unsubscribe` (its two jobs went to
`control.withdraw`, the asker's, and a node's `expired`, the worker's), the
`launcher.*` prefix (renamed `supervisor.*`), `Heartbeat.step`, `Value.step`,
`Stopped.final_step` and the `{step: N}` atom. A 0.4.0 log holds none of them;
a 0.3.0 log reaches 0.4.0 only through `runstate migrate` (spec §7).

**Fields** a schema checks (spec §3, "The fields"):
- `claim_seq`: an integer ≥ 1, never null, on `commit`, `heartbeat`, `rewind`,
  `stopped`, `bound` and `died`: the seq of the `lifecycle.started` the record
  speaks for or ends.
- `resume_from` and `parent`: an integer ≥ 1, or null.
- `values`: an array of unique integers ≥ 1, order significant, no `maxItems`;
  nullable on `stopped` only.
- `fired`, `expired`, `sources` and `honored`: arrays of unique, non-empty
  strings.
- `consumed_seq`: an integer ≥ 0, the control-drain watermark. In the
  reference it is the seq of the newest `control.*` record the claim's drains
  have handled, 0 before the first; the drain records it only after it has
  written what that record needed (a `bound`, a `nak`), so a watermark at or
  above a request's seq means the request was registered or refused.
- `t`: a number ≥ 0, required on every lifecycle, control-withdraw and
  supervisor body that has one; **nullable on `value`** (the data plane's clock,
  which a writer may not have: translation writes `t: null`). `nak` and `bound`
  are undated.
- On `stopped`, three couplings, each an `if`/`then`: `completed` ⟹ `error`
  null; `error` non-null ⟹ `values` null; `values` null ⟹ `fired` and
  `expired` empty.

**Constraints no schema can check** (marked *prose* in spec §3, and listed in
`lifecycle-v0.6`'s description): `resume_from` names an extendable node below
its record's seq; `parent` names an extendable node and equals the claim's
position; every seq in `values` is below the record's own; each `fired` names a
source subscription the claim registered; a request in `expired` fires at no
later node of that lineage; a seq is named at most once along a lineage;
`died.claim_seq` names a `lifecycle.started`. Your reader enforces the first
three and the duplicate where §4.3 says, and raises `MalformedRecordError`.

### 3.2 Nodes and lineage (spec §2)

**Terms** (spec §2):

| term | of | meaning |
|---|---|---|
| node | a record | a `lifecycle.commit`, or a worker's `lifecycle.stopped` (the exit node); named by its own seq |
| closed | a node | a completed exit |
| torn | a node | an exit that did not complete and discarded its open iteration (`values: null`) |
| extendable | a node | any other node: a commit, or a non-completed exit with non-null `values` |
| lineage | a node | the chain from it by `parent` to a node whose `parent` is null |
| position | a claim | where it stands now (§3.3) |
| head | a claim | its readable exit, else its position |
| settled | a read | its subject cannot grow |
| `ended` | a claim | its exit, or a death report naming it, is on the log |
| held | a claim | not `ended` |
| released | a worker | it has read a death report naming its own claim, and writes nothing more |
| source / eager | a name | sampled on demand / an element whenever emitted |
| lease / durable | a subscription | has a time or count atom, lives per claim / has neither, lives per lineage |
| spent | a request | named by a node's `expired`, a `withdraw`, a `nak`, or (a stop) an exit's `honored` |
| void | a lease | its claim has ended |
| W | a read | the reader's `last_seq()`, taken first |

**Closed, torn and extendable** are read from the exit's `completed` and
`values` alone:

| exit | `completed` | `error` | `values` | kind | outcome |
|---|---|---|---|---|---|
| completed | true | null | array or null | **closed** | `completed` |
| preempted (a stop, a target reached, a plain exit) | false | null | array | **extendable** | `preempted` |
| preempted after `discard()` | false | null | null | **torn** | `preempted` |
| errored | false | string | null | **torn** | `errored` |

A completed exit is closed whatever its `values`; a completed exit after
`discard()` writes `values: null` and is closed, not torn. Every commit is
extendable. A node's kind never changes, so a judgment about a node is
permanent.

**The node rule** (spec §2, "The commit"; spec §9's first item):
- **`parent` names an extendable node** of the same run, or is null at the first
  node after a fresh start or a rewind to the root. It is **the claim's position
  before the node**; an exit's `parent` is the position at the exit.
- **A node follows what it names:** every seq in `values` is lower than the
  node's own seq, and so is `parent`.
- **A value belongs to the node whose `values` names its seq.** A value no node
  names is outside every lineage: uncommitted (pending at a rewind, a discard or
  an error exit, written after its claim's exit, or a source's register write),
  or foreign.
- **Order within a node** is the order of its `values` list: the writer's own
  order, which no reordering of the log can move. Two `emit`s of one name in one
  iteration are **two elements**, not one replacing the other.
- **A seq named twice along one lineage** is malformed.
- **One record per event:** one `commit` per iteration; a `heartbeat` is
  liveness only, not a node; a rewind is a record of its own.

### 3.3 The claim, the position and the head (spec §2, §4)

**Who and where.** `lifecycle.started` is the claim: *who* computes (the
witness, given exclusive ownership by the claim CAS, holding liveness, leases and
the process's state) and *where* it starts:
- `resume_from` is an extendable node of this run, or null: no node of this
  run — a fresh start, or state no node of the run holds, such as a checkpoint
  from outside it. **A claim that resumes from a node must name it**, or a
  resumed episode silently becomes a fresh root.
- `sources` declares the names the claim exposes to be sampled on demand
  (§3.4); `[]` declares none.
- The envelope `request_id` re-emits the launch id (§3.12), or is null for a
  run nobody launched.

**The position.** A claim's position is left by its **newest position-bearing
record, in the claim's own order**: a commit leaves itself; a `started` or a
`rewind` leaves its `resume_from`; a null `resume_from` leaves the empty
lineage. Commits advance it. `lifecycle.rewind` restates it only: a rewind
changes *where*, never *who* — the witness, its leases and its liveness survive
it.

**The head.** A claim's head is its exit if it has a readable one (§4.4), and
otherwise its position. The head of a commitless claim is its `resume_from`
node, which may be an older claim's node; after a trailing rewind it is the
rewind's target.

**The run's head** under `LatestClaimHead` is the newest claim's head: the order
among claims (fixed by the claim CAS: a claim's seq is its rank), then that
writer's own order. No clock is read, and an older claim's head is never chosen
over the newest claim's. With no claim, the head is the empty lineage: node
none, **not settled**.

**Positions are not compared** across writers (spec §2). The orders every rule
relies on are exactly:
- the order among claims, fixed by the claim CAS;
- each writer's own program order;
- "a record follows what it names", which covers a watermark (`consumed_seq`)
  and the request it passes;
- a record its writer CAS-appends at W follows every record at or below W, all
  of which that writer read;
- the substrate contract (§2.2).

So a displaced worker's late records, which name its own old claim by
`claim_seq`, can neither move the newest claim's position nor end it.

### 3.4 Sources, eager streams and the coordinate (spec §2)

- **A source** is a name the claim declares in `started.sources`, sampled on
  demand: the worker keeps a register, and a subscription samples it at the
  nodes where it fires.
- **An eager stream** is every other value name. It gets an element whenever
  the worker emits it.
- **Membership is decided by the envelope and the committing claim:** a
  committed value is an element of its name's stream iff the claim that
  committed it did not declare the name a source. A name may change kind
  between claims (code edited, then resumed). Membership reads the `value`
  envelope's topic and name, never its body, so a malformed value body raises
  when its value is read, never when it is counted.
- **A source's samples are keyed by their node,** (name, node), and read by
  node. They are never counted: their existence depends on who asked.
- **What names what:** a subscription targets a source, or nothing (pure
  demand); a progress atom, `prefix_len`, `ensure` and `steps()` name an eager
  stream or `Commits()`; `prefix`, `series`, `last_element` and `history` name an
  eager stream.

**The coordinate.** `count(X, node)` is the number of X's elements on the
lineage up to **and including** `node`, where X is an eager stream or
`Commits()`.
- **`Commits()` counts the `lifecycle.commit` nodes:** iterations, exactly. An
  exit is not a commit, so it adds no iteration, though its values still count
  as elements of their streams. **"Step" means this count everywhere,** never
  lineage depth, since a lineage may pass through an extendable exit.
- **Every progress number is a count.** Empty is 0, with no sentinel; a target
  `n` is reached iff the count is at least `n`; elements are indexed from 0. A
  name that was a source throughout counts 0, so a target naming a declared
  source can never be reached, and the reference refuses one up front (§4.1).
- **`Target(x, n)`** is "the first n of x", spelled on the wire `{stream: x, n}`
  or `{commits: n}` (spec §4). A target of 0 is reached at once.

There is no 0.3.0-style half-open fencepost: `{commits: N}` holds once N
iterations have committed.

### 3.5 `control.*` (subscription-v0.4)

The message kind **is the topic**. Every control record's `request_id` is
**required and non-null**; its `name` is present.

- **`control.subscribe`:** `request_id` is the subscription; `name` is the
  source it targets, or **null for pure demand**; the body is a schedule (§3.6).
- **`control.stop`:** `name` null; body `{from?}` — one-shot, at most a `from`
  (`every`/`until` are schema-rejected). With no `from` it holds once drained.
- **`control.withdraw`:** `name` null; body `{t}` with `t` a required number
  ≥ 0, the asker's clock; `request_id` names the request withdrawn, a stop or a
  subscription. It needs no worker: it spends the request by name (§3.11). The
  reference reads only its name, never its body, so a withdrawal spends what it
  names even when its body is malformed.

**Request ids** name one request each and are never reused: once any answer
names an id, the id is **spent** (§3.11), wherever that answer sits, and a later
record reusing it is dead on arrival. The drain's handling of each control
record, by its id (spec §4, "What a drain does"):
- **A spent id is skipped silently,** on every control topic.
- **A missing or empty id is nameless,** on every control topic and in every
  fold. The schema forbids it, so only a non-conforming writer produces one. A
  worker refuses it `malformed`, with a `nak` whose `request_id` is null, once:
  only above the newest watermark an earlier claim wrote (the nearest earlier
  claim's newest `consumed_seq`, or its exit's seq; a claim that died before its
  first watermark defers to the one before it).
- **A re-send of a live id** may replace its schedule, which only the worker
  reads, but not what readers read: its **target** (`name`), or its **class**
  (lease or durable). A re-send that changes either is refused `malformed`, which
  spends the id, since a `nak` names the id and so the live original too. A
  subscription's class and target are therefore **its first send's**.
- **An accepted re-send registers the request afresh** for what the claim
  holds: a lease's time atoms count from the re-send's drain in this claim, its
  fire count returns to 0, and its time baseline is dropped. That is how a lease
  is renewed. What the lineage holds stays: a lease's lineage baselines, and all
  of a durable subscription's firing state.
- **A stop's re-send** follows the same rule: an accepted re-send of a pending
  stop replaces its registration, its time `from` counted from the re-send; a
  re-send of a stop that has held is ignored, since the decision is a level; a
  refused re-send spends the id.

**Pure demand** (`name` null) has a lifetime, not a schedule: its body takes only
an `until`. A `from`, an `every` or a `{count: n}` atom in it is refused
`malformed` (the schema cannot see this; it is a `nak`). It never fires, so no
node's `fired` names it; it keeps a `serve()` worker alive until its `until`
holds at a node, which names it in `expired`, or until a withdrawal or refusal
names it; with a time atom in its `until` it is a lease, and void once its claim
has ended. With no `until` it lasts until withdrawn.

### 3.6 The condition algebra over counts (subscription-v0.4 `$defs`)

A schedule is `{from?, every?, until?}`: fire from `from` (absent: at once),
repeat every `every` (**absent: one-shot**), expire at `until` (absent: never).

```
Atom       := {"stream": S, "n": N} | {"commits": N} | {"time_seconds": T}
              # S a non-empty string; N an integer 0 .. 2^53-1; T a number >= 0
Condition  := Atom | {"any": [Condition, …>=1]} | {"all": [Condition, …>=1]}
UntilCond  := Atom | {"count": C>=0}
            | {"any": [UntilCond, …>=1]} | {"all": [UntilCond, …>=1]}
#   from, every : Condition       until : UntilCond      stop.from : Condition
```

- **`{stream, n}`** counts the elements of an eager stream on the lineage;
  **`{commits: n}`** counts iterations — the lineage's own clock, which replaces
  the removed `{step: N}`. Both are **lineage atoms**. `time_seconds` is wall
  time; `count` is a subscription's firings, grammatical only in `until`.
- **Every threshold takes n ≥ 0.** A 0 holds at once: in `from` and `until` it
  re-spells an absent `from` or an `until` that holds; in `every` it fires at
  every evaluation, the exit included.
- **`any` = OR, `all` = AND**, each over a non-empty list. Every condition is
  monotone. There is **no normal form**: conditions are never compared, hashed
  or canonicalized.
- **Lease or durable** (spec §3): a schedule with any `time_seconds` or `count`
  atom anywhere is a **lease**; every other schedule, whose atoms are all
  `{stream, n}` or `{commits: n}`, is **durable**. A stop has no class.

**Evaluation** (spec §3, "Evaluation"):
- **When:** at each `commit()`, and at an exit that did not discard its
  iteration; never at a `heartbeat()`, which evaluates only stops. Each node is
  evaluated once, `until` first, with no interpolation inside a jump.
- **Where:** counts are taken **at the node being written**, its pending values
  included. (At the previous node, `from {commits: 1}` would fire one commit
  late.)
- **`from` and `until`** hold once `count(X, node) ≥ n`.
- **`every` is the delta rule:** it fires when `count(now) − count(at the last
  firing) ≥ n`, not rounded to a multiple.
  - A **lineage atom's baseline** is its count at the newest node on the
    lineage whose `fired` names the subscription — for a lease as for a durable
    subscription. It is rebuilt at every position change, so after a rewind
    below the last firing, `every` counts from the newest firing left on the
    lineage.
  - A **time atom's baseline** is the wall clock at the lease's last firing,
    kept by the claim; a rewind does not move it.
  - **An atom with no baseline holds**, so the first firing is immediate.
- **Time atoms** count from the request's drain in the claim: a lease's from its
  drain (or its accepted re-send's) in this claim, a stop's from its drain in
  each claim, so a stop re-armed after a crash restarts its clock.
- **Expiry.** A subscription whose `until` holds at a node, or a one-shot that
  fires there, is named in that node's `expired` — firing and expiry share one
  record, so no crash falls between them. One whose `until` already holds when
  it registers **expires at the next node, firing nothing**. `until {count: n}`
  counts the firings up to and including the node, so it is named in the
  `expired` of the node of its nth firing. A withdrawn subscription is spent by
  the withdrawal, and no node names it.
- **A source fires once per node:** where one or more subscriptions fire for a
  source, the worker writes **one** sample of its register (a `value` with the
  source's `name` and a null `request_id`), before the node that commits it.
  The node's `values` list the pending values, then the samples; the reference
  orders the samples by each source's first firing in that evaluation, and
  stamps them with the node's own `t`.
- **A source with no value yet does not fire;** the subscription waits for the
  first `set`.

### 3.7 `lifecycle.nak` and the refusals

A refused control request; envelope `request_id` = the refused request (null for
a nameless one). Body `{reason, message}`, `reason ∈ {malformed, unsatisfiable,
unsupported}`, `message` free text. A nak **refuses one bad request without
crashing the worker**, and **spends the id it names**.

- **`malformed`** — the body does not parse as its grammar, or the request
  conflicts with the protocol in a way the schema cannot see: a nameless record;
  a pure demand with a `from`, an `every` or a `{count: n}` atom; a re-send that
  changes its target or class.
- **`unsatisfiable`** — well-formed, but refused against the declaration of the
  claim evaluating it (spec §3, "Refusals"):
  - a subscription targeting a name that claim does not declare a source
    (eager streams are read, not subscribed);
  - a progress atom (`{stream, n}`) naming a name that claim declares a source:
    its count would depend on who asked. The reference applies this one to a
    stop's `from` too;
  - an **empty window**: a `from` that implies the `until` (on one X, `until`'s
    n at most `from`'s). An absent `from` reads as `from` 0 on each X, so
    `{until: {commits: 0}}` is refused as `{from: {commits: 0}, until: {commits:
    0}}` is. The check does not depend on the position.

  The request id is spent, so an asker resubscribes under a fresh id if the kind
  changes back. 0.3.0's refusal of an `until` that already holds is gone: it now
  expires at the next node.
- **`unsupported`** — a `control.*` topic other than `subscribe`, `stop` and
  `withdraw`, which the schema forbids, so only a non-conforming writer
  produces one. The spec does not restate this 0.3.0 rule; the reference keeps
  it.

**What the refusals keep.** A registered source subscription can fire in
future, except one waiting on a source that is never `set`, and a `{stream}`
atom naming a stream that is never emitted (a misspelling). Either pins a
`serve()` worker; an asker bounds such a request with a time `until`, which
makes it a lease that dies with its claim.

### 3.8 The worker (spec §4)

If you build a worker that speaks the conventions, these steps are
**normative**: observers rely on their consequences.

**Construction** takes three required inputs: `resume_from` (a fixed node, the
latest claim's head, or explicitly none), `sources` (the names it exposes, `[]`
for none), and a checkpoint save policy (§3.14). `resume_from` may be resolved
from the latest claim's head: the head itself, or for a torn exit its `parent`,
or a fresh start on the empty lineage; a **closed head refuses**, since the run
is complete.

**The claim**, in order:
1. read W, and validate `resume_from` there, **walking the lineage that ends at
   its node** (an extendable node of this run, at or below W). That lineage is
   settled at W, so a malformed one refuses with nothing written;
2. if the latest claim is held, call the death-report probe (`report_if_dead`,
   §3.10): if it reports, start again; if not, **lose the claim**;
3. CAS `lifecycle.started` at W;
4. after winning, fold the answers by name up to that same W (a stop's
   `honored`, withdrawals and refusals) and the lease bindings, and rebuild
   position-dependent state at `resume_from`. The CAS proves W was the tail, so
   this fold is the one step 1 would have made, and a loser reads nothing.

The worker's own fold drops a subscription only when a withdrawal or a refusal
has spent it, or it is a void lease. Expiry it takes per lineage, from the
rebuild, so it re-arms a durable subscription another lineage expired. Its first
drain then reads every control record on the log, those at or below W included.

**A claim-race loser** writes nothing, ever. The spec says only "lose the
claim"; in the reference, the loser's `commit()` and `heartbeat()` return True,
its other calls do nothing, and `claimed` tells a lost claim from a commanded
stop, as in 0.3.0. A loser that wrote anything could write onto the winner's
live log.

**Values:**
- **`emit(name, value)`** sends a `value` record at once and adds its seq to the
  pending values. It refuses a declared source.
- **`set(name, value)`** updates a source's register; nothing is written until a
  subscription fires. It refuses an undeclared name.
- **`include(seq)`** adds a value sent through another handle to the next node.
  It refuses unless the record at `seq` is a `value` with a non-null name and a
  null `request_id`, `seq > claim_seq`, the seq is neither pending, nor
  committed on the current lineage, nor discarded by this claim at a rewind, and
  its name is not a declared source. Authorship stays unprovable: an honor
  system.

**`commit()`** ends an iteration. In order:
1. drain control, and the death reports naming its claim; a subscription
   registered in this drain may fire in this commit;
2. evaluate subscriptions at the node being written;
3. write one sample per source whose subscriptions fired;
4. write the `lifecycle.commit`: `parent` the position, `values` the pending
   values then the samples, `fired`, `expired`, `consumed_seq` the drain's
   watermark;
5. run the save policy (§3.14);
6. return the stop decision.

A `commit()` that raises before its node lands **tears the iteration**, as
`discard()` does: the write's outcome may be unknown, and a subscription's state
has already moved with its evaluation. Its exit is torn.

**A drain** reads the death reports up to its W **before** it handles any
control there, so a released worker handles nothing. Each record a drain
writes — a `lifecycle.bound` for a lease it registers, a `lifecycle.nak` for a
request it refuses — is **CAS-appended at the tail the drain last read**, its own
writes included, and the drain runs again on a lost CAS. So a death report that
lands first fences the worker before it binds or refuses anything; without the
CAS, a binding would void the lease with the released claim, and a refusal would
spend the id on the released claim's declaration.

**The stop decision.** A stop's `from` is evaluated at each node the claim
writes (counts at the node being written) and at each `heartbeat()` (counts at
the position, the heartbeat's clock). A stop with no `from` holds once drained.
Once a pending stop holds, the decision is **True for the rest of the claim**, a
rewind included: a monotone level. `commit()` and `heartbeat()` return it, and a
side-effect-free poll (`stop_pending`) reads it.

**`heartbeat()`** drains control and writes a `lifecycle.heartbeat`. It
evaluates no subscription, only the stops, and commits nothing: it is the
liveness record for inside a slow iteration, and splits no iteration.

**`rewind(target)`** writes `lifecycle.rewind {claim_seq, resume_from, t}`, its
target validated as `resume_from`'s is (`resume_from: null` is the root). Pending
values are discarded (they stay on the log outside every lineage). The stop
decision is unchanged.

**At every position change** (a claim or a rewind) the worker rebuilds from the
lineage at the new position: the per-stream counts and the `Commits()` count;
each durable subscription's fire count, `every` baseline and expiry, from
`fired` and `expired` (re-arming every durable subscription live on the new
lineage); each lease's lineage baselines, from `fired`; and it **resets the
source registers**, so a sample never carries a value computed on an abandoned
branch. A lease's clock, fire count and expiry are the claim's, and survive a
rewind.

**`discard()`** declares the open iteration torn. After it, `commit()` and
`heartbeat()` write nothing and return True (no drain either), `stop_pending` is
True, and `emit`, `set`, `include` and `rewind` raise. The exit after it writes
`values: null` and evaluates nothing. Call it from the worker's own flow, never
from a signal handler.

**The exit** — `stopped(completed, error)` — writes the exit node by the
**dying-breath CAS**. In order:
1. read W and drain up to it: stops, withdrawals, the death reports naming its
   claim. It **registers no subscription**, and passes over every named
   `control.subscribe` (a nameless one it refuses, as every drain does): one
   arriving after the last commit stays live for the next claim;
2. unless the exit discards its iteration (an error, or after `discard()`),
   evaluate the registered subscriptions once, at the exit node;
3. write the samples once. A failure while writing them makes the exit an error
   exit: torn, the failure its `error`, and the samples already written stay
   outside every lineage;
4. read W again, drain as in step 1, and CAS-append `stopped` at it, with
   `parent` the position, `values` (pending then samples, or null if discarded),
   `fired`, `expired` and `honored`. **On a lost CAS repeat this step only**: do
   not evaluate again, and honour a stop first drained here;
5. if the exit is extendable, run the save policy.

The exit's other rules:
- **`honored`** names every stop pending at the exit, **and every stop whose own
  condition held at a node or a heartbeat of the claim, even one withdrawn
  since**. A withdrawal after `commit()` or `heartbeat()` returned True is too
  late. `honored` is a **set**: its order means nothing.
- **There is at most one exit per claim** (the CAS), and it is always a node. It
  takes no node argument: a caller-supplied one would be a lineage lie.
- **`retire()`**, `serve()`'s careful death, drains everything at steps 1 and 4,
  subscriptions included, and writes no exit if any subscription is live for the
  claim after either read. Its exit fires nothing and is extendable.
- **An exit that starves** (its CAS lost past the bound) raises and leaves the
  claim held, for a death report to end. Only the exit may be retried; every
  other call is fenced.
- **After its own exit,** a worker writes nothing: `commit()` and `heartbeat()`
  return True, `stop_pending` is True, its other calls do nothing.

**A released worker fences itself** (spec §4). Once a drain reads a death report
naming its own claim, it latches: every method that writes writes nothing,
`commit()`, `heartbeat()` and `retire()` return True, and the exit, `emit`,
`set`, `include`, `rewind` and `discard()` do nothing. A mistaken release costs
at most the iteration in flight: a commit racing the report may land.

**Leases.** A lease is bound to its claim at drain by `lifecycle.bound`
`{claim_seq}` (envelope `request_id` = the lease), written by CAS before the
registration takes effect. It survives a rewind, and is void once its claim has
ended.

**The drivers:** `steps(until)` calls `commit()` once per iteration and yields
each iteration's index, `count(Commits(), position)` before its commit, so a
resumed driver continues run-absolute from the log; it ends when the target is
reached at the position, or on a stop. `serve()` calls `commit()` while some
subscription is live for it (a lease of its claim, or a durable subscription
live on its lineage) and retires at zero. A worker that calls `commit()` itself
from a host loop's callback is a first-class driver.

### 3.9 The exit's kinds, and what follows

- A **completed** exit is closed: neither resumed from nor rewound to. "Train
  more" resumes from an ancestor checkpoint, which starts a new branch.
- A **non-completed exit that is not torn** is extendable: the next claim may
  resume from it, and a chunk's post-loop values stay on the lineage the next
  chunk extends. This is what `ensure`'s default producer relies on: its worker
  exits preempted at the target.
- A **torn** exit (errored, or after `discard()`) is not extendable; resuming
  from the latest head after one continues the exit's `parent`.
- An exit that did not discard its iteration **commits post-loop values and
  fires on them**; one that discarded evaluates nothing (`values: null`,
  `fired: []`, `expired: []`), whether or not it completed.

### 3.10 Death reports (supervisor-v0.5)

`supervisor.*` is the **external-report plane**, renamed from `launcher.*`
(spec §3). Its writer outlives the worker and reports on it from outside.

- **`supervisor.launched`** `{handle, status: "running", t}` and
  **`supervisor.terminated`** `{reason, exit_code, signal, t}` name a **launch**:
  their envelope `request_id` is the launch id, required. `terminated` pairs its
  fields with `reason`: `exited` carries an integer `exit_code` ≥ 0 and a null
  `signal`; `killed` a `signal` ≥ 1 and a null `exit_code`. Bodies unchanged from
  `launcher-v0.4`.
- **`supervisor.died`** `{claim_seq, outcome, evidence, t}` — the **death
  report** — names a **claim**, by `claim_seq`; its envelope `request_id` is
  null. `outcome ∈ {preempted, errored, killed, lost}` (a death is never a
  completion); `lost` says its writer saw the claim end without an exit, and
  nothing of how. `evidence` is a non-empty string **no fold parses** — where a
  report says who wrote it. `t` is the reporter's clock when it saw the death.

**A claim has ended** once its exit or a death report naming it is on the log.
That one predicate, `ended`, serves every fold: the claim gate, lease voiding,
the verdict, demand and the Watcher. No fold probes a handle, and no fold
matches a `supervisor.terminated` to a claim. `terminated` ends nothing for a
claimed run.

**Who writes a death report** (spec §3). Any party, an observer included, may
write one for a death it **holds evidence of**:
- **Evidence, never inference.** A reap (an exit code or signal), a same-host
  probe that finds the claim's process gone, an accounting record (`slurm:
  TIMEOUT`), and a backend's session probe that finds ended the session that
  wrote the claim (§4.7) are evidence. Staleness, silence and a cross-host
  absence are not: a slow worker is alive, and a report would fence it.
- **Evidence without a cause reports only while the claim is held.** A probe
  that finds the process gone, an exit 0 and a thread that returned report
  `lost`, and only if nothing has ended the claim yet. A signal (`killed`), a
  nonzero exit code or a raised exception (`errored`) and an accounting record
  carry a cause, and report whether or not the claim has ended. A worker
  released by a mistaken report exits 0, and its reap adds nothing.
- **Reports are CAS-appended** at the W their writer read, and re-read on a lost
  CAS, so a `lost` report is checked against "held" at the W it lands at.
- **A claim already ended by a death report is reported again** by each holder of
  evidence; the verdict keeps them all (§4.4). A claim with a readable exit is
  not reported.
- **Evidence prefixes** (spec §3): `reap:` for a launcher's reap of a process
  or thread, `probe:` for a same-host probe, `session:` for the session probe,
  and `migrated:` for a report the 0.3.0 → 0.4.0 migration wrote (spec §7, E1).
  Other writers name their own (`slurm:`). The reference spells them `reap:
  signal 9`, `reap: exit 1`, `reap: exit 0`, `reap: returned`, `reap:
  <ExceptionType>: <message>`, `probe: local://host/pid` and `session:
  <server>/<key>`. No fold reads them.
- **A read never writes.** Every fold, the verdict and a handle's liveness probe
  stay pure; a party that wants a probe recorded calls the reporter. The one
  exception is a reap, which yields the exit status once: whoever reaps records
  it there.

The reference writers (spec §3, "The writers"): a launcher writes `died` beside
`terminated` when the launch has ended and the newest claim naming the launch
(by its `started.request_id`) has no readable exit — a subprocess launcher only
when that claim's handle is the launch's own, compared whole; a thread launcher
by the launch id alone. The library's actors call the probe (`report_if_dead`)
before they act on a held claim: the worker's claim path, the relaunch deciders,
and `ensure` on each poll. A launcher that holds an **orphan** — a child whose
claim a `died` names and no `stopped` names — kills it (`SIGKILL`) before
launching a successor, and that reap writes `terminated` only.

### 3.11 Requests: three scopes, and the answer fold by name (spec §2, §5)

Each kind of request keeps its own scope:

| request | scope | spent by | void |
|---|---|---|---|
| a stop | **by name**, for the whole log: a request about the process | an exit's `honored`, a `control.withdraw` or a `nak` naming it | never |
| a lease | **per claim**: bound by `lifecycle.bound`; its clock, fire count and expiry are the claim's | a `control.withdraw` or a `nak` naming it, or **any** node's `expired` naming it | once its claim has `ended` |
| a durable subscription | **per lineage**: its atoms are all lineage coordinates | a `control.withdraw` or a `nak` naming it (for the whole log), or an `expired` naming it **on the head's lineage** only | never |

The boundary: a request whose answers are lineage data is per lineage, and a
request about the process is by name. A stop honoured on one branch is not
re-armed on another; a durable subscription another lineage expired is live
again on a lineage that has not, served there by the worker and launched for by
a waker.

**The answer fold** (`live_demand`, `undischarged_stops`, and their incremental
forms; spec §5) reads those rules, plus:
- **a subscription's class and target are its first send's;**
- **a subscribe whose body does not parse is live until a `nak` names it,** as a
  malformed stop is pending until its `nak`: no fold judges a body the worker has
  not refused, and a waker must launch a worker to refuse it. It is never voided,
  since no claim binds it;
- **`ended` is presence-based and never raises:** the presence of a `stopped` or
  `died` naming the claim, readable or not. A malformed record in an old claim's
  window must not poison the present; the verdict is where a contradiction in a
  window raises;
- **nothing is demanded of a closed head:** live demand is empty while the
  latest claim's head is closed. A subscription posted to a completed run is
  never answered and never refused;
- a claim that dies before its first commit revives nothing.

The decision to launch a worker and the decision to keep serving read demand by
the same rules.

**`fired` and `expired` are answers too:** `answers(request_id)` is the sample
of its target at each node on the head's lineage whose `fired` names it, one per
firing; a durable subscription answered on an abandoned branch is answered again
on the new one, exactly once per lineage. `value` records never name a
subscription (`request_id` null): `fired` does.

### 3.12 Launch identity (supervisor-v0.5; `specs/launcher-record-identity.md`)

Both `supervisor.launched` and `supervisor.terminated` **must** carry the
envelope `request_id` — **one id per launch, minted by the launcher** — and the
worker **re-emits it on its `lifecycle.started`**. It reaches the worker
ambiently: `RUNSTATE_LAUNCH_ID` in the child's environment (the interop-relevant
half — a second-language launcher sets the same variable) or a ContextVar around
an in-process target. `null` iff nobody launched the worker.

The id is **correlation only** — it never scopes visibility. It attributes a
claim to its launch: a launcher's death report targets the newest claim naming
its launch, `ensure` attributes a drive's claim by it, and a Watcher holding a
launch's handle reads the verdict of the claims naming that launch (§4.4). A run
**nothing ever claimed** takes its launch's `terminated` as its verdict.

### 3.13 Liveness

**None of it is substrate-owned** — presence is emitted records. Best-to-worst:

1. **The worker's exit** — `lifecycle.stopped` naming the claim.
2. **A death report** — `supervisor.died` naming the claim, from evidence.
3. **Probe the handle** — resolve it (`kill -0`, `squeue -j`) for the *fact* of
   death — or the backend's **session probe** (§4.7). A reader returns a
   presumption; a party that wants it recorded writes a death report (§3.10).
4. **Staleness** — no record of the claim witnessed for longer than a
   threshold ⟹ presumed dead. **The universal floor**, a local inference only,
   never written down.

**What witnesses a claim** (spec §3, observer-clock §5): a new `started`, or a
new `commit`, `heartbeat` or `rewind` naming the latest claim, resets the
Watcher's **witnessed** clock — arrival, not the record's `t`. The Watcher seeds
the unwitnessed prefix from the newest of the claim's `started`, commits,
heartbeats and rewinds, so a cold attach to a long-dead run reads dead.

The **handle** is a portable, scheme-tagged token: `local://host/pid`,
`slurm://jobid`, `k8s://ns/pod`, `ray://actor`. `local://{hostname}/{pid}` is
parsed by splitting the text after `local://` on its **last** `/`. Resolution is
**hostname-scoped**: another host's handle is not locally resolvable (abstain),
never a verdict from the wrong pid table. pid reuse fools the probe while the
reusing process lives (spec §12; the `?start=T` disambiguator is deferred).

### 3.14 The checkpoint recipe (spec §6)

A recipe, not protocol; runstate supplies no directory. Its one wire-relevant
rule: **no lineage lie can be written** — a checkpoint's state is the state
committed by the node it names.
- **Save** only immediately after a commit lands, and after an **extendable**
  exit lands — never after a torn or closed one. The policy decides only
  whether, at those moments.
- **A save that raises propagates, and the node it followed stands.** After a
  commit, the raise ends the claim with an errored, torn exit whose `parent` is
  that node. After an extendable exit, nothing more is written: the exit's
  verdict stands. **Nothing records a failed save**: whether a node has a
  checkpoint is the store's fact, never the log's.
- **Load:** a checkpoint names its node; pass the checkpoint itself as
  `resume_from` or the rewind's target; after claiming, assert the loaded node
  equals the worker's resume node.
- **Choose** only complete checkpoints: those whose node is at or below the
  head's W.

---

## 4. The harvest — the reads, and what the schemas do not carry

### 4.1 The public raise-contract table

What the reference Python **raises**, when. A second implementation need not
raise the same *types*, but it must handle the same *conditions*.

| entry point | raises | when |
|---|---|---|
| `attach_channel` / `create_channel` | `ValueError` | `backend="sqlite"` or `"postgres"` with `root=None`; an unknown backend string |
| `attach_channel` / `create_channel` | `ImportError` | `backend="postgres"` without `psycopg` (the message names `pip install runstate[postgres]`) |
| `attach_channel` | `RunNotFound` | the run has no records — the non-mutating open's absence signal |
| `attach_channel` / `create_channel` | `LogFormatMismatch` | the root holds a newer format than this release's, or the run's log sits at an older format's address (run `runstate migrate`) — `specs/log-formats.md` §4 |
| `attach_channel` / `create_channel` | `LogFormatMissing` | the run's log sits at the legacy unversioned address |
| `create_channel` / `attach_channel` (Postgres) | `RuntimeError` | the format's `log` table is absent (`ensure_schema(dsn)` first); the server runs `fsync = off`; the server is older than PostgreSQL 16; a transaction-mode pooler is in the path; no fresh session key after 16 draws |
| every read over a lineage | `MalformedRecordError(seq, topic, detail)` | a structural or membership violation it walks or resolves (§4.3) |
| `AtNode(x).resolve` | `ValueError` | `x` is not a node, or is above the read's W |
| `Worker(...)` | `ValueError` | `resume_from` names no extendable node of this run at or below W; `LatestClaimHead()` resolves a closed head |
| `Worker(...)` | `TypeError` | `sources` is a string, not a sequence of names |
| `Worker(...)` | `MalformedRecordError` | the lineage `resume_from` names is malformed (nothing is written) |
| `Worker.emit` / `set` / `include` | `ValueError` | `emit` of a declared source; `set` of an undeclared name; `include` failing one of its checks (§3.8) |
| `Worker.emit` | `TypeError` | the value does not serialize |
| `Worker.rewind` | `ValueError` | the target is not an extendable node of this run |
| `Worker.emit` / `set` / `include` / `rewind` | `RuntimeError` | called after `discard()` |
| `Worker.steps(until)` | `ValueError` | the target names a declared source |
| `Worker(...)`, `commit`, `heartbeat`, `stopped` | `WorkerStarvedError` | a CAS loop (the claim, a drain's `bound` or `nak`, the exit) lost past the bound; after a starved exit the claim stays held |
| `Target(x, n)` | `ValueError` | `n < 0` |
| `Watcher.broadcast` | `MalformedSchedule` | the schedule does not parse (pure demand's rule included); nothing is written |
| `Watcher.broadcast` | `ValueError` | an empty `request_id` |
| `report_death` | `ValueError` | the record at `claim_seq` is not a `lifecycle.started`; `outcome` is not a death's (`completed`, `presumed_dead`); `evidence` is empty |
| `report_death` | `ClaimAliveError` | the claim's handle probes alive and `force` is not set |
| `report_death` | `ReportStarvedError` | its CAS lost past the bound |
| `ensure` | `ValueError` | the target names a source its drive's claim declares |
| `ensure` | `RunFailedError` | a drive's claim ended with any of its outcomes in `Outcome.failures()`; carries the `RunResult` |
| `ensure` | `NoProgressError` | a drive's claim ended having written no commit |
| `ensure` | `RecordlessExitError` | a drive's process died with no claim naming its launch, at the fixed point (a re-drive reproduces it), never while another claim is unended |
| `await_consumed` | `ValueError` | the request reuses a spent id; the record at `seq` is not a `control.subscribe` or `control.stop` (a `control.withdraw` needs no worker); its id is missing or empty; no record sits at `seq`; a re-send of a void lease id |
| `peek_terminal`, `live_episode` | `MalformedRecordError` | a record the verdict rests on is unreadable where no skip is defined: the latest claim's `started`, an unclaimed run's `terminated`, a contradiction in the latest claim's window (two readable exits) |
| `await_consumed` | `TimeoutError` | `timeout` elapsed |
| `Watcher.poll` | `KeyError` | the run was never tracked |

The exception types in the exported surface include `MalformedRecordError`,
`MalformedSchedule`, `ClaimAliveError`, `ReportStarvedError`,
`WorkerStarvedError`, `RunFailedError`, `NoProgressError`,
`RecordlessExitError`, `RunNotFound` and `LogFormatError` with its subclasses
`LogFormatMismatch` and `LogFormatMissing`.

### 4.2 The head, settledness and the reads (spec §5)

**Every read that depends on a branch takes a head Strategy**, chosen once at
the edge (required, keyword-only in the reference): `resolve(ch) -> Head(node,
settled, w)` reads `w = last_seq()` first and resolves the head at it.

| Strategy | head |
|---|---|
| `LatestClaimHead()` | the newest claim's head (§3.3); `node` none on the empty lineage, which is not settled |
| `AtNode(node)` | that node, for forensics or an abandoned branch; refuses a seq that is not a node or is above w |

A resolved `Head` is itself a Strategy resolving to itself, so a polled caller
resolves once per poll and passes it to every read in that poll.

**Settledness is a property of a node:** a read is settled iff its subject cannot
grow — always under `AtNode(x)`, and under `LatestClaimHead` iff the head is
closed. A displaced worker's completed exit does not settle the newest claim's
read. The (outcome, node) pair a read returns is permanent under either
Strategy; only `LatestClaimHead`'s *choice* of head is a report.

**The reads** (elements are `Element(node, seq, value, t)`):
- `prefix_len(x)` = `count(x, head)`, for an eager stream or `Commits()`.
- `prefix(stream, n)` → `Complete(elements, node)` (the first n), `Pending(partial,
  node)` (fewer, not settled) or `SettledShort(partial, node)` (fewer, settled:
  no more will come). At n = 0, `Complete((), node)`. Only `Complete` has
  `elements`, so a short read cannot pass for a complete one.
- `series(stream)`: one eager stream's elements in lineage order.
- `last_element(stream)`: the last in lineage order — not always the newest by
  seq, since `include` can commit an older value after a newer one.
- `aligned(names, axis)`: one row per node committing any of `names`; the row's x
  is `count(axis, node)`; cells are uniform tuples of elements (empty, one or
  several); sources' samples appear here, keyed by node.
- `answers(request_id)`: one sample per firing on the head's lineage.
- `history(stream, schedule)`: replays a schedule once per node, as live
  evaluation does, and returns the elements committed where it fires; time
  atoms read the node's `t`, with the epoch the earliest `started.t`.
- `streams()` → `Streams(eager, sources)`, by the committing claim's
  declaration; a name that changed kind is in both.
- `ensure(producer, target)` → `Reached(node)` or `Unreachable(closed_at)`; it
  has no head parameter (`LatestClaimHead` is implied). It reads first; returns
  `Unreachable` without producing when the head is closed below the target;
  otherwise it drives the producer and polls, testing the two outcomes before
  the failure exits (§4.1).

### 4.3 Well-formedness (spec §5)

A read raises `MalformedRecordError(seq, topic, detail)` for:
- **structural, on every node walked:** a `parent`, a `resume_from` or a
  `values` seq at or above its record's own seq; a `parent` or a `resume_from`
  naming a record that is not an extendable node;
- **structural, where a claim's records are held** (the worker at its own
  writes, and the head fold): a node whose `parent` is not its claim's position
  before it — which rules out a rollback written as a commit with no rewind;
- **membership, only for the records a read resolves:** a `values` seq naming a
  record that is not a `value`;
- **only in the pure walk:** a seq committed twice along one lineage (in a fold
  it would cost O(depth) per value).

A **malformed value body** raises when its value is read, never when it is
counted. **An incremental fold raises wherever its pure read raises**, except for
the duplicate check; membership is not exempt.

### 4.4 The verdict (spec §5, "The verdict")

`peek_terminal` reads **the latest claim's self-report if it is readable, else
the death reports naming that claim**. A run that nothing ever claimed takes its
launch's `supervisor.terminated`.

- **Readable** means the body parses as lifecycle-v0.6's `Stopped` or
  supervisor-v0.5's `Died`. An unreadable exit is no self-report, and counts as
  absent for the head. Either still **ends its claim when its `claim_seq`
  parses**, and counts as a `lost` among the death reports' outcomes; no fold
  reads more of its body. Its seq goes in `RunResult.malformed`. "Parses", in
  the reference, is read from the `claim_seq` key alone: an integer (not a
  boolean) ≥ 1, below the record's own seq, since a record follows what it names.
- **Several death reports are all kept.** The verdict's outcomes are the set of
  the reports' outcomes, **less `lost` when another is present**. Nothing is
  chosen among them, and the verdict does not depend on their order.
- **The `stopped` → outcome projection** tests `error is not None`, not
  truthiness: `error` non-null → `errored`; else `completed` → `completed`; else
  `preempted`.
- **The `terminated` → outcome projection** (an unclaimed run only):
  `reason == "killed"` → `killed`; else `exit_code == 0` → `completed`; else
  `errored`. The reference reads the newest `terminated` at or below W,
  whichever launch it names, and raises `MalformedRecordError` on an unreadable
  one, since it is the run's present verdict. An unclaimed run's `completed` is
  a fact about a process: `worker_completed` is False for it.

`RunResult`:

| field | meaning |
|---|---|
| `outcomes: frozenset[Outcome]` | never empty: one element under a self-report, a launch's `terminated` or an inference; the set above under death reports |
| `tier: Tier` | the viewpoint the verdict rests on (below) |
| `claim_seq: int \| None` | the claim the verdict is about |
| `node: int \| None` | the exit node's seq under a self-report, else none |
| `error: str \| None` | the exit's `error`; none under `died` |
| `reports: tuple[DeathReport, ...]` | the readable death reports naming the claim, in seq order, each `DeathReport(seq, outcome, evidence, t)`; empty under every other tier |
| `malformed: tuple[int, ...]` | the seqs of the unreadable records the verdict skipped |
| `run_id: str \| None` | the run, stamped by the reads that know it (the Watcher, `ensure`) |

`Outcome` is `completed | preempted | errored | killed | lost | presumed_dead`,
and `Outcome.failures() = {errored, killed, lost, presumed_dead}`. There is
deliberately **no `success` boolean**: a consumer's policy tests the set, as
`ensure` does with "any outcome in `failures()`".

`Tier`, a closed enum:

| tier | rests on | `claim_seq` |
|---|---|---|
| `exit` | the claim's own readable exit | required |
| `died` | the death reports and unreadable records that ended it | required |
| `terminated` | an unclaimed run's launch | none |
| `probed_dead` | the Watcher's handle probe (outcome `presumed_dead`) | either |
| `heartbeat_stale` | the Watcher's staleness (outcome `presumed_dead`) | either |
| `session_ended` | the Watcher's session probe (outcome `presumed_dead`) | required |

`probed_dead` and `heartbeat_stale` may concern a run nothing claimed yet: a
launch can stale or its handle probe dead before anything claims (the
never-beaconed startup death). **A Watcher that holds a launch's handle reads
that launch's verdict** — the claims naming its launch id, and while none has
landed, the run is running, subject to the probe and staleness tiers; otherwise
a relaunch of a run that already has an exit would read the previous claim's
verdict. A Watcher observing a run it holds no handle for reads the latest
claim's.

`worker_completed` is "the head is closed".

### 4.5 `await_consumed` — "did my request land?" (spec §5)

- a `Nak`, when a refusal names it;
- `None`, accepted, when a watermark of a claim reaches it (a commit's or a
  heartbeat's `consumed_seq` ≥ its seq), or an answer names it (a node's
  `fired` or `expired`, an exit's `honored`);
- the terminal `RunResult`, when the latest claim ended by its own exit above
  the request and no watermark of that claim reaches it: the exit was
  CAS-appended after its writer read the request, so the claim saw the request
  and ended without taking it;
- the terminal `RunResult`, when the head is closed;
- otherwise it waits for the next claim: a request above the ending exit, and a
  request above the last watermark of a claim that death reports ended (a death
  report shows that its writer read the request, not that the dead claim did).

### 4.6 Bounded reads, the incremental folds, and the clocks

**The criterion for a polled read:** its cost is independent of lineage length
and log age, given held state (spec §5).
- **Polled, through held state:** the head fold, `PrefixLen`, `LastElement`,
  `Answers`, `LiveDemand`, `PendingStops`, `peek_terminal`, `live_episode`,
  staleness, and the Watcher's held head and count.
- **One-shot:** the pure `prefix_len`, `last_element`, `answers`, `live_demand`,
  `series`, `prefix`, `aligned`, `history` and `streams`. The pure
  `live_demand` scans the whole log and never runs on a polled path.

The folds over a head are polled with `fold.poll(ch, head)`, at O(new records)
per poll; the folds by name, `LiveDemand` and `PendingStops`, read the whole log
and are polled with `fold.poll(ch)` (`LiveDemand` holds its own head fold, and
re-derives which expiries lie on the head's lineage when the head moves off its
last one, at O(L)). `Answers` re-derives on a head that does not descend from
its last one and returns the full list. **Their test: incremental equals pure
after every record** (`tests/test_order_independence.py` drives it over worker
histories).

**The pure walk** uses windowed bulk reads backward from the head, not point
reads (about 5 µs a row against 65 µs a node on Postgres).

**Fold an episode-scoped question over its window.** The claim is a Markov
boundary: an episode's liveness and verdict read only the records at or after
its `started`. Only a fold whose question is the whole log (a lineage or a
series replay, the answer fold by name) scans it, and never on a polled path.

**The three clocks** (design §11; `specs/observer-clock.md`):
- **`seq`** — the substrate transport order, the sole authoritative order.
- **`count(Commits(), node)`** — the worker's logical clock, replacing 0.3.0's
  `step`. It is a count read off the lineage, never a body field.
- **`t`** — the writer's wall clock, a body field. `seq` orders; `t` measures:
  `t` is never an ordering key or an arbiter, is authoritative within one
  writer's records and approximate across writers. A commit's `t` is at or after
  the `t` of every value it holds that its writer stamped. `t` is required where
  the writer has the event's time, and never fabricated: `Value.t` stays nullable.
  Staleness (`now() − t`) is a local inference, never a record-plane verdict.

`last_activity` dates a run by the newest `t` among the latest of its **dated
topics**: the worker's own `started`, `commit`, `heartbeat`, `rewind` and
`stopped`, and `supervisor.launched`. `supervisor.died` and
`supervisor.terminated` are **not** dated topics: each is dated when its writer
saw an end, which may be hours after the run last did anything.

### 4.7 The Postgres interop constants (must be bit-identical cross-implementation)

A second-language holder or observer **sharing a Postgres database** with the
reference must reproduce these exactly (`specs/channel-postgres.md`):

- **Schema-provisioning advisory lock key:** `0x72756E7374617465` — the 8 ASCII
  bytes of `"runstate"` as one positive `int8`. `ensure_schema` wraps the DDL in
  `pg_advisory_xact_lock(0x72756E7374617465)`.
- **The session key.** Every connection, at its open and **before its first
  write**, draws a **fresh random 64-bit key** and takes it with
  `pg_try_advisory_lock`, drawing again while another connection holds it (at
  most 16 draws, then a raise). The key is **never derived from the run or the
  claim**: every racing claimant shares those, and a derived key would turn the
  lock into a claim gate. Right after taking it, the open confirms in `pg_locks`
  that its own backend holds it; a transaction-mode pooler fails here.
- **The stamp.** Every row a connection writes — by the CAS and by the
  unconditional append — carries its writer's key in `writer_key bigint` and the
  server's identity in `writer_server text`, computed inside the statement that
  writes the row. A row a migration copied carries no stamp, and the probe
  abstains on it.
- **The server's identity** is the system identifier (`pg_control_system()`) and
  the timeline the WAL is being inserted on,
  `(pg_split_walfile_name(pg_walfile_name(pg_current_wal_lsn()))).timeline_id`
  — never the checkpoint's timeline, which a promotion leaves stale for minutes.
  PostgreSQL 16 is the floor, for `pg_split_walfile_name`.
- **The probe** reads the key on the claim's row (`writer_session(seq)`) and asks
  `pg_locks`, read-only, whether the key is still held in this database
  (`session_ended(session)`), abstaining in recovery or when the current server's
  identity differs from the stamp's. Its evidence: **every record naming a claim
  travels on the claim's session, never re-established** — a channel never
  reconnects — so once that session has ended, nothing can commit a record in
  the claim's name. It outranks a live pid on the same host.
- **A durable server.** The channel sets `synchronous_commit = on` on its own
  connection and refuses a server with `fsync = off`: under either, a crash
  loses rows readers already saw and reuses their seqs.

0.3.0's episode-keyed lock (`hashtextextended` of `len:run_id:started_seq`, taken
after the claim CAS) and its birth grace are gone in 0.4.0.

---

## 5. Interop with the Python reference on shared storage

Two implementations interoperate by sharing a backend's storage and agreeing on
its layout. Everything here is the storage contract, not an API.

### 5.1 The `RUNSTATE_*` environment variables

A launcher sets these in the worker's environment; a second-language launcher
sets the **same** variables.

| variable | meaning | default |
|---|---|---|
| `RUNSTATE_RUN_ID` | the run to attach to | **required** (else `KeyError`) |
| `RUNSTATE_CHANNEL_ROOT` | the directory (sqlite) / DSN (postgres) / namespace (memory) | `None` |
| `RUNSTATE_CHANNEL_BACKEND` | `memory` / `sqlite` / `postgres` | `sqlite` |
| `RUNSTATE_LAUNCH_ID` | the launch's correlation id, re-emitted on `started` (§3.12) | `None` |
| `RUNSTATE_SQLITE_JOURNAL_MODE` | sqlite journal mode | `WAL` |

`RUNSTATE_SQLITE_JOURNAL_MODE` accepts exactly `{WAL, DELETE, TRUNCATE,
PERSIST}`. **The NFS caveat:** WAL needs a memory-mapped `-shm` sidecar a
network filesystem cannot back coherently, so an NFS-homed deployment exports
`RUNSTATE_SQLITE_JOURNAL_MODE=DELETE`. DELETE removes the hang, not the
cross-host CAS correctness: **single-writer-per-run is required on NFS**.

### 5.2 The SQLite layout

**One file per run, under its format's directory:**
**`<root>/v0.4.0/<run_id>.db`** (`specs/log-formats.md` §3). The opening checks
of `specs/log-formats.md` §4 run before any read or write, and an implementation
that interoperates must compute the address and raise on the same five cases.
The 0.4.0 `log` table is 0.3.0's, unchanged:

```sql
CREATE TABLE IF NOT EXISTS log (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    topic      TEXT NOT NULL,
    name       TEXT,
    request_id TEXT,
    body       TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_log_topic_seq ON log (topic, seq);
CREATE INDEX IF NOT EXISTS idx_log_topic_name_seq ON log (topic, name, seq);
```

- `seq` is the SQLite autoincrement (the single sequencer); `created_at` is
  advisory — order is `seq`.
- `body` is stored as **opaque JSON text**, never interpreted.
- The connection runs in **autocommit** with `PRAGMA busy_timeout=5000`; the
  journal mode is (re)applied on every connection at open.
- SQLite has no session probe: a same-host crash nobody reports reads as held
  until a reap, a probe or another party reports it (spec §12).

### 5.3 The Postgres layout (`specs/channel-postgres.md`)

**One shared `log` table for all runs, in its format's schema** —
`runstate_v0_4_0` (`specs/log-formats.md` §3). DDL, run with that schema as the
`search_path`:

```sql
CREATE TABLE IF NOT EXISTS log (
    run_id        text   NOT NULL,
    seq           bigint NOT NULL,
    topic         text   NOT NULL,
    name          text,
    request_id    text,
    body          text   NOT NULL,
    created_at    double precision NOT NULL,
    writer_key    bigint,
    writer_server text,
    PRIMARY KEY (run_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_log_run_topic_seq ON log (run_id, topic, seq);
CREATE INDEX IF NOT EXISTS idx_log_run_topic_name_seq ON log (run_id, topic, name, seq);
```

- **`PRIMARY KEY (run_id, seq)` is the CAS arbiter.** The CAS is a guarded
  `INSERT … SELECT … WHERE (SELECT COALESCE(MAX(seq),0) FROM log WHERE
  run_id=%s) = %(expected)s`: rowcount 1 → won; rowcount 0 (gate false) **or**
  `UniqueViolation` → `None`; a connection drop or lock timeout → **raise**.
- **`body` stays `text`.** `jsonb` would reorder keys and canonicalize numbers —
  mutating the opaque body.
- **`writer_key` and `writer_server`** are the session stamp (§4.7): nullable,
  written by the backend beside the record, never a wire field.
- **DDL is out of the hot path.** `CREATE TABLE IF NOT EXISTS` is not
  concurrency-safe in Postgres, so DDL lives in a one-time `ensure_schema(dsn)`
  under `pg_advisory_xact_lock(0x72756E7374617465)`; the channel only probes
  `to_regclass('log')` and raises if absent.
- **One dedicated connection per handle,** in autocommit, serialized by an
  internal lock; **no transaction-mode pooler** (the key, the search path and
  the stamp are session state). A channel **never reconnects**: a dropped
  connection makes its next `send` raise.

### 5.4 The memory backend

In-process only (`in_process` tier). `create_channel(backend="memory")` shares a
log across handles in one process via a registry keyed by `(root, run_id)`. It
reads a tail as a slice and keeps a per-topic index for `latest`. Nothing to
interop with across implementations.

---

## 6. What to test — the conformance suite as a checklist

A new implementation reproduces the shape of the Python suite ([`../tests/`](../tests/)).

- **Backend conformance, parametrized over every backend**
  (`tests/test_channel.py`, `[memory, sqlite, sqlite:delete, postgres]`):
  append/read/latest/last_seq; contiguous 1-based `seq`; the
  value-snapshot-at-`send`; the topic-pattern grammar; the `request_ids` filter;
  the CAS trichotomy.
- **The tier-gated concurrency suite** (`tests/test_concurrency.py`): each test
  marks `@pytest.mark.tier(...)` (`in_process` < `cross_process` <
  `cross_host`), and a backend below the tier skips. The substrate contract is
  here (`test_interop_substrate_contract_across_threads` / `_across_processes`).
- **Schema conformance** (`tests/test_schema.py`): a scenario emits every
  reserved topic — `commit`, `rewind`, `withdraw`, `died`, and `stopped`
  completed, preempted, torn and errored — and validates each envelope against
  the envelope schema and its convention schema, asserting the scenario covered
  the whole vocabulary. Negative cases include: `error` with non-null `values`,
  a torn exit that fires, a `died` with an outcome of `completed`, a `value` with
  a `step`, `{commits: -1}`. §8 embeds a wire-example set your test can validate
  the same way.
- **The interop items of spec §9**, one test each (`tests/test_interop.py`, and
  the substrate contract above). Another implementation must match every one:

| spec §9 item | reference test |
|---|---|
| the node rule: `parent` and `resume_from` name an extendable node, `parent` is the claim's position; a node follows what it names; order within a node; a seq at most once along a lineage | `test_interop_node_rule` |
| closed, torn and extendable from `completed` and `values`; an error exit is torn; `values: null` evaluates nothing | `test_interop_closed_torn_extendable` |
| membership by the committing claim's declared sources, decided by the envelope | `test_interop_membership_by_declaration` |
| the head rule, and settledness as a property of a node | `test_interop_head_and_settledness` |
| the substrate contract, and well-formedness as completeness | `test_interop_substrate_contract_*`, `test_interop_well_formedness_is_completeness` |
| `fired` and `expired` on nodes; one sample per fired source per node | `test_interop_fired_expired_and_one_sample` |
| leases per claim (lineage baselines on the lineage), durable subscriptions per lineage, demand by name, empty on a closed head | `test_interop_leases_durables_and_demand` |
| a stop's `from` at each node and heartbeat, latched; `honored` naming every stop that held, even one withdrawn since | `test_interop_stop_from_latch_and_honored` |
| the atoms: counts at the node being written, the delta rule, `{commits: n}`, an `until` holding at registration expiring at the next node | `test_interop_atoms` |
| the three `unsatisfiable` refusals; the `malformed` re-send and pure-demand refusals | `test_interop_refusals` |
| a pure demand's lifetime | `test_interop_pure_demand_lifetime` |
| `ended` as one predicate; the verdict by viewpoint; outcomes under death reports | `test_interop_ended_and_the_verdict_by_viewpoint` |
| the answer fold, by name: `honored`, `expired`, `withdraw`, `nak` | `test_interop_answer_fold_by_name` |
| a released worker writes nothing more; a drain's `bound` or `nak` only by CAS at its last-read tail | `test_interop_fence_and_cas_drain` |
| a death report from evidence, never inference; `lost` only while held | `test_interop_death_report_from_evidence` |

- **Order independence** (`tests/test_order_independence.py`): the named reads
  are invariant under every causal reordering of a worker-written history —
  every reordering that keeps each writer's order, the order among claims, the
  CAS-at-W order and "a record follows what it names", with every named seq
  renamed (`claim_seq`, `consumed_seq`, `parent`, `values`, `resume_from`).
  **Each incremental fold equals its pure read after every record.**

---

## 7. The why layer — re-deriving the conventions from three decision rules

An implementer who internalizes the constructions behind the layers
([`backlog/protocol-algebra.md`](backlog/protocol-algebra.md)) can **re-derive**
most of the drain semantics rather than memorizing them.

- **L1 — the log is the free monoid; initiality decides the surface.** Every
  "stateful communication shape" (register, queue, mailbox, counter, lineage)
  is a **unique fold** of the log — which is why they are *queries*, not
  primitives. `send` / `read`(+cursor) / `latest` / `last_seq` / CAS is
  **complete**. (Full retention is what makes the log initial; GC or compaction
  *quotients* it.)
- **L2 — conventions are a designated-elimination discipline.** Every
  control-plane fact is an **intro/elim pair**, and **what consumes a fact is
  fixed by the convention**. The folds compute **Γ, the multiset of
  introduced-but-not-yet-consumed facts**. Every eliminator **names** what it
  consumes, so Γ is the same whatever order records arrive in.

  | intro | designated eliminator | multiplicity |
  |---|---|---|
  | `lifecycle.started` | the `lifecycle.stopped` or a `supervisor.died` naming it (`claim_seq`): `ended` | obligation (the worker's exit, or a death report from evidence) |
  | `supervisor.launched` | `supervisor.terminated` (by launch id) | obligation (launcher viewpoint) |
  | `control.subscribe` | a `control.withdraw` or `nak` naming its id; an `expired` naming it — any node's for a lease, the head's lineage's for a durable one | **affine** — may never be consumed; a lease is also void once its claim has `ended` |
  | `control.stop` | an exit's `honored`, a `control.withdraw` or a `nak` naming it | **linear** — consumed exactly once |

  Pure `value` events are the **no-obligation** case. Two eliminators are new in
  0.4.0, each answering a 0.3.0 forgery: `control.withdraw` (an asker clearing
  its own stop no longer forges a `stopped`), and `supervisor.died` (a third
  party ending a claim no longer forges the dead worker's exit). Expiry moved
  onto the node (`expired`) so that firing and expiry are one record.
- **L3 — fold observers separately; join only at the verdict.** `lifecycle.*`
  (the worker's self-report) and `supervisor.*` (the external report) are
  **independent partial observers**; `RunResult.outcomes` and its `tier` are the
  canonical projection of their join. **Never merge observers in the data**: a
  third party writes a `died`, never a `stopped`.

**Placement note:** the reader-facing seed of these rules lives here; the
formal treatment's final home remains open in `backlog/protocol-algebra.md`.

---

## 8. Validated wire examples

Canonical envelope records, **validated by the drift-guard test**
[`../tests/test_implementers_guide.py`](../tests/test_implementers_guide.py)
against the 0.4.0 stack, so they can never drift from the wire format.
**Convention used by the test:**

- ` ```json ` fenced blocks are **valid**, complete envelope records — the test
  validates each against the **envelope schema + the convention schema for its
  topic**; all must pass, and together they must cover **every** reserved topic.
- ` ```jsonc ` fenced blocks (in §8.2) are **deliberately invalid** envelope
  records — the test asserts each is **rejected** by the envelope schema or its
  convention schema. (They contain no actual comments; the different fence label
  is the only signal.)
- No other JSON in this guide uses these two fences.

### 8.1 Valid records (one or more per reserved topic)

Together these records read as one run's log, seq 1 to 22, and agree with
every rule of §3. The alternative endings in the last group each stand alone at
seq 21, on that log without its seq 21 and 22.

The launch, and the launcher's spawn record (its `request_id` is the launch id):

```json
{"seq": 1, "topic": "supervisor.launched", "name": null, "request_id": "launch-abc",
 "body": {"handle": "local://host42/12345", "status": "running", "t": 1721145599.0}}
```

A stop, conditional on 500 iterations (`from` only; `request_id` required: the
exit that honours it, a withdrawal or a nak names it):

```json
{"seq": 2, "topic": "control.stop", "name": null, "request_id": "stop-1",
 "body": {"from": {"commits": 500}}}
```

A subscription to the source `grad_norm` with a compound schedule: fire from
100 iterations; then every 10 iterations or 60 s, whichever first; until both
5000 elements of `loss` and 100 firings. Its `time_seconds` and `count` atoms
make it a **lease**:

```json
{"seq": 3, "topic": "control.subscribe", "name": "grad_norm", "request_id": "sub-1",
 "body": {"from": {"commits": 100},
          "every": {"any": [{"commits": 10}, {"time_seconds": 60}]},
          "until": {"all": [{"stream": "loss", "n": 5000}, {"count": 100}]}}}
```

A **durable** subscription (lineage atoms only), sampling `grad_norm` at every
new element of the eager stream `loss`, from the first node:

```json
{"seq": 4, "topic": "control.subscribe", "name": "grad_norm", "request_id": "sub-2",
 "body": {"every": {"stream": "loss", "n": 1}}}
```

A **pure demand** (`name` null): it never fires, and keeps a `serve()` worker
alive until withdrawn, since it has no `until`. Its body may take only an
`until`:

```json
{"seq": 5, "topic": "control.subscribe", "name": null, "request_id": "demand-1",
 "body": {}}
```

A subscription to `loss`, which the claim below does not declare a source, so
its first drain refuses it (seq 10):

```json
{"seq": 6, "topic": "control.subscribe", "name": "loss", "request_id": "sub-bad",
 "body": {}}
```

The claim, CAS-appended at W = 6: a fresh start (`resume_from: null`) declaring
one source, and re-emitting the launch id on `request_id`:

```json
{"seq": 7, "topic": "lifecycle.started", "name": null, "request_id": "launch-abc",
 "body": {"handle": "local://host42/12345", "t": 1721145600.0,
          "resume_from": null, "sources": ["grad_norm"]}}
```

The first iteration emits a `loss` (an element of an eager stream) and `set`s
the `grad_norm` register, which writes nothing:

```json
{"seq": 8, "topic": "value", "name": "loss", "request_id": null,
 "body": {"value": 0.92, "t": 1721145601.0}}
```

Its `commit()` drains seq 2–6. The binding: `sub-1` is a lease, so before
registering it the drain binds it to its own claim, by CAS at the tail it read
(`request_id` required: the lease it binds):

```json
{"seq": 9, "topic": "lifecycle.bound", "name": null, "request_id": "sub-1",
 "body": {"claim_seq": 7}}
```

The refusal of `sub-bad`, also by CAS (closed reason enum; undated; it spends
the id):

```json
{"seq": 10, "topic": "lifecycle.nak", "name": null, "request_id": "sub-bad",
 "body": {"reason": "unsatisfiable", "message": "loss is not a declared source"}}
```

`sub-2` fires at once (an atom with no baseline holds); `sub-1` does not, since
its `from` of 100 commits does not hold. So the commit writes one sample of
`grad_norm` (`request_id` null, since a value never names a subscription), and
then the node naming both values, in the writer's order. Its `parent` is null,
the position a fresh claim leaves, and its `consumed_seq` the newest control
record its drains handled:

```json
{"seq": 11, "topic": "value", "name": "grad_norm", "request_id": null,
 "body": {"value": 3.2, "t": 1721145601.2}}
```

```json
{"seq": 12, "topic": "lifecycle.commit", "name": null, "request_id": null,
 "body": {"claim_seq": 7, "consumed_seq": 6, "parent": null, "values": [8, 11],
          "fired": ["sub-2"], "expired": [], "t": 1721145601.2}}
```

A heartbeat inside the next, slow iteration (liveness only, not a node):

```json
{"seq": 13, "topic": "lifecycle.heartbeat", "name": null, "request_id": null,
 "body": {"claim_seq": 7, "consumed_seq": 6, "t": 1721145630.0}}
```

The second iteration commits on the first (`parent` 12). `loss` counts 2 there
and 1 at `sub-2`'s last firing, so `sub-2` fires again:

```json
{"seq": 14, "topic": "value", "name": "loss", "request_id": null,
 "body": {"value": 0.88, "t": 1721145660.0}}
```

```json
{"seq": 15, "topic": "value", "name": "grad_norm", "request_id": null,
 "body": {"value": 2.9, "t": 1721145660.2}}
```

```json
{"seq": 16, "topic": "lifecycle.commit", "name": null, "request_id": null,
 "body": {"claim_seq": 7, "consumed_seq": 6, "parent": 12, "values": [14, 15],
          "fired": ["sub-2"], "expired": [], "t": 1721145660.2}}
```

A rewind to the first node: the position is 12 again, and the register is
reset. The second iteration's node stays on the log, on an abandoned branch:

```json
{"seq": 17, "topic": "lifecycle.rewind", "name": null, "request_id": null,
 "body": {"claim_seq": 7, "resume_from": 12, "t": 1721145661.0}}
```

The asker withdraws its pure demand (`request_id` names the request withdrawn;
`t` is the asker's clock):

```json
{"seq": 18, "topic": "control.withdraw", "name": null, "request_id": "demand-1",
 "body": {"t": 1721145662.0}}
```

A `loss` emitted after the rewind, a new `set`, and the **preempted, extendable
exit** the worker writes as it leaves its loop: a non-completed exit with
non-null `values`, whose `parent` is the position at the exit (12, the rewind's
target). On this lineage `loss` counts 2 at the exit node, its pending value
included, against 1 at `sub-2`'s newest firing left on the lineage (node 12), so
`sub-2` fires at the exit. The exit honours `stop-1`, which was pending at the
exit though its `from` never held: an exit names every pending stop:

```json
{"seq": 19, "topic": "value", "name": "loss", "request_id": null,
 "body": {"value": 0.87, "t": 1721145700.0}}
```

```json
{"seq": 20, "topic": "value", "name": "grad_norm", "request_id": null,
 "body": {"value": 2.8, "t": 1721145700.2}}
```

```json
{"seq": 21, "topic": "lifecycle.stopped", "name": null, "request_id": null,
 "body": {"completed": false, "error": null, "claim_seq": 7, "honored": ["stop-1"],
          "parent": 12, "values": [19, 20], "fired": ["sub-2"], "expired": [],
          "t": 1721145700.2}}
```

The process's end, reaped by its launcher: a `terminated` naming the launch.
The claim has a readable exit, so no death report follows, and `terminated`
enters no verdict of a claimed run:

```json
{"seq": 22, "topic": "supervisor.terminated", "name": null, "request_id": "launch-abc",
 "body": {"reason": "exited", "exit_code": 0, "signal": null, "t": 1721145701.0}}
```

**Alternative endings**, each at seq 21 on the log above without its seq 21 and
22.

A **completed** exit (a closed node; `honored` is a set):

```json
{"seq": 21, "topic": "lifecycle.stopped", "name": null, "request_id": null,
 "body": {"completed": true, "error": null, "claim_seq": 7, "honored": ["stop-1"],
          "parent": 12, "values": [19, 20], "fired": ["sub-2"], "expired": [],
          "t": 1721145700.2}}
```

An **errored** exit: torn, so `values` is null and it evaluates nothing; the
pending `loss` at seq 19 and the sample at seq 20 stay outside every lineage:

```json
{"seq": 21, "topic": "lifecycle.stopped", "name": null, "request_id": null,
 "body": {"completed": false, "error": "CUDA OOM", "claim_seq": 7, "honored": ["stop-1"],
          "parent": 12, "values": null, "fired": [], "expired": [],
          "t": 1721145710.0}}
```

A **preempted, torn** exit after `discard()`: no error, `values: null`:

```json
{"seq": 21, "topic": "lifecycle.stopped", "name": null, "request_id": null,
 "body": {"completed": false, "error": null, "claim_seq": 7, "honored": ["stop-1"],
          "parent": 12, "values": null, "fired": [], "expired": [],
          "t": 1721145710.0}}
```

A **death report**: the worker was killed with no exit, and the launcher's reap
names the claim, with its evidence. It ends the claim and voids `sub-1`, the
lease bound to it; `stop-1` stays pending for the next claim:

```json
{"seq": 21, "topic": "supervisor.died", "name": null, "request_id": null,
 "body": {"claim_seq": 7, "outcome": "killed", "evidence": "reap: signal 9",
          "t": 1721145720.0}}
```

### 8.2 Invalid records (deliberately rejected)

Each violates one pinned constraint; the reason is stated, and the test asserts
rejection.

An **extra top-level envelope field** — rejected by `envelope-v0.2`
(`additionalProperties: false`; authorship is an honor system, with no field):

```jsonc
{"seq": 1, "topic": "value", "name": "loss", "request_id": null,
 "body": {"value": 0.5, "t": 1.0}, "author": "me"}
```

A **`value` with a `step`** — rejected by `value-v0.3` (`step` is removed: the
logical clock is `count(Commits(), node)`, read off the lineage):

```jsonc
{"seq": 2, "topic": "value", "name": "loss", "request_id": null,
 "body": {"value": 0.5, "step": 1, "t": 1.0}}
```

A **`value` naming a subscription** — rejected by `value-v0.3` (a value's
`request_id` is null; a node's `fired` names the subscription):

```jsonc
{"seq": 3, "topic": "value", "name": "grad_norm", "request_id": "sub-1",
 "body": {"value": 3.2, "t": 1.0}}
```

A **`value` with no name** — rejected by `value-v0.3` (the envelope `name` is the
stream or source, required non-null):

```jsonc
{"seq": 4, "topic": "value", "name": null, "request_id": null,
 "body": {"value": 3.2, "t": 1.0}}
```

An **`error` with non-null `values`** — rejected by `lifecycle-v0.6` (`error`
non-null ⟹ `values` null: an error exit is torn):

```jsonc
{"seq": 5, "topic": "lifecycle.stopped", "name": null, "request_id": null,
 "body": {"completed": false, "error": "boom", "claim_seq": 1, "honored": [],
          "parent": null, "values": [2], "fired": [], "expired": [], "t": 1.0}}
```

A **torn exit that fires** — rejected by `lifecycle-v0.6` (`values` null ⟹
`fired` and `expired` empty: an exit that discarded its iteration evaluates
nothing):

```jsonc
{"seq": 6, "topic": "lifecycle.stopped", "name": null, "request_id": null,
 "body": {"completed": false, "error": null, "claim_seq": 1, "honored": [],
          "parent": null, "values": null, "fired": ["sub-1"], "expired": [],
          "t": 1.0}}
```

A **completed exit carrying an `error`** — rejected by `lifecycle-v0.6`
(`completed` ⟹ `error` null):

```jsonc
{"seq": 7, "topic": "lifecycle.stopped", "name": null, "request_id": null,
 "body": {"completed": true, "error": "boom", "claim_seq": 1, "honored": [],
          "parent": null, "values": null, "fired": [], "expired": [], "t": 1.0}}
```

A **`stopped` that names no claim** — rejected by `lifecycle-v0.6` (`claim_seq`
is no longer nullable: `stopped` is the worker's alone, and a third party writes
a `died`):

```jsonc
{"seq": 8, "topic": "lifecycle.stopped", "name": null, "request_id": null,
 "body": {"completed": false, "error": null, "claim_seq": null, "honored": [],
          "parent": null, "values": [], "fired": [], "expired": [], "t": 1.0}}
```

A **`stopped` honouring an empty id** — rejected by `lifecycle-v0.6` (`honored`
items are non-empty, since no request id is empty):

```jsonc
{"seq": 9, "topic": "lifecycle.stopped", "name": null, "request_id": null,
 "body": {"completed": false, "error": null, "claim_seq": 1, "honored": [""],
          "parent": null, "values": [], "fired": [], "expired": [], "t": 1.0}}
```

A **0.3.0 `stopped`**, with `final_step` and without the node fields — rejected by
`lifecycle-v0.6`:

```jsonc
{"seq": 10, "topic": "lifecycle.stopped", "name": null, "request_id": null,
 "body": {"completed": true, "error": null, "final_step": 999, "claim_seq": 1,
          "honored": [], "t": 1.0}}
```

A **heartbeat with a `step`** — rejected by `lifecycle-v0.6` (removed: a
heartbeat is liveness only):

```jsonc
{"seq": 11, "topic": "lifecycle.heartbeat", "name": null, "request_id": null,
 "body": {"step": 1, "consumed_seq": 0, "claim_seq": 1, "t": 1.0}}
```

A **claim without `sources`** — rejected by `lifecycle-v0.6` (present-nullable:
`sources: []` declares none, and `resume_from: null` is a fresh start; neither
is omitted):

```jsonc
{"seq": 12, "topic": "lifecycle.started", "name": null, "request_id": null,
 "body": {"handle": "local://host42/1", "t": 1.0, "resume_from": null}}
```

A **commit carrying a `request_id`** — rejected by `lifecycle-v0.6` (null on
`commit`, `heartbeat`, `rewind` and `stopped`):

```jsonc
{"seq": 13, "topic": "lifecycle.commit", "name": null, "request_id": "sub-1",
 "body": {"claim_seq": 1, "consumed_seq": 0, "parent": null, "values": [],
          "fired": [], "expired": [], "t": 1.0}}
```

A **commit naming a value twice** — rejected by `lifecycle-v0.6` (`values` items
are unique):

```jsonc
{"seq": 14, "topic": "lifecycle.commit", "name": null, "request_id": null,
 "body": {"claim_seq": 1, "consumed_seq": 0, "parent": null, "values": [2, 2],
          "fired": [], "expired": [], "t": 1.0}}
```

A **death report with an outcome of `completed`** — rejected by `supervisor-v0.5`
(a death is never a completion):

```jsonc
{"seq": 15, "topic": "supervisor.died", "name": null, "request_id": null,
 "body": {"claim_seq": 1, "outcome": "completed", "evidence": "reap: exit 0",
          "t": 1.0}}
```

A **death report naming a launch** — rejected by `supervisor-v0.5` (`died` names
a claim by `claim_seq`; its `request_id` is null):

```jsonc
{"seq": 16, "topic": "supervisor.died", "name": null, "request_id": "launch-abc",
 "body": {"claim_seq": 1, "outcome": "lost", "evidence": "probe: local://host42/1",
          "t": 1.0}}
```

A **death report with empty evidence** — rejected by `supervisor-v0.5`
(`evidence` is a non-empty string):

```jsonc
{"seq": 17, "topic": "supervisor.died", "name": null, "request_id": null,
 "body": {"claim_seq": 1, "outcome": "lost", "evidence": "", "t": 1.0}}
```

A **`terminated` with `request_id: null`** — rejected by `supervisor-v0.5` (the
launch's correlation id is required on `launched` and `terminated`):

```jsonc
{"seq": 18, "topic": "supervisor.terminated", "name": null, "request_id": null,
 "body": {"reason": "exited", "exit_code": 0, "signal": null, "t": 1.0}}
```

A **negative threshold**, `{commits: -1}` — rejected by `subscription-v0.4`
(every threshold is from 0 to 2⁵³−1):

```jsonc
{"seq": 19, "topic": "control.subscribe", "name": "grad_norm", "request_id": "r",
 "body": {"until": {"commits": -1}}}
```

A **step atom** — rejected by `subscription-v0.4` (`{step: N}` is removed;
`{commits: n}` takes its place):

```jsonc
{"seq": 20, "topic": "control.stop", "name": null, "request_id": "r",
 "body": {"from": {"step": 500}}}
```

A **`count` atom outside `until`** — rejected by `subscription-v0.4` (`count` is
grammatical only under `until`):

```jsonc
{"seq": 21, "topic": "control.subscribe", "name": "grad_norm", "request_id": "r",
 "body": {"from": {"count": 5}}}
```

A **stream atom with no `n`** — rejected by `subscription-v0.4` (both `stream`
and `n` are required):

```jsonc
{"seq": 22, "topic": "control.subscribe", "name": "grad_norm", "request_id": "r",
 "body": {"from": {"stream": "loss"}}}
```

A **stop with an `every`** — rejected by `subscription-v0.4` (a stop is
one-shot: at most a `from`):

```jsonc
{"seq": 23, "topic": "control.stop", "name": null, "request_id": "r",
 "body": {"every": {"commits": 10}}}
```

A **stop with no `request_id`** — rejected by `subscription-v0.4` (every control
record is answered by name):

```jsonc
{"seq": 24, "topic": "control.stop", "name": null, "request_id": null,
 "body": {}}
```

A **withdrawal with no `t`** — rejected by `subscription-v0.4` (`t` is required):

```jsonc
{"seq": 25, "topic": "control.withdraw", "name": null, "request_id": "stop-1",
 "body": {}}
```

The **retired `control.unsubscribe`** — rejected by `subscription-v0.4` (the
`control.*` topic set is closed to `subscribe`, `stop` and `withdraw`):

```jsonc
{"seq": 26, "topic": "control.unsubscribe", "name": null, "request_id": "sub-1",
 "body": {}}
```

Some refusals are **valid on the wire and refused by a `nak`**, since a schema
cannot see them: a pure demand with a `from` (`{"from": {"commits": 1}}` under
`name: null`), a re-send that changes its target or class, a subscription to a
name the claim does not declare a source, a `{stream}` atom naming a source,
and an empty window. Each is a valid ` ```json ` record, which is why none
appears here.

---

## 9. Where to go next

- The wire format is authoritative in [`../protocol/`](../protocol/); the
  semantics in [`specs/commits-and-lineage.md`](specs/commits-and-lineage.md),
  [`design-v0.2.md`](design-v0.2.md) and the shipped [`specs/`](specs/).
- The spec's known limits (§12) bound what a second implementation can promise:
  forgery and authority are an honor system; observation is relative to the
  newest claim; a stop names no claim; death reports are trusted; a same-host
  crash nobody reports reads as held.
- The next layers are backlogged and **not converged**: the lineage graph
  ([`backlog/lineage-graph.md`](backlog/lineage-graph.md)), demand as `asked`
  facts ([`backlog/demand-streams.md`](backlog/demand-streams.md)), liveness
  promises ([`backlog/liveness-promise.md`](backlog/liveness-promise.md)) and
  in-log compaction ([`backlog/in-log-compaction.md`](backlog/in-log-compaction.md)).
  A second implementation should **not** anticipate any of them.
- Schema codegen for other languages is a tracked idea
  ([`backlog/index.md`](backlog/index.md) "Tactical").
