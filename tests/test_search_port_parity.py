# -*- coding: utf-8 -*-
"""The golden record for the `maniphest search` port (#476).

`search()` in `phabfive/cli/maniphest.py` interpreted a search template and
the command line by itself: the precedence rule, the pattern parsing, the
task-id grammar, the deprecation of `--all` and the criteria guard all lived
inside the command, which is why a program could not run a saved search
without reimplementing it.

The port moves that interpretation into `phabfive.spec.search`. Every
assertion here is written against the *command*, not against the new module,
so it says the same thing before and after the move: given this command line
and this template, these are the exact keyword arguments that reach
`Maniphest.task_search`, in this order, with these messages on the way.

Each class below names one row of the port's contract - the behaviours that
look like accidents and are not. Losing one of them silently is precisely
what a port is good at, so each is pinned by name rather than by being
implied by a bigger test.
"""

from unittest.mock import MagicMock, call, patch

import pytest
from typer.testing import CliRunner

from phabfive.cli.maniphest import maniphest_app
from phabfive.exceptions import PhabfiveConfigException, PhabfiveDataException
from phabfive.transitions import parse_status_patterns

runner = CliRunner()


# Every keyword `task_search` takes, so that a missing one is a failure
# rather than a silently absent assertion.
TASK_SEARCH_KEYS = frozenset(
    {
        "text_query",
        "tag",
        "include_task_ids",
        "exclude_task_ids",
        "assigned",
        "author",
        "space",
        "visible_to",
        "editable_by",
        "created_after",
        "created_before",
        "updated_after",
        "updated_before",
        "column_patterns",
        "priority_patterns",
        "status_patterns",
        "show_history",
        "show_metadata",
        "show_policy",
        "include_closed",
        "limit",
        "order",
        # The constraints maniphest.search always answered and phabfive did
        # not send until #478. They pass through the planner exactly as the
        # keys above do, so the golden record covers them too.
        "closed_after",
        "closed_before",
        "closed_by",
        "ids",
        "phids",
        "subscriber",
        "subtype",
        "parent",
        "subtask",
        "has_parents",
        "has_subtasks",
    }
)


def _output(result):
    """Combined stdout/stderr regardless of click version."""
    output = result.output
    try:
        output += result.stderr
    except (ValueError, AttributeError):
        pass
    return output


def _mock_maniphest(payload=None):
    mock_m = MagicMock()
    mock_m.task_search.return_value = payload
    # The real parser, so the command sees real StatusPattern objects and a
    # bad pattern fails the way the instance would make it fail
    mock_m.parse_status_patterns_with_api.side_effect = parse_status_patterns
    return mock_m


def _invoke(args, *, configs=None, payload=None, output_format=None):
    """Run `maniphest search` with a mocked app, and optionally a template.

    `configs` is what the template loader answers with, in the legacy shape
    the command has always been handed: one mapping per search, with its own
    banner title and description beside the parameters.
    """
    mock_m = _mock_maniphest(payload)
    patches = [patch("phabfive.cli.maniphest._get_maniphest_app", return_value=mock_m)]

    argv = ["search", *args]
    if configs is not None:
        mock_m._load_search_config.return_value = configs
        argv = ["search", "--with", "template.yaml", *args]

    if output_format is not None:
        patches.append(
            patch(
                "phabfive.cli.maniphest._get_output_format", return_value=output_format
            )
        )

    with patches[0]:
        if len(patches) > 1:
            with patches[1]:
                result = runner.invoke(maniphest_app, argv)
        else:
            result = runner.invoke(maniphest_app, argv)

    return result, mock_m


def _kwargs(mock_m, index=0):
    """The keyword arguments of one `task_search` call."""
    kwargs = mock_m.task_search.call_args_list[index][1]
    assert set(kwargs) == TASK_SEARCH_KEYS, sorted(set(kwargs) ^ TASK_SEARCH_KEYS)
    return kwargs


def _config(search, title=None, description=None):
    return {"search": search, "title": title, "description": description}


class TestEveryArgumentIsAKeyword:
    """`task_search` is called with keywords only, and with all of them.

    Every other assertion in this file reads `call_args[1]`, so a port that
    started passing one value positionally would make them assert nothing.
    """

    def test_nothing_is_passed_positionally(self):
        _, mock_m = _invoke(["--tag", "proj"])

        assert mock_m.task_search.call_args[0] == ()

    def test_the_full_keyword_set_is_always_sent(self):
        _, mock_m = _invoke(["--tag", "proj"])

        assert set(mock_m.task_search.call_args[1]) == TASK_SEARCH_KEYS


class TestTheDefaultCall:
    """What a plain `--tag proj` sends, key by key.

    The golden record: everything not asked for is None, the three `show-*`
    flags and `include_closed` are False, and only `limit` has a value.
    """

    def test_one_filter_and_nothing_else(self):
        _, mock_m = _invoke(["--tag", "proj"])

        assert _kwargs(mock_m) == {
            "text_query": None,
            "tag": "proj",
            "include_task_ids": None,
            "exclude_task_ids": None,
            "assigned": None,
            "author": None,
            "space": None,
            "visible_to": None,
            "editable_by": None,
            "created_after": None,
            "created_before": None,
            "updated_after": None,
            "updated_before": None,
            "column_patterns": None,
            "priority_patterns": None,
            "status_patterns": None,
            "show_history": False,
            "show_metadata": False,
            "show_policy": False,
            "include_closed": False,
            "limit": 100,
            "order": None,
            # #478's constraints, absent unless asked for, and `None` rather
            # than `False` for the two booleans: a task with no parent is a
            # real thing to search for, so "not asked" cannot be False.
            "closed_after": None,
            "closed_before": None,
            "closed_by": None,
            "ids": None,
            "phids": None,
            "subscriber": None,
            "subtype": None,
            "parent": None,
            "subtask": None,
            "has_parents": None,
            "has_subtasks": None,
        }


class TestLimitSentinel:
    """C1: `--limit` cannot be told apart from its own default.

    Typer has no unset marker, so the command passes `limit if limit != 100
    else None` and a template's `limit:` wins over a *typed* `--limit 100`.
    A quirk, deliberately kept by the port rather than fixed inside it: the
    correct fix changes behaviour, so it gets its own issue.
    """

    def test_a_template_limit_is_honoured(self):
        _, mock_m = _invoke([], configs=[_config({"tag": "proj", "limit": 5})])

        assert _kwargs(mock_m)["limit"] == 5

    def test_the_cli_limit_overrides_a_template(self):
        _, mock_m = _invoke(
            ["--limit", "7"], configs=[_config({"tag": "proj", "limit": 5})]
        )

        assert _kwargs(mock_m)["limit"] == 7

    def test_typing_the_default_limit_does_not_beat_a_template(self):
        _, mock_m = _invoke(
            ["--limit", "100"], configs=[_config({"tag": "proj", "limit": 5})]
        )

        assert _kwargs(mock_m)["limit"] == 5

    def test_zero_is_a_real_value_and_overrides(self):
        _, mock_m = _invoke(
            ["--limit", "0"], configs=[_config({"tag": "proj", "limit": 5})]
        )

        assert _kwargs(mock_m)["limit"] == 0

    def test_no_template_and_no_flag_is_a_hundred(self):
        _, mock_m = _invoke(["--tag", "proj"])

        assert _kwargs(mock_m)["limit"] == 100


class TestBooleanSentinels:
    """C2: a flag that is off is "not supplied", so a template's true wins."""

    @pytest.mark.parametrize(
        "flag,key,parameter",
        [
            ("--show-history", "show-history", "show_history"),
            ("--show-metadata", "show-metadata", "show_metadata"),
            ("--show-policy", "show-policy", "show_policy"),
            ("--all", "all", "include_closed"),
        ],
    )
    def test_the_flag_turns_it_on(self, flag, key, parameter):
        _, mock_m = _invoke([flag, "--tag", "proj"])

        assert _kwargs(mock_m)[parameter] is True

    @pytest.mark.parametrize(
        "key,parameter",
        [
            ("show-history", "show_history"),
            ("show-metadata", "show_metadata"),
            ("show-policy", "show_policy"),
            ("all", "include_closed"),
        ],
    )
    def test_a_template_true_survives_without_the_flag(self, key, parameter):
        _, mock_m = _invoke([], configs=[_config({"tag": "proj", key: True})])

        assert _kwargs(mock_m)[parameter] is True

    @pytest.mark.parametrize(
        "flag,key,parameter",
        [
            ("--show-history", "show-history", "show_history"),
            ("--show-metadata", "show-metadata", "show_metadata"),
            ("--show-policy", "show-policy", "show_policy"),
            ("--all", "all", "include_closed"),
        ],
    )
    def test_omitting_the_flag_cannot_turn_a_template_true_back_off(
        self, flag, key, parameter
    ):
        # There is no --no-show-history, so "off" is indistinguishable from
        # "unset" and the template keeps the last word. Pinned because it is
        # the reason the sentinel exists.
        _, mock_m = _invoke([], configs=[_config({"tag": "proj", key: True})])

        assert _kwargs(mock_m)[parameter] is True

    @pytest.mark.parametrize(
        "key,parameter",
        [
            ("show-history", "show_history"),
            ("show-metadata", "show_metadata"),
            ("show-policy", "show_policy"),
        ],
    )
    def test_a_template_false_stays_false(self, key, parameter):
        _, mock_m = _invoke([], configs=[_config({"tag": "proj", key: False})])

        assert _kwargs(mock_m)[parameter] is False


class TestOrderIsNotClobbered:
    """C3: `--order` has no default, so a template's `order:` survives."""

    def test_no_order_anywhere_is_none(self):
        _, mock_m = _invoke(["--tag", "proj"])

        assert _kwargs(mock_m)["order"] is None

    def test_a_template_order_reaches_task_search(self):
        _, mock_m = _invoke([], configs=[_config({"tag": "proj", "order": "title"})])

        assert _kwargs(mock_m)["order"] == "title"

    def test_the_flag_wins(self):
        _, mock_m = _invoke(
            ["--order", "created:asc"],
            configs=[_config({"tag": "proj", "order": "title"})],
        )

        assert _kwargs(mock_m)["order"] == "created:asc"


class TestTheBanner:
    """C4: printed for rich and tree only, and only when it labels something.

    stdout, not stderr: a person reading the results reads the banner with
    them. Exact text, because it is the only thing separating two searches'
    results from each other.
    """

    def _banner(self, configs, output_format="rich"):
        result, _ = _invoke([], configs=configs, output_format=output_format)
        return result

    def test_a_titled_single_search_gets_one(self):
        result = self._banner([_config({"tag": "proj"}, title="Mine")])

        assert "=" * 60 in result.stdout
        assert "🔍 Mine" in result.stdout

    def test_a_description_is_printed_under_the_title(self):
        result = self._banner(
            [_config({"tag": "proj"}, title="Mine", description="What I own")]
        )

        assert "🔍 Mine" in result.stdout
        assert "📝 What I own" in result.stdout

    def test_a_description_alone_still_gets_a_banner(self):
        result = self._banner([_config({"tag": "proj"}, description="Only this")])

        assert "📝 Only this" in result.stdout

    def test_an_unnamed_search_in_a_multi_document_file_is_numbered(self):
        result = self._banner(
            [_config({"tag": "one"}), _config({"tag": "two"})],
        )

        assert "🔍 Search 1" in result.stdout
        assert "🔍 Search 2" in result.stdout

    def test_an_unnamed_single_search_gets_no_banner(self):
        result = self._banner([_config({"tag": "proj"})])

        assert "🔍" not in result.stdout

    def test_tree_gets_one_too(self):
        result = self._banner(
            [_config({"tag": "proj"}, title="Mine")], output_format="tree"
        )

        assert "🔍 Mine" in result.stdout

    @pytest.mark.parametrize("output_format", ["json", "jsonl", "yaml", "value"])
    def test_a_machine_format_gets_none(self, output_format):
        result = self._banner(
            [_config({"tag": "proj"}, title="Mine")], output_format=output_format
        )

        assert "🔍" not in result.stdout
        assert "=" * 60 not in result.stdout


class TestIncludeAndExcludeAreParsedBeforeAnythingRuns:
    """C5: the task-id grammar, and its two errors, before the first query."""

    def test_a_comma_separated_string(self):
        _, mock_m = _invoke(["--include", "T1,T2"])

        assert _kwargs(mock_m)["include_task_ids"] == [1, 2]

    def test_a_yaml_list(self):
        _, mock_m = _invoke([], configs=[_config({"include": ["T1", "T2"]})])

        assert _kwargs(mock_m)["include_task_ids"] == [1, 2]

    def test_a_yaml_list_whose_entries_hold_commas(self):
        _, mock_m = _invoke([], configs=[_config({"include": ["T1,T2", "T3"]})])

        assert _kwargs(mock_m)["include_task_ids"] == [1, 2, 3]

    def test_whitespace_around_an_id_is_ignored(self):
        _, mock_m = _invoke(["--include", " T1 , T2 "])

        assert _kwargs(mock_m)["include_task_ids"] == [1, 2]

    def test_an_empty_entry_is_skipped(self):
        _, mock_m = _invoke(["--include", "T1,,T2"])

        assert _kwargs(mock_m)["include_task_ids"] == [1, 2]

    def test_a_list_of_only_separators_is_none(self):
        _, mock_m = _invoke(["--tag", "proj", "--exclude", ","])

        assert _kwargs(mock_m)["exclude_task_ids"] is None

    def test_a_bad_id_stops_the_search(self):
        result, mock_m = _invoke(["--include", "BAD"])

        assert result.exit_code == 1
        assert "Invalid task ID 'BAD'. Expected format: T123" in _output(result)
        mock_m.task_search.assert_not_called()

    def test_another_applications_monogram_is_not_a_task(self):
        result, mock_m = _invoke(["--tag", "proj", "--exclude", "T1,K2"])

        assert result.exit_code == 1
        assert "Invalid task ID 'K2'. Expected format: T123" in _output(result)
        mock_m.task_search.assert_not_called()

    def test_an_id_in_both_lists_stops_the_search(self):
        result, mock_m = _invoke(["--include", "T1,T2", "--exclude", "T2,T1"])

        assert result.exit_code == 1
        assert "T1, T2 cannot be both included and excluded" in _output(result)
        mock_m.task_search.assert_not_called()

    def test_the_overlap_is_named_in_numeric_order(self):
        result, _ = _invoke(["--include", "T10,T2", "--exclude", "T10,T2"])

        assert "T2, T10 cannot be both included and excluded" in _output(result)


class TestTemplateLoadFailure:
    """C7: any failure to read the template is one line and exit 1."""

    def test_the_message_and_the_status(self):
        mock_m = _mock_maniphest()
        mock_m._load_search_config.side_effect = PhabfiveDataException("no good")

        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=mock_m):
            result = runner.invoke(maniphest_app, ["search", "--with", "template.yaml"])

        assert result.exit_code == 1
        assert "ERROR: Failed to load template file: no good" in _output(result)
        mock_m.task_search.assert_not_called()


class TestNoTemplateIsOneEmptySearch:
    """C8: with no `--with`, the command plans exactly one unconstrained search."""

    def test_a_bare_search_hits_the_criteria_guard(self):
        result, mock_m = _invoke([])

        assert result.exit_code == 2
        mock_m.task_search.assert_not_called()

    def test_one_filter_makes_exactly_one_call(self):
        _, mock_m = _invoke(["--tag", "proj"])

        assert mock_m.task_search.call_count == 1


class TestTemplateKeySpellings:
    """C9: template keys are hyphenated, except `text_query`."""

    @pytest.mark.parametrize(
        "key,value,parameter",
        [
            ("text_query", "needle", "text_query"),
            ("tag", "proj", "tag"),
            ("assigned", "@me", "assigned"),
            ("author", "alice", "author"),
            ("space", "S1", "space"),
            ("visible-to", "users", "visible_to"),
            ("editable-by", "admin", "editable_by"),
            ("created-after", "7d", "created_after"),
            ("created-before", "1y", "created_before"),
            ("updated-after", "2w", "updated_after"),
            ("updated-before", "3m", "updated_before"),
        ],
    )
    def test_each_key_reaches_its_parameter(self, key, value, parameter):
        _, mock_m = _invoke([], configs=[_config({key: value})])

        assert _kwargs(mock_m)[parameter] == value

    def test_an_underscored_spelling_of_a_hyphenated_key_is_refused(self):
        """`created_after:` is not a key, and is never quietly ignored.

        A real template is refused by the loader before the command sees it.
        These tests hand the command an already-loaded item, which is the
        only way to reach the planner's own check - the second half of the
        same guard, and the one a program that plans from a dict meets.
        Either way it is exit 1 naming the key, never a search that silently
        dropped a filter.
        """
        result, mock_m = _invoke([], configs=[_config({"created_after": "7d"})])

        assert result.exit_code == 1
        assert "Unsupported search parameters: created_after" in _output(result)
        mock_m.task_search.assert_not_called()


class TestDeprecatedAllSaysWhereItCameFrom:
    """C10: two wordings, one per source, and the conflict that follows."""

    def test_the_flag_names_the_flag(self):
        result, _ = _invoke(["--all"])

        assert "WARNING: --all is deprecated, use --status=any instead." in _output(
            result
        )

    def test_a_template_names_the_template(self):
        result, _ = _invoke([], configs=[_config({"all": True})])

        assert (
            "WARNING: 'all: true' in a search template is deprecated, "
            "use 'status: any' instead." in _output(result)
        )

    def test_the_flag_wording_wins_when_both_say_it(self):
        result, _ = _invoke(["--all"], configs=[_config({"all": True})])

        assert "WARNING: --all is deprecated" in _output(result)
        assert "in a search template is deprecated" not in _output(result)

    @pytest.mark.parametrize("status", ["open", "closed", "closed+in:Resolved"])
    def test_a_narrower_scope_is_refused(self, status):
        result, mock_m = _invoke(["--all", "--status", status])

        assert result.exit_code == 1
        assert "ERROR: --all cannot be combined with --status" in _output(result)
        mock_m.task_search.assert_not_called()

    def test_the_warning_is_printed_before_the_refusal(self):
        result, _ = _invoke(["--all", "--status", "open"])

        output = _output(result)
        assert output.index("--all is deprecated") < output.index(
            "--all cannot be combined"
        )

    def test_both_named_scopes_are_listed_sorted(self):
        result, _ = _invoke(["--all", "--status", "open,closed"])

        assert "--status closed/open; use --status alone" in _output(result)

    def test_status_any_is_not_a_conflict(self):
        result, mock_m = _invoke(["--all", "--status", "any"])

        assert result.exit_code == 0
        mock_m.task_search.assert_called_once()


class TestTheCriteriaGuard:
    """C11: which values count as a criterion, and when the guard runs."""

    @pytest.mark.parametrize(
        "search",
        [
            {"text_query": "needle"},
            {"tag": "proj"},
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
    def test_each_criterion_lifts_the_guard(self, search):
        result, mock_m = _invoke([], configs=[_config(search)])

        assert result.exit_code == 0
        mock_m.task_search.assert_called_once()

    @pytest.mark.parametrize(
        "search",
        [
            {"exclude": "T1"},
            {"show-history": True},
            {"show-metadata": True},
            {"show-policy": True},
            {"limit": 5},
            {"order": "title"},
        ],
    )
    def test_these_do_not_lift_it(self, search):
        result, mock_m = _invoke([], configs=[_config(search)])

        assert result.exit_code == 2
        mock_m.task_search.assert_not_called()

    def test_it_runs_per_item_so_an_earlier_search_still_ran(self):
        """A two-search file whose second search is empty prints the first.

        The guard is inside the loop, not a validation pass before it, so
        document 1's results reach the terminal and only then does the
        command exit 2.
        """
        result, mock_m = _invoke([], configs=[_config({"tag": "one"}), _config({})])

        assert result.exit_code == 2
        assert mock_m.task_search.call_count == 1
        assert _kwargs(mock_m)["tag"] == "one"


class TestPatternErrors:
    """C12: one message per pattern kind, each exit 1, none of them searching."""

    @pytest.mark.parametrize(
        "flag,label",
        [
            ("--column", "column"),
            ("--priority", "priority"),
            ("--status", "status"),
        ],
    )
    def test_a_bad_pattern_is_named_by_its_kind(self, flag, label):
        result, mock_m = _invoke([flag, "bogus:value", "--tag", "proj"])

        assert result.exit_code == 1
        assert f"ERROR: Invalid {label} filter pattern:" in _output(result)
        mock_m.task_search.assert_not_called()

    def test_the_status_pattern_goes_through_the_api_aware_parser(self):
        """`parse_status_patterns_with_api` costs a request before searching.

        It is the app's method, not the free function, because a status
        pattern may name a status only this instance knows about.
        """
        _, mock_m = _invoke(["--status", "in:Resolved", "--tag", "proj"])

        mock_m.parse_status_patterns_with_api.assert_called_once_with("in:Resolved")

    def test_the_column_pattern_reaches_task_search_parsed(self):
        _, mock_m = _invoke(["--column", "in:Backlog", "--tag", "proj"])

        patterns = _kwargs(mock_m)["column_patterns"]
        assert patterns is not None
        assert [c["type"] for p in patterns for c in p.conditions] == ["in"]

    def test_patterns_are_parsed_before_the_criteria_guard(self):
        """A bad pattern is exit 1, not the guard's exit 2.

        `--column` is the only thing on this command line, so both checks
        apply and only the order decides which status the user sees.
        """
        result, _ = _invoke(["--column", "bogus:value"])

        assert result.exit_code == 1


class TestATagThatMatchesNothing:
    """C13: `task_search` answers None, and that is not an error."""

    def test_none_is_displayed_and_the_status_is_zero(self):
        mock_m = _mock_maniphest(payload=None)

        with (
            patch("phabfive.cli.maniphest._get_maniphest_app", return_value=mock_m),
            patch("phabfive.cli.maniphest._display_tasks") as display,
        ):
            result = runner.invoke(maniphest_app, ["search", "--tag", "nothing"])

        assert result.exit_code == 0
        assert display.call_args[0][0] is None

    def test_the_next_search_in_the_file_still_runs(self):
        mock_m = _mock_maniphest(payload=None)
        mock_m._load_search_config.return_value = [
            _config({"tag": "one"}),
            _config({"tag": "two"}),
        ]

        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=mock_m):
            result = runner.invoke(maniphest_app, ["search", "--with", "template.yaml"])

        assert result.exit_code == 0
        assert mock_m.task_search.call_count == 2


class TestTheNoMatchHint:
    """C14: only a text search that found nothing explains itself."""

    def test_a_text_search_with_no_tasks_says_so(self):
        result, _ = _invoke(["needle"], payload={"tasks": []})

        assert "No tasks found" in _output(result)

    def test_a_text_search_with_tasks_says_nothing(self):
        result, _ = _invoke(["needle"], payload={"tasks": [{"id": 1}]})

        assert "No tasks found" not in _output(result)

    def test_an_empty_search_without_a_text_query_says_nothing(self):
        result, _ = _invoke(["--tag", "proj"], payload={"tasks": []})

        assert "No tasks found" not in _output(result)

    def test_a_none_payload_with_a_text_query_still_says_so(self):
        result, _ = _invoke(["needle"], payload=None)

        assert "No tasks found" in _output(result)

    def test_the_text_query_comes_from_a_template_too(self):
        result, _ = _invoke(
            [], configs=[_config({"text_query": "needle"})], payload={"tasks": []}
        )

        assert "No tasks found" in _output(result)


class TestPerItemOrdering:
    """C15: one item is finished completely before the next one starts."""

    def test_the_banner_of_item_two_follows_the_results_of_item_one(self):
        mock_m = _mock_maniphest(payload={"tasks": []})
        mock_m._load_search_config.return_value = [
            _config({"tag": "one"}, title="One"),
            _config({"tag": "two"}, title="Two"),
        ]

        def display(*_args, **_kwargs):
            print("RESULTS")

        with (
            patch("phabfive.cli.maniphest._get_maniphest_app", return_value=mock_m),
            patch("phabfive.cli.maniphest._get_output_format", return_value="rich"),
            patch("phabfive.cli.maniphest._display_tasks", side_effect=display),
        ):
            result = runner.invoke(maniphest_app, ["search", "--with", "template.yaml"])

        stdout = result.stdout
        assert stdout.index("🔍 One") < stdout.index("RESULTS")
        assert stdout.index("RESULTS") < stdout.index("🔍 Two")

    def test_a_failure_in_item_two_leaves_item_one_searched(self):
        result, mock_m = _invoke(
            [], configs=[_config({"tag": "one"}), _config({"include": "BAD"})]
        )

        assert result.exit_code == 1
        assert mock_m.task_search.call_count == 1

    def test_each_item_is_searched_with_its_own_parameters(self):
        _, mock_m = _invoke(
            [],
            configs=[
                _config({"tag": "one", "limit": 5}),
                _config({"tag": "two", "order": "title"}),
            ],
        )

        assert _kwargs(mock_m, 0)["tag"] == "one"
        assert _kwargs(mock_m, 0)["limit"] == 5
        assert _kwargs(mock_m, 0)["order"] is None
        assert _kwargs(mock_m, 1)["tag"] == "two"
        assert _kwargs(mock_m, 1)["limit"] == 100
        assert _kwargs(mock_m, 1)["order"] == "title"


class TestTaskSearchErrorsAreCaught:
    """C16: exactly the two types the command answers with one line."""

    @pytest.mark.parametrize(
        "error",
        [
            PhabfiveConfigException("bad policy"),
            PhabfiveDataException("no such project"),
        ],
    )
    def test_one_line_and_exit_one(self, error):
        mock_m = _mock_maniphest()
        mock_m.task_search.side_effect = error

        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=mock_m):
            result = runner.invoke(maniphest_app, ["search", "--tag", "proj"])

        assert result.exit_code == 1
        assert f"ERROR: {error}" in _output(result)

    def test_the_display_is_not_reached(self):
        mock_m = _mock_maniphest()
        mock_m.task_search.side_effect = PhabfiveDataException("nope")

        with (
            patch("phabfive.cli.maniphest._get_maniphest_app", return_value=mock_m),
            patch("phabfive.cli.maniphest._display_tasks") as display,
        ):
            result = runner.invoke(maniphest_app, ["search", "--tag", "proj"])

        assert result.exit_code == 1
        display.assert_not_called()


class TestTheDisplayCall:
    """What `_display_tasks` is handed, since the port must keep handing it."""

    def test_the_payload_the_format_the_app_and_tabular(self):
        payload = {"tasks": [{"id": 1}]}
        mock_m = _mock_maniphest(payload=payload)

        with (
            patch("phabfive.cli.maniphest._get_maniphest_app", return_value=mock_m),
            patch("phabfive.cli.maniphest._get_output_format", return_value="rich"),
            patch("phabfive.cli.maniphest._display_tasks") as display,
        ):
            runner.invoke(maniphest_app, ["search", "--tag", "proj"])

        assert display.call_args == call(payload, "rich", mock_m, tabular=True)
