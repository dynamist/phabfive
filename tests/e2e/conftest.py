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
import time
import uuid

# 3rd party imports
import pytest
import requests


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


@pytest.fixture(scope="session")
def phabfive_raw(live_env):
    """Run the CLI and hand back the whole result, exit code included.

    The `phabfive` fixture asserts success, which is the right default and
    no use at all for the commands whose contract is the exit code - a
    repository that does not exist is a failed lookup, and a partial
    result still fails.
    """

    def run(*args):
        return subprocess.run(
            [
                sys.executable,
                "-c",
                "from phabfive.cli import cli_entrypoint; cli_entrypoint()",
                *args,
            ],
            env=live_env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    return run


@pytest.fixture
def create_task(phabfive):
    """Create a task with a unique title, return (monogram, title)."""

    def create(*args):
        title = f"E2E test {uuid.uuid4().hex[:8]}"
        output = phabfive("maniphest", "create", title, "--yes", *args)
        match = re.search(r"/(T\d+)\b", output)
        assert match, f"no task link in output: {output}"
        return match.group(1), title

    return create


@pytest.fixture
def create_repository(phabfive, conduit):
    """Create a repository with a unique short name, and answer with it.

    Anything that mutates a repository needs one of its own. The seeded
    repositories are read by a dozen tests around this one, and a policy is
    exactly the kind of change that would make one of them answer
    differently - or, set wrong, hide the repository from them entirely.

    The short name is the handle because `repo create` prints the change it
    made rather than a monogram, and `repo show` and `repo edit` both take a
    short name.

    Deactivated afterwards, so repeated runs do not pile up repositories in
    `repo list` - Conduit has no way to delete one. The wait before that is
    not optional: the pull daemon does not import an inactive repository, so
    deactivating one that has not finished leaves `isImporting` true for
    good, and `settled_repositories` then fails for every later run on the
    instance rather than for the run that caused it.
    """
    created = []

    def create():
        name = f"e2e-{uuid.uuid4().hex[:8]}"
        phabfive("diffusion", "repo", "create", name, "--yes")
        created.append(name)
        return name

    yield create

    for name in created:
        deadline = time.monotonic() + 60

        while time.monotonic() < deadline:
            repos = conduit(
                "diffusion.repository.search",
                **{"constraints[shortNames][0]": name},
            )["data"]

            if not any(repo["fields"].get("isImporting") for repo in repos):
                break

            time.sleep(2)

        phabfive("diffusion", "repo", "edit", name, "--status=inactive", "--yes")


@pytest.fixture(scope="session")
def conduit(live_env):
    """Call a Conduit method directly, for facts the CLI cannot report."""

    def call(method, **params):
        response = requests.post(
            live_env["PHAB_URL"].rstrip("/") + "/" + method,
            data={"api.token": live_env["PHAB_TOKEN"], **params},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        assert not payload["error_code"], (
            f"{method} failed: {payload['error_code']} {payload['error_info']}"
        )
        return payload["result"]

    return call


@pytest.fixture(scope="session")
def settled_repositories(conduit):
    """Wait until no repository is still importing.

    Phorge marks a hosted repository as importing until the daemons have
    finished its initial import, and the seeder does not wait for that -
    it has no reason to, refs exist before the daemons start. A test that
    runs the same command once per format and compares the results can
    therefore see `Importing` flip between two of its own invocations,
    which reads as the formats disagreeing when what actually changed was
    the repository. Asking for this first makes the four runs comparable.
    """
    deadline = time.monotonic() + 180

    while True:
        repos = conduit("diffusion.repository.search")["data"]

        if not any(repo["fields"].get("isImporting") for repo in repos):
            return

        assert time.monotonic() < deadline, (
            "repositories were still importing after 180s: "
            + ", ".join(
                f"R{repo['id']}" for repo in repos if repo["fields"].get("isImporting")
            )
        )
        time.sleep(2)


@pytest.fixture(scope="session")
def space_name(conduit):
    """What this instance calls a space monogram.

    A space's name is not something a test can hard-code. The monogram
    is positional: S3 is whatever sits third in
    phorge/seed/data/spaces.json, which carries no ids, so inserting or
    reordering a space silently changes what S3 means. An instance also
    keeps its MariaDB volume across `make up`, so deployed data can
    predate the seed file it is compared against.

    Asking the instance keeps a test about the --space flag from failing
    over which data happens to be there.
    """

    def name(monogram):
        result = conduit("phid.lookup", **{"names[0]": monogram})
        assert monogram in result, f"{monogram} does not exist on this instance"
        return result[monogram]["name"]

    return name
