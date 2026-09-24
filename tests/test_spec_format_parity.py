# -*- coding: utf-8 -*-

"""No serialization is second-class, and here is the check that says so (#490).

`specs/formats/` holds one create spec and one search spec written out four
times - `sprint.{yaml,json,jsonl,toml}` and `audit.{yaml,json,jsonl,toml}`.
They exist to be compared: four files that parse to the same spec and plan
the same work are what turns "phabfive reads four formats" from a sentence
in a docstring into something CI fails on.

The comparison is on the **plan**, not on the bytes. JSON and TOML cannot
carry the YAML file's comments and TOML has no null, so byte equality is not
a thing four files can have; what they can have is an identical folded body
and an identical list of plan records, which is everything that reaches the
instance.

Everything here is offline. The create plan is built with an empty
`Resolution()`, which `phabfive.spec.create.plan_create` uses as given and
never adds to, so every reference-valued key comes back unanswered and no
instance is asked about anything - the same way
`tests/test_create_port_parity.py` counts what a document walks to. The
plans are therefore the *shape* of the four documents, which is precisely
what is under test: whether the serialization changed it.
"""

import json
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from phabfive.maniphest.core import Maniphest
from phabfive.search import plan_item
from phabfive.spec import Kind, Severity, load_spec, validate_offline
from phabfive.spec.create import plan_create
from phabfive.spec.loader import SpecFormat, detect_format
from phabfive.spec.online import Resolution

REPOSITORY = Path(__file__).resolve().parent.parent
FORMATS_DIR = REPOSITORY / "specs" / "formats"

#: Every serialization, and the suffix each is written with.
FORMATS = ("yaml", "json", "jsonl", "toml")

#: The parity fixtures: one stem per kind, one file per format.
STEMS = {"sprint": Kind.CREATE, "audit": Kind.SEARCH}

PAIRS = [(stem, extension) for stem in STEMS for extension in FORMATS]
PAIR_IDS = [f"{stem}.{extension}" for stem, extension in PAIRS]


def _path(stem, extension):
    return FORMATS_DIR / f"{stem}.{extension}"


def _spec(stem, extension):
    return load_spec(_path(stem, extension))


def _app():
    """An already-constructed app, which is all planning ever takes.

    `Phabfive.__init__` is faked and `.phab` is a `MagicMock`, the way
    `tests/test_create_port_parity.py` does it: nothing here opens a socket,
    and nothing reads a configuration file.
    """
    with patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None):
        app = Maniphest()

    app.phab = MagicMock()
    app.url = "http://phorge.example.com"
    app.conf = {}
    app.lookup_store = None
    return app


def _comparable(value):
    """One plan value, in a shape two plans can be compared by.

    A parsed status or column pattern is a plain object with no `__eq__`, so
    two of them built from two files are never equal however identical the
    files are. Its `__str__` is the pattern as written, which is exactly the
    part that has to match, so that is what is compared.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, Mapping):
        return {key: _comparable(item) for key, item in value.items()}

    if isinstance(value, (list, tuple, set, frozenset)):
        return [_comparable(item) for item in value]

    return f"{type(value).__name__}({value})"


def _create_records(stem, extension):
    """What applying the create spec would create, as plain records."""
    spec = _spec(stem, extension).render()
    plan = plan_create(_app(), spec, validate=False, resolution=Resolution())

    return plan.as_records()


def _search_plans(stem, extension):
    """Every search the spec holds, interpreted into runnable parameters."""
    spec = _spec(stem, extension).render()
    items = spec.items("search")

    return [
        {
            "type": plan.object_type,
            "title": plan.title,
            "description": plan.description,
            "params": _comparable(plan.params),
        }
        for plan in (
            plan_item(_app(), item, index=index, total=len(items))
            for index, item in enumerate(items, start=1)
        )
    ]


# --------------------------------------------------------------------------
# The fixtures themselves
# --------------------------------------------------------------------------


@pytest.mark.parametrize("stem, extension", PAIRS, ids=PAIR_IDS)
def test_the_parity_fixture_is_shipped(stem, extension):
    """Guards everything below: three files and a gap would pass a walk."""
    assert _path(stem, extension).is_file(), (
        f"specs/formats/{stem}.{extension} is what makes the four formats "
        "comparable; the parity claim is unchecked without it"
    )


@pytest.mark.parametrize("stem, extension", PAIRS, ids=PAIR_IDS)
def test_the_parity_fixture_is_written_in_its_own_format(stem, extension):
    """Each file really is that serialization, not a copy with a new suffix.

    Detection goes by suffix first, so a YAML document saved as `.json`
    would be read as JSON and fail - but a file that is legal in two formats
    would not, and copying one into all four is the obvious way to make a
    parity test pass while proving nothing.
    """
    path = _path(stem, extension)
    text = path.read_text(encoding="utf-8")

    assert detect_format(path) is SpecFormat[extension.upper()]
    assert detect_format(text=text) is SpecFormat[extension.upper()], (
        f"{path.name} does not read as {extension} on its content alone"
    )


@pytest.mark.parametrize("stem, extension", PAIRS, ids=PAIR_IDS)
def test_the_parity_fixture_validates_offline(stem, extension):
    """A fixture that does not validate proves parity of two broken files."""
    spec = _spec(stem, extension)

    assert spec.envelope.kind is STEMS[stem]

    problems = [
        problem
        for problem in validate_offline(spec)
        if problem.severity == Severity.ERROR
    ]

    assert not problems, "\n".join(
        f"{problem.code}: {problem.object}.{problem.field}: {problem.reason}"
        for problem in problems
    )


# --------------------------------------------------------------------------
# The parity itself
# --------------------------------------------------------------------------


@pytest.mark.parametrize("stem", sorted(STEMS), ids=sorted(STEMS))
def test_the_four_formats_parse_to_the_same_spec(stem):
    """Same envelope, same variables, same body, whatever it was written in."""
    specs = {extension: _spec(stem, extension) for extension in FORMATS}
    first = specs["yaml"]

    for extension, spec in specs.items():
        assert spec.envelope.kind is first.envelope.kind, extension
        assert spec.envelope.spec == first.envelope.spec, extension
        assert spec.envelope.metadata == first.envelope.metadata, extension
        assert spec.variables == first.variables, extension
        assert spec.body == first.body, (
            f"{stem}.{extension} parses to a different body than {stem}.yaml:\n"
            f"  yaml: {json.dumps(first.body, sort_keys=True, default=str)}\n"
            f"  {extension}: {json.dumps(spec.body, sort_keys=True, default=str)}"
        )


def test_the_four_create_formats_plan_the_same_objects():
    """One unique plan across the four, which is the acceptance criterion."""
    plans = {extension: _create_records("sprint", extension) for extension in FORMATS}

    assert plans["yaml"], "the fixture plans nothing; there is nothing to compare"

    unique = {
        json.dumps(records, sort_keys=True, default=str) for records in plans.values()
    }

    assert len(unique) == 1, "specs/formats/sprint.* plan differently:\n" + "\n".join(
        f"  {extension}: {json.dumps(records, sort_keys=True, default=str)}"
        for extension, records in plans.items()
    )


def test_the_four_search_formats_plan_the_same_searches():
    """The same, for the search spec: same searches, same parameters."""
    plans = {extension: _search_plans("audit", extension) for extension in FORMATS}

    assert len(plans["yaml"]) > 1, (
        "the fixture holds one search; a multi-item document is what the "
        "folded item keys are being compared for"
    )

    unique = {
        json.dumps(planned, sort_keys=True, default=str) for planned in plans.values()
    }

    assert len(unique) == 1, "specs/formats/audit.* plan differently:\n" + "\n".join(
        f"  {extension}: {json.dumps(planned, sort_keys=True, default=str)}"
        for extension, planned in plans.items()
    )


# --------------------------------------------------------------------------
# The two spellings the other formats have no equivalent of
# --------------------------------------------------------------------------


@pytest.mark.parametrize("stem", sorted(STEMS), ids=sorted(STEMS))
def test_the_toml_fixture_uses_an_array_of_tables(stem):
    """TOML says "several items" with `[[key]]`, and the fixture shows it."""
    text = _path(stem, "toml").read_text(encoding="utf-8")
    key = "tasks" if STEMS[stem] is Kind.CREATE else "searches"

    assert text.count(f"[[{key}]]") > 1, (
        f"specs/formats/{stem}.toml is the file that demonstrates the "
        f"array-of-tables spelling; it needs more than one [[{key}]]"
    )


@pytest.mark.parametrize("stem", sorted(STEMS), ids=sorted(STEMS))
def test_the_jsonl_fixture_is_one_document_per_line(stem):
    """JSONL's whole shape: every line is a document, and nothing wraps them."""
    lines = [
        line
        for line in _path(stem, "jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert len(lines) > 1, (
        f"specs/formats/{stem}.jsonl is the file that demonstrates one "
        "document per line; a single line demonstrates nothing"
    )

    for number, line in enumerate(lines, start=1):
        document = json.loads(line)

        assert isinstance(document, dict), f"line {number} is not a document"
