# The migration implementation round (log format 0.3.0 → 0.4.0) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** Implement `commits-and-lineage.md` §7 as the retained step `v0_3_0_to_v0_4_0`, run it with the pure
0.4.0 reads over every real log available, and resolve everything it finds into a named rule, a golden log or
a spec change. Then the owner can approve revision 2 (review §12, item 15, ruling 12a) and #59 merges.

**Architecture:**
- **Stage 1, the wire and the runner.** It adds the 0.4.0 convention schemas to `protocol/` beside the 0.3.0
  ones, and the runner learns that exactly one step renumbers.
- **Stage 2, the step.** A pure transform over rows that carries its own copy of 0.3.0's semantics, in §7's
  six passes. It is not registered in `FORMATS` or `STEPS`: the format changes only at the end of layer 2's
  implementation.
- **Stage 3, the pure reads.** The 0.4.0 reads that P1–P4 need go in a new module, `runstate/lineage.py`: the
  head, the lineage, the counts, the series, the verdict and the stop fold. These are the pure halves of §5;
  the held, incremental folds come with layer 2.
- **Stage 4, the measurement.** A read-only corpus runner takes each real log through the shipped 0.2.0 → 0.3.0
  step, then the new one. It validates the output against the 0.4.0 schemas and checks P1–P4 per run, with
  each differing cell attributed to a named rule.
- **Stage 5, the resolution.** Stage 5 runs the census of lone stops, resolves the 41 cells of run 625dda74
  and every other unattributed cell, and writes the round up as review item 16 for the owner.

**Tech Stack:** Python 3.12, sqlite3, jsonschema (tests only, as now), psycopg 3 for the Postgres runner tests,
pytest, mypy `--strict`, black. No new runtime dependencies.

**Specs (read before any task):**
- `docs/specs/commits-and-lineage.md`: §7 above all; §2 and §3 for the wire; §5 for the reads; §10 for the
  tests.
- `docs/specs/log-formats.md`: §6 on steps.
- `docs/review-2026-10-04-commits-and-lineage.md`: §11.3 and §12, items 10, 13, 14 and 15. These are the
  adopted migration rules and the figures they replace.

## Global Constraints

- **Environment.** Use `./.conda/bin/python -m pytest`. In a worktree, `python -m pytest` from its root picks
  up the worktree's code (`pythonpath = ["."]`).
- **Gates at every commit:** `black --check runstate/ tests/ scripts/`, `mypy --strict runstate`,
  `pytest tests/`. The pre-commit hook runs them. Postgres tests skip without `RUNSTATE_TEST_PG_DSN`; Task 2
  needs them, so start the throwaway server `CLAUDE.md` describes.
- **The branch.** Work on `impl/migration-round`, cut from `docs/layer2-design-converge` (#59's head). Its PR
  merges after #59. Push after each task; the repo is private.
- **The format does not change in this round.**
  - `LOG_FORMAT` stays `"0.3.0"`, `FORMATS` and `STEPS` gain nothing, and the package version stays.
  - log-formats §2 rule 3: a format changes in one commit, together with the version bump. That commit is
    layer 2's last.
  - The 0.3.0 schemas stay in `protocol/` until then. The 0.4.0 ones sit beside them, and
    `tests/test_schema.py` keeps validating 0.3.0.
- **The step carries its own semantics.** It imports nothing from the package but `Row` and `MigrationError`,
  as the 0.2.0 step does, and an AST test enforces it. It never imports `runstate.lineage`.
- **Consumer repositories are read-only.**
  - The corpus is opened with `sqlite3.connect("file:...?mode=ro&immutable=1", uri=True)`, or read from a
    copy in the session scratchpad.
  - Nothing is written under any consumer's tree.
  - Files are counted, not paths: resolve symlinks and deduplicate by inode, since mycooc's cell→home
    symlinks visit every log twice.
- **Every difference gets a named rule, a golden log or a spec change, never a tolerance** (review item 10(e)).
  A count may attribute or defer. It never refutes a rule; that takes an argument.
- **Present-nullable fields and `additionalProperties: false`** in every new schema.
- **Commits** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

Each of these inputs is one a reasonable person expects to work, and the tests named pin it:
1. **Renumbering remaps every seq-valued field.** Every `claim_seq` (a `died`'s included), `consumed_seq`,
   `parent`, `values` entry, `resume_from` and `bound.claim_seq` must name the right record after M6. A missed
   field still holds an integer of the right type, pointing at the wrong record. Task 6 checks that every
   such field names a record of the expected topic, in every golden log; Task 11's runner checks it across the corpus.
2. **A claim's first commit parents in claim order (M1), never by step nearness.** Task 5's
   displacement-parent history fixes this: the draft's step rule picked a displaced worker's branch in 9 of 21
   reorderings.
3. **M5's third case synthesizes only in the claim M1 chooses.** Task 4: a trailing step past a commitless
   retry is synthesized; one beside a newer claim's real beat is not.
4. **A third party's later `stopped` reports a death only when its `error` is set.** Task 7: a crash the
   backstop reported, then cleared by a default body, reads `{errored}`, not `{errored, preempted}`.
5. **A refusal happens before the seal and names the run and the seq,** and the runner refuses any second
   renumbering step. Tasks 2 and 8.

## File structure

| Stage | Create | Modify |
|---|---|---|
| 1 | `protocol/lifecycle-v0.6.schema.json`, `subscription-v0.4`, `supervisor-v0.5`, `value-v0.3` (each `.schema.json`); `tests/test_schema_v0_4_0.py` | `runstate/migrations/__init__.py` (`Step.RENUMBERS`, the preservation check), `stores.py`, `v0_2_0_to_v0_3_0.py` (`RENUMBERS = False`); `docs/specs/log-formats.md` §6; `tests/test_migrate.py` |
| 2 | `runstate/migrations/v0_3_0_to_v0_4_0.py`; `tests/test_migration_v0_3_0_to_v0_4_0.py` | — |
| 3 | `runstate/lineage.py`; `tests/test_lineage.py` | — |
| 4 | `scripts/migration_round.py`; `tests/test_migration_round.py` | — |
| 5 | — | the step, its tests, `docs/specs/commits-and-lineage.md` §7, the review (item 16) |

---

## Stage 1: the wire and the runner

### Task 1: The 0.4.0 convention schemas

**Files:**
- Create: `protocol/lifecycle-v0.6.schema.json`, `protocol/subscription-v0.4.schema.json`,
  `protocol/supervisor-v0.5.schema.json`, `protocol/value-v0.3.schema.json`.
- Test: `tests/test_schema_v0_4_0.py`.

**Interfaces:**
- Produces: `STACK_0_4_0`, a `SchemaStack` for the envelope (unchanged, `envelope-v0.2`) and these four files.
  Stage 2's tests and Stage 4's runner validate migrated rows with it.

- [ ] **Step 1: Write the failing tests.**
  - Each schema passes `Draft202012Validator.check_schema`.
  - One valid body per record in spec §2's tables: `commit`, `stopped` (completed, preempted, torn, errored),
    `rewind`, `started` (with `resume_from` and `sources`), `heartbeat` (no `step`), `subscribe`, `stop`,
    `withdraw`, `died` (each outcome), `launched`, `terminated`, `value`.
  - §10's negative cases are refused: `error` with non-null `values`; a torn exit that fires; a `died` whose
    outcome is `completed`; a `value` with a `step`; `{commits: -1}`.
  - `{stream, n}` and `{commits: n}` take n from 0 to 2⁵³−1.
- [ ] **Step 2: Run them.** They fail: the files are missing.
- [ ] **Step 3: Write the schemas** from spec §2 and §3, field by field.
  - Fields are present-nullable, with `additionalProperties: false`.
  - The prose-only constraints marked *Prose* in §2 stay out of the schema. List them in each file's
    `description`.
- [ ] **Step 4: Run them.** They pass. `tests/test_schema.py` is untouched and still passes on 0.3.0.
- [ ] **Step 5: Commit.** `feat(protocol): the 0.4.0 convention schemas, beside 0.3.0's`.

### Task 2: The runner: exactly one step renumbers

**Files:**
- Modify: `runstate/migrations/__init__.py`, `runstate/migrations/stores.py`,
  `runstate/migrations/v0_2_0_to_v0_3_0.py`, `docs/specs/log-formats.md` §6.
- Test: `tests/test_migrate.py`.

**Interfaces:**
- Produces: the `Step` Protocol gains `RENUMBERS: ClassVar[bool]`. The new step sets it True. The runner
  enforces both of these:
  - **Preservation.** A step with `RENUMBERS = False` must return every input row with its `seq` and `topic`
    unchanged. Any rows it appends sit above the input's last seq.
  - **One renumberer.** At most one entry of `STEPS` sets `RENUMBERS`, and it leaves `0.3.0`.

- [ ] **Step 1: Write the failing tests** with toy steps, as `tests/test_migrate.py`'s `Tag` does:
  - A non-renumbering step that changes a seq is refused before the seal, on SQLite and on Postgres. The old
    log stays writable on SQLite, and the transaction rolls back on Postgres.
  - So is one that changes a topic.
  - A second renumbering step, or one leaving a format other than `0.3.0`, is refused when the chain is built.
  - A renumbering step's new seqs reach the new log on both stores.
- [ ] **Step 2: Run them.** They fail.
- [ ] **Step 3: Implement it.**
  - Put the check in `migrate_one`'s dry run (SQLite) and before the inserts (Postgres), in one shared helper.
  - Put the one-renumberer check in `chain`.
  - Set `V0_2_0_to_V0_3_0.RENUMBERS = False`.
  - Write the rule into log-formats §6. It is new text there: today §6 says nothing about seq preservation.
- [ ] **Step 4: Run them.** All pass, and the 0.2.0 → 0.3.0 step's tests are unchanged.
- [ ] **Step 5: Commit.** `feat(migrations): the runner enforces seq preservation, and one renumbering step`.

---

## Stage 2: the step

The step module is `runstate/migrations/v0_3_0_to_v0_4_0.py`.
- **Its docstring** states §7's rules, its refusals and its known limits, as the 0.2.0 step's does.
- **Its private record type and passes** mirror the 0.2.0 step's (`_Record`, a private `_Topic`, a `Backfill`
  that can also insert and move records).
- **`transform` is pure and deterministic.** SQLite calls it twice (the dry run, then the real run).

The golden logs go in `tests/test_migration_v0_3_0_to_v0_4_0.py`.
- **Builders.** They build 0.3.0 bodies (`_started`, `_beat`, `_stopped`, `_value`, `_launched`,
  `_terminated`, `_stop`, `_nak`, `_bound`). Keep them in that file, since the 0.2.0 test module's builders
  make 0.2.0 bodies.
- **Assertions.** Each golden log asserts its output rows exactly: seq, topic and body.

### Task 3: The module, its frozen semantics, and passes 1–2 (attribution and classification)

**Interfaces:**
- Produces: `class V0_3_0_to_V0_4_0` with `FROM = "0.3.0"`, `TO = "0.4.0"`, `RENUMBERS = True`, and
  `is_live` and `transform`.
  - **`is_live`** carries 0.3.0's semantics: the latest claim is live unless a `stopped` names it, or its
    handle resolves dead.
  - **Pass 1** attributes each heartbeat by its `claim_seq`, and each value positionally, to the latest
    `started` before it.
  - **Pass 2** classifies each `stopped` as one of four: a worker's exit; a third party's later stop; the
    backstop, identified by its text; or claimless.
  - **Lone stops get a placeholder rule:** the first `stopped` naming a claim is its exit. Task 14 replaces
    this with the census's rule.

- [ ] **Step 1: Failing tests.**
  - Golden log: a displaced worker's beats keep their claim.
  - Classification: each of the four classes.
  - M10's malformed `{reason, honored, claim_seq}` body is recognized as such.
  - An AST test: the module imports only `Row` and `MigrationError` from the package.
- [ ] **Steps 2–4:** run them (they fail), implement, run them (they pass).
- [ ] **Step 5: Commit.** `feat(migrations): 0.3.0 → 0.4.0, attribution and classification`.

### Task 4: Pass 3 — synthesis (M2, M3, M4, M5, M8)

**Interfaces:**
- Produces: for each claim, its commits, real or synthesized, with the values each commits, and the exit node's
  `parent` and `values`.
  - **M2:** the step label wins.
  - **M3:** stepless values stay outside every lineage.
  - **M4:** values after their claim's `stopped` stay uncommitted.
  - **M5:** a commit is synthesized in exactly three places: a skipped tick; a trailing step at or below a
    non-error exit's last step; and a trailing step s, whatever the exit, in the claim M1 will choose for a
    later claim whose first real beat is s+1. That claim is the newest one before it to reach step s, by a
    commit or by trailing values.
  - **Leading values** go to the claim's first commit.
  - **M8:** a synthetic commit's `t`, `consumed_seq`, `fired: []` and `expired: []`.

- [ ] **Step 1: Failing golden logs:**
  - a step-0 loading `status`;
  - a skipped tick, with values and without;
  - post-loop values;
  - a trailing step before a non-error exit (M5's second case);
  - M5's third case past a retry that crashed while loading (synthesized);
  - M5's third case beside a newer claim that reached the step by a real beat (not synthesized; A's values
    stay uncommitted);
  - values after `stopped`;
  - errored and preempted exits.
- [ ] **Steps 2–4:** run them (they fail), implement, run them (they pass).
- [ ] **Step 5: Commit.**

### Task 5: Pass 4 — M1, parents and `resume_from`

**Interfaces:**
- Produces:
  - A claim's later commits take its previous commit as `parent`.
  - Its first commit, at old step s, takes the newest earlier claim (in claim order) that holds a commit at
    s−1, and that claim's last such commit. With none, the parent is null: a parentless resume, which is
    named.
  - A commit is the parent even where an extendable exit follows it.
  - `started.resume_from` is the first commit's parent.
  - A claim with no integer-step beat takes the newest commit of the newest earlier claim that holds one, and
    is labelled an inference.

- [ ] **Step 1: Failing golden logs:** the displacement-parent history; a commitless claim; a parentless
  resume; a resume past a preempted exit, which parents on the commit.
- [ ] **Steps 2–4:** run them (they fail), implement, run them (they pass).
- [ ] **Step 5: Commit.**

### Task 6: Pass 5 — M6 moves and renumbering, M7 renames

**Interfaces:**
- Produces:
  - **M6:** each commit, in its claim's order, moves to just after the latest of three records: its last
    committed value, its parent, and the record its `consumed_seq` names. Then everything is renumbered.
  - **M7:** every seq-valued field is remapped. The list is derived from the schemas: `claim_seq` (on the
    heartbeat, the exit, `died` and `bound`), `consumed_seq`, `parent`, `values` and `resume_from`.
  - `stop@N` request ids stay as they are.
  - A synthetic record takes the `created_at` of the record it is placed after.

- [ ] **Step 1: Failing tests:**
  - In every golden log, every seq-valued field names a record of the expected topic (Review Focus 1).
  - 0 commits name a later value, 0 parents follow their child, and 0 commits precede the record their
    `consumed_seq` names.
  - The renumbering is a bijection on the input's records.
- [ ] **Steps 2–4:** run them (they fail), implement, run them (they pass).
- [ ] **Step 5: Commit.**

### Task 7: Records, and third parties' stops

**Interfaces:**
- Produces the record conversions:
  - `started` gains `resume_from` and `sources: []`.
  - A stepless heartbeat becomes `lifecycle.heartbeat`, without `step`.
  - A worker's `stopped` becomes the exit node, with `parent`, `values`, `fired: []` and `expired: []`, and
    `final_step` dropped.
  - A value drops `step`.
  - `launcher.*` records become `supervisor.*`.
- Produces the third-party rules (review item 14):
  - **The stop that ends its claim** becomes `died`: `errored` when `error` is set, `preempted` otherwise. A
    `completed: true` one is included, since a death is never a completion. The evidence is the `error` text,
    else `"migrated: third-party stopped"`, and `t` is carried.
  - **A later stop** becomes `died` only when `error` is set, and otherwise only its withdrawals.
  - **The backstop** is a third party's wherever it falls, so it always becomes `died`.
  - **M10's bodies** become `died preempted`, with their `reason` as the evidence.
  - **A claimless stop** becomes withdrawals only.
  - **Withdrawals:** each `honored` id of a third party's stop becomes a `control.withdraw` placed after it.

- [ ] **Step 1: Failing golden logs:**
  - a malformed `stopped`, and its repair (the repair writes no death);
  - one log per class of third-party stop;
  - a crash reported by the backstop, then cleared by a default body: the output holds one `died errored`
    (Review Focus 4);
  - a stepless heartbeat;
  - every output row validates against `STACK_0_4_0`.
- [ ] **Steps 2–4:** run them (they fail), implement, run them (they pass).
- [ ] **Step 5: Commit.**

### Task 8: Attributed death reports, refusals, and the whole step

**Interfaces:**
- Produces:
  - **Attributed death reports.** A claim that 0.3.0 saw ended only by its launch's `terminated` gets a `died`
    placed just after it. Only when the launch's handle equals the claim's, compared whole as
    `local://host/pid`. A signal maps to `killed`, a nonzero exit to `errored`, exit 0 to `lost`, with
    `signal s` or `exit n` as the evidence.
  - **M9 refusals,** each before the seal, naming the run and the seq:
    - any subscription, with its samples, and any `control.unsubscribe`;
    - a `control.stop` whose `from` holds a step atom;
    - two heartbeats of one step in one claim;
    - a value with a null `name`.

- [ ] **Step 1: Failing tests:**
  - Golden logs: a claim ended only by `terminated`, with an equal handle, a differing one, and the same pid on
    another host.
  - One test per refusal, through `SqliteStore` with the step registered by monkeypatch, as the `toy` fixture
    does. Each asserts that the old log is unsealed and that the message names the run and the seq.
  - `transform` called twice on the same rows gives equal output.
  - Every golden log's output validates against `STACK_0_4_0`.
- [ ] **Steps 2–4:** run them (they fail), implement, run them (they pass).
- [ ] **Step 5: Commit.** Push, and run a task review of Stage 2 as a whole.

---

## Stage 3: the pure reads

### Task 9: `runstate/lineage.py` — the head, the lineage, counts and series

**Interfaces:**
- Produces pure functions over a `Channel` holding a 0.4.0 log:
  - `Head(node, settled, w)` and `LatestClaimHead().resolve(ch)`;
  - `AtNode(node)`, which is a `NodeRef`;
  - `lineage(ch, *, head) -> list[int]`, the node seqs from the root to the head;
  - `count(ch, x, node)`, where `x` is a stream name or `Commits()`;
  - `prefix_len`, `series` and `last_element`.
- The head rule: the newest claim, then that writer's own order. A commitless claim's head is its
  `resume_from`. A trailing rewind moves it.
- Settledness: a read is settled iff its subject cannot grow (always under `AtNode`; under `LatestClaimHead`,
  iff the head is closed).

- [ ] **Step 1: Failing tests** from §10's lists ("Nodes and commits", "Reads"; the pure subset), on
  hand-written 0.4.0 logs in a `MemoryChannel`:
  - the head of a commitless claim;
  - `Commits()` across a resume and an extended exit;
  - a value no node names is excluded;
  - `last_element` in lineage order.
- [ ] **Steps 2–4:** run them (they fail), implement, run them (they pass).
- [ ] **Step 5: Commit.** `feat(lineage): the pure 0.4.0 reads — head, lineage, counts, series`.

### Task 10: The verdict and the stop fold, for 0.4.0

**Interfaces:**
- Produces `verdict(ch) -> RunResult04`. Its fields are `outcomes: frozenset[str]`, `reports`, `node`,
  `claim_seq`, `malformed` and `tier`.
  - It reads the latest claim's readable self-report, else the death reports naming it, as a set, less `lost`
    beside any other outcome.
  - A malformed record that ends the claim counts as `lost`.
  - An unclaimed run takes its launch's `terminated`.
- Produces `undischarged_stops(ch) -> list[int]`. A stop is spent by name: by an exit's `honored`, a
  `control.withdraw`, or a `nak`.
- These are local types for now. Layer 2 replaces `observables`' 0.3.0 versions with them at the format flip.

- [ ] **Step 1: Failing tests** from §10's "Death reports" (the pure subset): several reports kept as a set,
  `lost` dropped, a malformed `died` counting as `lost`, and `terminated` ending nothing for a claimed run.
- [ ] **Steps 2–4:** run them (they fail), implement, run them (they pass).
- [ ] **Step 5: Commit.**

---

## Stage 4: the measurement

### Task 11: The corpus runner

**Files:**
- Create: `scripts/migration_round.py`.
- Test: `tests/test_migration_round.py`.

**Interfaces:**
- Produces `python scripts/migration_round.py ROOT... --out DIR`. For each log file under the roots:
  1. **Find the files.** Resolve symlinks, deduplicate by inode, and skip any SQLite file with no runstate
     `log` table. Count what was skipped.
  2. **Read.** Open the log with `mode=ro&immutable=1` and read its rows. Determine its format by address
     (legacy or `v0.2.0` means 0.2.0; `v0.3.0` means 0.3.0).
  3. **Bring it to 0.3.0** in memory through the shipped step, recording any refusal.
  4. **Compute the old reads** over a `MemoryChannel` of the 0.3.0 rows: `value_series`, `peek_terminal` and
     `undischarged_stops`.
  5. **Run the new step,** recording a refusal by its reason. Validate every output row against
     `STACK_0_4_0`.
  6. **Compute the new reads** with `runstate.lineage` at `LatestClaimHead`.
  7. **Check P1–P4.**
     - **P3's cell mapping:** an old cell `(name, step)` maps to the new cell `(name, count(Commits(), n) − 1)`
       at the commit n that commits it on the head's lineage.
     - **Attribution:** each differing cell goes to one named class, in §7 P3's list, or *unattributed*.
     - **P4:** compare outcome sets, the old one taken as a one-element set, and stop ids, with each
       difference attributed to one of P4's named classes or *unattributed*.
  8. **Write the results:** one JSONL line per run to `DIR/runs.jsonl`, and `DIR/summary.json` with totals per
     class.
- The rule for attribution: every class is a predicate on the run and the cell, written next to the rule it
  names. A cell that two classes claim is reported as both, never silently given to one.

- [ ] **Step 1: Failing tests.** Run the runner over a temporary directory holding Stage 2's golden logs, as
  0.3.0 files, plus each P3 and P4 class built once. Each difference is attributed to its class; one
  deliberately unexplained difference reads *unattributed*; a symlinked duplicate is counted once.
- [ ] **Steps 2–4:** run them (they fail), implement, run them (they pass).
- [ ] **Step 5: Commit.**

### Task 12: Run the corpus

- [ ] **Step 1: Gather the corpus, read-only.**
  - Local: mycooc's `outputs/runs` (5,036 log files on 2026-10-23), translation (1,237 `.db` files, some not
    runstate logs), runstate-tui (32), bookgen (160).
  - The cluster store (4,683 channels, 3.0 GB; mycooc's E13): only with the owner's go-ahead, through a copy
    taken with `immutable=1` reads, as mycooc's E35 census did.
- [ ] **Step 2: Run** `scripts/migration_round.py` over each root, with output to the session scratchpad.
- [ ] **Step 3: Record the totals,** against §11.3's figures:
  - P1 failures;
  - P2 exceptions by kind;
  - P3 cells by class;
  - P4 differences by class;
  - refusals by M9 kind, including the count of null-name values;
  - unattributed cells, by run.

---

## Stage 5: the resolution

### Task 13: The census of lone stops, and its rule

- [ ] **Step 1: Classify every lone `stopped` by its writer** (review item 10(d)). A lone stop is one that is
  the only terminal naming its claim. The four mycooc writers each leave a signature in the body and its
  position:
  - the backstop: its text, or `error: null` after the runner's own kill;
  - the "clear staged stop" path: a default body, `final_step` set, a non-empty `honored`;
  - `reclaim`: `final_step: null`, and `t` set to SLURM's end time, well before `created_at`;
  - the repair script: a default body after a malformed one.

  Count each class, and the stops no signature fits.
- [ ] **Step 2: Derive the rule.** It must read the log alone, as M1's does. A stop it attributes to a third
  party becomes a `died`, mapped as in Task 7. One it cannot attribute stays a self-report with
  `values: null`.
- [ ] **Step 3: Encode the rule** in pass 2, replacing Task 3's placeholder. Add one golden log per class and
  re-run Task 12.
- [ ] **Step 4: Commit.**

### Task 14: The 41 cells of 625dda74, and every other unattributed difference

- [ ] **Step 1: Explain each unattributed cell,** starting with run 625dda74's 39 cells and the 2 added cells
  (review §11.3).
- [ ] **Step 2: Resolve each one in exactly one of three ways:**
  - **a named class,** if an existing rule explains it;
  - **a golden log,** if it shows a rule's edge case;
  - **a spec change,** if the rule is wrong. A spec change is written as a question for the owner and decided
    before it is encoded.
- [ ] **Step 3: Re-run to zero unattributed.**
- [ ] **Step 4: Commit.**

### Task 15: The write-up

- [ ] **Step 1: Write review item 16,** "the migration implementation round". It holds the corpus, the
  measured figures in place of §11.3's, the census and its rule, each resolution, and each spec change the
  owner decided.
- [ ] **Step 2: Update spec §7:** the lone-stop rule, the figures paragraph, and any decided change.
- [ ] **Step 3: Put the result before the owner** for ruling 12a, approving revision 2. Then #59 merges, then
  this branch's PR.
