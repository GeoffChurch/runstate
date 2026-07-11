# `completed` is the worker's sole terminal claim; `Stopped.reason` is removed (B′)

**Status:** converged 2026-06-04 (extended design dialectic, including the `Stopped.reason`
removal); ready for review → implementation.
**Basis:** refines `preempted-vs-completed.md` (which introduced the `completed`/`preempted` outcome
distinction). This spec changes the *default*, removes the free `reason` field, and reduces the
worker's terminal vocabulary to a single bit. **Origin:** the `ensure(until)` final review caught
this footgun in our *own* example — `examples/reuse/driver.py`'s `extend` case prints `got 8` instead
of resuming `8..19`, because the worker inherits the default `completed` and `ensure` short-circuits.

## The footgun

`Worker.__exit__` (`worker.py:57-64`) emits, on a clean non-commanded exit,
`stopped(reason=self._stop_reason or "completed")`. So a worker whose loop simply ends — exhausted
its `total`, or fell off — **defaults to `completed`**. For a *resumable* worker (one extended chunk
of a longer run) that is wrong, and it fails **silently**: a consumer (`ensure`'s read-first /
cross-round completed-check) sees `completed`, returns the short series, and **truncates**.

The Worker actually *knows* the right answer in only two cases — exception → failure,
`control.stop` → resumable — and **guesses `completed`** for every other clean exit. The guess is
wrong precisely for the case the memoizer exists to serve (re-drivable chunks), and it's silent.

## The design (B′)

1. **`completed` is the worker's *sole* terminal claim** — an affirmative, opt-in **boolean**. It is
   the one thing only the worker can know: intrinsic, permanent completion (convergence; a
   fixed-length job genuinely finished). The worker sets it explicitly; **nothing defaults to it.**

2. **The default clean halt makes no claim → projects to `preempted`.** A clean exit the worker
   didn't tag emits `completed=False` with no error; `preempted` is the *unmarked* default,
   `completed` the *marked* claim.

3. **`Stopped.reason` is removed.** The stop body becomes
   `{completed: bool, error: str|null, final_step: int|null}`. The worker has **no field in which to
   type `"preempted"`** (B′ enforced structurally, not by convention), and the free-form "why" is
   gone from the convention body — see *Where the "why" goes*.

**The record is still emitted** (auto, via `__exit__`) even for the default. Its *presence* is the
§7 clean-halt promise — "I halted at a clean, resumable checkpoint" — **stronger than exit-code-0** (a
process can exit 0 mid-step without flushing a checkpoint) and launcher-independent (the
foreign-episode and no-launcher paths rely on it). Presence (clean halt) is **orthogonal** to content
(the `completed` bit): B′ keeps the presence, defaults the bit to `False`.

### The guarantee: total reconstruction from `{completed, error}`

The invariant **`completed=True ⟹ error is None`** is enforced in `Stopped.__post_init__` (mirroring
`Terminated`'s existing `exited`-XOR-`killed` validation) and in the schema. The three terminal states
are then mutually exclusive and exhaustive:

| `completed` | `error` | outcome |
|---|---|---|
| `True` | `None` (enforced) | `completed` |
| `False` | `not None` | `errored` |
| `False` | `None` | `preempted` |

Since `completed=True` forces `error=None`, `error is not None` can only co-occur with
`completed=False` — so **`errored ⟺ error is not None` holds globally**, and the outcome is
recoverable with no free `reason`. (Test `error is not None`, **not** truthiness: a bare
`raise SomeError()` yields `str(exc) == ""`, which must still classify as `errored`.)

### Where the "why" goes (and why this is sound)

- **Domain-specific "why"** ("patience exhausted", "loss diverged", "NaN") → **user channels** (the
  `value` topic or a user-defined topic/artifact). It is workload opinion and does not belong in an
  opinion-free convention body; the substrate already gives workers arbitrary channels for it.
- **The one domain-agnostic finer distinction, `commanded` vs self-budget** → **recoverable from the
  log**, not duplicated. `commanded` ⟺ a `control.stop` preceded this stop; self-budget ⟺ a
  non-`completed` stop with no preceding `control.stop`. The `control.stop` envelope is the
  *canonical* record of commandedness (a self-label could lie), so removal here is de-duplication.
- **`error` stays** because it is *scoped*: the domain-agnostic diagnostic for the one failure
  outcome, surfaced by `RunResult.error`. It is not a general "why" — the general "why" is exactly
  what moves to user-space.

### Recipe: the completion-reason register (2026-07-11 — a shape, no vocabulary)

For a consumer that must branch on *why* (not just the closed `outcome`), the blessed way to record
it — **one recipe; build your own** — is a **value-plane register**, validated by the mycooc adoption
(which built it after the recurring completion-classification bug class this removal otherwise
invites, re-deriving "why" from `outcome + progress`):

- **Shape.** A stepless `value` record: `topic="value"`, a name of your choosing (mycooc uses
  `completion_reason`, the **conventional** name — a viewer/second consumer then has one place to
  look; the *vocabulary* stays yours), body `{value: {reason: <your word>}, step: null, t: now}`.
  `step=null` keeps it out of the step-indexed metric folds (it is a register, latest-by-`seq`, not a
  series point). No wire change — the substrate already carries arbitrary `value` bodies.
- **Writer(s).** The **worker** emits it once before its dying-breath `stopped` (it knows the
  intrinsic why); optionally the **orchestrator** emits it when it force-kills a worker that could not
  self-report (the exogenous why the killed worker never got to write). The value plane is
  author-agnostic, so both are legitimate.
- **Rule 1 — episode-scope the read.** Read only the register *after* `latest_episode().seq`, else a
  resumed run reports the *prior* dispatch's reason before it re-emits. (mycooc learned this; it is
  not optional.)
- **Rule 2 — the terminal owns done-ness; the register owns only *why*.** The register is written
  *before* the terminal, so it is a **prophecy** of a stop the worker's shutdown work (final
  checkpoint, artifact flush) must still complete: **never derive done-ness from the register alone.**
  Pair the read with `peek_terminal` — the terminal proves the stop happened and carries the
  `outcome`; the register only qualifies *why*. The hazard scales with the register→terminal gap: a
  worker that writes the reason early and then does expensive shutdown has a wide window in which a
  register-trusting reader calls it done mid-write. (mycooc's `_complete_from_channel` shortcuts a
  `PATIENCE` register to "done" without the terminal check — the pattern this rule warns against;
  benign there only because its two sends are adjacent, a microsecond window.)

### Why (the orthonormal core)

- **Ask each party only what it uniquely knows.** *Intrinsic completion* — only the worker knows it
  (→ `completed`). *Whether a bound is final or a chunk* — the **driver's** knowledge, which the
  worker often lacks (the same `steps(total=N)` is a fixed length standalone, a chunk under `ensure`).
  So the bound-reaching reason must *default* (to `preempted`), not be demanded of the worker.
- **Fail loud.** A forgotten `completed` → `preempted` → re-drive / loud no-progress, never silent
  truncation.
- **`preempted` is exogenous → it's the consumer's projection.** The bound is externally sourced. The
  worker reports its one endogenous fact (`completed` or not) + the failure diagnostic; the consumer
  names the resumability class.
- **Canonical form.** A free `reason` string is arbitrary, over-specified content in a convention
  body; a single `completed` bit + a scoped `error` is the minimal, closed, non-arbitrary basis. The
  terminal set is flat (3 states) — no term algebra needed.

## The change

- **`Stopped` body** (`payloads.py:52-58`, schema `lifecycle-v0.2.schema.json:48-56`): remove
  `reason`; the body is `{completed: bool, error: str|null, final_step: int|null}`. Validate
  `completed and error is not None` → `ValueError` (`__post_init__`); express the same in the schema
  (`if completed const true then error const null`), keep `additionalProperties:false`.
- **`Worker`** (`worker.py`): `stopped(*, completed=False, error=None, final_step=None)` (idempotent,
  first-writer-wins). `__exit__`: on exception → `stopped(error=str(exc), final_step=…)`; on a clean
  exit → `stopped(final_step=…)` (the default, `completed=False`). The worker claims completion with
  `w.stopped(completed=True)`. Remove `_stop_reason`/`"commanded"`: `tick` returns a stop **signal**
  (truthy ⇒ stop the loop) carrying no reason; `steps` breaks on it; `__exit__` emits the default.
- **`peek_terminal`** (`liveness.py:82-93`): lifecycle tier becomes
  `if s.error is not None: "errored"  elif s.completed: "completed"  else: "preempted"`. The launcher
  tier is unchanged (the no-clean-stop backstop). `RunResult` for the lifecycle tier sets
  `reason = outcome` (the lifecycle tier no longer has a finer-than-outcome label; the launcher tier
  keeps its `exited`/`killed`).
- **Prose:** §7 / `Stopped` "its existence = the run cleanly finished" →
  "its existence = a clean, *resumable* halt; *finished* (`completed=True`) is the worker's opt-in
  claim, otherwise `preempted`."

## Orthonormal-basis check

- **Independence:** removes a free field and a guess; adds a single bit + a validation. No redundancy.
- **Spanning:** the worker can still express everything it uniquely knows (done / failed-with-message
  / neither); domain "why" is expressible in user-space; nothing in-scope is lost.
- **Canonical form:** a closed `{completed: bool} + error` replaces an arbitrary free string; flat,
  minimal, no term algebra.
- **Orthogonality:** record *presence* (clean halt) ⟂ the `completed` *bit* (done) ⟂ `error` (failure
  diagnostic) ⟂ consumer *projection* (`preempted`/`killed`). The `completed=True ⟹ error=None`
  invariant keeps the two content fields non-overlapping.
- **Serendipity:** the worker's terminal vocabulary collapses to one bit; `commanded` de-duplicates
  into the `control.stop` it already implies; the launcher/lifecycle "two viewpoints" split is
  preserved (in-band self-report, independent of exit code).

## Scope / ripple

- `runstate/vocabulary/payloads.py` — `Stopped`: drop `reason`, add `completed: bool`, add the
  `__post_init__` invariant.
- `protocol/lifecycle-v0.2.schema.json` — `Stopped`: `completed` (boolean, required) replaces
  `reason`; the `completed⟹error null` constraint; `additionalProperties:false`. + `tests/test_schema.py`
  conformance (a `completed=True` body validates; `completed=True`+`error` is rejected).
- `runstate/worker.py` — the `stopped`/`__exit__`/`tick`/`steps` changes; drop `_stop_reason`.
- `runstate/liveness.py` — `peek_terminal` lifecycle tier rewrite; `RunResult` docs/`reason` note.
- Tests — `tests/test_worker.py` (default is `preempted`, not `completed`; claim via
  `completed=True`; a `control.stop` now yields `preempted` with no `"commanded"` label),
  `tests/test_liveness.py` (the `{completed,error}` projection; the invariant), `tests/test_memoizer.py`
  fixtures (`_cell`/`chunked` etc. stop emitting `reason="preempted"`/`"completed"` → use
  `w.stopped(completed=True)` only where genuinely final, else the default).
- `examples/reuse/driver.py` — make `train` **genuinely resumable** (don't claim `completed`) so the
  `extend` case resumes `8..19`, fixing the misleading comment the `ensure(until)` review flagged.
- `docs/design-v0.2.md` §7 + `docs/specs/preempted-vs-completed.md` — retune the prose.
- **Downstream (mycooc, separate repo — out of scope here):** emitter sets `completed=True` on
  patience-convergence; chunk / `max_steps` stops use the default; it stops emitting any `reason`.

## Non-goals

- **Not Option A or C** (do not force a declaration) — the bound-reaching reason isn't the worker's to
  know, so it defaults rather than being demanded.
- **No term algebra / closed reason enum** — the terminal set is flat (3 states), captured by one bit
  + the scoped `error`. (Resolves the earlier flagged choice: *remove* `reason` rather than make it
  nullable or close it to an enum.)
- **Claim API:** `w.stopped(completed=True)` is the claim; no separate `w.completed()` sugar (don't add
  surface for a one-liner).
