# -*- coding: utf-8 -*-
"""The spec API used as a library, against a live Phorge.

This is the flow a web frontend runs and the reason #475 exists: a dict
arrives in a request body, it is validated with no network and no token, and
only then is an app constructed - explicitly, from arguments, discovering
nothing - and asked about every reference at once.

Nothing here imports `phabfive.cli`. That is the point, not an accident:
importing it sets `TYPER_USE_RICH` in the process environment, and a program
that never runs a command must not have that done to it.
"""

import json
import os
import subprocess
import sys
import uuid

import pytest

import phabfive
from phabfive.spec import validate_online


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


def _payload(**overrides):
    """A create spec the way a request body would carry it."""
    data = {
        "spec": "phorge/v1alpha1",
        "kind": "create",
        "metadata": {"name": f"library-{uuid.uuid4().hex[:8]}"},
        "variables": {"release": "1.0"},
        "tasks": [
            {
                "id": "root",
                "title": "Bootstrap {{ release }}",
                "assignment": "@admin",
            },
            {
                "title": "Follow up on {{ release }}",
                "parents": ["$root"],
                "assignment": "admin",
                "subscribers": ["@admin"],
            },
        ],
    }
    data.update(overrides)
    return data


def test_a_dict_validates_offline_then_online(credentials, isolated):
    """The whole snippet from #475, with no file and no command anywhere."""
    spec = phabfive.Spec.from_data(_payload())

    assert spec.kind is phabfive.spec.Kind.CREATE
    assert spec.metadata.name.startswith("library-")
    assert [task["title"] for task in spec.items("task")] == [
        "Bootstrap {{ release }}",
        "Follow up on {{ release }}",
    ]

    assert spec.validate_offline() == []

    app = phabfive.Maniphest(**credentials)

    # The issue's snippet, verbatim: the method, not the function
    assert spec.validate_online(app) == []

    # And the two are the same thing, so a caller may write either
    assert validate_online(spec, app) == []


def test_the_caller_keeps_its_own_dict(credentials, isolated):
    """A request body is not the spec's to mutate."""
    payload = _payload()
    before = json.dumps(payload, sort_keys=True)

    spec = phabfive.Spec.from_data(payload)
    spec.validate_offline()
    validate_online(spec, phabfive.Maniphest(**credentials))

    assert json.dumps(payload, sort_keys=True) == before


def test_every_unresolvable_reference_is_reported_at_once(credentials, isolated):
    """Three bad users, three problems, one call - not the first failure.

    This is what makes the report renderable as per-field form errors, and
    it is the acceptance criterion the online layer exists for.
    """
    missing = f"nobody-{uuid.uuid4().hex[:8]}"
    spec = phabfive.Spec.from_data(
        {
            "kind": "create",
            "tasks": [
                {"id": "root", "title": "One", "assignment": f"@{missing}"},
                {
                    "title": "Two",
                    "parents": ["$root"],
                    "assignment": "@admin",
                    "subscribers": [f"@{missing}-b", f"@{missing}-c"],
                },
            ],
        }
    )

    assert spec.validate_offline() == []

    problems = validate_online(spec, phabfive.Maniphest(**credentials))

    assert [(p.object, p.field, p.code) for p in problems] == [
        ("tasks[0]", "assignment", "unknown-user"),
        ("tasks[1]", "subscribers[0]", "unknown-user"),
        ("tasks[1]", "subscribers[1]", "unknown-user"),
    ]
    assert {p.layer for p in problems} == {"online"}
    assert all(isinstance(p, phabfive.Problem) for p in problems)
    assert [p.as_record()["value"] for p in problems] == [
        f"@{missing}",
        f"@{missing}-b",
        f"@{missing}-c",
    ]


def test_offline_finds_what_the_instance_never_sees(credentials, isolated):
    """A bad spec is a list of problems, not an exception, and no app is built.

    Two mistakes in one document come back as two problems. The online layer
    is not reached at all, so an instance that is down changes none of this.
    """
    spec = phabfive.Spec.from_data(
        {
            "kind": "create",
            "tasks": [{"title": "{{ undeclared }}", "projects": ["$nothing"]}],
        }
    )

    problems = spec.validate_offline()

    assert [(p.object, p.field, p.code) for p in problems] == [
        ("tasks[0]", "title", "undefined-variable"),
        ("tasks[0]", "projects[0]", "unknown-local-id"),
    ]
    assert {p.layer for p in problems} == {"offline"}


def test_a_variable_supplied_from_outside_satisfies_the_offline_check(
    credentials, isolated
):
    """`variables=` is what a form field carries, and `--set` on the CLI."""
    spec = phabfive.Spec.from_data(
        {"kind": "create", "tasks": [{"title": "Ship {{ release }}"}]}
    )

    assert [p.code for p in spec.validate_offline()] == ["undefined-variable"]
    assert spec.validate_offline(variables={"release": "1.0"}) == []


def test_an_instance_that_refuses_is_not_a_bad_reference(credentials, isolated):
    """A failed check must never read as a clean one, or as a bad spec.

    `validate_online` lets the remote exception out rather than turning it
    into a problem: "this user does not exist" because the token was wrong
    is the one answer that must not be possible.
    """
    spec = phabfive.Spec.from_data(
        {"kind": "create", "tasks": [{"title": "One", "assignment": "@admin"}]}
    )
    app = phabfive.Maniphest(url=credentials["url"], token="api-" + "x" * 28)

    with pytest.raises(phabfive.PhabfiveRemoteException, match="ERR-INVALID-AUTH"):
        validate_online(spec, app)


def test_the_whole_flow_loads_nothing_of_the_command(live_env, tmp_path):
    """Out of process, because the e2e suite has already imported the CLI.

    The online half reaches phabfive.core, phabfive.conduit, phabricator and
    requests - all of which a library may load. typer, click, InquirerPy and
    phabfive.cli are the ones it may not, and TYPER_USE_RICH is the visible
    trace of having loaded them.
    """
    code = (
        "import json, os, sys; before = dict(os.environ); "
        "import phabfive; "
        "spec = phabfive.Spec.from_data({'kind': 'create', 'tasks': "
        "[{'title': 'Bootstrap', 'assignment': '@admin'}]}); "
        "offline = spec.validate_offline(); "
        "app = phabfive.Maniphest(url=os.environ['URL'], token=os.environ['TOKEN']); "
        "online = spec.validate_online(app); "
        "print(json.dumps({'offline': [p.as_record() for p in offline], "
        "'online': [p.as_record() for p in online], "
        "'typer_use_rich': 'TYPER_USE_RICH' in os.environ, "
        "'environ': before == dict(os.environ), "
        "'loaded': [n for n in ('typer', 'click', 'InquirerPy', 'phabfive.cli') "
        "if n in sys.modules]}))"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("PHAB_")}
    env.pop("TYPER_USE_RICH", None)
    env["HOME"] = str(tmp_path)
    env["URL"] = live_env["PHAB_URL"]
    env["TOKEN"] = live_env["PHAB_TOKEN"]

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "offline": [],
        "online": [],
        "typer_use_rich": False,
        "environ": True,
        "loaded": [],
    }
