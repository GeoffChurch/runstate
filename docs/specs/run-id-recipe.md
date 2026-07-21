# Recipe: reuse by content-addressed `run_id`

**A pattern, not a library function** (decided 2026-05-31; this supersedes the
earlier `default_run_id`/`hash_files` spec). Run identity is opaque and
caller-chosen, so make `run_id` a content hash of the inputs that determine your
run's output; then "have I already run this?" is just "does that run's log exist
and show a terminal result?" Canonicalizing your inputs is one line, and the
choice of *what counts as an input* is yours — so runstate ships the **pattern
and the lessons**, not code. (A shipped helper with default file-globs would only
hide the partition choice and risk silently omitting an output-determining file.)

## The pattern

```python
import json, hashlib
from runstate import attach_channel, peek_terminal, RunNotFound

def run_id(inputs: dict) -> str:                # inputs = everything that
    canon = json.dumps(inputs, sort_keys=True, allow_nan=False)   # determines output
    return hashlib.sha256(canon.encode()).hexdigest()[:32]

def hash_code(paths) -> str:                    # your partition: which files count
    h = hashlib.sha256()
    for p in sorted(paths):                     # sorted → deterministic
        h.update(p.encode()); h.update(b"\0")
        h.update(hashlib.sha256(p_bytes(p)).digest())   # content, not git state
    return h.hexdigest()

rid = run_id({**config, "seed": seed, "code": hash_code(my_files)})
try:
    with attach_channel(rid, root=ROOT) as ch:   # existing-only: never fabricates rid
        prior = peek_terminal(ch)
except RunNotFound:
    prior = None                                 # no such run yet
if prior is not None and prior.outcome == "completed":
    ...   # reuse
else:
    ...   # launch into rid (create_channel births it)

# Or for a sweep: key each Variant by run_id and call sweep(resume=True) — it
# already skips any run with a terminal record, so that *is* reuse-by-hash.
```

## What to get right (the lessons — this is the real content)

- **Hash code by *content*, not git's dirty/clean flag.** Two byte-identical
  checkouts must hash the same regardless of commit or dirty state. (mycooc
  shipped a bug here — a whole "are these fingerprints compatible?" predicate —
  by comparing dirty-vs-clean instead of content.)
- **Fold everything into one dict and hash it once.** Putting the code-hash in as
  a dict value lets JSON provide the field framing for free — no manual
  length-prefixing, no cross-field collisions.
- **`sort_keys=True`, `allow_nan=False`, no `default=str`.** Key order must not
  matter; `NaN`/`inf` and unserializable values should *raise*, not silently
  collide. This canonical form **is** your equality contract.
- **Own your partition — don't hide it.** Reuse only goes wrong via a *false hit*:
  you left an output-determining input out (a data file, a lib version, a flag).
  Seed, code, data are all just inputs — include exactly what determines your
  result. Which outcomes count as reusable (and any min-steps floor) is likewise
  your policy, applied via `peek_terminal`.

## Extendable runs: exclude the step-target

For a run you intend to *extend* (run further later, reusing the prefix), the
`run_id` must hash the trajectory-determining inputs **minus the step-target**
(`max_steps`/`N`) — the target is the *extend axis*, not identity (else `steps=100`
and `steps=500` are different runs and nothing extends). Relaunch the same `run_id`
with a higher target; the worker resumes from its `run_id`-keyed checkpoint and
continues the run-absolute `step`. The reuse check is then "did the prior run reach
≥ N?" — the min-steps floor above. (Mechanics: `docs/specs/run-episodes.md`.)

**Precondition (you own it): the trajectory must be *target-independent*** —
`loss[42]` is the same whether you asked for 100 or 500 steps. A schedule keyed on
the total (cosine-decay-over-`max_steps`) breaks this: a different target is then a
*different run*, and reusing a shorter run's prefix is silently wrong. If your
schedule depends on the total, either key it on the `run_id` (a fixed horizon) or
don't treat the run as extendable. This is the one place extend can silently
corrupt reuse.

## Identity locates (2026-06-11)

Under `specs/store.md` Recipe 1, the rid is also the run's **address**:
the channel and artifacts live at `runs/<rid[:2]>/<rid>/` and
`rid → location` is path construction, not a stored fact. The recipe's
kernel condition gains a second consumer: a false *hit* now silently
*converges* two intended computations on one home (no duplicate artifacts
to diff), so owning the partition matters more, not less. (Note for
one-root helpers: `sweep` and the lazy-launch activator hold one
launcher/root over N rids and need a per-rid wrapper under this layout.)

## Derived runs: identity composes (2026-06-11)

A computation *about* a run (analysis, evaluation, rendering) is itself a run,
and the recipe composes: its `run_id` hashes **the full read-set's content**
plus the analyzer's code and parameters —

```python
rid_analysis = run_id({
    "analyzed": R_rid,                  # provenance / discoverability
    "inputs": hash_files(read_set),     # EVERYTHING the analyzer reads
    "params": {...},                    # analysis args that shape output
    "code": hash_code(analyzer_sources),
})
```

Two traps, both aliasing (identity must never alias distinct computations):
`h(R_rid, code)` is stable while R's artifacts evolve across episodes; and a
*partial* read-set (just the tensors, not the metrics CSVs the analyzer also
reads) hides the same trap one layer down. Freshness then needs no policy —
the identity IS the freshness check (R changes ⟹ the read-set changes ⟹ a
new derived id; stale analyses remain as content-addressed history). Hash
only *settled* snapshots (`live_episode(R) is None`) — the hash and the
worker's later load are two reads of mutable files. Full rationale +
the one-step-run convention: `specs/derived-runs.md`.

## Why no shipped function

Canonicalization is one line; content-hashing a file set is trivial *and*
opinionated (which files count is yours). There's nothing here for runstate to
own that wouldn't be either trivial or a partition opinion. `run_id = h(inputs)`
is the "function C" point-memoization case from `design-v0.3-exploration.md` §6;
the substrate already makes content-addressed identity free by letting you choose
`run_id`.
