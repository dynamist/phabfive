# -*- coding: utf-8 -*-
"""git over SSH, through the sshd container and `kubectl port-forward`.

Reached the way `make ssh-forward` reaches it, with the admin key `make deploy`
generated and the sshkeys seed module registered.
"""

# python std lib
import os
import socket
import subprocess
import time
import uuid

# 3rd party imports
import pytest

from tests.k8s.conftest import KUBE_CONTEXT, NAMESPACE, ROOT

ADMIN_KEY = ROOT / "k8s/base/ssh/admin"
SSH_HOST = "phorge.localhost"
SSH_PORT = 2222


@pytest.fixture(scope="module")
def ssh_port():
    """A local port forwarded to svc/ssh for as long as the module runs."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    forward = subprocess.Popen(
        [
            "kubectl",
            "--context",
            KUBE_CONTEXT,
            "-n",
            NAMESPACE,
            "port-forward",
            "--address",
            "127.0.0.1",
            "svc/ssh",
            f"{port}:22",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        # It prints "Forwarding from ..." once it listens
        assert forward.stdout is not None
        line = forward.stdout.readline()
        assert "Forwarding from" in line, line
        yield port
    finally:
        forward.terminate()
        forward.wait(timeout=10)


@pytest.fixture
def git(ssh_port, tmp_path):
    """Run git with ssh pointed at the forwarded port, as `key` (the admin's by default)."""
    known_hosts = tmp_path / "known_hosts"

    def run(*args, key=ADMIN_KEY, check=True, cwd=None):
        command = (
            f"ssh -i {key} -o IdentitiesOnly=yes -o BatchMode=yes"
            f" -o UserKnownHostsFile={known_hosts} -o StrictHostKeyChecking=accept-new"
        )
        env = {**os.environ, "GIT_SSH_COMMAND": command, "GIT_TERMINAL_PROMPT": "0"}
        return subprocess.run(
            ["git", *args],
            check=check,
            capture_output=True,
            text=True,
            env=env,
            cwd=cwd,
            timeout=120,
        )

    return run


def clone_uri(ssh_port, short_name):
    return f"ssh://git@127.0.0.1:{ssh_port}/source/{short_name}.git"


@pytest.fixture
def hosted_repository(conduit):
    """An empty hosted repository of this test's own, active and imported.

    Pushing to a seeded one would change what the seed tests expect, and its
    branch could not be deleted again: dangerous change protection refuses
    it. Deactivated afterwards, like the e2e `create_repository`, so runs do
    not pile up repositories, and only once imported, since an inactive one
    never finishes importing.
    """
    name = f"ssh-{uuid.uuid4().hex[:8]}"
    result = conduit(
        "diffusion.repository.edit",
        transactions=[
            {"type": "vcs", "value": "git"},
            {"type": "name", "value": name},
            {"type": "shortName", "value": name},
            {"type": "status", "value": "active"},
        ],
    )
    phid = result["object"]["phid"]

    conduit("diffusion.looksoon", repositories=[phid])
    deadline = time.monotonic() + 60
    while True:
        repo = conduit("diffusion.repository.search", constraints={"phids": [phid]})[
            "data"
        ][0]
        if not repo["fields"].get("isImporting"):
            break
        assert time.monotonic() < deadline, f"{name} still importing"
        time.sleep(2)

    yield repo
    conduit(
        "diffusion.repository.edit",
        objectIdentifier=phid,
        transactions=[{"type": "status", "value": "inactive"}],
    )


def test_ssh_lists_the_refs_of_a_seeded_repository(git, ssh_port, seed):
    record = next(r for r in seed["repositories"]["repositories"] if r.get("branches"))
    output = git("ls-remote", clone_uri(ssh_port, record["shortName"])).stdout

    refs = {line.split("\t")[1] for line in output.splitlines()}
    expected = {f"refs/heads/{branch['name']}" for branch in record["branches"]} | {
        f"refs/tags/{commit['tag']}"
        for branch in record["branches"]
        for commit in branch["commits"]
        if "tag" in commit
    }
    assert expected <= refs


def test_ssh_refuses_a_key_phorge_does_not_know(git, ssh_port, seed, tmp_path):
    key = tmp_path / "stranger"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True
    )
    record = seed["repositories"]["repositories"][0]

    result = git(
        "ls-remote", clone_uri(ssh_port, record["shortName"]), key=key, check=False
    )

    assert result.returncode != 0
    assert "Permission denied" in result.stderr


def test_ssh_push_runs_the_commit_hook(
    git, ssh_port, conduit, hosted_repository, tmp_path
):
    """A push goes through ssh-exec, sudo to the daemon user and Phorge's commit hook.

    The hook is what tells Phorge about the new branch, so seeing it over
    Conduit right away proves the whole write path, not only that git ran.
    """
    work = tmp_path / "work"
    short_name = hosted_repository["fields"]["shortName"]
    git("clone", clone_uri(ssh_port, short_name), str(work))
    (work / "README.md").write_text("pushed over SSH\n", encoding="utf-8")
    git("add", "README.md", cwd=work)
    git(
        "-c",
        "user.name=phabfive",
        "-c",
        "user.email=phabfive@example.com",
        "commit",
        "-m",
        "Push over SSH",
        cwd=work,
    )
    git("push", "origin", "HEAD:refs/heads/main", cwd=work)

    branches = conduit("diffusion.branchquery", repository=hosted_repository["id"])
    assert [branch["shortName"] for branch in branches] == ["main"]


def test_built_in_uris_are_ssh(conduit, seed):
    """phd.user is what makes Phorge advertise built-in SSH URIs, on the forwarded port."""
    record = next(r for r in seed["repositories"]["repositories"] if r.get("shortName"))
    repo = conduit(
        "diffusion.repository.search",
        constraints={"shortNames": [record["shortName"]]},
        attachments={"uris": True},
    )["data"][0]
    uris = {
        uri["fields"]["uri"]["effective"] for uri in repo["attachments"]["uris"]["uris"]
    }

    assert f"ssh://git@{SSH_HOST}:{SSH_PORT}/source/{record['shortName']}.git" in uris
