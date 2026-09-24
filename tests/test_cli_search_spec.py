# -*- coding: utf-8 -*-
"""Tests for `phabfive search -f`, and for what `--with` became (#486).

**The new command dispatches on `kind:`.** A create spec handed to `search`
is answered with one sentence naming `phabfive apply -f`, as a usage refusal
rather than a validation problem.

**The kind is never handed to the loader.** `load_spec(path, kind="search")`
does not refuse a create spec, it *reinterprets* it - down to inventing an
empty search item, which runs an unconstrained search nobody asked for. That
was live on `project|paste|passphrase search --with` until this issue, so it
is pinned here from both entry points.

**`--with` still works, and says once what replaces it.** A `log.warning`,
the way `phabfive/core.py` deprecates the yaml-config spelling of `PHAB_URL`
- not a break. It is documented, shipped and in people's scripts.

**The two entry points differ in exactly one way**, deliberately: a failure
after the offline layer has passed is 2 from `search -f` where `--with`
answers 1, because by then it is the instance's answer and not the file's.
"""

# python std lib
import logging
from unittest.mock import MagicMock, patch

# 3rd party imports
import pytest
import typer
from typer.testing import CliRunner

# phabfive imports
from phabfive.cli import app, preprocess_monograms
from phabfive.cli import search_spec as cli_search_spec
from phabfive.cli import spec_run
from phabfive.cli.spec_flags import warn_with_deprecated
from phabfive.core import Phabfive
from phabfive.exceptions import PhabfiveDataException
from phabfive.spec.envelope import Kind

runner = CliRunner()

SEARCH = """\
spec: phorge/v1alpha1
kind: search
metadata:
  description: One task search
searches:
  - title: Recently touched
    search:
      limit: 10
"""

CREATE = """\
spec: phorge/v1alpha1
kind: create
tasks:
  - title: Set up CI
"""

# Neither key family, so the loader cannot place it and `--kind` is what it
# was waiting for: the legacy flat search template, which carries a title and
# a description and nothing that says what it is.
KINDLESS = """\
title: Recently touched
description: Whatever moved this week
"""

# Both key families and no `kind:`: the loader refuses to guess, and the one
# retry must not answer for it.
AMBIGUOUS = """\
tasks:
  - title: Set up CI
searches:
  - search:
      limit: 10
"""

# A search item that names nothing to narrow by. Legal as a document - the
# offline layer has nothing to object to - and refused by the command,
# because running it would search the whole instance.
NO_FILTER = """\
spec: phorge/v1alpha1
kind: search
metadata:
  description: A search that says nothing
searches:
  - title: Everything
    search: {}
"""

BROKEN = """\
kind: search
searches:
  - search:
      limit: lots
"""


@pytest.fixture(autouse=True)
def restore_output_format():
    """Put the class-level output format back, even when a test fails."""
    original = Phabfive._output_format
    try:
        yield
    finally:
        Phabfive._output_format = original


@pytest.fixture(autouse=True)
def never_builds_an_app():
    """Fail loudly if a test reaches the instance without saying it means to."""
    with patch.object(
        spec_run,
        "_instance",
        side_effect=AssertionError("the command constructed an app"),
    ) as never:
        yield never


def write(tmp_path, text, name="spec.yaml"):
    """Put a spec on disk and give back the path as a string."""
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def invoke(*args):
    """Run the root app, so the global --format callback runs too."""
    return runner.invoke(app, list(args))


def an_instance():
    """An app the runner can be handed, which asks nothing."""
    instance = MagicMock()
    instance.conf = {"PHAB_URL": "https://phorge.example.com/api/"}
    return instance


class TestDispatchOnKind:
    """The wrong kind is a sentence naming the right command."""

    def test_a_create_spec_is_refused_by_name(self, tmp_path, never_builds_an_app):
        path = write(tmp_path, CREATE)

        result = invoke("--format=rich", "search", "-f", path)

        assert result.exit_code == 1
        assert "is a create spec" in result.stderr
        assert f"phabfive apply -f {path}" in result.stderr
        never_builds_an_app.assert_not_called()

    def test_the_refusal_is_not_a_problem_record(self, tmp_path):
        result = invoke("--format=json", "search", "-f", write(tmp_path, CREATE))

        assert result.exit_code == 1
        assert result.stdout.strip() == ""

    def test_a_create_spec_is_not_reinterpreted_as_an_empty_search(self, tmp_path):
        """The trap fact 1 named: `kind="search"` invents a search item.

        Loaded the way the commands load it, the spec stays what it is, so
        the refusal above has something to refuse.
        """
        from phabfive.cli.spec_flags import load_of_kind

        spec = load_of_kind(write(tmp_path, CREATE), "search")

        assert spec.envelope.kind is Kind.CREATE
        assert spec.items("search") == []


class TestLoadingWithoutAKind:
    """One retry, and only when there is nothing of the other kind to lose."""

    def test_a_file_that_says_nothing_is_read_as_this_command_s_kind(self, tmp_path):
        from phabfive.cli.spec_flags import load_of_kind

        spec = load_of_kind(write(tmp_path, KINDLESS), "search")

        assert spec.envelope.kind is Kind.SEARCH

    def test_an_ambiguous_file_is_still_refused(self, tmp_path):
        from phabfive.cli.spec_flags import load_of_kind

        with pytest.raises(PhabfiveDataException) as refused:
            load_of_kind(write(tmp_path, AMBIGUOUS), "search")

        assert "could be either kind" in str(refused.value)

    def test_a_file_that_does_not_exist_raises_the_first_error(self, tmp_path):
        from phabfive.cli.spec_flags import load_of_kind

        with pytest.raises(Exception) as refused:
            load_of_kind(str(tmp_path / "nothing-here.yaml"), "search")

        assert "nothing-here.yaml" in str(refused.value)


class TestExitStatus:
    """The ladder, and the one place it differs from `--with`."""

    def test_a_search_that_ran_is_zero(self, tmp_path):
        with patch.object(spec_run, "_instance", return_value=an_instance()):
            with patch.object(cli_search_spec, "run_search_spec") as ran:
                result = invoke(
                    "--format=rich", "search", "-f", write(tmp_path, SEARCH)
                )

        assert result.exit_code == 0
        assert ran.call_count == 1
        assert ran.call_args.args[2].envelope.kind is Kind.SEARCH

    def test_an_offline_failure_is_one(self, tmp_path, never_builds_an_app):
        with patch.object(cli_search_spec, "run_search_spec") as never_ran:
            result = invoke("--format=rich", "search", "-f", write(tmp_path, BROKEN))

        assert result.exit_code == 1
        assert "wrong-type" in result.stdout
        never_ran.assert_not_called()
        never_builds_an_app.assert_not_called()

    def test_a_file_that_cannot_be_read_is_one(self, tmp_path, never_builds_an_app):
        result = invoke(
            "--format=rich", "search", "-f", str(tmp_path / "nothing-here.yaml")
        )

        assert result.exit_code == 1
        assert "unreadable" in result.stdout
        never_builds_an_app.assert_not_called()

    def test_the_loop_is_told_which_status_an_online_failure_takes(self, tmp_path):
        """2 is asked for once, where the loop can tell the two apart.

        The loop knows whether it caught the *file* being wrong - a search
        item with no filter, a pattern that does not parse - or the
        *instance* refusing. A blanket remap around the call would not, and
        would answer 2 for a file nothing was ever asked about.
        """
        with patch.object(spec_run, "_instance", return_value=an_instance()):
            with patch.object(cli_search_spec, "run_search_spec") as ran:
                result = invoke(
                    "--format=rich", "search", "-f", write(tmp_path, SEARCH)
                )

        assert result.exit_code == 0
        assert ran.call_args.kwargs["online_exit"] == 2

    def test_a_failure_the_instance_answered_with_is_two(self, tmp_path):
        """`run_search_spec` says 1 for `--with`; here the file is already clean."""
        with patch.object(spec_run, "_instance", return_value=an_instance()):
            with patch.object(
                cli_search_spec,
                "run_search_spec",
                side_effect=lambda *a, **kw: (_ for _ in ()).throw(
                    typer.Exit(kw["online_exit"])
                ),
            ):
                result = invoke(
                    "--format=rich", "search", "-f", write(tmp_path, SEARCH)
                )

        assert result.exit_code == 2

    def test_a_search_item_with_no_filter_at_all_is_one(self, tmp_path):
        """The file's fault, not the instance's: nothing was ever asked.

        `run_search_spec` is the real one here, so the status comes from the
        branch that caught it rather than from a remap around the call.
        """
        with patch.object(spec_run, "_instance", return_value=an_instance()):
            result = invoke("--format=rich", "search", "-f", write(tmp_path, NO_FILTER))

        assert result.exit_code == 1
        assert "at least one filter" in result.stderr

    def test_a_bad_set_pair_is_one(self, tmp_path, never_builds_an_app):
        result = invoke(
            "--format=rich", "search", "-f", write(tmp_path, SEARCH), "--set", "nope"
        )

        assert result.exit_code == 1
        assert "NAME=VALUE" in result.stderr
        never_builds_an_app.assert_not_called()


class TestWithIsDeprecated:
    """It still works, it says what replaces it, and it says it once."""

    def test_the_warning_names_the_new_form(self, caplog):
        with caplog.at_level(logging.WARNING):
            warn_with_deprecated("phabfive apply -f FILE")

        assert caplog.record_tuples == [
            (
                "phabfive.cli.spec_flags",
                logging.WARNING,
                "--with is deprecated; run `phabfive apply -f FILE` instead",
            )
        ]

    def test_loading_a_spec_for_with_warns_once(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING):
            spec = cli_search_spec.load_search_spec(write(tmp_path, SEARCH))

        assert spec.envelope.kind is Kind.SEARCH
        assert [one.getMessage() for one in caplog.records] == [
            "--with is deprecated; run `phabfive search -f FILE` instead"
        ]

    def test_with_still_loads_the_spec_it_always_did(self, tmp_path):
        spec = cli_search_spec.load_search_spec(write(tmp_path, SEARCH))

        assert [item["title"] for item in spec.items("search")] == ["Recently touched"]

    def test_with_refuses_a_create_spec_by_name(self, tmp_path):
        """The bug fact 1 named, from the deprecated entry point."""
        with pytest.raises(typer.Exit) as left:
            cli_search_spec.load_search_spec(write(tmp_path, CREATE))

        assert left.value.exit_code == 1

    def test_maniphest_create_with_names_apply(self, tmp_path):
        """Through the command, so the line a user sees is what is asserted.

        `caplog` cannot be used from here: the root callback calls
        `init_logging`, whose `dictConfig` replaces the root handlers and
        with them pytest's. The warning goes where a user reads it instead.
        """
        import phabfive.create
        from phabfive.spec.create import CreateItem, CreatePlan

        path = write(tmp_path, CREATE)
        maniphest = MagicMock()
        planned = CreatePlan(
            items=(
                CreateItem(
                    object_type="task",
                    path="tasks[0]",
                    display={"title": "Set up CI"},
                ),
            )
        )

        from phabfive.cli import maniphest as cli_maniphest

        with patch.object(cli_maniphest, "_get_maniphest_app", return_value=maniphest):
            with patch.object(phabfive.create, "plan_spec", return_value=planned):
                result = invoke(
                    "--format=rich",
                    "maniphest",
                    "create",
                    "--with",
                    path,
                    "--dry-run",
                )

        assert result.exit_code == 0
        warnings = [
            line
            for line in result.stderr.splitlines()
            if "--with is deprecated" in line
        ]
        assert warnings == [
            "WARNING - --with is deprecated; run `phabfive apply -f FILE` instead"
        ]
        # The deprecation warns and gets out of the way: what follows is the
        # preview `phabfive apply -f` prints, because that is what `--with`
        # now is (`phabfive.cli.create_spec`).
        assert "Set up CI" in result.stdout

    def test_maniphest_search_with_names_search(self, tmp_path):
        path = write(tmp_path, SEARCH)
        maniphest = MagicMock()
        maniphest._load_search_config.return_value = []

        from phabfive.cli import maniphest as cli_maniphest

        with patch.object(cli_maniphest, "_get_maniphest_app", return_value=maniphest):
            result = invoke("--format=rich", "maniphest", "search", "--with", path)

        assert result.exit_code == 0
        warnings = [
            line
            for line in result.stderr.splitlines()
            if "--with is deprecated" in line
        ]
        assert warnings == [
            "WARNING - --with is deprecated; run `phabfive search -f FILE` instead"
        ]
        maniphest._load_search_config.assert_called_once_with(path)

    def test_a_cross_type_refusal_names_the_form_that_stays(self, tmp_path):
        """`maniphest search` refuses another type - and says to use `-f`.

        It searches tasks, so a spec item of another type is refused rather
        than run as a task search. The sentence used to send the reader to
        `phabfive project search --with`, which is *also* deprecated and is
        printed directly under the warning telling them to stop using
        `--with`: a reader following both lines migrates twice. It names
        `phabfive search -f FILE`, which is the one command that runs every
        type a spec holds.
        """
        path = write(
            tmp_path,
            "spec: phorge/v1alpha1\n"
            "kind: search\n"
            "searches:\n"
            "  - title: Active projects\n"
            "    type: project\n"
            "    search:\n"
            "      status: active\n",
        )

        maniphest = an_instance()
        # `maniphest search --with` reads the file through this rather than
        # through `load_search_spec`, so the items the loop sees are what it
        # returns; a bare MagicMock would iterate as nothing and never reach
        # the planner that refuses the type.
        maniphest._load_search_config.return_value = [
            {
                "type": "project",
                "search": {"status": "active"},
                "title": "Active projects",
                "description": None,
            }
        ]

        from phabfive.cli import maniphest as cli_maniphest

        with patch.object(cli_maniphest, "_get_maniphest_app", return_value=maniphest):
            result = invoke("--format=rich", "maniphest", "search", "--with", path)

        assert result.exit_code == 1

        refusal = [
            line for line in result.stderr.splitlines() if line.startswith("ERROR:")
        ]

        assert len(refusal) == 1
        assert "phabfive search -f FILE" in refusal[0]
        assert "--with" not in refusal[0]


class TestMonogramsStillWork:
    """`search` is an ordinary command and the shortcut is untouched."""

    def test_a_monogram_still_expands(self):
        assert preprocess_monograms(["phabfive", "P45"]) == [
            "phabfive",
            "paste",
            "show",
            "P45",
        ]

    def test_a_monogram_after_a_format_option_still_expands(self):
        assert preprocess_monograms(["phabfive", "--format", "json", "T7"]) == [
            "phabfive",
            "--format",
            "json",
            "maniphest",
            "show",
            "T7",
        ]

    def test_search_with_a_spec_is_left_alone(self):
        argv = ["phabfive", "search", "--spec", "specs/search/blocked-tasks.yaml"]

        assert preprocess_monograms(list(argv)) == argv
