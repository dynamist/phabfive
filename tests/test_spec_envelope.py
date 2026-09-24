# -*- coding: utf-8 -*-

"""A spec may say what it is, and a spec that says nothing still loads.

The envelope - `spec:`, `kind:`, `metadata:` - is optional, and both halves
of that are pinned here. `TestAnEnvelopeIsOptional` writes its files inline:
inference is ergonomics that stays, so `tasks:` alone is enough to say what
a document is, and a user writing one by hand never has to type a version
string to get it read.

The shipped corpus is the other half, and since #489 it is the *enveloped*
half: all eleven files declare `spec:` and `kind:`, because an example
teaches the shape it shows and `phorge/v1alpha1` is explicitly free to
churn. What the corpus is asked here is the thing a fixture cannot fake -
the real files, through `_load_search_config` and through `Spec`, answering
the same thing.

The rest is the envelope itself: a kind inferred from the body, an ambiguous
file refused instead of guessed, an unknown `spec:` version refused by name,
and metadata a library caller can reach rather than only a command can print.
"""

import copy
import dataclasses
import os
from pathlib import Path

import pytest

from phabfive.exceptions import (
    PhabfiveDataException,
    PhabfiveInputException,
)
from phabfive.maniphest import Maniphest
from phabfive.spec import Envelope, Kind, Metadata, Spec, load_spec, parse_spec
from phabfive.spec.envelope import (
    DEFAULT_SEARCH_TYPE,
    SPEC_VERSION,
    SUPPORTED_SPEC_VERSIONS,
    infer_kind,
    normalize_documents,
    split_envelope,
)
from phabfive.spec.loader import load_documents

SPEC_ROOT = Path(__file__).resolve().parent.parent / "specs"

SEARCH_SPECS = sorted((SPEC_ROOT / "search").glob("*.yaml"))
CREATE_SPECS = sorted(
    path
    for pattern in ("*.yaml", "*.yml")
    for path in (SPEC_ROOT / "create").glob(pattern)
)


def _searches_only_tasks(path):
    """Whether every search in one file runs against Maniphest.

    `Maniphest._load_search_config` checks each `search:` against the *task*
    key set, so it refuses a project or paste search by design - `milestones`
    is not a task filter. The backward-compatibility promise that reader
    pins is therefore a promise about task searches: the other object types
    arrived with the spec format and never had a `--with` reader to be
    compatible with. The corpus walk in tests/test_spec_corpus.py is what
    covers those.
    """
    return all(
        (item.get("type") or "task") == "task"
        for item in load_spec(path, kind="search").items("search")
    )


TASK_SEARCH_SPECS = [path for path in SEARCH_SPECS if _searches_only_tasks(path)]


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


class TestTheShippedCorpus:
    """The shipped specs read the same through every reader that sees them."""

    def test_the_corpus_is_there(self):
        """Guard the guard: an empty glob would make everything below vacuous."""
        assert len(SEARCH_SPECS) >= 8
        assert len(CREATE_SPECS) >= 3
        assert len(TASK_SEARCH_SPECS) >= 8

    @pytest.mark.parametrize("path", TASK_SEARCH_SPECS, ids=lambda path: path.name)
    def test_a_search_spec_reads_as_load_search_config_reads_it(self, path):
        """The whole backward-compatibility promise, at the Spec level.

        `_load_search_config` is what `maniphest search --with` calls today,
        and three of these files are multi-document, which is the case a
        single-document reader would silently truncate.

        The spec is rendered first, because the command renders before it
        reads the filters: a `{{ stale_days }}` reaching the time parser as
        itself is the bug that put the render there, so comparing the
        unrendered spec would pin the shape nothing uses.
        """
        legacy = Maniphest.__new__(Maniphest)._load_search_config(str(path))

        spec = load_spec(path, kind="search")

        if spec.variables:
            spec = spec.render()

        assert [
            {
                # `type` is part of the projection: without it the command
                # cannot tell a paste search from a task one, and runs both
                # as tasks
                "type": item.get("type"),
                "search": item["search"],
                "title": item.get("title"),
                "description": item.get("description"),
            }
            for item in spec.items("search")
        ] == legacy

    @pytest.mark.parametrize("path", SEARCH_SPECS, ids=lambda path: path.name)
    def test_a_search_spec_declares_its_envelope(self, path):
        """The shipped files are what a user copies, so they show the format."""
        spec = load_spec(path)

        assert spec.envelope.spec == SPEC_VERSION
        assert spec.envelope.kind is Kind.SEARCH
        assert spec.envelope.kind_declared is True
        assert spec.envelope.metadata.description

    @pytest.mark.parametrize("path", CREATE_SPECS, ids=lambda path: path.name)
    def test_a_create_spec_keeps_its_variables_and_its_items(self, path):
        """The shape `create_tasks_from_config` reads, split the way it splits it.

        It pops `variables` off the root and then wants the items; a Spec
        hands both over without the caller reaching into a raw dict. Every
        object type a create spec may hold is asserted, not only `tasks`: a
        spec that creates one project and no task is a create spec, and
        reading only `tasks` from it is how a whole root key goes unread.
        """
        raw = load_documents(path)[0]

        spec = load_spec(path)

        assert spec.kind is Kind.CREATE
        assert spec.variables == (raw.get("variables") or {})
        assert "variables" not in spec.body

        for key, object_type in (
            ("tasks", "task"),
            ("projects", "project"),
            ("pastes", "paste"),
        ):
            assert spec.items(object_type) == (raw.get(key) or []), key

    def test_yaml_anchors_survive_into_the_body(self):
        """&WORKGROUP/*WORKGROUP is a YAML feature and must still expand."""
        spec = load_spec(SPEC_ROOT / "create" / "feature-epic.yaml")

        assert spec.variables["workgroup"] == [
            "gabriel.blomqvist",
            "sebastian.soderberg",
        ]


class TestAnEnvelopeIsOptional:
    """A document that says nothing about itself is still read.

    Written inline rather than against the corpus, which declares its
    envelope since #489. Inference is kept as ergonomics - a file that says
    `tasks:` has already said what it is - and not as a promise that files
    written before the format keep working, which `phorge/v1alpha1` does not
    make.
    """

    def test_a_search_document_declares_no_envelope(self, tmp_path):
        path = write(
            tmp_path,
            "search.yaml",
            "search:\n  priority: high\n",
        )

        spec = load_spec(path, kind="search")

        assert spec.envelope == Envelope(
            spec=None, kind=Kind.SEARCH, kind_declared=False, metadata=Metadata()
        )

    def test_a_create_document_needs_no_kind_argument(self, tmp_path):
        """`tasks:` is enough to say what the file is."""
        path = write(
            tmp_path,
            "create.yaml",
            "variables:\n  who: alice\ntasks:\n  - title: Ship it\n",
        )

        spec = load_spec(path)

        assert spec.kind is Kind.CREATE
        assert spec.envelope.kind_declared is False
        assert spec.variables == {"who": "alice"}
        assert spec.items("task") == [{"title": "Ship it"}]


class TestKindInference:
    """The verb, inferred from the body, or refused rather than guessed."""

    @pytest.mark.parametrize(
        "body",
        [
            "search:\n  priority: high\n",
            "searches:\n  - search:\n      priority: high\n",
        ],
        ids=["legacy-search", "searches"],
    )
    def test_a_search_shape_is_a_search(self, body):
        assert parse_spec(body, format="yaml").kind is Kind.SEARCH

    @pytest.mark.parametrize(
        "body",
        [
            "tasks:\n  - title: One\n",
            "projects:\n  - name: One\n",
            "pastes:\n  - title: One\n",
        ],
        ids=["tasks", "projects", "pastes"],
    )
    def test_a_create_shape_is_a_create(self, body):
        assert parse_spec(body, format="yaml").kind is Kind.CREATE

    def test_one_document_may_create_more_than_one_object_type(self):
        """kind carries the verb, so tasks and projects live in one spec."""
        spec = parse_spec(
            "projects:\n  - name: Sprint\ntasks:\n  - title: One\n", format="yaml"
        )

        assert spec.kind is Kind.CREATE
        assert len(spec.items("project")) == 1
        assert len(spec.items("task")) == 1

    def test_both_families_are_refused_not_guessed(self):
        with pytest.raises(PhabfiveDataException) as error:
            parse_spec("tasks:\n  - title: One\nsearch:\n  priority: high\n")

        message = str(error.value)
        assert "tasks" in message
        assert "search" in message
        assert "kind" in message

    def test_neither_family_names_the_keys_it_looked_for(self):
        with pytest.raises(PhabfiveDataException) as error:
            parse_spec("title: Just a banner\n", format="yaml")

        message = str(error.value)
        for key in ("searches", "tasks", "projects", "pastes"):
            assert key in message

    def test_a_declared_kind_beats_inference(self):
        """A create spec that happens to hold a `search:` key is still create."""
        spec = parse_spec(
            "kind: create\ntasks:\n  - title: One\nsearch: whatever\n", format="yaml"
        )

        assert spec.kind is Kind.CREATE
        assert spec.envelope.kind_declared is True

    def test_a_supplied_kind_needs_nothing_in_the_file(self):
        """Rule one of inference: a caller that knows is never overruled.

        This is what makes the legacy call sites safe - a search template may
        carry only `title:` and `description:`, which nothing could infer.
        """
        spec = parse_spec("title: Banner\ndescription: Words\n", kind="search")

        assert spec.kind is Kind.SEARCH
        assert spec.envelope.kind_declared is False
        assert spec.items("search") == [
            {"type": "task", "title": "Banner", "description": "Words", "search": {}}
        ]

    def test_a_supplied_kind_beats_a_declared_one(self):
        """A caller that knows what it is holding outranks the file."""
        spec = parse_spec(
            "kind: create\ntasks:\n  - title: One\n", format="yaml", kind="search"
        )

        assert spec.kind is Kind.SEARCH
        assert spec.envelope.kind_declared is True

    @pytest.mark.parametrize("kind", ["create", "search", Kind.CREATE, Kind.SEARCH])
    def test_both_spellings_of_a_kind_argument_are_accepted(self, kind):
        assert parse_spec("tasks:\n  - title: One\n", kind=kind).kind is Kind(
            getattr(kind, "value", kind)
        )

    def test_a_kind_that_is_not_a_kind_names_both(self):
        with pytest.raises(PhabfiveInputException) as error:
            parse_spec("kind: destroy\ntasks:\n  - title: One\n", format="yaml")

        message = str(error.value)
        assert "create" in message
        assert "search" in message

    def test_infer_kind_reads_several_documents_together(self):
        """A file whose first document is only variables is still inferable."""
        assert (
            infer_kind([{"variables": {"a": 1}}, {"tasks": [{"title": "One"}]}])
            is Kind.CREATE
        )

    def test_infer_kind_ignores_a_declared_kind(self):
        """Inference looks at the body; `kind:` is not inference."""
        with pytest.raises(PhabfiveDataException):
            infer_kind({"kind": "create"})


class TestSpecVersion:
    """v1alpha1 is deliberately unstable, so an unknown version is refused."""

    def test_the_supported_version_is_named_after_the_domain(self):
        assert SPEC_VERSION == "phorge/v1alpha1"
        assert SUPPORTED_SPEC_VERSIONS == frozenset({SPEC_VERSION})

    def test_the_supported_version_is_kept_verbatim(self):
        spec = parse_spec(
            f"spec: {SPEC_VERSION}\ntasks:\n  - title: One\n", format="yaml"
        )

        assert spec.envelope.spec == SPEC_VERSION

    @pytest.mark.parametrize(
        "version", ["phorge/v1", "phorge/v1alpha0", "phabfive/v1alpha1", "v1alpha1"]
    )
    def test_an_unknown_version_is_refused_by_name(self, version):
        """Named, and never half-parsed: no deprecation machinery, on purpose."""
        with pytest.raises(PhabfiveDataException) as error:
            parse_spec(f"spec: {version}\ntasks:\n  - title: One\n", format="yaml")

        message = str(error.value)
        assert version in message
        assert SPEC_VERSION in message

    def test_a_version_that_is_not_even_a_string_is_refused(self):
        with pytest.raises(PhabfiveDataException):
            parse_spec("spec: 1.0\ntasks:\n  - title: One\n", format="yaml")

    def test_a_spec_with_no_version_key_does_not_grow_one(self):
        spec = parse_spec("tasks:\n  - title: One\n", format="yaml")

        assert spec.envelope.spec is None
        assert "spec" not in spec.to_data()


METADATA_SPEC = """\
spec: phorge/v1alpha1
kind: create
metadata:
  name: sprint-setup
  description: Standard task set for a new sprint
  version: "2.1"
  author: alice
tasks:
  - title: Write the runbook
"""


class TestMetadataDescribesTheFile:
    """metadata: is reachable by a library caller, not only printable."""

    def test_every_field_is_on_the_object(self):
        spec = parse_spec(METADATA_SPEC, format="yaml")

        assert spec.metadata == Metadata(
            name="sprint-setup",
            description="Standard task set for a new sprint",
            version="2.1",
            author="alice",
            extra={},
        )
        assert spec.metadata is spec.envelope.metadata

    def test_metadata_is_not_left_in_the_body(self):
        spec = parse_spec(METADATA_SPEC, format="yaml")

        assert set(spec.body) == {"tasks"}

    @pytest.mark.parametrize(
        "text", ["tasks:\n  - title: One\n", "metadata:\ntasks: []\n"]
    )
    def test_a_spec_with_no_metadata_has_empty_metadata(self, text):
        spec = parse_spec(text, format="yaml")

        assert spec.metadata == Metadata()
        assert spec.metadata.name is None

    def test_a_version_written_as_a_number_is_kept_as_it_was_written(self):
        """`version: 2.1` is a YAML float and a real trap.

        Keeping the float is what lets the offline validation pass report it;
        coercing it to "2.1" here would hide the mistake from the person who
        has to fix the file.
        """
        spec = parse_spec("metadata:\n  version: 2.1\ntasks: []\n", format="yaml")

        assert spec.metadata.version == 2.1

    def test_an_unknown_metadata_key_is_kept_rather_than_dropped(self):
        """A warning for the offline pass, and it survives a round trip."""
        spec = parse_spec(
            "metadata:\n  name: one\n  team: platform\ntasks: []\n", format="yaml"
        )

        assert spec.metadata.extra == {"team": "platform"}
        assert spec.to_data()["metadata"] == {"name": "one", "team": "platform"}

    def test_metadata_that_is_not_a_mapping_is_refused(self):
        with pytest.raises(PhabfiveDataException) as error:
            parse_spec("metadata: alice\ntasks: []\n", format="yaml")

        assert "metadata" in str(error.value)

    def test_the_file_description_and_a_search_banner_are_different_things(self):
        """Both survive, and neither is moved into the other."""
        spec = parse_spec(
            "metadata:\n"
            "  name: audit\n"
            "  description: What this file is for\n"
            "title: Blocked work\n"
            "description: What this one search shows\n"
            "search:\n"
            "  column: 'in:Blocked'\n",
            format="yaml",
            kind="search",
        )

        assert spec.metadata.description == "What this file is for"
        assert spec.items("search") == [
            {
                "type": "task",
                "title": "Blocked work",
                "description": "What this one search shows",
                "search": {"column": "in:Blocked"},
            }
        ]


class TestSearchItems:
    """A search names its target per item, and defaults it to a task."""

    def test_type_defaults_to_task(self):
        spec = parse_spec("searches:\n  - search: {status: open}\n", format="yaml")

        assert spec.items("search")[0]["type"] == DEFAULT_SEARCH_TYPE == "task"

    def test_a_declared_type_is_kept(self):
        spec = parse_spec(
            "kind: search\nsearches:\n  - type: project\n    search: {status: active}\n",
            format="yaml",
        )

        assert [item["type"] for item in spec.items("search")] == ["project"]

    def test_a_banner_with_no_search_key_gets_an_empty_one(self):
        """`data.get("search", {})` is what the legacy reader has always done."""
        spec = parse_spec("title: Banner only\n", kind="search")

        assert spec.items("search")[0]["search"] == {}

    def test_a_document_carrying_both_shapes_is_refused(self):
        with pytest.raises(PhabfiveDataException) as error:
            parse_spec(
                "kind: search\ntitle: Banner\nsearches:\n  - search: {}\n",
                format="yaml",
            )

        message = str(error.value)
        assert "searches" in message
        assert "title" in message

    @pytest.mark.parametrize("text", ["kind: search\nsearches:\n", "searches: []\n"])
    def test_an_empty_searches_key_is_a_spec_with_no_searches(self, text):
        assert parse_spec(text, format="yaml").items("search") == []

    def test_a_searches_key_that_is_not_a_list_is_refused(self):
        with pytest.raises(PhabfiveDataException):
            parse_spec("kind: search\nsearches: one\n", format="yaml")

    def test_a_searches_item_that_is_not_a_mapping_is_refused(self):
        with pytest.raises(PhabfiveDataException):
            parse_spec("kind: search\nsearches:\n  - one\n", format="yaml")


class TestMultiDocument:
    """A multi-document file is one spec, not several."""

    THREE = (
        "title: One\nsearch:\n  priority: high\n"
        "---\n"
        "title: Two\nsearch:\n  priority: low\n"
        "---\n"
        "title: Three\nsearch:\n  priority: wish\n"
    )

    def test_the_documents_fold_into_one_list_in_file_order(self):
        spec = parse_spec(self.THREE, format="yaml")

        assert [item["title"] for item in spec.items("search")] == [
            "One",
            "Two",
            "Three",
        ]

    def test_the_envelope_is_declared_once(self):
        spec = parse_spec(
            "spec: phorge/v1alpha1\nkind: create\nmetadata:\n  name: two-parter\n"
            "tasks:\n  - title: One\n"
            "---\n"
            "tasks:\n  - title: Two\n",
            format="yaml",
        )

        assert spec.envelope.spec == SPEC_VERSION
        assert spec.metadata.name == "two-parter"
        assert [task["title"] for task in spec.items("task")] == ["One", "Two"]

    def test_repeating_the_same_envelope_is_accepted(self):
        spec = parse_spec(
            "kind: create\ntasks:\n  - title: One\n"
            "---\n"
            "kind: create\ntasks:\n  - title: Two\n",
            format="yaml",
        )

        assert len(spec.items("task")) == 2

    @pytest.mark.parametrize(
        "second",
        ["kind: search\nsearches: []\n", "spec: phorge/v1\n", "metadata:\n  name: b\n"],
        ids=["kind", "spec", "metadata"],
    )
    def test_documents_that_disagree_about_the_envelope_are_refused(self, second):
        text = (
            "spec: phorge/v1alpha1\nkind: create\nmetadata:\n  name: a\n"
            "tasks:\n  - title: One\n"
            "---\n" + second
        )

        with pytest.raises(PhabfiveDataException) as error:
            parse_spec(text, format="yaml")

        assert "one file" in str(error.value)

    def test_a_document_holding_only_variables_is_a_search_the_way_it_always_was(
        self,
    ):
        """`Maniphest._load_search_config` runs it, so this holds it.

        A document with a body but none of title/description/search is
        answered by the legacy reader with `{"search": {}, "title": None,
        "description": None}` - an unconstrained search. Folding it away
        here instead would make the two readers of the same legacy file
        disagree about how many searches it holds, which is a user-visible
        behaviour change the moment Phase 3 ports `maniphest search --with`.
        """
        spec = parse_spec(
            "kind: search\nvariables:\n  who: alice\n"
            "---\n"
            "title: One\nsearch:\n  assigned: '{{ who }}'\n",
            format="yaml",
        )

        assert spec.variables == {"who": "alice"}
        assert [item["search"] for item in spec.items("search")] == [
            {},
            {"assigned": "{{ who }}"},
        ]

    def test_an_envelope_only_document_adds_no_search(self):
        """The one shape that does fold away, because it is new.

        `spec:`/`kind:`/`metadata:` at the top of a multi-document file is
        not something the legacy reader has ever seen, and an empty search
        for it would run a query nobody wrote.
        """
        spec = parse_spec(
            "spec: phorge/v1alpha1\nkind: search\nmetadata:\n  name: stale\n"
            "---\n"
            "title: One\nsearch:\n  priority: high\n",
            format="yaml",
        )

        assert [item["search"] for item in spec.items("search")] == [
            {"priority": "high"}
        ]


class TestTheSameSpecInAnyFormat:
    """The envelope is not a YAML feature; every format can write one."""

    def test_json_and_yaml_produce_the_same_spec(self):
        yaml_text = (
            "spec: phorge/v1alpha1\nkind: search\nmetadata:\n  name: stale\n"
            "searches:\n  - type: task\n    search:\n      priority: high\n"
        )
        json_text = (
            '{"spec": "phorge/v1alpha1", "kind": "search", '
            '"metadata": {"name": "stale"}, '
            '"searches": [{"type": "task", "search": {"priority": "high"}}]}'
        )

        assert parse_spec(yaml_text, format="yaml") == parse_spec(
            json_text, format="json"
        )

    def test_toml_writes_a_multi_document_file_with_its_list_keys(self):
        toml_text = (
            'kind = "search"\n\n'
            "[[searches]]\ntitle = 'One'\n\n"
            "[[searches]]\ntitle = 'Two'\n"
        )

        spec = parse_spec(toml_text, format="toml")

        assert [item["title"] for item in spec.items("search")] == ["One", "Two"]

    def test_the_format_a_spec_was_written_in_is_recorded(self, tmp_path):
        path = write(
            tmp_path, "spec.toml", 'kind = "create"\n[[tasks]]\ntitle = "One"\n'
        )

        spec = load_spec(path)

        assert spec.format == "toml"
        assert spec.source == str(path)

    def test_where_a_spec_came_from_is_not_part_of_what_it_is(self, tmp_path):
        """A spec is its content; source and format are for error messages."""
        text = "kind: create\ntasks:\n  - title: One\n"
        path = write(tmp_path, "spec.yaml", text)

        assert load_spec(path) == parse_spec(text, format="yaml")


class TestTheSpecObject:
    """What a library caller holds, and what it may do with it."""

    def test_kind_and_metadata_are_shorthands_for_the_envelope(self):
        spec = parse_spec(METADATA_SPEC, format="yaml")

        assert spec.kind is spec.envelope.kind
        assert spec.metadata is spec.envelope.metadata

    def test_items_answers_for_both_spellings_of_an_object_type(self):
        spec = parse_spec("tasks:\n  - title: One\n", format="yaml")

        assert spec.items("task") == spec.items("tasks") == [{"title": "One"}]

    def test_items_is_empty_when_the_spec_holds_none(self):
        spec = parse_spec("tasks:\n  - title: One\n", format="yaml")

        assert spec.items("project") == []

    def test_items_hands_back_a_new_list_every_time(self):
        spec = parse_spec("tasks:\n  - title: One\n", format="yaml")

        spec.items("task").append({"title": "Two"})

        assert len(spec.items("task")) == 1

    def test_an_object_type_a_spec_cannot_hold_is_a_programming_error(self):
        spec = parse_spec("tasks:\n  - title: One\n", format="yaml")

        with pytest.raises(PhabfiveInputException):
            spec.items("countdown")

    def test_a_spec_is_frozen(self):
        spec = parse_spec("tasks:\n  - title: One\n", format="yaml")

        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.rendered = True  # type: ignore[misc]

    def test_nothing_is_rendered_yet(self):
        spec = parse_spec("tasks:\n  - title: One {{ who }}\n", format="yaml")

        assert spec.rendered is False

    def test_from_data_does_not_touch_the_callers_data(self):
        data = {"kind": "create", "tasks": [{"title": "One"}]}
        before = copy.deepcopy(data)

        spec = Spec.from_data(data)
        spec.body["tasks"][0]["title"] = "Changed"

        assert data == before

    def test_the_same_data_may_be_handed_over_twice(self):
        data = {"kind": "create", "variables": {"a": 1}, "tasks": [{"title": "One"}]}

        assert Spec.from_data(data) == Spec.from_data(data)

    def test_a_list_of_documents_is_accepted(self):
        spec = Spec.from_data(
            [{"tasks": [{"title": "One"}]}, {"tasks": [{"title": "Two"}]}]
        )

        assert len(spec.items("task")) == 2

    def test_data_that_is_not_a_document_is_refused(self):
        with pytest.raises(PhabfiveDataException):
            Spec.from_data("tasks: []")

    def test_a_document_that_is_not_a_mapping_is_refused(self):
        with pytest.raises(PhabfiveDataException):
            Spec.from_data([{"tasks": []}, ["nope"]])


class TestVariables:
    """Declared variables are neither envelope nor body."""

    def test_variables_are_split_out_of_the_body(self):
        spec = parse_spec(
            "variables:\n  who: alice\ntasks:\n  - title: '{{ who }}'\n", format="yaml"
        )

        assert spec.variables == {"who": "alice"}
        assert "variables" not in spec.body

    @pytest.mark.parametrize("text", ["tasks: []\n", "variables:\ntasks: []\n"])
    def test_a_spec_that_declares_none_has_none(self, text):
        assert parse_spec(text, format="yaml").variables == {}

    def test_a_declaration_with_a_default_is_left_unrendered(self):
        spec = parse_spec(
            "variables:\n  who:\n    default: alice\ntasks: []\n", format="yaml"
        )

        assert spec.variables == {"who": {"default": "alice"}}

    def test_variables_that_are_not_a_mapping_are_refused(self):
        with pytest.raises(PhabfiveDataException) as error:
            parse_spec("variables:\n  - alice\ntasks: []\n", format="yaml")

        assert "variables" in str(error.value)


class TestRoundTrip:
    """to_data writes a document that parses back to the same spec."""

    def test_a_spec_parses_back_to_itself(self):
        spec = parse_spec(METADATA_SPEC, format="yaml")

        assert Spec.from_data(spec.to_data()) == spec

    def test_an_inferred_kind_is_written_down(self):
        """The point of the round trip is a file nothing has to guess about."""
        data = parse_spec("tasks:\n  - title: One\n", format="yaml").to_data()

        assert data["kind"] == "create"

    def test_a_multi_document_spec_round_trips_as_one_document(self):
        spec = parse_spec(TestMultiDocument.THREE, format="yaml")

        again = Spec.from_data(spec.to_data())

        assert again.to_data() == spec.to_data()
        assert again.body == spec.body
        assert again.kind is spec.kind

    def test_writing_an_inferred_kind_down_is_what_declares_it(self):
        """The one thing a round trip changes, and it changes it on purpose.

        `kind_declared` says whether the file wrote `kind:`. `to_data` writes
        it, so the document it produces does declare one - which is the point
        of writing it rather than leaving the next reader to infer it again.
        """
        spec = parse_spec("tasks:\n  - title: One\n", format="yaml")

        assert spec.envelope.kind_declared is False
        assert Spec.from_data(spec.to_data()).envelope.kind_declared is True

    def test_to_data_hands_out_a_copy(self):
        spec = parse_spec("tasks:\n  - title: One\n", format="yaml")

        spec.to_data()["tasks"][0]["title"] = "Changed"

        assert spec.items("task") == [{"title": "One"}]


class TestSplitEnvelope:
    """The one-document case, for a caller that holds one document."""

    def test_it_separates_the_envelope_from_the_body(self):
        envelope, body = split_envelope(
            {
                "spec": SPEC_VERSION,
                "kind": "create",
                "metadata": {"name": "one"},
                "tasks": [{"title": "One"}],
            }
        )

        assert envelope == Envelope(
            spec=SPEC_VERSION,
            kind=Kind.CREATE,
            kind_declared=True,
            metadata=Metadata(name="one"),
        )
        assert body == {"tasks": [{"title": "One"}]}

    def test_it_is_normalize_documents_with_one_document(self):
        document = {"tasks": [{"title": "One"}]}

        assert split_envelope(document) == normalize_documents([document])


class TestLibraryHygiene:
    """phabfive/spec/ is library code; see CLAUDE.md."""

    def test_reading_an_envelope_does_not_touch_the_environment(self):
        before = dict(os.environ)

        parse_spec(METADATA_SPEC, format="yaml")

        assert dict(os.environ) == before

    # Everything §0 of the design forbids this subpackage, not just the CLI:
    # `phabfive.display`, `phabfive.table`, `phabfive.record_display`,
    # `phabfive.setup`, `phabfive.repl` and `phabfive.cache` are terminal or
    # process state and are equally out of bounds.
    FORBIDDEN = (
        "phabfive.cli",
        "phabfive.display",
        "phabfive.table",
        "phabfive.record_display",
        "phabfive.setup",
        "phabfive.repl",
        "phabfive.cache",
    )

    @pytest.mark.parametrize("name", FORBIDDEN)
    def test_the_envelope_module_imports_nothing_forbidden(self, name):
        """A grep, deliberately: `tests/test_spec_isolation.py` has the real
        out-of-process proof for the whole subpackage. This one is the fast
        one that names the module a reader is looking at.
        """
        import phabfive.spec.envelope as envelope

        text = Path(envelope.__file__).read_text(encoding="utf-8")

        assert name not in text


class TestRendering:
    """`Spec.render` is what `--set` is the command-line spelling of."""

    TEMPLATED = (
        "kind: create\nvariables:\n  team: Backend\n  sprint:\n"
        'tasks:\n  - title: "[{{ team }}] Sprint {{ sprint }}"\n'
    )

    def test_it_renders_the_body_against_the_declared_variables(self):
        spec = parse_spec(
            "kind: create\nvariables:\n  team: Backend\n"
            'tasks:\n  - title: "[{{ team }}] Planning"\n',
            format="yaml",
        )

        rendered = spec.render()

        assert rendered.body["tasks"][0]["title"] == "[Backend] Planning"
        assert rendered.rendered is True

    def test_supplied_values_beat_the_declarations(self):
        spec = parse_spec(self.TEMPLATED, format="yaml")

        rendered = spec.render({"team": "Platform", "sprint": 42})

        assert rendered.body["tasks"][0]["title"] == "[Platform] Sprint 42"
        assert rendered.variables == {"team": "Platform", "sprint": 42}

    def test_the_original_is_untouched(self):
        spec = parse_spec(self.TEMPLATED, format="yaml")
        before = copy.deepcopy(spec)

        spec.render({"sprint": 1})

        assert spec == before
        assert spec.rendered is False
        assert spec.body["tasks"][0]["title"] == "[{{ team }}] Sprint {{ sprint }}"

    def test_a_variable_nothing_supplies_is_refused(self):
        spec = parse_spec(self.TEMPLATED, format="yaml")

        with pytest.raises(PhabfiveDataException, match="sprint"):
            spec.render()

    def test_an_undefined_name_names_itself(self):
        spec = parse_spec(
            'kind: create\ntasks:\n  - title: "Sprint {{ sprint_numbr }}"\n',
            format="yaml",
        )

        with pytest.raises(PhabfiveDataException, match="sprint_numbr"):
            spec.render()

    def test_source_and_format_survive(self):
        spec = parse_spec(self.TEMPLATED, format="yaml", source="sprint.yaml")

        rendered = spec.render({"sprint": 1})

        assert rendered.source == "sprint.yaml"
        assert rendered.format == "yaml"


class TestTheThinMethods:
    """Every pass is reachable from the object a caller holds."""

    def test_validate_offline_is_the_function(self):
        from phabfive.spec.validate import validate_offline

        spec = parse_spec(
            "kind: search\nsearches:\n  - search: {limit: lots}\n", format="yaml"
        )

        assert spec.validate_offline() == validate_offline(spec)

    def test_validate_online_is_the_function(self):
        """The snippet in #475 calls the method, so the method has to exist."""
        from unittest.mock import MagicMock, patch

        spec = parse_spec("kind: create\ntasks:\n  - title: One\n", format="yaml")
        app = MagicMock()

        with patch(
            "phabfive.spec.online.validate_online", return_value=["sentinel"]
        ) as called:
            assert spec.validate_online(app) == ["sentinel"]

        called.assert_called_once_with(spec, app)
