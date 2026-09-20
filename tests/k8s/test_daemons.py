# -*- coding: utf-8 -*-
"""The daemons run as the user that serves Conduit.

Regression test for #369. `phd start` as root creates /app/repo/<id> owned
by root while Apache serves Conduit as www-data, and git then refuses every
ref query in that repository with "detected dubious ownership", which made
diffusion.branchquery, diffusion.tagsquery and diffusion.refsquery unusable.

This asserts the invariant rather than querying a repository on purpose: it
holds on an instance with no repositories at all, where a test that queried
one would pass by vacuously skipping the git path. The `repositories` seed
module now creates two, and tests/k8s/test_seed.py queries their refs, which
is the same bug seen from the other end.
"""

# 3rd party imports
import pytest


def _processes(kubectl, pod):
    """(user, command) for every process in the phorge container."""
    output = kubectl(
        "exec", pod, "--", "ps", "-eo", "user:32,args", "--no-headers"
    ).stdout
    processes = []
    for line in output.splitlines():
        user, _, command = line.strip().partition(" ")
        if user:
            processes.append((user, command.strip()))
    return processes


@pytest.fixture(scope="module")
def phorge_pod(kubectl):
    name = kubectl(
        "get",
        "pod",
        "-l",
        "app.kubernetes.io/name=phorge",
        "-o",
        "jsonpath={.items[0].metadata.name}",
    ).stdout.strip()
    assert name, "no phorge pod is running"
    return name


@pytest.fixture(scope="module")
def webserver_user(kubectl, phorge_pod):
    """The user Apache forks its workers as, which is what runs Conduit.

    The master process stays root to hold port 80, so it is excluded.
    """
    users = {
        user
        for user, command in _processes(kubectl, phorge_pod)
        if "apache2" in command and user != "root"
    }
    assert len(users) == 1, f"expected one Apache worker user, got {sorted(users)}"
    return users.pop()


def test_daemons_run_as_the_webserver_user(kubectl, phorge_pod, webserver_user):
    daemons = {
        user
        for user, command in _processes(kubectl, phorge_pod)
        if "phd-daemon" in command or "exec_daemon.php" in command
    }
    assert daemons, "no Phorge daemons are running"
    assert daemons == {webserver_user}, (
        f"daemons run as {sorted(daemons)} but Conduit runs as "
        f"{webserver_user}; git will refuse the repositories they create"
    )


def test_repository_storage_is_owned_by_the_webserver_user(
    kubectl, phorge_pod, webserver_user
):
    """Nothing git checks for ownership may belong to another user.

    Depth 2 is /app/repo and /app/repo/<id>, which is what git tests before
    it will read a repository, and keeps this off every loose object.
    """
    output = kubectl(
        "exec",
        phorge_pod,
        "--",
        "find",
        "/app/repo",
        "-maxdepth",
        "2",
        "-not",
        "-user",
        webserver_user,
    ).stdout
    assert not output.strip(), (
        f"not owned by {webserver_user}, so git will refuse them:\n{output}"
    )
