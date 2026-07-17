"""The implementer's guide's wire examples never drift from the schema stack.

`docs/implementers-guide.md` embeds canonical envelope records so a non-Python
implementer can copy conforming bytes. This is the same drift-guard mechanic as
`tests/test_schema.py`, pointed at the guide's fenced code blocks instead of the
reference implementation's emissions.

The guide's convention (documented in its "Validated wire examples" section):

  * ```json  blocks are VALID, complete envelope records. Each must validate
    against the envelope schema AND the convention schema for its topic, and
    together they must cover every reserved topic (so "everything validated" is
    not hollow -- the same assertion test_schema.py makes of its scenario).
  * ```jsonc blocks are DELIBERATELY INVALID envelope records. Each must be
    REJECTED by the envelope schema or its topic's convention schema. (They hold
    no actual comments -- the distinct fence label is the only signal -- so
    json.loads still parses them.)

No other JSON in the guide uses these two fences.
"""

import json
import re
from pathlib import Path

import pytest

from runstate.vocabulary.payloads import Topic

jsonschema = pytest.importorskip("jsonschema")
from jsonschema import Draft202012Validator  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent
_PROTO = _ROOT / "protocol"
_GUIDE = _ROOT / "docs" / "implementers-guide.md"


def _validator(name, version="v0.2"):
    schema = json.loads((_PROTO / f"{name}-{version}.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


ENVELOPE = _validator("envelope")
CONVENTIONS = {
    "control.": _validator("subscription"),
    "lifecycle.": _validator("lifecycle", "v0.4"),
    "launcher.": _validator("launcher", "v0.4"),
    "value": _validator("value"),
}

# The complete reserved routing-key set, coupled to the enum (a hand-copied
# literal here would let a new reserved topic ship with the guide silently
# never documenting it -- test_schema.py pins its own copy against Topic too).
ALL_RESERVED_TOPICS = {str(t) for t in Topic}


def _convention_for(topic):
    for prefix, v in CONVENTIONS.items():
        if topic == prefix or topic.startswith(prefix):
            return v
    raise AssertionError(f"no convention schema for topic {topic!r}")


# A fenced block: the language token is captured as a whole word, so "json" and
# "jsonc" never alias (a lazy body stops at the first closing fence line).
_FENCE = re.compile(
    r"^```(?P<lang>[a-z]+)\n(?P<code>.*?)\n```$", re.DOTALL | re.MULTILINE
)


def _blocks(lang):
    text = _GUIDE.read_text()
    return [
        json.loads(m.group("code"))
        for m in _FENCE.finditer(text)
        if m.group("lang") == lang
    ]


def test_guide_exists():
    assert _GUIDE.is_file(), f"the guide is missing at {_GUIDE}"


def test_valid_examples_conform_and_cover_the_vocabulary():
    records = _blocks("json")
    assert records, "no ```json envelope examples found in the guide"
    seen = set()
    for record in records:
        # every valid example must pass BOTH the envelope schema and its
        # topic's convention schema (exactly test_schema.py's _validate).
        ENVELOPE.validate(record)
        _convention_for(record["topic"]).validate(record)
        seen.add(record["topic"])
    # the examples must exercise the whole reserved vocabulary, else
    # "everything validated" is hollow (test_schema.py makes the same check).
    assert seen == ALL_RESERVED_TOPICS


def test_invalid_examples_are_rejected():
    records = _blocks("jsonc")
    assert records, "no ```jsonc (invalid) examples found in the guide"
    for record in records:
        rejections = 0
        for validator in (ENVELOPE, _convention_for(record["topic"])):
            try:
                validator.validate(record)
            except jsonschema.ValidationError:
                rejections += 1
        assert rejections >= 1, (
            f"a ```jsonc example was accepted by both schemas but is documented "
            f"as invalid: {record!r}"
        )
