# `Launcher` Protocol: separate the uniform `open_channel` from the per-launcher `launch`

**Status:** forward-looking; surfaced by the mypy adoption. The interim is
`launcher: Any` in the four orchestration helpers (`relaunch_if_needed`,
`ensure_served`, `sweep`, `launch_producer`).

## The finding

The `Launcher` Protocol (`open_channel` + `launch`) **cannot be structurally
typed**: the two reference launchers have genuinely disjoint `launch` signatures,
driven by their bodies —

- `ThreadLauncher.launch` *calls* `target` → needs `target: Callable`;
- `LocalLauncher.launch` passes `cmd` to `subprocess.Popen` → needs
  `cmd: list[str] | str`.

`Callable ∩ (list | str) = ∅`, so no shared param type exists. `Any` in a Protocol
param is a *promise the impl accepts everything*, which `Callable` violates;
widening to `object` forces positional-only params + `**kwargs` that collide with
the impls' named `target` / `cmd` — erasing all `launch` typing for everyone.
Empirically verified under `mypy --strict`: **no typed `launch` admits both
launchers.**

So the imprecision is unavoidable. Today it is localized as `launcher: Any` in the
four helpers (a commented `Any` with a typed-local on the return), which keeps the
launchers' real signatures *and* clean consumer code (`sweep(ThreadLauncher())`
type-checks). The cost is that those 3–5 line helper bodies call `launcher.launch`
dynamically (trivially correct, test-covered).

## The redesign (when it earns its keep)

The `Launcher` Protocol's `launch` is the fiction (its own docstring says "launch's
target is launcher-specific by nature"). A clean model **separates the uniform part
from the variant part**:

- `Launcher` = just `open_channel(run_id) -> Channel` (genuinely uniform, typeable).
- `launch` becomes per-launcher; the orchestration helpers take a **pre-bound launch
  thunk** (`Callable[[], LaunchHandle]`) — or are otherwise parameterized by *how* to
  launch — rather than a launcher whose `launch` they call polymorphically.

Then `relaunch_if_needed` / `ensure_served` / `sweep` are fully typed, no `Any`.

**Trade-off:** the helper signatures change (the caller pre-binds the
launcher-specific call), so it is an API change. Defer until the typing cost of the
`Any` is actually felt, or until a third launcher sharpens the heterogeneity.
