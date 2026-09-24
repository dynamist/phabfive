# -*- coding: utf-8 -*-
"""Tests for `phabfive spec validate` (#474).

Three things are pinned here, and they are the three the issue asks for.

**`--offline` really is offline.** It has to run in CI or a pre-commit hook
on a machine that has never seen a Phorge token, so it must not construct an
app at all. That is asserted twice: in process, by patching the one function
that would build one and asserting it was never called; and out of process,
by running the real entry point with `PHAB_URL`, `PHAB_TOKEN` and `HOME`
taken away. The in-process half alone proves nothing - `phabfive.core` and
`phabricator` are already imported by the time this module runs.

**Each exit status comes from a fixture.** 0 clean, 1 an offline failure, 2
an online failure, 3 an instance that could not be asked. The last is not in
the issue's table; it is design H.5, and it is here so the issue author can
see it and object.

**The report names everything.** A spec with three mistakes reports three
records, not the first one, and a clean run says so rather than printing
nothing (#395).
"""

# python std lib
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# 3rd party imports
import pytest
from typer.testing import CliRunner

# phabfive imports
import phabfive.spec
from phabfive.cli import app
from phabfive.cli import spec as cli_spec
from phabfive.cli.completers import SPEC_FILE_EXTENSIONS, complete_spec_file
from phabfive.core import Phabfive
from phabfive.exceptions import PhabfiveAPIException, PhabfiveConnectionException
from phabfive.spec.problems import Layer, Problem, Severity

runner = CliRunner()

# Captured at import, before `never_builds_an_app` patches the attribute for
# every test: this is the real function, for the one test that exercises it.
REAL_ONLINE_APP = cli_spec._online_app

REPOSITORY = Path(__file__).resolve().parent.parent

SHIPPED_SEARCH = REPOSITORY / "specs" / "search" / "blocked-tasks.yaml"

# A search spec with three independent mistakes: a misspelled key, a value of
# the wrong type and a time that does not parse. Three, so "names every
# problem, not just the first" has something to say.
THREE_MISTAKES = """\
kind: search
searches:
  - search:
      colum: Backlog
      limit: lots
      created-after: nope!
"""

# Nothing wrong with it, and nothing in it that the online layer would ask
# the instance about either.
CLEAN = """\
kind: search
searches:
  - title: Recently touched
    search:
      limit: 10
"""


@pytest.fixture(autouse=True)
def restore_output_format():
    """Put the class-level output format back, even when a test fails.

    `Phabfive._output_format` is class state that `_setup_output_options`
    writes, so a test that leaves it set changes what every later test in
    the suite renders.
    """
    original = Phabfive._output_format
    try:
        yield
    finally:
        Phabfive._output_format = original


@pytest.fixture(autouse=True)
def never_builds_an_app():
    """Fail loudly if a test reaches the instance without saying it means to.

    Every test that wants the online layer patches this itself; anything
    else touching it is the bug this command exists to avoid.
    """
    with patch.object(
        cli_spec,
        "_online_app",
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


def an_online_app(url="https://phorge.example.com/api/"):
    """An app the online layer can be handed, which asks nothing."""
    instance = MagicMock()
    instance.conf = {"PHAB_URL": url}
    return instance


def a_problem(**overrides):
    """One online problem, of the shape `validate_online` returns."""
    fields = {
        "object": "tasks[0]",
        "field": "assignee",
        "value": "nosuchuser",
        "reason": "No user is called 'nosuchuser'.",
        "code": "unknown-user",
        "layer": Layer.ONLINE,
        "severity": Severity.ERROR,
    }
    fields.update(overrides)
    return Problem(**fields)


class TestOfflineConstructsNothing:
    """`--offline` must work where there is no configuration at all."""

    def test_no_app_is_constructed(self, tmp_path, never_builds_an_app):
        result = invoke(
            "--format=rich", "spec", "validate", write(tmp_path, CLEAN), "--offline"
        )

        assert result.exit_code == 0
        never_builds_an_app.assert_not_called()

    def test_a_failing_spec_constructs_nothing_either(
        self, tmp_path, never_builds_an_app
    ):
        result = invoke(
            "--format=rich",
            "spec",
            "validate",
            write(tmp_path, THREE_MISTAKES),
            "--offline",
        )

        assert result.exit_code == 1
        never_builds_an_app.assert_not_called()

    def test_an_offline_failure_does_not_reach_the_instance_without_offline(
        self, tmp_path, never_builds_an_app
    ):
        """The online layer does not run on a spec already known to be wrong."""
        result = invoke(
            "--format=rich", "spec", "validate", write(tmp_path, THREE_MISTAKES)
        )

        assert result.exit_code == 1
        never_builds_an_app.assert_not_called()


def scrubbed_environment():
    """As little environment as an interpreter needs to start.

    No `PHAB_URL`, no `PHAB_TOKEN`, and a `HOME` that does not exist, so
    `~/.arcrc` and `~/.config/phabfive.yaml` cannot be read. The same
    technique as tests/test_spec_isolation.py, for the same reason: in
    process, everything is already imported and every such assertion passes
    whatever the code does.
    """
    keep = ("PATH", "SYSTEMROOT", "LD_LIBRARY_PATH", "VIRTUAL_ENV")
    environment = {name: os.environ[name] for name in keep if name in os.environ}

    nowhere = str(Path(os.sep, "nonexistent"))

    environment["HOME"] = nowhere
    # Windows names the places a configuration could come from with its own
    # variables, and they are pointed at the same nowhere rather than left
    # out. `phabricator/__init__.py` reads `os.environ['ProgramData']` (:52)
    # and `os.environ['AppData']` (:60) as it imports, so an *absent* one is
    # a KeyError before anything is validated, not an empty directory.
    # Scrubbing them entirely stopped the interpreter starting at all, which
    # is not the thing under test.
    environment["USERPROFILE"] = nowhere
    environment["APPDATA"] = nowhere
    environment["LOCALAPPDATA"] = nowhere
    environment["ProgramData"] = nowhere
    environment["ALLUSERSPROFILE"] = nowhere
    environment["PYTHONPATH"] = str(REPOSITORY)
    environment["TERM"] = "dumb"

    assert "PHAB_URL" not in environment
    assert "PHAB_TOKEN" not in environment

    return environment


def run_cli(*args):
    """Run the real entry point in a fresh interpreter with nothing to read."""
    script = (
        "import sys\n"
        "from phabfive.cli import cli_entrypoint\n"
        f"sys.argv = ['phabfive', *{list(args)!r}]\n"
        "cli_entrypoint()\n"
    )

    return subprocess.run(
        [sys.executable, "-c", script],
        env=scrubbed_environment(),
        cwd=str(REPOSITORY),
        capture_output=True,
        text=True,
        timeout=120,
    )


class TestOfflineWithoutAnyConfiguration:
    """Out of process, with no token, no URL and no home directory."""

    def test_a_shipped_template_validates(self):
        result = run_cli(
            "--format=json", "spec", "validate", str(SHIPPED_SEARCH), "--offline"
        )

        assert result.returncode == 0, (
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )
        assert json.loads(result.stdout) == []

    def test_a_bad_spec_reports_and_exits_one(self, tmp_path):
        path = write(tmp_path, THREE_MISTAKES)

        result = run_cli("--format=json", "spec", "validate", path, "--offline")

        assert result.returncode == 1, (
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )
        assert sorted(one["code"] for one in json.loads(result.stdout)) == [
            "bad-time",
            "unknown-key",
            "wrong-type",
        ]

    def test_nothing_asked_for_a_token(self, tmp_path):
        """The proof that no configuration was wanted: no mention of one."""
        result = run_cli(
            "--format=json", "spec", "validate", str(SHIPPED_SEARCH), "--offline"
        )

        assert "PHAB_TOKEN" not in result.stderr
        assert "PHAB_URL" not in result.stderr
        assert result.stderr.strip().endswith("no problems found (offline).")


class TestExitStatus:
    """One fixture per code in the table, including the one design H.5 adds."""

    def test_clean_is_zero(self, tmp_path):
        with patch.object(cli_spec, "_online_app", return_value=an_online_app()):
            with patch.object(phabfive.spec, "validate_online", return_value=[]):
                result = invoke(
                    "--format=rich", "spec", "validate", write(tmp_path, CLEAN)
                )

        assert result.exit_code == 0

    def test_an_offline_failure_is_one(self, tmp_path):
        result = invoke(
            "--format=rich",
            "spec",
            "validate",
            write(tmp_path, THREE_MISTAKES),
            "--offline",
        )

        assert result.exit_code == 1

    def test_a_file_that_cannot_be_read_is_one(self, tmp_path):
        result = invoke(
            "--format=rich",
            "spec",
            "validate",
            str(tmp_path / "nothing-here.yaml"),
            "--offline",
        )

        assert result.exit_code == 1
        assert "nothing-here.yaml" in result.stdout

    def test_an_online_failure_is_two(self, tmp_path):
        with patch.object(cli_spec, "_online_app", return_value=an_online_app()):
            with patch.object(
                phabfive.spec, "validate_online", return_value=[a_problem()]
            ):
                result = invoke(
                    "--format=rich", "spec", "validate", write(tmp_path, CLEAN)
                )

        assert result.exit_code == 2
        assert "unknown-user" in result.stdout

    @pytest.mark.parametrize(
        "error",
        [
            PhabfiveConnectionException("connection refused"),
            PhabfiveAPIException("ERR-CONDUIT-CORE", "nope"),
        ],
    )
    def test_an_instance_that_cannot_be_asked_is_three(self, tmp_path, error):
        """Design H.5: not a bad spec, so not exit 2."""
        with patch.object(cli_spec, "_online_app", return_value=an_online_app()):
            with patch.object(phabfive.spec, "validate_online", side_effect=error):
                result = invoke(
                    "--format=rich", "spec", "validate", write(tmp_path, CLEAN)
                )

        assert result.exit_code == 3
        assert "unreachable" in result.stdout
        assert "https://phorge.example.com/api/" in result.stdout

    def test_a_failure_to_construct_is_three_too(self, tmp_path):
        with patch.object(
            cli_spec,
            "_online_app",
            side_effect=PhabfiveConnectionException("no route to host"),
        ):
            result = invoke("--format=rich", "spec", "validate", write(tmp_path, CLEAN))

        assert result.exit_code == 3

    def test_the_instance_is_named_even_when_construction_failed(self, tmp_path):
        """There is no app to ask for the URL, so the configuration is asked.

        `read_config` is a classmethod and answers with a (conf, explicit)
        pair, not a mapping - getting that wrong turned a clean exit 3 into
        an AttributeError traceback.
        """
        configuration = ({"PHAB_URL": "https://named.example.com/api/"}, True)

        with patch.object(
            cli_spec,
            "_online_app",
            side_effect=PhabfiveConnectionException("no route to host"),
        ):
            with patch.object(Phabfive, "read_config", return_value=configuration):
                result = invoke(
                    "--format=json", "spec", "validate", write(tmp_path, CLEAN)
                )

        assert result.exit_code == 3

        [record] = json.loads(result.stdout)

        assert record["value"] == "https://named.example.com/api/"
        assert "https://named.example.com/api/" in record["reason"]

    def test_no_configuration_at_all_is_three_with_a_record(self, tmp_path):
        """The offline layer passed and the online one never ran.

        Exit 1 would tell a CI job the spec is broken when the machine was,
        and a --format=json reader would get an unparseable empty stdout -
        which is exactly what the `unreadable`/`unreachable` records exist
        to prevent.
        """
        with patch.object(
            cli_spec,
            "_online_app",
            side_effect=cli_spec.Unconfigured("PHAB_TOKEN is not set"),
        ):
            result = invoke("--format=json", "spec", "validate", write(tmp_path, CLEAN))

        assert result.exit_code == 3

        [record] = json.loads(result.stdout)

        assert record["code"] == "unreachable"
        assert record["layer"] == "online"
        assert record["object"] == "$"
        assert "PHAB_TOKEN" in record["reason"]

    def test_declining_the_setup_wizard_is_unconfigured(self):
        """`_online_app` turns a declined wizard into the signal, not Exit(1)."""
        from phabfive.exceptions import PhabfiveConfigException

        with patch(
            "phabfive.cli.apps.new_app",
            side_effect=PhabfiveConfigException("PHAB_URL is not set"),
        ):
            with patch("phabfive.setup.offer_setup_on_error", return_value=False):
                with pytest.raises(cli_spec.Unconfigured):
                    REAL_ONLINE_APP()

    def test_an_unnameable_instance_still_exits_three(self, tmp_path):
        """Failing to name the instance is not a second failure."""
        with patch.object(
            cli_spec,
            "_online_app",
            side_effect=PhabfiveConnectionException("no route to host"),
        ):
            with patch.object(
                Phabfive, "read_config", side_effect=RuntimeError("no config at all")
            ):
                result = invoke(
                    "--format=json", "spec", "validate", write(tmp_path, CLEAN)
                )

        assert result.exit_code == 3

        [record] = json.loads(result.stdout)

        assert record["value"] is None
        assert record["code"] == "unreachable"

    def test_a_malformed_set_does_not_claim_the_spec_is_clean(self, tmp_path):
        result = invoke(
            "--format=rich",
            "spec",
            "validate",
            write(tmp_path, CLEAN),
            "--offline",
            "--set",
            "sprint",
        )

        assert result.exit_code == 1
        assert "NAME=VALUE" in result.stderr


class TestTheReport:
    """Every problem, in every format, and never silence."""

    def test_json_names_every_problem_not_the_first(self, tmp_path):
        result = invoke(
            "--format=json",
            "spec",
            "validate",
            write(tmp_path, THREE_MISTAKES),
            "--offline",
        )

        records = json.loads(result.stdout)

        assert len(records) == 3
        assert sorted(one["code"] for one in records) == [
            "bad-time",
            "unknown-key",
            "wrong-type",
        ]

    def test_json_round_trips_the_whole_record(self, tmp_path):
        """What lands on stdout is what Problem.as_record() built."""
        path = write(tmp_path, THREE_MISTAKES)

        result = invoke("--format=json", "spec", "validate", path, "--offline")

        spec = phabfive.spec.load_spec(path)
        expected = [one.as_record() for one in phabfive.spec.validate_offline(spec)]

        assert json.loads(result.stdout) == expected

    def test_jsonl_agrees_with_json(self, tmp_path):
        """The standing rule: jsonl parsed line by line is the json array."""
        path = write(tmp_path, THREE_MISTAKES)

        as_json = invoke("--format=json", "spec", "validate", path, "--offline").stdout
        as_jsonl = invoke(
            "--format=jsonl", "spec", "validate", path, "--offline"
        ).stdout

        assert [
            json.loads(line) for line in as_jsonl.splitlines() if line
        ] == json.loads(as_json)

    def test_a_clean_json_run_is_an_empty_array_not_silence(self, tmp_path):
        result = invoke(
            "--format=json", "spec", "validate", write(tmp_path, CLEAN), "--offline"
        )

        assert result.exit_code == 0
        assert json.loads(result.stdout) == []
        assert "no problems found" in result.stderr

    def test_a_clean_human_run_says_so(self, tmp_path):
        result = invoke(
            "--format=rich", "spec", "validate", write(tmp_path, CLEAN), "--offline"
        )

        assert result.exit_code == 0
        assert "no problems found (offline)." in result.stdout

    def test_a_clean_run_of_both_layers_says_which_ran(self, tmp_path):
        with patch.object(cli_spec, "_online_app", return_value=an_online_app()):
            with patch.object(phabfive.spec, "validate_online", return_value=[]):
                result = invoke(
                    "--format=rich", "spec", "validate", write(tmp_path, CLEAN)
                )

        assert "no problems found (offline and online)." in result.stdout

    def test_the_human_report_groups_by_object(self, tmp_path):
        result = invoke(
            "--format=rich",
            "spec",
            "validate",
            write(tmp_path, THREE_MISTAKES),
            "--offline",
        )

        lines = [line for line in result.stdout.splitlines() if line]

        # One heading for the one object, then its three fields indented
        assert lines[0] == "searches[0]"
        assert all(line.startswith("  ") for line in lines[1:4])
        assert lines[-1].endswith("3 errors, 0 warnings")

    def test_a_machine_format_keeps_prose_off_stdout(self, tmp_path):
        result = invoke(
            "--format=json",
            "spec",
            "validate",
            write(tmp_path, THREE_MISTAKES),
            "--offline",
        )

        # stdout alone parses, which is the whole point of --format=json
        assert len(json.loads(result.stdout)) == 3
        assert "3 errors" in result.stderr

    def test_yaml_emits_the_same_records(self, tmp_path):
        from io import StringIO

        from ruamel.yaml import YAML

        path = write(tmp_path, THREE_MISTAKES)

        as_yaml = invoke("--format=yaml", "spec", "validate", path, "--offline").stdout
        as_json = invoke("--format=json", "spec", "validate", path, "--offline").stdout

        assert YAML().load(StringIO(as_yaml)) == json.loads(as_json)

    def test_both_layers_report_together(self, tmp_path):
        """An offline warning and an online error come back in one report."""
        warning = Problem(
            object="metadata",
            field="nickname",
            value="x",
            reason="Unknown metadata key 'nickname'.",
            code="unknown-key",
            layer=Layer.OFFLINE,
            severity=Severity.WARNING,
        )

        with patch.object(cli_spec, "_online_app", return_value=an_online_app()):
            with patch.object(
                phabfive.spec, "validate_offline", return_value=[warning]
            ):
                with patch.object(
                    phabfive.spec, "validate_online", return_value=[a_problem()]
                ):
                    result = invoke(
                        "--format=json", "spec", "validate", write(tmp_path, CLEAN)
                    )

        records = json.loads(result.stdout)

        assert [one["layer"] for one in records] == ["offline", "online"]
        # A warning does not make the offline layer fail, so the online one ran
        assert result.exit_code == 2


class TestVariables:
    """`--set` supplies what the spec declares but does not carry."""

    WITH_A_VARIABLE = """\
kind: create
tasks:
  - title: "Sprint {{ sprint }} planning"
"""

    def test_an_undefined_variable_fails(self, tmp_path):
        result = invoke(
            "--format=json",
            "spec",
            "validate",
            write(tmp_path, self.WITH_A_VARIABLE),
            "--offline",
        )

        assert result.exit_code == 1
        assert [one["code"] for one in json.loads(result.stdout)] == [
            "undefined-variable"
        ]

    def test_set_supplies_it(self, tmp_path):
        result = invoke(
            "--format=json",
            "spec",
            "validate",
            write(tmp_path, self.WITH_A_VARIABLE),
            "--offline",
            "--set",
            "sprint=42",
        )

        assert result.exit_code == 0
        assert json.loads(result.stdout) == []

    def test_the_last_set_of_a_name_wins(self, tmp_path):
        result = invoke(
            "--format=json",
            "spec",
            "validate",
            write(tmp_path, self.WITH_A_VARIABLE),
            "--offline",
            "--set",
            "sprint=1",
            "--set",
            "sprint=2",
        )

        assert result.exit_code == 0

    def test_a_value_may_hold_an_equals_sign(self):
        assert cli_spec._parse_overrides(["query=a=b"]) == {"query": "a=b"}

    TEMPLATED_REFERENCE = """\
kind: create
variables:
  who:
tasks:
  - title: Something
    assignment: "@{{ who }}"
"""

    def test_the_online_layer_sees_what_set_supplied(self, tmp_path):
        """`--set` renders the spec, it does not only feed the offline pass.

        `index_references` skips any value still holding `{{`, so handing
        the *unrendered* spec to the online layer would look up nothing
        while the report said "no problems found (offline and online)" and
        exited 0. A clean verdict for a check that never ran is the one
        thing neither layer may produce.
        """
        seen = {}

        def remember(spec, app, **kwargs):
            seen["spec"] = spec
            return []

        with patch.object(cli_spec, "_online_app", return_value=an_online_app()):
            with patch.object(phabfive.spec, "validate_online", remember):
                result = invoke(
                    "--format=json",
                    "spec",
                    "validate",
                    write(tmp_path, self.TEMPLATED_REFERENCE),
                    "--set",
                    "who=alice",
                )

        assert result.exit_code == 0
        assert seen["spec"].rendered is True
        assert seen["spec"].body["tasks"][0]["assignment"] == "@alice"

    def test_a_templated_reference_that_does_not_resolve_still_exits_two(
        self, tmp_path
    ):
        """The consequence of the above, from the caller's end."""
        with patch.object(cli_spec, "_online_app", return_value=an_online_app()):
            with patch.object(
                phabfive.spec,
                "validate_online",
                return_value=[a_problem(value="@nosuchuser")],
            ):
                result = invoke(
                    "--format=json",
                    "spec",
                    "validate",
                    write(tmp_path, self.TEMPLATED_REFERENCE),
                    "--set",
                    "who=nosuchuser",
                )

        assert result.exit_code == 2
        assert [one["code"] for one in json.loads(result.stdout)] == ["unknown-user"]


class TestKind:
    """`--kind` says what a file is when it cannot be inferred."""

    def test_a_file_that_says_nothing_is_refused(self, tmp_path):
        path = write(tmp_path, "title: Just a banner\n")

        result = invoke("--format=rich", "spec", "validate", path, "--offline")

        assert result.exit_code == 1

    def test_kind_search_makes_it_readable(self, tmp_path):
        path = write(tmp_path, "title: Just a banner\n")

        result = invoke(
            "--format=rich", "spec", "validate", path, "--offline", "--kind", "search"
        )

        assert result.exit_code == 0


class TestSpecKindTracksTheLibrary:
    """`--kind` is a second spelling of `phabfive.spec.envelope.Kind`."""

    def test_the_two_enumerations_name_the_same_kinds(self):
        from phabfive.spec.envelope import Kind

        assert {one.value for one in cli_spec.SpecKind} == {one.value for one in Kind}

    def test_the_document_path_is_the_one_the_layers_write(self):
        """`$` is spelled in three places; they have to be the same `$`."""
        from phabfive.spec.validate import DOCUMENT

        assert cli_spec.DOCUMENT == DOCUMENT

    def test_the_codes_the_command_invents_are_documented_ones(self):
        from phabfive.spec.problems import CODES

        assert cli_spec.CODE_UNREADABLE in CODES
        assert cli_spec.CODE_UNREACHABLE in CODES


class TestTheGroupIsRegistered:
    """`phabfive spec` behaves like every other group."""

    def test_bare_group_shows_help_and_exits_two(self):
        result = invoke("spec")

        assert result.exit_code == 2
        assert "validate" in result.output

    def test_the_agent_footer_is_there(self):
        """Out of process: under CliRunner typer renders help with rich,
        which never calls format_epilog. tests/test_agent_help_footer.py
        says the same thing for every other group, and it is not a file
        this issue owns - so "spec" wants adding to its GROUPS list.
        """
        result = run_cli("spec", "--help")

        assert "Are you an AI?" in result.stdout + result.stderr

    def test_the_root_help_lists_it(self):
        """The row, not the word.

        "spec" appears in the root help whether or not the group is
        registered - "Edit monograms (routes to app-specific edit command)"
        carries it already - so the assertion is the group's own help text
        and its presence among the registered commands.
        """
        from typer.main import get_command

        result = invoke("--help")

        assert "Work with Phorge spec files" in result.output
        assert "spec" in get_command(app).commands


class TestFileCompletion:
    """The FILE argument completes, filtered to what the loader reads."""

    def test_only_spec_suffixes_are_offered(self, tmp_path):
        for name in ("a.yaml", "b.yml", "c.json", "d.jsonl", "e.toml", "f.ndjson"):
            (tmp_path / name).touch()
        for name in ("notes.md", "image.png", "Makefile"):
            (tmp_path / name).touch()

        offered = complete_spec_file(f"{tmp_path}{os.sep}")

        assert sorted(Path(one).name for one in offered) == [
            "a.yaml",
            "b.yml",
            "c.json",
            "d.jsonl",
            "e.toml",
            "f.ndjson",
        ]

    def test_every_offered_suffix_is_one_the_loader_detects(self, tmp_path):
        from phabfive.spec.loader import detect_format

        for extension in SPEC_FILE_EXTENSIONS:
            assert detect_format(tmp_path / f"spec.{extension}") is not None

    def test_the_suffixes_are_exactly_what_the_loader_knows(self):
        """Pin the drift: a new format in the loader must be offered too."""
        from phabfive.spec.loader import _EXTENSIONS

        assert set(SPEC_FILE_EXTENSIONS) == set(_EXTENSIONS)

    def test_a_prefix_narrows_the_offer(self, tmp_path):
        (tmp_path / "alpha.yaml").touch()
        (tmp_path / "beta.yaml").touch()

        offered = complete_spec_file(f"{tmp_path}{os.sep}al")

        assert [Path(one).name for one in offered] == ["alpha.yaml"]

    def test_a_directory_is_offered_so_completion_walks_on(self, tmp_path):
        (tmp_path / "specs").mkdir()

        offered = complete_spec_file(f"{tmp_path}{os.sep}")

        assert offered == [f"{tmp_path}{os.sep}specs{os.sep}"]

    def test_a_dotfile_is_hidden_until_a_dot_is_typed(self, tmp_path):
        (tmp_path / ".hidden.yaml").touch()

        assert complete_spec_file(f"{tmp_path}{os.sep}") == []
        assert complete_spec_file(f"{tmp_path}{os.sep}.") == [
            f"{tmp_path}{os.sep}.hidden.yaml"
        ]

    def test_an_unreadable_directory_completes_to_nothing(self, tmp_path):
        """A broken TAB is worse than an unhelpful one."""
        assert complete_spec_file(f"{tmp_path}{os.sep}nope{os.sep}") == []

    def _file_argument(self):
        from typer.main import get_command

        # Through the root app: a Typer with one command and no callback
        # collapses into that command when it is built on its own.
        group = get_command(app).commands["spec"]
        command = group.commands["validate"]
        [argument] = [one for one in command.params if one.name == "file"]
        return argument

    def test_the_argument_carries_the_completer(self):
        """`shell_complete is not None` would be vacuously true.

        It is a bound method on every click Parameter, so `--kind`, which
        carries no completer at all, satisfies that assertion too. A custom
        completer lands in `_custom_shell_complete`, and the proof it is
        wired is driving the real path and getting spec files back.
        """
        argument = self._file_argument()

        assert argument._custom_shell_complete is not None

    def test_tab_on_the_argument_offers_spec_files(self, tmp_path, monkeypatch):
        import click

        (tmp_path / "blocked-tasks.yaml").touch()
        (tmp_path / "notes.md").touch()
        monkeypatch.chdir(tmp_path)

        argument = self._file_argument()
        context = click.Context(click.Command("validate"))

        offered = argument.shell_complete(context, "blo")

        assert [one.value for one in offered] == ["blocked-tasks.yaml"]
