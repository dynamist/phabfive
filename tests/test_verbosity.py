# -*- coding: utf-8 -*-

"""Verbosity of the filter notices emitted by `maniphest search`.

`maniphest search` always narrows to a Space, taking `PHAB_SPACE` (default
S1) when `--space` is not given. That implicit narrowing used to be
announced with a `log.warning`, which made it the one informational
message loud enough to reach an unconfigured logger, so it printed on
every single search.

The notices now sit at INFO alongside the other filter messages, and the
CLI configures logging so that `-v` opts into them. These tests pin both
halves: the level the notices are logged at, and the level the CLI
derives from `-v` / `--log-level`.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from phabfive.cli import app, preprocess_monograms, resolve_log_level
from phabfive.maniphest.core import Maniphest

runner = CliRunner()

PROJECT_A = "PHID-PROJ-aaaaaaaaaaaaaaaaaaaa"

SPACES = {
    "S1": {"phid": "PHID-SPCE-1", "name": "Global", "fullName": "Global", "uri": "/S1"},
    "S2": {"phid": "PHID-SPCE-2", "name": "Secret", "fullName": "Secret", "uri": "/S2"},
}


def _maniphest(conf=None, project_phids=None):
    """A Maniphest with a mocked API, resolving S1/S2 and one project."""
    maniphest = Maniphest()
    maniphest.phab = MagicMock()
    maniphest.url = "https://phabricator.example.com"
    maniphest.conf = conf if conf is not None else {"PHAB_SPACE": "S1"}

    def lookup(names):
        return {name: SPACES[name] for name in names if name in SPACES}

    maniphest.phab.phid.lookup.side_effect = lookup

    response = MagicMock()
    response.response = {"data": []}
    response.get.return_value = {"after": None}
    maniphest.phab.maniphest.search.return_value = response

    maniphest._resolve_project_phids = MagicMock(
        return_value=project_phids if project_phids is not None else [PROJECT_A]
    )
    maniphest._get_open_statuses = MagicMock(return_value=["open"])

    return maniphest


def _records(caplog, needle):
    """Log records whose message contains needle."""
    return [r for r in caplog.records if needle in r.getMessage()]


@patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None)
class TestFilterNoticeLevels:
    """The filter notices belong at INFO, not WARNING."""

    def test_implicit_space_notice_is_info(self, mock_init, caplog):
        caplog.set_level(logging.INFO)
        _maniphest().task_search(tag="TeamA")

        matches = _records(caplog, "Filtering to space(s)")
        assert len(matches) == 1

        record = matches[0]
        # The whole point of the change: this must not be a warning, or it
        # reaches users who never asked for it.
        assert record.levelno == logging.INFO
        assert "PHAB_SPACE" in record.getMessage()
        assert "--space='*'" in record.getMessage()

    def test_implicit_space_notice_is_silent_by_default(self, mock_init, caplog):
        caplog.set_level(logging.WARNING)
        _maniphest().task_search(tag="TeamA")

        assert _records(caplog, "Filtering to space(s)") == []

    def test_explicit_space_is_announced(self, mock_init, caplog):
        caplog.set_level(logging.INFO)
        _maniphest().task_search(tag="TeamA", space="S1,S2")

        matches = _records(caplog, "Filtering to space(s)")
        assert len(matches) == 1
        assert matches[0].levelno == logging.INFO
        assert "S1,S2" in matches[0].getMessage()
        # An explicit choice needs no nudge about the default
        assert "PHAB_SPACE" not in matches[0].getMessage()

    def test_single_tag_is_announced(self, mock_init, caplog):
        caplog.set_level(logging.INFO)
        _maniphest().task_search(tag="TeamA")

        matches = _records(caplog, "Filtering to tag(s)")
        assert len(matches) == 1
        assert matches[0].levelno == logging.INFO
        assert "TeamA" in matches[0].getMessage()

    def test_unresolvable_configured_space_still_warns(self, mock_init, caplog):
        caplog.set_level(logging.INFO)
        maniphest = _maniphest(conf={"PHAB_SPACE": "SNope"})
        maniphest.task_search(tag="TeamA")

        matches = _records(caplog, "Could not resolve default space")
        assert len(matches) == 1
        # A misconfigured space is a real problem, so it stays loud
        assert matches[0].levelno == logging.WARNING


class TestResolveLogLevel:
    """-v and -q walk one ladder, counting against each other."""

    @pytest.mark.parametrize(
        "verbose,quiet,expected",
        [
            (0, 0, "WARNING"),
            (1, 0, "INFO"),
            (2, 0, "DEBUG"),
            (0, 1, "ERROR"),
            (0, 2, "CRITICAL"),
        ],
    )
    def test_each_step(self, verbose, quiet, expected):
        assert resolve_log_level(verbose, quiet) == expected

    @pytest.mark.parametrize(
        "verbose,quiet,expected",
        [
            (3, 0, "DEBUG"),
            (99, 0, "DEBUG"),
            (0, 3, "CRITICAL"),
            (0, 99, "CRITICAL"),
        ],
    )
    def test_saturates_at_both_ends(self, verbose, quiet, expected):
        """Past the end of the ladder the level holds instead of wrapping."""
        assert resolve_log_level(verbose, quiet) == expected

    @pytest.mark.parametrize(
        "verbose,quiet,expected",
        [
            (1, 1, "WARNING"),
            (2, 1, "INFO"),
            (1, 2, "ERROR"),
        ],
    )
    def test_opposing_flags_cancel(self, verbose, quiet, expected):
        assert resolve_log_level(verbose, quiet) == expected


class TestCliWiring:
    """The CLI must actually configure logging, which it once stopped doing."""

    def _level_for(self, argv):
        with patch("phabfive.cli.init_logging") as init:
            runner.invoke(app, argv)
        assert init.called, f"logging never configured for {argv}"
        return init.call_args[0][0]

    @pytest.mark.parametrize(
        "argv,expected",
        [
            (["maniphest", "--help"], "WARNING"),
            (["-v", "maniphest", "--help"], "INFO"),
            (["-vv", "maniphest", "--help"], "DEBUG"),
            (["--verbose", "maniphest", "--help"], "INFO"),
            (["-q", "maniphest", "--help"], "ERROR"),
            (["-qq", "maniphest", "--help"], "CRITICAL"),
            (["--quiet", "maniphest", "--help"], "ERROR"),
        ],
    )
    def test_level_passed_to_init_logging(self, argv, expected):
        assert self._level_for(argv) == expected

    def test_log_level_option_is_gone(self):
        """--log-level was replaced by -v/-q and must not linger."""
        result = runner.invoke(app, ["--log-level=DEBUG", "maniphest", "--help"])

        assert result.exit_code != 0
        assert "No such option" in result.output


class TestMonogramsAfterValuelessFlags:
    """A valueless global flag must not swallow the monogram after it.

    Monogram preprocessing assumed every option consumes the next argv
    entry, which is true of --format and -l but not of -v, so
    "phabfive -v T123" stopped expanding to "maniphest show T123".
    """

    @pytest.mark.parametrize(
        "args,expected",
        [
            (["T123"], ["maniphest", "show", "T123"]),
            (["-v", "T123"], ["-v", "maniphest", "show", "T123"]),
            (["-vv", "T123"], ["-vv", "maniphest", "show", "T123"]),
            (["--verbose", "T123"], ["--verbose", "maniphest", "show", "T123"]),
            (["-q", "T123"], ["-q", "maniphest", "show", "T123"]),
            (["-qq", "T123"], ["-qq", "maniphest", "show", "T123"]),
            (["--quiet", "T123"], ["--quiet", "maniphest", "show", "T123"]),
            (["-v", "edit", "T123"], ["-v", "maniphest", "edit", "T123"]),
            (
                ["-v", "T123", "a comment"],
                ["-v", "maniphest", "comment", "T123", "a comment"],
            ),
        ],
    )
    def test_valueless_flags_keep_expansion(self, args, expected):
        assert preprocess_monograms(["phabfive", *args])[1:] == expected

    @pytest.mark.parametrize(
        "args,expected",
        [
            (["-l", "5", "T123"], ["-l", "5", "maniphest", "show", "T123"]),
            (
                ["--format", "json", "T123"],
                ["--format", "json", "maniphest", "show", "T123"],
            ),
            (["--format=json", "T123"], ["--format=json", "maniphest", "show", "T123"]),
        ],
    )
    def test_valued_options_still_consume_their_value(self, args, expected):
        assert preprocess_monograms(["phabfive", *args])[1:] == expected
