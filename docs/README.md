# docs/

Project documentation lives here. Three categories:

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

**The design docs are historical, not authoritative.** The authoritative
sources for what the library currently is are:

1. `../protocol/messages-v0.1.schema.json` — wire format
2. `../protocol/spec.md` — semantics
3. The code in `../runstate/` and the tests in `../tests/`

The design docs are kept because the rationale for each cut is sometimes
useful when revisiting decisions. They are not maintained as the design
evolves — they're a record of how we got here.

## `design-v0.2*.md`

The v0.2 redesign — **forward-looking, not yet implemented** (the library in
`../runstate/` is still v0.1). v0.2 is a full redesign: a topic-log substrate
with opt-in conventions (cooperative-control, subscription, lifecycle,
launcher).

- `design-v0.2.md` — the **converged design**. Has open questions; not yet
  schema-frozen. The destination.
- `design-v0.2-exploration.md` — the full decision trail (an 11-revision
  dialectic) and rejected-alternative diagnoses that produced it. The journey,
  kept for rationale.

Same caveat as v0.1: the design docs are the *direction*, not authoritative for
what the library currently *is* (that's the code + schema + spec).
