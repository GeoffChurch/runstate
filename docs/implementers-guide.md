# Protocol-implementer's guide — writing a non-Python runstate

**Status:** DERIVED, not authoritative. Dated 2026-07-16. This guide restates
the wire contract for someone building a **second implementation** (Rust, Go,
TypeScript, …). It is a reading of the binding artifacts, never the tiebreaker.

**Authority order** (when this guide disagrees with any of them, they win):

1. The **JSON Schema stack** in [`../protocol/`](../protocol/) — the wire format
   (envelope + per-convention schemas, each `additionalProperties: false`,
   independently versioned).
2. [`design-v0.2.md`](design-v0.2.md) — the semantics, plus the shipped
   [`specs/`](specs/) for scopes not yet folded back into it.
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
  ([`tests/test_channel.py`](../tests/test_channel.py)).
- **Speaks-the-conventions** — on top of a conforming log, you also produce and
  consume the opt-in typed bodies (`control.*`, `lifecycle.*`, `launcher.*`,
  `value`) pinned by the convention schemas. A worker, an orchestrator, a
  launcher, or an observer each speaks *some* of the conventions.

The conventions are **opt-in and independent** (design §5, §10). The minimal
useful client is small: a dashboard whose only job is a stop button implements
**the envelope + exactly one topic** — it appends one `control.stop` record
(§3.2) and reads `lifecycle.stopped` to see the effect. It never touches
`value`, `launcher.*`, subscriptions, or the condition-algebra. Conversely, a
worker that opts out of the conventions entirely composes its own loop from raw
`send`/`read`/`latest` + the liveness tiers (§3.9) — the substrate imposes no
message shape.

Interop is **by wire records, not by API**. Your implementation and the Python
reference interoperate iff they agree on (a) the envelope, (b) the storage
layout of a shared backend (§5), and (c) the convention bodies you both touch.
Nothing else is shared — no code, no RPC, no run-time negotiation.

---

## 2. The substrate contract (design §4)

A channel is a handle on **one run's** append-only log of envelopes. The
envelope (design §4; [`envelope-v0.2.schema.json`](../protocol/envelope-v0.2.schema.json);
`runstate/channel/envelope.py`):

```
seq        : integer >= 1     substrate-assigned total order (see below)
topic      : string, len>=1   CLOSED protocol-owned routing key
name       : string|null      OPEN app-owned identifier (metric name, sub target)
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
  indeterminate fault — a silent loss here is a liveness hole (a claim that a
  wedged writer might yet roll back would leave the run claimed by nobody). The
  check-and-append is **one critical section across handles and processes**: the
  read of the head and the append are atomic w.r.t. every other writer. This is
  the standard optimistic-concurrency primitive of log stores (NATS
  expected-last-seq, EventStore `expectedVersion`), and it is the **only**
  non-fold operation in the surface — everything else is a query
  ([`backlog/protocol-algebra.md`](backlog/protocol-algebra.md) L1). Everything
  that looks like distributed coordination in runstate — single-spawn, episode
  claim, the careful death — rests on this one primitive.

- **`read` — cursored, non-destructive.** Returns envelopes with `seq > after`,
  in `seq` order, filtered by `topics` (§2.3), `name` (exact), `request_ids`,
  and `limit`. **Cursors are caller-owned**: the substrate keeps **no per-reader
  state and no registry of who is reading** (design §4 "Caller-owned cursors").
  N readers each see every matching envelope. Crash-resume = persist the `seq`
  and re-`read(after=seq)`; start position is just the initial cursor.
  - `request_ids` scopes **visibility**: a reader passing `[r1, r2]` sees
    records whose `request_id ∈ {null} ∪ {r1, r2}` (its own ids **plus**
    unaddressed broadcasts). An **empty** `request_ids=[]` means "only the
    broadcasts" (`request_id IS NULL`). Visibility is **read-side filtering, not
    enforcement**, until a backend can enforce it (design §6). The worker reads
    all `control.*` regardless.

- **`latest(topic, name=None)` — the register/most-recent projection.** The most
  recent envelope on a `(topic, name)`, or `None`. It is a substrate primitive
  (not merely a `read` helper) on **backend-optimization** grounds (SQLite
  indexed `ORDER BY seq DESC LIMIT 1`; NATS `last_per_subject`), and its
  well-definedness assumes a **single writer per `(topic, name)`** (the common
  case) or a single sequencer (design §4). `latest` takes **exact topics only,
  no patterns** (§2.3).

- **`last_seq()` — the CAS's read half.** The log's last `seq` (`0` = empty),
  O(1) on every backend (`len(log)`; `MAX(seq)` on the key). Admitted on the
  **op-admission principle**: *the surface must be readable in every coordinate
  it requires callers to assert* (design §4). The CAS makes every claimant assert
  the head (`expected_seq`); `last_seq` is that coordinate's read. Its two
  sanctioned uses: a CAS claimant reading the head before a head-first capped
  attach, and an incremental reader's has-anything-new watermark. (`count`,
  `first`, `read_range` are **not** in the surface — no caller is required to
  assert them.)

- **`close()`** — release *this handle's* resources. Does **not** delete the
  log, stop the worker, or affect any other handle on the run
  (`runstate/channel/base.py` module docstring). Many handles coexist on one run.

### 2.2 The `seq` contract — contiguous, 1-based, per log

`seq` is **one contiguous, 1-based sequence — exactly `1..N`, no gaps, across
all topics** — assigned by the log's single sequencer (design §4 "A per-log
total order is the contract"). Consequences a second implementation **must**
reproduce:

- `last_seq() == N == the record count` (contiguity makes these equal —
  `runstate/channel/memory.py:last_seq` returns `len(self._log)`).
- The CAS base case is `expected_seq=0` for the empty log.
- **The pairing-by-`seq` rule** (§3.6) compares positions *across* topics in
  every instance, so a backend offering only per-topic FIFO **cannot host the
  conventions**. The conformance suite pins contiguity on every backend
  (design §4; `tests/test_channel.py`).

Assign `seq` from a single serialization point. SQLite uses `INTEGER PRIMARY KEY
AUTOINCREMENT`; Postgres uses `MAX(seq)+1` under `PRIMARY KEY (run_id, seq)` (a
global `SERIAL` would gap on rollback and is wrong — §5.3); the in-memory
backend uses `len(log)+1`.

### 2.3 The topic-pattern grammar (in full)

The **only** wildcard is a **trailing `.>`** (design §4 "The pattern grammar, in
full"; verified in `runstate/channel/memory.py:_topic_match` and
`sqlite.py:_escape_glob`/`read`):

- `"control.>"` matches every topic **starting with** `"control."` — the text
  before `.>` is a **literal prefix**.
- **Metacharacters in the prefix are inert.** A topic containing `*`, `?`, `[`,
  `%`, `_` matches only itself: SQLite escapes `*?[` into single-character GLOB
  classes (`_escape_glob`), Postgres escapes `%`, `_`, `\` for `LIKE`
  (`postgres.py:_escape_like`). Your backend must neutralize its own matcher's
  metacharacters so an arbitrary user topic never acts as a wildcard.
- **Matching is case-sensitive** (SQLite uses GLOB, not the ASCII-case-insensitive
  LIKE; Python uses `str.startswith`; Postgres uses `LIKE` with a backslash-escaped
  literal prefix — which is case-sensitive by default; the index in §5.3 is a plain
  btree, no special opclass).
- There is **no bare `">"`**, and **no mid-string wildcard**.
- **`latest` takes exact topics only** — no patterns.
- `read(topics=[])` (an explicit empty list) returns **nothing** ("among these
  zero topics": vacuously none) — distinct from `topics=None` (no filter).

### 2.4 Thread-safety and retention

- **Every handle is thread-safe** (design §4). One handle may be shared across
  threads (the reference `ThreadLauncher` hands one instance to both the worker
  and the Watcher). Under mixed traffic through one handle — CAS claims
  interleaved with plain sends — every acknowledged send is durably in the log
  and at most one CAS wins; each backend serializes its own connection use
  internally (SQLite/Postgres take an internal lock; the memory backend a
  `threading.Lock`).
- **Retention is full** until the run's channel is explicitly cleaned up
  (design §4; §12.9). There is **no in-log GC or compaction** — this is the
  precondition that `peek_terminal` and cross-episode resume rely on. (In-log
  compaction is deferred; see
  [`backlog/in-log-compaction.md`](backlog/in-log-compaction.md).)

---

## 3. The conventions (current wire versions)

Opt-in typed bodies over the substrate. Wire versions **as of 2026-07-16**:
envelope/subscription/value at **v0.2**; lifecycle/launcher at **v0.4** (the
observer-clock bump dated the beacon — [`specs/observer-clock.md`](specs/observer-clock.md)).
Each schema is `additionalProperties: false` and independently versioned; adding
a field is a deliberate version bump, never silent (design §10).

### 3.0 Notation: present-nullable vs omittable-slot (design §7 note)

Two distinct "optional" shapes; the schemas pin the difference and your types
must too:

- **Present-nullable** (worker-authored `lifecycle.*` / `value` body fields):
  `field?` here means *the key is always present, its value possibly `null`*.
  The schema marks it `required` with type `["…", "null"]`. Example:
  `Heartbeat.step` is present-nullable — a stepless worker sends `{"step": null,
  …}`, **never** omits the key. Omitting it is a schema error
  (`test_schema.py::test_heartbeat_body_is_pinned`).
- **Omittable-slot** (orchestrator-authored **schedule** slots `from`/`every`/
  `until` in a subscription body): the key is *omittable and non-nullable* —
  **absence is itself the semantics** (fire-now / one-shot / never-expire), and
  `{"from": null}` is **schema-invalid** (`subscription-v0.2` `$defs.Schedule`
  has no null option). This is the one deliberate carve-out from present-nullable.
- At the **envelope** level, `name`/`request_id` are genuinely
  *omittable-or-null* (either absent or explicitly `null`).

### 3.1 The topics table

Reserved topics, producer, consumer, and pinning schema (design §5, §7, §8;
[`api.md`](api.md) "Wire topics"). The substrate routes on
`topic`/`name`/`request_id` and never parses `body`.

| topic | body | produced by | consumed by | schema |
|---|---|---|---|---|
| `control.subscribe` | schedule `{from?, every?, until?}` | orchestrator | worker | `subscription-v0.2` |
| `control.unsubscribe` | `{}` (cancels by `request_id`) | orchestrator (or worker on expiry) | worker | `subscription-v0.2` |
| `control.stop` | `{from?}` | orchestrator | worker | `subscription-v0.2` |
| `lifecycle.started` | `{handle, t}` | worker | observers | `lifecycle-v0.4` |
| `lifecycle.heartbeat` | `{step?, consumed_seq, t}` | worker | observers | `lifecycle-v0.4` |
| `lifecycle.stopped` | `{completed, error?, final_step?, t}` | worker | observers | `lifecycle-v0.4` |
| `lifecycle.nak` | `{reason, message}` | worker | requester (by `request_id`) | `lifecycle-v0.4` |
| `launcher.launched` | `{handle, status, t}` | launcher | observers | `launcher-v0.4` |
| `launcher.terminated` | `{reason, exit_code?, signal?, t}` | launcher | observers | `launcher-v0.4` |
| `value` | `{value, step?, t?}` | worker | observers | `value-v0.2` |

`t` (v0.4) is the **emitter's wall-clock at emission**, **required and non-null**
on `started`/`heartbeat`/`stopped`/`launched`/`terminated`. `Nak` is
**undated** (not read by the observer plane). `Value.t` stays **present-nullable**
(the data plane's clock, a separate concern — observer-clock §3). See §4.6 for
the three-clocks rules that govern `t`.

### 3.2 `control.*` (the subscription convention, design §6)

The message kind **is the topic** — there is no `kind` discriminator (design §6;
the closed topic vocabulary is the discriminator). `subscription-v0.2` pins:

- `control.subscribe`: body is a **schedule** (§3.3); envelope `name` = the
  target value's name; `request_id` is **required, non-null** and correlates the
  answers.
- `control.unsubscribe`: body `{}` (empty, `additionalProperties:false`);
  cancels by `request_id`, which is **required, non-null**.
- `control.stop`: body `{from?}` — one-shot, **at most a `from`** (`every`/
  `until` are schema-rejected: a stop fires once, and an `until` could perversely
  gate it from ever firing). `request_id` is optional (traceability only).

### 3.3 The schedule condition-algebra (design §6; `subscription-v0.2` `$defs`)

A schedule is `{from?, every?, until?}`. It **fires at `from`** (default: the
next safe point), **repeats every `every`** (**absent `every` ⟹ one-shot**), and
**expires per `until`** (if present). Each slot is a **Condition**:

```
Coord      := {"step": N>=0} | {"time_seconds": S>=0}
Threshold  := Coord | {"count": C>=0}          # count only under `until`
Condition  := Coord      | {"any": [Condition, …>=1]} | {"all": [Condition, …>=1]}
UntilCond  := Threshold  | {"any": [UntilCond, …>=1]} | {"all": [UntilCond, …>=1]}
#   from, every : Condition       until : UntilCond
```

Binding semantics (verified in `runstate/vocabulary/schedule.py`):

- **Thresholds are `>=` comparisons** (`satisfied`): `step` fires when
  `step is not None and step >= N`; `time_seconds` when `elapsed >= S`; `count`
  when `count >= C`. Every condition is therefore **monotone** — once true, it
  stays true as the coordinates advance. A `step` threshold is **never satisfied
  for a stepless worker** (`step is None`).
- **`any` = whichever crosses first (OR / min); `all` = whichever crosses last
  (AND / max).** Both take a **non-empty** list (`minItems: 1`).
- **Clocks per slot:** `from`/`until` are **absolute** (step value,
  seconds-since-registration, total fires); `every` is **deltas since the last
  fire**.
- **`count` is grammatical only in `until`** — structurally, via the per-slot
  term type (`Condition` has no count; `UntilCondition` does). It is a **fire
  budget**, meaningful only as an expiry. A `count` anywhere else is `malformed`
  (§3.4). Nesting recurses with the slot: `{"until": {"any": [{"count": 3},
  {"step": 10}]}}` is legal; the same under `from` is not
  (`test_schema.py::test_count_grammar_recurses_with_its_slot`).
- **`until` gates *before* the fire** at the same safe point — the boundary
  coordinate produces **no** fire (the window is **half-open**, §4.5) — **except**
  a `count`-`until`, which expires **on** the fire that spends the budget (the
  count only moves with the fire). (`Subscription.tick`: a pre-fire expiry gate,
  then the fire, then a post-fire expiry check.)
- **No normal form.** The algebra is freely associative; equivalent encodings
  are behaviorally inert, and runstate never compares/dedups/hashes conditions,
  so canonicalization would buy nothing (design §6; `CLAUDE.md` rubric). Do
  **not** normalize; a `maxDepth` resource guard is the only sanctioned
  restriction.

### 3.4 `lifecycle.nak` — the closed reason enum (design §6; `lifecycle-v0.4`)

A refused control request; envelope `request_id` = the offending request. Body
`{reason, message}` with `reason ∈ {malformed, unsatisfiable, unsupported}`:

- **`malformed`** — the body did not conform to the grammar (a sender bug):
  unknown schedule key, a non-condition slot, a `count` atom outside `until`, a
  negative threshold. Decided by the **structural gate** *before* any semantic
  check (`schedule.py:malformed_schedule` / `malformed_stop_trigger`), so a
  registered schedule is guaranteed to evaluate cleanly at every safe point.
- **`unsatisfiable`** — well-formed but **statically zero-fire**
  (`schedule.py:is_unsatisfiable`): `until` already true at registration; a
  step-keyed condition on a **stepless** worker; or an **empty window**
  (`from ⟹ until` — the gate opens no earlier than it closes, decided for a
  *conjunctive* `from` by a single-point corner check; a `from` containing `any`
  degrades to a dynamic never-fire rather than reaching for a normal form). A
  merely future or already-crossed `from` is **not** unsatisfiable — by the `>=`
  semantics it fires at the next safe point.
- **`unsupported`** — an unknown `control` verb (a topic under `control.` that
  is not `subscribe`/`unsubscribe`/`stop`).

A nak **refuses one bad request without crashing the worker** — the message is
naked and dropped, never fatal (design §6; `worker.py:_drain_control` wraps each
control record in a try/except → `nak(malformed)`). The *dynamic* never-fire (a
worker that stops before the trigger) is **not** a nak — it is signalled by
`lifecycle.stopped`; a merely slow worker is a patience-cap concern (§3.9,
design §9).

### 3.5 The reference worker loop (design §6; `runstate/worker.py`)

If you build a worker that speaks the conventions, these steps are **normative**
(an observer relies on their observable consequences). Each `tick(step)`:

1. **Drain `control.*`** after the persisted cursor, applying the folds in a
   **fixed order per subscribe** (`worker.py:_handle_control`):
   1. missing `request_id` → `nak(malformed)`;
   2. **answered-skip** — an `unsubscribe` **or** `nak` *following* the subscribe
      by `seq` and bearing its `request_id` means it is already answered →
      **skip** (the positional answer fold, §3.6). A resumed episode thus never
      resurrects an expired lease nor re-naks a refused request;
   3. **boundary-void pop-then-skip** — a **time-referencing** subscribe with a
      prior episode's `started` between it and this episode's own claim is voided
      ([`specs/time-lease-boundary.md`](specs/time-lease-boundary.md)); the skip
      still **rescinds its same-id predecessor** (registrations are slots, not a
      set — `worker.py` pops the id, then returns);
   4. **structural `malformed` gate** — the full grammar check
      (`malformed_schedule`);
   5. **`unsatisfiable`** — the static zero-fire check (`is_unsatisfiable`);
   6. else **register**.
   The order has **observable consequences** an implementation must reproduce
   (design §6 loop step 1): an answered-and-malformed subscribe is **never
   re-naked** on resume, and a boundary-voided malformed time-lease gets **no
   nak**. A `control.stop` is added to the **pending set** unless already
   discharged by a `lifecycle.stopped` later on the log (the discharge floor,
   §3.6). **Then** advance the cursor.
2. **Service due subscriptions** — emit `value`s. A registration **expires** the
   moment no future fire is possible (`until` met, one-shot consumed, recurrence
   impossible — *registered ⟺ fire-possible*, enforced). Expiry is
   **emit-then-delete**: the worker writes the **expiry counter-record** (a
   worker-authored `control.unsubscribe` bearing the `request_id`) to the log
   **before** deleting the in-memory registration, so a crash between the two
   re-derives correctly ([`specs/service-worker.md`](specs/service-worker.md)).
   **Register before reap** (step 1 before step 2) so a keepalive *refresh* never
   transiently zeroes the demand count.
3. **Beacon the heartbeat — exactly one per tick, unconditional, after steps
   1–2.** The dense per-tick axis `progress` relies on it (`observables.progress`).
   Emitting it *after* the answers and services means its `consumed_seq` (the
   worker's read position in its **inbound `control` order**, design §11) is a
   **truthful watermark** for the same-tick records — every nak, registration
   effect, value, and expiry counter-record is on the log before the beat.
4. **Evaluate the stop decision** — the pending set's `any`-join, a **monotone
   level** (§3.6). If stopping, emit `lifecycle.stopped` and exit.

`consumed_seq` is **not** a global `seq` — it is the read position in the inbound
`control` order (design §11–12). "Did my request land?" is the **consumption
watermark** (`consumed_seq ≥ its seq` and no `nak`), and it is **answer-first**:
a `nak` following the request resolves it regardless of the watermark
(design §6; `watcher.await_consumed`). The worker advances `consumed_seq` only
**after** durably registering/naking, so it is a true registration watermark,
not merely "read past."

### 3.6 The pairing-by-`seq` rule (design §7; `specs/stop-discharge.md`)

*A standing fact is paired with its counter-record by log position — the counter
must **follow** it by `seq`.* This is the single rule behind the drain semantics,
and it has **four instances** (design §7 states them once):

1. **`control.stop` ↔ the next `lifecycle.stopped`** (the discharge). A stop is
   pending from its append until the next `stopped` that follows it by `seq`; any
   `stopped` **discharges every pending stop at once** (a broadcast answer,
   matching the `stopped` record's broadcast nature). A discharged stop is
   history, never again input. Public observer home:
   `observables.undischarged_stops`.
2. **`control.subscribe` ↔ the next `control.unsubscribe`-or-`nak`** bearing its
   `request_id` (the **answer fold**). Public home: `observables.live_demand`.
   The worker's own **expiry counter-record** (§3.5) applies the same eliminator
   a client's rescind does — author-blind (design §5; the expiry record is
   bookkeeping, not a command).
3. A **time-referencing subscribe ↔ additionally the next episode boundary**
   (`specs/time-lease-boundary.md`): a time-lease is a contract with one living
   episode, voided **recordlessly** by a foreign `started`, re-anchoring at most
   once.
4. **Episode terminality ↔ no `started`/`launched` following the terminal
   record** (`specs/run-episodes.md`).

`value` fires are **deliberately not answers** — the answer set stays
schedule-independent and the fold body-light. The in-episode stop decision is the
pending set's `any`-join — a **level** that latches by inheritance, never a
consumed-once pulse (a host that misses one `True` recovers it at the next safe
point; `Worker.stop_pending` is the side-effect-free poll). Both verbs re-derive
from `seq 0` across episodes, which is why a stop sent while the run is down is
answered by the next episode — **exactly once** (that episode's own `stopped`
discharges it).

### 3.7 Episodes: the birth-CAS self-claim and the retire death-CAS

A `run_id` names a **durable log that hosts multiple resumable worker episodes**
(`specs/run-episodes.md`). The single-spawn guarantee is a **worker self-claim**,
and the whole thing rests on the CAS (§2.1):

- **Birth claim** (`worker.py.__init__`): read the head with `last_seq()`;
  compute the drain folds from **topic-filtered reads capped at that head**;
  if `live_episode` is already live, set `_lost` and exit without acting; else
  `send(Started, expected_seq=head)`. Winning at `head` proves nothing landed
  past the cap, so the capped folds equal an unfiltered same-read's folds (the
  head-first capped attach, design §12.5 — a 10⁶-envelope attach went 3.4 s →
  1.5 ms). The claim re-emits the launch id on `request_id` (§3.8).
- **Death CAS** (`worker.py.retire`): the dying breath of a service worker is
  **compare-and-appended against the drained log** — episodes are CAS-claimed at
  both ends, so a subscribe racing the death is never orphaned. **Discipline:
  `expected_seq` comes only from a read, never from an own append's returned
  seq** (an own append can land on top of an unseen racing subscribe); **any
  record found — including the worker's own naks/expiry unsubscribes — forces one
  more read**, so the CAS fires only against a tail this loop has fully seen and
  drained.

A claim-race **loser** may not act on the channel at all — its `tick` touches
nothing and returns `True` (stop at this safe point), and its explicit
`stopped()`/`emit()` no-op (else a double-spawn loser could write a `completed`
claim onto the winner's live log — `specs/lazy-launch.md`). `Worker.claimed`
distinguishes a lost claim from a commanded stop.

### 3.8 Launch identity (`launcher-v0.4`; `specs/launcher-record-identity.md`)

Both `launcher.*` records **must** carry the envelope's `request_id` — **one id
per launch, minted by the launcher**, stamped on `launched` and `terminated`,
and **re-emitted by the worker on its `lifecycle.started`**. It reaches the
worker **ambiently**: `RUNSTATE_LAUNCH_ID` in the child's environment (the
cross-process, interop-relevant half — a second-language launcher sets the same
variable) or a ContextVar bound around an in-process target
(`runstate/vocabulary/launch.py`). `null` iff nobody launched the worker
(a hand-run worker — honest information).

The id is **correlation only** — it never scopes visibility. The verdict fold
pairs a death to the launch the **claimed** episode answered
(`observables._launcher_terminal`), never by log position: this is what stops a
late reap or a claim-race loser's death from **forging a live episode's verdict**
(the loser's death names a launch no episode ever claimed; the stale reap names
the old launch). A launcher record with **no** `request_id` is `malformed` to the
verdict plane (`observables._launch_id` raises `MalformedRecordError`).

### 3.9 The liveness detector — a layered, opt-in stack (design §8)

**None of it is substrate-owned** — presence is *emitted messages*, never
substrate state (a mutable TTL'd lease is deliberately avoided, design §13).
Best-to-worst:

1. **Clean completion** — `lifecycle.stopped` exists (a record).
2. **Reaped death** — `launcher.terminated` (the manner; needs a `wait()`ing
   parent).
3. **Probe the handle** — resolve it (`kill -0`, `squeue -j`) for the *fact* of
   death, actor-independently.
4. **Heartbeat staleness** — the newest `lifecycle.heartbeat` older than a
   threshold ⟹ crashed/hung. **The universal floor.** Because the beacon is
   tick-driven, staleness catches **hangs**, not just crashes — but a worker in a
   legitimately **long single step** stops beaconing and looks dead. This is the
   irreducible **dead-vs-busy** ambiguity: the threshold is per-workload tuning,
   and a worker that *can* sub-divide a long step should beacon within it.

The **handle** is a portable, scheme-tagged token: `local://host/pid`,
`slurm://jobid`, `k8s://ns/pod`, `ray://actor` (`runstate/vocabulary/handle.py`).
The `local://` grammar: `local://{hostname}/{pid}`, parsed by taking the text
after `local://`, splitting on the **last** `/` into `(host, pid)`, and parsing
`pid` as an int. `resolve` is **hostname-scoped**: a `local://` handle for
**another host** is *not locally resolvable* (returns "abstain", → the staleness
floor) — never a false verdict from the wrong pid table. (A pid-reuse `?start=T`
disambiguator is deferred — [`backlog/conventions-hygiene.md`](backlog/conventions-hygiene.md)
F9 — and the reference parser does not yet accept it.)

**Staleness is a local inference** — see §4.6.

---

## 4. The harvest — what a second implementation cannot infer from the schemas

This is the material the schemas do not carry, that a non-Python implementer
would otherwise have to read the Python to discover. Every value below is
verified against the current tree.

### 4.1 The public raise-contract table

What the reference Python **raises**, when. A second implementation need not
raise the same *types*, but it must handle the same *conditions* — and a
cross-language consumer of the Python reference must expect these. (Verified
against `runstate/` at 2026-07-16.)

| entry point | raises | when |
|---|---|---|
| `attach_channel` / `create_channel` | `ValueError` | `backend="sqlite"` with `root=None`; `backend="postgres"` with `root=None`; an unknown backend string |
| `attach_channel` / `create_channel` | `ImportError` | `backend="postgres"` without `psycopg` installed (message names `pip install runstate[postgres]`) |
| `attach_channel` | `RunNotFound` | the run has no records (a missing, empty, or foreign store) — the non-mutating open's absence signal |
| `current_channel` | `KeyError` | `RUNSTATE_RUN_ID` unset (then propagates `create_channel`'s `ValueError`/`ImportError`) |
| `Watcher.poll` | `KeyError` | `run_id` was never `add()`/`observe()`-tracked |
| `ensure` | `ValueError` | `until` contains a `count` atom (no driven count axis); the default launch-producer got a non-`{"step": N}` `until` |
| `ensure` | `TypeError` | `producer.extend(until)` returned `None` (the seam contract requires a liveness handle) |
| `ensure` | `RunFailedError` | the producer run reached a **failure** outcome (`errored`/`killed`/`presumed_dead`); carries the `RunResult` observed **at raise time** |
| `ensure` | `NoProgressError` | `ensure`'s **own** spawn died without advancing the step frontier and no live episode owns the run (own-spawn-scoped; a foreign episode re-drives) |
| `history` | `ValueError` | a conforming `value` point has `step: null` (this is a stepped-trajectory reader); a **time-referencing** schedule with **no epoch** (no `lifecycle.started.t` on the log) |
| `peek_terminal`, `live_episode`, `await_consumed` | `MalformedRecordError` | a **verdict-plane** record cannot be interpreted (bad keys, a constraint violation, a launcher record with no `request_id`) |
| `Worker.emit` | `ValueError` | called before the first tick or on a stepless worker (a `step=null` point would poison `history` for the name) |

**Verified corrections to prior notes:** (1) `MalformedRecordError` is raised
**only** by the two **verdict folds** (`peek_terminal`, `live_episode`) and
`await_consumed`'s nak parse — the **measurement folds** (`progress`,
`value_series`, `live_demand`, `last_activity`) **skip** uninterpretable records
instead of raising, and `latest_episode`/`undischarged_stops` are pure reads
(`observables.py` module docstring — the tolerance split by plane). (2) `history`
has **no** "divergent re-emission" raise: since G1
([`backlog/value-plane-divergence-resolution.md`](backlog/value-plane-divergence-resolution.md),
shipped 2026-06-27) it **collapses take-the-latest by `seq`**. (3)
`PostgresChannel.__init__` raises **`RuntimeError`** ("call `ensure_schema(dsn)`
first") if the shared `log` table is absent — so `create_channel` /
`attach_channel` with `backend="postgres"` can surface a `RuntimeError` indirectly,
in addition to the `ValueError`/`ImportError` above.

Four of these exception types are part of the exported surface
(`runstate.__all__`): **`MalformedRecordError`**, **`RunFailedError`**,
**`NoProgressError`**, and **`RunNotFound`** (a `LookupError` subclass — the
non-mutating-open absence signal). The rest are Python builtins.

### 4.2 The conformance tier ladder (`tests/conftest.py`)

A backend declares the strongest **contention tier** it supports; a concurrency
test declares the tier it needs. The ladder (`conftest.py:_TIERS`, `_MAX_TIER`):

| tier | means | backend at this tier |
|---|---|---|
| `in_process` | shared only within one OS process (a process-global registry) | `memory` |
| `cross_process` | one durable store, multiple connections / OS processes on a local FS | `sqlite`, `sqlite:delete` |
| `cross_host` | the shared-log CAS is the cross-host claim arbiter (one server = one total order) | `postgres` |

Each tier **requires** the CAS/single-spawn guarantees of the tiers below it. A
concurrency test marks the tier it needs (`@pytest.mark.tier("cross_process")`,
default `in_process`); the fixture **SKIPs** (not xfails) a backend below the
required tier — "not applicable by nature", not "known bug". A second
implementation that adds a backend declares its tier and must pass the tier-gated
suite up to it.

### 4.3 The `RunResult.reason` per-tier vocabulary

`RunResult.outcome` is the **closed** verdict enum (§4.4). `reason` is the
**verbatim per-tier label** — the raw "why", finer than the bucket
(`observables.RunResult`, `watcher.py`). It is deliberately **branched-on by
nobody** in the reference, but a second implementation must reproduce the
vocabulary so a cross-language consumer sees the same strings:

| tier | `reason` values | source |
|---|---|---|
| lifecycle (`lifecycle.stopped`) | `reason == str(outcome)` — one of `"completed"`, `"preempted"`, `"errored"` (a **plain string**, not the enum repr) | `peek_terminal` |
| launcher (`launcher.terminated`) | `"exited"` or `"killed"` (finer than the outcome bucket) | `peek_terminal` |
| inference (Watcher only, all `outcome == "presumed_dead"`) | `"probed_dead"` (handle resolved dead, recordless), `"heartbeat_stale"`, `"episode_lock_released"` (the episode lock dropped past the birth grace) | `watcher.poll` |

### 4.4 The `{completed, error}` → `outcome` projection

The closed `outcome` enum is `COMPLETED | PREEMPTED | ERRORED | KILLED |
PRESUMED_DEAD` (`observables.Outcome`; `failures() = {ERRORED, KILLED,
PRESUMED_DEAD}`). The **record-based** projection (`peek_terminal`):

- **`lifecycle.stopped`** — tested **`error is not None`** (B′), **not**
  truthiness, so an empty-string error `""` still projects **errored**:
  - `error is not None` → **errored**;
  - else `completed` → **completed**;
  - else → **preempted**.
  (`Stopped` enforces `completed ⟹ error is None`, so the two content fields
  never overlap — `payloads.py:Stopped.__post_init__`.)
- **`launcher.terminated`**:
  - `reason == "killed"` → **killed**;
  - else `exit_code == 0` → **completed**;
  - else → **errored**.

**A terminal record stands until a new episode CLAIMS** (`peek_terminal` is
episode-aware): the stop tier reads the latest `stopped` unless a newer `started`
follows it; the launcher tier reads the death of the launch that the latest claim
answered (§3.8). There is deliberately **no `success` boolean** — that is a
policy the consumer owns, not something the producer bakes in (design §9;
`CLAUDE.md` rubric — the closed enum is the canonical projection of the liveness
tiers).

### 4.5 The half-open window fencepost (`observables.progress`)

A target `until={"step": N}` is the **half-open window `[0, N)`** — steps
`0 … N-1`. So:

- the target is **reached iff `progress + 1 >= N`** (equivalently
  `progress >= N - 1`);
- `progress is None` (no stepped record yet) is **window-step 0**, so `N == 0` is
  **trivially reached**.

This is what `ensure`/`history` gate on internally (`memoizer._window_step =
_progress + 1`), and it agrees with the read-side `Subscription` expiry gate
(which excludes the boundary point `N`). A consumer asking "did this run reach
its target?" uses **this** arithmetic — not a bespoke off-by-one. The `+1` is
applied in the coordinate, **never** by rewriting `{step:N}` → `{step:N-1}`
(which would break `any`/`all`, whose atoms all evaluate against the same passed
coordinates — `memoizer.py:_window_step` docstring).

### 4.6 The three clocks + the observer-clock rules

Three clocks coexist (design §11; `specs/observer-clock.md`):

- **`seq`** — the substrate transport order (the per-log total order).
- **`step`** — the worker's logical clock (a body field).
- **`t` / wall-clock** — real time (the `t` body field, v0.4).

Binding rules (`specs/observer-clock.md` §4 — carry these verbatim into a second
implementation):

- **`seq` orders. `t` measures.** `seq` is the **sole** authoritative order; `t`
  is **never** an ordering key, a correctness filter, or an arbiter.
- **`t` is a worker/emitter wall-clock** — authoritative within one writer's
  records, approximate **across** writers (an observer's request vs a worker's
  beacon mix two clocks). The **topic + episode** identify the emitter; a fold
  comparing across them is comparing clocks and must know it.
- **Staleness (`now() − t`) is a LOCAL inference**, valid only for a
  roughly-synced observer. It stays in the `Watcher` (the inference plane) and
  **never** becomes a record-plane verdict. At any distance where clocks cannot
  be synced, **absence of news is not news** (a log ending at T cannot
  distinguish "died at T" from "the rest hasn't arrived").
- **`t` is required ⟹ never fabricated.** Required works because every emitter of
  a dated record *has* the event's time. It does **not** license a
  `default_factory=now()` — where the time may be genuinely unknown (`Value.t`,
  the data plane) the field stays **present-nullable**, explicitly `null`, never
  defaulted. **Do not stamp `now()` onto a record whose real time is unknown.**
- **Time never arbitrates an irreversible decision.** Time may gate a
  **reversible** one (`ensure`'s production wait; the GC grace window), but an
  **irreversible** one (deleting a run) must be gated on a **record-plane fact**.
  Claiming an episode on staleness *inference* is a **refuted** dead end
  ([`dead_ends/failure-detector.md`](dead_ends/failure-detector.md)).

The Watcher keeps the beacon's **arrival time** as its skew-immune witness clock,
and **seeds** the un-witnessed prefix from the newest beacon's own `t` on
registration — so a cold attach to a long-dead run dies correctly instead of
reading fresh (`watcher._heartbeat_seed`; observer-clock §5). `last_activity` reads
the freshness clock directly: the **newest `t`** among the latest of **all five**
dated topics — `started` / `heartbeat` / `stopped` and `launched` / `terminated`.
`started`/`launched` are load-bearing, not padding: a run that attached or was
spawned but has not beaconed yet must still have an age, or the GC's "skip homes
younger than T" cannot protect a genuinely-recent home. And never a `max` over the
whole log (one fast-clock record would pin a whole-log max into the future forever,
and this feeds the GC's irreversible action — observer-clock §6).

### 4.7 The Postgres interop constants (must be bit-identical cross-implementation)

A second-language holder or observer **sharing a Postgres database** with the
reference must reproduce these exactly (`runstate/channel/postgres.py`;
`specs/channel-postgres.md`):

- **Schema-provisioning advisory lock key:** `0x72756E7374617465` — the 8 ASCII
  bytes of `"runstate"` as one positive `int8` (`_SCHEMA_LOCK_KEY`). `ensure_schema`
  wraps the DDL in `pg_advisory_xact_lock(0x72756E7374617465)` so cold-starters
  serialize the DDL instead of racing the `pg_type`/`pg_class` catalog.
- **Episode-lock key MATERIAL:** the length-prefixed string
  **`f"{len(run_id)}:{run_id}:{started_seq}"`** (`_episode_key_str`). The length
  prefix means distinct `(run_id, started_seq)` pairs never alias — a `:` inside
  `run_id` cannot shift the boundary.
- **Hashed server-side** as **`hashtextextended(material, 0)`** (seed `0`) to the
  `int8` advisory-lock key. It is **not** a two-`int4` key (which collapses to a
  32-bit collision domain since first episodes share `started_seq`) and **not**
  Python's salted `hash()` (unstable across processes). The holder takes
  `pg_advisory_lock(hashtextextended(material, 0))` on its **session** connection
  after winning the CAS; an observer reads `pg_locks` read-only for that key. See
  §5.3 and [`backlog/cross-host-claim-gate.md`](backlog/cross-host-claim-gate.md)
  for why the lock is a **Watcher-consumed liveness signal, never a claim
  arbiter**.

---

## 5. Interop with the Python reference on shared storage

Two implementations interoperate by sharing a backend's storage and agreeing on
its layout. Everything here is the storage contract, not an API.

### 5.1 The `RUNSTATE_*` environment variables

A launcher sets these in the worker's environment; a second-language launcher
sets the **same** variables. In the reference they are read at three sites:
`current_channel()` reads the first three from the env and delegates to
`create_channel` (`runstate/__init__.py:current_channel`; the explicit locators
`create_channel` / `attach_channel` take `run_id`/`root`/`backend` as arguments
instead), `vocabulary/launch.py` reads `RUNSTATE_LAUNCH_ID`, and
`channel/sqlite.py` reads the journal-mode knob:

| variable | meaning | default |
|---|---|---|
| `RUNSTATE_RUN_ID` | the run to attach to | **required** (else `KeyError`) |
| `RUNSTATE_CHANNEL_ROOT` | the directory (sqlite) / DSN (postgres) / namespace (memory) | `None` |
| `RUNSTATE_CHANNEL_BACKEND` | `memory` / `sqlite` / `postgres` | `sqlite` |
| `RUNSTATE_LAUNCH_ID` | the launch's correlation id, re-emitted on `started` (§3.8) | `None` |
| `RUNSTATE_SQLITE_JOURNAL_MODE` | sqlite journal mode | `WAL` |

`RUNSTATE_SQLITE_JOURNAL_MODE` accepts exactly `{WAL, DELETE, TRUNCATE, PERSIST}`
(an unknown value raises `ValueError` — `sqlite.py:_resolve_journal_mode`). **The
NFS caveat:** WAL needs a memory-mapped `-shm` sidecar a network filesystem
cannot back coherently (it wedges the open in uninterruptible I/O sleep), so an
**NFS-homed deployment exports `RUNSTATE_SQLITE_JOURNAL_MODE=DELETE`** (the
rollback journal, which uses POSIX byte-range locks). **DELETE removes the hang,
not the cross-host CAS correctness** — SQLite's POSIX locks are unreliable on
many NFS mounts, so the birth-claim CAS can admit two winners and
**single-writer-per-run is REQUIRED on NFS, not merely typical**
(`sqlite.py:SqliteChannel` docstring).

### 5.2 The SQLite layout (`runstate/channel/sqlite.py`)

**One file per run.** The per-run locator is **`<root>/<run_id>.db`**
(`channel/__init__.py:_locate`). The `log` table DDL and index, verbatim:

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
```

- `seq` is the SQLite autoincrement (the single sequencer); `created_at` is
  `time.time()` (advisory — order is `seq`).
- `body` is stored as **opaque JSON text** (`json.dumps(..., separators=(",",
  ":"))`), never interpreted.
- The connection runs in **autocommit** (`isolation_level=None`) with
  `PRAGMA busy_timeout=5000`. The chosen journal mode is (re)applied on every
  connection at open (only WAL persists in the file header; the rollback modes are
  per-connection).
- The `idx_log_topic_seq` index makes `latest(topic)` a seek
  (`WHERE topic=? ORDER BY seq DESC LIMIT 1`) instead of a full scan on every
  Watcher poll.

### 5.3 The Postgres layout (`runstate/channel/postgres.py`; `specs/channel-postgres.md`)

**One shared `log` table for all runs**, so a cross-host viz/BO can query across
runs with no per-run DDL. DDL + index, verbatim:

```sql
CREATE TABLE IF NOT EXISTS log (
    run_id     text   NOT NULL,
    seq        bigint NOT NULL,
    topic      text   NOT NULL,
    name       text,
    request_id text,
    body       text   NOT NULL,
    created_at double precision NOT NULL,
    PRIMARY KEY (run_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_log_run_topic_seq ON log (run_id, topic, seq);
```

- **`PRIMARY KEY (run_id, seq)` is the CAS arbiter.** The CAS is a guarded
  `INSERT … SELECT … WHERE (SELECT COALESCE(MAX(seq),0) FROM log WHERE
  run_id=%s) = %(expected)s`: rowcount 1 → won; rowcount 0 (gate false) **or**
  `UniqueViolation` (a rival committed the same `seq`) → `None`; a connection
  drop / `lock_timeout` exhaustion → **raise**. The `UniqueViolation` catch is
  **specific** — a blanket `except` would map a lock/connection fault to a
  synthesized loss, violating the trichotomy (§2.1). Contiguous per-run `seq` is
  forced by `MAX(seq)+1` per run (a global `SERIAL` gaps on rollback — wrong).
- **`body` stays `text` — the only correct choice, not a simplification.**
  `jsonb` would reorder keys, drop duplicates, and canonicalize
  numbers/whitespace — *mutating the opaque body*, breaking the byte-fidelity /
  immutable-snapshot contract (design §4). A second implementation must store the
  body verbatim.
- **DDL is out of the hot path.** `CREATE TABLE IF NOT EXISTS` is **not**
  concurrency-safe in Postgres (concurrent first-connectors race the catalog), so
  DDL lives in a one-time `ensure_schema(dsn)` wrapped in
  `pg_advisory_xact_lock(0x72756E7374617465)` (§4.7); the channel constructor
  only probes `to_regclass('log')` and raises if absent.
- **What a second writer must serialize:** each channel handle holds **one
  dedicated connection** in `autocommit`, serialized by an **internal lock**
  (psycopg connections are not safe for concurrent statement use; the
  shared-handle topology needs it — exactly as `SqliteChannel._lock` does). The
  endpoint must be **direct or session-pooled** — a transaction-mode pooler
  (pgbouncer) reassigns the backend per statement, silently breaking the session
  advisory lock; this is self-checked at `hold_episode` and raises a clear error.

### 5.4 The memory backend

In-process only (`in_process` tier). `create_channel(backend="memory")` shares a
log across handles in **one process** via a registry keyed by `(root, run_id)`
(`channel/__init__.py:_MEMORY_LOGS`). Not cross-process — it exists for tests and
single-process orchestration. Nothing to interop with across implementations.

---

## 6. What to test — the conformance suite as a checklist

A new implementation reproduces the shape of the Python suite ([`../tests/`](../tests/)):

- **Backend conformance, parametrized over every backend**
  (`tests/test_channel.py` via the `ch` / `open_run` fixtures in
  `conftest.py`, parametrized `[memory, sqlite, sqlite:delete, postgres]`). Every
  backend must pass **independently**: append/read/latest/last_seq; **contiguous
  1-based `seq`**; the value-snapshot-at-`send` immutability; the topic-pattern
  grammar (§2.3, including inert metacharacters and empty-`topics`); the
  `request_ids` visibility filter (own ids + null broadcasts, and empty-list =
  broadcasts only); the **CAS trichotomy** (a race → exactly one winner + `None`
  for losers; a moved log → `None`; a wedged writer → raise; shared-handle
  interleaving).
- **The tier-gated concurrency sub-suite** (`tests/test_concurrency.py` via
  `conc_backend`): each concurrency test marks `@pytest.mark.tier(...)`; a backend
  below the tier **skips**. This is where cross-process single-spawn (sqlite) and
  cross-host single-spawn (postgres) are pinned.
- **Schema conformance — the emit-everything scenario** (`tests/test_schema.py`):
  drive a worker scenario that emits **every** reserved topic, harvest the log,
  and validate each envelope against the **envelope schema + its convention
  schema**; then assert the scenario actually exercised the whole reserved
  vocabulary (`seen == ALL_RESERVED_TOPICS`) so "everything validated" is not
  hollow. Plus **negative cases**: representative malformed bodies are **rejected**
  (`additionalProperties`, the closed enums, `count`-only-in-`until`, the
  present-nullable required fields, the reason-field pairing on `terminated`, the
  `completed ⟹ error null` coupling). §8 embeds a wire-example set your own test
  can validate the same way.
- **The convention behaviors**, if you speak them: the worker loop's fixed drain
  order and its observable consequences (§3.5); the pairing-by-`seq` folds
  (§3.6); the episode birth/death CAS discipline (§3.7); the verdict projection
  (§4.4). The reference pins these in `tests/test_worker.py`,
  `tests/test_observables.py`, `tests/test_run_episodes.py`,
  `tests/test_service_worker.py`.

---

## 7. The why layer — re-deriving the conventions from three decision rules

An implementer who internalizes the constructions behind the layers
([`backlog/protocol-algebra.md`](backlog/protocol-algebra.md)) can **re-derive**
most of the drain semantics rather than memorizing them. The three decision
rules:

- **L1 — the log is the free monoid; initiality decides the surface.** Every
  "stateful communication shape" (register, queue, mailbox, counter,
  bounded-window) is a **unique fold** of the log — which is why they are
  *queries*, not primitives. A proposed substrate op is either **(a) a fold** →
  a derived helper (`latest` is admitted only as a *memoized* fold, on
  optimization grounds) or **(b) a new atomic transition** → it must justify
  itself the way the CAS did (contract + conformance tests). `send` / `read`(+cursor)
  / `latest` / `last_seq` / CAS is **complete**. (Full retention is what makes
  the log initial; GC/compaction *quotients* it — the honesty caveat behind
  §2.4's no-in-log-GC contract and [`backlog/in-log-compaction.md`](backlog/in-log-compaction.md).)
- **L2 — conventions are a designated-elimination discipline.** Every
  control-plane fact is an **intro/elim pair**; **what consumes a fact is fixed by
  the convention, never ad hoc**. The convention folds compute **Γ = the multiset
  of introduced-but-not-yet-consumed facts** — which is *exactly* the discharge
  rule, "every control fact is live until its counter-record." A new convention
  message must arrive as an intro/elim pair with a designated discharge
  (multiplicity declared) **or** be a pure `value` carrying no obligation.

  | intro | designated eliminator | multiplicity |
  |---|---|---|
  | `lifecycle.started` | `lifecycle.stopped` | obligation (worker must eventually emit; `launcher.terminated` is the external backstop) |
  | `launcher.launched` | `launcher.terminated` | obligation (launcher viewpoint) |
  | `control.subscribe` | `control.unsubscribe` (or `nak`) | **affine** — may never be consumed (standing state); a time-lease additionally has the episode boundary as a *second* eliminator |
  | `control.stop` | the next `lifecycle.stopped` | **linear** — consumed exactly once |

  Pure `value` events are the **no-obligation** case (no eliminator, no entry in
  Γ). The elimination is **author-blind** — the worker writing an expiry
  `control.unsubscribe` applies the same affine eliminator a client's rescind
  does (which is why there is no `lifecycle.expired` constructor: every consumer
  would immediately quotient the two). This lens catches ill-typed proposals: the
  original one-shot stop had *no designated eliminator* (its consumption was an
  ephemeral `tick() → True` return), which is precisely the bug the
  stop-discharge fix repaired.

- **L3 — fold observers separately; join only at the verdict.** `lifecycle.*`
  (self-report) and `launcher.*` (external report) are **independent partial
  observers**; `RunResult.outcome` is the canonical projection of their **join**
  (which is why there is **no `success` bool` — the enum *is* the projection).
  **Never merge observers in the data**; take the join at the edge. (This rejects
  "just write the `terminated` into `lifecycle.*`" without discussion.)

**Placement note (annotating `backlog/protocol-algebra.md`'s open question):** the
reader-facing seed of these rules now lives **here** (this section, dated
2026-07-16); the formal treatment's final home — a design appendix vs
`overview.md` incorporation — **remains open** in that backlog file.

---

## 8. Validated wire examples

Canonical envelope records, **validated by the drift-guard test**
[`../tests/test_implementers_guide.py`](../tests/test_implementers_guide.py), so
they can never drift from the wire format. **Convention used by the test:**

- ` ```json ` fenced blocks are **valid**, complete envelope records — the test
  validates each against the **envelope schema + the convention schema for its
  topic**; all must pass, and together they must cover **every** reserved topic.
- ` ```jsonc ` fenced blocks (in §8.2) are **deliberately invalid** envelope
  records — the test asserts each is **rejected** by the envelope schema or its
  convention schema. (They contain no actual comments; the different fence label
  is the only signal, so `json.loads` still parses them.)
- No other JSON in this guide uses these two fences. (Bare bodies/schedules shown
  for illustration appear inline in prose.)

### 8.1 Valid records (one or more per reserved topic)

A subscribe with a compound schedule (fire from step 100; then every 10 steps or
60 s, whichever first; until both step 5000 and 100 fires):

```json
{"seq": 1, "topic": "control.subscribe", "name": "loss", "request_id": "sub-1",
 "body": {"from": {"step": 100},
          "every": {"any": [{"step": 10}, {"time_seconds": 60}]},
          "until": {"all": [{"step": 5000}, {"count": 100}]}}}
```

An unsubscribe (cancels `sub-1`; empty body):

```json
{"seq": 2, "topic": "control.unsubscribe", "name": null, "request_id": "sub-1",
 "body": {}}
```

A conditional stop (stop at step 500; `from` only):

```json
{"seq": 3, "topic": "control.stop", "name": null, "request_id": "stop-1",
 "body": {"from": {"step": 500}}}
```

The worker's claim, re-emitting its launch id on `request_id` (§3.8):

```json
{"seq": 4, "topic": "lifecycle.started", "name": null, "request_id": "launch-abc",
 "body": {"handle": "local://host42/12345", "t": 1721145600.0}}
```

A stepped heartbeat, and a **stepless** heartbeat (a `serve()` worker; `step`
present-nullable):

```json
{"seq": 5, "topic": "lifecycle.heartbeat", "name": null, "request_id": null,
 "body": {"step": 42, "consumed_seq": 3, "t": 1721145602.0}}
```

```json
{"seq": 6, "topic": "lifecycle.heartbeat", "name": null, "request_id": null,
 "body": {"step": null, "consumed_seq": 3, "t": 1721145601.5}}
```

A **completed** stop and an **errored** stop (`error is not None` ⟹ errored,
§4.4):

```json
{"seq": 7, "topic": "lifecycle.stopped", "name": null, "request_id": null,
 "body": {"completed": true, "error": null, "final_step": 999, "t": 1721145700.0}}
```

```json
{"seq": 8, "topic": "lifecycle.stopped", "name": null, "request_id": null,
 "body": {"completed": false, "error": "CUDA OOM at step 512", "final_step": 512,
          "t": 1721145710.0}}
```

A nak (closed reason enum; undated):

```json
{"seq": 9, "topic": "lifecycle.nak", "name": null, "request_id": "sub-bad",
 "body": {"reason": "unsatisfiable", "message": "schedule can produce no fires"}}
```

A launcher record **with its required `request_id`**, and a killed termination
(reason-field pairing: `signal` non-null, `exit_code` null):

```json
{"seq": 10, "topic": "launcher.launched", "name": null, "request_id": "launch-abc",
 "body": {"handle": "local://host42/12345", "status": "running", "t": 1721145599.0}}
```

```json
{"seq": 11, "topic": "launcher.terminated", "name": null, "request_id": "launch-abc",
 "body": {"reason": "killed", "exit_code": null, "signal": 9, "t": 1721145720.0}}
```

A value (answering `sub-1`; `step`/`t` present-nullable but stamped here):

```json
{"seq": 12, "topic": "value", "name": "loss", "request_id": "sub-1",
 "body": {"value": 0.5, "step": 42, "t": 1721145602.0}}
```

### 8.2 Invalid records (deliberately rejected)

Each violates one pinned constraint; the reason is stated, and the test asserts
rejection.

An **extra top-level envelope field** — rejected by `envelope-v0.2`
(`additionalProperties: false`):

```jsonc
{"seq": 1, "topic": "value", "name": "loss", "request_id": null,
 "body": {"value": 0.5, "step": 1, "t": 1.0}, "author": "me"}
```

A **heartbeat with no `t`** — rejected by `lifecycle-v0.4` (`t` is required
non-null since the observer-clock bump):

```jsonc
{"seq": 2, "topic": "lifecycle.heartbeat", "name": null, "request_id": null,
 "body": {"step": 1, "consumed_seq": 0}}
```

A **launcher record with `request_id: null`** — rejected by `launcher-v0.4` (the
launch's correlation id is required on both records, §3.8):

```jsonc
{"seq": 3, "topic": "launcher.terminated", "name": null, "request_id": null,
 "body": {"reason": "exited", "exit_code": 0, "signal": null, "t": 1.0}}
```

A **`count` atom outside `until`** — rejected by `subscription-v0.2` (`count` is
grammatical only under `until`, §3.3):

```jsonc
{"seq": 4, "topic": "control.subscribe", "name": "loss", "request_id": "r",
 "body": {"from": {"count": 5}}}
```

A **completed stop carrying an `error`** — rejected by `lifecycle-v0.4` (the
`completed ⟹ error is null` coupling, §4.4):

```jsonc
{"seq": 5, "topic": "lifecycle.stopped", "name": null, "request_id": null,
 "body": {"completed": true, "error": "boom", "final_step": null, "t": 1.0}}
```

---

## 9. Where to go next

- The wire format is authoritative in [`../protocol/`](../protocol/); the
  semantics in [`design-v0.2.md`](design-v0.2.md) and the shipped
  [`specs/`](specs/).
- For the two cross-implementation-relevant open designs: cross-host liveness for
  the claim gate ([`backlog/cross-host-claim-gate.md`](backlog/cross-host-claim-gate.md))
  and in-log compaction ([`backlog/in-log-compaction.md`](backlog/in-log-compaction.md)).
  Both are **not converged** and owner-gated — a second implementation should
  **not** anticipate either.
- Schema codegen for other languages (Rust types via `quicktype` /
  `datamodel-code-generator` analogs, round-tripped through the schema) is a
  tracked idea ([`backlog/index.md`](backlog/index.md) "Tactical").
