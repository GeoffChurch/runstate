# Conventions hygiene (2026-06 basis audit)

Surfaced by an adversarial "orthonormal-basis" audit of the L2 conventions
(2026-06-02; the rubric is in `CLAUDE.md` → "Design rigor"). The basis is
**largely tight** — the substrate/convention cut, the four liveness tiers, the
two-viewpoints split, and no-`kind`/no-`success`/no-normal-form all hold, and
the "log = initial (under full retention)", "`outcome` = canonical projection",
and "heartbeat-staleness subsumes a substrate lease" claims verified. These are
the *residual* defects: prose-vs-wire drift and dead/never-consumed fields.
None is wire-breaking; all deferred here to keep the memoizer thread focused.

(Two stale meta-claims — "Unit-valued heartbeat ≈ terminal" and "algebra ≈
normal form" — were corrected directly in `CLAUDE.md` when the rubric landed:
the shipped heartbeat is deliberately *enriched* `{step, consumed_seq}` to
amortize the subscribe-ack, and the condition-algebra is the *free* term
algebra, not a normal form, since conditions are never compared/hashed.)

## F1 (high) — `lifecycle.phase` is a phantom basis vector

**Resolved 2026-06-02 (Thread A): cut.**

Documented in `docs/design-v0.2.md` §5 (table) and §7 ("Body `{phase}`;
`latest` queries the current phase") but **absent** from the lifecycle schema
enum (`protocol/lifecycle-v0.2.schema.json`), from `runstate/vocabulary/payloads.py`,
and from all code/tests. It is redundant with the `value` + `name="phase"` +
`latest` projection, and the protocol recognizes it for nothing (unlike
`lifecycle.stopped` / `launcher.terminated` driving `peek_terminal`). Cut from
the §5/§7 prose — it is a user `value` register, not a reserved lifecycle topic.
(Counter: a reserved topic gives a closed-vocabulary enumeration guarantee a
user `name` can't — but nothing consumes it, so that buys nothing.)

## F3 (med) — produced-but-unconsumed fields

**Resolved 2026-06-02 (Thread A): shipped `await_consumed` (watcher.py) as the
blessed consumer of `heartbeat.consumed_seq`.**

`heartbeat.consumed_seq` (the subscribe-ack watermark, §6/§13 "it *is* the ack")
and `started.hostname` / `started.attached_at` are written but **read by nothing
shipped** (the Watcher reads only `heartbeat.step`). Legitimate forward-design
for an orchestrator ack-check (`latest("lifecycle.heartbeat").consumed_seq >=
my_seq`) — now addressed by `await_consumed` in `runstate/watcher.py`, the
canonical "did my control land?" read. *Update 2026-07-10:* `started.attached_at`
has since gained shipped consumers (the run epoch in `memoizer.history` /
`_elapsed` — `specs/ensure-until-condition.md`); `started.hostname` alone remained
unconsumed — never emitted non-null, and the hostname is already carried inside
the handle string, so the field never held data on any log. **Closed same day:
removed in `lifecycle`-`v0.3`** (the review's basis audit confirmed the
independence violation; the viewer forward-case routes to the handle grammar or
a value-plane register; existing logs migrated).

## F8 (low-med) — `RunResult.elapsed` is a dead field

**Resolved 2026-06-02 (Thread A): dropped.**

Declared (`runstate/liveness.py`) and documented (§9 "spans started→stopped")
but **never populated** — `peek_terminal` and the Watcher `presumed_dead` paths
all omit it, so it is always `None`. Dropped from the dataclass and from the §9
sketch. A dead field on the canonical verdict object weakens the
minimal-`RunResult` story. (Note: if duration-on-the-verdict is wanted later, a
deliberate `stopped.t` field could carry the stopped timestamp as a separate
convention decision — that would require a lifecycle schema version bump.)

## F9 (low-med) — `local://host/pid` lacks the PID-reuse disambiguator

Design §8 specifies `local://host/pid?start=T`; the code emits bare
`local://host/pid` and `resolve()` ignores any `?start=`, so a recycled PID can
read as falsely alive. Already self-documented as deferred in
`runstate/vocabulary/handle.py`. Backstop: heartbeat-staleness (tier 4). Add the
start-time disambiguator when hardening liveness resolution.

**Rationale for deferring (captured 2026-06-02 Thread A):** liveness has three
mechanisms on a provability/portability/latency tradeoff. (1) A **held OS
handle** (`Popen.poll()` / `pidfd`) is provably correct but local and
non-serializable — used by the spawner; the memoizer's `ensure` rides it via
`LaunchHandle.is_alive()`, so it is pid-reuse-immune there. (2) **Heartbeat
staleness** is sound and portable but latent — the universal backstop. (3) The
**bare-string `os.kill` probe** is portable and immediate but heuristic (pid
reuse), and `?start=T` only *sharpens* it (a coarse or slow clock can still
collide). Provable + portable + immediate is impossible simultaneously; the
string probe is never the sole tier, so its non-provability is acceptable.
**Conclusion:** add `?start=T` when hardening liveness resolution; it is not
needed for correctness.

---

Low-rated and self-defended as probably-correct, **not** actioned: F2 (`started`
carrying handle + episode-identity), F5 (`launched.status` single-value enum,
forward-compat), F6 (`StopTrigger ⊊ Schedule` hand-enforced semantic
restriction), F7 (`value.step` denormalized onto the datum).
