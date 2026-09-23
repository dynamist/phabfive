# -*- coding: utf-8 -*-
"""Creating a project and a paste from a spec: the keys, and the builders.

Three things are pinned here, and they are the three halves of #481 that can
be pinned without a server:

1. **The key set.** A `projects:` or `pastes:` item may hold exactly what
   `project create` and `paste create` take, because both read one
   declaration in `phabfive.spec.registry`. A key a spec could write and no
   command reads - or a flag a command offers and no spec can write - is the
   drift the registry exists to end (#295), and it is now a create-side
   drift as well as a search-side one.
2. **The build/apply seam.** `Project.project_create_transactions` and
   `Paste.paste_create_transactions` take values that are already resolved
   and send nothing. That is what lets a create spec resolve every name in a
   whole document in one pass and then build each object from the answers,
   where the command resolves one object's names and builds it once. Both
   ends produce the same transactions, so neither can grow a field the other
   does not send.
3. **The hashtag.** A project cannot be deleted or archived through Conduit,
   so a name Phorge would refuse has to be found before the *first* write and
   not halfway through a batch. `Project.hashtag_conflicts` answers for a
   whole document at once, which is what a validation pass over one asks.

What is deliberately not here: applying a project or paste spec end to end.
`phabfive.spec.create.plan_create` still refuses every object type but
"task", so there is nothing to drive yet; see this file's companion issue
report for what remains to be wired.
"""

from unittest.mock import MagicMock

import pytest
from typer.main import get_command

from phabfive.cli.paste import paste_app
from phabfive.cli.project import project_app
from phabfive.exceptions import PhabfiveConfigException, PhabfiveDataException
from phabfive.paste.core import Paste
from phabfive.project.core import Project, check_project_create_options
from phabfive.spec.envelope import Spec
from phabfive.spec.registry import cli_flags, field_by_name, fields_for, spec_keys


def _project():
    """A Project whose client is a mock, built without touching a config."""
    project = Project.__new__(Project)
    project.phab = MagicMock()
    project.conf = {}
    # No project holds any hashtag unless a test says one does.
    project.phab.project.search.return_value = {"data": []}

    return project


def _paste():
    paste = Paste.__new__(Paste)
    paste.phab = MagicMock()
    paste.conf = {}

    return paste


def _types(transactions):
    return [one["type"] for one in transactions]


def _by_type(transactions):
    return {one["type"]: one["value"] for one in transactions}


#: Flags that are about the run and not about the object being created, so
#: no spec key answers to them: a spec is a file, and how to print it, what
#: to confirm and whether to send it at all are the command's.
NOT_SPEC_FLAGS = frozenset({"--dry-run", "--interactive", "--yes", "--help"})


def _flags(app, command):
    """Every long flag one command actually offers."""
    found = get_command(app).commands[command]

    return {opt for param in found.params for opt in param.opts if opt.startswith("--")}


# --------------------------------------------------------------------------
# 1. The key set
# --------------------------------------------------------------------------


class TestTheDeclaredKeys:
    def test_a_project_item_holds_what_project_create_takes(self):
        assert spec_keys("project", "create") == {
            "name",
            "description",
            "icon",
            "color",
            "slugs",
            "members",
            "parent",
            "milestone-of",
            "space",
            "visible-to",
            "editable-by",
            "joinable-by",
        }

    def test_a_paste_item_holds_what_paste_create_takes(self):
        assert spec_keys("paste", "create") == {
            "title",
            "content",
            "language",
            "projects",
            "subscribers",
            "visible-to",
            "editable-by",
        }

    def test_the_four_keys_a_task_spec_could_not_express(self):
        """#481's other half: the flag path had these and a spec did not."""
        assert {"status", "column", "visible-to", "editable-by"} <= spec_keys(
            "task", "create"
        )

    def test_every_declared_create_flag_exists_on_its_command(self):
        """A flag named in the registry and absent from the command is a lie.

        The create-side half of `tests/test_search_template_keys.py`. It runs
        per command rather than once, because three commands share the key
        set: `--visible-to` is one declaration and three flags.
        """
        assert cli_flags("project", "create") <= _flags(project_app, "create")
        assert cli_flags("paste", "create") <= _flags(paste_app, "create")

    def test_every_create_flag_is_a_declared_key(self):
        """The other direction, which is the one drift actually happens in.

        The inclusion above catches a registry key whose flag does not
        exist - a typo in the registry. It cannot catch the opposite, and
        the opposite is what a person adding a feature does: give
        `paste create` a new flag, forget the spec key, and a spec can no
        longer express what the command can. That is what made the key set
        two overlapping sets before #481.

        `NOT_SPEC_FLAGS` is the allow-list of flags that are about *this
        run* rather than about the object: how to answer, whether to ask,
        whether to send anything at all. A spec says none of those - a
        create spec is applied by `phabfive apply`, which owns its own
        `--dry-run` and `--yes`.
        """
        for app, object_type in ((project_app, "project"), (paste_app, "paste")):
            offered = _flags(app, "create") - NOT_SPEC_FLAGS

            assert offered <= cli_flags(object_type, "create"), object_type

    def test_a_paste_names_its_title_field_the_way_a_task_does(self):
        """One declaration for both, because Phorge's API agrees they are one.

        The transaction is `title` on `maniphest.edit` and on `paste.edit`
        alike, and the web UI labels both "Name". The asymmetry CLAUDE.md
        warns about - `fields.title` against `fields.name` - is on the read
        side, and is not a spec key.
        """
        title = field_by_name("title", "paste", "create")

        assert title is not None
        assert title is field_by_name("title", "task", "create")
        assert title.cli == "--title"

    def test_a_project_name_is_positional_and_so_has_no_flag(self):
        name = field_by_name("name", "project", "create")

        assert name is not None
        assert name.cli is None
        assert "--name" not in _flags(project_app, "create")

    def test_nothing_creates_a_passphrase(self):
        """Phorge exposes no `passphrase.edit`; the key set is empty, not wrong."""
        assert fields_for("passphrase", "create") == ()


class TestOfflineValidationReachesTheNewKeys:
    """A declared key is checked wherever it is written, complete or not.

    The three create pairs are not in `DECLARED_COMPLETE` yet - see the note
    there - so an *undeclared* key is still left alone. What changes with
    #481 is that a declared one now has its value checked in a `projects:`
    and a `pastes:` item, where before only `color:` and `icon:` did.
    """

    def _problems(self, body):
        return Spec.from_data({"kind": "create", **body}).validate_offline()

    def test_a_colour_phorge_does_not_have_is_refused_without_a_token(self):
        codes = [one.code for one in self._problems({"projects": [{"color": "beige"}]})]

        assert "unknown-value" in codes

    def test_a_policy_outside_the_grammar_is_refused_on_a_project(self):
        problems = self._problems(
            {"projects": [{"name": "Platform", "visible-to": "everyone"}]}
        )

        assert [one.code for one in problems] == ["bad-policy"]

    def test_a_policy_outside_the_grammar_is_refused_on_a_paste(self):
        """The same key, the same check, on the object type that never had it."""
        problems = self._problems(
            {"pastes": [{"title": "Notes", "editable-by": "everyone"}]}
        )

        assert [one.code for one in problems] == ["bad-policy"]

    def test_a_key_written_empty_is_a_key_that_is_not_there(self):
        assert (
            self._problems({"projects": [{"name": "Platform", "description": None}]})
            == []
        )

    def test_a_project_spec_that_is_right_reports_nothing(self):
        assert (
            self._problems(
                {
                    "projects": [
                        {
                            "name": "Platform",
                            "description": "The platform team",
                            "icon": "infrastructure",
                            "color": "blue",
                            "slugs": ["platform", "plat"],
                            "members": ["@me"],
                            "parent": "#engineering",
                            "space": "S1",
                            "visible-to": "users",
                        }
                    ],
                    "pastes": [
                        {
                            "title": "Deploy notes",
                            "content": "run it\n",
                            "language": "yaml",
                            "visible-to": "users",
                        }
                    ],
                }
            )
            == []
        )


# --------------------------------------------------------------------------
# 2. The project builder
# --------------------------------------------------------------------------


class TestProjectCreateTransactions:
    def test_it_sends_nothing_when_it_is_told_not_to_check_the_hashtag(self):
        """The property that makes it usable from a plan: no requests.

        Asserted over the whole recorded call list rather than over one
        method, because "project.search was not called" proves nothing when
        the code under test would have called `project.query`.
        """
        project = _project()

        project.project_create_transactions(
            "Platform",
            description="The platform team",
            members=[("PHID-USER-a", "@alice")],
            parent=("PHID-PROJ-eng", "#engineering"),
            space=("PHID-SPCE-1", "S1 Default"),
            policies={"view": ("users", "All Users")},
            check_hashtag=False,
        )

        assert project.phab.mock_calls == []

    def test_every_field_becomes_a_transaction_and_a_preview_line(self):
        project = _project()

        transactions, changes = project.project_create_transactions(
            "Platform",
            description="The platform team",
            icon="infrastructure",
            color="blue",
            slugs=["#platform", "plat"],
            members=[("PHID-USER-a", "@alice"), ("PHID-USER-b", "@bob")],
            parent=("PHID-PROJ-eng", "#engineering"),
            space=("PHID-SPCE-1", "S1 Default"),
            policies={
                "view": ("users", "All Users"),
                "edit": ("PHID-PROJ-eng", "#engineering"),
                "join": ("admin", "Administrators"),
            },
            check_hashtag=False,
        )

        assert _by_type(transactions) == {
            "name": "Platform",
            "parent": "PHID-PROJ-eng",
            "description": "The platform team",
            "icon": "infrastructure",
            "color": "blue",
            "slugs": ["platform", "plat"],
            "members.add": ["PHID-USER-a", "PHID-USER-b"],
            "space": "PHID-SPCE-1",
            "view": "users",
            "edit": "PHID-PROJ-eng",
            "join": "admin",
        }

        # --dry-run matters more here than anywhere else in phabfive, because
        # a project cannot be undone: every transaction has to be previewed,
        # under the label the web UI uses and with a name rather than a PHID.
        assert [change["field"] for change in changes] == [
            "Name",
            "Parent",
            "Description",
            "Icon",
            "Color",
            "Hashtags",
            "Members",
            "Space",
            "Visible To",
            "Editable By",
            "Joinable By",
        ]
        assert {change["new"] for change in changes} >= {
            "#engineering",
            "#platform, #plat",
            "@alice, @bob",
            "S1 Default",
            "All Users",
            "Administrators",
        }
        assert all(change["old"] is None for change in changes)

    def test_a_collection_is_filled_with_add_and_never_with_set(self):
        """`slugs` is the one exception, and it is why a spec may not anchor.

        `project.edit` has `members.add` and `watchers.add` but no
        `slugs.add`: the transaction replaces the whole list. That is safe
        only on an object being created, which is exactly what this builds.
        """
        project = _project()

        transactions, _ = project.project_create_transactions(
            "Platform",
            slugs=["plat"],
            members=["PHID-USER-a"],
            check_hashtag=False,
        )
        collections = [
            kind
            for kind in _types(transactions)
            if kind.endswith((".add", ".set", ".remove")) or kind == "slugs"
        ]

        assert collections == ["slugs", "members.add"]
        assert not [kind for kind in _types(transactions) if kind.endswith(".set")]

    def test_a_bare_phid_is_accepted_where_a_pair_is(self):
        project = _project()

        transactions, changes = project.project_create_transactions(
            "Platform", parent="PHID-PROJ-eng", check_hashtag=False
        )

        assert _by_type(transactions)["parent"] == "PHID-PROJ-eng"
        assert changes[1] == {
            "field": "Parent",
            "old": None,
            "new": "PHID-PROJ-eng",
        }

    def test_a_member_named_twice_is_one_member(self):
        project = _project()

        transactions, changes = project.project_create_transactions(
            "Platform",
            members=[("PHID-USER-a", "@alice"), ("PHID-USER-a", "@alice")],
            check_hashtag=False,
        )

        assert _by_type(transactions)["members.add"] == ["PHID-USER-a"]
        assert changes[-1]["new"] == "@alice"

    def test_a_milestone_takes_no_icon_colour_or_slug(self):
        project = _project()

        with pytest.raises(PhabfiveConfigException) as error:
            project.project_create_transactions(
                "Sprint 2",
                milestone_of=("PHID-PROJ-plat", "#platform"),
                icon="folder",
                check_hashtag=False,
            )

        assert "milestone takes no" in str(error.value)

    def test_a_milestone_is_never_asked_about_its_hashtag(self):
        """Any number of milestones may share a name; only the others clash."""
        project = _project()

        project.project_create_transactions(
            "Sprint 2", milestone_of=("PHID-PROJ-plat", "#platform")
        )

        assert project.phab.project.search.call_count == 0

    def test_a_project_without_a_milestone_parent_is_asked_about_its_hashtag(self):
        project = _project()

        project.project_create_transactions("Platform")

        project.phab.project.search.assert_called_once_with(
            constraints={"slugs": ["Platform"]}
        )

    def test_a_name_whose_hashtag_is_taken_is_refused_before_anything_is_sent(self):
        project = _project()
        project.phab.project.search.return_value = {
            "data": [
                {
                    "id": 7,
                    "phid": "PHID-PROJ-other",
                    "fields": {"name": "Platform Team", "slug": "platform"},
                }
            ]
        }

        with pytest.raises(PhabfiveDataException) as error:
            project.project_create_transactions("Platform")

        assert "generates the same hashtag" in str(error.value)
        assert project.phab.project.edit.call_count == 0

    def test_a_name_is_checked_before_it_is_stripped(self):
        project = _project()

        transactions, _ = project.project_create_transactions(
            "  Platform  ", check_hashtag=False
        )

        assert _by_type(transactions)["name"] == "Platform"

    def test_nothing_is_built_without_a_name(self):
        project = _project()

        with pytest.raises(PhabfiveConfigException):
            project.project_create_transactions("   ", check_hashtag=False)


class TestTheOptionCheckIsShared:
    """The same combinations are refused whoever asks, and before any lookup."""

    def test_a_parent_and_a_milestone_parent_cannot_be_combined(self):
        with pytest.raises(PhabfiveConfigException) as error:
            check_project_create_options("Platform", parent="#a", milestone_of="#b")

        assert "either a subproject or a milestone" in str(error.value)

    def test_a_colour_phorge_does_not_have_is_refused(self):
        with pytest.raises(PhabfiveConfigException):
            check_project_create_options("Platform", color="beige")

    def test_it_answers_with_the_name_the_create_will_use(self):
        assert check_project_create_options("  Platform  ") == "Platform"

    def test_the_command_refuses_a_milestone_icon_before_it_looks_anything_up(self):
        """Which is the point of separating it: a wrong spec costs no request."""
        project = _project()

        with pytest.raises(PhabfiveConfigException):
            project.build_project_create(
                "Sprint 2", milestone_of="#platform", icon="folder"
            )

        assert project.phab.mock_calls == []


class TestHashtagConflicts:
    def test_it_answers_for_every_name_in_one_document(self):
        project = _project()
        taken = {
            "id": 7,
            "phid": "PHID-PROJ-other",
            "fields": {"name": "Platform Team", "slug": "platform"},
        }
        project.phab.project.search.side_effect = [
            {"data": [taken]},
            {"data": []},
        ]

        assert project.hashtag_conflicts(["Platform", "Backend"]) == {"Platform": taken}

    def test_a_name_written_twice_is_asked_about_once(self):
        project = _project()

        project.hashtag_conflicts(["Platform", "Platform"])

        assert project.phab.project.search.call_count == 1

    def test_the_project_being_edited_may_keep_its_own_hashtag(self):
        project = _project()
        project.phab.project.search.return_value = {
            "data": [{"phid": "PHID-PROJ-self", "fields": {"name": "Platform"}}]
        }

        assert (
            project.hashtag_conflicts(["Platform"], exclude_phid="PHID-PROJ-self") == {}
        )

    def test_a_failed_lookup_is_a_phabfive_exception(self):
        project = _project()
        project.phab.project.search.side_effect = RuntimeError("boom")

        with pytest.raises(PhabfiveDataException):
            project.hashtag_conflicts(["Platform"])

    def test_the_sentence_names_the_project_already_holding_it(self):
        other = {
            "id": 7,
            "phid": "PHID-PROJ-other",
            "fields": {"name": "Platform Team", "slug": "platform"},
        }

        message = Project.hashtag_taken_message("Platform", other)

        assert "Platform" in message and "Platform Team" in message


# --------------------------------------------------------------------------
# 3. The paste builder
# --------------------------------------------------------------------------


class TestPasteCreateTransactions:
    def test_it_sends_nothing(self):
        paste = _paste()

        paste.paste_create_transactions("Deploy notes", content="run it")

        assert paste.phab.mock_calls == []

    def test_the_transaction_is_title_and_not_name(self):
        """Paste's API field names are its own; CLAUDE.md's rule 5, as a test."""
        paste = _paste()

        transactions, changes = paste.paste_create_transactions(
            "Deploy notes", content="run it"
        )

        assert _by_type(transactions)["title"] == "Deploy notes"
        assert "name" not in _by_type(transactions)
        # The display label is the web UI's, which says Name for every app.
        assert changes[0]["field"] == "Name"

    def test_every_field_becomes_a_transaction(self):
        paste = _paste()

        transactions, changes = paste.paste_create_transactions(
            "Deploy notes",
            content="one\ntwo\n",
            language="yaml",
            tags=["PHID-PROJ-plat"],
            subscribers=["PHID-USER-a"],
            policies={"view": "users", "edit": "PHID-PROJ-plat"},
        )

        assert _by_type(transactions) == {
            "title": "Deploy notes",
            "text": "one\ntwo\n",
            "language": "yaml",
            "projects.add": ["PHID-PROJ-plat"],
            "subscribers.add": ["PHID-USER-a"],
            "view": "users",
            "edit": "PHID-PROJ-plat",
        }
        assert [change["field"] for change in changes] == [
            "Name",
            "Content",
            "Language",
            "Tags",
            "Subscribers",
            "Visible To",
            "Editable By",
        ]

    def test_the_content_is_summarised_and_never_printed_whole(self):
        paste = _paste()

        _, changes = paste.paste_create_transactions(
            "Big", content="\n".join(str(number) for number in range(500))
        )
        content = next(one for one in changes if one["field"] == "Content")

        assert "500 lines" in content["new"]

    def test_a_collection_is_filled_with_add_and_never_with_set(self):
        paste = _paste()

        transactions, _ = paste.paste_create_transactions(
            "Deploy notes",
            content="run it",
            tags=["PHID-PROJ-plat"],
            subscribers=["PHID-USER-a"],
        )

        assert not [kind for kind in _types(transactions) if kind.endswith(".set")]
        assert {"projects.add", "subscribers.add"} <= set(_types(transactions))

    def test_a_tag_may_carry_the_name_the_instance_answered_with(self):
        """A PHID is what is sent and never what a dry run prints."""
        paste = _paste()

        transactions, changes = paste.paste_create_transactions(
            "Deploy notes",
            content="run it",
            tags=[("PHID-PROJ-plat", "#platform")],
            subscribers=[("PHID-USER-a", "@alice"), ("PHID-USER-a", "@alice")],
        )

        assert _by_type(transactions)["projects.add"] == ["PHID-PROJ-plat"]
        assert _by_type(transactions)["subscribers.add"] == ["PHID-USER-a"]
        shown = {change["field"]: change["new"] for change in changes}
        assert shown["Tags"] == "#platform"
        assert shown["Subscribers"] == "@alice"

    def test_a_key_that_was_not_asked_for_is_left_out(self):
        """Conduit does not take None as a value."""
        paste = _paste()

        transactions, changes = paste.paste_create_transactions(
            "Deploy notes", content="run it"
        )

        assert "language" not in _by_type(transactions)
        assert [change["field"] for change in changes] == ["Name", "Content"]

    def test_create_from_content_builds_and_then_applies(self):
        paste = _paste()
        paste.phab.paste.edit.return_value = {"object": {"id": 42, "phid": "PHID-PSTE"}}

        created = paste.create_paste_from_content(
            title="Deploy notes", content="run it", language="yaml"
        )

        assert created == {"id": 42, "phid": "PHID-PSTE"}
        sent = paste.phab.paste.edit.call_args.kwargs["transactions"]
        assert _by_type(sent) == {
            "title": "Deploy notes",
            "text": "run it",
            "language": "yaml",
            "projects.add": [],
            "subscribers.add": [],
        }

    def test_a_policy_asked_for_is_resolved_once_and_sent(self):
        paste = _paste()
        paste.phab.paste.edit.return_value = {"object": {"id": 42, "phid": "PHID-PSTE"}}

        paste.create_paste_from_content(
            title="Secret", content="shh", visible_to="users"
        )
        sent = _by_type(paste.phab.paste.edit.call_args.kwargs["transactions"])

        assert sent["view"] == "users"


class TestThePasteCommandOffersThePolicyFlags:
    """UX consistency: `project create` has them, so `paste create` does too.

    Without them the registry would declare a `visible-to` a paste spec can
    write and no `paste` command can, which is the drift in the other
    direction.
    """

    def test_the_flags_exist(self):
        assert {"--visible-to", "--editable-by"} <= _flags(paste_app, "create")

    def test_a_policy_outside_the_grammar_never_reaches_the_instance(self):
        from typer.testing import CliRunner

        from phabfive.cli import app

        with pytest.MonkeyPatch.context() as patch:
            built = MagicMock()
            patch.setattr("phabfive.cli.paste._get_paste_app", lambda: built)
            result = CliRunner().invoke(
                app,
                [
                    "paste",
                    "create",
                    "Notes",
                    "--content=x",
                    "--visible-to=everyone",
                    "--yes",
                ],
            )

        assert result.exit_code == 1
        # Named, so that an exit 1 for some other reason cannot pass this.
        assert "--visible-to must be one of" in result.output
        assert built.create_paste_from_content.call_count == 0

    def test_a_policy_inside_the_grammar_reaches_the_app(self):
        from typer.testing import CliRunner

        from phabfive.cli import app

        with pytest.MonkeyPatch.context() as patch:
            built = MagicMock()
            built.create_paste_from_content.return_value = {"id": 42}
            patch.setattr("phabfive.cli.paste._get_paste_app", lambda: built)
            CliRunner().invoke(
                app,
                [
                    "paste",
                    "create",
                    "Notes",
                    "--content=x",
                    "--visible-to=users",
                    "--editable-by=admin",
                    "--yes",
                ],
            )

        kwargs = built.create_paste_from_content.call_args.kwargs
        assert kwargs["visible_to"] == "users"
        assert kwargs["editable_by"] == "admin"
