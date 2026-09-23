# -*- coding: utf-8 -*-

"""Running a search spec from a program, with no command line anywhere (#476).

`phabfive.spec.search` is the half of `maniphest search` that is not a
terminal: what a spec and a caller's overrides mean together, what a search
would send, and what it answered. The command is a caller of it like any
other, which is the whole point - a web frontend or a script gets the same
interpretation without reimplementing `search()`.

The tests below hand `plan_search` a plain dict and a stub app. Nothing here
constructs a `Maniphest`, opens a socket or reads a file, because none of
that is what a plan is.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from phabfive.exceptions import (
    PhabfiveConfigException,
    PhabfiveDataException,
    PhabfiveInputException,
)
from phabfive.spec import Spec
from phabfive.spec.problems import Layer, Severity, problem
from phabfive.spec.search import (
    SearchPlan,
    SearchPlanError,
    SearchResult,
    banner_title,
    check_search_params,
    plan_search,
    plan_searches,
    run_search,
    wants_banner,
)
from phabfive.transitions import parse_status_patterns

REPOSITORY = Path(__file__).resolve().parent.parent


class StubApp:
    """As little app as a plan needs: one parser and one search method.

    A plan asks the instance exactly one thing - whether a status pattern
    names a status it knows - and a run calls one method. Everything else a
    `Maniphest` is would only hide that.
    """

    def __init__(self, payload=None):
        self.payload = payload
        self.calls = []

    def parse_status_patterns_with_api(self, value):
        self.calls.append(("parse_status_patterns_with_api", value))
        return parse_status_patterns(value)

    def task_search(self, **kwargs):
        self.calls.append(("task_search", kwargs))
        return self.payload


def item(search=None, **rest):
    """One `searches:` item, as `Spec.items("search")` yields one."""
    return {"type": "task", "search": search or {}, **rest}


class TestPlanningFromADict:
    """A spec is data; so is a plan built from one."""

    def test_the_parameters_are_what_task_search_takes(self):
        plan = plan_search(StubApp(), item({"tag": "infra", "limit": 5}))

        assert plan.params["tag"] == "infra"
        assert plan.params["limit"] == 5
        assert plan.params["order"] is None
        assert plan.params["show_history"] is False
        assert plan.params["include_closed"] is False

    def test_a_hyphenated_key_becomes_an_underscored_parameter(self):
        plan = plan_search(
            StubApp(), item({"visible-to": "users", "created-after": "7d"})
        )

        assert plan.params["visible_to"] == "users"
        assert plan.params["created_after"] == "7d"

    def test_text_query_is_the_one_key_spelled_with_an_underscore(self):
        plan = plan_search(StubApp(), item({"text_query": "needle"}))

        assert plan.params["text_query"] == "needle"
        assert plan.text_query == "needle"

    def test_an_item_with_no_search_section_is_an_empty_search(self):
        plan = plan_search(StubApp(), {"title": "Just a banner"})

        assert plan.params["tag"] is None
        assert plan.has_criteria is False

    def test_the_object_type_defaults_to_task(self):
        assert plan_search(StubApp(), {"search": {"tag": "x"}}).object_type == "task"

    def test_the_plan_is_frozen(self):
        plan = plan_search(StubApp(), item({"tag": "infra"}))

        with pytest.raises(Exception):
            plan.params = {}

    def test_the_source_does_not_take_part_in_equality(self):
        """The same search read from a file and from a dict is one search."""
        one = plan_search(StubApp(), item({"tag": "x"}), source="a.yaml")
        two = plan_search(StubApp(), item({"tag": "x"}), source="<data>")

        assert one == two


class TestPrecedence:
    """Overrides beat the spec; a None override is not an override."""

    def test_an_override_wins(self):
        plan = plan_search(
            StubApp(), item({"tag": "infra"}), overrides={"tag": "platform"}
        )

        assert plan.params["tag"] == "platform"

    def test_a_none_override_leaves_the_spec_alone(self):
        plan = plan_search(StubApp(), item({"tag": "infra"}), overrides={"tag": None})

        assert plan.params["tag"] == "infra"

    def test_an_override_with_no_spec_value_is_used(self):
        plan = plan_search(StubApp(), item(), overrides={"tag": "platform"})

        assert plan.params["tag"] == "platform"

    def test_the_default_is_last(self):
        plan = plan_search(StubApp(), item(), overrides={"limit": None})

        assert plan.params["limit"] == 100

    def test_a_false_in_the_spec_is_a_value_not_an_absence(self):
        plan = plan_search(StubApp(), item({"show-history": False}))

        assert plan.params["show_history"] is False

    def test_a_zero_override_is_a_value_not_an_absence(self):
        plan = plan_search(StubApp(), item({"limit": 5}), overrides={"limit": 0})

        assert plan.params["limit"] == 0


class TestPatterns:
    """The three transition filters are parsed once, at plan time."""

    def test_a_column_pattern_is_parsed(self):
        plan = plan_search(StubApp(), item({"column": "in:Backlog"}))

        assert [
            condition["type"]
            for pattern in plan.params["column_patterns"]
            for condition in pattern.conditions
        ] == ["in"]

    def test_a_status_pattern_goes_through_the_app(self):
        """Only the instance knows which statuses it has."""
        app = StubApp()

        plan_search(app, item({"status": "any"}))

        assert app.calls == [("parse_status_patterns_with_api", "any")]

    def test_no_pattern_is_none_rather_than_an_empty_list(self):
        plan = plan_search(StubApp(), item({"tag": "x"}))

        assert plan.params["column_patterns"] is None
        assert plan.params["priority_patterns"] is None
        assert plan.params["status_patterns"] is None

    @pytest.mark.parametrize(
        "key,label",
        [("column", "column"), ("priority", "priority"), ("status", "status")],
    )
    def test_a_bad_pattern_names_its_kind(self, key, label):
        with pytest.raises(SearchPlanError) as error:
            plan_search(StubApp(), item({key: "bogus:value"}))

        assert str(error.value).startswith(f"Invalid {label} filter pattern:")
        assert error.value.check == "invalid-pattern"


class TestTaskIdGrammar:
    """`include:` and `exclude:` take monograms, however they are written."""

    @pytest.mark.parametrize(
        "value",
        ["T1,T2", ["T1", "T2"], ["T1,T2"], " T1 , T2 ", "T1,,T2"],
    )
    def test_the_accepted_spellings(self, value):
        plan = plan_search(StubApp(), item({"include": value}))

        assert plan.params["include_task_ids"] == [1, 2]

    def test_nothing_is_none_rather_than_an_empty_list(self):
        plan = plan_search(StubApp(), item({"tag": "x", "exclude": ","}))

        assert plan.params["exclude_task_ids"] is None

    def test_another_applications_monogram_is_not_a_task(self):
        with pytest.raises(SearchPlanError) as error:
            plan_search(StubApp(), item({"include": "K2"}))

        assert str(error.value) == "Invalid task ID 'K2'. Expected format: T123"
        assert error.value.check == "invalid-task-id"

    def test_an_id_in_both_lists_is_refused(self):
        with pytest.raises(SearchPlanError) as error:
            plan_search(StubApp(), item({"include": "T10,T2", "exclude": "T2,T10"}))

        assert str(error.value) == "T2, T10 cannot be both included and excluded"
        assert error.value.check == "include-exclude-overlap"


class TestHasCriteria:
    """What counts as having asked for something."""

    @pytest.mark.parametrize(
        "search",
        [
            {"text_query": "needle"},
            {"tag": "infra"},
            {"assigned": "@me"},
            {"author": "alice"},
            {"space": "S1"},
            {"visible-to": "users"},
            {"editable-by": "admin"},
            {"created-after": "7d"},
            {"created-before": "1y"},
            {"updated-after": "2w"},
            {"updated-before": "3m"},
            {"all": True},
            {"column": "in:Backlog"},
            {"priority": "in:high"},
            {"status": "any"},
            {"include": "T1"},
        ],
    )
    def test_a_criterion(self, search):
        assert plan_search(StubApp(), item(search)).has_criteria is True

    @pytest.mark.parametrize(
        "search",
        [
            {},
            {"exclude": "T1"},
            {"show-history": True},
            {"show-metadata": True},
            {"show-policy": True},
            {"limit": 5},
            {"order": "title"},
        ],
    )
    def test_not_a_criterion(self, search):
        assert plan_search(StubApp(), item(search)).has_criteria is False


class TestTheBanner:
    """A search's own title and description, not the file's metadata."""

    def test_a_named_search_wants_one(self):
        plan = plan_search(StubApp(), item({"tag": "x"}, title="Mine"))

        assert plan.wants_banner is True
        assert plan.banner_title == "Mine"

    def test_a_described_search_wants_one(self):
        plan = plan_search(StubApp(), item({"tag": "x"}, description="Why"))

        assert plan.wants_banner is True

    def test_a_lone_unnamed_search_does_not(self):
        plan = plan_search(StubApp(), item({"tag": "x"}))

        assert plan.wants_banner is False

    def test_one_of_several_unnamed_searches_does(self):
        plan = plan_search(StubApp(), item({"tag": "x"}), index=2, total=3)

        assert plan.wants_banner is True
        assert plan.banner_title == "Search 2"

    def test_the_functions_and_the_properties_agree(self):
        assert wants_banner(None, None, 2) is True
        assert wants_banner(None, None, 1) is False
        assert banner_title(None, 4) == "Search 4"
        assert banner_title("Mine", 4) == "Mine"

    def test_the_metadata_of_the_file_is_not_the_banner(self):
        """`metadata: name:` describes the document, `title:` one result."""
        spec = Spec.from_data(
            {
                "kind": "search",
                "metadata": {"name": "sprint-searches"},
                "searches": [{"search": {"tag": "x"}}],
            }
        )

        plan = plan_search(StubApp(), spec.items("search")[0])

        assert plan.title is None
        assert spec.metadata.name == "sprint-searches"


class TestConflictingStatusScopes:
    """The deprecated `all:` against a `status:` group that names a scope."""

    @pytest.mark.parametrize("status", ["open", "closed", "closed+in:Resolved"])
    def test_a_named_scope_conflicts(self, status):
        plan = plan_search(StubApp(), item({"all": True, "status": status}))

        assert plan.conflicting_status_scopes

    def test_both_scopes_are_listed_sorted(self):
        plan = plan_search(StubApp(), item({"all": True, "status": "open,closed"}))

        assert plan.conflicting_status_scopes == ("closed", "open")

    def test_any_does_not_conflict(self):
        plan = plan_search(StubApp(), item({"all": True, "status": "any"}))

        assert plan.conflicting_status_scopes == ()

    def test_without_all_there_is_nothing_to_conflict_with(self):
        plan = plan_search(StubApp(), item({"status": "open"}))

        assert plan.conflicting_status_scopes == ()

    def test_it_is_reported_and_not_raised(self):
        """A caller decides what a deprecated key colliding is worth."""
        plan = plan_search(StubApp(), item({"all": True, "status": "open"}))

        assert plan.params["include_closed"] is True


class TestUnsupportedKeys:
    """A key nothing reads is refused, never silently dropped."""

    def test_the_planner_refuses_it(self):
        with pytest.raises(PhabfiveDataException) as error:
            plan_search(StubApp(), item({"milestone": "Q3"}))

        assert "milestone" in str(error.value)

    def test_the_message_names_the_search(self):
        with pytest.raises(PhabfiveDataException) as error:
            plan_search(StubApp(), item({"milestone": "Q3"}), index=3)

        assert str(error.value).startswith("searches[2]:")

    def test_a_section_that_is_not_a_mapping_is_refused(self):
        with pytest.raises(PhabfiveDataException) as error:
            check_search_params(None, where="Document 1")

        assert "must be a dictionary" in str(error.value)

    def test_an_empty_section_is_fine(self):
        check_search_params({}, where="Document 1")


class TestObjectTypes:
    """Only task searches can run yet, and the others say so."""

    @pytest.mark.parametrize("object_type", ["project", "paste", "passphrase"])
    def test_a_type_with_no_runner_is_refused_by_name(self, object_type):
        with pytest.raises(SearchPlanError) as error:
            plan_search(StubApp(), {"type": object_type, "search": {}})

        assert object_type in str(error.value)
        assert error.value.check == "unsupported-type"

    def test_an_unknown_type_names_the_ones_that_exist(self):
        with pytest.raises(SearchPlanError) as error:
            plan_search(StubApp(), {"type": "sandwich", "search": {}})

        assert "task" in str(error.value)


class TestRunning:
    """The second step: one call, and the answer untranslated."""

    def test_the_params_are_the_keyword_arguments(self):
        app = StubApp(payload={"tasks": []})
        plan = plan_search(app, item({"tag": "infra", "limit": 5}))

        run_search(app, plan)

        method, kwargs = app.calls[-1]
        assert method == "task_search"
        assert kwargs == dict(plan.params)

    def test_the_payload_is_not_translated(self):
        payload = {"tasks": [{"id": 1}], "search_params": {"tag": "infra"}}
        app = StubApp(payload=payload)
        plan = plan_search(app, item({"tag": "infra"}))

        assert run_search(app, plan).payload is payload

    def test_the_result_carries_its_own_plan(self):
        app = StubApp(payload={"tasks": []})
        plan = plan_search(app, item({"tag": "infra"}))

        assert run_search(app, plan).plan is plan

    def test_a_tag_that_matched_nothing_is_an_empty_result_not_an_error(self):
        """`task_search` answers None when a project filter matched nothing.

        Surprising and load-bearing: the command prints nothing and exits 0,
        so a result has to tolerate it rather than treat it as a failure.
        """
        app = StubApp(payload=None)
        plan = plan_search(app, item({"tag": "nothing"}))

        result = run_search(app, plan)

        assert result.payload is None
        assert result.records == []


class TestRecords:
    """The uniform view over whatever an app answered with."""

    def test_a_task_payload(self):
        result = SearchResult(plan=SearchPlan(), payload={"tasks": [{"id": 1}]})

        assert result.records == [{"id": 1}]

    def test_a_payload_with_no_tasks_key(self):
        result = SearchResult(plan=SearchPlan(), payload={"search_params": {}})

        assert result.records == []

    def test_a_list_payload(self):
        result = SearchResult(plan=SearchPlan(), payload=[{"id": 1}, {"id": 2}])

        assert result.records == [{"id": 1}, {"id": 2}]

    def test_no_payload(self):
        assert SearchResult(plan=SearchPlan()).records == []


class StubSpec:
    """A spec-shaped object that answers with the problems it was given.

    `Spec` is frozen and `validate_online` reaches the real resolver table,
    so a stub is what lets the atomic report be tested without an instance.
    """

    def __init__(self, items, problems=()):
        self._items = list(items)
        self._problems = list(problems)
        self.source = "<stub>"
        self.validations = 0

    def items(self, object_type):
        assert object_type == "search"
        return list(self._items)

    def validate_online(self, app):
        self.validations += 1
        return list(self._problems)


class TestPlanningEverySearchAtOnce:
    """`plan_searches`: one validation pass, then every plan."""

    def test_every_item_is_planned_in_order(self):
        spec = StubSpec([item({"tag": "one"}), item({"tag": "two"})])

        plans = plan_searches(StubApp(), spec)

        assert [plan.params["tag"] for plan in plans] == ["one", "two"]

    def test_each_plan_knows_where_it_is(self):
        spec = StubSpec([item({"tag": "one"}), item({"tag": "two"})])

        plans = plan_searches(StubApp(), spec)

        assert [(plan.index, plan.total) for plan in plans] == [(1, 2), (2, 2)]

    def test_the_source_is_carried_for_messages(self):
        spec = StubSpec([item({"tag": "one"})])

        assert plan_searches(StubApp(), spec)[0].source == "<stub>"

    def test_overrides_apply_to_every_item(self):
        spec = StubSpec([item({"tag": "one"}), item({"tag": "two"})])

        plans = plan_searches(StubApp(), spec, overrides={"limit": 5})

        assert [plan.limit for plan in plans] == [5, 5]

    def test_the_instance_is_asked_once_however_many_searches(self):
        spec = StubSpec([item({"tag": "one"}), item({"tag": "two"})])

        plan_searches(StubApp(), spec)

        assert spec.validations == 1

    def test_every_unresolved_reference_is_named_in_one_error(self):
        """The acceptance: three missing projects say so once, before paging."""
        problems = [
            problem(
                f"searches[{index}]",
                field="tag",
                value=name,
                reason=f"{name!r} does not name anything on this instance",
                code="unknown-project",
                layer=Layer.ONLINE,
            )
            for index, name in enumerate(("#one", "#two", "#three"))
        ]
        spec = StubSpec([item({"tag": "#one"})], problems=problems)

        with pytest.raises(PhabfiveDataException) as error:
            plan_searches(StubApp(), spec)

        message = str(error.value)
        assert "#one" in message and "#two" in message and "#three" in message

    def test_nothing_is_planned_when_a_reference_failed(self):
        """A plan list with a hole in it is worse than an error."""
        spec = StubSpec(
            [item({"tag": "#one"})],
            problems=[
                problem("searches[0]", reason="no", code="unknown-project"),
            ],
        )

        with pytest.raises(PhabfiveDataException):
            plan_searches(StubApp(), spec)

    def test_a_warning_does_not_stop_the_search(self):
        """An icon outside the observed set is a report, not a refusal."""
        spec = StubSpec(
            [item({"tag": "infra"})],
            problems=[
                problem(
                    "searches[0]",
                    reason="maybe",
                    code="unknown-icon",
                    layer=Layer.ONLINE,
                    severity=Severity.WARNING,
                )
            ],
        )

        plans = plan_searches(StubApp(), spec)

        assert [plan.params["tag"] for plan in plans] == ["infra"]

    def test_validation_can_be_skipped(self):
        spec = StubSpec(
            [item({"tag": "#one"})],
            problems=[problem("searches[0]", reason="no", code="unknown-project")],
        )

        plans = plan_searches(StubApp(), spec, validate=False)

        assert spec.validations == 0
        assert len(plans) == 1

    def test_a_real_spec_is_accepted(self):
        """Against `Spec` itself, not only the stub."""
        spec = Spec.from_data(
            {"kind": "search", "searches": [{"search": {"tag": "infra"}}]}
        )

        plans = plan_searches(StubApp(), spec, validate=False)

        assert [plan.params["tag"] for plan in plans] == ["infra"]


class TestTheExceptionTypes:
    """A specific subclass, so the handlers that exist already answer."""

    def test_a_plan_error_is_an_input_exception(self):
        assert issubclass(SearchPlanError, PhabfiveInputException)

    def test_and_therefore_a_config_exception(self):
        """Which is what `maniphest search` already catches."""
        assert issubclass(SearchPlanError, PhabfiveConfigException)

    def test_and_therefore_a_value_error(self):
        assert issubclass(SearchPlanError, ValueError)


def _scrubbed_environment():
    """As little environment as an interpreter needs to start.

    The same scrubbing `tests/test_spec_isolation.py` documents: `HOME` and
    every Windows configuration variable are *defined* and point nowhere,
    because `phabricator/__init__.py` reads `ProgramData` and `AppData` as it
    imports and an absent one is a `KeyError` before anything is tested.
    """
    keep = ("PATH", "SYSTEMROOT", "LD_LIBRARY_PATH", "VIRTUAL_ENV")
    environment = {name: os.environ[name] for name in keep if name in os.environ}

    nowhere = str(Path(os.sep, "nonexistent"))

    environment["HOME"] = nowhere
    environment["USERPROFILE"] = nowhere
    environment["APPDATA"] = nowhere
    environment["LOCALAPPDATA"] = nowhere
    environment["ProgramData"] = nowhere
    environment["ALLUSERSPROFILE"] = nowhere
    environment["PYTHONPATH"] = str(REPOSITORY)
    environment["TERM"] = "dumb"

    assert "PHAB_URL" not in environment
    assert "PHAB_TOKEN" not in environment
    assert "TYPER_USE_RICH" not in environment

    return environment


SCRIPT = """
import json, os, sys

from phabfive.spec import Spec
from phabfive.spec.search import plan_searches, run_search


class App:
    def parse_status_patterns_with_api(self, value):
        from phabfive.transitions import parse_status_patterns

        return parse_status_patterns(value)

    def task_search(self, **kwargs):
        self.sent = kwargs
        return {"tasks": [{"id": 7, "fields": {"name": "Seven"}}]}


spec = Spec.from_data(
    {
        "kind": "search",
        "searches": [
            {"title": "Mine", "search": {"tag": "infra", "limit": 5}},
        ],
    }
)

app = App()
plans = plan_searches(app, spec, validate=False)
result = run_search(app, plans[0])

print(
    json.dumps(
        {
            "records": [task["id"] for task in result.records],
            "limit": plans[0].limit,
            "title": plans[0].banner_title,
            "sent_tag": app.sent["tag"],
            "forbidden": [
                name
                for name in ("phabfive.cli", "phabricator", "requests")
                if name in sys.modules
            ],
            "typer_use_rich": "TYPER_USE_RICH" in os.environ,
        }
    )
)
"""


class TestOutOfProcess:
    """A search spec runs from a dict with no command line in the process.

    In a fresh interpreter, because by the time this suite runs `phabfive.cli`
    is already in `sys.modules` and `TYPER_USE_RICH` is already set, so an
    in-process version of this would pass whatever the code imported.
    """

    @pytest.fixture(scope="class")
    def answer(self):
        result = subprocess.run(
            [sys.executable, "-c", SCRIPT],
            env=_scrubbed_environment(),
            cwd=str(REPOSITORY),
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert result.returncode == 0, (
            f"exit {result.returncode}\n--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
        )

        return json.loads(result.stdout)

    def test_the_search_ran(self, answer):
        assert answer["records"] == [7]
        assert answer["sent_tag"] == "infra"

    def test_the_spec_was_interpreted(self, answer):
        assert answer["limit"] == 5
        assert answer["title"] == "Mine"

    def test_no_command_line_was_imported(self, answer):
        assert answer["forbidden"] == []

    def test_the_process_environment_was_not_touched(self, answer):
        assert answer["typer_use_rich"] is False
