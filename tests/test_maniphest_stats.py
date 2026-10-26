# -*- coding: utf-8 -*-
"""`maniphest stats`: tasks created and closed per period (#598)."""

import datetime
import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from phabfive.cli.maniphest import _STATS_GROUPINGS, _STATS_PERIODS, maniphest_app
from phabfive.exceptions import PhabfiveInputException
from phabfive.maniphest import Maniphest
from phabfive.maniphest.core import TaskMatches
from phabfive.maniphest.stats import (
    GROUPINGS,
    NO_GROUP,
    PERIODS,
    build_stats,
    percentile,
    period_of,
    periods_between,
)

runner = CliRunner()

DAY = 86400


def at(*args):
    """A naive local datetime."""
    return datetime.datetime(*args)


def epoch(*args):
    return int(at(*args).timestamp())


def task(
    phid,
    created=None,
    closed=None,
    status="open",
    points=None,
    owner=None,
    closer=None,
    projects=(),
    subtype="default",
):
    return {
        "id": int(phid.rsplit("-", 1)[-1]) if phid[-1].isdigit() else 0,
        "phid": phid,
        "fields": {
            "dateCreated": created,
            "dateClosed": closed,
            "status": {"value": status},
            "points": points,
            "ownerPHID": owner,
            "closerPHID": closer,
            "subtype": subtype,
        },
        "attachments": {"projects": {"projectPHIDs": list(projects)}},
    }


class TestPeriods:
    @pytest.mark.parametrize(
        "moment, period, label, start, end",
        [
            # A Wednesday, in ISO week 27, which starts on Monday 29 June
            (at(2026, 7, 1, 9), "week", "2026-W27", at(2026, 6, 29), at(2026, 7, 6)),
            (at(2026, 7, 1), "month", "2026-07", at(2026, 7, 1), at(2026, 8, 1)),
            (at(2026, 12, 31), "month", "2026-12", at(2026, 12, 1), at(2027, 1, 1)),
            (at(2026, 2, 14), "quarter", "2026-Q1", at(2026, 1, 1), at(2026, 4, 1)),
            (at(2026, 11, 3), "quarter", "2026-Q4", at(2026, 10, 1), at(2027, 1, 1)),
        ],
    )
    def test_a_moment_falls_in_one_calendar_period(
        self, moment, period, label, start, end
    ):
        assert period_of(moment, period) == (label, start, end)

    @pytest.mark.parametrize(
        "moment, label",
        [
            # ISO weeks belong to the year their Thursday is in
            (at(2024, 12, 30), "2025-W01"),
            (at(2027, 1, 1), "2026-W53"),
            (at(2026, 12, 31), "2026-W53"),
            (at(2027, 1, 4), "2027-W01"),
        ],
    )
    def test_iso_weeks_cross_the_new_year(self, moment, label):
        assert period_of(moment, "week")[0] == label

    def test_a_sunday_is_the_last_day_of_its_week(self):
        assert period_of(at(2026, 7, 5, 23, 59), "week")[0] == "2026-W27"
        assert period_of(at(2026, 7, 6), "week")[0] == "2026-W28"

    def test_every_period_of_the_range_is_listed_and_the_ends_are_clipped(self):
        spans = periods_between(at(2026, 7, 1), at(2026, 7, 15), "week")

        assert spans == [
            ("2026-W27", at(2026, 7, 1), at(2026, 7, 6)),
            ("2026-W28", at(2026, 7, 6), at(2026, 7, 13)),
            ("2026-W29", at(2026, 7, 13), at(2026, 7, 15)),
        ]

    def test_an_empty_range_has_no_periods(self):
        assert periods_between(at(2026, 7, 1), at(2026, 7, 1), "month") == []

    def test_a_period_that_is_not_one_is_refused(self):
        with pytest.raises(PhabfiveInputException, match="week, month, quarter"):
            period_of(at(2026, 7, 1), "fortnight")


class TestPercentile:
    @pytest.mark.parametrize(
        "values, fraction, expected",
        [
            ([1, 2, 3, 4], 0.5, 2.5),
            ([4, 1, 3, 2], 0.5, 2.5),
            ([1, 2, 3], 0.5, 2.0),
            ([7, 42], 0.85, 36.75),
            ([5], 0.85, 5.0),
            (list(range(1, 21)), 0.85, 17.15),
        ],
    )
    def test_it_interpolates_between_closest_ranks(self, values, fraction, expected):
        assert percentile(values, fraction) == pytest.approx(expected)

    def test_no_values_have_no_percentile(self):
        assert percentile([], 0.5) is None

    def test_one_very_old_task_does_not_drag_the_median(self):
        assert percentile([1, 1, 2, 2, 900], 0.5) == 2.0


SINCE = at(2026, 7, 1)
UNTIL = at(2026, 8, 1)


def stats(created=(), closed=(), **kwargs):
    kwargs.setdefault("since", SINCE)
    kwargs.setdefault("until", UNTIL)
    kwargs.setdefault("period", "month")
    kwargs.setdefault("closed_statuses", ["resolved"])
    return build_stats(list(created), list(closed), **kwargs)


class TestCounting:
    def test_created_and_closed_are_bucketed_by_their_own_dates(self):
        records = stats(
            created=[task("T-1", created=epoch(2026, 7, 2))],
            closed=[
                task(
                    "T-2",
                    created=epoch(2026, 6, 1),
                    closed=epoch(2026, 7, 3),
                    status="resolved",
                )
            ],
        )

        assert records == [
            {
                "Period": "2026-07",
                "Start": "2026-07-01",
                "End": "2026-07-31",
                "Created": 1,
                "Closed": 1,
                "Net": 0,
                "MedianDaysToClose": 32.0,
                "P85DaysToClose": 32.0,
            }
        ]

    def test_a_task_in_several_matched_projects_counts_once(self):
        one = task("T-1", created=epoch(2026, 7, 2))

        records = stats(created=[one, dict(one), one])

        assert records[0]["Created"] == 1

    def test_only_the_counted_statuses_are_closed(self):
        closed = [
            task("T-1", epoch(2026, 7, 1), epoch(2026, 7, 2), status="resolved"),
            task("T-2", epoch(2026, 7, 1), epoch(2026, 7, 3), status="wontfix"),
            task("T-3", epoch(2026, 7, 1), epoch(2026, 7, 4), status="invalid"),
        ]

        assert stats(closed=closed)[0]["Closed"] == 1
        assert stats(closed=closed, closed_statuses=None)[0]["Closed"] == 3
        assert (
            stats(closed=closed, closed_statuses=["resolved", "wontfix"])[0]["Closed"]
            == 2
        )

    def test_net_is_created_minus_closed_as_counted(self):
        records = stats(
            created=[task(f"T-{n}", created=epoch(2026, 7, 2)) for n in range(3)],
            closed=[
                task("T-9", epoch(2026, 7, 1), epoch(2026, 7, 2), status="resolved"),
                task("T-8", epoch(2026, 7, 1), epoch(2026, 7, 2), status="wontfix"),
            ],
        )

        assert records[0]["Net"] == 2

    def test_what_falls_outside_the_range_is_ignored(self):
        """The range is half-open: since counts, until does not."""
        records = stats(
            created=[
                task("T-1", created=epoch(2026, 6, 30, 23, 59)),
                task("T-2", created=epoch(2026, 7, 1)),
                task("T-3", created=epoch(2026, 8, 1)),
            ]
        )

        assert records[0]["Created"] == 1

    def test_every_period_has_a_record_even_an_empty_one(self):
        records = stats(
            created=[task("T-1", created=epoch(2026, 7, 2))],
            since=at(2026, 7, 1),
            until=at(2026, 10, 1),
        )

        assert [(r["Period"], r["Created"]) for r in records] == [
            ("2026-07", 1),
            ("2026-08", 0),
            ("2026-09", 0),
        ]
        assert records[1]["MedianDaysToClose"] is None

    def test_time_to_close_is_the_median_and_85th_percentile_in_days(self):
        closed = [
            task(
                f"T-{days}",
                created=epoch(2026, 7, 31) - days * DAY,
                closed=epoch(2026, 7, 31),
                status="resolved",
            )
            for days in (1, 2, 3, 4, 100)
        ]

        record = stats(closed=closed)[0]

        assert record["MedianDaysToClose"] == 3.0
        assert record["P85DaysToClose"] == pytest.approx(4 + 0.4 * 96)

    def test_a_reopened_task_counts_in_the_period_of_its_last_close(self):
        """dateClosed is the most recent close; the first one is gone."""
        reclosed = task(
            "T-1", epoch(2026, 7, 1), closed=epoch(2026, 8, 20), status="resolved"
        )

        records = stats(closed=[reclosed], until=at(2026, 9, 1))

        assert [(r["Period"], r["Closed"]) for r in records] == [
            ("2026-07", 0),
            ("2026-08", 1),
        ]

    def test_a_record_dates_its_period_by_its_first_and_last_day(self):
        records = stats(since=at(2026, 7, 1), until=at(2026, 7, 15), period="week")

        assert [(r["Start"], r["End"]) for r in records] == [
            ("2026-07-01", "2026-07-05"),
            ("2026-07-06", "2026-07-12"),
            ("2026-07-13", "2026-07-14"),
        ]


class TestPoints:
    def test_points_are_left_out_where_no_task_has_any(self):
        record = stats(created=[task("T-1", created=epoch(2026, 7, 2))])[0]

        assert "PointsAdded" not in record
        assert "PointsCompleted" not in record

    def test_points_added_and_completed_are_summed(self):
        records = stats(
            created=[
                task("T-1", created=epoch(2026, 7, 2), points="3"),
                task("T-2", created=epoch(2026, 7, 2), points=2.5),
                task("T-3", created=epoch(2026, 7, 2)),
            ],
            closed=[
                task("T-4", epoch(2026, 7, 1), epoch(2026, 7, 2), "resolved", 5),
                task("T-5", epoch(2026, 7, 1), epoch(2026, 7, 2), "wontfix", 8),
            ],
        )

        assert records[0]["PointsAdded"] == 5.5
        assert records[0]["PointsCompleted"] == 5.0

    def test_an_empty_period_reports_zero_points_once_points_exist(self):
        records = stats(
            created=[task("T-1", created=epoch(2026, 7, 2), points=1)],
            until=at(2026, 9, 1),
        )

        assert records[1]["PointsAdded"] == 0.0


class TestGroups:
    def test_by_owner_names_each_owner_and_keeps_the_unowned(self):
        names = {"PHID-USER-a": "alice", "PHID-USER-b": "bob"}
        records = stats(
            created=[
                task("T-1", created=epoch(2026, 7, 2), owner="PHID-USER-b"),
                task("T-2", created=epoch(2026, 7, 2), owner="PHID-USER-a"),
                task("T-3", created=epoch(2026, 7, 2)),
            ],
            by="owner",
            names=names,
        )

        assert [(r["Owner"], r["Created"]) for r in records] == [
            ("alice", 1),
            ("bob", 1),
            (NO_GROUP, 1),
        ]
        assert list(records[0])[:2] == ["Period", "Owner"]

    def test_a_task_in_two_projects_counts_in_each_and_once_in_each(self):
        both = task(
            "T-1",
            created=epoch(2026, 7, 2),
            projects=["PHID-PROJ-a", "PHID-PROJ-b"],
        )
        records = stats(
            created=[both, both],
            by="project",
            names={"PHID-PROJ-a": "Alpha", "PHID-PROJ-b": "Beta"},
        )

        assert [(r["Project"], r["Created"]) for r in records] == [
            ("Alpha", 1),
            ("Beta", 1),
        ]

    def test_every_group_seen_has_a_record_in_every_period(self):
        records = stats(
            created=[
                task("T-1", created=epoch(2026, 7, 2), subtype="bug"),
                task("T-2", created=epoch(2026, 8, 2), subtype="feature"),
            ],
            until=at(2026, 9, 1),
            by="subtype",
        )

        assert [(r["Period"], r["Subtype"], r["Created"]) for r in records] == [
            ("2026-07", "bug", 1),
            ("2026-07", "feature", 0),
            ("2026-08", "bug", 0),
            ("2026-08", "feature", 1),
        ]

    def test_by_closer_groups_the_closes_by_who_closed_them(self):
        records = stats(
            closed=[
                task(
                    "T-1",
                    epoch(2026, 7, 1),
                    epoch(2026, 7, 2),
                    "resolved",
                    closer="PHID-USER-a",
                )
            ],
            by="closer",
            names={"PHID-USER-a": "alice"},
        )

        assert records == [
            {
                "Period": "2026-07",
                "Closer": "alice",
                "Start": "2026-07-01",
                "End": "2026-07-31",
                "Created": 0,
                "Closed": 1,
                "Net": -1,
                "MedianDaysToClose": 1.0,
                "P85DaysToClose": 1.0,
            }
        ]

    def test_a_grouping_that_is_not_one_is_refused(self):
        with pytest.raises(PhabfiveInputException, match="owner, project"):
            stats(by="colour")


def _app(created=(), closed=(), status_map=None):
    """A Maniphest whose two searches answer with these items."""
    with patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None):
        maniphest = Maniphest()
    maniphest.phab = MagicMock()
    maniphest.phab.phid.query.return_value = {
        "PHID-USER-a": {"name": "alice"},
    }
    maniphest._get_api_status_map = MagicMock(
        return_value=status_map
        or {
            "defaultClosedStatus": "resolved",
            "closedStatuses": {"1": "resolved", "2": "wontfix"},
        }
    )

    def match(**kwargs):
        if kwargs.get("created_after") is not None:
            return TaskMatches(tasks=list(created))
        return TaskMatches(tasks=list(closed))

    maniphest._match_tasks = MagicMock(side_effect=match)
    return maniphest


class TestTaskStats:
    def test_two_searches_cover_the_whole_range(self):
        maniphest = _app()

        maniphest.task_stats(
            since="2026-07-01", until="2026-10-01", period="week", tag="Dev"
        )

        calls = [call.kwargs for call in maniphest._match_tasks.call_args_list]
        assert len(calls) == 2
        assert calls[0]["created_after"] == at(2026, 7, 1)
        assert calls[0]["created_before"] == at(2026, 10, 1)
        assert "closed_after" not in calls[0]
        assert calls[1]["closed_after"] == at(2026, 7, 1)
        assert calls[1]["closed_before"] == at(2026, 10, 1)
        for kwargs in calls:
            assert kwargs["tag"] == "Dev"
            # A task created in the range counts whether it is open or not
            assert kwargs["include_closed"] is True

    def test_the_default_closed_status_is_the_only_one_counted(self):
        closed = [
            task("T-1", epoch(2026, 7, 1), epoch(2026, 7, 2), "resolved"),
            task("T-2", epoch(2026, 7, 1), epoch(2026, 7, 2), "wontfix"),
        ]

        default = _app(closed=closed).task_stats(
            since="2026-07-01", until="2026-08-01", period="month"
        )
        every = _app(closed=closed).task_stats(
            since="2026-07-01", until="2026-08-01", period="month", all_closed=True
        )

        assert default[0]["Closed"] == 1
        assert every[0]["Closed"] == 2

    def test_an_instance_with_its_own_resolved_status_is_asked(self):
        closed = [task("T-1", epoch(2026, 7, 1), epoch(2026, 7, 2), "done")]
        maniphest = _app(closed=closed, status_map={"defaultClosedStatus": "done"})

        records = maniphest.task_stats(
            since="2026-07-01", until="2026-08-01", period="month"
        )

        assert records[0]["Closed"] == 1

    def test_by_owner_names_the_owners_in_one_lookup(self):
        created = [
            task("T-1", created=epoch(2026, 7, 2), owner="PHID-USER-a"),
            task("T-2", created=epoch(2026, 7, 2), owner="PHID-USER-a"),
        ]
        maniphest = _app(created=created)

        records = maniphest.task_stats(
            since="2026-07-01", until="2026-08-01", period="month", by="owner"
        )

        maniphest.phab.phid.query.assert_called_once_with(phids=["PHID-USER-a"])
        assert records[0]["Owner"] == "alice"
        assert records[0]["Created"] == 2

    def test_no_lookup_without_a_named_grouping(self):
        maniphest = _app(created=[task("T-1", created=epoch(2026, 7, 2))])

        maniphest.task_stats(since="2026-07-01", until="2026-08-01", by="subtype")

        maniphest.phab.phid.query.assert_not_called()

    @pytest.mark.parametrize(
        "kwargs, message",
        [
            ({"since": "2026-07-01", "period": "day"}, "Invalid period"),
            ({"since": "2026-07-01", "by": "colour"}, "Invalid grouping"),
            ({"since": None}, "since"),
            ({"since": "2026-02-30"}, "^since: Invalid date"),
            ({"since": "2026-07-01", "until": "nope"}, "^until: "),
            ({"since": "2026-07-01", "until": "2026-06-01"}, "is not after since"),
        ],
    )
    def test_a_bad_range_is_refused_before_anything_is_fetched(self, kwargs, message):
        maniphest = _app()

        with pytest.raises(PhabfiveInputException, match=message):
            maniphest.task_stats(**kwargs)

        maniphest._match_tasks.assert_not_called()


class TestTheSpecItem:
    def test_a_task_stats_item_is_planned_into_task_stats_arguments(self):
        from phabfive.search import plan_item

        app = MagicMock()
        app.parse_status_patterns_with_api.side_effect = lambda raw: ["parsed", raw]

        plan = plan_item(
            app,
            {
                "type": "task-stats",
                "search": {
                    "tag": "Development",
                    "since": "2026-07-01",
                    "by": "owner",
                    "all-closed": True,
                    "exclude": "T5,T6",
                    "status": "closed",
                },
            },
        )

        assert plan.object_type == "task-stats"
        assert plan.params["tag"] == "Development"
        assert plan.params["since"] == "2026-07-01"
        assert plan.params["until"] is None
        assert plan.params["period"] == "week"
        assert plan.params["by"] == "owner"
        assert plan.params["all_closed"] is True
        assert plan.params["exclude_task_ids"] == [5, 6]
        assert plan.params["status_patterns"] == ["parsed", "closed"]

    @pytest.mark.parametrize("key", ["limit", "order", "include", "created-after"])
    def test_a_key_a_count_has_no_use_for_is_refused(self, key):
        from phabfive.exceptions import PhabfiveDataException
        from phabfive.search import plan_item

        with pytest.raises(PhabfiveDataException, match="Unsupported"):
            plan_item(
                MagicMock(),
                {"type": "task-stats", "search": {"since": "1w", key: "x"}},
            )

    def test_a_count_with_no_range_has_no_criteria(self):
        from phabfive.search import has_criteria, plan_item

        plan = plan_item(MagicMock(), {"type": "task-stats", "search": {}})

        assert not has_criteria(plan)

    def test_the_offline_validator_knows_the_keys(self):
        from phabfive.spec.loader import parse_spec
        from phabfive.spec.validate import validate_offline

        good = parse_spec(
            "kind: search\nsearches:\n  - type: task-stats\n    search:\n"
            "      since: 2026-07-01\n      period: month\n      by: owner\n",
            format="yaml",
        )
        bad = parse_spec(
            "kind: search\nsearches:\n  - type: task-stats\n    search:\n"
            "      since: 2026-07-01\n      period: fortnight\n      limit: 5\n",
            format="yaml",
        )

        assert validate_offline(good) == []
        assert sorted(one.field for one in validate_offline(bad)) == [
            "search.limit",
            "search.period",
        ]


class TestTheCommand:
    def test_the_flags_reach_task_stats(self):
        maniphest = MagicMock()
        maniphest.task_stats.return_value = [
            {"Period": "2026-W27", "Created": 1, "Closed": 0, "Net": 1}
        ]

        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=maniphest):
            result = runner.invoke(
                maniphest_app,
                [
                    "stats",
                    "--since",
                    "2026-07-01",
                    "--until",
                    "2026-08-01",
                    "--period",
                    "month",
                    "--by",
                    "project",
                    "--all-closed",
                    "--tag",
                    "Development",
                    "--subtype",
                    "bug",
                ],
                obj={},
            )

        assert result.exit_code == 0, result.output
        kwargs = maniphest.task_stats.call_args.kwargs
        assert kwargs["since"] == "2026-07-01"
        assert kwargs["until"] == "2026-08-01"
        assert kwargs["period"] == "month"
        assert kwargs["by"] == "project"
        assert kwargs["all_closed"] is True
        assert kwargs["tag"] == "Development"
        assert kwargs["subtype"] == "bug"

    def test_since_is_required(self):
        result = runner.invoke(maniphest_app, ["stats", "--tag", "Development"])

        assert result.exit_code == 2

    def test_a_refusal_is_one_line_and_status_1(self):
        maniphest = MagicMock()
        maniphest.task_stats.side_effect = PhabfiveInputException("Invalid period")

        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=maniphest):
            result = runner.invoke(maniphest_app, ["stats", "--since", "1w"])

        assert result.exit_code == 1
        assert "ERROR: Invalid period" in result.output

    def test_the_choices_are_the_libraries(self):
        assert _STATS_PERIODS == PERIODS
        assert _STATS_GROUPINGS == tuple(GROUPINGS)


class TestTheDisplay:
    RECORDS = [
        {"Period": "2026-W27", "Created": 2, "Closed": 1, "Net": 1},
        {"Period": "2026-W28", "Created": 0, "Closed": 3, "Net": -3},
    ]

    def test_jsonl_is_one_period_per_line(self, capsys):
        from phabfive.display import display_task_stats

        display_task_stats(self.RECORDS, "jsonl", MagicMock())

        lines = capsys.readouterr().out.splitlines()
        assert [json.loads(line) for line in lines] == self.RECORDS

    def test_json_is_one_array(self, capsys):
        from phabfive.display import display_task_stats

        display_task_stats(self.RECORDS, "json", MagicMock())

        assert json.loads(capsys.readouterr().out) == self.RECORDS

    def test_the_terminal_gets_a_table(self):
        from phabfive.display import display_task_stats

        with patch("phabfive.display.display_records_table") as table:
            display_task_stats(self.RECORDS, "rich", MagicMock())

        assert table.call_args.args[1] == self.RECORDS

    def test_nothing_is_an_empty_list_in_json(self, capsys):
        from phabfive.display import display_task_stats

        display_task_stats([], "json", MagicMock())

        assert capsys.readouterr().out.strip() == "[]"
