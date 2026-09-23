# -*- coding: utf-8 -*-

"""Search specs for project, paste and passphrase (#477).

A search spec names its target per item::

    kind: search
    searches:
      - type: project
        search: {status: active, members: ["@me"]}
      - type: paste
        search: {author: "@me"}
      - type: passphrase
        search: {type: key}

`type:` defaults to `task`, which is what every search template in the tree
means today, so nothing here is about tasks except where a mixed document
proves the four run side by side.

Three things are pinned below, in this order: what a spec *means* per object
type (`phabfive.search.dispatch`, no terminal anywhere), what the commands do
with one (`--with` on the three search commands, every output format), and
that the passphrase walk is documented rather than hidden - the filters are
applied in Python over every credential the token can see, and a user who
does not know that cannot understand why `--limit 2 --type key` is not a
cheap query.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from phabfive.cli.passphrase import passphrase_app
from phabfive.cli.paste import paste_app
from phabfive.cli.project import project_app
from phabfive.exceptions import (
    PhabfiveDataException,
    PhabfiveInputException,
)
from phabfive.search import (
    SEARCH_RUNNERS,
    app_for,
    has_criteria,
    plan_item,
    records_of,
    run_item,
    run_spec,
    runner_for,
    searched_text,
)
from phabfive.search.dispatch import accepted_keys
from phabfive.spec import Spec
from phabfive.spec.registry import OBJECT_TYPES, spec_keys
from phabfive.spec.search import SearchPlanError

REPOSITORY = Path(__file__).resolve().parent.parent

runner = CliRunner()

ADMIN_PHID = "PHID-USER-1234567890abcdefghij"

#: Every format a search can be asked for. `rich` and `tree` are the human
#: ones, and the rest are what a program reads.
FORMATS = ("rich", "tree", "table", "yaml", "json", "jsonl", "value")


def item(object_type, search=None, **rest):
    """One `searches:` item, as `Spec.items("search")` yields one."""
    return {"type": object_type, "search": search or {}, **rest}


class StubApp:
    """One app standing in for all four, with a real Phabfive behind it.

    Deliberately **not** a `phabfive.core.Phabfive`: `app_for` hands back
    anything that is not one and answers the type's search method, so a
    mixed document needs no live instance and no class per type. Everything
    else - the console, the link formatting, the configured URL - is
    delegated to a real `Phabfive`, because the display is what these tests
    are watching and a MagicMock console prints nothing at all.
    """

    def __init__(self, **answers):
        from phabfive.core import Phabfive

        real = Phabfive.__new__(Phabfive)
        real.conf = {
            "PHAB_URL": "https://phorge.example.com/api/",
            "PHAB_TOKEN": "api-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        }
        real.url = "https://phorge.example.com"
        real.phab = MagicMock()
        real.lookup_store = None
        real.phab.user.whoami.return_value = {"phid": ADMIN_PHID}

        def search(constraints):
            # No user is called "me", which would make @me ambiguous
            if constraints.get("usernames") == ["me"]:
                return {"data": []}
            return {"data": [{"phid": ADMIN_PHID, "fields": {"username": "admin"}}]}

        real.phab.user.search.side_effect = search

        # Bypasses __getattr__ below, which would otherwise look for it on
        # the real app and find nothing
        object.__setattr__(self, "_real", real)

        self.search = MagicMock(return_value=answers.get("project", {"projects": []}))
        self.paste_search = MagicMock(return_value=answers.get("paste", {"pastes": []}))
        self.search_passphrases = MagicMock(return_value=answers.get("passphrase", []))
        self.task_search = MagicMock(return_value=answers.get("task", {"tasks": []}))

    def parse_status_patterns_with_api(self, value):
        """The one thing planning a *task* search asks the instance."""
        from phabfive.transitions import parse_status_patterns

        return parse_status_patterns(value)

    def __getattr__(self, name):
        return getattr(self._real, name)


def stub_app(**answers):
    """An app that resolves users and answers each search with what it is given."""
    return StubApp(**answers)


def project_record(name="infra"):
    """One project as `project search` answers with it."""
    return {
        "_url": f"https://phorge.example.com/tag/{name}/",
        "_link": name,
        "Project": {"Name": name, "Hashtag": name, "Status": "active"},
    }


def paste_record(name="deploy notes"):
    """One paste as `paste search` answers with it."""
    return {
        "_url": "https://phorge.example.com/P1",
        "_link": "P1",
        "_monogram": "P1",
        "Paste": {"Name": name, "Author": "admin", "Language": "text"},
    }


def credential_record(name="deploy key"):
    """One credential as `passphrase search` answers with it."""
    return {
        "id": "K1",
        "url": "https://phorge.example.com/K1",
        "_link": "K1",
        "type": "SSH Key",
        "name": name,
        "username": None,
    }


ANSWERS = {
    "project": {"projects": [project_record()]},
    "paste": {"pastes": [paste_record()]},
    "passphrase": [credential_record()],
}


class TestWhatASpecMeansPerObjectType:
    """One `search:` section, one search method's keyword arguments."""

    def test_a_project_search_is_project_search_s_arguments(self):
        plan = plan_item(
            stub_app(),
            item("project", {"status": "archived", "members": ["@me"], "limit": 5}),
        )

        assert plan.object_type == "project"
        assert plan.params["status"] == "archived"
        assert plan.params["members"] == ["@me"]
        assert plan.params["limit"] == 5

    def test_a_project_search_names_no_status_and_gets_the_active_one(self):
        """The default the command uses, not "every status"."""
        plan = plan_item(stub_app(), item("project", {"members": ["@me"]}))

        assert plan.params["status"] == "active"
        assert plan.params["milestones"] is None
        assert plan.params["spaces"] is None

    def test_a_repeatable_filter_reads_a_string_or_a_list(self):
        """`icons: "group,tag"` and `icons: [group, tag]` are one spelling."""
        as_string = plan_item(stub_app(), item("project", {"icons": "group,tag"}))
        as_list = plan_item(stub_app(), item("project", {"icons": ["group", "tag"]}))

        assert as_string.params["icons"] == ["group", "tag"]
        assert as_list.params["icons"] == as_string.params["icons"]

    def test_a_paste_search_resolves_its_author_before_anything_is_fetched(self):
        app = stub_app()

        plan = plan_item(app, item("paste", {"author": "@me", "text_query": "deploy"}))

        assert plan.params["constraints"] == {
            "query": "deploy",
            # paste.search names this "authors"; maniphest.search calls the
            # same filter "authorPHIDs", and the wrong one is
            # ERR-INVALID-CONSTRAINT
            "authors": [ADMIN_PHID],
        }
        assert "authorPHIDs" not in plan.params["constraints"]

    def test_a_paste_search_naming_a_user_that_does_not_exist_is_refused(self):
        app = stub_app()
        app.phab.user.search.side_effect = None
        app.phab.user.search.return_value = {"data": []}

        with pytest.raises(PhabfiveDataException):
            plan_item(app, item("paste", {"author": "nobody"}))

        app.paste_search.assert_not_called()

    def test_a_passphrase_search_is_a_name_and_a_type(self):
        plan = plan_item(
            stub_app(), item("passphrase", {"type": "key", "text_query": "deploy"})
        )

        assert plan.params["query"] == "deploy"
        assert plan.params["credential_type"] == "key"

    def test_a_passphrase_search_never_asks_for_secret_material(self):
        """The filters are applied over every credential the token can see.

        So a *search* asking for secrets would fetch the secret of every
        credential on the instance rather than of the ones that matched.
        `passphrase show` is how one secret is read, by id.
        """
        plan = plan_item(stub_app(), item("passphrase", {"type": "key"}))

        assert plan.params["need_secrets"] is False
        assert "show-secret" not in accepted_keys(runner_for("passphrase"))
        assert "need_secrets" not in accepted_keys(runner_for("passphrase"))

    @pytest.mark.parametrize("object_type", ["project", "paste", "passphrase"])
    def test_a_limit_of_zero_means_every_match(self, object_type):
        plan = plan_item(stub_app(), item(object_type, {"limit": 0}))

        assert plan.params["limit"] is None

    @pytest.mark.parametrize("object_type", ["project", "paste", "passphrase"])
    def test_a_limit_that_is_not_a_number_is_refused_by_name(self, object_type):
        with pytest.raises(PhabfiveInputException) as error:
            plan_item(stub_app(), item(object_type, {"limit": "lots"}))

        assert "limit" in str(error.value)

    @pytest.mark.parametrize(
        "object_type,search",
        [
            ("project", {"tag": "infra"}),
            ("paste", {"assigned": "@me"}),
            ("passphrase", {"colors": ["red"]}),
        ],
    )
    def test_a_key_nothing_reads_is_refused_by_name(self, object_type, search):
        """A key accepted and then ignored is the drift #295 ended."""
        with pytest.raises(PhabfiveDataException) as error:
            plan_item(stub_app(), item(object_type, search))

        assert list(search)[0] in str(error.value)
        assert "Supported:" in str(error.value)

    def test_a_search_section_that_is_not_a_mapping_is_refused(self):
        with pytest.raises(PhabfiveDataException):
            plan_item(stub_app(), item("project", search=None) | {"search": ["x"]})

    def test_an_object_type_nothing_searches_is_refused_by_name(self):
        with pytest.raises(SearchPlanError) as error:
            plan_item(stub_app(), item("repository", {}))

        assert error.value.check == "unsupported-type"
        assert "project" in str(error.value)

    def test_the_type_defaults_to_task(self):
        """Every search template in the tree means a task search."""
        plan = plan_item(stub_app(), {"search": {"tag": "infra"}})

        assert plan.object_type == "task"

    def test_an_override_beats_the_spec_and_none_does_not(self):
        plan = plan_item(
            stub_app(),
            item("project", {"status": "active", "limit": 5}),
            overrides={"status": "archived", "limit": None},
        )

        assert plan.params["status"] == "archived"
        assert plan.params["limit"] == 5


class TestTheKeysASpecMayUse:
    """The registry declares all four now; the table has to agree with it."""

    def test_every_object_type_a_spec_may_name_can_be_searched(self):
        assert set(SEARCH_RUNNERS) == set(OBJECT_TYPES)

    @pytest.mark.parametrize("object_type", sorted(OBJECT_TYPES))
    def test_the_registry_and_the_table_agree(self, object_type):
        """The declaration and the builder, compared - not derived.

        `accepted_keys` prefers the registry, so comparing it with
        `spec_keys` would be one expression against itself. What has to
        agree is the registry's declaration and `SearchRunner.keys`, which
        is the list the builder actually reads: a key declared and not built
        is a filter that quietly does not happen, and a key built and not
        declared is refused by the loader before the builder sees it.
        """
        declared = spec_keys(object_type, "search")

        assert declared, object_type

        if object_type == "task":
            # The task runner carries no key list: `plan_search` interprets a
            # task search, and these are the tables and branches there that
            # read a key. Every declared task key has to be in one of them.
            from phabfive.spec.search import PARAM_DEFAULTS, SEARCH_PARAMS

            read = (
                set(SEARCH_PARAMS)
                | set(PARAM_DEFAULTS)
                # Interpreted rather than passed through: the three
                # transition patterns, the two task-id lists and the order
                | {"column", "priority", "status", "include", "exclude", "order"}
            )

            assert declared == read
            return

        assert declared == frozenset(runner_for(object_type).keys)

    def test_accepted_keys_is_now_the_registry_for_every_type(self):
        """The interim table is no longer what a spec is checked against."""
        for object_type in sorted(OBJECT_TYPES):
            runner = runner_for(object_type)

            assert accepted_keys(runner) == spec_keys(object_type, "search")

    def test_every_type_declares_its_key_set_complete(self):
        """Which is what makes an undeclared key an error rather than ignored."""
        from phabfive.spec.registry import DECLARED_COMPLETE

        assert {object_type for object_type, verb in DECLARED_COMPLETE} == set(
            OBJECT_TYPES
        )


class TestRunningOne:
    """What the app answered, and the one view over all four shapes."""

    @pytest.mark.parametrize("object_type", ["project", "paste", "passphrase"])
    def test_the_records_are_reached_the_same_way_whatever_was_searched(
        self, object_type
    ):
        app = stub_app(**ANSWERS)

        result = run_item(app, plan_item(app, item(object_type, {"limit": 5})))

        assert result.payload == ANSWERS[object_type]
        assert len(records_of(result)) == 1

    def test_a_search_that_found_nothing_is_an_empty_list(self):
        app = stub_app()

        result = run_item(app, plan_item(app, item("project")))

        assert records_of(result) == []

    def test_the_method_is_called_with_exactly_the_plan_s_parameters(self):
        app = stub_app(**ANSWERS)
        plan = plan_item(app, item("passphrase", {"type": "key", "limit": 2}))

        run_item(app, plan)

        app.search_passphrases.assert_called_once_with(
            query=None, credential_type="key", need_secrets=False, limit=2
        )

    def test_a_project_search_with_no_filter_is_a_request(self):
        """It lists every active project in PHAB_SPACE, which is a thing to ask."""
        app = stub_app()

        assert has_criteria(plan_item(app, item("project"))) is True

    @pytest.mark.parametrize("object_type", ["paste", "passphrase"])
    def test_a_paste_or_passphrase_search_with_no_filter_asked_nothing(
        self, object_type
    ):
        app = stub_app()

        assert has_criteria(plan_item(app, item(object_type))) is False

    @pytest.mark.parametrize(
        "object_type,search,expected",
        [
            ("project", {"text_query": "infra"}, "infra"),
            ("paste", {"text_query": "deploy"}, "deploy"),
            ("passphrase", {"text_query": "key"}, "key"),
            ("project", {"members": ["@me"]}, None),
        ],
    )
    def test_the_free_text_is_found_whatever_shape_the_parameters_have(
        self, object_type, search, expected
    ):
        app = stub_app()

        assert searched_text(plan_item(app, item(object_type, search))) == expected


class TestOneDocumentHoldingSeveralTypes:
    """The acceptance: a spec mixing types runs each in order."""

    MIXED = {
        "kind": "search",
        "searches": [
            {"type": "project", "search": {"members": ["@me"]}},
            {"type": "paste", "search": {"author": "@me"}},
            {"type": "passphrase", "search": {"type": "key"}},
            {"type": "task", "search": {"tag": "infra"}},
        ],
    }

    def test_every_search_runs_in_document_order(self):
        app = stub_app(**ANSWERS, task={"tasks": [{"Task": {"Name": "one"}}]})

        results = list(run_spec(app, Spec.from_data(self.MIXED)))

        assert [result.plan.object_type for result in results] == [
            "project",
            "paste",
            "passphrase",
            "task",
        ]
        assert [len(records_of(result)) for result in results] == [1, 1, 1, 1]

    def test_each_search_is_numbered_by_its_position(self):
        app = stub_app(**ANSWERS, task={"tasks": []})

        results = list(run_spec(app, Spec.from_data(self.MIXED)))

        assert [result.plan.index for result in results] == [1, 2, 3, 4]
        assert {result.plan.total for result in results} == {4}

    def test_nothing_is_searched_until_the_result_is_taken(self):
        """A generator, so the first two searches answer before a third fails."""
        app = stub_app(**ANSWERS, task={"tasks": []})

        searches = run_spec(app, Spec.from_data(self.MIXED))

        app.search.assert_not_called()
        next(searches)
        app.search.assert_called_once()
        app.paste_search.assert_not_called()


class TestTheAppEachTypeIsSearchedWith:
    """One configuration and one client, however many types a spec names."""

    def _parent(self):
        from phabfive.project import Project

        parent = Project.__new__(Project)
        parent.conf = {"PHAB_URL": "https://phorge.example.com/api/"}
        parent.url = "https://phorge.example.com"
        parent.phab = MagicMock()
        parent.lookup_store = None

        return parent

    def test_a_sibling_app_shares_the_parent_s_client(self):
        from phabfive.paste import Paste

        parent = self._parent()

        paste = app_for("paste", parent)

        assert isinstance(paste, Paste)
        assert paste.phab is parent.phab
        assert paste.conf is parent.conf

    def test_the_parent_itself_is_used_when_it_is_already_the_right_app(self):
        parent = self._parent()

        assert app_for("project", parent) is parent

    def test_each_type_is_built_once_however_many_items_name_it(self):
        app = stub_app(**ANSWERS)
        spec = Spec.from_data(
            {
                "kind": "search",
                "searches": [
                    {"type": "paste", "search": {"author": "@me"}},
                    {"type": "paste", "search": {"text_query": "deploy"}},
                ],
            }
        )

        with patch("phabfive.search.dispatch.app_for", return_value=app) as built:
            list(run_spec(app, spec))

        assert built.call_count == 1

    def test_an_object_type_nothing_searches_has_no_app(self):
        with pytest.raises(SearchPlanError):
            app_for("repository", self._parent())


class TestTheCommandsIngestion:
    """`--with` on the three search commands, in every output format."""

    @staticmethod
    def _spec_file(tmp_path, body):
        path = tmp_path / "searches.yaml"
        path.write_text(body, encoding="utf-8")

        return str(path)

    @pytest.mark.parametrize("output_format", FORMATS)
    @pytest.mark.parametrize(
        "app,patched,body",
        [
            (
                project_app,
                "phabfive.cli.project._get_project_app",
                "kind: search\nsearches:\n  - type: project\n"
                "    search: {members: ['@me']}\n",
            ),
            (
                paste_app,
                "phabfive.cli.paste._get_paste_app",
                "kind: search\nsearches:\n  - type: paste\n"
                "    search: {author: '@me'}\n",
            ),
            (
                passphrase_app,
                "phabfive.cli.passphrase._get_passphrase_app",
                "kind: search\nsearches:\n  - type: passphrase\n"
                "    search: {type: key}\n",
            ),
        ],
        ids=["project", "paste", "passphrase"],
    )
    def test_a_search_spec_of_each_type_emits_records(
        self, tmp_path, output_format, app, patched, body
    ):
        with patch(patched) as get_app:
            get_app.return_value = stub_app(**ANSWERS)

            result = runner.invoke(
                app,
                ["search", "--with", self._spec_file(tmp_path, body)],
                obj={"format": output_format},
            )

        assert result.exit_code == 0, result.output
        assert result.output.strip()

    def test_a_json_search_spec_emits_the_records_as_json(self, tmp_path):
        body = "kind: search\nsearches:\n  - type: project\n    search: {}\n"

        with patch("phabfive.cli.project._get_project_app") as get_app:
            get_app.return_value = stub_app(**ANSWERS)

            result = runner.invoke(
                project_app,
                ["search", "--with", self._spec_file(tmp_path, body)],
                obj={"format": "json"},
            )

        assert result.exit_code == 0, result.output
        emitted = json.loads(result.output)
        assert [record["Project"] for record in emitted] == [
            project_record()["Project"]
        ]

    def test_one_document_runs_every_type_in_order(self, tmp_path):
        body = (
            "kind: search\n"
            "searches:\n"
            "  - type: project\n"
            "    search: {members: ['@me']}\n"
            "  - type: paste\n"
            "    search: {author: '@me'}\n"
            "  - type: passphrase\n"
            "    search: {type: key}\n"
        )

        with patch("phabfive.cli.project._get_project_app") as get_app:
            app = stub_app(**ANSWERS)
            get_app.return_value = app

            result = runner.invoke(
                project_app,
                ["search", "--with", self._spec_file(tmp_path, body)],
                obj={"format": "jsonl"},
            )

        assert result.exit_code == 0, result.output
        emitted = [json.loads(line) for line in result.output.splitlines() if line]
        # Each record names what it is, in the section the app's own `show`
        # command publishes it under
        sections = [next(key for key in record if key != "Link") for record in emitted]
        assert sections == ["Project", "Paste", "Credential"]

    def test_a_command_line_value_overrides_the_spec(self, tmp_path):
        body = (
            "kind: search\nsearches:\n  - type: project\n    search: {status: active}\n"
        )

        with patch("phabfive.cli.project._get_project_app") as get_app:
            app = stub_app(**ANSWERS)
            get_app.return_value = app

            result = runner.invoke(
                project_app,
                [
                    "search",
                    "--with",
                    self._spec_file(tmp_path, body),
                    "--status",
                    "archived",
                ],
                obj={"format": "json"},
            )

        assert result.exit_code == 0, result.output
        assert app.search.call_args.kwargs["status"] == "archived"

    def test_a_banner_separates_two_searches_in_a_human_format(self, tmp_path):
        body = (
            "kind: search\n"
            "searches:\n"
            "  - type: project\n"
            "    title: Mine\n"
            "    search: {members: ['@me']}\n"
            "  - type: project\n"
            "    search: {members: ['admin']}\n"
        )

        with patch("phabfive.cli.project._get_project_app") as get_app:
            get_app.return_value = stub_app(**ANSWERS)

            result = runner.invoke(
                project_app,
                ["search", "--with", self._spec_file(tmp_path, body)],
                obj={"format": "tree"},
            )

        assert result.exit_code == 0, result.output
        assert "Mine" in result.output
        assert "Search 2" in result.output

    def test_a_machine_format_gets_no_banner(self, tmp_path):
        body = (
            "kind: search\nsearches:\n  - type: project\n    title: Mine\n"
            "    search: {members: ['@me']}\n"
        )

        with patch("phabfive.cli.project._get_project_app") as get_app:
            get_app.return_value = stub_app(**ANSWERS)

            result = runner.invoke(
                project_app,
                ["search", "--with", self._spec_file(tmp_path, body)],
                obj={"format": "json"},
            )

        assert result.exit_code == 0, result.output
        assert "Mine" not in result.output

    def test_a_spec_that_does_not_load_is_one_message_and_exit_one(self, tmp_path):
        path = tmp_path / "broken.yaml"
        path.write_text("kind: search\nsearches: {not: a list}\n", encoding="utf-8")

        with patch("phabfive.cli.project._get_project_app") as get_app:
            get_app.return_value = stub_app()

            result = runner.invoke(project_app, ["search", "--with", str(path)])

        assert result.exit_code == 1
        assert "ERROR" in result.output

    def test_a_key_nothing_reads_is_named_and_exits_one(self, tmp_path):
        body = "kind: search\nsearches:\n  - type: paste\n    search: {tag: infra}\n"

        with patch("phabfive.cli.paste._get_paste_app") as get_app:
            get_app.return_value = stub_app()

            result = runner.invoke(
                paste_app, ["search", "--with", self._spec_file(tmp_path, body)]
            )

        assert result.exit_code == 1
        assert "tag" in result.output

    def test_a_paste_search_with_no_filter_at_all_is_refused(self, tmp_path):
        body = "kind: search\nsearches:\n  - type: paste\n    search: {}\n"

        with patch("phabfive.cli.paste._get_paste_app") as get_app:
            app = stub_app()
            get_app.return_value = app

            result = runner.invoke(
                paste_app, ["search", "--with", self._spec_file(tmp_path, body)]
            )

        assert result.exit_code == 1
        app.paste_search.assert_not_called()

    def test_show_secret_cannot_be_combined_with_a_spec(self, tmp_path):
        body = (
            "kind: search\nsearches:\n  - type: passphrase\n    search: {type: key}\n"
        )

        with patch("phabfive.cli.passphrase._get_passphrase_app") as get_app:
            app = stub_app(**ANSWERS)
            get_app.return_value = app

            result = runner.invoke(
                passphrase_app,
                [
                    "search",
                    "--with",
                    self._spec_file(tmp_path, body),
                    "--show-secret",
                ],
            )

        assert result.exit_code == 1
        assert "--show-secret" in result.output
        app.search_passphrases.assert_not_called()

    def test_a_search_command_with_no_spec_is_unchanged(self, tmp_path):
        """`--with` is an alternative to the flags, not a new requirement."""
        with patch("phabfive.cli.paste._get_paste_app") as get_app:
            app = stub_app(**ANSWERS)
            get_app.return_value = app

            result = runner.invoke(
                paste_app, ["search", "deploy"], obj={"format": "json"}
            )

        assert result.exit_code == 0, result.output
        assert app.paste_search.call_args.kwargs["constraints"] == {"query": "deploy"}


class TestWhatThePassphraseWalkCosts:
    """It is documented rather than hidden, which is the acceptance."""

    def _documentation(self):
        return (REPOSITORY / "docs" / "search-templates.md").read_text(encoding="utf-8")

    def test_the_documentation_says_the_filters_are_applied_here(self):
        text = self._documentation().lower()

        assert "passphrase.query" in text
        assert "takes no constraints" in text

    def test_the_documentation_says_what_a_small_limit_does_not_buy(self):
        text = self._documentation().lower()

        assert "every credential" in text
        assert "limit" in text

    def test_the_command_help_says_it_too(self):
        """A person reading `--help` is the one about to pay for the walk."""
        result = runner.invoke(passphrase_app, ["search", "--help"])

        assert result.exit_code == 0
        assert "passphrase.query" in result.output


class TestRunningOneWithoutACommandLine:
    """`phabfive.search` is library code, proved out of process.

    In process this cannot be asserted: by the time the suite runs,
    `phabfive.cli` is already in `sys.modules` and `TYPER_USE_RICH` is
    already in the environment, whatever the code does.
    """

    def _environment(self):
        """As little environment as an interpreter needs to start.

        `HOME` and the Windows configuration variables are kept *defined*
        but pointed nowhere: `phabricator/__init__.py` reads `ProgramData`
        and `AppData` as it imports, so removing them is a KeyError before
        anything is planned.
        """
        keep = ("PATH", "SYSTEMROOT", "LD_LIBRARY_PATH", "VIRTUAL_ENV")
        environment = {name: os.environ[name] for name in keep if name in os.environ}

        nowhere = str(Path(os.sep, "nonexistent"))

        for name in (
            "HOME",
            "USERPROFILE",
            "APPDATA",
            "LOCALAPPDATA",
            "ProgramData",
            "ALLUSERSPROFILE",
        ):
            environment[name] = nowhere

        environment["PYTHONPATH"] = str(REPOSITORY)
        environment["TERM"] = "dumb"

        assert "TYPER_USE_RICH" not in environment

        return environment

    def test_planning_a_search_imports_no_command_line(self):
        script = """
import json, os, sys

from phabfive.search import plan_item

class App:
    pass

plan = plan_item(App(), {"type": "project", "search": {"icons": "group,tag"}})

print(json.dumps({
    "icons": plan.params["icons"],
    "cli": "phabfive.cli" in sys.modules,
    "typer": "TYPER_USE_RICH" in os.environ,
    "passphrase": "phabfive.passphrase" in sys.modules,
}))
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            env=self._environment(),
            cwd=str(REPOSITORY),
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert result.returncode == 0, (
            f"exit {result.returncode}\n--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
        )
        assert json.loads(result.stdout) == {
            "icons": ["group", "tag"],
            "cli": False,
            "typer": False,
            # Planning one type never imports another type's app
            "passphrase": False,
        }
