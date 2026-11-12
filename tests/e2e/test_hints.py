# -*- coding: utf-8 -*-
"""`phabfive hints` and the create reminder, in a repository on a live Phorge."""

# python std lib
import json
import subprocess
import sys

# 3rd party imports
import pytest

from tests.e2e.test_project import DEVELOPMENT_COLUMNS


@pytest.fixture
def repository(tmp_path, live_env):
    """A checkout whose .arcconfig names the seeded #development board."""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".arcconfig").write_text(
        json.dumps(
            {
                "phabricator.uri": live_env["PHORGE_HOST"],
                "phabfive": {
                    "tags": {
                        "workboard": "#development",
                        "hints": {
                            "Architecture": "Changes to module boundaries",
                            "no-such-project-anywhere": None,
                        },
                    }
                },
            }
        )
    )

    def run(*args):
        return subprocess.run(
            [
                sys.executable,
                "-c",
                "from phabfive.cli import cli_entrypoint; cli_entrypoint()",
                *args,
            ],
            cwd=tmp_path,
            env=live_env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    return run


def test_hints_show_the_board_and_each_hint(repository):
    result = repository("--format=json", "hints")

    assert result.returncode == 0, result.stderr
    [record] = json.loads(result.stdout)

    # The e2e environment names the instance, and the environment wins
    assert record["Instance"]["From"] == "PHORGE_HOST"
    assert record["Repository"]["Config"].endswith(".arcconfig")
    assert "Instances" not in record
    board = record["Workboard"]
    assert board["Project"] == "Development"
    assert board["Link"].endswith("/")
    assert set(DEVELOPMENT_COLUMNS) <= set(board["Columns"])
    architecture, stale = record["Tags"]
    assert architecture["Project"] == "Architecture"
    assert architecture["Description"]
    assert architecture["Note"] == "Changes to module boundaries"
    assert stale["Problem"].startswith("No project")
    assert "no-such-project-anywhere" in result.stderr


def test_a_dry_run_create_names_the_unmet_hints(repository):
    result = repository(
        "maniphest", "create", "e2e hint reminder", "--description=x", "--dry-run"
    )

    assert result.returncode == 0, result.stderr
    assert "Tagging with workboard #development" in result.stderr
    assert (
        "Not tagged: Architecture, no-such-project-anywhere (repository hints"
        in result.stderr
    )
