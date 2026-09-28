# -*- coding: utf-8 -*-

"""`@some` and `@none` on `--parent`, `--subtask` and `--commit`.

`--parent=@some` replaced `--has-parents`, and `--parent=@none` the
`has-parents: false` only a spec could write; `--subtask` the same for
`--has-subtasks`. `--commit` takes only the keywords, and is the one the
server cannot answer, so it is filtered in Python.
"""

from unittest.mock import MagicMock, patch

import jsonschema
import pytest

from phabfive.cli.completers import complete_relation
from phabfive.exceptions import PhabfiveInputException
from phabfive.maniphest import Maniphest
from phabfive.spec import Kind, build_schema, parse_spec, validate_offline
from phabfive.spec.search import task_relation


def _task(task_id, priority=50):
    return {
        "id": task_id,
        "phid": f"PHID-TASK-{task_id}",
        "fields": {
            "name": f"Task {task_id}",
            "status": {"name": "Open", "value": "open"},
            "priority": {"name": "Normal", "value": priority},
            "description": {"raw": ""},
        },
        "attachments": {"columns": {"boards": {}}},
    }


def _maniphest(tasks, commits):
    """A search answering `tasks`, where `commits` maps a task id to its commits."""
    with patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None):
        maniphest = Maniphest()
    maniphest.phab = MagicMock()
    maniphest.url = "https://phorge.example.com"
    maniphest.conf = {"PHAB_SPACE": "S1"}
    maniphest.phab.project.query.return_value = {"data": {}}

    def search(**kwargs):
        response = MagicMock()
        response.response = {"data": tasks}
        return response

    def edge_search(**kwargs):
        data = [
            {
                "sourcePHID": f"PHID-TASK-{task_id}",
                "edgeType": "task.commit",
                "destinationPHID": commit,
            }
            for task_id, phids in commits.items()
            if f"PHID-TASK-{task_id}" in kwargs["sourcePHIDs"]
            for commit in phids
        ]
        return {"data": data, "cursor": {"after": None}}

    maniphest.phab.maniphest.search.side_effect = search
    maniphest.phab.edge.search.side_effect = edge_search
    return maniphest


def _ids(result):
    return [int(task["_url"].rsplit("/T", 1)[1]) for task in result["tasks"]]


class TestTaskRelation:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("@some", "@some"),
            ("@None", "@none"),
            (["@none"], "@none"),
            (" @some ", "@some"),
            ("T1,T2", [1, 2]),
            (None, None),
            ("", None),
        ],
    )
    def test_it_reads(self, value, expected):
        assert task_relation(value) == expected

    @pytest.mark.parametrize("value", ["T1,@none", ["@some", "T1"], "@some,@none"])
    def test_a_keyword_is_the_whole_value(self, value):
        with pytest.raises(PhabfiveInputException, match="for --parent"):
            task_relation(value, option="--parent")

    def test_without_ids_a_task_is_refused(self):
        with pytest.raises(PhabfiveInputException, match="Expected @some or @none"):
            task_relation("T1", ids=False)

    def test_a_malformed_id_is_still_refused(self):
        with pytest.raises(PhabfiveInputException, match="T123"):
            task_relation("P45")


class TestCommitFilter:
    def test_some_keeps_the_tasks_with_a_commit(self):
        maniphest = _maniphest(
            [_task(1), _task(2), _task(3)], {2: ["PHID-CMIT-a"], 3: ["PHID-CMIT-b"]}
        )

        result = maniphest.task_search(text_query="x", commit="@some")

        assert sorted(_ids(result)) == [2, 3]

    def test_none_keeps_the_tasks_without(self):
        maniphest = _maniphest([_task(1), _task(2)], {2: ["PHID-CMIT-a"]})

        result = maniphest.task_search(text_query="x", commit="@none")

        assert _ids(result) == [1]

    def test_it_is_applied_before_the_limit(self):
        """The limit keeps the top N of the tasks that match, not of all."""
        maniphest = _maniphest(
            [_task(1, priority=90), _task(2, priority=50), _task(3, priority=10)],
            {2: ["PHID-CMIT-a"], 3: ["PHID-CMIT-b"]},
        )

        result = maniphest.task_search(text_query="x", commit="@some", limit=1)

        assert _ids(result) == [2]

    def test_one_edge_search_for_every_candidate(self):
        maniphest = _maniphest([_task(1), _task(2)], {})

        maniphest.task_search(text_query="x", commit="@none")

        [call] = maniphest.phab.edge.search.call_args_list
        assert sorted(call.kwargs["sourcePHIDs"]) == ["PHID-TASK-1", "PHID-TASK-2"]
        assert call.kwargs["types"] == ["task.commit"]

    def test_it_alone_is_a_search(self):
        maniphest = _maniphest([_task(1)], {})

        assert _ids(maniphest.task_search(commit="@none")) == [1]

    def test_without_it_no_edge_is_read(self):
        maniphest = _maniphest([_task(1)], {})

        maniphest.task_search(text_query="x")

        maniphest.phab.edge.search.assert_not_called()


def _check(search):
    text = "spec: phorge/v1alpha1\nkind: search\nsearches:\n  - search: " + search
    return validate_offline(parse_spec(text, format="yaml"))


class TestTheSpec:
    @pytest.mark.parametrize(
        "search",
        [
            '{parent: "@none"}',
            '{subtask: "@some"}',
            '{parent: ["@some"]}',
            '{commit: "@none"}',
            "{parent: T1}",
        ],
    )
    def test_the_keywords_are_valid(self, search):
        assert _check(search) == []

    @pytest.mark.parametrize(
        "search", ['{parent: "T1,@none"}', '{subtask: ["@some", "T1"]}']
    )
    def test_a_keyword_beside_an_id_is_refused(self, search):
        [problem] = _check(search)

        assert problem.code == "bad-monogram"
        assert "cannot be combined" in problem.reason

    def test_commit_takes_no_task(self):
        [problem] = _check("{commit: T1}")

        assert problem.code == "unknown-value"

    @pytest.mark.parametrize(
        "key, replacement", [("has-parents", "parent"), ("has-subtasks", "subtask")]
    )
    def test_the_removed_keys_name_their_replacement(self, key, replacement):
        [problem] = _check(f"{{{key}: true}}")

        assert problem.code == "unknown-key"
        assert f'Write {replacement}: "@some"' in problem.reason

    @pytest.mark.parametrize(
        "search, valid",
        [
            ({"parent": "@none"}, True),
            ({"parent": ["@some"]}, True),
            ({"parent": "T1,T2"}, True),
            ({"commit": "@some"}, True),
            ({"commit": "T1"}, False),
            ({"parent": "@all"}, False),
        ],
    )
    def test_the_schema_agrees(self, search, valid):
        document = {
            "spec": "phorge/v1alpha1",
            "kind": "search",
            "searches": [{"search": search}],
        }
        validator = jsonschema.Draft202012Validator(build_schema(Kind.SEARCH))

        errors = list(validator.iter_errors(document))

        assert (not errors) is valid


class TestCompletion:
    def test_the_keywords_are_offered(self):
        assert complete_relation("") == ["@some", "@none"]
        assert complete_relation("@n") == ["@none"]
