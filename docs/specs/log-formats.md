# Spec: log formats — a log's format is part of its address

**Status:** IMPLEMENTED 2026-10-03: format 0.2.0, the addresses, the opening checks, sealing, and
`runstate migrate`, which refuses a run before sealing it (§6). Designed with the owner section by section.
Its first user is [`reference-by-name.md`](reference-by-name.md), which introduced format 0.3.0, the
current one, with the first retained step (0.2.0 → 0.3.0) the same day.

**What it gives:** every log has exactly one format, and every log can say which. A runstate release opens
only logs of its own format and raises on any other. Logs move between formats only through retained,
chained migration steps that copy, never rewrite.

## 1. The problem

Until now a convention bump was handled by an owner-run script that rewrote the author's logs in place,
then was deleted once it converged. That worked while one author held every log. It stops working on two
counts.

**Consumers now upgrade on their own schedule.** mycooc, translation and runstate-tui pin runstate to a
commit (2026-10-03), so a log can sit at an old format indefinitely. Whatever moves it forward must still
exist when its owner upgrades. `../backlog/release-and-stability-contract.md` §(b) anticipated this, and
named the missing piece as "version detection, a step ladder".

**A log's format cannot be told from its shape.** `value.t` was redefined from per-episode to absolute
time, with the same field and the same shape (`memoizer.md`). Some future changes will be undetectable by
inspection. A step ladder needs to know where to start, so the format must be stated, not inferred.

## 2. The rules

1. **A log has exactly one format**, named by **the runstate release that introduced it**: a version
   string such as `"0.3.0"`. It is never optional, never nullable, and never inferred.
2. **A release knows one format,** `runstate.LOG_FORMAT`: the version of the last release that changed
   the format. Releases that do not change the format inherit it (0.3.1 still writes and reads `0.3.0`).
3. **A format changes in exactly one commit,** together with the package-version bump. Spike branches
   never introduce a new format string.
4. **The format is the outermost part of a log's address.** Each format owns its layout below that
   point, so a later format may change anything there: one file per run, a shared database, cross-run
   metadata.
5. **No mixed logs.** A log is entirely in one format. Nothing reads one format as another.
6. **Exactly one writable log per run.** A run whose log is being migrated, or has been, has a sealed old
   log and at most one writable new one.

**Why the address, and not a record or metadata.** A record would make a created-but-unclaimed log hold
records, which changes the existence rule (`channel-locators.md`: a run exists iff it has records). It
would also put a fact about the container into the run's history. In-file metadata would need a format
table in every log, an atomic stamp at creation, and a per-run notion on Postgres's shared table, which
cannot describe a change to that table's structure. An address needs none of these, and makes migration
non-destructive. A **directory per format** rather than a per-run directory (`<root>/<rid>/v0.3.0.db`)
because only the outermost component leaves every future layout free.

## 3. Addresses

| backend | format 0.2.0 | format 0.3.0 | legacy (unversioned) |
|---|---|---|---|
| SQLite | `<root>/v0.2.0/<rid>.db` | `<root>/v0.3.0/<rid>.db` | `<root>/<rid>.db` |
| Postgres | schema `runstate_v0_2_0`, table `log` | schema `runstate_v0_3_0`, table `log` | table `log` outside any `runstate_v…` schema |
| Memory | — | — | — |

- **Directory and schema names** are `v` plus the version, and `runstate_v` plus the version with dots as
  underscores.
- **Each format's layout lives in its own module,** `runstate/formats/v0_2_0.py` and so on. It is a
  function from (root, run id) to an address for each persistent backend. The runtime uses only the
  current format's module. The others exist for the opening checks and the migration steps.
- **Memory logs are in-process and ephemeral,** so they are always in the current format and carry no
  address.
- **The legacy Postgres table** is the `log` that an unqualified name resolves to under the connection's
  own search path, because every statement of a runstate from before versioned addresses was unqualified.
  That is `public.log` by default, and another schema when the DSN carries
  `options=-csearch_path=...`. It counts only outside the `runstate_v…` schemas, and only if it has a
  `run_id` column: another application's `log` is not runstate's. *Found in the final review:* the check
  first looked only in `public`, so a run in another schema read as not found, and `create_channel`
  birthed an empty run over it; and an unrelated `public.log` raised `UndefinedColumn` on every open.

## 4. Opening a log

`attach_channel` and `create_channel` both run these checks, in order, before anything reads or writes.
Every party opens a log through one of them: the Worker, the folds, `ensure`, the Watcher, and raw
`channel.send` users.

1. **A newer format is present.** If the root holds any format newer than `LOG_FORMAT` (a `vX.Y.Z/`
   directory, or a `runstate_v…` schema), raise **`LogFormatMismatch`**, telling the caller to upgrade
   runstate. A newer format's interior cannot be known, but its name can, so this needs one directory or
   schema listing, cacheable per process. Without it an old runstate would read "not found" and relaunch a
   run that has moved on. On Postgres the listing covers the **whole database**, so consumers that share
   one database upgrade in lockstep (§8).
2. **The current format's address exists.** Open it.
3. **An older format's address holds the run.** Older layouts come from the format modules. Raise
   **`LogFormatMismatch`**, naming the found and expected formats and telling the caller to run
   `runstate migrate`.
4. **The legacy unversioned address holds the run.** Raise **`LogFormatMissing`**. Its message carries the
   one-time onboarding instructions (§7).
5. **Nothing anywhere.** `attach_channel` raises `RunNotFound`. `create_channel` births the log at the
   current address.

Both errors subclass a new **`LogFormatError`**. Neither is ever caught and turned into "not found".
Memory channels have no address, so they skip these checks.

## 5. Sealing

A sealed log is readable and refuses every write.

- **SQLite.** Checkpoint, switch the file to `journal_mode=DELETE` so that it opens read-only without a
  writable `-wal` or `-shm`, then make the file read-only. An old writer's next append fails with
  "attempt to write a readonly database".
- **Postgres.** The old schema gains a `sealed_runs(run_id)` table and an insert-refusing trigger on its
  `log` table. Sealing a run and copying it happen in one transaction, holding a lock that blocks
  concurrent inserts for that run (§6 gives the failure behavior).

*Found in implementation.* SQLite sealing **raises rather than partially sealing** when another
connection holds the log open. And sealing by file permissions **does not bind the root user**, who can
still write a read-only file; the seal is a guard against the wrong upgrade order, not against root.

Sealing is what makes the wrong upgrade order fail loudly for a writer that resolves the sealed log's
address: its append is refused by the file system or the database.

**A writer from before format versioning never resolves a versioned address.** It checks no format and
opens only the legacy address (§3), which onboarding (§7) empties by moving the log away, so the seal on the
moved copy never reaches it. Onboarding therefore leaves a **tombstone** at the legacy address, which plays
the seal's part for such a writer:

- **SQLite.** An empty, read-only `<root>/<rid>.db`. The old writer's create fails with "attempt to write a
  readonly database", in WAL and DELETE mode alike, and its attach finds no records (`RunNotFound`).
- **Postgres.** An empty `log` with the old columns, in the schema the legacy table was found in, and a
  trigger on it that refuses every insert. The old `ensure_schema` finds the table and creates nothing, and
  the old writer's first append raises.

*Found in the final review.* Without the tombstone the wrong order did not fail at all. A pre-versioning
worker started after onboarding saw no records at the legacy address, birthed a fresh log there (on
Postgres, its `ensure_schema` recreated `log`), recomputed the run from step 0, and finished without an
error.

The tombstone is part of the onboarding instructions, not runtime code. This release's opening checks reach
an onboarded run through checks 2 and 3 (§4) before they look at the legacy address, so they answer as they
would without it, before `runstate migrate` and after; tests pin both backends.

## 6. `runstate migrate`

```
runstate migrate <root> [<rid> ...] [--to V] [--backend sqlite|postgres]
                 [--resolve <ambiguity>=<strategy> ...]
```

`<root>` is a directory for SQLite and a DSN for Postgres. With no run ids, every run under the root is
migrated.

- **Steps** live in `runstate/migrations/`, one module per step, each declaring `FROM` and `TO` format
  versions, its ambiguities (below, "Ambiguity"), and a transform from a run's records in `FROM` to its
  records in `TO`.
- **The runner chains steps** from each log's format to `--to`, which defaults to `LOG_FORMAT`. It refuses
  if there is no path, or if two steps leave the same format.
- **A step preserves seq and topic, except one.** A step returns every input row with its `seq` and `topic`
  unchanged; it may rewrite a body, and it may append rows above the input's last seq. The runner refuses a
  step that changes a seq or a topic, drops a row, or appends at or below the last seq, and it refuses
  before the seal (on Postgres, before any insert, so the transaction rolls back). Exactly one step is
  exempt, declared by `RENUMBERS`: 0.3.0 -> 0.4.0 renumbers. The runner also refuses to build a chain
  if more than one registered step sets `RENUMBERS`, or if the one that does leaves a format other than
  0.3.0. Every other format boundary is a place where a pinned reader's seq cursors stay valid.
- **Each step, per run:**
  1. Refuse if the run has a live episode, read with the `FROM` format's semantics; a step carries what it
     needs of its old format. An unresolvable foreign claim reads as live, so a stranded claim must be
     released first.
  2. Refuse if the step, or a strategy it was given, refuses the run's records. A step's refusal is
     deterministic, and so is a strategy's, so it comes before the seal: on SQLite the runner dry-runs the
     transform on the records it read for step 1.
  3. Seal the old log.
  4. Transform into the new format's address. On SQLite, write to a temporary file of its own in the target
     directory and rename it into place; on Postgres, use the sealing transaction.
- **A refusal leaves the run untouched.** Every refusal is a `MigrationError` that names its run
  (`run '<rid>': …`), and `migrate` carries on past it to the other runs (below, "Carrying on past
  refusals"). The refused run stays writable and unmigrated, and a re-run refuses it the same way. One
  residual on SQLite: records that land between the pre-seal read and the seal can still make the post-seal
  transform refuse, which leaves the run sealed and unmigrated; recovery is §8's rollback. On Postgres
  there is no such window, because the seal is taken before the read.
- **A failure is recoverable by re-running `runstate migrate`, which completes it.** On SQLite a failure
  after the seal leaves the run sealed and unmigrated: new code raises "migrate", and old code cannot
  write. On Postgres the seal and the copy are one transaction, so a failure, a refusal included, rolls
  back completely and leaves the run unsealed and unmigrated. A chain that stops part-way leaves the run
  at an intermediate format, and re-running continues from there.
- **Steps are retained, never deleted.** This resolves `release-and-stability-contract.md` §(b) in favor
  of its option 3, with the detection problem removed by the address.

*Found in implementation.* The CLI needs `--backend sqlite|postgres`, in the synopsis above. On Postgres the
seal is taken first, then the rows are read and copied in that one transaction.

*Found in the final review.* `runstate migrate` exited 0 having done nothing when the root did not exist,
or when a DSN was given without `--backend postgres` and read as a SQLite root with no runs. It refuses
both now: a SQLite root that is not a directory, and a `postgres://` or `postgresql://` root without
`--backend postgres`. Every message that says to migrate gives the whole command, `runstate migrate
<root>` or `runstate migrate '<dsn>' --backend postgres` (a placeholder, since a DSN can carry a
password).

### Ambiguity

*Specified 2026-10-24, not yet implemented* (review §12, item 16). **A step that cannot settle an answer
from the log refuses the run, unless the user explicitly supplies a Strategy for that kind of question.**
There is no default guess: a migration is an easy place to make a large mistake, and a replayable mistake
is still made. Every step shares the mechanism below, so every ambiguous migration has the same
ergonomics.

**Vocabulary.**
- An **ambiguity** is a question a step must answer about some records of a run, which the log can leave
  open. A step declares its ambiguities as a closed set, and names each in kebab-case, as the command line
  spells it.
- A **site** is one record of one run, named by run id and seq, at which an ambiguity's question is open:
  two histories that a conforming writer of the input format could produce give the same log there, and
  need different output. **A site is where histories of conforming writers call for different outputs,
  never where the shipped built-ins happen to disagree:** the choice of built-ins never removes a site, and
  how often one history occurs may justify a built-in's description, never a site's absence. **A site test
  reads only the step's input log,** never a reading of any of the step's ambiguities, its own included
  (review §12, item 16).
- A **malformed record,** one whose body the input format does not define, has no conforming writer, so its
  meaning is open by definition. It is a question only for a step that reads that body. A step carries a
  record it does not read to the next step with its body unread, as the 0.2.0 → 0.3.0 step carries a
  malformed `stopped`, adding only the names its rules write. The step that reads it either declares a
  malformed-record ambiguity for that kind of record, every such record being one of its sites, or refuses
  the record outright. Only that step declares it (review §12, item 16).
- A **proven site** is a record at which the question arises but the log settles it: what the log proves
  there, under the input format's conventions, gives one reading, its **proven reading**. A proven
  site is not a site. It never forces a choice, and no strategy changes its reading.
- A **reading** is one answer at one record: a value of the ambiguity's closed reading type.
- A **strategy** implements the ambiguity's Strategy Protocol. It is called once per run with the run's
  records, its sites of the ambiguity, and the readings the step gave the ambiguities declared before it,
  and it returns a reading for each site, or refuses the run with a reason. It is deterministic, a function
  of what it is given, so the dry run and the transform agree.
- A step's **resolutions** are its whole configuration: exactly one strategy for each ambiguity it
  declares.
- The **choices** are the `--resolve`s given, by ambiguity name, across a chain: possibly fewer than its
  steps declare. The detection pass runs on the choices, and builds each step's resolutions from them once
  every ambiguity of that step that occurs has one.

**The principle: ambiguity is judged against any conforming writer of the input format, under that format's
conventions.** A question is settled where the input format's conventions pin the meaning of the records it
reads, and open where they do not. Its conventions are what its schemas and its design write: another spec
may elaborate one, never make one, and a reference implementation's behaviour makes none. **Every meaning
they do not pin is a declared ambiguity, with its narrowest site; no convention is pinned by ruling** (review
§12, item 16). A step judges against the format it reads, never against a later one, and never against one
consumer's writers:
- runstate ships **generic** built-in strategies only. A built-in is any reading expressible over the input
  format's own vocabulary that is right for some conforming writer;
- a reading of a consumer's texts or shapes is that consumer's strategy, and its "preset" is its own set of
  `--resolve` choices, kept in the consumer;
- a malformed record that a step reads is refused unless a strategy for its malformed-record ambiguity reads
  it, and one of a kind the step declares no such ambiguity for is refused outright.

Each step's spec names what the input format pins, with where each is written, and lists its ambiguities.

**The Step protocol gains:**
- **`AMBIGUITIES`:** the step's closed set, an enum whose members' values are the command-line names, in
  the order the step asks them. Each member has one declaration, of the type
  `runstate/migrations/ambiguity.py` defines: its name; a one-sentence definition; its reading type; its
  Strategy Protocol; and its built-in strategies. The set may be empty.
- **`detect(rows)`:** the run's sites of each ambiguity, read from the input log alone, writing nothing. It
  takes no strategy.
- **`transform(rows, *, resolutions)`:** `resolutions` is required and keyword-only. It calls each strategy
  once per run, in the declared order, each receiving the readings given before it. It checks every reading
  against what the log proves, refusing the run, naming the strategy and the seq, when a reading
  contradicts it. It returns the readings it gave at the sites with the rows, for the manifest.

The built-in strategies live beside their step, in a module of their own:
`v0_2_0_to_v0_3_0_resolutions.py` and `v0_3_0_to_v0_4_0_resolutions.py`. `runstate/migrations/ambiguity.py`
holds what every step shares:
- the declaration type;
- `Choices`, the partial set the detection pass runs on;
- `Resolutions`, which holds exactly one strategy for each declared ambiguity, and is validated against its
  step's set when it is built;
- the unresolved refusal;
- the command-line parsing.

**`resolutions` is also a required keyword-only argument of `migrate()`.** There its type is `Choices`, not
`Resolutions`, since a chain's later steps can be resolved only once the earlier ones are: `migrate()` builds
each step's `Resolutions` from the choices as it reaches that step, and refuses a chain step with an
occurring ambiguity that has none.

**No defaults; `refuse` always.** Every ambiguity has the built-in `refuse`, which refuses a run at its first
site. It is the only strategy a step's resolutions may hold for an ambiguity the user did not resolve, which
can happen only where that ambiguity does not occur. No other built-in is a default, and no option resolves
every ambiguity at once. A proven site takes its proven reading whatever the strategy.

**A strategy sees every site of its kind** in one call per run, with the run's records and the earlier
readings, so it can read a run's records together, as a claim's several stops must be read. A reading the
log excludes is refused, naming the strategy and the seq.

**The detection pass.** `runstate migrate` first runs every selected run's chain of steps in memory, sealing
and writing nothing: step 2 above, for the whole root before any run is sealed. It goes **step by step**. A
step's sites are found on its input log, which for a later step is the earlier step's output. An ambiguity
**occurs** in the root when any run has a site of it. **The pass refuses unless every ambiguity that occurs
has a `--resolve`,** and then writes nothing at all. There is no default and no accept-all.

**A later step's ambiguities are undecided until every occurring ambiguity of the earlier steps in the chain
is resolved,** since its sites are found on their output. So a user resolves in one round per step: a root at
format 0.2.0 that migrates to 0.4.0 takes at most two. On refusal the pass prints a summary **per ambiguity**,
never a list of every site:
- the step, the ambiguity's name and its one-sentence definition;
- its readings, and its built-in strategies, each with a one-line description;
- how many sites it has, in how many runs;
- a few example sites: the first three, by run id and seq;
- the option to add, `--resolve <ambiguity>=<strategy>`.

It then names the later steps whose ambiguities are undecided until these are resolved.

```
stop-author (0.3.0 -> 0.4.0): who wrote a lifecycle.stopped, the claim's worker or a third party,
  where the log does not prove it.
  readings: worker | third-party
  built-ins: first-is-exit  a claim's first open stop is its worker's exit
             last-is-exit   a claim's last open stop is its worker's exit
             third-party    every open stop is a third party's
             refuse         refuse every run with a site
  sites: <n> in <m> runs, e.g. <rid> seq <seq>, <rid> seq <seq>, <rid> seq <seq>
  add: --resolve stop-author=<built-in or module:attr>
```

**`--resolve <ambiguity>=<strategy>`,** repeatable, one per ambiguity.
- **`<ambiguity>`** is a name some step of the chain declares. Names are unique across the registered
  steps: the runner refuses to start when two steps declare one name, as it refuses two steps that leave one
  format.
- **`<strategy>`** is a built-in's name, spelled with no colon, or **`module:attr`**, naming an object that
  implements the ambiguity's Strategy Protocol. The command line resolves each once, before the detection
  pass: it imports `module:attr` and checks the object against the Protocol. It refuses an unknown
  ambiguity, an unknown built-in, an import that fails, and an object that is not a strategy for that
  ambiguity.
- **A `--resolve` for an ambiguity that does not occur** is accepted, so a consumer can keep one set of
  choices for every root.
- **An explicit `--resolve <ambiguity>=refuse`** refuses only the runs with a site of it, and the rest
  migrate. An occurring ambiguity left unresolved refuses the whole invocation.

**Carrying on past refusals.** Once the detection pass is satisfied, `migrate` migrates every run it can.
A run refused by a strategy, by `refuse`, or by a step's own refusal is left untouched, and the other runs
go on. `migrate()` returns the runs it moved and every refusal, each naming its run, and the command prints
them and exits nonzero when any run was refused. Until now it stopped at the first refusal.

**The manifest** records, for each root, what every migrated run was migrated under. The log formats are
untouched: the manifest belongs to the root, outside every format's address.
- **SQLite:** `<root>/runstate-migrations.jsonl`, one JSON object per line, appended. It is neither a format
  directory (§4, check 1) nor a legacy address (§4, check 4).
- **Postgres:** the table `manifest` in the schema `runstate_migrations`. That schema is outside every
  `runstate_v…` schema, so check 1 does not read it as a format, and the table is not named `log`, so
  check 4 does not read it as a legacy log.
- **One entry per run per step:** the run id; the step's `FROM` and `TO`; the runstate version that ran it;
  when; the resolutions, each ambiguity's strategy as it was given, a built-in's name or `module:attr`; and
  the run's sites, by ambiguity, each site's seq with the reading it was given. Proven sites are not
  recorded.
- **When it is written:** once the run's new log is published. On Postgres the entry is inserted in the
  sealing transaction. On SQLite it is appended after the rename, so a crash between the two leaves a
  migrated run with no entry, which a re-run cannot add, since the run is no longer at the old format (a
  known limit).
- **Copying a run** between roots (§8) copies its log at its format's address. Its manifest entries go with
  it only if they are copied by hand.
- **Collecting a run** (`store.md` Recipe 3) leaves its manifest entries behind unless the collector deletes
  them too (§11).

**The escape hatch: a whole step is a Strategy too** (documented, not built). A consumer whose history does
not fit a step's declared seams may supply its own step for that edge, named `module:attr`: an object
implementing the Step protocol, with the stock step's `FROM` and `TO`. It may import the stock step's
passes and reuse them. The same runner runs it under the same invariants:
- it keeps every input row's seq and topic, the one renumbering step's exemption aside;
- every row it writes conforms to the `TO` format's schemas, which the runner checks row by row for a
  custom step, since no runstate test covers it;
- its refusals come before the seal;
- the manifest records it by its `module:attr`, beside the resolutions it declares.

No command-line option takes one yet. The trigger to build it is a consumer whose log a step's declared
ambiguities cannot express.

**Redo is a known gap, not built.** A migrated run's old log is sealed and its new one exists, so `runstate
migrate` does not migrate it again under other resolutions. §8's rollback is the manual path: make the old
log writable, delete the new one, and migrate again. The manifest says what was chosen the first time, and
the new entry is appended beside the old.

**The precedent.** Every later step declares its ambiguities, with an empty set when it has none, so every
step takes `resolutions` and the detection pass treats every chain alike. The first two steps declare theirs
in [`reference-by-name.md`](reference-by-name.md) §6 (format 0.2.0 → 0.3.0) and
[`commits-and-lineage.md`](commits-and-lineage.md) §7 (format 0.3.0 → 0.4.0). The mechanism's tests are in
§10.

## 7. Onboarding legacy logs

Logs written before format versioning sit at the legacy address. Nothing infers their format. The
`LogFormatMissing` message tells the user:

> This log predates versioned addresses. If it was written by runstate at or after `4729fcd`
> (2026-07-16, lifecycle-v0.4 and launcher-v0.4), it is format 0.2.0 (check: its `lifecycle.heartbeat`
> bodies carry a `t` field). First stop every process that writes under the root, and drain any queued
> jobs that would. Then move it, with its `-wal`, `-shm` and `-journal` files, to `<root>/v0.2.0/<rid>.db`,
> leave a tombstone at its old address, and run `runstate migrate <root>`.

It also gives a one-line shell loop that does this for every legacy log under a root. The loop skips empty
files and logs already in place, so running it again is safe.

*Found in the final review.* A writer in a rollback journal mode (DELETE, which mycooc uses on NFS) that
died mid-transaction leaves a hot `-journal` holding the pages it overwrote; the log is consistent only
once the journal rolls back. So the loop moves the `-journal` with its log, and `runstate migrate`'s first
read, before the seal, opens the log read-write, because a read-only open cannot roll a hot journal back. On Postgres the instruction is one
transaction: create schema `runstate_v0_2_0`, `ALTER TABLE <schema>.log SET SCHEMA runstate_v0_2_0`, then
create the tombstone (§5) in the schema the table was found in.

**The tombstone is why the move is not enough.** A writer still running a release from before versioned
addresses resolves only the legacy address. Without the tombstone it would find nothing there and start the
run over, silently; with it, its next write fails (§5).

**The move is the user's explicit statement of the format.** `runstate migrate` has no legacy option.

**The legacy check (rule 4 in §4) is the only legacy code, and it has a deletion trigger:** delete it once
every consumer has onboarded. Until then it must stay, because without it a legacy run would read as "not
found" and be relaunched from scratch.

## 8. Operating rules

- **The upgrade procedure for a consumer:** stop its old processes and drain its queued jobs, bump its
  pin, onboard its legacy logs (§7), then migrate them, in that order. A job queued under the old pin runs
  the old code whenever it starts. If the order is wrong anyway, the seal and the tombstone (§5) are the
  backstop: they make the old writer fail loudly rather than start the run over.
- **Rollback:** make the old log writable again (`chmod u+w`, or delete the run from `sealed_runs`),
  delete the new one, and revert the pin. Reverting to a pin from before versioned addresses also undoes
  the onboarding: delete the tombstone, and move the log back to the legacy address.
- **Garbage collection:** a sealed log may be deleted once nothing pins its format. This is an owner
  action. `store.md` Recipe 3 gains a rule: collecting a run deletes its log at its format's address,
  because a log no longer sits inside the run's home, and deletes its tombstone at the legacy address with
  it. A tombstone left behind would make the run's id read as an unonboarded legacy log (§4, check 4).
- **Copying a run** between roots or backends copies its log at its format's address, which carries the
  format with it.
- **Consumers that share one Postgres database upgrade in lockstep.** The newer-format check (§4, check
  1) covers the whole database: once one consumer migrates, the new format's schema exists, and every
  open by a consumer still pinned to the old release raises `LogFormatMismatch` until it upgrades too.

## 9. Interop

The address scheme and the opening checks are part of the protocol. Another language's implementation must
compute addresses the same way and raise on the same five cases. The conformance suite gains tests for each
(§10).

## 10. Tests

On SQLite and Postgres (Memory has no addresses):

- the five opening cases (§4), including refusing a root that holds a newer format, and the legacy
  "missing" error carrying its instructions;
- an old-style writer appending to a sealed log fails loudly;
- chaining through test-only steps (`FROM`→`TO` edges, no-path and two-steps-from-one-format refusals);
- a migration that fails midway leaves the run sealed and unmigrated, and a retry completes it;
- refusing a run with a live episode;
- the atomic publish: no reader ever sees a partial new log;
- the ambiguity mechanism (§6, "Ambiguity"):
  - `Resolutions` refuses a missing ambiguity, an undeclared one, and two strategies for one;
  - the command line refuses an unknown ambiguity or built-in, a failed import, an object of the wrong
    Protocol, and two steps declaring one name;
  - `detect` takes no strategy, and finds the same sites whatever the choices;
  - the detection pass writes nothing when an occurring ambiguity has no `--resolve`, prints one summary per
    ambiguity with at most three example sites, and holds a later step's ambiguities undecided until the
    earlier step's are resolved;
  - an explicit `refuse` refuses only the runs with a site, and the others migrate;
  - a run refused by a strategy leaves the other runs migrated, and the command exits nonzero;
  - a strategy sees every site of its kind with the earlier readings, a proven site reads the same under
    every strategy, and a reading the log excludes is refused;
  - the manifest holds one entry per migrated run per step, written in the sealing transaction on Postgres,
    and none for a refused run.

## 11. Ripple

- `channel-locators.md`: the address scheme and §4's checks.
- `channel-postgres.md`: a schema per format.
- `store.md`: a log's address under placement, and Recipe 3's deletion rule.
- `design-v0.2.md` §4: the locators check formats.
- `CLAUDE.md`: the upgrade procedure, beside the pinning note.
- `../backlog/release-and-stability-contract.md` §(b): resolved by this spec.
- `store.md` Recipe 3: collecting a run also deletes its entries in the root's migration manifest (§6,
  "Ambiguity").
- `CLAUDE.md`: the upgrade procedure's `runstate migrate` takes a `--resolve` for each ambiguity the
  detection pass reports, once the code ships.
- `reference-by-name.md` §6 and `commits-and-lineage.md` §7: each step's declared ambiguities.

## Related

- [`reference-by-name.md`](reference-by-name.md) — introduces format 0.3.0, and the first migration step.
- [`channel-locators.md`](channel-locators.md) — the locators this spec extends.
- [`../backlog/identity-in-records.md`](../backlog/identity-in-records.md) — the design both specs serve.
