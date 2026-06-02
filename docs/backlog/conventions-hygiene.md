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

Documented in `docs/design-v0.2.md` §5 (table) and §7 ("Body `{phase}`;
`latest` queries the current phase") but **absent** from the lifecycle schema
enum (`protocol/lifecycle-v0.2.schema.json`), from `runstate/vocabulary/payloads.py`,
and from all code/tests. It is redundant with the `value` + `name="phase"` +
`latest` projection, and the protocol recognizes it for nothing (unlike
`lifecycle.stopped` / `launcher.terminated` driving `peek_terminal`). **Cut it
from the §5/§7 prose** — it is a user `value` register, not a reserved lifecycle
topic. (Counter: a reserved topic gives a closed-vocabulary enumeration
guarantee a user `name` can't — but nothing consumes it, so that buys nothing.)

## F3 (med) — produced-but-unconsumed fields

`heartbeat.consumed_seq` (the subscribe-ack watermark, §6/§13 "it *is* the ack")
and `started.hostname` / `started.attached_at` are written but **read by nothing
shipped** (the Watcher reads only `heartbeat.step`). Legitimate forward-design
for an orchestrator ack-check (`latest("lifecycle.heartbeat").consumed_seq >=
my_seq`) — but until a consumer exists (and `test_schema`/an example exercises
it), the prose should stop calling `consumed_seq` load-bearing, or the consumer
should be added. This is what makes the heartbeat non-terminal — the cost it
pays for an ack nobody yet collects.

## F8 (low-med) — `RunResult.elapsed` is a dead field

Declared (`runstate/liveness.py`) and documented (§9 "spans started→stopped")
but **never populated** — `peek_terminal` and the Watcher `presumed_dead` paths
all omit it, so it is always `None`. Either populate it (`peek_terminal` has the
`started`/`stopped` seqs; the Watcher tracks `last_heartbeat_at`) or drop it
from the dataclass + §9. A dead field on the canonical verdict object weakens
the minimal-`RunResult` story. (A real `elapsed` needs a time axis — see the
`value.t` → absolute-wall-clock decision in `docs/specs/memoizer.md`.)

## F9 (low-med) — `local://host/pid` lacks the PID-reuse disambiguator

Design §8 specifies `local://host/pid?start=T`; the code emits bare
`local://host/pid` and `resolve()` ignores any `?start=`, so a recycled PID can
read as falsely alive. Already self-documented as deferred in
`runstate/vocabulary/handle.py`. Backstop: heartbeat-staleness (tier 4). Add the
start-time disambiguator when hardening liveness resolution.

---

Low-rated and self-defended as probably-correct, **not** actioned: F2 (`started`
carrying handle + episode-identity), F5 (`launched.status` single-value enum,
forward-compat), F6 (`StopTrigger ⊊ Schedule` hand-enforced semantic
restriction), F7 (`value.step` denormalized onto the datum).
