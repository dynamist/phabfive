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
