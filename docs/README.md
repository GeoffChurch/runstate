# docs/

**Start here:** [`overview.md`](overview.md) — a guided tour of the whole system
(what it does, the layers, every component justified). It is the reader's entry
point, and it is **derived, not authoritative**: it restates the design for
newcomers, and where it disagrees with the schemas or `design-v0.2.md`, those
win (authority order at the bottom of this file).

The map:

- [`overview.md`](overview.md) — the guided tour (derived; maintained in sync).
- `backlog/` — forward-looking ideas (living documents; index + standalone files).
- `dead_ends/` — refuted ideas with diagnosis (doesn't exist yet).
- `specs/` — converged, ready-to-implement feature designs.
- `plans/` — dated execution artifacts for landed threads.
- `design-v0.2*.md` — the authoritative prose, plus `design-v0.3-exploration.md`
  (the in-progress v0.3 trail); `design-v0.1*.md` — historical.

## `backlog/`

Forward-looking ideas — things we might do but haven't validated or
prioritized yet. Each entry can be one of:

- **One-liner in `backlog/index.md`** — for ideas that don't warrant a
  full file (a paragraph or a sentence is enough).
- **Standalone file** (`backlog/<topic>.md`) — for ideas worth
  elaborating, especially when they have multiple considered approaches,
  open questions, or prerequisite work.

**Backlog entries are living documents.** They evolve as we investigate
the idea: new alternatives get added, considered-and-rejected approaches
get documented with their reasons, prerequisites get clarified. The
entry's job is to capture the current best understanding of the idea, so
that when we eventually pick it up (or decide to drop it), the
accumulated context is there.

When an idea is executed:
- **Validated and shipped** → delete the entry; the permanent record
  is in the code + a writeup or commit message.
- **Refuted by investigation** → move to `dead_ends/` (parallel
  structure) with a diagnosis of what didn't work and why.

## `dead_ends/` (doesn't exist yet; create when first needed)

Refuted ideas with diagnosis. Parallels `backlog/`. The point isn't
"things we considered briefly and rejected"; it's "things we
investigated seriously enough to learn something, and now the *reason*
they didn't work is worth preserving so we don't re-tread the same
ground."

When `dead_ends/<topic>.md` is created, also drop the entry from
`backlog/index.md` and add it to `dead_ends/index.md`.

## `specs/`

Converged, **ready-to-implement** feature designs — the contract a thread
implements against (`run-episodes.md`, `memoizer.md`, `run-id-recipe.md`,
`stop-discharge.md`, …). A spec typically graduates out of a backlog
investigation. It is authoritative *for its scope* until the design doc and
code absorb it; a shipped spec stays as the record of what was built.

## `plans/`

Dated implementation plans (`plans/2026-06-01-run-episodes.md`, …) — the
execution artifact for a spec that landed. A historical record of *how*;
useful archaeology, never authoritative.

## `design-v0.1*.md`

Historical design documents from the brainstorming arc that produced
v0.1. Five revisions preserved as separate files:

- `design-v0.1-original.md` — initial scope (had Store, Hasher, Phase,
  Preempter, typed events; too speculative)
- `design-v0.1-rev2.md` — dropped Store + Hasher + reuse-by-hash
- `design-v0.1-rev3-overcut.md` — also dropped Preempter + Phase + all
  typed messages (over-correction; subagent review flagged it as
  indistinguishable from `subprocess.Popen` + JSON queue)
- `design-v0.1-rev4.md` — restored cooperative-preempt vocabulary +
  Reconfigure + CompletionReason (still had several smaller issues)
- `design-v0.1.md` — the final rev that informed implementation (drops
  Reconfigure, drops CompletionReason in favor of primitive RunResult
  signals, adds Ack as first-class, etc.)

**The design docs are historical, not authoritative.** The v0.1 pull-first
command/event model these docs describe was **superseded by the v0.2 redesign**
(see below; the authoritative sources are listed there). They're kept because
the rationale for each cut is sometimes useful when revisiting decisions — not
because they describe the current library.

## `design-v0.2*.md`

The v0.2 redesign — **the current model, implemented** in `../runstate/`. A
topic-log substrate with opt-in conventions (cooperative-control, subscription,
lifecycle, launcher) plus reference orchestration (launchers, Watcher, sweep).

- `design-v0.2.md` — the **converged design**: the authoritative prose for the
  v0.2 semantics.
- `design-v0.2-exploration.md` — the full decision trail (an 11-revision
  dialectic) and rejected-alternative diagnoses that produced it. The journey,
  kept for rationale.
- `design-v0.3-exploration.md` — the in-progress v0.3 decision trail (the
  run-episodes / memoizer / Store-dissolution arc); its converged outputs land
  in `specs/`.

**The authoritative sources for what the library currently is:**

1. The JSON Schema stack in `../protocol/` (`envelope-v0.2.schema.json` + the
   per-convention schemas) — the wire format.
2. `design-v0.2.md` — the semantics (plus the shipped `specs/` for the scopes
   not yet folded back into it).
3. The code in `../runstate/` and the tests in `../tests/`.

`overview.md` sits *below* all three — a derived restatement for reading order,
never the tiebreaker. (The v0.1 wire artifacts — `messages-v0.1.schema.json`,
`spec.md` — were deleted from `protocol/` when superseded; git carries them.)
As with v0.1, the design docs capture *direction* and rationale; the code +
schema are what's binding.
