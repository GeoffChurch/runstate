# `completed` is the worker's sole terminal claim; the default clean halt projects to `preempted` (B′)

**Status:** converged 2026-06-04 (extended design dialectic); ready for review → implementation.
**Basis:** refines `preempted-vs-completed.md` (which introduced the `completed`/`preempted` outcome
distinction and renamed the outcome value). This spec changes the *default* and the *worker-side
vocabulary*. **Origin:** the `ensure(until)` final review caught this footgun in our *own* example —
`examples/reuse/driver.py`'s `extend` case prints `got 8` instead of resuming `8..19`, because the
worker inherits the default `completed` and `ensure` short-circuits.

## The footgun

`Worker.__exit__` (`worker.py:57-64`) emits, on a clean non-commanded exit,
`stopped(reason=self._stop_reason or "completed")`. So a worker whose loop simply ends — exhausted
its `total`, or fell off — **defaults to `completed`**. For a *resumable* worker (one extended chunk
of a longer run) that is wrong, and it fails **silently**: a consumer (`ensure`'s read-first /
cross-round completed-check) sees `completed`, returns the short series, and **truncates**.

The Worker actually *knows* the reason in only two cases — exception → `errored`, `control.stop` →
`commanded` — and **guesses `completed`** for every other clean exit. The guess is wrong precisely
for the case the memoizer exists to serve (re-drivable chunks), and it's silent.

## The design (B′)

Three moves, in the order the dialectic settled them:

1. **`completed` is the worker's *sole* terminal claim** — affirmative, opt-in. It is the one thing
   only the worker can know: intrinsic, permanent completion (convergence; a fixed-length job
   genuinely finished). The worker declares it explicitly; **nothing defaults to it.**

2. **The default clean halt carries *no* positive claim → projects to `preempted`.** A clean exit
   the worker didn't tag emits `reason=null` ("nothing claimed"), which `peek_terminal` *already*
   projects to `preempted` (its `else` branch). `preempted` becomes the *unmarked* default;
   `completed` the *marked* claim.

3. **The worker never emits the literal `"preempted"`.** That word is exogenous — it is the
   *consumer-side outcome*, apt there because the bound that cut the worker short (`ensure`'s
   `max_steps`, a `control.stop`, a launch-configured budget) is *external*. The worker reports
   endogenous *causes* (or withholds the claim); the consumer *classifies* resumability.

**The record is still emitted** (auto, via `__exit__`) even for the default. Its *presence* is the
§7 clean-halt promise — "I halted at a clean, resumable checkpoint" — which is **stronger than
exit-code-0** (a process can exit 0 mid-step without flushing a checkpoint) and is
launcher-independent (the foreign-episode and no-launcher paths rely on it). Presence (clean halt) is
**orthogonal** to content (done-or-not): B′ keeps the presence, defaults the content to `null`. This
also realizes the "`completed` or nothing" intuition *at the content level* — `null` *is* "nothing
said" — without losing the record.

### Why (the orthonormal core)

- **Ask each party only what it uniquely knows.** *Intrinsic completion* — only the worker knows it
  (→ `completed`). *Whether a bound is final or a chunk* — that is the **driver's** knowledge, which
  the worker often lacks: the same `steps(total=N)` is a fixed length when run standalone and a chunk
  when `ensure`-driven, and the bare worker can't tell which context it's in. So the bound-reaching
  reason is **not the worker's to declare** — it must *default*, and the safe value is `preempted`
  (resumable). Forcing the worker to declare it (Options A/C, considered and rejected) demands
  knowledge it doesn't have, so a forced choice is no better than a wrong default.
- **Fail loud.** A forgotten `completed` → `preempted` → re-drive / loud no-progress, never silent
  truncation.
- **`preempted` is exogenous → it belongs to the consumer.** The bound is externally sourced
  (imposed by `ensure`, or configured at launch and merely polled internally). From the consumer's
  vantage the worker *was* preempted, so the word fits *there*. As a worker self-report it is a
  category error: the worker emits *causes*, the consumer names the *class*.

## The change

- **`Stopped.reason`: `str` → `Optional[str]`** (`payloads.py:55`), and the schema
  `Stopped.reason` `{"type":"string"}` → `{"type":["string","null"]}`
  (`protocol/lifecycle-v0.2.schema.json:52`). `null` = "clean halt, no positive claim." *This is the
  one wire change — flagged in Open questions.*
- **`Worker.stopped(reason=None, ...)`** (was `reason="completed"`, `worker.py:113`); **`__exit__`**
  (`worker.py:63`) emits `stopped(reason=self._stop_reason, ...)` — drop the `or "completed"`. The
  worker claims completion by calling `w.stopped(reason="completed")` (idempotent first-writer-wins,
  unchanged) before/at exit.
- **`peek_terminal` — no code change** (`liveness.py:82-93`): `s.reason == "completed"` is `False`
  for `None` and for any descriptive cause, so it already falls to the `else` → `preempted`. The
  **launcher tier is untouched** — it is the *no-clean-stop* backstop (hard crash / non-runstate
  worker that left no `stopped`); B′ always emits a clean `stopped`, so the lifecycle tier fires
  first and the launcher tier's `exit 0 → completed` does not apply to a B′ worker.
- **Prose:** the §7 / `Stopped` description "its existence = the run cleanly finished" →
  "its existence = a clean, *resumable* halt; *finished* (`completed`) is the worker's opt-in claim,
  otherwise `preempted`."

### Reason vocabulary under B′

| `reason` | meaning | outcome |
|---|---|---|
| `null` (default) | clean halt, no positive claim | `preempted` |
| `"completed"` | intrinsic permanent completion (the sole *claim*) | `completed` |
| `"errored"` | exception (carries `error` message) | `errored` (auto) |
| any other string (`"commanded"`, `"max_steps"`, …) | optional *descriptive cause* | `preempted` |

B′ forbids only the worker **typing the outcome word `"preempted"`** — by convention + cleanup, not
by validation. Descriptive causes stay legal (the field is free); they all project to `preempted`.

## Orthonormal-basis check

- **Independence:** not a new primitive — re-defaults an existing one and widens `reason` to nullable.
  Adds no redundancy; removes a wrong guess.
- **Spanning:** supplies the missing "resumable by default" behavior the `completed` guess fouled,
  without over-reach: the worker still only declares `completed` (what it alone knows).
- **Canonical form:** `completed` is the one worker-knowable terminal claim; `null` is the
  least-arbitrary representation of "no claim" (and matches the repo's present-nullable convention).
  `preempted` lives where it's apt — the consumer projection.
- **Orthogonality:** record *presence* (clean halt) ⟂ record *content* (the `completed` bit) ⟂
  consumer *projection* (`preempted`). B′ touches only the content default; presence and projection
  are unchanged.
- **Serendipity:** `peek_terminal` needs *no* code change — `null` already projects to `preempted`;
  the fix falls out of the existing `else`. The launcher/lifecycle "two viewpoints" split is
  preserved (the worker still self-reports its clean halt in-band, independent of any exit code).

## Scope / ripple

- `runstate/worker.py` — the default flip (`stopped` param + `__exit__`).
- `runstate/vocabulary/payloads.py` — `Stopped.reason: Optional[str]`.
- `protocol/lifecycle-v0.2.schema.json` — `Stopped.reason` nullable (+ `tests/test_schema.py`
  conformance: assert a `null`-reason `stopped` validates).
- `runstate/liveness.py` — **no code change**; prose only (the `else` comment + `RunResult` docs).
- Tests — `tests/test_worker.py` (the default is now `null`/`preempted`, not `completed`);
  `tests/test_liveness.py` (a `null`-reason stop → `preempted`); `tests/test_memoizer.py` fixtures
  `_cell`/`chunked` and any worker emitting `reason="preempted"` → rely on the default (or claim
  `completed` where genuinely final).
- `examples/reuse/driver.py` — make `train` **genuinely resumable** (don't claim `completed`) so the
  `extend` case resumes `8..19`, fixing the misleading comment the `ensure(until)` review flagged.
- `docs/design-v0.2.md` §7 + `docs/specs/preempted-vs-completed.md` — retune the
  "worker self-reports preempted / clean stop = finished" prose to the B′ framing.
- **Downstream (mycooc, separate repo — out of scope here):** its emitter stops typing `"preempted"`;
  patience-convergence claims `completed`; chunk / `max_steps` stops rely on the default.

## Non-goals / open for review

- **Not Option A or C** (do *not* force a declaration on finite-`total` or on every clean exit) —
  the bound-reaching reason isn't the worker's to know, so it must default, not be demanded.
- **Don't forbid descriptive causes** — `"commanded"` etc. remain legal worker color (free string),
  all projecting to `preempted`. Only the literal outcome word `"preempted"` is retired from
  worker-side use.
- **The one wire change** (nullable `reason`): the alternative is *B′-minimal* — keep `reason`
  a required string and default to a neutral non-null word (no schema change). I recommend `null`:
  it's the honest representation of "no claim," matches the present-nullable convention, and needs no
  `peek_terminal` change. Decide at review.
- **Claim API:** `w.stopped(reason="completed")` suffices; an optional `w.completed()` convenience is
  possible (sugar for it). Decide at review.
