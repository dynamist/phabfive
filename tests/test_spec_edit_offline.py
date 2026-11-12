# -*- coding: utf-8 -*-

"""The offline rules of an edit document (`kind: edit`).

An edit item names an object that exists and states what it should be. The
rules pinned here are the ones a file can be checked against with no
instance: which keys an item has, that it names its object, the two shapes a
list may take, and that a value states a result rather than a move.

`jsonschema` appears as an oracle for the same documents, the way it does in
`tests/test_spec_validate_offline.py`: the published schema and the
hand-rolled walk have to agree about what is a well-formed edit document.
"""

import datetime
import subprocess
import sys

import jsonschema
import pytest

from phabfive.exceptions import PhabfiveDataException, PhabfiveInputException
from phabfive.spec import Spec, build_schema, parse_spec
from phabfive.spec.edit import (
    MixedListError,
    format_stamp,
    parse_list_edit,
    parse_stamp,
)
from phabfive.spec.envelope import Kind
from phabfive.spec.registry import DECLARED_COMPLETE, fields_for, spec_keys


def edit(*items, **envelope):
    return Spec.from_data({"kind": "edit", **envelope, "tasks": list(items)})


def codes(spec):
    return [problem.code for problem in spec.validate_offline()]


def fields(spec):
    return [(problem.field, problem.code) for problem in spec.validate_offline()]


class TestTheKind:
    def test_edit_is_a_kind(self):
        assert edit({"task": "T1"}).kind is Kind.EDIT

    def test_it_is_never_inferred(self):
        """`tasks:` alone is a create, whatever the items look like."""
        spec = Spec.from_data({"tasks": [{"task": "T1", "status": "open"}]})

        assert spec.kind is Kind.CREATE

    def test_an_edit_that_lost_its_kind_is_refused_and_says_what_it_is(self):
        """The safety net under "never inferred".

        Read as a create, the document must not validate: a create that
        went ahead would make a new task out of an item meant to change an
        old one. `task:` is the key that stops it, and the sentence names
        the kind rather than suggesting `tasks`.
        """
        spec = Spec.from_data(
            {
                "tasks": [
                    {"task": "T1", "modified": "2026-01-31T09:30:00Z", "title": "x"}
                ]
            }
        )
        problems = spec.validate_offline()

        assert [problem.code for problem in problems] == ["unknown-key", "unknown-key"]
        assert all("kind: edit" in problem.reason for problem in problems)

    def test_it_holds_tasks_and_nothing_else_yet(self):
        spec = Spec.from_data(
            {"kind": "edit", "tasks": [{"task": "T1"}], "pastes": [{"paste": "P1"}]}
        )

        assert fields(spec) == [("pastes", "unknown-key")]

    def test_its_key_set_is_closed(self):
        assert ("task", "edit") in DECLARED_COMPLETE
        assert codes(edit({"task": "T1", "colour": "red"})) == ["unknown-key"]


class TestTheKeysAreTheCreateKeys:
    """What makes an exported document readable as either kind."""

    def test_every_value_key_is_a_create_key_of_the_same_kind(self):
        create = {field.name: field for field in fields_for("task", "create")}
        own = {"task", "modified", "parents", "subtasks"}

        for field in fields_for("task", "edit"):
            if field.name in own:
                continue

            assert field.name in create, field.name
            assert field.kind is create[field.name].kind
            assert field.multiple is create[field.name].multiple

    def test_the_keys(self):
        assert spec_keys("task", "edit") == {
            "task",
            "modified",
            "title",
            "description",
            "priority",
            "status",
            "assignment",
            "subscribers",
            "projects",
            "column",
            "space",
            "visible-to",
            "editable-by",
            "commits",
            "parents",
            "subtasks",
        }


class TestAnItemNamesItsObject:
    def test_a_whole_edit_is_two_keys(self):
        assert codes(edit({"task": "T1", "status": "resolved"})) == []

    def test_an_item_without_one_is_refused(self):
        assert fields(edit({"status": "open"})) == [("task", "missing-required")]

    @pytest.mark.parametrize("value", ["P1", "t1", "123", "$epic", "T1,T2", ["T1"]])
    def test_it_is_one_task_monogram(self, value):
        assert "bad-monogram" in codes(edit({"task": value}))

    def test_one_item_per_task(self):
        spec = edit(
            {"task": "T1", "status": "open"}, {"task": "T1", "priority": "high"}
        )

        assert fields(spec) == [("task", "duplicate-object")]


class TestACreateItemsStructureIsNotHere:
    @pytest.mark.parametrize("key", ["id", "tasks", "parent"])
    def test_it_is_refused_by_name_with_what_to_write(self, key):
        problems = edit({"task": "T1", key: "x"}).validate_offline()

        assert [(problem.field, problem.code) for problem in problems] == [
            (key, "unknown-key")
        ]
        assert "Did you mean" not in problems[0].reason

    def test_a_local_id_is_not_a_task(self):
        assert fields(edit({"task": "T1", "parents": ["$epic"]})) == [
            ("parents[0]", "bad-monogram")
        ]


class TestAListIsTheFullSetOrADelta:
    @pytest.mark.parametrize(
        "value",
        [
            ["alice", "bob"],
            [],
            ["+carol", "-bob"],
            ["+carol"],
            "alice,bob",
            "+carol,-bob",
        ],
    )
    def test_both_shapes_are_accepted(self, value):
        assert codes(edit({"task": "T1", "subscribers": value})) == []

    def test_one_list_is_not_both(self):
        assert fields(edit({"task": "T1", "projects": ["a", "+b"]})) == [
            ("projects", "mixed-list")
        ]

    def test_an_unquoted_plus_one_says_to_quote_it(self):
        problems = edit({"task": "T1", "subscribers": [+1]}).validate_offline()

        assert [problem.code for problem in problems] == ["wrong-type"]
        assert '"+1"' in problems[0].reason

    def test_the_operator_comes_off_before_the_value_is_checked(self):
        assert codes(edit({"task": "T1", "parents": ["+T2", "-T3"]})) == []
        assert fields(edit({"task": "T1", "parents": ["+P2"]})) == [
            ("parents[0]", "bad-monogram")
        ]

    def test_a_key_with_nothing_after_it_is_not_there(self):
        assert codes(edit({"task": "T1", "subscribers": None})) == []

    def test_parsing(self):
        assert parse_list_edit(["a", "b"]).values == ("a", "b")
        assert parse_list_edit([]).delta is False

        delta = parse_list_edit(["+a", "- b", "+c"])

        assert (delta.delta, delta.added, delta.removed) == (True, ("a", "c"), ("b",))
        assert delta.named == ("a", "c", "b")

        with pytest.raises(MixedListError):
            parse_list_edit(["a", "-b"])

        with pytest.raises(PhabfiveInputException, match="names nothing"):
            parse_list_edit(["+"])


class TestAValueIsAStateNotAMove:
    @pytest.mark.parametrize(
        "key,value",
        [
            ("priority", "raise"),
            ("priority", "higher"),
            ("priority", "Lower"),
            ("column", "forward"),
            ("column", "backward"),
        ],
    )
    def test_a_relative_value_is_refused(self, key, value):
        assert fields(edit({"task": "T1", key: value})) == [(key, "relative-value")]

    def test_a_create_document_is_not_asked(self):
        spec = Spec.from_data(
            {"kind": "create", "tasks": [{"title": "t", "priority": "raise"}]}
        )

        assert "relative-value" not in codes(spec)


class TestTheStamp:
    @pytest.mark.parametrize(
        "value",
        [
            "2026-01-31T09:30:00Z",
            "2026-01-31T09:30:00+00:00",
            "2026-01-31T10:30:00+01:00",
            "2026-01-31 09:30:00",
            datetime.datetime(2026, 1, 31, 9, 30),
            datetime.datetime(2026, 1, 31, 9, 30, tzinfo=datetime.timezone.utc),
        ],
    )
    def test_every_spelling_of_one_instant_is_that_instant(self, value):
        assert parse_stamp(value) == 1769851800
        assert codes(edit({"task": "T1", "modified": value})) == []

    @pytest.mark.parametrize("value", ["yesterday", "2026-01-31", 1769851800, True])
    def test_anything_else_is_refused(self, value):
        assert fields(edit({"task": "T1", "modified": value})) == [
            ("modified", "bad-timestamp")
        ]

    def test_it_is_written_the_way_it_is_read(self):
        assert format_stamp(1769851800) == "2026-01-31T09:30:00Z"
        assert parse_stamp(format_stamp(1769851800)) == 1769851800

    def test_the_serializations_agree(self):
        """Quoted, native in YAML, native in TOML: one instant."""
        documents = [
            parse_spec(
                'kind: edit\ntasks:\n  - task: T1\n    modified: "2026-01-31T09:30:00Z"\n',
                format="yaml",
            ),
            parse_spec(
                "kind: edit\ntasks:\n  - task: T1\n    modified: 2026-01-31T09:30:00Z\n",
                format="yaml",
            ),
            parse_spec(
                'kind = "edit"\n[[tasks]]\ntask = "T1"\nmodified = 2026-01-31T09:30:00Z\n',
                format="toml",
            ),
            parse_spec(
                '{"kind": "edit", "tasks": [{"task": "T1", '
                '"modified": "2026-01-31T09:30:00Z"}]}',
                format="json",
            ),
        ]

        for spec in documents:
            assert spec.validate_offline() == []
            assert parse_stamp(spec.items("task")[0]["modified"]) == 1769851800


class TestVariablesAndLiteralText:
    def test_an_edit_document_renders_like_any_other(self):
        spec = Spec.from_data(
            {
                "kind": "edit",
                "variables": {"who": "alice"},
                "tasks": [{"task": "T1", "assignment": "{{ who }}"}],
            }
        )

        assert spec.validate_offline() == []
        assert spec.render().items("task")[0]["assignment"] == "alice"

    def test_and_can_switch_it_off(self):
        spec = edit(
            {"task": "T1", "description": "{{ .Values.tag }}"}, interpolate=False
        )

        assert spec.validate_offline() == []


class TestTheRoundTrip:
    def test_to_data_parses_back_to_itself(self):
        spec = edit(
            {"task": "T1", "projects": ["+a"], "modified": "2026-01-31T09:30:00Z"},
            spec="phorge/v1alpha1",
            interpolate=False,
        )

        assert spec.to_data()["kind"] == "edit"
        assert Spec.from_data(spec.to_data()) == spec

    def test_documents_fold(self):
        spec = Spec.from_data(
            [
                {"kind": "edit", "tasks": [{"task": "T1"}]},
                {"tasks": [{"task": "T2"}]},
            ]
        )

        assert [item["task"] for item in spec.items("task")] == ["T1", "T2"]

    def test_a_section_that_is_not_a_list_is_refused(self):
        with pytest.raises(PhabfiveDataException):
            Spec.from_data({"kind": "edit", "tasks": {"task": "T1"}})


GOOD = [
    {"kind": "edit", "tasks": [{"task": "T1", "status": "resolved"}]},
    {
        "spec": "phorge/v1alpha1",
        "kind": "edit",
        "interpolate": False,
        "tasks": [
            {
                "task": "T1",
                "modified": "2026-01-31T09:30:00Z",
                "title": "A title",
                "description": "Text",
                "priority": "high",
                "status": "open",
                "assignment": "@none",
                "subscribers": ["alice", "bob"],
                "projects": [],
                "column": "Doing",
                "space": "S1",
                "visible-to": "users",
                "editable-by": "#infra",
                "commits": ["+rXabc1234"],
                "parents": ["T2"],
                "subtasks": ["-T3"],
            }
        ],
    },
]

BAD = [
    {"kind": "edit", "tasks": [{"status": "resolved"}]},
    {"kind": "edit", "tasks": [{"task": "T1", "colour": "red"}]},
    {"kind": "edit", "tasks": [{"task": "T1", "id": "x"}]},
    {"kind": "edit", "tasks": [{"task": "T1", "projects": ["a", "+b"]}]},
    {"kind": "edit", "tasks": [{"task": "P1"}]},
    {"kind": "edit", "tasks": [{"task": "T1", "modified": "yesterday"}]},
    {"kind": "edit", "tasks": [{"task": "T1"}], "pastes": []},
    {"kind": "edit", "tasks": [{"task": "T1", "subscribers": [1]}]},
]


class TestTheSchemaAgrees:
    @staticmethod
    def _oracle(data):
        validator = jsonschema.Draft202012Validator(build_schema("edit"))

        return not list(validator.iter_errors(data))

    def test_the_schema_is_a_schema(self):
        jsonschema.Draft202012Validator.check_schema(build_schema("edit"))
        jsonschema.Draft202012Validator.check_schema(
            build_schema("edit", object_type="task")
        )

    @pytest.mark.parametrize("data", GOOD)
    def test_both_say_a_good_document_is_good(self, data):
        assert self._oracle(data) is True
        assert Spec.from_data(data).validate_offline() == []

    @pytest.mark.parametrize("data", BAD)
    def test_both_say_a_bad_document_is_bad(self, data):
        assert self._oracle(data) is False
        assert Spec.from_data(data).validate_offline() != []

    def test_it_requires_the_kind_it_cannot_infer(self):
        assert self._oracle({"tasks": [{"task": "T1"}]}) is False

    def test_an_object_type_it_does_not_edit_is_refused(self):
        with pytest.raises(PhabfiveInputException, match="no paste items"):
            build_schema("edit", object_type="paste")


def test_checking_an_edit_document_needs_no_instance():
    """Out of process, like `tests/test_spec_isolation.py`: nothing online loads."""
    code = (
        "import sys\n"
        "from phabfive.spec import Spec\n"
        "spec = Spec.from_data({'kind': 'edit', 'tasks': [{'task': 'T1'}]})\n"
        "assert spec.validate_offline() == []\n"
        "print([m for m in sys.modules if m in ('phabricator', 'requests', "
        "'phabfive.cli', 'phabfive.core', 'phabfive.conduit', "
        "'phabfive.edit.spec', 'phabfive.maniphest')])\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )

    assert result.stdout.strip() == "[]", result.stdout
