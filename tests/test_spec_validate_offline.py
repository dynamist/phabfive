# -*- coding: utf-8 -*-

"""Layer 1: everything about a spec that can be decided without the server.

Three promises are asserted here, because all three are easy to lose:

1. **The schema is generated.** A field declared once in
   `phabfive.spec.registry` becomes a property of the JSON Schema document
   and a key the validator accepts, with no second list to keep in step. The
   test adds a field to the registry and looks for it in both.
2. **Every problem is reported.** A spec with six mistakes comes back with
   six records. A first-error-wins implementation passes every other test in
   this file and fails this one.
3. **The report is returned, not raised or printed.** A validation failure is
   a list of `Problem`s; only a spec that cannot be *parsed* raises, and that
   happens in the loader.

`jsonschema` is a test dependency and appears here as an **oracle**: it
checks that the generated document is a valid Draft 2020-12 schema, and that
its verdict on a corpus agrees with the hand-rolled walk's. phabfive does not
depend on it at runtime; see the module docstring of `phabfive/spec/schema.py`
for why.

`tests/test_spec_isolation.py` is the other half of this issue and proves out
of process that none of this reaches a network, a token or a config file.
"""

import copy
import json
import re
from pathlib import Path

import jsonschema
import pytest

from phabfive.exceptions import PhabfiveInputException
from phabfive.spec import (
    Kind,
    Problem,
    Severity,
    build_schema,
    load_spec,
    parse_spec,
    validate_offline,
)
from phabfive.spec.problems import Layer
from phabfive.spec.registry import (
    FIELDS,
    Field,
    FieldKind,
    fields_for,
    spec_keys,
)
from phabfive.spec.schema import (
    JSON_SCHEMA_DIALECT,
    SEARCH_ITEM_KEYS,
    TIME_PATTERN,
    property_schema,
    transition_pattern,
)

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"

SEARCH_TEMPLATES = sorted((TEMPLATES / "task-search").glob("*.yaml"))
CREATE_TEMPLATES = sorted(
    path
    for pattern in ("*.yaml", "*.yml")
    for path in (TEMPLATES / "task-create").glob(pattern)
)


def codes(problems):
    """The stable slugs of a report, which is what a CI job branches on."""
    return [one.code for one in problems]


def errors(problems):
    """Only the problems that would fail a build."""
    return [one for one in problems if one.severity == Severity.ERROR]


def by_field(problems, field):
    """Every problem naming one field, whatever else the spec got wrong."""
    return [one for one in problems if one.field == field]


def check(text, *, kind=None, variables=None):
    """Validate a YAML spec written inline."""
    return validate_offline(
        parse_spec(text, format="yaml", kind=kind), variables=variables
    )


class TestTheCorpusIsThere:
    """Guard the guard: an empty glob would make half this file vacuous."""

    def test_the_search_templates_are_found(self):
        assert len(SEARCH_TEMPLATES) >= 8

    def test_the_create_templates_are_found(self):
        assert len(CREATE_TEMPLATES) >= 3


class TestTheSchemaIsGeneratedFromTheRegistry:
    """One declaration, and the schema follows it."""

    def test_every_declared_field_is_a_property(self):
        schema = build_schema(Kind.SEARCH, object_type="task")
        properties = schema["properties"]["search"]["properties"]

        assert set(properties) == spec_keys("task", "search")

    def test_a_field_added_to_the_registry_appears_in_the_schema(self, monkeypatch):
        """The acceptance criterion: declare it once, and it is everywhere."""
        added = Field(
            name="estimate",
            kind=FieldKind.INT,
            objects=frozenset({"task"}),
            verbs=frozenset({"search"}),
            cli="--estimate",
            help="Story points.",
        )
        monkeypatch.setattr("phabfive.spec.registry.FIELDS", FIELDS + (added,))

        schema = build_schema(Kind.SEARCH, object_type="task")
        properties = schema["properties"]["search"]["properties"]

        assert properties["estimate"] == {
            "type": "integer",
            "description": "Story points.",
        }

    def test_a_field_added_to_the_registry_is_accepted_by_the_validator(
        self, monkeypatch
    ):
        """The other half: the same declaration stops the key being unknown."""
        spec = "kind: search\nsearches:\n  - search:\n      estimate: 5\n"

        assert codes(check(spec)) == ["unknown-key"]

        added = Field(
            name="estimate",
            kind=FieldKind.INT,
            objects=frozenset({"task"}),
            verbs=frozenset({"search"}),
        )
        monkeypatch.setattr("phabfive.spec.registry.FIELDS", FIELDS + (added,))

        assert check(spec) == []

    def test_a_field_removed_from_the_registry_becomes_unknown(self, monkeypatch):
        """The derivation runs in both directions, or it is a coincidence."""
        kept = tuple(field for field in FIELDS if field.name != "limit")
        monkeypatch.setattr("phabfive.spec.registry.FIELDS", kept)

        problems = check("kind: search\nsearches:\n  - search:\n      limit: 5\n")

        assert codes(problems) == ["unknown-key"]

    def test_the_help_line_becomes_the_description(self):
        field = next(one for one in fields_for("task", "search") if one.name == "tag")

        assert property_schema(field)["description"] == field.help

    def test_a_deprecated_field_is_annotated_but_still_valid(self):
        """`deprecated` is an annotation in 2020-12, so the schema still passes."""
        field = next(one for one in fields_for("task", "search") if one.name == "all")
        schema = property_schema(field)

        assert schema["deprecated"] is True
        assert "status: any" in schema["description"]


class TestTheGeneratedDocumentIsRealJsonSchema:
    """It is published for editors and other tools, so it has to be correct."""

    @pytest.mark.parametrize("kind", ["create", "search"])
    def test_it_validates_against_the_metaschema(self, kind):
        schema = build_schema(kind)

        assert schema["$schema"] == JSON_SCHEMA_DIALECT
        jsonschema.Draft202012Validator.check_schema(schema)

    @pytest.mark.parametrize("kind", ["create", "search"])
    def test_it_is_json_serialisable(self, kind):
        """A schema that cannot be written to a file cannot be published."""
        assert json.loads(json.dumps(build_schema(kind)))

    def test_an_unknown_kind_is_refused_rather_than_guessed(self):
        with pytest.raises(PhabfiveInputException, match="create, search"):
            build_schema("destroy")

    def test_an_unknown_object_type_is_refused(self):
        with pytest.raises(PhabfiveInputException, match="Unknown object type"):
            build_schema("search", object_type="sandwich")

    def test_an_object_type_with_no_declared_field_is_permissive(self):
        """A registry that says nothing cannot honestly call a key unknown."""
        schema = build_schema("create", object_type="paste")

        assert schema["additionalProperties"] is True


class TestTheOracleAgrees:
    """jsonschema's verdict and the hand-rolled walk's, over one corpus.

    Only *schema* mistakes, because a JSON Schema structurally cannot see an
    undefined variable or a dangling `$local-id`. Those have their own tests
    below.
    """

    GOOD = [
        "kind: search\nsearches:\n  - type: task\n    search: {limit: 5}\n",
        "kind: search\nsearches:\n  - type: task\n    title: Hi\n    search: {}\n",
        "kind: search\nsearches:\n  - search: {status: open, all: false}\n",
        "kind: search\nsearches:\n  - search: {created-after: 7d}\n",
        "kind: search\nsearches:\n  - search: {created-after: 14}\n",
        "kind: search\nsearches:\n  - search: {include: 'T1,T2'}\n",
        "kind: search\nsearches:\n  - search: {include: [T1, T2]}\n",
        "kind: search\nsearches:\n  - search: {order: 'updated:desc'}\n",
        "kind: search\nsearches:\n  - search: {visible-to: '#platform'}\n",
        "kind: search\nsearches:\n  - search: {column: 'not:in:Done'}\n",
        "kind: create\ntasks:\n  - title: One\n    id: one\n",
    ]

    BAD = [
        "kind: search\nsearches:\n  - search: {limit: lots}\n",
        "kind: search\nsearches:\n  - search: {colum: 'in:X'}\n",
        "kind: search\nsearches:\n  - search: {show-history: yes-please}\n",
        "kind: search\nsearches:\n  - search: {order: sideways}\n",
        "kind: search\nsearches:\n  - search: {created-after: '3 fortnights'}\n",
        "kind: search\nsearches:\n  - search: {include: 'nope'}\n",
        "kind: search\nsearches:\n  - search: {visible-to: nonsense}\n",
        "kind: search\nsearches:\n  - type: sandwich\n    search: {}\n",
        "kind: search\nsearches:\n  - banner: Hi\n    search: {}\n",
        "kind: create\nbadgers: []\n",
        "kind: create\ntasks:\n  - title: One\n    id: 'has space'\n",
    ]

    @staticmethod
    def _oracle(text):
        spec = parse_spec(text, format="yaml")
        validator = jsonschema.Draft202012Validator(build_schema(spec.kind))

        return not list(validator.iter_errors(spec.to_data()))

    @pytest.mark.parametrize("text", GOOD)
    def test_both_say_a_good_spec_is_good(self, text):
        assert self._oracle(text) is True
        assert errors(check(text)) == []

    @pytest.mark.parametrize("text", BAD)
    def test_both_say_a_bad_spec_is_bad(self, text):
        assert self._oracle(text) is False
        assert errors(check(text)) != []

    @pytest.mark.parametrize("path", SEARCH_TEMPLATES)
    def test_the_shipped_search_templates_satisfy_both(self, path):
        spec = load_spec(path, kind="search")
        validator = jsonschema.Draft202012Validator(build_schema(Kind.SEARCH))

        assert list(validator.iter_errors(spec.to_data())) == []
        assert validate_offline(spec) == []

    @pytest.mark.parametrize("path", CREATE_TEMPLATES)
    def test_the_shipped_create_templates_satisfy_both(self, path):
        spec = load_spec(path, kind="create")
        validator = jsonschema.Draft202012Validator(build_schema(Kind.CREATE))

        assert list(validator.iter_errors(spec.to_data())) == []
        assert validate_offline(spec) == []


class TestTheGeneratedGrammarsMatchTheParsers:
    """The published regular expressions and the walk must say the same thing.

    The document is what another tool validates with; the parsers are what
    phabfive validates with. Two spellings of one grammar drift unless
    something compares them.
    """

    ACCEPTED = [
        ("column", "in:Backlog"),
        ("column", "not:in:Done"),
        ("column", "forward"),
        ("column", "in:Review,in:Code Review,in:QA"),
        ("status", "closed+in:Resolved,closed+in:Duplicate"),
        ("status", "any+in:Resolved"),
        ("status", "from:Open:raised"),
        ("priority", "in:High"),
        ("priority", "raised"),
    ]

    @pytest.mark.parametrize("entity,value", ACCEPTED)
    def test_the_published_pattern_accepts_what_the_parser_accepts(self, entity, value):
        from phabfive import transitions

        parse = {
            "column": transitions.parse_column_patterns,
            "status": transitions.parse_status_patterns,
            "priority": transitions.parse_priority_patterns,
        }[entity]

        parse(value)

        assert re.match(transition_pattern(entity), value)

    @pytest.mark.parametrize("value", ["nonsense", "in:", "not:", "", ",", "+"])
    def test_the_published_pattern_refuses_what_the_parser_refuses(self, value):
        """Both halves asserted, which is the point of the class.

        Asserting only that the regex refuses a value says nothing about
        agreement: it passes just as happily on a value the parser accepts,
        which is how `in:A++in:B` sat in this list while
        `parse_column_patterns` read it fine.
        """
        from phabfive.exceptions import PhabfiveException
        from phabfive import transitions

        with pytest.raises(PhabfiveException):
            transitions.parse_column_patterns(value)

        assert not re.match(transition_pattern("column"), value)

    # Every spelling the two once disagreed on, plus the shapes around them.
    # `split_pattern_groups` splits on "," then "+", strips each part and
    # drops the empty ones, so all of these are conditions with noise around
    # them rather than syntax errors.
    CORPUS = [
        "in:A",
        "from:A",
        "to:A",
        "been:A",
        "never:A",
        "not:in:A",
        "not: in:A",
        "in:Up Next",
        "in:A:forward",
        "from:A:backward",
        "from:A : forward",
        "forward",
        "backward",
        "not:forward",
        "not: forward",
        "in:",
        "",
        " ",
        ",",
        "+",
        "not:",
        ":A",
        "in",
        "in:A:bogus",
        "in:A:forward:extra",
        "bogus:A",
        "in:A,",
        "in:A+",
        "+in:A",
        "  in:A",
        "in:A ",
        "in:A,,in:B",
        "in:A++in:B",
        "in:A , in:B",
        "in:A+in:B",
        "in:A,in:B",
        "not:in:A+not:from:B",
        "from:A:forward+in:B,to:C",
        "in :A",
        "in: A",
        "in:   ",
        "from:A:forward:",
        "in:A+,in:B",
        ",in:A,",
        "+ + in:A",
    ]

    @pytest.mark.parametrize("entity", ["column", "status", "priority"])
    @pytest.mark.parametrize("value", CORPUS)
    def test_the_published_pattern_and_the_parser_agree(self, entity, value):
        """The class docstring, enforced over a corpus rather than by eye.

        A hand-picked accepted list and a hand-picked refused list cannot
        catch a disagreement nobody thought to write down; a corpus walked
        through both readers can.
        """
        from phabfive.exceptions import PhabfiveException
        from phabfive import transitions

        parse = {
            "column": transitions.parse_column_patterns,
            "status": transitions.parse_status_patterns,
            "priority": transitions.parse_priority_patterns,
        }[entity]

        try:
            parse(value)
            parsed = True
        except PhabfiveException:
            parsed = False

        assert bool(re.match(transition_pattern(entity), value)) is parsed

    @pytest.mark.parametrize("value", ["1h", "7d", "2w", "3m", "1y", "14", "0.5d"])
    def test_the_published_time_pattern_accepts_what_maniphest_parses(self, value):
        from phabfive.maniphest.utils import parse_time_with_unit

        parse_time_with_unit(value)

        assert re.match(TIME_PATTERN, value)

    @pytest.mark.parametrize(
        "value", ["3 fortnights", "", "-1d", "d", "7q", "1,2", "7%"]
    )
    def test_the_published_time_pattern_refuses_what_maniphest_refuses(self, value):
        from phabfive.exceptions import PhabfiveException
        from phabfive.maniphest.utils import parse_time_with_unit

        with pytest.raises(PhabfiveException):
            parse_time_with_unit(value)

        assert not re.match(TIME_PATTERN, value)

    # What the parser reads only because its "a bare number is days" branch
    # hands the text to float(). They are accidents of that branch, not part
    # of the format, so the published pattern does not bless them - and
    # nothing reports them either, because `_check_time` calls the parser.
    FLOAT_ACCIDENTS = ["1e3", "nan", "inf", "-0", "1_0", ".5", "7.", "+7"]

    @pytest.mark.parametrize("value", FLOAT_ACCIDENTS)
    def test_the_published_time_pattern_is_narrower_than_float(self, value):
        from phabfive.maniphest.utils import parse_time_with_unit

        parse_time_with_unit(value)

        assert not re.match(TIME_PATTERN, value)

    @pytest.mark.parametrize("value", FLOAT_ACCIDENTS)
    def test_the_walk_never_reports_what_maniphest_would_have_run(self, value):
        """The direction that matters: no false positive from the pattern.

        `created-after: "7d "` and `created-after: "1e3"` are values
        `maniphest search --with` runs today. Reporting `bad-time` for them
        would refuse a template that works, which is why the offline pass
        parses instead of matching.
        """
        problems = check(f'kind: search\nsearch:\n  created-after: "{value}"\n')

        assert problems == []

    @pytest.mark.parametrize("value", ["7d ", " 7d", " 7 d "])
    def test_surrounding_whitespace_is_a_time_to_both_readers(self, value):
        from phabfive.maniphest.utils import parse_time_with_unit

        parse_time_with_unit(value)

        assert re.match(TIME_PATTERN, value)
        assert check(f'kind: search\nsearch:\n  created-after: "{value}"\n') == []


class TestTheEnvelope:
    """Step 1: what the file says about itself."""

    def test_an_unquoted_version_is_a_float_and_is_reported(self):
        """`version: 2.1` is the trap this check exists for."""
        problems = check("kind: create\nmetadata:\n  version: 2.1\ntasks: []\n")

        assert codes(problems) == ["wrong-type"]
        assert problems[0].object == "metadata"
        assert problems[0].field == "version"
        assert 'version: "2.1"' in problems[0].reason

    def test_a_quoted_version_is_fine(self):
        assert check('kind: create\nmetadata:\n  version: "2.1"\ntasks: []\n') == []

    def test_an_unknown_metadata_key_is_a_warning_not_an_error(self):
        problems = check("kind: create\nmetadata:\n  owner: alice\ntasks: []\n")

        assert codes(problems) == ["unknown-key"]
        assert problems[0].severity == Severity.WARNING
        assert errors(problems) == []

    def test_an_unknown_spec_version_on_a_hand_built_spec_is_reported(self):
        """The loader refuses one; a frontend building an Envelope can still make one."""
        from phabfive.spec import Envelope, Spec

        spec = Spec(envelope=Envelope(spec="phorge/v9", kind=Kind.CREATE), body={})
        problems = validate_offline(spec)

        assert codes(problems) == ["unknown-spec-version"]
        assert "phorge/v1alpha1" in problems[0].reason


class TestVariables:
    """Step 2: every name the spec reads has a value, and none of them loop."""

    def test_an_undefined_variable_names_itself(self):
        problems = check(
            "kind: create\nvariables:\n  team: Backend\n"
            'tasks:\n  - title: "Sprint {{ sprint_numbr }} planning"\n'
        )

        assert codes(problems) == ["undefined-variable"]
        assert problems[0].object == "tasks[0]"
        assert problems[0].field == "title"
        assert "sprint_numbr" in problems[0].reason

    def test_a_close_misspelling_is_offered(self):
        problems = check(
            "kind: create\nvariables:\n  sprint: 42\n"
            'tasks:\n  - title: "{{ sprintt }}"\n'
        )

        assert "Did you mean 'sprint'?" in problems[0].reason

    def test_a_supplied_variable_counts_as_declared(self):
        text = 'kind: create\ntasks:\n  - title: "{{ sprint }}"\n'

        assert codes(check(text)) == ["undefined-variable"]
        assert check(text, variables={"sprint": 42}) == []

    def test_a_declared_variable_with_no_value_is_reported(self):
        problems = check("kind: create\nvariables:\n  sprint:\ntasks: []\n")

        assert codes(problems) == ["missing-variable"]
        assert problems[0].field == "sprint"

    def test_a_default_supplies_it(self):
        assert (
            check("kind: create\nvariables:\n  sprint:\n    default: 42\ntasks: []\n")
            == []
        )

    def test_an_override_supplies_it(self):
        problems = check(
            "kind: create\nvariables:\n  sprint:\ntasks: []\n",
            variables={"sprint": 42},
        )

        assert problems == []

    def test_a_circular_reference_names_the_circle(self):
        problems = check(
            'kind: create\nvariables:\n  a: "{{ b }}"\n  b: "{{ a }}"\ntasks: []\n'
        )

        assert codes(problems) == ["circular-variable"]
        assert "a → b → a" in problems[0].reason

    def test_a_variable_reading_an_undefined_name_names_its_own_key(self):
        problems = check('kind: create\nvariables:\n  a: "{{ nope }}"\ntasks: []\n')

        assert problems[0].object == "variables"
        assert problems[0].field == "a"

    def test_a_rendered_value_is_checked_as_what_it_becomes(self):
        """`visible-to: "{{ who }}"` is checked as the policy it renders to."""
        good = 'kind: search\nvariables:\n  who: public\nsearches:\n  - search: {visible-to: "{{ who }}"}\n'
        bad = 'kind: search\nvariables:\n  who: nonsense\nsearches:\n  - search: {visible-to: "{{ who }}"}\n'

        assert check(good) == []
        assert codes(check(bad)) == ["bad-policy"]

    def test_a_templated_number_is_left_to_the_apply_that_coerces_it(self):
        """Jinja returns text, and YAML cannot hold an unquoted {{ ... }}."""
        text = 'kind: search\nvariables:\n  n: 5\nsearches:\n  - search: {limit: "{{ n }}"}\n'

        assert check(text) == []

    def test_a_number_written_as_text_is_still_reported(self):
        """The leniency is for a templated value, not for every string."""
        assert codes(check('kind: search\nsearches:\n  - search: {limit: "5"}\n')) == [
            "wrong-type"
        ]

    def test_one_mistake_is_not_reported_twice(self):
        """An undefined name is not also an invalid value for its field."""
        text = 'kind: search\nsearches:\n  - search: {limit: "{{ n }}"}\n'

        assert codes(check(text)) == ["undefined-variable"]


class TestStaticSemantics:
    """Step 5: what a JSON Schema structurally cannot express."""

    def test_a_duplicate_local_id_names_both_objects(self):
        problems = check(
            "kind: create\ntasks:\n  - id: one\n    title: A\n  - id: one\n    title: B\n"
        )

        assert codes(problems) == ["duplicate-local-id"]
        assert problems[0].object == "tasks[1]"
        assert problems[0].field == "id"
        assert "tasks[0]" in problems[0].reason

    def test_a_malformed_local_id_is_reported(self):
        problems = check("kind: create\ntasks:\n  - id: 'has space'\n    title: A\n")

        assert codes(problems) == ["bad-local-id"]

    def test_a_dangling_local_reference_names_the_key(self):
        problems = check(
            "kind: create\ntasks:\n  - id: one\n    title: A\n"
            "    projects: ['$missing']\n"
        )

        assert codes(problems) == ["unknown-local-id"]
        assert problems[0].object == "tasks[0]"
        assert problems[0].field == "projects[0]"
        assert "$missing" in problems[0].reason

    def test_a_local_reference_that_resolves_is_clean(self):
        assert (
            check(
                "kind: create\nprojects:\n  - id: sprint\n    name: Sprint\n"
                "tasks:\n  - title: A\n    projects: ['$sprint']\n"
            )
            == []
        )

    def test_a_cycle_between_objects_is_reported(self):
        problems = check(
            "kind: create\ntasks:\n"
            "  - id: one\n    title: A\n    parents: ['$two']\n"
            "  - id: two\n    title: B\n    subtasks: ['$one']\n"
        )

        assert "local-id-cycle" in codes(problems)
        assert "$one" in problems[-1].reason

    def test_a_malformed_parent_monogram_is_reported(self):
        problems = check(
            "kind: create\ntasks:\n  - title: A\n    parents: ['Fix the thing']\n"
        )

        assert codes(problems) == ["bad-monogram"]
        assert problems[0].field == "parents[0]"

    def test_a_well_formed_parent_monogram_is_clean(self):
        assert check("kind: create\ntasks:\n  - title: A\n    parents: [T123]\n") == []

    def test_a_project_name_is_not_held_to_the_monogram_grammar(self):
        """`projects:` names projects, which are not monograms."""
        assert (
            check("kind: create\ntasks:\n  - title: A\n    projects: [Admin]\n") == []
        )

    def test_a_paste_monogram_is_not_a_task_id(self):
        """`include:` takes task ids, and P45 is a well-formed nothing here.

        `phabfive.cli.maniphest.parse_task_id_list` exits with "Invalid task
        ID 'P45'. Expected format: T123", so accepting it offline would just
        move the failure later - and the generated schema would have blessed
        it for every other tool too.
        """
        problems = check("kind: search\nsearch:\n  include: P45\n")

        assert codes(problems) == ["bad-monogram"]
        assert "T123" in problems[0].reason

    def test_a_task_monogram_is_still_fine(self):
        assert check("kind: search\nsearch:\n  include: T45,T46\n") == []

    def test_the_published_pattern_refuses_the_paste_monogram_too(self):
        """Offline and the schema must not disagree about one value."""
        from phabfive.spec.registry import field_by_name
        from phabfive.spec.schema import monogram_list_pattern

        field = field_by_name("include", "task", "search")

        assert field is not None
        assert not re.match(monogram_list_pattern(field.monograms), "P45")
        assert re.match(monogram_list_pattern(field.monograms), "T45")

    def test_a_task_that_is_not_a_mapping_is_reported_once(self):
        """The create sections are not shape-checked by the loader; this is it."""
        problems = check("kind: create\ntasks:\n  - nope\n")

        assert codes(problems) == ["wrong-type"]
        assert problems[0].object == "tasks[0]"
        assert problems[0].field is None

    def test_a_nested_task_is_walked_too(self):
        problems = check(
            "kind: create\ntasks:\n  - title: A\n    tasks:\n"
            "      - title: B\n        parents: ['nope']\n"
        )

        assert codes(problems) == ["bad-monogram"]
        assert problems[0].object == "tasks[0].tasks[0]"


class TestEverythingIsReportedAtOnce:
    """The acceptance criterion a first-error-wins implementation fails."""

    SIX_MISTAKES = """
kind: search
metadata:
  version: 2.1
searches:
  - type: task
    search:
      colum: "in:Backlog"
      limit: "lots"
      order: sideways
      visible-to: nonsense
      status: "in:"
"""

    def test_six_mistakes_are_six_problems(self):
        problems = errors(check(self.SIX_MISTAKES))

        assert len(problems) == 6

    def test_each_one_names_its_own_key(self):
        problems = check(self.SIX_MISTAKES)
        fields = {one.field for one in problems}

        assert fields == {
            "version",
            "search.colum",
            "search.limit",
            "search.order",
            "search.visible-to",
            "search.status",
        }

    def test_the_codes_are_the_stable_slugs(self):
        assert set(codes(check(self.SIX_MISTAKES))) == {
            "wrong-type",
            "unknown-key",
            "unknown-value",
            "bad-policy",
            "bad-pattern",
        }

    def test_the_envelope_is_reported_before_the_body(self):
        problems = check(self.SIX_MISTAKES)

        assert problems[0].object == "metadata"

    def test_the_body_is_reported_in_registry_declaration_order(self):
        """Deterministic order is testable order."""
        problems = [one for one in check(self.SIX_MISTAKES) if one.field != "version"]
        declared = [field.name for field in fields_for("task", "search")]
        seen = [one.field.removeprefix("search.") for one in problems]

        # The unknown key comes first, then the declared ones in their order
        assert seen[0] == "colum"
        positions = [declared.index(name) for name in seen[1:]]
        assert positions == sorted(positions)


class TestTheReportIsData:
    """It is returned, and it is what a frontend and `--format=json` render."""

    def test_it_returns_rather_than_raises(self):
        assert isinstance(check("kind: create\ntasks:\n  - badger: 1\n"), list)

    def test_a_clean_spec_is_an_empty_list(self):
        assert check("kind: create\ntasks:\n  - title: A\n") == []

    def test_every_problem_is_an_offline_problem(self):
        problems = check("kind: search\nsearches:\n  - search: {limit: lots}\n")

        assert {one.layer for one in problems} == {Layer.OFFLINE}

    def test_a_record_survives_json(self):
        problems = check("kind: search\nsearches:\n  - search: {limit: lots}\n")
        record = problems[0].as_record()

        assert json.loads(json.dumps(record, default=str)) == record
        assert set(record) == {
            "object",
            "field",
            "value",
            "reason",
            "code",
            "layer",
            "severity",
        }

    def test_problems_compare_by_value(self):
        """A test asserts a whole expected list at once, or it asserts nothing."""
        text = "kind: search\nsearches:\n  - search: {limit: lots}\n"

        assert check(text) == [
            Problem(
                object="searches[0]",
                field="search.limit",
                value="lots",
                reason="limit takes a whole number, not a str",
                code="wrong-type",
            )
        ]

    def test_the_spec_is_not_mutated_by_validating_it(self):
        spec = parse_spec(
            'kind: create\nvariables:\n  a: 1\ntasks:\n  - title: "{{ a }}"\n',
            format="yaml",
        )
        # deepcopy, not dataclasses.replace: replace is shallow, so `before`
        # would share the very same `body` dict and the comparison would be a
        # dict against itself, passing however the body was mutated.
        before = copy.deepcopy(spec)

        validate_offline(spec)

        assert spec == before
        assert spec.body == before.body
        assert spec.variables == before.variables
        assert spec.body["tasks"][0]["title"] == "{{ a }}"

    def test_the_method_on_spec_is_the_function(self):
        spec = parse_spec(
            "kind: search\nsearches:\n  - search: {limit: lots}\n", format="yaml"
        )

        assert spec.validate_offline() == validate_offline(spec)


class TestWhatIsDeliberatelyNotDecidedOffline:
    """Instance-defined vocabulary belongs to layer 2, and must not leak here."""

    @pytest.mark.parametrize(
        "text",
        [
            "kind: create\ntasks:\n  - title: A\n    priority: sizzling\n",
            "kind: create\ntasks:\n  - title: A\n    space: S99\n",
            "kind: create\ntasks:\n  - title: A\n    assignment: nobody-at-all\n",
            "kind: search\nsearches:\n  - search: {assigned: nobody-at-all}\n",
            "kind: search\nsearches:\n  - search: {tag: no-such-project}\n",
            "kind: search\nsearches:\n  - search: {column: 'in:No Such Column'}\n",
            "kind: search\nsearches:\n  - search: {status: 'in:no-such-status'}\n",
        ],
    )
    def test_a_value_only_the_server_knows_is_not_judged(self, text):
        assert errors(check(text)) == []

    def test_no_instance_enum_is_baked_into_the_schema(self):
        """A generated enum here would bless a value the server never named."""
        text = json.dumps(build_schema("search"))

        for invented in ("unbreak", "wontfix", "resolved", "fa-briefcase"):
            assert invented not in text

    def test_the_order_grammar_is_in_the_schema_because_it_is_static(self):
        from phabfive.constants import MANIPHEST_ORDER_CHOICES

        field = next(one for one in fields_for("task", "search") if one.name == "order")

        assert property_schema(field)["enum"] == list(MANIPHEST_ORDER_CHOICES)


class TestTheSearchItemItself:
    """`type:`, the banner, and the filters, which are three different things."""

    def test_the_item_keys_are_the_declared_four(self):
        assert SEARCH_ITEM_KEYS == ("type", "title", "description", "search")

    def test_an_unknown_item_key_is_reported(self):
        problems = check("kind: search\nsearches:\n  - banner: Hi\n    search: {}\n")

        assert codes(problems) == ["unknown-key"]
        assert problems[0].field == "banner"

    def test_an_unknown_search_type_is_reported(self):
        problems = check(
            "kind: search\nsearches:\n  - type: sandwich\n    search: {}\n"
        )

        assert codes(problems) == ["unknown-value"]
        assert problems[0].field == "type"

    def test_a_banner_that_is_not_text_is_reported(self):
        problems = check("kind: search\nsearches:\n  - title: [a, b]\n    search: {}\n")

        assert codes(problems) == ["wrong-type"]

    def test_an_object_type_with_no_declared_field_accepts_anything(self):
        """Phase 1 declares task search fields only; a project search is open."""
        assert (
            check(
                "kind: search\nsearches:\n  - type: project\n    search: {status: active}\n"
            )
            == []
        )

    def test_a_search_that_is_not_a_mapping_is_reported(self):
        problems = check("kind: search\nsearches:\n  - search: nope\n")

        assert codes(problems) == ["wrong-type"]
        assert problems[0].field == "search"

    def test_a_searches_item_that_is_not_a_mapping_raises_in_the_loader(self):
        """Structure raises, content is reported - there is no item to report on."""
        from phabfive.exceptions import PhabfiveDataException

        with pytest.raises(PhabfiveDataException, match="not a mapping"):
            check("kind: search\nsearches:\n  - nope\n")


class TestUnknownTopLevelKeys:
    """A key nothing reads is a mistake, not a comment."""

    def test_a_create_spec_holds_three_sections(self):
        problems = check("kind: create\nbadgers: []\n")

        assert codes(problems) == ["unknown-key"]
        assert "tasks, projects, pastes" in problems[0].reason

    def test_a_near_miss_is_offered(self):
        problems = check("kind: create\ntask: []\n")

        assert "Did you mean 'tasks'?" in problems[0].reason
