# -*- coding: utf-8 -*-

"""The constraints project, paste and user search send, and --order (#479).

Three things are pinned here, for each of the three apps:

* every new filter reaches the endpoint as the constraint Phorge names -
  a wrong key is ERR-INVALID-CONSTRAINT, and an unknown *value* is usually
  an empty result rather than an error, which reads like "nothing matched";
* `--order` reaches the endpoint, because without it `--limit` returns an
  arbitrary subset: the server answers in its own order and phabfive would
  sort only the page it happened to get;
* every spelling the parser accepts maps to an order the endpoint has. A
  field added to a table with no API order behind it turns those red.

The constraint names and the order names were read off a live Phorge
(project.search, paste.search and user.search on the local instance), not
guessed: project.search has no created-date constraint and paste.search no
modified-date one, so neither is wired up.
"""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from phabfive.cli.paste import paste_app
from phabfive.cli.project import project_app
from phabfive.cli.user import user_app
from phabfive.constants import (
    PASTE_ORDER_CHOICES,
    PASTE_ORDER_DIRECTIONS,
    PASTE_ORDER_FIELDS,
    PASTE_ORDER_SUGGESTIONS,
    PROJECT_MILESTONE_ICON,
    PROJECT_ORDER_CHOICES,
    PROJECT_ORDER_DIRECTIONS,
    PROJECT_ORDER_FIELDS,
    PROJECT_ORDER_SUGGESTIONS,
    USER_ORDER_CHOICES,
    USER_ORDER_DIRECTIONS,
    USER_ORDER_FIELDS,
    USER_ORDER_SUGGESTIONS,
)
from phabfive.core import Phabfive
from phabfive.exceptions import PhabfiveConfigException, PhabfiveInputException
from phabfive.ordering import complete_order_value, parse_order, sort_records
from phabfive.paste.core import (
    PASTE_API_ORDERS,
    Paste,
    build_paste_search_constraints,
    paste_id_list,
)
from phabfive.project.core import PROJECT_API_ORDERS, Project, project_id_list
from phabfive.user import USER_API_ORDERS, User, user_id_list

runner = CliRunner()

URL = "http://phorge.localhost"


@pytest.fixture
def restore_output_format():
    original = Phabfive._output_format
    try:
        yield
    finally:
        Phabfive._output_format = original


def _pager(records=()):
    """A *.search that records every call and answers one page."""
    calls = []

    def search(**kwargs):
        calls.append(kwargs)
        return {"data": list(records), "cursor": {"after": None}}

    search.calls = calls

    return search


def _project(id_, name, slug=None):
    return {
        "id": id_,
        "phid": f"PHID-PROJ-{id_}",
        "type": "PROJ",
        "fields": {
            "name": name,
            "slug": slug or name.lower(),
            "milestone": None,
            "parent": None,
            "icon": {"key": "project"},
            "color": {"key": "blue"},
            "status": "active",
            "dateCreated": 1700000000,
            "dateModified": 1700000000,
            "spacePHID": None,
            "policy": {},
        },
    }


def _paste(id_, title="notes"):
    return {
        "id": id_,
        "phid": f"PHID-PASTE-{id_}",
        "fields": {
            "title": title,
            "authorPHID": "PHID-USER-admin",
            "language": "text",
            "status": "active",
            "dateCreated": 1700000000,
            "dateModified": 1700000000,
            "spacePHID": None,
        },
    }


def _user(id_, username):
    return {
        "id": id_,
        "phid": f"PHID-USER-{username}",
        "fields": {
            "username": username,
            "realName": username.title(),
            "roles": ["verified", "approved", "activated"],
            "dateCreated": 1700000000,
            "dateModified": 1700000000,
        },
    }


def _project_app(records=()):
    with patch("phabfive.project.core.Phabfive.__init__", return_value=None):
        project = Project()

    project.phab = MagicMock()
    project.phab.project.search.side_effect = _pager(records)
    project.phab.phid.query.return_value = {}
    project.url = URL
    # "*" so no Space is resolved: this file is about the other constraints
    project.conf = {"PHAB_SPACE": "*"}
    project.format_link = lambda url, text=None, **kwargs: url

    return project


def _paste_app(records=()):
    with patch("phabfive.paste.core.Phabfive.__init__", return_value=None):
        paste = Paste()

    paste.phab = MagicMock()
    paste.phab.paste.search.side_effect = _pager(records)
    paste.phab.phid.query.return_value = {}
    paste.url = URL
    paste.conf = {}
    paste.format_link = lambda url, text=None, **kwargs: url

    return paste


def _user_app(records=()):
    with patch("phabfive.user.Phabfive.__init__", return_value=None):
        user = User()

    user.phab = MagicMock()
    user.phab.user.search.side_effect = _pager(records)
    user.url = URL
    user.conf = {}
    user.format_link = lambda url, text=None, **kwargs: url

    return user


def _calls(app, endpoint):
    return getattr(app.phab, endpoint).search.side_effect.calls


def _constraints(app, endpoint):
    return _calls(app, endpoint)[0]["constraints"]


class TestProjectConstraints:
    """The four project.search constraints phabfive did not expose."""

    def test_ids_reach_the_api_as_numbers(self):
        project = _project_app()

        project.search(ids=["12", "20"])

        assert _constraints(project, "project")["ids"] == [12, 20]

    def test_phids_reach_the_api(self):
        project = _project_app()

        project.search(phids=["PHID-PROJ-12,PHID-PROJ-13"])

        assert _constraints(project, "project")["phids"] == [
            "PHID-PROJ-12",
            "PHID-PROJ-13",
        ]

    def test_slugs_answer_which_project_owns_a_hashtag(self):
        project = _project_app()

        project.search(slugs=["#web_team"])

        # The "#" is how a person writes a hashtag, not part of the slug
        assert _constraints(project, "project")["slugs"] == ["web_team"]

    def test_watchers_are_resolved_to_phids(self):
        project = _project_app()

        with patch(
            "phabfive.project.core.resolve_user_phids",
            return_value={"@me": ("PHID-USER-admin", "admin")},
        ) as resolve:
            project.search(watchers=["@me"])

        assert _constraints(project, "project")["watchers"] == ["PHID-USER-admin"]
        assert resolve.call_args.kwargs["option"] == "--watcher"

    def test_watching_is_not_membership(self):
        """Two constraints, so one must not be sent as the other."""
        project = _project_app()

        with patch(
            "phabfive.project.core.resolve_user_phids",
            return_value={"@me": ("PHID-USER-admin", "admin")},
        ):
            project.search(watchers=["@me"])

        assert "members" not in _constraints(project, "project")

    def test_a_filter_nobody_asked_for_is_not_sent(self):
        project = _project_app()

        project.search()

        assert set(_constraints(project, "project")) == {"status"}

    def test_a_hashtag_given_as_an_id_is_refused_by_name(self):
        project = _project_app()

        with pytest.raises(PhabfiveInputException, match="--slug"):
            project.search(ids=["#qa"])

        assert _calls(project, "project") == []

    def test_project_id_list_takes_the_three_spellings(self):
        assert project_id_list("12,20") == [12, 20]
        assert project_id_list(["12", 20]) == [12, 20]
        assert project_id_list(None) is None


class TestProjectOrder:
    def test_the_default_is_by_name(self):
        """What `project search` has always printed, now asked of the server."""
        project = _project_app()

        project.search()

        assert _calls(project, "project")[0]["order"] == "name"

    #: Every spelling the parser accepts and what project.search is asked
    #: for. Written out rather than derived from PROJECT_API_ORDERS: an
    #: assertion built from the same map the code reads it out of can only
    #: fail on a KeyError that would have raised first.
    SENT = {
        "name": "name",
        "name:asc": "name",
        "name:desc": ["-name"],
        "created": "newest",
        "created:asc": "oldest",
        "created:desc": "newest",
        "relevance": "relevance",
    }

    def test_the_expectation_covers_every_spelling(self):
        assert sorted(self.SENT) == sorted(PROJECT_ORDER_CHOICES)

    @pytest.mark.parametrize("choice", PROJECT_ORDER_CHOICES)
    def test_every_spelling_reaches_the_api(self, choice):
        project = _project_app()

        project.search(order=choice)

        assert _calls(project, "project")[0]["order"] == self.SENT[choice]

    def test_z_to_a_is_a_column_vector_phorge_has_no_builtin_for(self):
        project = _project_app()

        project.search(order="name:desc")

        assert _calls(project, "project")[0]["order"] == ["-name"]

    def test_an_invalid_order_is_refused_before_anything_is_fetched(self):
        project = _project_app()

        with pytest.raises(PhabfiveConfigException, match="Invalid order 'bogus'"):
            project.search(order="bogus")

        assert _calls(project, "project") == []

    def test_phorges_own_name_for_an_order_is_suggested(self):
        project = _project_app()

        with pytest.raises(PhabfiveConfigException) as error:
            project.search(order="newest")

        assert "Did you mean 'created'?" in str(error.value)

    def test_the_example_names_a_field_this_app_has(self):
        """Not maniphest's: a project has no "updated" order."""
        project = _project_app()

        with pytest.raises(PhabfiveConfigException) as error:
            project.search(order="bogus")

        assert "updated" not in str(error.value)
        assert "e.g. 'name:asc'" in str(error.value)

    def test_relevance_takes_no_direction(self):
        project = _project_app()

        with pytest.raises(PhabfiveConfigException, match="takes no direction"):
            project.search(order="relevance:desc")

    def test_the_results_are_returned_in_the_order_asked_for(self):
        project = _project_app([_project(2, "Beta"), _project(1, "alpha")])

        found = project.search(order="name")

        assert [p["Project"]["Name"] for p in found["projects"]] == ["alpha", "Beta"]

    def test_the_limit_is_applied_after_the_ordering(self):
        """An --icon search is two searches, and the cut comes after both.

        Phorge shows every milestone with the "milestone" icon whatever is
        stored on it, so `--icon=milestone` asks the server for ordinary
        projects and for milestones separately. Cutting either one to the
        limit would drop what the ordering puts first.
        """
        project = _project_app()
        plain = _project(2, "Beta")
        milestone = _project(1, "alpha")
        milestone["fields"]["milestone"] = 1

        def search(**kwargs):
            calls.append(kwargs)
            is_milestone = kwargs["constraints"].get("isMilestone")

            return {
                "data": [milestone] if is_milestone else [plain],
                "cursor": {"after": None},
            }

        calls = []
        search.calls = calls
        project.phab.project.search.side_effect = search

        found = project.search(icons=[PROJECT_MILESTONE_ICON], limit=1)

        assert len(calls) == 2
        assert [p["Project"]["Name"] for p in found["projects"]] == ["alpha"]


class TestProjectSearchCommand:
    @patch("phabfive.cli.project._get_project_app")
    def test_every_new_flag_reaches_the_app(self, get_app, restore_output_format):
        project = MagicMock()
        project.search.return_value = {"projects": []}
        get_app.return_value = project

        result = runner.invoke(
            project_app,
            [
                "search",
                "--ids",
                "12,20",
                "--phids",
                "PHID-PROJ-12",
                "--slug",
                "#web_team",
                "--watcher",
                "@me",
                "--order",
                "created:asc",
            ],
        )

        assert result.exit_code == 0, result.output
        kwargs = project.search.call_args.kwargs
        assert kwargs["ids"] == ["12", "20"]
        assert kwargs["phids"] == ["PHID-PROJ-12"]
        assert kwargs["slugs"] == ["#web_team"]
        assert kwargs["watchers"] == ["@me"]
        assert kwargs["order"] == "created:asc"

    @patch("phabfive.cli.project._get_project_app")
    def test_a_bad_id_is_one_line_and_exit_one(self, get_app, restore_output_format):
        project = MagicMock()
        project.search.side_effect = PhabfiveInputException("Invalid project ID '#qa'")
        get_app.return_value = project

        result = runner.invoke(project_app, ["search", "--ids", "#qa"])

        assert result.exit_code == 1
        assert "ERROR: Invalid project ID" in result.stderr

    @patch("phabfive.cli.project._get_project_app")
    def test_order_is_refused_with_a_spec_rather_than_ignored(
        self, get_app, restore_output_format
    ):
        get_app.return_value = MagicMock()

        result = runner.invoke(
            project_app, ["search", "--with", "searches.yaml", "--order", "created"]
        )

        assert result.exit_code == 1
        assert "--order cannot be combined with --with" in result.stderr

    @patch("phabfive.cli.project._get_project_app")
    def test_every_unspecced_flag_is_named_at_once(
        self, get_app, restore_output_format
    ):
        get_app.return_value = MagicMock()

        result = runner.invoke(
            project_app,
            ["search", "--with", "searches.yaml", "--slug", "qa", "--ids", "12"],
        )

        assert result.exit_code == 1
        assert "--ids" in result.stderr
        assert "--slug" in result.stderr


class TestPasteConstraints:
    """paste.search's own names, which are not maniphest.search's."""

    def test_ids_take_a_monogram_or_a_number(self):
        assert build_paste_search_constraints(ids="P12,13")["ids"] == [12, 13]

    def test_another_applications_monogram_is_refused(self):
        with pytest.raises(PhabfiveInputException, match="Expected format: P123"):
            build_paste_search_constraints(ids="T45")

    def test_phids_reach_the_api(self):
        constraints = build_paste_search_constraints(phids=["PHID-PASTE-1"])

        assert constraints["phids"] == ["PHID-PASTE-1"]

    def test_languages_reach_the_api(self):
        constraints = build_paste_search_constraints(languages="python,bash")

        assert constraints["languages"] == ["python", "bash"]

    def test_statuses_reach_the_api(self):
        constraints = build_paste_search_constraints(statuses=["archived"])

        assert constraints["statuses"] == ["archived"]

    def test_a_status_phorge_answers_with_nothing_is_refused_here(self):
        """An unknown status is an empty result, which reads like a fact."""
        with pytest.raises(PhabfiveConfigException, match="active, archived"):
            build_paste_search_constraints(statuses=["closed"])

    def test_the_dates_are_epoch_seconds(self):
        constraints = build_paste_search_constraints(
            created_after="7d", created_before="1d"
        )

        assert constraints["createdStart"] < constraints["createdEnd"]
        assert isinstance(constraints["createdStart"], int)

    def test_a_time_that_is_not_one_names_the_key(self):
        with pytest.raises(PhabfiveInputException, match="created-after"):
            build_paste_search_constraints(created_after="soon")

    def test_the_author_constraint_is_the_one_paste_search_has(self):
        """ "authors", where maniphest.search calls its own "authorPHIDs"."""
        constraints = build_paste_search_constraints(author_phids=["PHID-USER-admin"])

        assert constraints == {"authors": ["PHID-USER-admin"]}

    def test_nothing_asked_for_is_no_constraints_at_all(self):
        assert build_paste_search_constraints() == {}

    def test_paste_id_list_takes_the_three_spellings(self):
        assert paste_id_list("P12,13") == [12, 13]
        assert paste_id_list(["p12", 13]) == [12, 13]
        assert paste_id_list(None) is None

    @pytest.mark.parametrize(
        "asked,constraint",
        [
            ({"ids": "P12"}, "ids"),
            ({"phids": ["PHID-PASTE-1"]}, "phids"),
            ({"languages": "python"}, "languages"),
            ({"statuses": "active"}, "statuses"),
            ({"created_after": "7d"}, "createdStart"),
            ({"created_before": "1d"}, "createdEnd"),
            ({"author_phids": ["PHID-USER-admin"]}, "authors"),
            ({"text_query": "notes"}, "query"),
        ],
    )
    def test_each_constraint_reaches_the_endpoint(self, asked, constraint):
        """Built and then sent: a builder nothing forwards filters nothing."""
        paste = _paste_app()

        paste.paste_search(constraints=build_paste_search_constraints(**asked))

        assert constraint in _constraints(paste, "paste")


class TestPasteOrder:
    def test_the_default_is_newest_first(self):
        paste = _paste_app()

        paste.paste_search()

        assert _calls(paste, "paste")[0]["order"] == "newest"

    #: As TestProjectOrder.SENT: written out, not derived from the map the
    #: code reads the value out of.
    SENT = {
        "created": "newest",
        "created:asc": "oldest",
        "created:desc": "newest",
        "relevance": "relevance",
    }

    def test_the_expectation_covers_every_spelling(self):
        assert sorted(self.SENT) == sorted(PASTE_ORDER_CHOICES)

    @pytest.mark.parametrize("choice", PASTE_ORDER_CHOICES)
    def test_every_spelling_reaches_the_api(self, choice):
        paste = _paste_app()

        paste.paste_search(order=choice)

        assert _calls(paste, "paste")[0]["order"] == self.SENT[choice]

    def test_oldest_first_is_the_other_builtin(self):
        paste = _paste_app()

        paste.paste_search(order="created:asc")

        assert _calls(paste, "paste")[0]["order"] == "oldest"

    def test_a_title_order_paste_search_does_not_have_is_refused(self):
        """PhabricatorPasteQuery has no title order, so neither does this."""
        paste = _paste_app()

        with pytest.raises(PhabfiveConfigException, match="created, relevance"):
            paste.paste_search(order="title")

        assert _calls(paste, "paste") == []

    def test_the_results_are_returned_in_the_order_asked_for(self):
        paste = _paste_app([_paste(1), _paste(3), _paste(2)])

        found = paste.paste_search(order="created:asc")

        assert [p["_url"] for p in found["pastes"]] == [
            f"{URL}/P1",
            f"{URL}/P2",
            f"{URL}/P3",
        ]


class TestPasteSearchCommand:
    def _invoke(self, args, get_app):
        paste = MagicMock()
        paste.paste_search.return_value = {"pastes": []}
        get_app.return_value = paste

        return runner.invoke(paste_app, ["search", *args]), paste

    @patch("phabfive.cli.paste._get_paste_app")
    def test_every_new_flag_reaches_the_app(self, get_app, restore_output_format):
        result, paste = self._invoke(
            [
                "--ids",
                "P12",
                "--phids",
                "PHID-PASTE-1",
                "--language",
                "python",
                "--status",
                "archived",
                "--created-after",
                "7d",
                "--order",
                "created:asc",
            ],
            get_app,
        )

        assert result.exit_code == 0, result.output
        kwargs = paste.paste_search.call_args.kwargs
        constraints = kwargs["constraints"]
        assert constraints["ids"] == [12]
        assert constraints["phids"] == ["PHID-PASTE-1"]
        assert constraints["languages"] == ["python"]
        assert constraints["statuses"] == ["archived"]
        assert "createdStart" in constraints
        assert kwargs["order"] == "created:asc"

    @pytest.mark.parametrize(
        "args",
        [
            ["--ids", "P12"],
            ["--phids", "PHID-PASTE-1"],
            ["--language", "python"],
            ["--status", "archived"],
            ["--created-after", "7d"],
            ["--created-before", "7d"],
        ],
    )
    @patch("phabfive.cli.paste._get_paste_app")
    def test_each_new_filter_lifts_the_bare_search_guard(
        self, get_app, args, restore_output_format
    ):
        result, paste = self._invoke(args, get_app)

        assert result.exit_code == 0, result.output
        paste.paste_search.assert_called_once()

    @patch("phabfive.cli.paste._get_paste_app")
    def test_order_alone_does_not_lift_it(self, get_app, restore_output_format):
        """It says how to sort a search, not which pastes to look at."""
        result, paste = self._invoke(["--order", "created"], get_app)

        assert result.exit_code == 2
        assert "Usage:" in result.output
        paste.paste_search.assert_not_called()

    @patch("phabfive.cli.paste._get_paste_app")
    def test_an_unknown_status_is_one_line_and_exit_one(
        self, get_app, restore_output_format
    ):
        result, paste = self._invoke(["--status", "closed"], get_app)

        assert result.exit_code == 1
        assert "Unknown paste status" in result.stderr
        paste.paste_search.assert_not_called()

    @patch("phabfive.cli.paste._get_paste_app")
    def test_a_filter_is_refused_with_a_spec_rather_than_ignored(
        self, get_app, restore_output_format
    ):
        result, _ = self._invoke(
            ["--with", "searches.yaml", "--language", "python"], get_app
        )

        assert result.exit_code == 1
        assert "--language cannot be combined with --with" in result.stderr


class TestUserConstraints:
    def test_ids_reach_the_api_as_numbers(self):
        user = _user_app()

        user.search(ids="1,2")

        assert _constraints(user, "user")["ids"] == [1, 2]

    def test_phids_reach_the_api(self):
        user = _user_app()

        user.search(phids=["PHID-USER-admin"])

        assert _constraints(user, "user")["phids"] == ["PHID-USER-admin"]

    def test_usernames_are_the_exact_constraint_not_namelike(self):
        user = _user_app()

        user.search(usernames="admin,deploy.bot")

        constraints = _constraints(user, "user")
        assert constraints["usernames"] == ["admin", "deploy.bot"]
        assert "nameLike" not in constraints

    def test_the_substring_filter_is_still_namelike(self):
        """The deliberate choice at user.py: query matches whole words only."""
        user = _user_app()

        user.search(query="holm")

        assert _constraints(user, "user")["nameLike"] == "holm"

    def test_both_can_narrow_one_search(self):
        user = _user_app()

        user.search(usernames="admin", query="adm")

        constraints = _constraints(user, "user")
        assert constraints["usernames"] == ["admin"]
        assert constraints["nameLike"] == "adm"

    def test_the_dates_are_epoch_seconds(self):
        user = _user_app()

        user.search(created_after="30d", created_before="1d")

        constraints = _constraints(user, "user")
        assert constraints["createdStart"] < constraints["createdEnd"]

    def test_a_shortcut_is_not_an_exact_username(self):
        """The constraint matches the stored name, so "@me" finds nobody."""
        user = _user_app()

        with pytest.raises(PhabfiveInputException, match="--username matches"):
            user.search(usernames="@me")

        assert _calls(user, "user") == []

    def test_a_username_given_as_an_id_is_refused_by_name(self):
        user = _user_app()

        with pytest.raises(PhabfiveInputException, match="--usernames"):
            user.search(ids=["admin"])

        assert _calls(user, "user") == []

    def test_user_id_list_takes_the_three_spellings(self):
        assert user_id_list("1,2") == [1, 2]
        assert user_id_list(["1", 2]) == [1, 2]
        assert user_id_list(None) is None


class TestUserOrder:
    def test_the_default_is_by_username(self):
        """What `user search` has always printed, now asked of the server.

        It is a column vector rather than a builtin because
        PhabricatorPeopleQuery has no builtin order by name at all.
        """
        user = _user_app()

        user.search(query="a")

        assert _calls(user, "user")[0]["order"] == ["username"]

    #: As TestProjectOrder.SENT.
    SENT = {
        "username": ["username"],
        "username:asc": ["username"],
        "username:desc": ["-username"],
        "created": "newest",
        "created:asc": "oldest",
        "created:desc": "newest",
        "relevance": "relevance",
    }

    def test_the_expectation_covers_every_spelling(self):
        assert sorted(self.SENT) == sorted(USER_ORDER_CHOICES)

    @pytest.mark.parametrize("choice", USER_ORDER_CHOICES)
    def test_every_spelling_reaches_the_api(self, choice):
        user = _user_app()

        user.search(query="a", order=choice)

        assert _calls(user, "user")[0]["order"] == self.SENT[choice]

    def test_newest_first_is_a_builtin(self):
        user = _user_app()

        user.search(query="a", order="created")

        assert _calls(user, "user")[0]["order"] == "newest"

    def test_an_invalid_order_is_refused_before_anything_is_fetched(self):
        user = _user_app()

        with pytest.raises(PhabfiveConfigException, match="username, created"):
            user.search(query="a", order="title")

        assert _calls(user, "user") == []

    def test_the_results_are_returned_in_the_order_asked_for(self):
        user = _user_app([_user(1, "viola"), _user(2, "admin")])

        records = user.search(query="a", order="username")

        assert [r["User"]["Username"] for r in records] == ["admin", "viola"]

    def test_the_limit_reaches_the_server_with_the_order(self):
        """Which is what makes --limit the first N of the order asked for."""
        user = _user_app([_user(1, "admin")])

        user.search(usernames="admin", limit=5)

        call = _calls(user, "user")[0]
        assert call["limit"] == 5
        assert call["order"] == ["username"]


class TestUserSearchCommand:
    def _invoke(self, args):
        user = _user_app()

        with patch("phabfive.user.User", return_value=user):
            return runner.invoke(user_app, ["search", *args]), user

    def test_every_new_flag_reaches_the_api(self, restore_output_format):
        result, user = self._invoke(
            [
                "--ids",
                "1",
                "--phids",
                "PHID-USER-admin",
                "--usernames",
                "admin",
                "--created-after",
                "30d",
                "--order",
                "created",
            ]
        )

        assert result.exit_code == 0, result.output
        call = _calls(user, "user")[0]
        assert call["constraints"]["ids"] == [1]
        assert call["constraints"]["phids"] == ["PHID-USER-admin"]
        assert call["constraints"]["usernames"] == ["admin"]
        assert "createdStart" in call["constraints"]
        assert call["order"] == "newest"

    @pytest.mark.parametrize(
        "args",
        [
            ["--ids", "1"],
            ["--phids", "PHID-USER-admin"],
            ["--usernames", "admin"],
            ["--created-after", "30d"],
            ["--created-before", "30d"],
        ],
    )
    def test_each_new_filter_lifts_the_bare_search_guard(
        self, args, restore_output_format
    ):
        result, user = self._invoke(args)

        assert result.exit_code == 0, result.output
        assert _calls(user, "user")

    def test_order_alone_does_not_lift_it(self, restore_output_format):
        result, user = self._invoke(["--order", "created"])

        assert result.exit_code == 2
        assert "Usage:" in result.output
        assert _calls(user, "user") == []

    def test_a_bad_id_is_one_line_and_exit_one(self, restore_output_format):
        result, user = self._invoke(["--ids", "admin"])

        assert result.exit_code == 1
        assert "ERROR: Invalid user ID" in result.stderr
        assert _calls(user, "user") == []


class TestTheOrderGrammarIsOneGrammar:
    """What the three tables have to satisfy, whoever adds the fourth."""

    TABLES = {
        "project": (
            PROJECT_ORDER_FIELDS,
            PROJECT_ORDER_DIRECTIONS,
            PROJECT_ORDER_CHOICES,
            PROJECT_ORDER_SUGGESTIONS,
            PROJECT_API_ORDERS,
        ),
        "paste": (
            PASTE_ORDER_FIELDS,
            PASTE_ORDER_DIRECTIONS,
            PASTE_ORDER_CHOICES,
            PASTE_ORDER_SUGGESTIONS,
            PASTE_API_ORDERS,
        ),
        "user": (
            USER_ORDER_FIELDS,
            USER_ORDER_DIRECTIONS,
            USER_ORDER_CHOICES,
            USER_ORDER_SUGGESTIONS,
            USER_API_ORDERS,
        ),
    }

    @pytest.mark.parametrize("app", sorted(TABLES))
    def test_every_accepted_spelling_has_an_api_order_behind_it(self, app):
        """Red for a field added to a table with nothing to send for it."""
        fields, directions, choices, _, api_orders = self.TABLES[app]

        for choice in choices:
            assert parse_order(choice, fields, directions) in api_orders

    @pytest.mark.parametrize("app", sorted(TABLES))
    def test_no_api_order_is_unreachable(self, app):
        fields, directions, choices, _, api_orders = self.TABLES[app]

        reached = {parse_order(choice, fields, directions) for choice in choices}
        assert reached == set(api_orders)

    @pytest.mark.parametrize("app", sorted(TABLES))
    def test_every_hint_points_at_a_field_this_app_has(self, app):
        """A suggestion naming another app's field is worse than none."""
        fields, directions, _, suggestions, _ = self.TABLES[app]

        for hint in suggestions.values():
            assert parse_order(hint, fields, directions)

    @pytest.mark.parametrize("app", sorted(TABLES))
    def test_a_hint_is_never_offered_for_a_spelling_that_works(self, app):
        fields, _, _, suggestions, _ = self.TABLES[app]

        assert not set(suggestions) & set(fields)

    @pytest.mark.parametrize("app", sorted(TABLES))
    def test_completion_offers_the_fields_then_the_directions(self, app):
        fields, directions, _, _, _ = self.TABLES[app]

        assert complete_order_value("", fields, directions) == list(fields)

        directional = next(f for f in fields if directions[f])
        assert complete_order_value(f"{directional}:", fields, directions) == [
            f"{directional}:asc",
            f"{directional}:desc",
        ]

    @pytest.mark.parametrize("app", sorted(TABLES))
    def test_a_directionless_field_completes_nothing_after_the_colon(self, app):
        fields, directions, _, _, _ = self.TABLES[app]

        for field in fields:
            if directions[field] is None:
                assert complete_order_value(f"{field}:", fields, directions) == []


class TestSortRecords:
    """The client-side tie-break over what the server already ordered."""

    RECORDS = [{"id": 2, "name": "b"}, {"id": 1, "name": "a"}]
    KEYS = {"id": lambda record: record["id"]}

    def test_a_field_with_a_key_is_sorted(self):
        assert [
            r["id"] for r in sort_records(self.RECORDS, "id", "asc", self.KEYS)
        ] == [
            1,
            2,
        ]

    def test_desc_reverses_it(self):
        assert [
            r["id"] for r in sort_records(self.RECORDS, "id", "desc", self.KEYS)
        ] == [2, 1]

    def test_a_field_with_no_key_keeps_the_servers_order(self):
        """Which is what relevance means: the response carries no rank."""
        assert sort_records(self.RECORDS, "relevance", None, self.KEYS) == self.RECORDS

    def test_the_input_is_not_mutated(self):
        original = list(self.RECORDS)

        sort_records(self.RECORDS, "id", "asc", self.KEYS)

        assert self.RECORDS == original


class TestTheCompletersAreWiredToTheirOwnApp:
    """One wrapper per app over one grammar, and no way to tell them apart.

    `complete_order_value` is exercised directly above, which says the
    grammar is right and nothing about which table each command handed it.
    A wrapper pointed at another app's fields completes spellings that
    command refuses, and every assertion above still passes - so the
    wrappers are compared with their own app's tables here.
    """

    WRAPPERS = {
        "project": ("phabfive.cli.project", "complete_project_order"),
        "paste": ("phabfive.cli.paste", "complete_paste_order"),
        "user": ("phabfive.cli.user", "complete_user_order"),
    }

    FIELDS = {
        "project": PROJECT_ORDER_FIELDS,
        "paste": PASTE_ORDER_FIELDS,
        "user": USER_ORDER_FIELDS,
    }

    DIRECTIONS = {
        "project": PROJECT_ORDER_DIRECTIONS,
        "paste": PASTE_ORDER_DIRECTIONS,
        "user": USER_ORDER_DIRECTIONS,
    }

    @staticmethod
    def _wrapper(app):
        import importlib

        module, name = TestTheCompletersAreWiredToTheirOwnApp.WRAPPERS[app]
        return getattr(importlib.import_module(module), name)

    @pytest.mark.parametrize("app", sorted(WRAPPERS))
    def test_a_bare_tab_offers_this_app_s_own_fields(self, app):
        assert self._wrapper(app)("") == list(self.FIELDS[app])

    @pytest.mark.parametrize("app", sorted(WRAPPERS))
    def test_the_directions_come_after_this_app_s_own_field(self, app):
        fields, directions = self.FIELDS[app], self.DIRECTIONS[app]
        directional = next(field for field in fields if directions[field])

        assert self._wrapper(app)(f"{directional}:") == [
            f"{directional}:asc",
            f"{directional}:desc",
        ]

    @pytest.mark.parametrize("app", sorted(WRAPPERS))
    def test_another_app_s_field_is_not_offered(self, app):
        """The assertion a wrapper handed the wrong table fails."""
        others = {
            field
            for other, fields in self.FIELDS.items()
            if other != app
            for field in fields
        } - set(self.FIELDS[app])

        for field in others:
            assert self._wrapper(app)(field) == []

    def test_the_paste_status_completer_offers_the_two_paste_search_knows(self):
        from phabfive.cli.paste import complete_paste_status
        from phabfive.constants import PASTE_STATUS_CHOICES

        assert complete_paste_status("") == list(PASTE_STATUS_CHOICES)
        assert complete_paste_status("arch") == ["archived"]
        assert complete_paste_status("open") == []

    def test_the_username_completer_offers_no_shortcut(self):
        """`--usernames` matches the stored name exactly and refuses "@me".

        Offering it would complete a value guaranteed to exit 1.
        """
        from phabfive.cli.completers import (
            complete_user_list_filter,
            complete_username_list,
        )

        with patch(
            "phabfive.cli.completers._cached_user_records",
            return_value=[{"username": "admin", "realName": None, "disabled": False}],
        ):
            assert complete_username_list("") == ["admin"]
            assert complete_username_list("@") == []
            assert complete_username_list("admin,") == ["admin,admin"]

            # The filter completer, which is a different option's, still does
            assert ("@me", "yourself") in complete_user_list_filter("")


class TestTheRegistryRestatesTheseTables:
    """`phabfive/spec/registry.py` may import nothing but the standard library.

    So the project statuses and colours are written out there a second time,
    and these are what keep the restatement from drifting: adding a status or
    a colour to `phabfive.constants` turns one of them red.
    """

    def test_the_declared_statuses_are_the_ones_the_command_accepts(self):
        from phabfive.constants import PROJECT_STATUS_CHOICES
        from phabfive.spec.registry import field_by_name

        field = field_by_name("status", "project", "search")

        assert field is not None
        assert list(field.choices) == list(PROJECT_STATUS_CHOICES)

    def test_the_declared_colours_are_the_ones_the_command_accepts(self):
        from phabfive.constants import PROJECT_COLORS
        from phabfive.spec.registry import field_by_name

        field = field_by_name("colors", "project", "search")

        assert field is not None
        assert list(field.choices) == list(PROJECT_COLORS)


class TestASubDayTimeIsNotNow:
    """`1h` used to mean "now", on every command that takes a TIME.

    `parse_time_with_unit("1h")` is 1/24 of a day and `days_ago_to_timestamp`
    truncated it to whole days, so `--created-after=1h` asked for records
    created after this instant and found none, while `--created-before=1h`
    asked for everything. Pinned per app, because #479 put the same helper on
    two more commands.
    """

    @pytest.mark.parametrize(
        "value,seconds",
        # "m" is months, 30 days each - not minutes, which phabfive has no
        # unit for
        [("1h", 3600), ("12h", 43200), ("1d", 86400), ("1m", 30 * 24 * 3600)],
    )
    def test_an_hour_is_an_hour(self, value, seconds):
        import time

        from phabfive.maniphest.utils import days_ago_to_timestamp
        from phabfive.spec.times import parse_time_with_unit

        now = int(time.time())
        answered = days_ago_to_timestamp(parse_time_with_unit(value))

        assert abs((now - answered) - seconds) <= 1

    def test_a_paste_search_an_hour_back_is_not_a_search_from_now(self):
        import time

        paste = _paste_app()

        paste.paste_search(
            constraints=build_paste_search_constraints(created_after="1h")
        )

        start = _constraints(paste, "paste")["createdStart"]
        assert int(time.time()) - start > 3000

    def test_a_user_search_an_hour_back_is_not_a_search_from_now(self):
        import time

        user = _user_app()

        user.search(created_after="1h")

        start = _constraints(user, "user")["createdStart"]
        assert int(time.time()) - start > 3000


class TestTheInvalidOrderMessage:
    """The example names a field of the app being used, and is pinned.

    `_invalid` picks the first directional field of the table it was handed,
    so maniphest's example changed from a hard-coded "updated:asc" to
    "priority:asc" when the message was shared - and nothing went red. These
    are what make the next such change deliberate.
    """

    APPS = {
        "maniphest": ("priority:asc", "priority, updated, created, closed"),
        "project": ("name:asc", "name, created, relevance"),
        "paste": ("created:asc", "created, relevance"),
        "user": ("username:asc", "username, created, relevance"),
    }

    TABLES = {
        "project": (PROJECT_ORDER_FIELDS, PROJECT_ORDER_DIRECTIONS),
        "paste": (PASTE_ORDER_FIELDS, PASTE_ORDER_DIRECTIONS),
        "user": (USER_ORDER_FIELDS, USER_ORDER_DIRECTIONS),
    }

    @pytest.mark.parametrize("app", sorted(APPS))
    def test_the_example_and_the_field_list_are_this_app_s(self, app):
        if app == "maniphest":
            from phabfive.constants import (
                MANIPHEST_ORDER_DIRECTIONS,
                MANIPHEST_ORDER_FIELDS,
            )

            fields, directions = MANIPHEST_ORDER_FIELDS, MANIPHEST_ORDER_DIRECTIONS
        else:
            fields, directions = self.TABLES[app]

        example, listed = self.APPS[app]

        with pytest.raises(PhabfiveConfigException) as error:
            parse_order("bogus", fields, directions)

        message = str(error.value)
        assert "Invalid order 'bogus'" in message
        assert f"Valid fields: {listed}" in message
        assert f"e.g. '{example}'" in message
