# -*- coding: utf-8 -*-

"""What exists after a create spec fails partway through (#485).

Conduit has no transactions and creation is one call per object, so a spec
creating seventy objects that is refused on the fiftieth has left forty-nine
real objects behind. `templates/task-create/mega-2024-simulation.yml` is
exactly that shape. Answering with the server's exception - which is what
the old recursion did - throws away the one thing the caller needs: which
of them exist.

Three properties are pinned here, and together they are the feature:

1. **Every object gets a record**, ``created``, ``failed`` or ``skipped``,
   and ``skipped`` is a different thing from ``failed``: nothing was sent
   for it, so nothing exists for it.
2. **The records are yielded as they happen**, so a long run reports
   progress and a caller interrupted halfway still holds the record of
   what was created. `CreateReport` is the convenience over the generator,
   and `CreateReport.raise_for_failure` carries the records across the one
   boundary that cannot yield - a caller that answers with a value.
3. **Nothing is retried that could double-create.** A create is never
   marked as an idempotent write, so `phabfive.retry` will repeat it only
   when it never reached the server at all.

`tests/test_spec_create.py` owns what a plan is and
`tests/test_spec_local_refs.py` the order it is applied in; what is here is
only what happens when one of those applications is refused.
"""

import json
from itertools import islice
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from phabfive.cli import app
from phabfive.core import Phabfive
from phabfive.exceptions import PhabfiveDataException
from phabfive.json_output import iter_records
from phabfive.maniphest.core import Maniphest
from phabfive.retry import Pacer, retries_writes
from phabfive.spec import Spec
from phabfive.spec.create import (
    CreateFailed,
    CreateRecord,
    CreateReport,
    apply_plan,
    apply_spec,
    plan_create,
)

runner = CliRunner()

REFUSED = "ERR-CONDUIT-CORE: the daemon refused that"


def _phab(refuse=None):
    """A client that creates projects and tasks, and refuses one by title.

    `refuse` is the title whose `maniphest.edit` raises, standing in for
    everything a server can answer a create with - a policy the caller
    cannot use, a custom field it rejected, the daemon being down.
    """
    phab = MagicMock()
    phab.project.query.return_value = {"data": {}}
    phab.user.whoami.return_value = {"phid": "PHID-USER-caller", "userName": "caller"}
    phab.user.search.return_value = {"data": []}

    # Nothing on the instance answers to any hashtag this spec asks for, so
    # `hashtag_conflicts` refuses nothing before the run starts
    phab.project.search.return_value = {"data": []}

    task_ids = iter(range(1, 100))
    project_ids = iter(range(100, 200))

    # What `retries_writes()` said while each create was being sent, which
    # is the honest way to ask whether a create was marked retryable: the
    # mark is a contextvar around the call, not a property of the call
    phab.marked_retryable = []

    def create_task(transactions):
        title = _title(transactions)
        phab.marked_retryable.append(retries_writes())

        if refuse is not None and title == refuse:
            raise RuntimeError(REFUSED)

        task_id = next(task_ids)

        return {"object": {"id": task_id, "phid": f"PHID-TASK-{task_id}"}}

    def create_project(transactions):
        phab.marked_retryable.append(retries_writes())
        project_id = next(project_ids)

        return {"object": {"id": project_id, "phid": f"PHID-PROJ-{project_id}"}}

    phab.maniphest.edit.side_effect = create_task
    phab.project.edit.side_effect = create_project

    return phab


def _title(transactions):
    """The title a create's transactions carry, for the fake server."""
    for transaction in transactions:
        if transaction.get("type") in ("title", "name"):
            return transaction.get("value")

    return None


def _maniphest(phab, conf=None):
    with patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None):
        maniphest = Maniphest()

    maniphest.phab = phab
    maniphest.url = "http://phorge.localhost"
    maniphest.conf = dict(conf or {})

    return maniphest


#: A project and four tasks, every one of them tagged into the project, so
#: the project is created first - `tests/test_spec_local_refs.py` owns why -
#: and the tasks follow in document order. The fourth object of the five is
#: "Wire it up", which is the one the fake server refuses.
SPEC = {
    "projects": [{"id": "platform", "name": "Platform", "slugs": ["platform"]}],
    "tasks": [
        {"title": "Sprint kickoff", "id": "kickoff", "projects": ["$platform"]},
        {"title": "Bootstrap", "projects": ["$platform"]},
        {"title": "Wire it up", "projects": ["$platform"]},
        {"title": "Follow-up", "projects": ["$platform"]},
    ],
}

FOURTH = "Wire it up"


def _plan(phab, body=None):
    return plan_create(
        _maniphest(phab), Spec.from_data(body or SPEC, kind="create").render()
    )


def _run(refuse=FOURTH, conf=None):
    """Apply the spec against a server that refuses one object."""
    phab = _phab(refuse=refuse)
    plan = _plan(phab)

    return phab, apply_spec(_maniphest(phab, conf), plan)


class TestTheReportOfAPartialRun:
    """Three created, one failed, the rest skipped - the issue's acceptance."""

    def test_every_object_is_accounted_for(self):
        _, report = _run()

        assert [
            (record.object_type, record.title, record.status)
            for record in report.records
        ] == [
            ("project", "Platform", "created"),
            ("task", "Sprint kickoff", "created"),
            ("task", "Bootstrap", "created"),
            ("task", FOURTH, "failed"),
            ("task", "Follow-up", "skipped"),
        ]

    def test_the_failure_carries_what_the_server_said(self):
        _, report = _run()

        assert report.failures[0].reason == REFUSED
        assert isinstance(report.failures[0].error, RuntimeError)
        assert report.ok is False

    def test_nothing_after_the_failure_is_sent(self):
        """ "Skipped" means nothing exists for it, so nothing may be sent."""
        phab, report = _run()

        titles = [
            _title(call.kwargs["transactions"])
            for call in phab.maniphest.edit.call_args_list
        ]

        assert titles == ["Sprint kickoff", "Bootstrap", FOURTH]
        assert report.skipped[0].title == "Follow-up"

    def test_a_created_object_says_what_it_became(self):
        """So it can be cleaned up by monogram rather than by searching."""
        _, report = _run()
        project, kickoff, _bootstrap = report.created

        assert project.monogram == "#platform"
        assert project.phid == "PHID-PROJ-100"
        assert kickoff.monogram == "T1"
        assert kickoff.id == 1

    def test_nothing_that_was_not_created_claims_a_phid(self):
        _, report = _run()

        assert all(record.phid is None for record in report.failures + report.skipped)
        assert all(record.id is None for record in report.failures + report.skipped)

    def test_a_clean_run_reports_every_object_as_created(self):
        _, report = _run(refuse=None)

        assert [record.status for record in report.records] == ["created"] * 5
        assert report.ok is True
        assert report.task_ids == [1, 2, 3, 4]


class TestTheRecordsAreMachineReadable:
    """The report is data a program reads, not a sentence it greps."""

    def test_the_records_are_the_keys_the_issue_names(self):
        _, report = _run()

        assert report.as_records()[0] == {
            "local_id": "platform",
            "type": "project",
            "path": "projects[0]",
            "status": "created",
            "phid": "PHID-PROJ-100",
            "id": 100,
            "monogram": "#platform",
            "title": "Platform",
            "reason": None,
        }

    def test_an_object_with_no_local_id_says_so_rather_than_omitting_it(self):
        """Every record has every key, so a jsonl reader needs no defaults."""
        _, report = _run()
        failed = report.as_records()[3]

        assert failed["local_id"] is None
        assert failed["type"] == "task"
        assert failed["title"] == FOURTH
        assert failed["status"] == "failed"
        assert failed["reason"] == REFUSED

    def test_they_survive_the_json_emitters(self):
        """The same records, whichever of the two JSON formats asked."""
        _, report = _run()
        records = report.as_records()

        one_array = json.loads("".join(iter_records(records, "json")))
        one_per_line = [json.loads(line) for line in iter_records(records, "jsonl")]

        assert one_array == records
        assert one_per_line == records

    def test_a_local_id_is_what_names_an_object_a_later_run_would_recognise(self):
        _, report = _run()

        assert [record.local_id for record in report.records] == [
            "platform",
            "kickoff",
            None,
            None,
            None,
        ]


class TestItYieldsAsItGoes:
    """A long run reports progress, and an interrupted one keeps its records."""

    def test_a_record_arrives_before_the_next_object_is_created(self):
        phab = _phab(refuse=None)
        plan = _plan(phab)

        records = apply_plan(_maniphest(phab), plan)

        first = next(records)

        assert first.object_type == "project"
        # The project was created and nothing else was, so the caller holds
        # the record of it before the first task is even attempted
        assert phab.maniphest.edit.call_count == 0

        second = next(records)

        assert second.title == "Sprint kickoff"
        assert phab.maniphest.edit.call_count == 1

    def test_abandoning_the_generator_creates_nothing_further(self):
        """What an interrupted process leaves behind is what it had yielded."""
        phab = _phab(refuse=None)
        plan = _plan(phab)

        held = list(islice(apply_plan(_maniphest(phab), plan), 2))

        assert [record.title for record in held] == ["Platform", "Sprint kickoff"]
        assert phab.maniphest.edit.call_count == 1

    def test_the_failure_does_not_come_out_as_an_exception(self):
        """A generator that raised would lose exactly these records."""
        phab = _phab(refuse=FOURTH)
        plan = _plan(phab)

        records = list(apply_plan(_maniphest(phab), plan))

        assert [record.status for record in records][-2:] == ["failed", "skipped"]


class TestNothingIsRetriedThatCouldDoubleCreate:
    def test_a_create_is_never_marked_as_an_idempotent_write(self):
        """`phabfive.retry` repeats a write only when it was marked, and a
        create repeated after a read timeout is a second object."""
        phab, _ = _run(refuse=None)

        assert phab.marked_retryable == [False] * 5

    def test_the_refused_create_is_attempted_once(self):
        phab, _ = _run()

        sent = [
            call
            for call in phab.maniphest.edit.call_args_list
            if _title(call.kwargs["transactions"]) == FOURTH
        ]

        assert len(sent) == 1


class TestThePace:
    """PHAB_PACE spaces out a spec apply, as it does every other batch."""

    def test_it_waits_between_writes(self):
        waits = []

        with patch("phabfive.retry._sleep", side_effect=waits.append):
            _run(refuse=None, conf={"PHAB_PACE": 0.5})

        # Five objects, and the first never waits
        assert len(waits) == 4
        assert all(0 < wait <= 0.5 for wait in waits)

    def test_an_unpaced_run_never_sleeps(self):
        waits = []

        with patch("phabfive.retry._sleep", side_effect=waits.append):
            _run(refuse=None)

        assert waits == []

    def test_a_caller_may_hand_in_its_own_pacer(self):
        phab = _phab(refuse=None)
        plan = _plan(phab)
        pace = MagicMock(wraps=Pacer(0))

        list(apply_plan(_maniphest(phab), plan, pace=pace))

        assert pace.wait.call_count == 5


class TestRaisingKeepsTheRecords:
    """The one boundary that cannot yield: a caller answering with a value."""

    def test_a_partial_run_raises_with_the_report_attached(self):
        _, report = _run()

        with pytest.raises(CreateFailed) as excinfo:
            report.raise_for_failure()

        assert excinfo.value.report is report
        assert [record.status for record in excinfo.value.report.records] == [
            "created",
            "created",
            "created",
            "failed",
            "skipped",
        ]

    def test_the_server_refusal_stays_the_cause(self):
        _, report = _run()

        with pytest.raises(CreateFailed) as excinfo:
            report.raise_for_failure()

        assert isinstance(excinfo.value.__cause__, RuntimeError)
        assert REFUSED in str(excinfo.value.__cause__)

    def test_a_clean_run_hands_the_report_back(self):
        _, report = _run(refuse=None)

        assert report.raise_for_failure() is report

    def test_it_is_a_data_exception_so_every_existing_handler_answers_it(self):
        assert issubclass(CreateFailed, PhabfiveDataException)

    def test_the_sentence_says_how_much_exists(self):
        _, report = _run()

        assert str(CreateFailed(report)) == (
            f"3 of 5 objects created. task {FOURTH!r} failed: {REFUSED}. "
            "1 object not attempted."
        )

    def test_a_named_object_is_named_by_its_local_id(self):
        """Which is what a resumed run would recognise it by."""
        record = CreateRecord(
            object_type="project",
            path="projects[0]",
            status="failed",
            local_id="platform",
            title="Platform",
        )

        assert record.label == "project $platform 'Platform'"

    def test_an_unnamed_object_falls_back_to_where_it_was_written(self):
        record = CreateRecord(object_type="task", path="tasks[3]", status="skipped")

        assert record.label == "task tasks[3]"

    def test_a_clean_report_summarises_without_a_failure(self):
        assert CreateReport().summary == "0 of 0 objects created."


@pytest.fixture
def restore_output_format():
    """Put the class-level output format back, even when a test fails."""
    original = Phabfive._output_format
    try:
        yield
    finally:
        Phabfive._output_format = original


def _cli_maniphest(report):
    """A Maniphest whose template create failed partway through."""
    instance = MagicMock()
    instance.create_tasks_from_yaml.side_effect = CreateFailed(report)

    return instance


def _invoke(tmp_path, report, *options):
    template = tmp_path / "tasks.yaml"
    template.write_text("tasks:\n")

    with patch(
        "phabfive.cli.maniphest._get_maniphest_app",
        return_value=_cli_maniphest(report),
    ):
        return runner.invoke(
            app,
            [*options, "maniphest", "create", "--with", str(template)],
        )


class TestTheCommandReportsIt:
    """`maniphest create --with`, which is where a person meets this."""

    def test_a_machine_format_puts_the_records_on_stdout(
        self, tmp_path, restore_output_format
    ):
        _, report = _run()

        result = _invoke(tmp_path, report, "--format=json")

        assert [record["status"] for record in json.loads(result.stdout)] == [
            "created",
            "created",
            "created",
            "failed",
            "skipped",
        ]

    def test_the_sentence_about_them_goes_to_stderr(
        self, tmp_path, restore_output_format
    ):
        _, report = _run()

        result = _invoke(tmp_path, report, "--format=json")

        assert "3 of 5 objects created" in result.stderr
        assert result.exit_code == 1

    def test_jsonl_writes_one_record_per_line(self, tmp_path, restore_output_format):
        _, report = _run()

        result = _invoke(tmp_path, report, "--format=jsonl")
        lines = [
            json.loads(line) for line in result.stdout.splitlines() if line.strip()
        ]

        assert len(lines) == 5
        assert lines[3]["reason"] == REFUSED

    def test_a_human_format_lists_what_exists_on_stderr(
        self, tmp_path, restore_output_format
    ):
        _, report = _run()

        result = _invoke(tmp_path, report, "--format=rich")

        assert result.stdout.strip() == ""
        assert "created  project $platform 'Platform' #platform" in result.stderr
        assert f"failed   task {FOURTH!r}: {REFUSED}" in result.stderr
        assert "skipped  task 'Follow-up'" in result.stderr
        assert result.exit_code == 1

    def test_yaml_writes_the_records_as_yaml(self, tmp_path, restore_output_format):
        """The third machine shape, and the one nothing covered.

        `--format=yaml` is a machine format like `json` and `jsonl`, so it
        writes the records to stdout and the sentence to stderr, and a
        reader loads it back into the same five records.
        """
        from io import StringIO

        from ruamel.yaml import YAML

        _, report = _run()

        result = _invoke(tmp_path, report, "--format=yaml")
        records = YAML(typ="safe").load(StringIO(result.stdout))

        assert [one["status"] for one in records] == [
            "created",
            "created",
            "created",
            "failed",
            "skipped",
        ]
        assert records[3]["reason"] == REFUSED
        assert "3 of 5 objects created" in result.stderr
        assert result.exit_code == 1


class TestAnAnchorAfterAFailure:
    """A run that stopped reads nothing more, not even an anchor.

    An anchor is never written, so it looked safe to handle before the
    "something already failed" guard. It is not: an anchor whose `parent:`
    is a `$local-id` whose object failed reaches `_realise`, which raises
    `CreatePlanError` straight out of the generator - and `apply_spec`'s
    `tuple(...)` then loses every record there was, including the objects
    that really were created. That is precisely the loss this issue exists
    to prevent, and the message ("names an object this run has not created")
    blames the planner for the server's refusal.
    """

    BODY = {
        "tasks": [
            {"title": "Done first"},
            {"id": "epic", "title": "Epic"},
            {"parent": "$epic", "tasks": [{"title": "Child"}]},
        ],
    }

    def test_the_records_survive_a_failed_anchor_target(self):
        phab = _phab(refuse="Epic")
        report = apply_spec(_maniphest(phab), _plan(phab, self.BODY))

        assert [(one.title, one.status) for one in report.records] == [
            ("Done first", "created"),
            ("Epic", "failed"),
            ("Child", "skipped"),
        ]

    def test_what_was_created_before_it_is_still_named(self):
        """T1 exists on the instance; the report has to say so."""
        phab = _phab(refuse="Epic")
        report = apply_spec(_maniphest(phab), _plan(phab, self.BODY))

        assert report.task_ids == [1]
        assert report.created[0].monogram == "T1"

    def test_nothing_is_sent_for_the_children_of_a_failed_anchor(self):
        phab = _phab(refuse="Epic")
        apply_spec(_maniphest(phab), _plan(phab, self.BODY))

        assert [_title(call.kwargs["transactions"]) for call in _edits(phab)] == [
            "Done first",
            "Epic",
        ]


def _edits(phab):
    return phab.maniphest.edit.call_args_list


class TestTheShippingPathProducesIt:
    """`CreateFailed` from the one path a person reaches, not only from a mock.

    The tests above stand `create_tasks_from_yaml` in with a mock whose
    `side_effect` is a `CreateFailed`, which proves the formatter and
    nothing about the wiring. These drive the real
    `Maniphest.create_tasks_from_config` over a client that refuses the
    fourth object - if it went back to raising what the server said, the
    `except CreateFailed` branch in the command would be dead code and
    these would be the tests that noticed (#485).
    """

    def test_create_tasks_from_config_raises_create_failed(self):
        phab = _phab(refuse=FOURTH)

        with pytest.raises(CreateFailed) as excinfo:
            _maniphest(phab).create_tasks_from_config(dict(SPEC, kind="create"))

        report = excinfo.value.report

        assert [one.status for one in report.records] == [
            "created",
            "created",
            "created",
            "failed",
            "skipped",
        ]
        assert isinstance(excinfo.value.__cause__, RuntimeError)

    def test_the_task_ids_it_returns_leave_the_project_out(self):
        """`{"task_ids": ...}` is handed to `task_show`, so it holds tasks.

        A spec may create a project too, and a project's id looked up as a
        task is a task that does not exist. The old loop collected every
        created object's id.
        """
        phab = _phab()

        answer = _maniphest(phab).create_tasks_from_config(dict(SPEC, kind="create"))

        # 1..4 are the four tasks; the project was created as 100
        assert answer == {"task_ids": [1, 2, 3, 4]}

    def test_the_command_reports_the_partial_run_end_to_end(
        self, tmp_path, restore_output_format
    ):
        """No mocked entry point: a real template, a real `Maniphest`."""
        import ruamel.yaml

        template = tmp_path / "sprint.yaml"
        stream = ruamel.yaml.YAML()
        with open(template, "w") as handle:
            stream.dump(dict(SPEC, kind="create"), handle)

        app_under_test = _maniphest(_phab(refuse=FOURTH))

        with patch(
            "phabfive.cli.maniphest._get_maniphest_app",
            return_value=app_under_test,
        ):
            result = runner.invoke(
                app,
                [
                    "--format=json",
                    "maniphest",
                    "create",
                    "--with",
                    str(template),
                ],
            )

        assert result.exit_code == 1
        assert [one["status"] for one in json.loads(result.stdout)] == [
            "created",
            "created",
            "created",
            "failed",
            "skipped",
        ]
        assert "3 of 5 objects created" in result.stderr
