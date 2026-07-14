# third-party-observer — what the log doesn't say to a party that didn't launch the run

**Status:** LIVING (opened 2026-07-14). A ledger, not a spec: it owns one *persona*
and the diagnosis behind a cluster of findings. Each item graduates to its own spec,
on its own timeline, when it converges — the `launcher-record-identity` shape.

## The diagnosis

> **The log records what a run *did* — but not *when* it did it, nor *what it was
> asked to do*.** Both facts exist. Neither is on the log. The clock lives in a
> backend-private column (`created_at` — written by every backend, exposed by none);
> the target lives in the caller's head (`ensure(until=…)`, injected as a launch
> kwarg). **Neither absence is visible to the party that launched the run** — that
> party knows what it asked for, and its `Watcher` was there when the beacons
> arrived. Both turn fatal the moment a *third party attaches to a run it did not
> start*.

That party — the **third-party, attach-later observer/controller** — is the TUI, the
viewer, a scheduler that outlives its runs, and any operator inspecting a cold log.
It is a persona the protocol has never had, and every item below is a hole it and
only it falls into.

## How this was found

The 2026-07 review's **stage 6** planned to *simulate* this persona ("the TUI/viz
extension-builder as acceptance test"). Instead it was executed adversarially: three
independent red-team agents (protocol/basis lens; build-it-against-real-data lens;
scheduler/bandit lens) were set on a concrete TUI proposal with instructions to
refute it. They killed it — by two independent routes — and surfaced the items below.
**Every claim recorded here was re-verified by hand against the shipped code before
being written down**; the ones that were not reproducible are not here.

Meta-lesson, banked: an *imagined* persona finds the gaps you are clever enough to
imagine. A persona with a keyboard finds the ones you aren't.

---

## 1. The observer clock — a dead run reads as `Running`  *(ship FIRST, alone)*

**→ GRADUATED TO SPEC 2026-07-14: [`../specs/observer-clock.md`](../specs/observer-clock.md)** (DRAFT,
pending the adversarial pass). The fork below resolved to **(a), envelope-v0.3** — and the
spec found two things this entry did not: the bump costs **zero migration** (every real log
already carries the timestamp; the backends have been recording it privately all along), and it
**deletes** `Value.t`, `Started.attached_at`, and the Watcher's arrival-time state. The clamp that
looked necessary is dead too (unbounded silent poisoning); what replaced it is a rule, not a
mechanism: **`seq` orders, `t` measures, and `t` is comparable only within one writer.**

**No liveness record carries a time.** `Envelope` is `(seq, topic, name, request_id,
body)`. `Heartbeat` is `{step, consumed_seq}`. `Stopped` / `Terminated` / `Launched`
carry no clock at all. The one record whose entire job is *"I am alive"* has no time
of its own.

So an observer cannot ask **"when did this run last do anything?"** — and the failure
is not a missing convenience, it is a **wrong verdict**:

```
# a run whose last write was 21 days ago:
Watcher.poll(...)  ->  Running(step=41, beacon_age=9.5e-06)
```

`Watcher._track` seeds `last_heartbeat_at = now()` at **registration**, because the
beacon has no timestamp to consult. Any observer that attaches *after* the fact
therefore paints every abandoned run green — for at least `heartbeat_timeout`
seconds of every session, and forever if the timeout is unset. On the real corpus
that is five mycooc runs, dead 12–21 days, reading live. This is the same failure
family as the forged launcher verdict fixed in `../specs/launcher-record-identity.md`
— a fold confidently reporting a state that is false — one tier up.

It compounds: `live_episode` returns **live** for any handle naming another host
(`resolve` → None → conservatively live), and `resolve`'s own docstring says the
caller should "fall back to heartbeat staleness" — **a fallback no observer can
implement**, because the heartbeat has no clock.

**This is not a bug in the Watcher; it is a persona gap.** Seed-at-registration is
*correct* for the orchestrator that launched the run (it catches a startup crash that
never beacons at all). It is wrong for whoever arrives later.

**A consumer already broke the abstraction over it**: mycooc opens the db with raw
`sqlite3` (`file:…?mode=ro`) and runs `SELECT max(created_at) FROM log` — reaching
past the public API into a backend-private column, having explicitly rejected file
mtime (its own comment: a `--status` pass touches the db/-wal/-shm mtimes, so an
mtime-based age resets to ~0 on every poll). A consumer bypassing the protocol is the
strongest possible evidence of a missing primitive.

**The design fork (this is the whole spec):**
- **(a) Lift the append time onto the envelope** (`envelope-v0.3`). Rejected on its
  face by design §4's **lift-rule** — a field is in the envelope iff the substrate
  *indexes/routes/filters* on it, and nothing routes on time. Re-opening the lift-rule
  is a bigger claim than this item needs.
- **(b) The worker stamps its own beacon** (`Heartbeat.t`, a `lifecycle` bump).
  Consistent with the plane's existing clocks — `Value.t` and `Started.attached_at`
  already are worker-stamped wall-clock. Cost: it is the *worker's* clock, so
  cross-host staleness inherits clock skew.
- **(c) An opt-in backend capability** — `freshness()` / `last_activity()`, the
  `EpisodeHolder`/`EpisodeProbe` pattern in `channel/base.py`: isinstance-detected,
  resolved at the Watcher's boundary. Reads the substrate's *own* append clock (no
  skew, one writer of truth), needs **no convention bump at all**, and is what the
  2026-07 review already predicted ("`created_at`: don't lift; future = a
  `freshness()` capability"). Cost: a capability is not a *record* — it answers about
  the log's head, not about any past envelope, so it cannot date a cold log's
  history, only its last activity. (Ask whether that is in fact all this persona
  needs.)

The choice between (b) and (c) is the three-clocks question wearing a new hat, and it
decides whether this is a convention bump or a capability. **Do not batch it with
item 2** — conventions version on independent timelines, and this one is small,
urgent, and possibly zero-primitive.

**Prior art — and the lesson in it.** This gap was already filed, on 2026-06-23, as
[wal-liveness-mtime](wal-liveness-mtime.md): a consumer deriving freshness from the
`.db` file mtime read *stale on a healthy run* (WAL puts commits in the sidecar; the
main file's mtime only moves on checkpoint — measured: 306 s stale while the log was
1 s old), and it proposed exactly option (c) — *"expose a first-class `last_write_ts`
/ freshness helper … a `freshness()` that reads max(created_at) would make the right
thing the easy thing."* It was graded **"[minor · observability]"** and left open for
three weeks.

That grade was wrong, and *predictably* wrong: to the persona then in view (an
orchestrator watching its own live run) a bad clock is a **cosmetic** defect — a pulse
that sawtooths. To the persona that had not yet arrived, the same missing clock is a
**wrong verdict** — a dead run reported as running. **Severity is persona-relative,
and a gap filed under the only persona you have will be under-graded for the persona
you don't.** Worth remembering the next time something is filed as "minor · cosmetic".

## 2. The run's TARGET is not on the log — the missing basis vector

The run's target exists **only** as the caller's `ensure(until=…)` argument, injected
into the worker as a launch kwarg via `target_key="up_to"` — a hack that reaches into
the worker function's *signature*, and which `launch_producer` can only honor for
`{"step": N}` (it raises on any other condition). No convention body carries it:
`Launched` is `{handle, status}`; `Started` is `{handle, attached_at}`.

**So the log answers *how far it got* (`progress`) and never *how far it was asked to
get*.** Consequences, all real: a launch-ignorant party cannot say "run to N"; a
controller that dies cannot reconstruct any run's contract; a viewer cannot show
progress-toward-target; and `ensure_served` has no progress coordinate with which to
refuse a doomed relaunch.

Two adversaries working different lenses converged on this independently. It is the
one item here that is a **missing basis vector**, not a missing surface.

**Candidate canonical form:** `control.target { until: <Condition> }` — a **register**
(latest-wins via `channel.latest`, no positional fold, no lease, no counter-record),
**worker-directed** so `control.*` keeps its single audience, carrying exactly one
concern (*how far*) and no cmd/env (the launch recipe stays off the log — see the
rejected alternatives). Discharge is **derived, not recorded**: satisfied ⟺
`progress + 1 >= N`, the fencepost already documented on `observables.progress`.

**Serendipity, if it lands:** the daemon's `until`→`up_to` translation vanishes;
`memoizer`'s `target_key` hack dies and `ensure` generalizes past step-only targets;
`steps(start=k)`'s `k` becomes derivable from `progress`; the viewer gets
progress-toward-target for free; and **the crash-loop guard falls out** — "target
unmet ∧ frontier did not advance ⟹ do not relaunch" is exactly `ensure`'s existing
`NoProgressError` logic, which `ensure_served` cannot express today.

**The open question that decides its fate:** `control.stop{from: {step: N}}` and
`control.target{until: {step: N}}` are near-duals — *stop at N* vs *run to N*. Either
the target **subsumes** conditional-stop (which would **delete** a primitive: a basis
*reduction*, and a strong argument for shipping it) or the two overlap and it violates
orthogonality. Settle that before writing code. Full spec + adversarial pass.

### Rejected alternatives (both refuted with reproductions — do not revisit blind)

- **Demand-via-`control.subscribe`** (a step-conditioned lease as durable demand, with
  a daemon translating its `until` into the launch target). **DEAD, in both spellings
  and in opposite directions.** A *recurring* sub (`every` + `until: {step: N}`)
  **never expires** — `steps(N)` yields `0…N-1` and the expiry gate is `step >= N`, so
  no counter-record is ever written and demand outlives a *clean completion*: a daemon
  would relaunch a finished run forever (verified). A *bare one-shot* (`until: {step:
  N}`) **fires once and evaporates at step 0**, so a crashed run's demand is gone and
  the daemon never revives it — the one thing it exists for (verified). `until` bounds
  *firing*, not *membership*: the algebra cannot express "run to N", and was never
  meant to. It is also an **audience conflation** — the worker reads the message as a
  sampling schedule while the daemon reads it as a launch order.
- **`control.launch` carrying the launch recipe** (cmd/env). Rejected on the meta-
  constraint: it bakes process management into the protocol ("the library transports
  messages, not processes") and turns any channel with write access into a
  remote-code-execution surface. The recipe stays in a trusted, off-log table. *(The
  instinct that a **message** is needed was right; the payload was wrong. It is the
  target, not the recipe.)*
- **A standing daemon as the fix.** `ensure_served` gates on `live_demand ∧ no live
  episode` and **nothing else** — no failure gate (unlike `ensure`, which has both
  `RunFailedError` and `NoProgressError`). Promoting the caller-invoked recipe to an
  unattended daemon converts that into a **crash-loop generator** at poll cadence. And
  the reason `lazy-launch.md` felt safe shipping *no* flap policy is that time-leases
  are voided at episode boundaries — a bound that step-keyed demand escapes **by
  construction**. Any daemon needs item 2's progress coordinate first.

## 3. Run enumeration — there is no surface at all

Nothing in the library enumerates runs (no `list_runs`, no `SELECT DISTINCT run_id`,
no directory walk). `open_channel` locates *a* run by id; the Channel surface is
per-run. But a viewer's first screen is *"what runs exist?"*.

Worse, the answer is **filesystem-shaped and `PostgresChannel` is not**: every
Postgres op is scoped `WHERE run_id = %s`, there are no roots, no directories, no
symlinks. Discovery has no Postgres *shape*, not merely no Postgres implementation.

And the on-disk reality refutes the tidy story: `../specs/store.md` Recipe 1
(content-addressed homes + thin cells with pointers) holds for **~25% of mycooc's
2,564 real cells** (the rest are pre-Phase-7 legacy with no channel at all) and for
**0% of translation** (no cells, four roots, flat `runs/*.db`). A TUI cannot assume
Recipe 1; discovery must be a pluggable resolver, and the app — not runstate — should
own the layout adapters.

**Likely core shape:** an opt-in capability Protocol (`list_runs()`), the *third*
instance of the `EpisodeHolder`/`EpisodeProbe` pattern — which is itself a signal that
"opt-in, isinstance-detected backend capability" is a basis vector worth naming
explicitly rather than re-deriving each time.

## 4. There is no read-only open

`open_channel` **creates**: opening a nonexistent run fabricates `<rid>.db`, `-wal`
and `-shm` (verified). For a viewer that means resolving a stale/GC'd pointer
**manufactures a phantom empty run** and pollutes a content-addressed store — and that
the API **cannot distinguish "no run" from "empty run"** without stepping outside it to
`os.path.exists`. (mycooc, again, went around: raw `sqlite3` with `mode=ro`.) Wanted:
`open_channel(..., create=False)`, or a read-only mode.

## 5. The folds have no cursor

`value_series(channel)` and `progress(channel)` take no `after=`. But the measured
scale plan in `visualization-story.md` *prescribes* incremental folding ("carry
per-run cursors and fold plot state incrementally over `read(after=cursor)`"), and the
one function that owns the value-decode rules, `_value_points`, is **private** — its
docstring: *"the designated escape hatch if a custom-fold consumer ever appears; until
then the bring-your-own-fold seam is the substrate itself."* **The custom-fold consumer
has appeared.** Either publish a cursored `value_series(channel, after=…)`, or bless
`_value_points`; a viewer restricted to the public API must otherwise re-implement the
decode rules and drift from them.

## 6. A third party cannot stop a run safely

The only public stop is `channel.send({}, topic=Topic.CONTROL_STOP)`. Sent to a run
that is **already dead** — which item 1 guarantees the TUI will believe is alive — it
becomes an **undischarged stop with no retraction verb** (`control.unstop` was
rejected as A7 in `../specs/stop-discharge.md`), `await_consumed` returns
`TimeoutError` rather than a refusal, and **the next legitimate episode drains it**,
halts at step 0, and the run's recorded `progress` **regresses** (2 → 0, reproduced).

The "stop-while-down is honored by the next episode" rule is a *deliberate, defensible*
choice for a caller who knows the run is down. It is a landmine for a UI whose status
column lies. Wanted: a public predicate for **"will a stop sent now be served by the
live episode, or armed for a future one?"** — not derivable today without re-deriving
liveness, which item 1 says an observer cannot do.

---

## Two plain bugs, fixable immediately and independently

- **`examples/reuse/driver.py` records the wrong checkpoint.** It writes
  `{"next": up_to}` *after* the loop — the **target**, not the **frontier** — so a run
  cut short by a cooperative stop records a checkpoint it never reached. Reproduced:
  park at step 4 → checkpoint claims 8 → resume starts at 8 → runs zero steps →
  `NoProgressError`. **Our own canonical "resumable" example does not survive
  preemption**, and it is the pattern users copy. *(Fixed 2026-07-14.)*
- **`visualization-story.md`'s poll-cost numbers are ~3× optimistic.** Re-measured on
  400 real translation logs, channels held open: `peek_terminal` **30.1 µs/run**
  (documented: 5–17), `progress` 16.8, `last_seq` 6.8 — a full status row is **53.6
  µs/run**, i.e. **~64 ms/frame for 1,200 runs**, not the documented ~20 ms. Still
  viable at 1 Hz, but it is 5–7% of a core, not "free". Also unrecorded: a
  `SqliteChannel` costs **3 fds** (1,204 fds for 400 channels), so a viewer **EMFILEs
  at ~340 runs** on a default 1024-fd system — it needs an LRU channel pool.
  *(Corrected 2026-07-14.)*

## Ship order

1. **The observer clock** (item 1) — alone, first, own timeline. Small, urgent, a
   wrong-verdict bug, and every screen is downstream of it.
2. **The run's target** (item 2) — full spec + adversarial pass; it may delete a
   primitive, so it gets the treatment.
3. **Enumeration / read-only open / cursored folds** (3–5) — each small; take them as
   the build demands them, so the demand is evidence rather than speculation.
4. **Third-party stop safety** (item 6) — with the control half, after item 1 (a safe
   stop needs a true status).

## Out of scope here

The data-plane / viewer / artifact protocols (`visualization-story.md`) — those remain
a **separate project**, and nothing above changes that. This ledger is only about what
*runstate itself* fails to tell a party that did not launch the run.
