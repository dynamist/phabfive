# -*- coding: utf-8 -*-
"""Fixtures for running the phabfive CLI against a live Phorge.

Run with `make test-e2e`, which deploys nothing: start Phorge with
`make up` first. The tests create real tasks, only point them at a
disposable instance.
"""

# python std lib
import json
import os
import re
import subprocess
import sys
import uuid

# 3rd party imports
import pytest


@pytest.fixture(scope="session")
def live_env():
    if not os.environ.get("PHAB_URL") or not os.environ.get("PHAB_TOKEN"):
        pytest.skip("PHAB_URL and PHAB_TOKEN must point to a disposable Phorge")
    env = dict(os.environ, PHAB_CACHE="0", NO_COLOR="1")
    return env


@pytest.fixture(scope="session")
def phabfive(live_env):
    """Run the CLI, return stdout. json=True parses --format json output."""

    def run(*args, json_output=False):
        cmd = [
            sys.executable,
            "-c",
            "from phabfive.cli import cli_entrypoint; cli_entrypoint()",
        ]
        if json_output:
            cmd += ["--format", "json"]
        result = subprocess.run(
            [*cmd, *args], env=live_env, capture_output=True, text=True, timeout=120
        )
        assert result.returncode == 0, (
            f"phabfive {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}"
        )
        return json.loads(result.stdout) if json_output else result.stdout

    return run


@pytest.fixture
def create_task(phabfive):
    """Create a task with a unique title, return (monogram, title)."""

    def create(*args):
        title = f"E2E test {uuid.uuid4().hex[:8]}"
        output = phabfive("maniphest", "create", title, "--force", *args)
        match = re.search(r"/(T\d+)\b", output)
        assert match, f"no task link in output: {output}"
        return match.group(1), title

    return create
