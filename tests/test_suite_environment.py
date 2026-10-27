# -*- coding: utf-8 -*-
"""The suite's result must not depend on the developer's terminal (#414).

These tests assert on text that rich and typer render. Before tests/conftest.py
pinned the environment, a shell with FORCE_COLOR set, or TERM=dumb, or a typer
imported before phabfive.cli, failed some of them when run on their own and
passed them in a full run - and CI's environment happened to pass all of them,
so they only ever failed locally. Each environment below is one that did.
"""

# python std lib
import os
import subprocess
import sys
from pathlib import Path

# 3rd party imports
import pytest

ROOT = Path(__file__).resolve().parent.parent

RENDERED_TEXT_TESTS = [
    "tests/test_diffusion.py::TestRepoListCli::test_the_url_alias_is_hidden_from_the_help",
    "tests/test_diffusion.py::TestUriEditIoCli::test_the_help_lists_the_values_phorge_takes",
    "tests/test_diffusion.py::TestRepoShowRecord::test_unknown_hosting_reads_as_unreported_in_every_format",
    "tests/test_cache_cli.py::TestInfo::test_mentions_accounts_cached_under_another_token",
    "tests/test_passphrase_records.py::TestShowCommand::test_rich_and_tree_say_the_secret_is_hidden",
]


@pytest.mark.parametrize(
    "environment",
    [
        {"FORCE_COLOR": "1", "TERM": "xterm-256color"},
        {"FORCE_COLOR": "1", "TERM": "dumb"},
        {"TTY_COMPATIBLE": "1", "TERM": "xterm-256color"},
        {"TYPER_USE_RICH": "1", "FORCE_COLOR": "1", "TERM": "dumb"},
    ],
    ids=lambda environment: ",".join(f"{k}={v}" for k, v in environment.items()),
)
def test_rendered_text_tests_pass_whatever_the_terminal(environment):
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    env.update(environment)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--color=no",
            *RENDERED_TEXT_TESTS,
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
