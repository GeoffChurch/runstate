# cockpit — a control-plane TUI for runstate runs (a SEPARATE project)

**Status:** CONVERGED design 2026-07-16 (supersedes `webapp-viewer.md`, deleted — a
v0.1 artifact describing `to_orchestrator` / the `messages` table / `role="orchestrator"`
/ `FileChannel`, none of which survived the v0.2 rework). **The project does not exist
yet**; this entry is its design until the repo does, then it becomes a pointer.

The gate `visualization-story.md` set — *"revisit when a viewer audience exists"* — is
met: the audience is the owner, running mycooc + translation, who already hand-rolled a
`--status` table in `run_experiment.py`. The cockpit is that table's successor.

## What it is

> **A control-plane cockpit: a TUI that answers "what is happening / what happened"
> across groups of runs, and lets you act on one. No plots.** Its own repo, depending
> on runstate.

**Home:** `~/src/runstate-tui` (owner, 2026-07-16) — a sibling of `runstate`, not a
subdirectory: the separation is the point (`visualization-story.md`'s *"split by
**project**, not by file"*), and a sibling repo is what forces the cockpit to consume
runstate as a *dependency*, through its public API, exactly as a third party would. That
is what makes the API-purity rule below real instead of aspirational.

## Why this shape — the identity, and how it clears the bar

CLAUDE.md sets the bar: *"only ship this as a coherent protocol story, not 'another
tracking tool.'"* The cockpit clears it **by construction** rather than by discipline:

- It shows **only what runstate uniquely knows** — liveness and the terminal verdict
  (`peek_terminal`), the step frontier (`progress`), freshness (`last_activity`), the
  episode boundary, undischarged stops, live demand.
- It does **the one thing no tracker can: act** — send `control.stop`, and watch it
  discharge.
- **Plots stay wandb's job.** Dropping them is not a scope compromise; it is what makes
  the story coherent. It also **dodges the data-plane protocol** (rich values:
  histograms/images/tensors) — the largest speculative surface in
  `visualization-story.md`, and the one most likely to die the way `control.target` did.

Differentiator, stated plainly: it works **on a cold log, with no daemon, no SaaS, and
no instrumentation** — because the log already holds everything it renders.

**Read the bar as a *shipping* bar, not a *building* bar.** "Only ship a coherent
protocol story" does not mean "design three protocols first" — that is precisely the
speculation that killed `control.target` in a day. Build against what exists; let the
protocol be *extracted* from real friction; ship it when it is coherent. This is also
the ledger's own instruction for its items 3–5: *"take them as the build demands them,
so the demand is evidence rather than speculation."*

## The design rule (the conceit — do not relax it)

> **The cockpit may use ONLY runstate's public API. Every time it can't, that is a
> FINDING — not a workaround.**

No raw `sqlite3`. No `?mode=ro` side-doors. No reaching for the private
`_value_points`. When the public API cannot answer, **file it and stop**; then decide
whether runstate is missing a primitive or the cockpit is asking wrong.

Rationale: this makes the cockpit **the review's stage 6 with a keyboard, permanently
and mechanically** — no in-the-moment discipline required. Both real consumers broke
this rule under pressure (mycooc's `SELECT max(created_at) FROM log`, reaching past the
API into a backend-private column), and the ledger's own verdict on that is *"a consumer
bypassing the protocol is the strongest possible evidence of a missing primitive."* The
rule converts that evidence from a shrug into a build error.

## Architecture — three units, each testable alone

1. **Resolver** (`groups.toml` → `[(run_id, label)]`). Owns the **layout adapters**:
   `glob`, `cells`, `explicit`, later `postgres`. Knows nothing about status or rendering.

   **The resolver supplies the LABEL, and this is load-bearing, not cosmetic.** mycooc's
   `run_id`s are content-addressed hashes — a table of them is unreadable, and triage
   ("which variant won?") is impossible. Runs have **no name on the log**, deliberately:
   *"tags/names have no home — by design so far."* The obvious reflex is to demand a
   run-metadata/name protocol; **don't.** A name is a *layout* artifact — mycooc's cell
   path already **is** the label (`algo_books/lam=0.3`), and translation's filename is.
   The adapter that located the run already knows what to call it. So the label rides
   with discovery, which the app owns, and **runstate gains nothing** — the same split,
   holding under pressure at the first place it was tested.
2. **Status fold** (`run_id` → a row: verdict, progress, age, episode count,
   undischarged stops, live demand). Pure over runstate's observables; no UI. **Owns the
   LRU channel pool** — non-negotiable, see Scale.
3. **TUI** (table + drill-down + act). Renders rows; sends `control.stop`.

**Data flow:** `groups.toml → resolve → [run_id] → status fold (pooled) → table`,
refreshed at **1 Hz**.

## Discovery: the app owns it — and that may refute a ledger item

A group is **a resolver expression, not a frozen list** — a sweep grows as variants
launch, so a static list is stale on arrival; a re-resolved expression picks up new runs
for free.

Sufficiently general, checked against the real corpus: a **glob** covers translation's
flat `runs/*.db` (4 roots, no cells); a **cells** adapter covers mycooc's Recipe-1
homes-and-pointers (~25% of its 2,564 cells; the rest are pre-Phase-7 legacy with no
channel at all); an **explicit** id list covers the rest. It even covers the case the
ledger said had *"no Postgres **shape**, not merely no Postgres implementation"* —
because `SELECT DISTINCT run_id` is just another resolver. **The app is allowed that
opinion; runstate is not.** That is the split working, and it is exactly the ledger's own
conclusion: *"discovery must be a pluggable resolver, and the app — not runstate — should
own the layout adapters."*

**Consequence: runstate may never need item 3 (`list_runs()`).** The build refutes it
rather than demands it.

## "Watch live" vs "triage a sweep" — resolved

They are **not two screens**. Same data (a table of runs with status, progress, age),
two questions: live sorts by activity and wants a pulse; triage sorts by verdict and
wants comparison. That is **a filter and a sort, not a mode**. "Inspect one run deeply"
is the drill-down. So the app is *one table + one detail view*.

## Scope — v1

- **Table:** label, group, status, progress, age. *(Label, not run_id — see the
  resolver. The rid is drill-down detail, not a column you read.)*
- **Drill-down:** episodes, undischarged stops, live demand, raw envelope tail.
- **One action:** stop.
- **Done =** the owner reaches for it, unprompted, to answer "what is happening."

**It will NOT replace `run_experiment.py --status`, and should not try.** That table is
**experiment-aware** — variants, phases, the ladder, patience — and those are workload
opinions the cockpit is forbidden to hold (they are exactly the "step/loss/phase/
experiment" CLAUDE.md keeps out of the protocol). The cockpit answers the **run-layer**
question across groups and repos; `--status` answers mycooc's experiment-layer question
inside one. They coexist, and a cockpit that started growing a `phase` column would be
the first sign it had lost the plot. *(The honest risk this creates: if the run layer
alone is not useful enough to reach for, the cockpit fails — and that failure is itself
the finding, because it would mean the interesting state lives in the workload, not the
protocol.)*

## Scale constraints (measured 2026-07-14; the design MUST respect these)

- **~54 µs/run** for a full status row (`peek_terminal` 30.1 + `progress` 16.8 +
  `last_seq` 6.8). A 100-run group ≈ **5 ms/frame** — free at 1 Hz. The alarming
  **64 ms/frame** figure only bites at 1,200 runs, which grouping avoids.
- **A `SqliteChannel` costs 3 fds** → a viewer **EMFILEs at ~340 open runs** on a
  default 1024-fd system. **The LRU pool is not optional.**
- **Refolding per frame is not viable** (O(N), ~3.2–3.9 µs/envelope; a 10⁶ log is ~1.9 s
  and materializes ~0.77 GB transient). **v1 sidesteps this entirely by having no
  plots** — which is why item 5 (cursored folds) is *deferred, not demanded*.

## The experiment — what the build will confirm or refute

| Ledger item | Prediction |
|---|---|
| **3 — enumeration** | **Refuted.** The app owns discovery; the core never needs `list_runs()`. |
| **5 — cursored folds** | **Deferred.** It was only ever a *plotting* requirement; v1 has no plots. |
| **4 — no read-only open** | **Proposed fix refuted; replacement candidate below.** |
| **6 — third-party stop** | **Live on day one.** May already be dissolved by item 1 — the cockpit finds out. |
| **2 — the target** | The missing progress-bar denominator. If that is the top annoyance in real use, the rework has its evidence. If not, it was speculation. |

Three of five reshaped rather than implemented — which is itself the argument for
building before speccing.

## Item 4 — the proposed fix is refuted; the candidate is lazy creation

**Verified 2026-07-16:** a pure open of a nonexistent run creates three files.

```
before:     (empty)
after open: ['nonexistent-rid.db', 'nonexistent-rid.db-shm', 'nonexistent-rid.db-wal']
last_seq:   0    read(): []
```

But **Postgres creates nothing on open** (every op is `WHERE run_id = %s` on a shared
table) and memory only makes an ephemeral dict entry. So:

> **`open_channel` is side-effect-free on two backends and pollutes the disk on the
> third. The create-on-open is a SQLite artifact leaking through the abstraction — it
> was never part of the Channel contract.**

That kills the ledger's `open_channel(..., create=False)`: a flag would **codify the
leak** — a knob suppressing a side effect the contract never promised, on the one
backend that has it, and a no-op on Postgres. The no-op is the tell.

**And item 4's stated defect is not the real one.** It complains the API *"cannot
distinguish 'no run' from 'empty run'."* Under an append-only topic log **a run IS its
log** — zero envelopes and never-created are *the same state*, and `last_seq() == 0`
already says so uniformly on every backend. The model has no such distinction, and
asking the API to invent one is opinion creep. **The real complaint was always the side
effect**, written up as an ontology gap.

**Candidate: lazy creation — SQLite does not touch disk until the first write.** Open
becomes side-effect-free everywhere; the backends **converge** instead of diverging;
`last_seq() == 0` uniformly means "nothing here"; the phantom-run pollution of a
content-addressed store disappears; and **no new API exists at all** — no flag, no
second verb, no mode. It *deletes* an inconsistency rather than adding a surface — the
shape of item 8's fix, which deleted the reap discipline rather than extending it.

Alternatives, recorded: a **`mode="r"` read-only open** gives the cockpit a can't-write
*guarantee* (defense-in-depth for the API-purity rule) but is unnecessary once open
creates nothing, and is another mode flag. **Two verbs** (`create_channel` /
`open_channel`) is the textbook split but breaks every caller for a distinction the model
says does not exist.

**Not settled — deliberately.** Lazy creation has a visible cost (errors move from
open-time to first-write-time, a worse place to learn your root is unwritable) and may
have callers depending on eager creation that nobody has looked for. **This analysis was
done in one head, which is exactly what produced `control-target.md`.** It gets the
adversarial treatment before it is believed, and the cockpit's resolver hitting a stale
pointer for real is the thing that tests it. **Decide it then, not now.**

## Item 6 — the stop button's hazard (open)

A stop sent to a run that is already dead becomes an **undischarged stop with no
retraction verb** (`control.unstop` was rejected as A7 in `../specs/stop-discharge.md`);
the next legitimate episode drains it, halts at step 0, and the run's recorded `progress`
**regresses (2 → 0, reproduced)**. Against mycooc/translation's real content-addressed
logs that is damage, not a UI annoyance.

The rule *"stop-while-down is honored by the next episode"* is deliberate and defensible
for a caller who **knows** the run is down — it was a landmine only for *"a UI whose
status column lies."* **Item 1 shipped, so the status column no longer lies**, and item
6's stated blocker (*"not derivable without re-deriving liveness, which an observer
cannot do"*) is at least partly dissolved. Whether the residue is real — a foreign-host
handle still reads conservatively-live — is a **design question for the build**, not a
hand-wave.

## Non-goals

- **Plots**, and therefore the **data-plane protocol** (rich values). Not v1, and not
  the identity.
- **The viewer-discovery and artifact-storage protocols** — speculative until the
  cockpit demands them.
- **Discovery inside runstate** (the app owns layout).
- **A daemon.** The cockpit is invoked, not standing; `A3` in `../specs/control-target.md`
  explains what an unattended relauncher costs.

## Open questions

- **Item 6's safety predicate** — is there a public "will a stop sent now be served?" and
  is it still needed post-item-1?
- **Lazy creation's real cost** (above) — and whether `mode="r"` earns its place anyway
  as the API-purity rule's enforcement.
- **Does the cockpit want `run_epoch`?** ("started 3 days ago, ran 2 h" needs the epoch;
  `last_activity` shipped without its twin, and the promotion was deferred for want of a
  second consumer — the cockpit would be it.)
