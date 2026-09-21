# -*- coding: utf-8 -*-
"""phabfive used as a library, against a live Phorge.

Every other e2e test drives the command in a subprocess. These construct the
classes the way a program would - configured from arguments, with nothing
read from the environment or the user's files - and use what they return.
"""

import uuid

import pytest

import phabfive


@pytest.fixture
def credentials(live_env):
    return {"url": live_env["PHAB_URL"], "token": live_env["PHAB_TOKEN"]}


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """No configuration to discover: an empty HOME and no PHAB_* variables.

    Explicit arguments must be enough on their own, so nothing else may be
    able to supply what they leave out.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    for name in ("PHAB_URL", "PHAB_TOKEN", "PHAB_SPACE"):
        monkeypatch.delenv(name, raising=False)


def test_whoami(credentials, isolated):
    user = phabfive.User(**credentials)

    assert user.whoami()["userName"] == "admin"


def test_create_show_and_search(credentials, isolated):
    maniphest = phabfive.Maniphest(**credentials)
    title = f"E2E library {uuid.uuid4().hex[:8]}"

    created = maniphest.create_task(title, tags=["QA"], priority="high")

    [task] = maniphest.task_show([created["id"]])["tasks"]
    assert task["Task"]["Name"] == title
    assert task["Task"]["Priority"] == "High"
    assert isinstance(task["_link"], str)

    # By tag, not text: the full-text index is updated asynchronously
    found = maniphest.task_search(tag="QA")["tasks"]
    assert title in [record["Task"]["Name"] for record in found]


def test_sibling_apps_share_one_client(credentials, isolated):
    diffusion = phabfive.Diffusion(**credentials)

    diffusion.repo_list()

    assert diffusion.passphrase.phab is diffusion.phab


def test_a_bad_token_fails_on_verify(credentials, isolated):
    bad = dict(credentials, token="api-" + "x" * 28)

    with pytest.raises(phabfive.PhabfiveRemoteException, match="ERR-INVALID-AUTH"):
        phabfive.User(**bad, verify=True)
