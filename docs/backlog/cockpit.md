# cockpit — a control-plane TUI (now its own project)

**MOVED 2026-07-17 to [`GeoffChurch/runstate-tui`](https://github.com/GeoffChurch/runstate-tui)**
(private). The app's live design is the README there; git carries the former full design
in this file's history (the identity, the three-unit architecture, the scale numbers, and
the item-4 adversarial pass that refuted `lazy creation` in favour of `create=False`). This
entry is now the **runstate-facing** record: why the split, and what the cockpit build is
predicted to demand of runstate.

## Why a separate project

`visualization-story.md`'s discipline: *split by project, not by file.* runstate stays the
minimal cooperative-control protocol + substrate; the viewer is a distinct concern, a
distinct audience, and a distinct evolution timeline. As a **sibling** repo depending on
runstate, the cockpit consumes the public API exactly as a third party would — which is
what makes its governing rule (**public API only; every gap is a finding**) a real
acceptance test rather than an aspiration.

## What it means for runstate — the acceptance test

The cockpit is the [`third-party-observer`](third-party-observer.md) persona **with a
keyboard**. It builds against runstate's existing observer surface and treats every place
the public API can't answer as a filed finding — so it is the concrete consumer that will
either **demand or refute** each of that ledger's remaining items. Predictions the build
will settle (earned ones already reshaped, not speculated):

| ledger item | prediction |
|---|---|
| **3 — run enumeration** | **Refuted.** The app owns discovery (a resolver over its own layout adapters); runstate never needs `list_runs()`. |
| **5 — cursored folds** | **Deferred.** Only ever a *plotting* need; v1 has no plots. |
| **4 — read-only open** | **UNSETTLED — a real runstate question.** `open_channel` creates a `<rid>.db` on a missing run, so a stale pointer manufactures a phantom into a content-addressed store. Candidates, least-commitment first: **stat-before-open** (app-side, no runstate change) covers only the missing-pointer phantom; **`create=False`** is the *only* fix for the second harm — the open silently **schema-mutates a foreign *valid* sqlite db** (`executescript(_SCHEMA)` at open), which stat-before-open cannot catch (file exists; mutation is at open). Re-grade above *minor* accordingly. The `lazy creation` alternative was refuted — see git. Decide when the resolver first hits a stale rid. |
| **6 — third-party stop** | **Live the moment the stop button exists.** Wanted: a public "will a stop sent now be served?" predicate. The observer clock (item 1) shipped, so an observer can now tell live from dead — item 6 may be **partly dissolved already**; the cockpit finds out. |
| **2 — the target** | The missing progress-bar **denominator**. If a viewer's top annoyance is "no target to show progress *toward*", that is the evidence [`../specs/control-target.md`](../specs/control-target.md)'s rework needs; if not, it was speculation. |

The discipline that survived the design: **do not settle items 4 / 6 up front** — each is
demand-driven, surfacing at a specific first touch. Pre-deciding them is the speculation the
whole cockpit design is a reaction against (and the exact failure that sank `control.target`).

## Also here in history

The former full design recorded two ideas worth carrying into the repo (now in its README's
deferred section): **stat-before-open** for item 4, and **`request_id="webui:<unique>"`** on
the stop button as the multi-writer attribution stopgap (confirmed via `await_consumed`).
