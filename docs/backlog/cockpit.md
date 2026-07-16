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
| **4 — no read-only open** | **UNSETTLED** (see below). My refutation of it was itself refuted; the candidate is now `create=False`, graded **minor**. Do NOT read this row as a win — it is the specimen. |
| **6 — third-party stop** | **Live on day one.** May already be dissolved by item 1 — the cockpit finds out. |
| **2 — the target** | The missing progress-bar denominator. If that is the top annoyance in real use, the rework has its evidence. If not, it was speculation. |

Two of five reshaped on build evidence (3, 5); item 4 is UNSETTLED and its earlier "refuted" mark was motivated reasoning — see below. The honest score is two, not three, and the
argument for building before speccing rests on the two that were earned.

## Item 4 — my refutation was wrong; the candidate is `create=False`

**Superseded 2026-07-16 by a two-lens adversarial pass.** An earlier draft of this
section argued for **lazy creation** on an ontology claim. **That argument is refuted,
and the record of how is worth more than the conclusion** — it is the second time in one
day that solo reasoning produced a confident, wrong design (cf. `../specs/control-target.md`).

### What the argument was, and what killed it

It ran: *(M1)* open is side-effect-free on Postgres and memory, so create-on-open is a
**SQLite artifact leaking through the abstraction, never part of the Channel contract**;
*(M2)* under an append-only log **a run IS its log**, so never-created and zero-envelopes
are **the same state** — item 4's "cannot distinguish no-run from empty-run" is not a
defect but opinion creep; *(M3)* therefore `create=False` would *codify the leak* (and is
a **no-op on Postgres — the tell**), and **lazy creation** is right because it adds no API.

Every move fails, on verified evidence:

- **M1 — the empirical base is false.** `channel/base.py`'s *first paragraph* says the
  opposite of what M1 asserts: *"A Channel is a handle on a run's shared topic log — not
  the log itself. **The log is the durable, shared thing (a SQLite file; the
  process-global in-memory registry)**."* **The contract defines the log AS the
  container.** Create-on-open is not a leak *through* the abstraction; it is the
  abstraction's stated ontology — and M1 was asserted without reading it. Further:
  memory is **not** side-effect-free (`_MEMORY_LOGS.setdefault` mints an entry, `close()`
  is `pass`; measured: **1,000 pure opens+closes → 1,000 resident entries**), and
  Postgres's silence is **not testimony** — it has no per-run container at all, so it is
  structurally incapable of exhibiting the behavior in either direction.
- **M2 — an equivocation on "log"** (the envelope sequence vs. the durable container the
  contract names). Existence and non-emptiness come apart in at least four places *in
  this repo*: `tests/test_memoizer.py`'s store pin asserts an **exact set** of `.db`
  files found by `rglob` (a phantom fails it — the suite already treats phantom-creation
  as a defect); `store.md`'s dispatcher **mkdirs the home before opening the channel**
  (three ordered states); the sqlite backend already distinguishes, arbitrarily (a
  missing **root** raises, a missing **run** creates); and — fatally — **the cockpit's own
  Resolver answers "what runs exist" by `glob`/`readlink`, never by `last_seq()`**.
  Existence came apart from non-emptiness *in the very unit that motivated the argument*.
- **M3 — the inference is backwards.** "A no-op on Postgres is the tell" is refuted from
  inside the same ABC: `MemoryChannel.close()` is `pass` and `last_seq()` is `len()` on
  memory. **No-ops where there is nothing to do is what a uniform contract looks like.**
  Postgres's no-op is the **proof**, not the tell: Postgres has no container, so it
  *already behaves as `create=False`* — the flag is that contract written down.

### What the build lens found (it disagreed with the argument lens — read both)

A second adversary **prototyped lazy creation** and recommended shipping it bundled with
fixes. It is kept here because its evidence is what actually settles the question, and
because the two verdicts disagreeing is the most informative outcome available.

**It refuted one over-claim:** lazy does **not** corrupt the birth CAS. Measured — **480
claimants across 40×12 and 12×15 process races on a nonexistent run: `winners=1` every
round, `0` spurious raises, byte-identical to eager.** Schema creation does not race.
Also verified sound: all seven observables answer identically on a never-written run;
WAL/`busy_timeout`/J3 intact; the attach path fine over 25 real spawns; and a **12.6×
speedup** on a missing run's status row (94.3 µs → 7.5 µs).

**But it confirmed the orphan with a live repro, and named what the argument missed:**

> **Eager creation is doing *two* jobs — creating the file, and preflighting writability.
> The proposal names only the first.**

`LocalLauncher.launch` is ordered `open_channel` (:258) → `Popen` (:270) → `send(Launched)`
(:272), and it *must* Popen first (it needs the pid). Today the eager open proves the root
is writable **before the child exists**. Under lazy, on an existing-but-unwritable root:
the child spawns, `send` raises, `_handles.append` never runs — **a live subprocess with
no `launcher.launched` record, invisible to every fold, that `reap()` can never see.**
Its own mitigation is partial by its own admission: reordering the append makes the orphan
*reapable*, but *"it still runs; full mitigation needs the launcher to kill on
send-failure."* Eager prevents the spawn outright.

It also found a **new** major: under lazy, a **typo'd or unmounted root reads as
"everything is empty"** — a full cockpit table of healthy-looking empty runs. *A fold
lying: this ledger's signature bug.* Repairable with one `isdir` stat — but note what
that repair is: **re-adding, by hand, a check eager was doing for free.**

### The decision: `create=False`, and why the disagreement resolves this way

**Feasibility is not desirability.** The builder proved lazy *can* work; the arguer showed
it *should not be the choice*. The asymmetry settles it:

> **The phantom is a READER's problem. Lazy fixes it by changing WRITER semantics** —
> deleting a preflight the reference launcher depends on, breaking a real consumer idiom
> (mycooc's `test_open_cell_channel_finds_db` calls `open_channel` *purely to materialize
> a `.db`* for a glob), and needing a compensating check to stop a missing root from
> lying. **`create=False` costs writers nothing** (the eager default is unchanged) and
> gives the reader an opt-out — in ~10 lines, in the form POSIX (`O_CREAT`) and SQLite
> (`mode=rw`, verified: raises on an absent file, creates nothing) already converged on.

Lazy's one advantage was "no new API" — which is **counting signature width as basis
elegance**. The rubric measures primitives, not parameters.

**Severity, honestly: MINOR — item 4 oversold itself, and so did I.** Its headline ("a
stale/GC'd pointer manufactures a phantom") is **not reproducible under Recipe 1** — a
whole-home GC removes the *directory*, so the open raises. The phantom needs a **flat**
root (translation's shape) or a mkdir'd-but-never-ensured home. A `glob`-resolved cockpit
may never hit it at all. **This is the evidence-based refutation that was available all
along, and my ontology argument crowded it out.**

**Still deferred to the build**, per this document's own rule — minor, and the demand
should be evidence.

### The motivated-reasoning tell, recorded on purpose

Items 3 and 5 were refuted by **evidence from the build** (the app owns discovery; no
plots ⇒ no cursors) — earned. **Item 4 alone was refuted by an assertion made in one
head**, and it is precisely the item that got the count to *"three of five reshaped —
which is itself the argument for building before speccing."* **The conclusion was doing
the premise's work.** Worse: the experiment table above scored item 4 "refuted" while this
section said "not settled — deliberately." *The ledger was marked before the pass it was
asking for, in the same document.* Left here as a specimen.

### `mode="r"` — REOPENED as its own question (a real gap, found in passing)

This document dismissed a read-only mode as "unnecessary once open creates nothing."
**Refuted, and independently of everything above.** Verified under *both* eager and lazy:
reading an **existing** run whose root is read-only raises `attempt to write a readonly
database` — because the read path connects read-write (WAL conversion + `executescript`
are writes). **A viewer on an archive mount cannot read at all today.** `mode=ro` reads it
fine, which is exactly what mycooc's `run_experiment.py`'s `file:{db}?mode=ro` was
reaching for.

So **create-or-not** and **writable-or-not** are orthogonal axes, and neither proposal
addresses the second. The cockpit plausibly wants both — the table opened read-only, the
stop button **explicitly escalating** to a writable handle, which makes every write an
auditable act rather than a latent capability. **Do not fold this into item 4.**

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
- **`mode=ro` — a real, independent gap** (above): a viewer on a read-only mount **cannot
  read an existing run at all today** (the read path connects read-write). Orthogonal to
  item 4; do not fold them. Decide whether the cockpit opens read-only by default and the
  stop button **explicitly escalates** to a writable handle.
- **Does `create=False` ever get demanded?** A `glob`-resolved cockpit never opens a run
  it did not find, so the phantom may be unreachable in practice. Take it if and when the
  `explicit`/`cells` adapter hits a stale rid on a flat root. *(Second consumer already
  waiting: `sweep.py`'s `with launcher.open_channel(v.run_id) as ch:` — the library's own
  pure-read open.)*
- **Does the cockpit want `run_epoch`?** ("started 3 days ago, ran 2 h" needs the epoch;
  `last_activity` shipped without its twin, and the promotion was deferred for want of a
  second consumer — the cockpit would be it.)
