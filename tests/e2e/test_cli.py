# -*- coding: utf-8 -*-
"""The phabfive CLI against a live Phorge, see docs/phorge-setup.md."""

# python std lib
import json
from urllib.parse import urlparse


def test_whoami(phabfive, live_env):
    [host] = phabfive("user", "whoami", json_output=True)
    assert host["User"]["UserName"] == "admin"
    assert host["Host"] == urlparse(live_env["PHAB_URL"]).hostname


def test_create_on_a_workboard(phabfive, create_task):
    task_id, title = create_task("--tag", "Development", "--priority", "high")
    [task] = phabfive("maniphest", "show", task_id, json_output=True)
    assert task["Task"]["Name"] == title
    assert task["Task"]["Priority"] == "High"
    assert task["Task"]["Status"] == "Open"
    assert task["Boards"]["Development"]["Column"] == "Backlog"


def test_edit_status(phabfive, create_task):
    task_id, _title = create_task()
    phabfive("maniphest", "edit", task_id, "--status", "resolved")
    [task] = phabfive("maniphest", "show", task_id, json_output=True)
    assert task["Task"]["Status"] == "Resolved"


def test_search_by_tag(phabfive, create_task):
    _task_id, title = create_task("--tag", "QA")
    tasks = phabfive("maniphest", "search", "--tag", "QA", json_output=True)
    assert title in [task["Task"]["Name"] for task in tasks]


def test_create_in_a_space(phabfive, create_task, space_name):
    task_id, _title = create_task("--space", "S3")
    [task] = phabfive("maniphest", "show", task_id, json_output=True)
    assert task["Space"] == space_name("S3")


def test_jsonl_is_one_task_per_line(phabfive, create_task):
    first, first_title = create_task()
    second, second_title = create_task()

    # The fixture puts positional args straight after the interpreter, so a
    # global option passed here still lands before the subcommand
    output = phabfive("--format", "jsonl", "maniphest", "show", first, second)

    lines = output.splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["Task"]["Name"] for line in lines] == [
        first_title,
        second_title,
    ]


def test_repo_show_describes_a_seeded_repository(phabfive):
    """GUNNAR is hosted, with history, per phorge/seed/data/repositories.json."""
    [repo] = phabfive("diffusion", "repo", "show", "GUNNAR", json_output=True)

    assert repo["Repository"]["Callsign"] == "GUNNAR"
    assert repo["Repository"]["Short Name"] == "gunnar-firmware"
    assert repo["Repository"]["Default Branch"] == "main"
    assert repo["Repository"]["VCS"] == "git"
    assert repo["Repository"]["Hosted"] is True
    assert repo["Link"].endswith("/source/gunnar-firmware/")
    assert repo["Policy"]["View"] == "All Users"


def test_repo_show_resolves_every_way_in(phabfive):
    """Monogram, callsign and short name all name the same repository.

    The monogram is asked for rather than assumed: the seeder keys on the
    callsign and creates only what is missing, so which R number GUNNAR
    got depends on what was already in the database.
    """
    [seeded] = phabfive("diffusion", "repo", "show", "GUNNAR", json_output=True)
    monogram = seeded["Repository"]["Monogram"]

    monograms = set()
    for identifier in (monogram, "GUNNAR", "gunnar-firmware"):
        [repo] = phabfive("diffusion", "repo", "show", identifier, json_output=True)
        monograms.add(repo["Repository"]["Monogram"])

    assert monograms == {monogram}


def test_repo_show_reads_the_refs_off_a_hosted_repository(phabfive):
    """The reason the seeder builds real history: refs to answer with."""
    [repo] = phabfive(
        "diffusion",
        "repo",
        "show",
        "GUNNAR",
        "--show-branches",
        "--show-tags",
        json_output=True,
    )

    assert repo["Branches"] == ["feature/telemetry", "main"]
    assert repo["Tags"] == ["v1.0.0"]


def test_repo_show_on_a_repository_nobody_pushed_to(phabfive):
    """Empty is an empty result, not a failure."""
    [repo] = phabfive(
        "diffusion",
        "repo",
        "show",
        "SPIKE",
        "--show-branches",
        "--show-tags",
        json_output=True,
    )

    assert repo["Branches"] == []
    assert repo["Tags"] == []


def test_repo_show_takes_several_repositories(phabfive):
    repos = phabfive("diffusion", "repo", "show", "GUNNAR,SPIKE", json_output=True)

    assert [r["Repository"]["Callsign"] for r in repos] == ["GUNNAR", "SPIKE"]


def test_repo_show_formats_agree(phabfive):
    """The whole point of the command: one record, five ways of writing it."""
    from ruamel.yaml import YAML

    load = YAML(typ="safe").load
    args = (
        "diffusion",
        "repo",
        "show",
        "GUNNAR",
        "SPIKE",
        "--show-branches",
        "--show-tags",
    )

    as_json = phabfive(*args, json_output=True)
    as_yaml = load(phabfive("--format", "yaml", *args))
    as_jsonl = [
        json.loads(line) for line in phabfive("--format", "jsonl", *args).splitlines()
    ]
    # Rich is the same record in YAML shape - hyperlinks and colour, not a
    # different layout - and NO_COLOR plus a pipe leave it parseable.
    as_rich = load(phabfive("--format", "rich", *args))

    assert as_yaml == as_json
    assert as_jsonl == as_json
    assert as_rich == as_json


def test_repo_show_on_a_repository_that_does_not_exist(phabfive_raw):
    """A failed lookup, not an empty result."""
    result = phabfive_raw("diffusion", "repo", "show", "R9999")

    assert result.returncode == 1
    assert "not found" in result.stderr


def test_repo_show_fails_on_a_partial_result(phabfive_raw):
    """GUNNAR is shown, and the exit code still says something was missed."""
    result = phabfive_raw(
        "--format", "jsonl", "diffusion", "repo", "show", "GUNNAR,R9999"
    )

    assert result.returncode == 1
    assert len(result.stdout.splitlines()) == 1
    assert json.loads(result.stdout)["Repository"]["Callsign"] == "GUNNAR"
