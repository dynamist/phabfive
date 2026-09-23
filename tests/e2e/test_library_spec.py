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


# --------------------------------------------------------------------------
# A search spec, run from a dict, with no command anywhere (#476, #477)
# --------------------------------------------------------------------------


def test_a_search_spec_runs_from_a_dict(credentials, isolated):
    """Plan, then run: the two halves, with no file and no CLI.

    `plan_searches` is the atomic form - every reference in the whole spec
    is resolved before the first query - which is what a program wanting the
    report up front calls. The command uses the per-item form instead, so
    that a later search's failure does not move ahead of an earlier
    search's output.
    """
    from phabfive.spec.search import plan_searches, run_search

    spec = phabfive.Spec.from_data(
        {
            "kind": "search",
            "searches": [
                {"title": "Mine", "search": {"assigned": "@admin", "limit": 5}},
                {"search": {"status": "any", "limit": 3}},
            ],
        }
    )

    assert spec.validate_offline() == []

    app = phabfive.Maniphest(**credentials)
    plans = plan_searches(app, spec)

    assert [plan.object_type for plan in plans] == ["task", "task"]
    assert [plan.title for plan in plans] == ["Mine", None]
    assert [plan.index for plan in plans] == [1, 2]
    assert plans[0].params["assigned"] == "@admin"

    results = [run_search(app, plan) for plan in plans]

    # The second search asks for every task, so the instance's seed data
    # guarantees at least one record
    assert len(results[1].records) >= 1
    assert all(isinstance(record, dict) for record in results[1].records)
    assert all(isinstance(result, phabfive.SearchResult) for result in results)
    assert all(isinstance(result.plan, phabfive.SearchPlan) for result in results)


def test_a_search_spec_naming_a_user_who_does_not_exist_says_so_once(
    credentials, isolated
):
    """The reference is resolved before anything is queried."""
    from phabfive.spec.search import plan_searches

    missing = f"nobody-{uuid.uuid4().hex[:8]}"
    spec = phabfive.Spec.from_data(
        {
            "kind": "search",
            "searches": [
                {"search": {"assigned": f"@{missing}"}},
                {"search": {"author": f"@{missing}-b"}},
            ],
        }
    )

    app = phabfive.Maniphest(**credentials)

    problems = validate_online(spec, app)

    assert [(p.object, p.field, p.code) for p in problems] == [
        ("searches[0]", "search.assigned", "unknown-user"),
        ("searches[1]", "search.author", "unknown-user"),
    ]

    with pytest.raises(phabfive.PhabfiveDataException) as error:
        plan_searches(app, spec)

    assert missing in str(error.value)


def test_a_mixed_spec_runs_every_type_from_a_dict(credentials, isolated):
    """One document, four object types, one client - and no command."""
    from phabfive.search import records_of, run_spec

    spec = phabfive.Spec.from_data(
        {
            "kind": "search",
            "searches": [
                {"type": "project", "search": {"status": "any", "limit": 2}},
                {"type": "paste", "search": {"limit": 2}},
                {"type": "passphrase", "search": {"limit": 2}},
                {"type": "task", "search": {"status": "any", "limit": 2}},
            ],
        }
    )

    assert spec.validate_offline() == []

    app = phabfive.Maniphest(**credentials)
    results = list(run_spec(app, spec))

    assert [result.plan.object_type for result in results] == [
        "project",
        "paste",
        "passphrase",
        "task",
    ]

    for result in results:
        records = records_of(result)
        assert isinstance(records, list)
        assert len(records) <= 2
        # The uniform view and the plan's own agree, whatever the payload is
        assert records == result.records


def test_a_search_spec_runs_with_nothing_of_the_command_loaded(live_env, tmp_path):
    """Out of process, because the e2e suite has already imported the CLI."""
    code = (
        "import json, os, sys; before = dict(os.environ); "
        "import phabfive; "
        "from phabfive.search import records_of, run_spec; "
        "spec = phabfive.Spec.from_data({'kind': 'search', 'searches': ["
        "{'type': 'project', 'search': {'status': 'any', 'limit': 2}}, "
        "{'search': {'status': 'any', 'limit': 2}}]}); "
        "app = phabfive.Maniphest(url=os.environ['URL'], token=os.environ['TOKEN']); "
        "results = list(run_spec(app, spec)); "
        "print(json.dumps({"
        "'offline': [p.as_record() for p in spec.validate_offline()], "
        "'types': [r.plan.object_type for r in results], "
        "'counts': [len(records_of(r)) for r in results], "
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

    answered = json.loads(result.stdout)

    assert answered["offline"] == []
    assert answered["types"] == ["project", "task"]
    assert all(count <= 2 for count in answered["counts"])
    assert answered["typer_use_rich"] is False
    assert answered["environ"] is True
    assert answered["loaded"] == []


# --------------------------------------------------------------------------
# The plan, held and inspected without applying it (#480)
# --------------------------------------------------------------------------


def test_a_create_plan_is_inspectable_without_applying_it(credentials, isolated):
    """#480's acceptance: a frontend holds the plan and nothing was written.

    `plan_spec` resolves everything - every name is a PHID by the time it
    answers - and sends nothing. Which is what lets a request body come
    back as "here is what this would create, confirm it" rather than as a
    list of objects that already exist.
    """
    from phabfive import Maniphest
    from phabfive.create import plan_spec
    from phabfive.spec import Spec

    app = Maniphest(url=credentials["url"], token=credentials["token"])
    plan = plan_spec(app, Spec.from_data(_payload(), kind="create").render())

    assert [item.object_type for item in plan.items] == ["task", "task"]
    assert plan.counts() == {"task": 2}

    root = plan.by_local_id()["root"]

    # Rendered, so no `{{ release }}` survives into a title
    assert root.display["title"] == "Bootstrap 1.0"

    # Resolved, so the assignee is a PHID and not the "@admin" it was
    # written as - the one online pass answered it, not the apply
    owner = next(one for one in root.transactions if one["type"] == "owner")
    assert owner["value"].startswith("PHID-USER-")

    # The second task waits for the first, by the *path* that identifies it
    follow_up = plan.by_path()["tasks[1]"]
    assert follow_up.depends_on == (root.path,)


def test_a_create_plan_survives_json(credentials, isolated):
    """Serializable, which is the other half of #480's acceptance."""
    from phabfive import Maniphest
    from phabfive.create import plan_spec
    from phabfive.spec import Spec

    app = Maniphest(url=credentials["url"], token=credentials["token"])
    plan = plan_spec(app, Spec.from_data(_payload(), kind="create").render())

    records = json.loads(json.dumps(plan.as_records()))

    assert [one["type"] for one in records] == ["task", "task"]
    assert records[0]["id"] == "root"
    assert records[1]["depends_on"] == ["tasks[0]"]

    # The `$local-id` is still literal in the transaction: it names something
    # that does not exist yet, so there is no PHID to write down, and
    # `apply_plan` substitutes it as each object comes into existence
    parents = next(
        one for one in records[1]["transactions"] if one["type"] == "parents.add"
    )
    assert parents["value"] == ["$root"]


def test_nothing_was_created_while_the_plan_was_built(credentials, isolated, conduit):
    """The count of tasks named in the plan is unchanged on the instance."""
    from phabfive import Maniphest
    from phabfive.create import plan_spec
    from phabfive.spec import Spec

    payload = _payload()
    title = payload["tasks"][0]["title"].replace("{{ release }}", "1.0")

    app = Maniphest(url=credentials["url"], token=credentials["token"])
    plan_spec(app, Spec.from_data(payload, kind="create").render())

    found = conduit("maniphest.search", **{"constraints[query]": f'"{title}"'})["data"]

    assert found == []
