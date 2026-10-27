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
                 [--assume <module:attr> ...] [--resolve <projection>=<chooser> ...]
                 [--accept <divergence> ...] [--cap N] [--budget N] [--run-on N]
```

`<root>` is a directory for SQLite and a DSN for Postgres. With no run ids, every run under the root is
migrated.

- **Steps** live in `runstate/migrations/`, one module per step, each declaring `FROM` and `TO` format
  versions, its facts, projections and divergences (below, "Ambiguity"), and a transform from a run's
  records in `FROM` to its records in `TO`.
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
  2. Refuse if the step, a chooser it was given, or a divergence no `--accept` names refuses the run's
     records. A step's refusal is deterministic, and so is a chooser's, so it comes before the seal: on
     SQLite the runner dry-runs the transform on the records it read for step 1.
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

*Specified 2026-10-24* (review §12, item 16); *revised 2026-10-26 for facts, choosers and divergences, and
implemented* in `runstate/migrations/ambiguity.py` (the plan's Task 21). **A step that cannot settle an
answer from the log refuses the run, unless the user states a fact about its writers that settles it, or
explicitly names a chooser for that kind of question.** There is no default guess: a migration is an easy
place to make a large mistake, and a replayable mistake is still made. Every step shares the mechanism
below, so every ambiguous migration has the same ergonomics.

**The owner's principle** (2026-10-25): consumers decide what happened, by facts and choosers; runstate
decides how the new format writes it down, by encoding rules, the same for every consumer. So `--assume`
takes facts, `--resolve <projection>=<chooser>` takes choosers, and `--accept <divergence>` takes
acceptances. Otherwise a migrated log's records would mean different things depending on who migrated it.

**Vocabulary.**
- A **fact** classifies every element of one kind, a claim or one kind of act its writer makes, into one
  class of a closed domain, defined over what the writer does, never over a pattern in the log. A
  **statement** is a subset of the domain; the unstated fact is the whole domain. Facts belong to a
  **writer version**, never to a root, and a consumer's **classifier** names the version of each record's
  writer where the log does not. `commits-and-lineage.md` §7, "The writer facts", gives the 0.3.0 → 0.4.0
  step's facts and its classifier Protocol.
- A **projection** is a kind of question a step must answer about some records of a run, which the log
  and the stated facts can leave open. A step declares its projections as a closed set, and names each in
  kebab-case, as the command line spells it.
- A **fork** is one open question at one record of one run, named by run id and seq: its **site**, where
  two histories that a conforming writer of the input format could produce, and that satisfy every stated
  fact, give the same log there and need different output. **A site is where histories of conforming
  writers call for different outputs, never where the shipped built-ins happen to disagree:** the choice of
  built-ins never removes a site, and how often one history occurs may justify a built-in's description,
  never a site's absence. **A site test reads only the step's input log and the stated facts,** never a
  reading of any of the step's projections, its own included (review §12, item 16). Each fork names the
  facts whose statement would settle it, none where no fact can.
- A **malformed record,** one whose body the input format does not define, has no conforming writer, so its
  meaning is open by definition. It is a question only for a step that reads that body. A step carries a
  record it does not read to the next step with its body unread, as the 0.2.0 → 0.3.0 step carries a
  malformed `stopped`, adding only the names its rules write. The step that reads it either declares a
  projection for that kind of record, every such record being one of its sites, or refuses the record
  outright. Only that step declares it (review §12, item 16).
- A **proven site** is a record at which the question arises but the log settles it: what the log proves
  there, under the input format's conventions, gives one reading, its **proven reading**. A proven site
  is not a fork. It never asks a chooser, and no chooser changes its reading.
- A **reading** is one answer at one fork: a value of the projection's reading type.
- A **chooser** implements its projection's chooser Protocol: `choose(question)` returns an answer or a
  `Refusal` with a reason. It is deterministic, a function of the question it is given, so the dry run and
  the transform agree. A step's question carries what its chooser may read: the run's records, its forks
  of the projection, and the readings given to the projections asked before it.
- A **divergence** is a rule under which a read of the migrated log answers differently from the same
  read of the input log (the owner, 2026-10-25). Corrections count. It is not an ambiguity: no two
  histories differ, so the site principle stays clean. Every rule that changes a read is one; a rule that
  changes no read is not, as `commits-and-lineage.md` §7's E1 is not, since no fold parses `evidence`.
- A step's **resolutions** are its whole configuration: its facts (stated, else unstated), the limits, and
  exactly one chooser for each projection it declares.
- The **choices** are what the user gave across a chain: the `--assume`s, the `--resolve`s by projection
  name, possibly fewer than its steps declare, the `--accept`s and the limits (cap, budget and run-on). The
  detection pass runs on the choices, and builds each step's resolutions from them once every projection of
  that step that forks has a chooser.

**The principle: ambiguity is judged against any conforming writer of the input format, under that format's
conventions.** A question is settled where the input format's conventions pin the meaning of the records it
reads, and open where they do not. Its conventions are what its schemas and its design write: another spec
may elaborate one, never make one, and a reference implementation's behaviour makes none. **Every meaning
they do not pin is a declared projection, with its narrowest site; no convention is pinned by ruling**
(review §12, item 16). A step judges against the format it reads, never against a later one, and never
against one consumer's writers, except as that consumer states facts about them:
- runstate ships **generic** built-in choosers only. A built-in is any reading expressible over the input
  format's own vocabulary that is right for some conforming writer;
- a reading of a consumer's texts or shapes is that consumer's chooser or classifier, and its "preset" is
  its own set of `--assume`, `--resolve` and `--accept` choices, kept in the consumer;
- a step may ship a **constructor of its facts** for a reference implementation's writers, as the 0.3.0 →
  0.4.0 step ships `reference(*, ...)` for the reference `Worker` (`commits-and-lineage.md` §7, "The writer
  facts"). It states only what that implementation's code fixes, and takes every fact the consumer's own code
  decides as a required argument, so none is left wide unasked. It pins no convention: a consumer's `--assume`
  names its own call of it, which is that consumer's word that its writers are those, recorded in the
  manifest as any statement is;
- a malformed record that a step reads is refused unless a chooser for its projection reads it, and one of a
  kind the step declares no projection for is refused outright.

Each step's spec names what the input format pins, with where each is written, and lists its facts,
projections and divergences.

**The Step protocol** (`runstate/migrations/__init__.py`) gains, from the types `ambiguity.py` defines:
- **`FACTS`:** a `Facts` declaration: the type a statement takes, whose objects `--assume` names (for the
  0.3.0 → 0.4.0 step, its facts per writer version and its classifier); the statement that holds when the
  user states nothing, every fact its whole domain; and how the manifest renders a statement, the
  classifier's identity included. A step with nothing to state declares `NO_FACTS`.
- **`PROJECTIONS`:** its closed set, in the order the step asks them, each a `Projection`: its name; a
  one-sentence definition; what its readings are; its generic built-in choosers, each with a one-line
  description, to which `refuse` is always added; and its answer's shape, which turns whatever a chooser
  returned into the typed answer or says what is wrong with it. The set may be empty. It is a tuple of
  module constants, not an enum, since each projection carries its own question and answer types.
- **`DIVERGENCES`:** its closed set, each a `Divergence`: its name, and the reads it changes and how, in one
  sentence. The set may be empty.
- **`detect(rows, *, assumptions, history)`:** the run's forks of each projection, from the input log and
  the stated facts alone, writing nothing. It takes no chooser.
- **`transform(rows, *, resolutions, history)`:** `resolutions` is required and keyword-only. It asks each
  projection's chooser at the run's forks, in the declared order, each question carrying the readings given
  before it. It checks every reading against what the log proves, refusing the run, naming the chooser and
  the seq, when a reading contradicts it. It returns the rows, the reading it gave at each fork, and where
  each divergence's rule fires, for the manifest and the runner.
- **`history`** is the run's entries in the root's manifest, oldest first, the earlier steps of this
  invocation's chain included: how the earlier steps migrated it. A later step may read an earlier step's
  choices from it (the owner's D3): in a log born in format 0.2.0, the 0.3.0 → 0.4.0 step reads the
  `record-claim` and `stop-honored` readings that filled its names (`commits-and-lineage.md` §7, "Names an
  earlier step filled").

The built-in choosers live beside their step, in its module. `runstate/migrations/ambiguity.py` holds what
every step shares:
- the declaration types (`Facts`, `Projection`, `Divergence`), the `Chooser` Protocol and `refuse`;
- `Choices`, what the user gave, and `Assumptions` and `Resolutions`, built from it per step;
- the manifest's `Entry`, the detection pass's report and its refusal (`Undecided`);
- the command-line parsing.

**`choices` is a required keyword-only argument of `migrate()`.** It takes `Choices`, not `Resolutions`,
since a chain's later steps can be resolved only once the earlier ones are: `migrate()` builds each step's
`Resolutions` from the choices, and the detection pass refuses a chain step with a fork of a projection that
has no chooser.

**No defaults; `refuse` always.** Every projection has the built-in `refuse`, which refuses a run with a fork
of it. It is the only chooser a step's resolutions may hold for a projection the user did not resolve, which
can happen only where no run has a fork of it. No other built-in is a default, and no option resolves every
projection at once. A proven site takes its proven reading whatever the chooser.

**A chooser sees every fork of its kind** in one call per run, or one fork at a time where the step's
decode re-prunes between choices (below), with the run's records and the earlier readings, so it can read a
run's records together, as a claim's several stops must be read. A reading the log excludes is refused,
naming the chooser and the seq.

**The decode order is a rule.** A step asks a run's forks in an order its declaration states, since an
answer can remove candidates that a later fork's answers stood on, and the order is part of what the
manifest's readings mean:
- the 0.2.0 → 0.3.0 step asks `record-claim`, then `stop-honored`, then `lease-bound`, each once per run with
  all its forks;
- the 0.3.0 → 0.4.0 step's engine (`v0_3_0_to_v0_4_0_engine.py`'s `decode`) asks per fork: its subjects
  from the last claim back to the first, then the run; for each subject its End, then its Span, then its
  Origin, then its Writer; within a projection, position by position in log order. After each answer only
  the candidates through it are kept, and the next fork is read off what remains. Its `end-time` forks are
  asked last, on the decided lineage, commit by commit, then rewind by rewind. The last claim goes first
  because its records are the ones a resumed worker and every current read rest on; the claims before it
  are read in the light of what it was.

**The detection pass.** `runstate migrate` first runs every selected run's chain of steps in memory, sealing
nothing and changing no log's content: step 2 above, for the whole root before any run is sealed. (On SQLite
it opens each log read-write, as every pre-seal read does, so it can roll back a dead writer's hot journal or
checkpoint its WAL; neither changes a committed record.) It goes **step by step**. A step's forks are found
on its input log, which for a later step is the earlier step's output. A projection **forks** in the root
when any run has a fork of it, and a divergence **fires** in it when any run's dry run fires it. **The pass
refuses unless every projection that forks has a `--resolve` and every divergence that fires has an
`--accept`,** and then migrates nothing: the user meets every question before anything is written. There is
no default and no accept-all.

**A later step's forks are undecided until every fork of the earlier steps in the chain is resolved, and
every divergence they fire accepted,** since they are found on their output. So a user resolves in one round
per step: a root at format 0.2.0 that migrates to 0.4.0 takes at most two. On refusal the pass prints a
summary **per projection**, never a list of every site:
- the step, the projection's name and its one-sentence definition;
- its readings, and its built-in choosers, each with a one-line description;
- how many forks it has, in how many runs;
- a few example forks: the first three, by run id and seq;
- the facts whose statement would settle them, each with how many forks it would settle, and how many no
  fact can;
- the option to add, `--resolve <projection>=<chooser>`.

It prints one summary per divergence that fires unaccepted (below), names the later steps whose forks are
undecided until these are answered, and lists the runs already refused whatever is chosen (a live run, a
step's own refusal), so the user meets them in the same round.

```
stop-honored (0.2.0 -> 0.3.0): which stops a lifecycle.stopped answered, of those since the previous one.
  readings: the seqs of the stops it honored
  built-ins: positional  every stop since the previous stopped
             drained     the stops its claim's newest heartbeat before it had consumed
             none        no stop: each stays pending
             refuse      refuse every run with a fork of it
  forks: <n> in <m> runs, e.g. <rid> seq <seq>, <rid> seq <seq>, <rid> seq <seq>
  settled by stating: no fact (<n>)
  add: --resolve stop-honored=<built-in or module:attr>
```

**Divergences.** Detection finds each divergence where its rule fires, on the dry run once the step's forks
are resolved, since a reading can make a rule fire, and reports it by name: the reads it changes and how,
how many sites in how many runs, and the first three sites. **A divergence that fires halts the detection
pass until the user accepts it by name,** `--accept <divergence>`, exactly as a fork without a chooser does
(the owner, 2026-10-26): it is a question the user has not answered, and the approved contract is that
nothing is written until every question is. `refuse` is the only alternative: the invocation is refused,
and a user who wants the runs it fires in left behind names the other runs.

```
ended-lease (0.2.0 -> 0.3.0): live_demand no longer lists a lease bound to the latest claim once a terminal
  names that claim, which 0.2.0's listed though no worker would serve it.
  sites: <n> in <m> runs, e.g. <rid> seq <seq>, <rid> seq <seq>
  add: --accept ended-lease
```

A divergence is named by the link that made the read differ: a rule of the step, or a reading other than
the one the input format's own read took. A read changed back along the way changed nothing, so nothing
fires for it.

An acceptance holds for every run of the invocation where its rule fires; an `--accept` of a divergence that
fires nowhere is allowed, so a consumer can keep one set of choices for every root.

**The command line.** Each choice is resolved once, before the detection pass.
- **`--assume <module:attr>`,** repeatable, imports the object and checks that it is a statement of a
  registered step's facts, which it then states for every step of that kind. It refuses an import that
  fails, an object that is no step's statement, and two for one step.
- **`--resolve <projection>=<chooser>`,** repeatable, one per projection. `<projection>` is a name some
  registered step declares, so a consumer can keep one set of choices for every root. Names are unique
  across the registered steps: the runner refuses to start when two steps declare one projection, or one
  divergence, as it refuses two steps that leave one format. `<chooser>` is a built-in's name, spelled with
  no colon, or `module:attr`, naming an instance that implements the `Chooser` Protocol. It refuses an
  unknown projection, an unknown built-in, an import that fails, a class where an instance is needed, and an
  object that is no chooser. A `--resolve` for a projection that does not fork is accepted. An explicit
  `--resolve <projection>=refuse` refuses only the runs with a fork of it, and the rest migrate. A
  projection that forks and is left unresolved refuses the whole invocation.
- **A consumer's chooser is validated where it is called,** since the Protocol's check is only that the
  object has `choose`. A chooser that raises, answers in another shape than its projection's, or was written
  for another projection's question refuses the run, naming the run, the projection and the chooser, and
  never escapes as a traceback. The step then checks the answer against what the log proves.
- **`--accept <divergence>`,** repeatable, names a divergence some registered step declares, resolved to its
  declaration once. It refuses an unknown name and a repeated one.
- **`--cap N`** bounds the states a step may hold at once on one run before it refuses the run, naming the
  record: the 0.3.0 → 0.4.0 engine's cap. It decides a refusal, so it is set at the edge, the command line,
  and recorded with each migrated run, never as a library default. The command line's value is 3,000. The
  engine keeps every layer's edges for its backward pass, so its memory is the cap times the records, about
  420 bytes a state: 664f8795, the widest real log measured, holds 2,344 states a layer over 4,017 records
  in 1.8 GB. 3,000 clears that layer by a quarter, and refuses b02eb431 under facts too wide to follow at
  its seq 81 in 6 s and 105 MB, where 20,000 exhausted 4 GB.
- **`--budget N`** bounds the states a step may hold on one run summed over every layer, which its memory
  follows, refusing the run past it, naming the record; the cap alone does not, since a log holding a layer
  near the cap over many more records would exceed a machine's memory (about 16 GB at 3,000 over 13,000).
  The command line's value is 6,000,000 states, about 2.5 GB: it clears 664f8795's 4.3 million by two fifths.
  The build is the peak: the decode that follows narrows the built graph in place of itself, holding the
  graph and the one its answer leaves, and its working set fits in what the build freed (`cli.py` gives the
  measurements).
- **`--run-on N`** is how many labels past the log's highest step a run of passes that no record pins is
  followed, per claim: the 0.3.0 → 0.4.0 engine's horizon, past which such a run is cut. It decides how many
  silent passes a reading may hold, so it is set at the edge and recorded too. The command line's value is
  2, the oracle's, whose histories run on by at most one pass.

**Carrying on past refusals.** Once the detection pass is satisfied, `migrate` migrates every run it can.
A run refused by a chooser, by `refuse`, or by a step's own refusal is left untouched, and the other runs go
on. (A divergence refuses a run here only where records landed after the pass read it.) `migrate()` returns
the runs it moved and every refusal, each naming its run, and the command prints them and exits nonzero
when any run was refused.

**The manifest** records, for each root, what every migrated run was migrated under. The log formats are
untouched: the manifest belongs to the root, outside every format's address.
- **SQLite:** `<root>/runstate-migrations.jsonl`, one JSON object per line, appended. It is neither a format
  directory (§4, check 1) nor a legacy address (§4, check 4).
- **Postgres:** the table `manifest` in the schema `runstate_migrations`. That schema is outside every
  `runstate_v…` schema, so check 1 does not read it as a format, and the table is not named `log`, so
  check 4 does not read it as a legacy log.
- **One entry per run per step:** the run id; the step's `FROM` and `TO`; the code that ran it, as the
  runstate version with the commit its install records (a git pin's), and the SHA-256 of the step module's
  source, since a pin's version string (`0.3.0.dev0`) names no code; when; the limits; the facts, as `--assume`
  spelled them (null when unstated) and as the step renders them, the classifier's identity included; each
  projection's chooser as it was given, a built-in's name or `module:attr`, or null where none was given, as
  for a projection that forked in no run; each fork's reading, keyed by its site's seq and then its subject
  (a step may key a projection whose forks have no site of their own otherwise, saying how: the 0.3.0 →
  0.4.0 step keys `end-time` by claim seq and then the commit's index, or `rewind <e>` for the rewind that
  closed the claim's epoch `e`, `commits-and-lineage.md` §7); and each divergence that fired, with its
  sites, every one accepted. Proven sites are not recorded.
- **When it is written:** with the run's new log. On Postgres the entry is inserted in the sealing
  transaction. On SQLite it is appended before the rename that publishes the log, so a migrated run always
  has its entry. A crash between the two leaves an entry for an attempt that did not publish; a re-run
  appends its own. **A reader takes a run's latest entry per step:** the attempt that published last wrote
  the last line. A later step's `history` is these, oldest first. On NFS, `O_APPEND` is not atomic, so two
  migrators of one root can interleave lines: run one migrator per root (a known limit).
- **Copying a run** between roots (§8) copies its log at its format's address. Its manifest entries go with
  it only if they are copied by hand.
- **Collecting a run** (`store.md` Recipe 3) leaves its manifest entries behind unless the collector deletes
  them too (§11).

**The escape hatch: a whole step is a Strategy too** (documented, not built). A consumer whose history does
not fit a step's declared facts and projections may supply its own step for that edge, named `module:attr`:
an object implementing the Step protocol, with the stock step's `FROM` and `TO`. It may import the stock
step's passes and reuse them. The same runner runs it under the same invariants:
- it keeps every input row's seq and topic, the one renumbering step's exemption aside;
- every row it writes conforms to the `TO` format's schemas, which the runner checks row by row for a
  custom step, since no runstate test covers it;
- its refusals come before the seal;
- it declares at least the stock step's divergences, and reports where each fires;
- the manifest records it by its `module:attr`, beside the resolutions it declares.

No command-line option takes one yet. The trigger to build it is a consumer whose log a step's declared
facts and projections cannot express.

**Redo is a known gap, not built.** A migrated run's old log is sealed and its new one exists, so `runstate
migrate` does not migrate it again under other choices. §8's rollback is the manual path: make the old log
writable, delete the new one, and migrate again. The manifest says what was chosen the first time, and the
new entry is appended beside the old.

**The precedent.** Every later step declares its facts, its projections and its divergences, each empty
where it has none, so every step takes `resolutions` and the detection pass treats every chain alike. The
first two steps declare theirs in [`reference-by-name.md`](reference-by-name.md) §6 (format 0.2.0 → 0.3.0:
no facts, three projections, five divergences) and [`commits-and-lineage.md`](commits-and-lineage.md) §7
(format 0.3.0 → 0.4.0, rebuilt on its engine by the plan's Task 22). The mechanism's tests are in §10.

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
- the ambiguity mechanism (§6, "Ambiguity"; `tests/test_migrate.py`, with toy steps that fork, take facts
  and diverge, and `tests/test_migration_v0_2_0_to_v0_3_0.py`):
  - the command line refuses an unknown projection, divergence or built-in, a repeated `--resolve` or
    `--accept`, a failed import, an object that is no chooser or no step's facts, two `--assume`s for one
    step, a cap below one, and two steps declaring one name;
  - `detect` takes no chooser, and finds the same forks whatever the choices; a stated fact settles the
    forks it names, and the manifest records it;
  - the detection pass migrates nothing while a projection that forks has no `--resolve` or a divergence
    that fires has no `--accept`, prints one summary per question with at most three examples and the facts
    that would settle a fork, keeps the runs already refused, and holds a later step's forks undecided
    until the earlier step's are answered;
  - an explicit `refuse` refuses only the runs with a fork, and the others migrate;
  - a run refused by a chooser or a step leaves the other runs migrated, and the command exits nonzero; a
    chooser that raises, answers in another shape or was written for another projection is a refusal
    naming the run, the projection and the chooser, and a class is refused where an instance is needed;
  - a chooser sees its forks with the earlier readings; a later step receives the earlier steps' entries,
    from this invocation and from the manifest; a proven site reads the same under every chooser, and a
    reading the log excludes is refused, a `record-claim` reading naming a claim proven dead among them;
  - a divergence halts the pass until it is accepted, and is reported once per name; each of the 0.2.0 →
    0.3.0 step's divergences fires in exactly the runs where a read it names answers differently, at the
    records whose read differs (`reattributed-record` at every fork read otherwise than by position),
    checked against master's reads on the golden logs under four sets of readings, and a `record-claim`
    reading that changes the verdict fires `reattributed-record`, not the positional leak's `stale-beat`;
  - each of the 0.3.0 → 0.4.0 step's divergences fires where its rule applies, and every read the
    migration changes on the goldens and on the corpus is attributed to one that fired; its `exit-honored`
    fires at exactly the exits of the simulator's stated costs (`commits-and-lineage.md` §7);
  - the manifest holds one entry per migrated run per step, written in the sealing transaction on Postgres,
    and on SQLite before the publish, the latest per step read back; none for a refused run; a root holding
    it opens as it did without it.

## 11. Ripple

- `channel-locators.md`: the address scheme and §4's checks.
- `channel-postgres.md`: a schema per format.
- `store.md`: a log's address under placement, and Recipe 3's deletion rule.
- `design-v0.2.md` §4: the locators check formats.
- `CLAUDE.md`: the upgrade procedure, beside the pinning note.
- `../backlog/release-and-stability-contract.md` §(b): resolved by this spec.
- `store.md` Recipe 3: collecting a run also deletes its entries in the root's migration manifest (§6,
  "Ambiguity").
- `CLAUDE.md`: the upgrade procedure's `runstate migrate` takes a `--resolve` for each projection the
  detection pass reports, and an `--accept` for each divergence it reports. The code ships with the plan's
  Task 21; the line is the owner's to add.
- `reference-by-name.md` §6 and `commits-and-lineage.md` §7: each step's declared facts, projections and
  divergences.

## Related

- [`reference-by-name.md`](reference-by-name.md) — introduces format 0.3.0, and the first migration step.
- [`channel-locators.md`](channel-locators.md) — the locators this spec extends.
- [`../backlog/identity-in-records.md`](../backlog/identity-in-records.md) — the design both specs serve.
