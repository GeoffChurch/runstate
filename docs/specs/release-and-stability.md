# Release and stability — what a runstate release promises

**Status: RULED 2026-11-02** by the owner. It applies from the first tagged release, 0.4.0. It rules the six
decisions the 2026-07-16 draft `docs/backlog/release-and-stability-contract.md` set out; that draft is deleted
with this spec, and git carries its trail. One of them, (b), was already resolved by `log-formats.md`.

## 1. Two versions, one promise

- **The package version** is SemVer: `pyproject.toml`'s `version`, what `pip` resolves and PyPI orders.
- **The wire's version is the log format** (`log-formats.md` §2), named by the release that introduced it. A
  release that does not change the format inherits it (0.4.1 reads and writes `0.4.0`). The per-convention
  schema versions (`lifecycle-v0.6`, `subscription-v0.4`, …) name the parts of a format and are not promised
  separately: a convention bump is a format change.
- **Before 1.0**, a MINOR release may change the log format, always with a retained `runstate migrate` step
  from the previous format (§3). It may also break the Python API, the names in `runstate.__all__`. A PATCH
  release does neither. **From 1.0**, either is a MAJOR.
- **Naming** (the draft's (e)) follows from `log-formats.md` §2 rule 3: a format changes in one commit with the
  package-version bump, so package 0.4.0 introduced log format 0.4.0. The docs' "v0.2" and "v0.3" name design
  arcs (`design-v0.2.md`, `design-v0.3-exploration.md`), not versions.

## 2. Breaks

- **No deprecation windows** (the draft's (d)). A renamed or removed public name breaks at once, in a MINOR
  before 1.0, and `CHANGELOG.md` lists it. There are no re-exports and no `DeprecationWarning` shims: a path
  kept only because a name used to exist is the legacy path the owner's rules forbid.
- **A wire change is always a new log format.** `additionalProperties: false` makes every body change
  deliberate, and the format in a log's address makes every open check it, so a reader never meets a log it
  does not understand.

## 3. Migrations

Resolved 2026-10-03 by `log-formats.md` (the draft's (b)): steps are retained and chained, and
`runstate migrate` moves a log of any registered format to the current one. A step copies and seals, and is
never deleted.

## 4. Schemas

- **The JSON schemas are the protocol**: language-neutral, and authoritative for the wire.
- **Every registered format's schemas are kept, in `protocol/v<format>/`**, named as a log's address names its
  format (the draft's (c)). The current format's are in `protocol/v0.4.0/`. An older format's stay because a
  retained migration step reads that format's logs, and its owner needs their description.
  `tests/test_schema_v0_4_0.py` checks that `protocol/` holds exactly one directory per registered format.
- **The Python implementation re-encodes the current stack by hand.** Each body is a frozen dataclass whose
  constructor refuses what its schema definition refuses (`vocabulary/payloads.py`), and `parse_schedule` spells
  the subscription grammar (`vocabulary/schedule.py`). It never loads the JSON, so its core needs no
  `jsonschema`. The two encodings are one contract, held to one meaning by tests:
  - everything the library writes validates against the schemas (`tests/test_schema.py`);
  - a body parses if and only if its definition admits it (`tests/test_payloads.py`). The bodies compared
    include ones generated from each definition: a candidate value for every field from every JSON type and
    the definition's own enums and consts, crossed field by field, so the check does not depend on which
    edges a person thought of.

    Two disagreements are deliberate and tested: a whole-number float is no integer here, and NaN is no
    number. The subscription grammar's iff test (`tests/test_schedule.py`) still uses hand-picked schedules.
- **The wheel carries no schemas** (the draft's (f)). The wheel is the Python implementation, and it does not
  read them. The protocol ships in the sdist (`MANIFEST.in`) and in the public repository.

  Revive when an installed user, or another language's implementation, needs the schemas from an install.
  Then they move into the package as package data, or into their own versioned artifact.

## 5. What a release is

- A commit that sets `pyproject.toml`'s `version` (no `.dev` suffix) and gives `CHANGELOG.md` its section,
  then the tag `v<version>` on it. `.github/workflows/release.yml` refuses a tag that does not match the
  version, runs the full suite, builds, and publishes to PyPI by trusted publishing.
- The owner creates the PyPI project and its trusted-publisher binding once, before the first tag. Until then
  the workflow's upload fails, which is its interlock.
- PyPI never accepts a version twice, so a mistake in a release is fixed by the next PATCH.
