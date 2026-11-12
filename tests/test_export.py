# -*- coding: utf-8 -*-

"""Writing a spec from objects that exist (`phabfive.export`, `phabfive export`).

An export is only worth having if it can be read back, so most of what is
pinned here is one property seen from several sides: **the document says
what the object is, in the grammar a spec takes, and nothing else.**

- every value is the *input* spelling - a status key, a `#hashtag`, a policy
  keyword - never the label a show command prints;
- the document validates offline and against the published schema;
- the text parses back to exactly the spec it was written from, whatever
  the description holds;
- an edit export names and stamps each object, a create export does neither.

That an unchanged export applies as no change at all needs the edit engine
and a server, and is `tests/e2e`.
"""

import json
from unittest.mock import patch

import jsonschema
import pytest
from typer.testing import CliRunner

from phabfive.cli import app, preprocess_monograms
from phabfive.cli.export import _monogram, _piped
from phabfive.core import Phabfive
from phabfive.exceptions import (
    PhabfiveDataException,
    PhabfiveInputException,
    PhabfiveNotFoundException,
)
from phabfive.export import dump_spec, export_spec
from phabfive.spec import Spec, build_schema, parse_spec
from phabfive.spec.registry import spec_keys

runner = CliRunner()

HANDLES = {
    "PHID-USER-alice": {"type": "USER", "name": "alice", "uri": "/p/alice/"},
    "PHID-USER-bob": {"type": "USER", "name": "bob", "uri": "/p/bob/"},
    "PHID-PROJ-infra": {"type": "PROJ", "name": "Infrastructure", "uri": "/tag/infra/"},
    "PHID-PROJ-sec": {"type": "PROJ", "name": "Security", "uri": "/tag/security/"},
    "PHID-SPCE-1": {"type": "SPCE", "name": "S1 Default", "uri": "/S1"},
    "PHID-TASK-2": {"type": "TASK", "name": "T2", "uri": "/T2"},
    "PHID-TASK-10": {"type": "TASK", "name": "T10", "uri": "/T10"},
    "PHID-CMIT-1": {"type": "CMIT", "name": "rXabc123", "uri": "/rXabc123"},
}


def a_task(task_id=1, **fields):
    base = {
        "name": "Fix the login flow",
        "description": {"raw": "First line\nSecond line\n"},
        "ownerPHID": "PHID-USER-alice",
        "status": {"value": "open", "name": "Open"},
        "priority": {"value": 80, "name": "High"},
        "spacePHID": "PHID-SPCE-1",
        "dateModified": 1769851800,
        "policy": {"view": "users", "interact": "users", "edit": "PHID-PROJ-sec"},
    }
    base.update(fields)

    return {
        "id": task_id,
        "phid": f"PHID-TASK-{task_id}",
        "fields": base,
        "attachments": {
            "projects": {"projectPHIDs": ["PHID-PROJ-sec", "PHID-PROJ-infra"]},
            "subscribers": {"subscriberPHIDs": ["PHID-USER-bob", "PHID-USER-alice"]},
            "columns": {
                "boards": {"PHID-PROJ-sec": {"columns": [{"name": "In Progress"}]}}
            },
        },
    }


class FakePhab:
    """A Conduit client answering from tables, and counting what it is asked."""

    def __init__(self, tasks, edges=None, handles=None):
        self.tasks = {task["id"]: task for task in tasks}
        self.edges = edges or {}
        self.handles = HANDLES if handles is None else handles
        self.calls = []
        self.maniphest = self
        self.phid = self

    def search(self, constraints, attachments):
        self.calls.append("maniphest.search")
        assert attachments == {"columns": True, "projects": True, "subscribers": True}

        return {
            "data": [
                self.tasks[task_id]
                for task_id in constraints["ids"]
                if task_id in self.tasks
            ]
        }

    def query(self, phids):
        self.calls.append("phid.query")

        return {phid: self.handles[phid] for phid in phids if phid in self.handles}


class FakeApp:
    def __init__(self, *tasks, **more):
        self.phab = FakePhab(tasks, **more)


@pytest.fixture(autouse=True)
def edges(monkeypatch):
    def fetch(phab, task_phids, kinds):
        phab.calls.append("edge.search")

        return {
            phid: {kind: list(phab.edges.get((phid, kind), [])) for kind in kinds}
            for phid in task_phids
        }

    monkeypatch.setattr("phabfive.maniphest.fetchers.fetch_edges_for_tasks", fetch)


def exported(*tasks, kind="edit", monograms=None, **more):
    application = FakeApp(*tasks, **more)
    wanted = monograms or [f"T{task['id']}" for task in tasks]

    return export_spec(application, wanted, kind=kind), application


class TestAnEditExport:
    def test_it_states_the_whole_task_in_the_grammar_a_spec_takes(self):
        spec, _ = exported(
            a_task(),
            edges={
                ("PHID-TASK-1", "parents"): ["PHID-TASK-10", "PHID-TASK-2"],
                ("PHID-TASK-1", "commits"): ["PHID-CMIT-1"],
            },
        )

        assert spec.to_data() == {
            "spec": "phorge/v1alpha1",
            "kind": "edit",
            "interpolate": False,
            "tasks": [
                {
                    "task": "T1",
                    "modified": "2026-01-31T09:30:00Z",
                    "title": "Fix the login flow",
                    "status": "open",
                    "priority": "high",
                    "assignment": "alice",
                    "projects": ["#infra", "#security"],
                    "column": "In Progress",
                    "subscribers": ["alice", "bob"],
                    "space": "S1",
                    "visible-to": "users",
                    "editable-by": "#security",
                    "parents": ["T2", "T10"],
                    "subtasks": [],
                    "commits": ["rXabc123"],
                    "description": "First line\nSecond line\n",
                }
            ],
        }

    def test_every_key_it_writes_is_a_key_an_edit_takes(self):
        spec, _ = exported(a_task())

        assert set(spec.items("task")[0]) <= spec_keys("task", "edit")

    def test_it_is_clean_offline_and_against_the_schema(self):
        spec, _ = exported(a_task(), a_task(2, ownerPHID=None))
        validator = jsonschema.Draft202012Validator(build_schema("edit"))

        assert spec.validate_offline() == []
        assert list(validator.iter_errors(spec.to_data())) == []

    def test_what_is_empty_is_written_as_empty(self):
        """An edit states the task, so "no subscribers" is said, not skipped."""
        bare = a_task(ownerPHID=None, description={"raw": ""})
        bare["attachments"] = {}
        [item] = exported(bare)[0].items("task")

        assert item["assignment"] == "@none"
        assert item["projects"] == []
        assert item["subscribers"] == []
        assert item["description"] == ""
        assert "column" not in item

    def test_lists_are_sorted_so_the_file_moves_only_when_the_task_does(self):
        first = a_task()
        second = a_task()
        second["attachments"]["projects"]["projectPHIDs"].reverse()
        second["attachments"]["subscribers"]["subscriberPHIDs"].reverse()

        assert exported(first)[0] == exported(second)[0]

    def test_the_tasks_come_out_in_the_order_asked_for_and_once_each(self):
        spec, _ = exported(a_task(1), a_task(2), monograms=["T2", "t1", "T2"])

        assert [item["task"] for item in spec.items("task")] == ["T2", "T1"]

    def test_it_asks_once_however_many_tasks(self):
        _, application = exported(a_task(1), a_task(2), a_task(3))

        assert application.phab.calls == [
            "maniphest.search",
            "edge.search",
            "phid.query",
        ]

    def test_it_says_interpolate_false(self):
        """A description quoting a template must not be read as one."""
        spec, _ = exported(a_task(description={"raw": "Set {{ .Values.tag }}"}))

        assert spec.interpolates is False
        assert spec.validate_offline() == []


class TestWhatCannotBeReadBackIsLeftOut:
    def test_a_priority_outside_the_stock_table(self):
        [item] = exported(a_task(priority={"value": 42, "name": "Odd"}))[0].items(
            "task"
        )

        assert "priority" not in item

    def test_a_column_when_the_task_is_on_several_boards(self):
        both = a_task()
        both["attachments"]["columns"]["boards"]["PHID-PROJ-infra"] = {
            "columns": [{"name": "Backlog"}]
        }

        assert "column" not in exported(both)[0].items("task")[0]

    def test_a_name_the_token_cannot_see_is_its_phid(self):
        spec, _ = exported(a_task(), handles={})
        [item] = spec.items("task")

        assert item["assignment"] == "PHID-USER-alice"
        assert item["projects"] == ["PHID-PROJ-infra", "PHID-PROJ-sec"]
        assert spec.validate_offline() == []

    def test_a_subscriber_that_is_not_a_user_is_its_phid(self):
        mixed = a_task()
        mixed["attachments"]["subscribers"]["subscriberPHIDs"] = [
            "PHID-USER-bob",
            "PHID-OPKG-1",
        ]

        assert exported(mixed)[0].items("task")[0]["subscribers"] == [
            "bob",
            "PHID-OPKG-1",
        ]

    @pytest.mark.parametrize(
        "stored", ["obj.subscriptions.subscribers", "obj.maniphest.author"]
    )
    def test_a_policy_the_grammar_has_no_spelling_for(self, stored):
        """Written verbatim it is `bad-policy`, and the export would not apply."""
        spec, _ = exported(a_task(policy={"view": stored, "edit": "users"}))
        [item] = spec.items("task")

        assert "visible-to" not in item
        assert item["editable-by"] == "users"
        assert spec.validate_offline() == []

    @pytest.mark.parametrize("keyword", ["public", "users", "admin", "no-one"])
    def test_every_policy_keyword_is_written(self, keyword):
        spec, _ = exported(a_task(policy={"view": keyword, "edit": keyword}))

        assert spec.items("task")[0]["visible-to"] == keyword
        assert spec.validate_offline() == []

    def test_a_user_policy_is_written_with_its_at(self):
        owned = a_task(policy={"view": "PHID-USER-bob", "edit": "PHID-PLCY-custom"})
        [item] = exported(owned)[0].items("task")

        assert item["visible-to"] == "@bob"
        assert item["editable-by"] == "PHID-PLCY-custom"


class TestACreateExport:
    def test_it_describes_a_new_task_like_the_old_one(self):
        spec, application = exported(
            a_task(), kind="create", edges={("PHID-TASK-1", "parents"): ["PHID-TASK-2"]}
        )

        assert spec.to_data() == {
            "spec": "phorge/v1alpha1",
            "kind": "create",
            "interpolate": False,
            "tasks": [
                {
                    "title": "Fix the login flow",
                    "status": "open",
                    "priority": "high",
                    "assignment": "alice",
                    "projects": ["#infra", "#security"],
                    "column": "In Progress",
                    "subscribers": ["alice", "bob"],
                    "space": "S1",
                    "visible-to": "users",
                    "editable-by": "#security",
                    "description": "First line\nSecond line\n",
                }
            ],
        }
        # The relations are not written, so they are not even asked for
        assert "edge.search" not in application.phab.calls

    def test_it_is_a_valid_create_spec(self):
        spec, _ = exported(a_task(), kind="create")
        validator = jsonschema.Draft202012Validator(build_schema("create"))

        assert spec.validate_offline() == []
        assert list(validator.iter_errors(spec.to_data())) == []
        assert set(spec.items("task")[0]) <= spec_keys("task", "create")

    def test_what_is_nothing_is_not_written(self):
        """`assignment: "@none"` and `projects: []` mean nothing to a create."""
        bare = a_task(ownerPHID=None, description={"raw": ""})
        bare["attachments"] = {}
        [item] = exported(bare, kind="create")[0].items("task")

        assert set(item) == {
            "title",
            "status",
            "priority",
            "space",
            "visible-to",
            "editable-by",
        }


class TestRefusals:
    def test_a_task_that_does_not_exist_names_every_one(self):
        with pytest.raises(PhabfiveNotFoundException, match="T404, T405"):
            exported(a_task(), monograms=["T1", "T404", "T405"])

    @pytest.mark.parametrize("monogram", ["P1", "K7", "R2", "T", "task", "T1x"])
    def test_a_type_it_does_not_write_is_refused_by_name(self, monogram):
        with pytest.raises(PhabfiveInputException, match="It takes: T123"):
            exported(a_task(), monograms=[monogram])

    def test_a_search_spec_is_not_something_to_export_as(self):
        with pytest.raises(PhabfiveInputException, match="edit or create"):
            exported(a_task(), kind="search")


TEXTS = [
    "one line",
    "two\nlines",
    "two\nlines\n",
    "trailing blank lines\n\n\n",
    "\nleading newline",
    "  leading spaces\nsecond",
    "trailing space \nsecond",
    "windows\r\nline endings\r\n",
    "tab\tseparated\nsecond",
    "- looks: like yaml\n- [1, 2]\n",
    "# not a comment\n",
    "{{ a.template }}\n{% raw %}",
    "colon: at the end:",
    "\n",
    "\n\n",
    "   ",
    "",
    "unicode: åäö ☃\nsecond",
    "'quoted' and \"double\"",
    "back\\slash\nsecond",
]

TITLES = ["yes", "no", "null", "123", "1.5", "2026-01-31", "T12", "#tag", "@none", "~"]


class TestTheTextParsesBackExactly:
    """Fidelity: an export that reads back differently is a change nobody made."""

    @pytest.mark.parametrize("text", TEXTS)
    @pytest.mark.parametrize("format", ["yaml", "json", "jsonl"])
    def test_a_description(self, text, format):
        spec, _ = exported(a_task(description={"raw": text}))
        back = parse_spec(dump_spec(spec, format), format=format)

        assert back.items("task")[0]["description"] == text
        assert back == spec

    @pytest.mark.parametrize("title", TITLES)
    def test_a_title_that_looks_like_something_else(self, title):
        spec, _ = exported(a_task(name=title))
        back = parse_spec(dump_spec(spec), format="yaml")

        assert back.items("task")[0]["title"] == title
        assert back.validate_offline() == []

    def test_the_stamp_stays_text(self):
        """Unquoted it would be a native timestamp; equal, but not this spec."""
        spec, _ = exported(a_task())

        assert "modified: '2026-01-31T09:30:00Z'" in dump_spec(spec)

    def test_ordinary_text_is_a_block_a_person_can_edit(self):
        spec, _ = exported(a_task())

        assert "    description: |\n      First line\n      Second line\n" in dump_spec(
            spec
        )

    def test_text_a_block_cannot_hold_is_still_exact(self):
        spec, _ = exported(a_task(description={"raw": "trailing space \nsecond"}))
        text = dump_spec(spec)

        assert "description: |" not in text
        assert parse_spec(text, format="yaml") == spec

    def test_a_long_title_is_not_folded(self):
        long = "word " * 60
        spec, _ = exported(a_task(name=long.strip()))

        assert f"title: {long.strip()}\n" in dump_spec(spec)

    def test_it_ends_in_one_newline(self):
        spec, _ = exported(a_task())

        for format in ("yaml", "json", "jsonl"):
            assert dump_spec(spec, format).endswith("\n")

    def test_jsonl_is_one_line(self):
        spec, _ = exported(a_task())

        assert dump_spec(spec, "jsonl").count("\n") == 1

    def test_a_format_it_does_not_write(self):
        with pytest.raises(PhabfiveInputException, match="yaml, json or jsonl"):
            dump_spec(exported(a_task())[0], "toml")

    def test_yaml_that_would_not_survive_falls_back_to_json(self):
        """The check is real: break the pretty writer and the text is still right."""
        spec, _ = exported(a_task())

        with patch("phabfive.export._yaml", return_value="kind: edit\ntasks: []\n"):
            text = dump_spec(spec)

        assert json.loads(text) == spec.to_data()
        assert parse_spec(text, format="yaml") == spec


class TestAnExportWithItsKindRemoved:
    def test_it_is_refused_rather_than_applied_as_a_create(self):
        spec, _ = exported(a_task())
        data = spec.to_data()
        del data["kind"]

        problems = Spec.from_data(data).validate_offline()

        assert problems
        assert {problem.code for problem in problems} == {"unknown-key"}


# --------------------------------------------------------------------------
# The command
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def restore_output_format():
    original = Phabfive._output_format
    try:
        yield
    finally:
        Phabfive._output_format = original


@pytest.fixture
def application():
    one = FakeApp(a_task(1), a_task(2))

    with patch("phabfive.cli.apps.get_app", return_value=one):
        yield one


def invoke(*args, **kwargs):
    return runner.invoke(app, list(args), **kwargs)


class TestTheCommand:
    def test_it_writes_yaml_to_stdout(self, application):
        result = invoke("export", "T1")

        assert result.exit_code == 0, result.output
        assert result.stdout.startswith("spec: phorge/v1alpha1\nkind: edit\n")
        assert parse_spec(result.stdout, format="yaml").items("task")[0]["task"] == "T1"

    def test_yaml_is_the_default_whatever_the_terminal(self, application):
        """Not rich on a TTY and not PHORGE_FALLBACK off one: it is a file."""
        with patch.object(Phabfive, "_get_auto_format", return_value="rich"):
            result = invoke("export", "T1")

        assert result.stdout.startswith("spec: phorge/v1alpha1\n")

    @pytest.mark.parametrize("format", ["json", "jsonl"])
    def test_format_picks_another_serialization(self, application, format):
        result = invoke(f"--format={format}", "export", "T1")

        assert json.loads(result.stdout)["kind"] == "edit"

    @pytest.mark.parametrize("format", ["rich", "tree", "table", "value"])
    def test_a_terminal_format_is_refused(self, application, format):
        result = invoke(f"--format={format}", "export", "T1")

        assert result.exit_code == 1
        assert "yaml, json, jsonl" in result.output

    def test_as_create(self, application):
        result = invoke("export", "T1", "--as", "create")
        spec = parse_spec(result.stdout, format="yaml")

        assert spec.kind.value == "create"
        assert "task" not in spec.items("task")[0]

    def test_output_writes_a_file_and_nothing_else(self, application, tmp_path):
        target = tmp_path / "task.yaml"
        result = invoke("export", "T1", "T2", "-o", str(target))

        assert result.exit_code == 0
        assert result.stdout == ""
        assert len(parse_spec(target.read_text(), format="yaml").items("task")) == 2

    def test_a_link_is_a_monogram(self, application):
        result = invoke("export", "https://phorge.example.com/T2")

        assert parse_spec(result.stdout, format="yaml").items("task")[0]["task"] == "T2"

    def test_a_missing_task_is_an_error_and_no_document(self, application):
        result = invoke("export", "T1", "T404")

        assert result.exit_code == 1
        assert result.stdout == ""
        assert "T404" in result.output

    def test_a_type_it_does_not_write(self, application):
        result = invoke("export", "P1")

        assert result.exit_code == 1
        assert "It takes: T123" in result.output

    def test_the_monogram_shortcut_leaves_it_alone(self):
        """`phabfive T1` expands; `phabfive export T1` must not."""
        argv = ["phabfive", "export", "T1", "T2"]

        assert preprocess_monograms(list(argv)) == argv


class TestStandardInput:
    def test_monograms(self, application):
        result = invoke("export", input="T2\nT1\n")

        tasks = parse_spec(result.stdout, format="yaml").items("task")

        assert [item["task"] for item in tasks] == ["T2", "T1"]

    def test_the_yaml_a_search_prints(self, application):
        piped = "- Link: https://phorge.example.com/T1\n  Task:\n    Name: x\n"
        result = invoke("export", input=piped)

        assert result.exit_code == 0, result.output
        assert parse_spec(result.stdout, format="yaml").items("task")[0]["task"] == "T1"

    def test_parsing(self):
        assert _piped("T1 T2\nT3") == ["T1", "T2", "T3"]
        assert _piped('[{"Link": "http://h/T4"}, {"Link": "http://h/T5"}]') == [
            "T4",
            "T5",
        ]
        assert _piped('{"Link": "http://h/T6"}\n---\n{"Link": "http://h/T7"}') == [
            "T6",
            "T7",
        ]
        assert _monogram("https://phorge.example.com/T12") == "T12"

    def test_json_lines_with_nothing_between_the_records(self):
        """What `--format=jsonl` prints, which is not one YAML stream."""
        assert _piped('{"Link": "http://h/T8"}\n{"Link": "http://h/T9"}\n') == [
            "T8",
            "T9",
        ]

    def test_a_record_with_no_link_is_refused(self):
        with pytest.raises(PhabfiveDataException, match="no Link"):
            _piped("- Task:\n    Name: x\n")

    def test_nothing_piped_is_an_error(self, application):
        result = invoke("export", input="")

        assert result.exit_code == 1
