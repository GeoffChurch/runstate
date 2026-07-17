# release-and-stability-contract — freezing the wire for strangers

**Status:** PROPOSED (2026-07-16) — drafted for owner ratification; nothing here
is policy until ruled. Each decision below carries a *recommendation*, not a
decision. The `feat/adoptability` thread prepared the release *mechanics*
(packaging metadata, `MANIFEST.in`, a tag-gated PyPI workflow) but left every
*policy* here open.

## The tension this confronts

runstate develops by a **no-compat, develop-by-migration** doctrine that has
served it well and is visible all over the history:

- superseded schema files are **deleted**, not deprecated (`protocol: delete the
  superseded v0.1 artifacts (git carries them)`);
- a convention bump ships an **owner-run migration script** that rewrites
  existing logs in place, and once the author's own logs are migrated the script
  is **deleted too** (`lifecycle-v0.3: migration converged; delete the script`;
  `launcher-v0.3: migration converged; delete the script`);
- `additionalProperties: false` on every convention body means a field can never
  be added silently — every wire change is a deliberate, breaking bump.

This is coherent and cheap **because every log in existence belongs to the
author**. The migration script has to handle exactly the logs on the author's
disk, then it can be thrown away. The moment a stranger runs `pip install
runstate`, writes a log, and upgrades, that assumption breaks: their log is on
*their* disk, the schema file that described it has been deleted from the repo,
and the migration script that would move it forward was deleted three commits
after it converged. **The first public release is the event that makes the
doctrine hostile to its own users.** This document is the pre-mortem.

Nothing forces the doctrine to change *before* a release — pre-release, the
author is still the only user. The recommendations below are about what the
contract becomes *at* the release boundary.

Definitions used throughout:

- **package version** — the `pyproject.toml` `version` (today `0.2.0.dev0`),
  what `pip` resolves and PyPI orders.
- **convention version** — the per-schema `-vX.Y` in `protocol/` (envelope
  `v0.2`; subscription/value `v0.2`; lifecycle/launcher `v0.4`), each on its own
  timeline. These are **not** the package version and never have been.

---

## (a) What is frozen at first release, and what SemVer means

**Tension.** A public release is a promise of stability. But runstate has two
version axes (package vs per-convention wire), and SemVer is defined for one
number. What exactly does "1.2.3" *promise* about a wire format that is itself
five independently-versioned schemas?

**Options.**

1. Freeze the whole wire (envelope + all five conventions at their current
   versions) and map SemVer onto the *package*: MAJOR = any breaking wire bump
   *or* Python-API break; MINOR = additive; PATCH = fixes.
2. Freeze only the envelope + the substrate `Channel` surface (the truly load-
   bearing interop contract) and treat convention bumps as MINOR-with-migration
   pre-1.0, MAJOR post-1.0.
3. Version the wire independently of the package entirely (publish a
   "protocol vX" number distinct from the PyPI version) and let SemVer govern
   only the Python API.

**Recommendation (not ruled).** Option 1 for the package number, with the
freeze scoped explicitly: at first release, **freeze the envelope
(`envelope-v0.2`) and each convention at its shipped version** (subscription
`v0.2`, value `v0.2`, lifecycle `v0.4`, launcher `v0.4`). State in the release
notes that, pre-1.0, a breaking wire bump rides a MINOR package bump *with a
retained migration script* (see (b)); post-1.0 a breaking wire bump is a MAJOR.
The per-convention `-vX.Y` stays the ground truth for *which* shape a given log
speaks; the package SemVer is the coarse "did anything break for me" signal.
The envelope is the strongest freeze candidate — it is provably the initial
object among communication views (design rigor rubric), and nothing has bumped
it since v0.2.

---

## (b) The public form of the migration doctrine

**Tension.** "Delete the migration script once it converges" is right for a
solo author and wrong for a library with users, who upgrade on their own
schedule and need the script that matches *their* starting version.

**Options.**

1. Keep deleting scripts; tell users to pin and never skip a version.
2. **Retain** every migration script in `scripts/` once external users exist,
   named by the bump it performs (the observer-clock script already sets this
   precedent — it is retained, not deleted).
3. Fold migrations into the library as a `runstate migrate` command that
   detects a log's convention version and steps it forward.

**Recommendation (not ruled).** Option 2 as the *policy*, phrased publicly as:
"**Pre-1.0, a minor release may require an offline migration.** Migration
scripts ship in `scripts/`, are named by their bump, and are **retained** (not
deleted) from the first public release onward. Migrations are offline and
quiescence-gated — never run against a live run." Option 3 is the natural
evolution but is real work (version detection, a step ladder) and should be a
*later* backlog item, not a release blocker. The trigger to start retaining is
(c).

---

## (c) The end-of-schema-file-deletion trigger

**Tension.** Deleting a superseded schema file (`lifecycle-v0.3.schema.json`
vanished when `v0.4` landed) means a stranger holding a `v0.3` log has no
machine-readable description of it in the current tree.

**Options.**

1. Stop deleting schema files immediately (retain all versions now).
2. Stop deleting at the **first public release**; keep pruning until then.
3. Never retain; treat git history as the archive.

**Recommendation (not ruled).** Option 2. The trigger is unambiguous and
aligns with (b): **the first `pip install`-able release is the point after which
superseded `protocol/*.schema.json` files are retained** (moved to a
`protocol/archive/` or suffixed, owner's call on layout), and migration scripts
stop being deleted. Before that, keep the current prune-on-bump discipline — it
keeps the pre-release tree clean and costs nothing while the author is the only
reader. Git history (Option 3) is a poor archive for a *user* who should not
have to `git log` a dependency to parse their own data.

---

## (d) Deprecation / notice policy

**Tension.** `additionalProperties: false` makes graceful, additive evolution
impossible by construction — there is no "soft" deprecation window on the wire.
The Python API has more room (a shim, a `DeprecationWarning`).

**Options.**

1. No deprecation windows; every break is a hard bump with a migration (honest
   to the `additionalProperties: false` reality).
2. Deprecation windows on the **Python API only** (a removed/renamed public name
   keeps a re-export + `DeprecationWarning` for one MINOR), while the **wire**
   stays hard-bump-with-migration.
3. Relax `additionalProperties: false` to allow additive fields (rejected: it is
   load-bearing — see CLAUDE.md; it is what makes a bump *deliberate*).

**Recommendation (not ruled).** Option 2. Keep `additionalProperties: false` and
its hard-bump discipline for the wire (a wire change is always a versioned
migration, never a silent addition), and grant the *Python surface* — the names
in `runstate.__all__` — a one-MINOR deprecation window with a
`DeprecationWarning` before removal. Document the asymmetry explicitly so users
do not expect wire graciousness. Reject Option 3 outright.

---

## (e) The version-naming tension (three numbers, no agreement)

**Tension.** Three sources disagree about "what version is this":

- `pyproject.toml` says `0.2.0.dev0`;
- the docs (README status, `design-v0.2.md` revision history, `docs/backlog/`)
  call the shipped arc **"v0.3"** (the run-episodes / memoizer / service-worker
  thread);
- the wire conventions are at **`v0.4`** (lifecycle, launcher).

A newcomer cannot tell which number to trust, and `0.2.0.dev0` badly understates
what has actually shipped.

**Options.**

1. Release as **0.3.0**, matching the docs' "v0.3" narrative; state in the
   release notes that the package version and the per-convention wire versions
   are **decoupled** (the package is 0.3.0; lifecycle/launcher happen to be at
   wire-v0.4, which is fine and expected).
2. Release as **0.4.0** to match the highest wire version (rejected:
   conflates the two axes — it implies the package tracks the max convention
   version, which is not a rule anyone wants to maintain).
3. Reset the docs to stop calling the arc "v0.3" and release as **0.2.0**
   (rejected: throws away accurate history for a smaller number).

**Recommendation (not ruled).** Option 1: **release as `0.3.0`**, and add one
sentence to the README and `docs/README.md` making the decoupling explicit —
"the package version (SemVer, PyPI) and the per-convention wire versions
(`protocol/*-vX.Y`) are independent axes; do not read one from the other." This
is the smallest change that makes all three sources honest simultaneously. The
task that prepared release mechanics deliberately **did not** change
`version = "0.2.0.dev0"` — bumping it is this decision, and it is the owner's.

---

## (f) Schemas are not in the wheel

**Tension.** `protocol/` lives *outside* the `runstate` package (it is a top-
level directory, sibling to `runstate/`). The wheel therefore carries **no
schema files** — verified: `unzip -l` on the built wheel shows only
`runstate/**` + dist-info. A user who `pip install runstate`s and wants to
validate a log against the authoritative schema has nothing to validate against;
only the **sdist** carries `protocol/` (via the new `MANIFEST.in`). The library
is described as "schema-authoritative," but the artifact most people install
omits the schemas.

**Options.**

1. **Relocate** `protocol/` under the package (`runstate/protocol/*.json`) and
   ship it as package-data, so `importlib.resources.files("runstate.protocol")`
   resolves at runtime. Cleanest for users; a real move (imports, tests, docs
   references, the schema conformance test's `_PROTO` path all update).
2. **sdist-only** (status quo after this thread): schemas travel with source,
   not the wheel. Fine if runtime schema access is never a supported feature.
3. **Separate artifact**: publish the schemas as their own versioned package
   (`runstate-protocol`) or a released archive, decoupling the wire spec's
   distribution from the Python library entirely — coherent with the "other-
   language implementations are first-class" stance.

**Recommendation (not ruled).** Decide by answering one question first: **is
runtime schema access a supported feature?** If yes (a user or another-language
port should be able to fetch the canonical schema from an install), **Option 1**
— relocate to package-data — is the right shape, and the schema conformance test
already centralizes the path (`tests/test_schema.py:_PROTO`) so the blast radius
is contained. If schema access is a *development/spec-authoring* concern only,
Option 2 is already done and sufficient. Option 3 is attractive only once a
non-Python implementation actually exists and wants to depend on the wire spec
without depending on the Python package. **This thread deliberately did not
restructure anything** — `protocol/` stays put; this is flagged, not fixed.

---

## What this thread already did (mechanics, all inert)

So the ratification has a clean base to rule on:

- `pyproject.toml` — classifiers, keywords, `[project.urls]`. **Version
  unchanged** (that is decision (e)).
- `MANIFEST.in` — the sdist now carries `protocol/`, `docs/`, `examples/`,
  `scripts/`, `CHANGELOG.md`, `LICENSE`. Wheel contents unchanged.
- `CHANGELOG.md` — Keep-a-Changelog, reconstructed from the repo's records.
- `.github/workflows/release.yml` — tag-gated (`v*`), full-suite-then-build-
  then-publish via PyPI **trusted publishing** (no secrets). **Inert** until the
  owner creates the PyPI project + trusted-publisher binding and pushes a tag.

None of it publishes anything, changes the version, or restructures the layout.
