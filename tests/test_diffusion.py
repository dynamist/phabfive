# -*- coding: utf-8 -*-

# 3rd party imports
from unittest.mock import MagicMock, patch

import pytest

# phabfive imports
from phabfive.diffusion import Diffusion
from phabfive.diffusion.fetchers import fetch_uris
from phabfive.diffusion.formatters import format_repositories
from phabfive.exceptions import PhabfiveDataException


def _repo(name, short_name=None, status="active"):
    return {
        "id": 1,
        "phid": f"PHID-REPO-{name}",
        "fields": {
            "name": name,
            "shortName": short_name or name,
            "status": status,
        },
        "attachments": {"uris": {"uris": []}},
    }


def _phab_with_repos(repos):
    phab = MagicMock()
    phab.diffusion.repository.search.return_value = {"data": repos}
    return phab


@pytest.fixture
def diffusion():
    with (
        patch("phabfive.diffusion.core.Phabfive.__init__", return_value=None),
        patch("phabfive.diffusion.core.passphrase.Passphrase"),
    ):
        diffusion = Diffusion()
    diffusion.phab = MagicMock()
    diffusion.phab.diffusion.repository.edit.return_value = {
        "object": {"phid": "PHID-REPO-new"}
    }
    return diffusion


class TestEmptyInstance:
    """An instance without repositories is valid, not an error."""

    def test_repo_list_is_empty(self):
        assert format_repositories(_phab_with_repos([])) == []

    def test_uri_list_reports_unknown_repository(self):
        """No repositories means the one asked for does not exist.

        None rather than [] so the CLI can tell an unknown repository
        apart from one that simply has no URIs to list.
        """
        assert fetch_uris(_phab_with_repos([]), repo_id="myrepo") is None

    def test_uri_list_is_empty_for_repository_without_uris(self):
        """An existing repository with no URIs is an empty result, not an error."""
        phab = _phab_with_repos([_repo("myrepo")])

        assert fetch_uris(phab, repo_id="myrepo") == []

    def test_uri_list_reports_unknown_repository_among_others(self):
        phab = _phab_with_repos([_repo("myrepo")])

        assert fetch_uris(phab, repo_id="otherrepo") is None

    def test_repo_create_creates_first_repository(self, diffusion):
        diffusion.phab.diffusion.repository.search.return_value = {"data": []}

        assert diffusion.create_repository(name="myrepo") == "PHID-REPO-new"
        diffusion.phab.diffusion.repository.edit.assert_called_once()

    def test_uri_create_reports_missing_repository(self, diffusion):
        diffusion.phab.diffusion.repository.search.return_value = {"data": []}
        diffusion._validate_credential_type = MagicMock(return_value="PHID-CDTL-1")

        with pytest.raises(PhabfiveDataException, match="does not exist"):
            diffusion.create_uri(
                repository_name="myrepo",
                new_uri="https://example.com/repo.git",
                credential="K1",
            )


class TestRepoCreateDuplicateCheck:
    def test_rejects_exact_name(self, diffusion):
        diffusion.phab.diffusion.repository.search.return_value = {
            "data": [_repo("myrepo")]
        }

        with pytest.raises(PhabfiveDataException, match="already exists"):
            diffusion.create_repository(name="myrepo")
        diffusion.phab.diffusion.repository.edit.assert_not_called()

    def test_rejects_existing_short_name(self, diffusion):
        diffusion.phab.diffusion.repository.search.return_value = {
            "data": [_repo("My Repository", short_name="myrepo")]
        }

        with pytest.raises(PhabfiveDataException, match="already exists"):
            diffusion.create_repository(name="myrepo")

    def test_allows_name_contained_in_another_name(self, diffusion):
        """'test' must not clash with an existing 'latest'."""
        diffusion.phab.diffusion.repository.search.return_value = {
            "data": [_repo("latest")]
        }

        assert diffusion.create_repository(name="test") == "PHID-REPO-new"


class TestRepoList:
    def test_filters_by_status_and_sorts_by_name(self):
        phab = _phab_with_repos(
            [_repo("zeta"), _repo("alpha"), _repo("old", status="inactive")]
        )

        assert format_repositories(phab, status=["active"]) == [
            {"name": "alpha"},
            {"name": "zeta"},
        ]


def _uri(
    uri="https://github.com/dynamist/phabfive.git",
    io="observe",
    display="never",
    disabled=False,
    credential_phid=None,
):
    """A repository URI shaped the way diffusion.repository.search returns it."""
    return {
        "id": 10,
        "phid": "PHID-RURI-test",
        "fields": {
            "repositoryPHID": "PHID-REPO-test",
            "uri": {"raw": uri, "display": uri, "effective": uri},
            "io": {"raw": io, "default": "none", "effective": io},
            "display": {"raw": display, "default": "never", "effective": display},
            "credentialPHID": credential_phid,
            "disabled": disabled,
        },
    }


def _phab_with_uri(uri_record, repo_short_name="myrepo"):
    repo = _repo("myrepo", short_name=repo_short_name)
    repo["attachments"]["uris"]["uris"] = [uri_record]
    return _phab_with_repos([repo])


class TestResolveUriRecord:
    """Fetching a URI in full, so an edit can be shown before it is made."""

    def test_returns_the_matching_uri(self):
        from phabfive.diffusion.resolvers import resolve_uri_record

        record = _uri()
        phab = _phab_with_uri(record)

        found = resolve_uri_record(
            phab, "myrepo", "https://github.com/dynamist/phabfive.git"
        )

        assert found is record

    def test_unknown_uri_raises(self):
        from phabfive.diffusion.resolvers import resolve_uri_record

        phab = _phab_with_uri(_uri())

        with pytest.raises(PhabfiveDataException, match="Uri does not exist"):
            resolve_uri_record(phab, "myrepo", "https://elsewhere/x.git")

    def test_unknown_repo_raises(self):
        from phabfive.diffusion.resolvers import resolve_uri_record

        phab = _phab_with_uri(_uri())

        with pytest.raises(PhabfiveDataException, match="does not exist"):
            resolve_uri_record(phab, "nope", "https://github.com/dynamist/phabfive.git")


class TestBuildUriEdit:
    """`uri edit` has to say what it would change before changing it."""

    def test_display_change_is_described(self, diffusion):
        transactions, changes = diffusion.build_uri_edit(_uri(), display="always")

        assert transactions == [{"type": "display", "value": "always"}]
        assert changes == [{"field": "Display", "old": "never", "new": "always"}]

    def test_value_already_set_is_not_a_change(self, diffusion):
        transactions, changes = diffusion.build_uri_edit(_uri(), display="never")

        assert transactions == []
        assert changes == []

    def test_disable_is_described_from_its_current_state(self, diffusion):
        transactions, changes = diffusion.build_uri_edit(_uri(), disable=True)

        assert transactions == [{"type": "disable", "value": True}]
        assert changes == [{"field": "Disabled", "old": "False", "new": "True"}]

    def test_several_options_at_once(self, diffusion):
        transactions, changes = diffusion.build_uri_edit(
            _uri(), io="read", display="always"
        )

        assert len(transactions) == 2
        assert [c["field"] for c in changes] == ["I/O", "Display"]

    def test_nothing_asked_for_is_no_change(self, diffusion):
        assert diffusion.build_uri_edit(_uri()) == ([], [])

    def test_building_never_calls_the_api(self, diffusion):
        diffusion.build_uri_edit(_uri(), display="always")

        diffusion.phab.diffusion.uri.edit.assert_not_called()


class TestUriEditCredentialSecrecy:
    """A credential is named by monogram, and its secret is never read.

    These mocks previously returned a dict from get_secret and stubbed out
    _validate_credential_type, neither of which matched what those functions
    do. That is why the credential never resolving to a PHID went unnoticed.
    The record below is the shape passphrase.query actually returns.
    """

    SECRET = "-----BEGIN OPENSSH PRIVATE KEY-----\nhunter2\n"

    def _diffusion(self, diffusion):
        diffusion.passphrase.get_credential_record = MagicMock(
            return_value={
                "id": 2,
                "phid": "PHID-CDTL-newcred",
                "monogram": "K2",
                "type": "ssh-key-text",
            }
        )
        return diffusion

    def test_the_secret_is_never_fetched(self, diffusion):
        diffusion = self._diffusion(diffusion)
        diffusion.passphrase.get_secret = MagicMock(
            return_value={"material": {"privateKey": self.SECRET}}
        )

        diffusion.build_uri_edit(_uri(), credential="K2")

        diffusion.passphrase.get_secret.assert_not_called()

    def test_the_change_names_the_monogram_not_the_secret(self, diffusion):
        diffusion = self._diffusion(diffusion)
        diffusion.phab.phid.query.return_value = {"PHID-CDTL-oldcred": {"name": "K1"}}

        transactions, changes = diffusion.build_uri_edit(
            _uri(credential_phid="PHID-CDTL-oldcred"), credential="K2"
        )

        assert transactions == [{"type": "credential", "value": "PHID-CDTL-newcred"}]
        assert changes == [{"field": "Credential", "old": "K1", "new": "K2"}]

        rendered = repr(changes)
        assert self.SECRET not in rendered
        assert "hunter2" not in rendered

    def test_a_uri_with_no_credential_says_none(self, diffusion):
        diffusion = self._diffusion(diffusion)

        _, changes = diffusion.build_uri_edit(_uri(), credential="K2")

        assert changes == [{"field": "Credential", "old": "(none)", "new": "K2"}]

    def test_an_unnameable_credential_falls_back_to_its_phid(self, diffusion):
        diffusion = self._diffusion(diffusion)
        diffusion.phab.phid.query.side_effect = Exception("lookup failed")

        _, changes = diffusion.build_uri_edit(
            _uri(credential_phid="PHID-CDTL-oldcred"), credential="K2"
        )

        # Naming it is a convenience; it must never fail the edit.
        assert changes == [
            {"field": "Credential", "old": "PHID-CDTL-oldcred", "new": "K2"}
        ]


class TestEditUriComposition:
    """edit_uri keeps its old shape on top of the new build/apply split."""

    def test_dry_run_builds_but_does_not_apply(self, diffusion):
        result = diffusion.edit_uri(
            display="always", object_identifier=10, uri_record=_uri(), dry_run=True
        )

        assert result["dry_run"] is True
        assert result["changes"] == [
            {"field": "Display", "old": "never", "new": "always"}
        ]
        diffusion.phab.diffusion.uri.edit.assert_not_called()

    def test_applying_sends_the_built_transactions(self, diffusion):
        diffusion.edit_uri(display="always", object_identifier=10, uri_record=_uri())

        diffusion.phab.diffusion.uri.edit.assert_called_once_with(
            transactions=[{"type": "display", "value": "always"}],
            objectIdentifier=10,
        )

    def test_no_change_applies_nothing(self, diffusion):
        result = diffusion.edit_uri(
            display="never", object_identifier=10, uri_record=_uri()
        )

        assert result["changes"] == []
        diffusion.phab.diffusion.uri.edit.assert_not_called()

    def test_apply_wraps_api_errors(self, diffusion):
        from phabricator import APIError

        diffusion.phab.diffusion.uri.edit.side_effect = APIError("ERR", "nope")

        with pytest.raises(PhabfiveDataException):
            diffusion.apply_uri_edit(10, [{"type": "display", "value": "always"}])


class TestUriEditCli:
    """The flags `uri edit` never had: --dry-run, --yes and --interactive."""

    def _invoke(self, args, built=None):
        from typer.testing import CliRunner

        from phabfive.cli.diffusion import diffusion_app

        mock_diffusion = MagicMock()
        mock_diffusion.get_uri_and_repo.return_value = (_repo("myrepo"), _uri())
        mock_diffusion.describe_repository.return_value = "R1 (myrepo)"
        mock_diffusion.build_uri_edit.return_value = built or (
            [{"type": "display", "value": "always"}],
            [{"field": "Display", "old": "never", "new": "always"}],
        )

        with patch(
            "phabfive.cli.diffusion._get_diffusion_app", return_value=mock_diffusion
        ):
            result = CliRunner().invoke(diffusion_app, ["uri", "edit", *args])
        return result, mock_diffusion

    URI = "https://github.com/dynamist/phabfive.git"

    def test_dry_run_shows_the_change_without_making_it(self):
        result, diffusion = self._invoke(
            ["myrepo", self.URI, "--display=always", "--dry-run"]
        )

        assert result.exit_code == 0
        assert "[DRY RUN]" in result.output
        assert "Display: never → always" in result.output
        diffusion.apply_uri_edit.assert_not_called()

    def test_applying_prints_what_changed(self):
        result, diffusion = self._invoke(["myrepo", self.URI, "--display=always"])

        assert result.exit_code == 0
        assert "Display: never → always" in result.output
        diffusion.apply_uri_edit.assert_called_once()

    def test_no_change_says_so_and_applies_nothing(self):
        result, diffusion = self._invoke(
            ["myrepo", self.URI, "--display=never"], built=([], [])
        )

        assert result.exit_code == 0
        assert "No changes (already at target state)" in result.output
        diffusion.apply_uri_edit.assert_not_called()

    def test_yes_and_interactive_are_rejected(self):
        result, diffusion = self._invoke(
            ["myrepo", self.URI, "--display=always", "--yes", "--interactive"]
        )

        assert result.exit_code == 1
        diffusion.apply_uri_edit.assert_not_called()

    def test_interactive_declined_changes_nothing(self):
        with patch("phabfive.editor.confirm_apply", return_value=(False, 0)):
            result, diffusion = self._invoke(
                ["myrepo", self.URI, "--display=always", "--interactive"]
            )

        assert result.exit_code == 0
        diffusion.apply_uri_edit.assert_not_called()

    def test_interactive_accepted_applies(self):
        with patch("phabfive.editor.confirm_apply", return_value=(True, None)):
            result, diffusion = self._invoke(
                ["myrepo", self.URI, "--display=always", "--interactive"]
            )

        assert result.exit_code == 0
        diffusion.apply_uri_edit.assert_called_once()

    def test_interactive_owns_the_i_short_flag(self):
        """-i used to mean --io; it means --interactive now, like everywhere else."""
        with patch("phabfive.editor.confirm_apply", return_value=(True, None)):
            result, diffusion = self._invoke(
                ["myrepo", self.URI, "--display=always", "-i"]
            )

        assert result.exit_code == 0
        diffusion.apply_uri_edit.assert_called_once()

    def test_io_has_no_short_flag(self):
        result, diffusion = self._invoke(["myrepo", self.URI, "-i", "read"])

        assert result.exit_code != 0
        diffusion.apply_uri_edit.assert_not_called()

    def test_uri_is_set_with_its_own_option(self):
        result, diffusion = self._invoke(
            ["myrepo", self.URI, "--uri", "https://elsewhere/x.git", "--dry-run"]
        )

        assert result.exit_code == 0
        assert (
            diffusion.build_uri_edit.call_args.kwargs["uri"]
            == "https://elsewhere/x.git"
        )

    def test_the_old_new_uri_spelling_is_gone(self):
        result, diffusion = self._invoke(
            ["myrepo", self.URI, "--new-uri", "https://elsewhere/x.git"]
        )

        assert result.exit_code != 0
        diffusion.apply_uri_edit.assert_not_called()

    @pytest.mark.parametrize(
        "flag,value", [("-n", "x"), ("-d", "always"), ("-c", "K2")]
    )
    def test_removed_short_flags(self, flag, value):
        result, diffusion = self._invoke(["myrepo", self.URI, flag, value])

        assert result.exit_code != 0
        diffusion.apply_uri_edit.assert_not_called()

    def test_still_requires_at_least_one_option(self):
        result, diffusion = self._invoke(["myrepo", self.URI])

        assert result.exit_code == 1
        diffusion.get_uri_and_repo.assert_not_called()


def _paged_phab(pages):
    """A client whose repository.search answers across several pages.

    Conduit caps a page at 100 rows and hands back a cursor; a client that
    reads only the first response silently loses everything after it.
    """
    phab = MagicMock()
    responses = [
        {"data": page, "cursor": {"after": str(i + 1) if i + 1 < len(pages) else None}}
        for i, page in enumerate(pages)
    ]

    def search(**kwargs):
        after = kwargs.get("after")
        index = 0 if after is None else int(after)
        return responses[index]

    phab.diffusion.repository.search.side_effect = search
    return phab


class TestRepositoryPagination:
    """Every repository is seen, not just the first page."""

    def test_fetch_repositories_follows_the_cursor(self):
        from phabfive.diffusion.fetchers import fetch_repositories

        phab = _paged_phab([[_repo("first")], [_repo("second")]])

        names = [r["fields"]["name"] for r in fetch_repositories(phab)]

        assert names == ["first", "second"]

    def test_a_repository_on_a_later_page_still_resolves(self):
        from phabfive.diffusion.resolvers import resolve_shortname_to_id

        far = _repo("faraway")
        far["id"] = 77
        phab = _paged_phab([[_repo("first")], [far]])

        assert resolve_shortname_to_id(phab, "faraway") == 77

    def test_a_uri_on_a_later_page_still_resolves(self):
        from phabfive.diffusion.resolvers import resolve_uri_record

        far = _repo("faraway")
        far["attachments"]["uris"]["uris"] = [
            {"id": 9, "fields": {"uri": {"display": "git@example.com:far.git"}}}
        ]
        phab = _paged_phab([[_repo("first")], [far]])

        found = resolve_uri_record(phab, "faraway", "git@example.com:far.git")

        assert found["id"] == 9

    def test_a_single_page_response_without_a_cursor_still_works(self):
        """The Conduit shape used by every other test must keep working."""
        from phabfive.diffusion.fetchers import fetch_repositories

        assert len(fetch_repositories(_phab_with_repos([_repo("only")]))) == 1


class TestRepositoryIdentifiers:
    """A repository is addressable by monogram and callsign, not just shortName."""

    def _repos(self):
        nameless = _repo("nameless")
        nameless["id"] = 42
        nameless["fields"]["shortName"] = None
        nameless["fields"]["callsign"] = "TRON"
        return [_repo("plain"), nameless]

    def test_resolves_by_short_name(self):
        from phabfive.diffusion.fetchers import match_repository

        found = match_repository(self._repos(), "plain")

        assert found["fields"]["name"] == "plain"

    def test_resolves_by_monogram(self):
        from phabfive.diffusion.fetchers import match_repository

        found = match_repository(self._repos(), "R42")

        assert found["id"] == 42

    def test_resolves_by_callsign(self):
        from phabfive.diffusion.fetchers import match_repository

        found = match_repository(self._repos(), "TRON")

        assert found["id"] == 42

    def test_a_repository_without_a_short_name_is_still_reachable(self):
        """A repository created without a short name is not hypothetical."""
        from phabfive.diffusion.fetchers import match_repository

        assert match_repository(self._repos(), "R42")["fields"]["shortName"] is None

    def test_unknown_identifier_is_none(self):
        from phabfive.diffusion.fetchers import match_repository

        assert match_repository(self._repos(), "R999") is None

    def test_uri_edit_resolves_a_nameless_repository(self):
        from phabfive.diffusion.resolvers import resolve_uri_record

        nameless = _repo("nameless")
        nameless["id"] = 42
        nameless["fields"]["shortName"] = None
        nameless["attachments"]["uris"]["uris"] = [
            {"id": 7, "fields": {"uri": {"display": "git@example.com:x.git"}}}
        ]

        found = resolve_uri_record(
            _phab_with_repos([nameless]), "R42", "git@example.com:x.git"
        )

        assert found["id"] == 7

    def test_uri_list_accepts_a_monogram(self):
        """SKILL.md documents `diffusion uri list R5 --clone`."""
        from phabfive.diffusion.formatters import format_uris

        repo = _repo("anything")
        repo["id"] = 5
        repo["attachments"]["uris"]["uris"] = [
            {
                "id": 1,
                "fields": {
                    "uri": {"display": "git@example.com:x.git"},
                    "display": {"effective": "always"},
                },
            }
        ]

        assert format_uris(_phab_with_repos([repo]), "R5") == ["git@example.com:x.git"]


class TestMissingRepositoryIsNotATraceback:
    """A lookup that fails is a message, not a stack trace."""

    def _invoke(self, argv, method):
        from typer.testing import CliRunner

        from phabfive.cli.diffusion import diffusion_app

        mock_diffusion = MagicMock()
        getattr(mock_diffusion, method).side_effect = PhabfiveDataException(
            "Repository 'R42' does not exist"
        )

        with patch(
            "phabfive.cli.diffusion._get_diffusion_app", return_value=mock_diffusion
        ):
            return CliRunner().invoke(diffusion_app, argv)

    def test_uri_edit_reports_a_missing_repository_cleanly(self):
        result = self._invoke(
            ["uri", "edit", "R42", "someuri", "--io=read"], "get_uri_and_repo"
        )

        assert result.exit_code == 1
        assert "does not exist" in result.output
        assert "Traceback" not in result.output

    def test_uri_edit_reports_a_missing_uri_cleanly(self):
        result = self._invoke(
            ["uri", "edit", "myrepo", "nosuchuri", "--io=read"], "get_uri_and_repo"
        )

        assert result.exit_code == 1
        assert "Traceback" not in result.output


class TestBuildRepoEdit:
    """Repository edits describe themselves before they are applied."""

    def _record(self):
        record = _repo("oldname", short_name="oldname")
        record["fields"]["defaultBranch"] = "master"
        return record

    def test_default_branch_change(self, diffusion):
        transactions, changes = diffusion.build_repo_edit(
            self._record(), default_branch="main"
        )

        assert transactions == [{"type": "defaultBranch", "value": "main"}]
        assert changes == [{"field": "Default branch", "old": "master", "new": "main"}]

    def test_a_value_already_at_target_is_not_a_transaction(self, diffusion):
        transactions, changes = diffusion.build_repo_edit(
            self._record(), default_branch="master"
        )

        assert transactions == []
        assert changes == []

    def test_short_name_change_reports_the_built_in_uris_it_rewrites(self, diffusion):
        """Phabricator derives /source/<shortName>.git, so a rename moves it."""
        transactions, changes = diffusion.build_repo_edit(
            self._record(), short_name="newname"
        )

        assert transactions == [{"type": "shortName", "value": "newname"}]
        assert {
            "field": "Built-in URIs",
            "old": "/source/oldname.git",
            "new": "/source/newname.git",
        } in changes

    def test_name_change_alone_leaves_built_in_uris_unmentioned(self, diffusion):
        _, changes = diffusion.build_repo_edit(self._record(), name="newname")

        assert [c["field"] for c in changes] == ["Name"]

    def test_a_rename_sets_both_fields(self, diffusion):
        transactions, _ = diffusion.build_repo_edit(
            self._record(), name="newname", short_name="newname"
        )

        assert transactions == [
            {"type": "name", "value": "newname"},
            {"type": "shortName", "value": "newname"},
        ]

    def test_an_absent_short_name_is_described_as_none(self, diffusion):
        record = self._record()
        record["fields"]["shortName"] = None

        _, changes = diffusion.build_repo_edit(record, short_name="newname")

        assert changes[0] == {
            "field": "Short name",
            "old": "(none)",
            "new": "newname",
        }

    def test_dry_run_applies_nothing(self, diffusion):
        result = diffusion.edit_repository(
            default_branch="main",
            object_identifier=1,
            repo_record=self._record(),
            dry_run=True,
        )

        assert result["dry_run"] is True
        diffusion.phab.diffusion.repository.edit.assert_not_called()


class TestRepoEditCli:
    """repo edit matches the vocabulary uri edit settled on."""

    def _invoke(self, args, built=None):
        from typer.testing import CliRunner

        from phabfive.cli.diffusion import diffusion_app

        mock_diffusion = MagicMock()
        mock_diffusion.get_repo_record.return_value = _repo("oldname")
        mock_diffusion.build_repo_edit.return_value = built or (
            [{"type": "defaultBranch", "value": "main"}],
            [{"field": "Default branch", "old": "master", "new": "main"}],
        )

        with patch(
            "phabfive.cli.diffusion._get_diffusion_app", return_value=mock_diffusion
        ):
            result = CliRunner().invoke(diffusion_app, ["repo", "edit", *args])
        return result, mock_diffusion

    def test_dry_run_shows_the_change_without_making_it(self):
        result, diffusion = self._invoke(["R42", "--default-branch=main", "--dry-run"])

        assert result.exit_code == 0
        assert "[DRY RUN]" in result.output
        assert "Default branch: master → main" in result.output
        diffusion.apply_repo_edit.assert_not_called()

    def test_applies_by_default(self):
        result, diffusion = self._invoke(["R42", "--default-branch=main"])

        assert result.exit_code == 0
        diffusion.apply_repo_edit.assert_called_once()

    def test_requires_at_least_one_option(self):
        result, diffusion = self._invoke(["R42"])

        assert result.exit_code == 1
        diffusion.get_repo_record.assert_not_called()

    def test_rejects_an_unknown_status(self):
        result, diffusion = self._invoke(["R42", "--status=archived"])

        assert result.exit_code == 1
        diffusion.apply_repo_edit.assert_not_called()

    def test_yes_and_interactive_are_mutually_exclusive(self):
        result, diffusion = self._invoke(["R42", "--default-branch=main", "-y", "-i"])

        assert result.exit_code == 1
        diffusion.apply_repo_edit.assert_not_called()

    def test_no_changes_applies_nothing(self):
        result, diffusion = self._invoke(
            ["R42", "--default-branch=main"], built=([], [])
        )

        assert result.exit_code == 0
        assert "No changes" in result.output
        diffusion.apply_repo_edit.assert_not_called()

    def test_short_name_has_no_short_flag(self):
        result, diffusion = self._invoke(["R42", "-s", "newname"])

        assert result.exit_code != 0
        diffusion.apply_repo_edit.assert_not_called()

    def test_a_missing_repository_is_reported_cleanly(self):
        from typer.testing import CliRunner

        from phabfive.cli.diffusion import diffusion_app

        mock_diffusion = MagicMock()
        mock_diffusion.get_repo_record.side_effect = PhabfiveDataException(
            "Repository 'R99' does not exist"
        )

        with patch(
            "phabfive.cli.diffusion._get_diffusion_app", return_value=mock_diffusion
        ):
            result = CliRunner().invoke(
                diffusion_app, ["repo", "edit", "R99", "--default-branch=main"]
            )

        assert result.exit_code == 1
        assert "Traceback" not in result.output


class TestBuildRepoCreate:
    """A new repository describes itself before it exists."""

    def test_transactions_cover_every_field(self, diffusion):
        diffusion.phab.diffusion.repository.search.return_value = {"data": []}

        transactions, _ = diffusion.build_repo_create(name="newrepo")

        assert transactions == [
            {"type": "name", "value": "newrepo"},
            {"type": "shortName", "value": "newrepo"},
            {"type": "vcs", "value": "git"},
            {"type": "status", "value": "active"},
        ]

    def test_changes_describe_the_built_in_uris_it_mints(self, diffusion):
        diffusion.phab.diffusion.repository.search.return_value = {"data": []}

        _, changes = diffusion.build_repo_create(name="newrepo")

        assert {
            "field": "Built-in URIs",
            "old": "(none)",
            "new": "/source/newrepo.git",
        } in changes

    def test_the_duplicate_check_runs_at_build_time(self, diffusion):
        """So a dry run reports the clash instead of passing and failing later."""
        diffusion.phab.diffusion.repository.search.return_value = {
            "data": [_repo("newrepo")]
        }

        with pytest.raises(PhabfiveDataException, match="already exists"):
            diffusion.build_repo_create(name="newrepo")

        diffusion.phab.diffusion.repository.edit.assert_not_called()

    def test_create_repository_still_returns_the_phid(self, diffusion):
        """The pre-existing entry point keeps working over the new seam."""
        diffusion.phab.diffusion.repository.search.return_value = {"data": []}

        assert diffusion.create_repository(name="newrepo") == "PHID-REPO-new"


class TestRepoCreateCli:
    """repo create matches the vocabulary the edit commands settled on."""

    def _invoke(self, args, build=None):
        from typer.testing import CliRunner

        from phabfive.cli.diffusion import diffusion_app

        mock_diffusion = MagicMock()
        mock_diffusion.build_repo_create.return_value = build or (
            [{"type": "name", "value": "newrepo"}],
            [{"field": "Name", "old": "(none)", "new": "newrepo"}],
        )

        with patch(
            "phabfive.cli.diffusion._get_diffusion_app", return_value=mock_diffusion
        ):
            result = CliRunner().invoke(diffusion_app, ["repo", "create", *args])
        return result, mock_diffusion

    def test_dry_run_creates_nothing(self):
        result, diffusion = self._invoke(["newrepo", "--dry-run"])

        assert result.exit_code == 0
        assert "[DRY RUN]" in result.output
        assert "Name: (none) → newrepo" in result.output
        diffusion.apply_repo_create.assert_not_called()

    def test_creates_by_default(self):
        result, diffusion = self._invoke(["newrepo"])

        assert result.exit_code == 0
        diffusion.apply_repo_create.assert_called_once()

    def test_an_existing_name_is_reported_cleanly(self):
        from typer.testing import CliRunner

        from phabfive.cli.diffusion import diffusion_app

        mock_diffusion = MagicMock()
        mock_diffusion.build_repo_create.side_effect = PhabfiveDataException(
            "Repository newrepo already exists"
        )

        with patch(
            "phabfive.cli.diffusion._get_diffusion_app", return_value=mock_diffusion
        ):
            result = CliRunner().invoke(diffusion_app, ["repo", "create", "newrepo"])

        assert result.exit_code == 1
        assert "already exists" in result.output
        assert "Traceback" not in result.output

    def test_a_duplicate_is_caught_before_the_dry_run_prints(self):
        """--dry-run must fail on a clash, not report a creation that cannot happen."""
        from typer.testing import CliRunner

        from phabfive.cli.diffusion import diffusion_app

        mock_diffusion = MagicMock()
        mock_diffusion.build_repo_create.side_effect = PhabfiveDataException(
            "Repository newrepo already exists"
        )

        with patch(
            "phabfive.cli.diffusion._get_diffusion_app", return_value=mock_diffusion
        ):
            result = CliRunner().invoke(
                diffusion_app, ["repo", "create", "newrepo", "--dry-run"]
            )

        assert result.exit_code == 1
        assert "[DRY RUN]" not in result.output

    def test_yes_and_interactive_are_mutually_exclusive(self):
        result, diffusion = self._invoke(["newrepo", "-y", "-i"])

        assert result.exit_code == 1
        diffusion.apply_repo_create.assert_not_called()


def _uri_record(uri, io="observe", display="always", uri_id=1, builtin=False):
    return {
        "id": uri_id,
        "fields": {
            "uri": {"display": uri},
            "io": {"effective": io},
            "display": {"effective": display},
            "builtin": {"protocol": "ssh" if builtin else None},
        },
    }


class TestBuildUriCreate:
    """Creating a URI demotes the others; the preview has to say so."""

    def _diffusion_with(self, uris, diffusion):
        repo = _repo("myrepo")
        repo["attachments"]["uris"]["uris"] = uris
        diffusion.phab.diffusion.repository.search.return_value = {"data": [repo]}
        diffusion.passphrase.get_credential_record.return_value = {
            "phid": "PHID-CDTL-1",
            "type": "token",
            "monogram": "K1",
        }
        return diffusion

    def test_the_new_uri_is_described(self, diffusion):
        self._diffusion_with([], diffusion)

        _, changes = diffusion.build_uri_create(
            repository_name="myrepo",
            new_uri="git@example.com:group/project.git",
            io="observe",
            credential="K1",
        )

        assert {
            "field": "New URI",
            "old": "(none)",
            "new": "git@example.com:group/project.git",
        } in changes

    def test_every_existing_uri_is_named_as_a_demotion(self, diffusion):
        """This is the effect that is invisible in the arguments."""
        self._diffusion_with(
            [
                _uri_record("git@example.com:group/old.git", uri_id=1),
                _uri_record(
                    "ssh://host/source/myrepo.git", io="read", uri_id=2, builtin=True
                ),
            ],
            diffusion,
        )

        _, changes = diffusion.build_uri_create(
            repository_name="myrepo",
            new_uri="git@example.com:group/new.git",
            io="observe",
            credential="K1",
        )

        demotions = [c for c in changes if c["field"].startswith("Demotes")]
        assert len(demotions) == 2
        assert demotions[0] == {
            "field": "Demotes git@example.com:group/old.git",
            "old": "io=observe, display=always",
            # An observed URI cannot take io=read; Phorge rejects it.
            "new": "io=none, display=never",
        }
        assert demotions[1] == {
            "field": "Demotes ssh://host/source/myrepo.git",
            "old": "io=read, display=always",
            "new": "io=read, display=never",
        }

    def test_a_uri_already_demoted_is_not_listed(self, diffusion):
        self._diffusion_with(
            [_uri_record("git@example.com:group/old.git", io="none", display="never")],
            diffusion,
        )

        _, changes = diffusion.build_uri_create(
            repository_name="myrepo",
            new_uri="git@example.com:group/new.git",
            io="observe",
            credential="K1",
        )

        assert not [c for c in changes if c["field"].startswith("Demotes")]

    def test_the_credential_is_named_by_monogram(self, diffusion):
        self._diffusion_with([], diffusion)

        _, changes = diffusion.build_uri_create(
            repository_name="myrepo",
            new_uri="git@example.com:group/project.git",
            io="observe",
            credential="K1",
        )

        assert {"field": "Credential", "old": "(none)", "new": "K1"} in changes

    def test_the_secret_is_never_fetched(self, diffusion):
        """Attaching a credential needs its PHID, not its material."""
        self._diffusion_with([], diffusion)

        diffusion.build_uri_create(
            repository_name="myrepo",
            new_uri="git@example.com:group/project.git",
            io="observe",
            credential="K1",
        )

        diffusion.passphrase.get_secret.assert_not_called()

    def test_building_creates_nothing(self, diffusion):
        self._diffusion_with([_uri_record("git@example.com:group/old.git")], diffusion)

        diffusion.build_uri_create(
            repository_name="myrepo",
            new_uri="git@example.com:group/new.git",
            io="observe",
            credential="K1",
        )

        diffusion.phab.diffusion.uri.edit.assert_not_called()

    def test_a_nameless_repository_can_receive_a_uri(self, diffusion):
        """It resolves through match_repository like every other lookup."""
        repo = _repo("nameless")
        repo["id"] = 42
        repo["fields"]["shortName"] = None
        repo["attachments"]["uris"]["uris"] = []
        diffusion.phab.diffusion.repository.search.return_value = {"data": [repo]}
        diffusion.passphrase.get_credential_record.return_value = {
            "phid": "PHID-CDTL-1",
            "type": "token",
            "monogram": "K1",
        }

        plan, _ = diffusion.build_uri_create(
            repository_name="R42",
            new_uri="git@example.com:group/project.git",
            io="observe",
            credential="K1",
        )

        assert plan["repository_phid"] == repo["phid"]

    def test_an_unknown_repository_raises(self, diffusion):
        diffusion.phab.diffusion.repository.search.return_value = {"data": []}

        with pytest.raises(PhabfiveDataException, match="does not exist"):
            diffusion.build_uri_create(
                repository_name="nope", new_uri="x", io="observe", credential="K1"
            )


class TestUriCreateCli:
    """uri create matches the vocabulary the edit commands settled on."""

    def _invoke(self, args):
        from typer.testing import CliRunner

        from phabfive.cli.diffusion import diffusion_app

        mock_diffusion = MagicMock()
        mock_diffusion.build_uri_create.return_value = (
            {"repository_phid": "PHID-REPO-1", "demotions": [], "transactions": []},
            [
                {"field": "New URI", "old": "(none)", "new": "git@example.com:g/p.git"},
                {
                    "field": "Demotes git@example.com:g/old.git",
                    "old": "io=observe, display=always",
                    "new": "io=read, display=never",
                },
            ],
        )

        with patch(
            "phabfive.cli.diffusion._get_diffusion_app", return_value=mock_diffusion
        ):
            result = CliRunner().invoke(diffusion_app, ["uri", "create", *args])
        return result, mock_diffusion

    def test_dry_run_creates_nothing(self):
        result, diffusion = self._invoke(
            ["K1", "myrepo", "git@example.com:g/p.git", "--observe", "--dry-run"]
        )

        assert result.exit_code == 0
        assert "[DRY RUN]" in result.output
        diffusion.apply_uri_create.assert_not_called()

    def test_dry_run_shows_the_demotion(self):
        """The whole reason this command needed a preview."""
        result, _ = self._invoke(
            ["K1", "myrepo", "git@example.com:g/p.git", "--observe", "--dry-run"]
        )

        assert "Demotes git@example.com:g/old.git" in result.output
        assert "io=read, display=never" in result.output

    def test_creates_by_default(self):
        result, diffusion = self._invoke(
            ["K1", "myrepo", "git@example.com:g/p.git", "--observe"]
        )

        assert result.exit_code == 0
        diffusion.apply_uri_create.assert_called_once()

    def test_still_requires_observe_or_mirror(self):
        result, diffusion = self._invoke(["K1", "myrepo", "git@example.com:g/p.git"])

        assert result.exit_code == 1
        diffusion.apply_uri_create.assert_not_called()

    def test_yes_and_interactive_are_mutually_exclusive(self):
        result, diffusion = self._invoke(
            ["K1", "myrepo", "git@example.com:g/p.git", "--observe", "-y", "-i"]
        )

        assert result.exit_code == 1
        diffusion.apply_uri_create.assert_not_called()


class TestDemotionIo:
    """Phabricator validates io per URI kind, so one value does not fit both.

    `uri create` sent io=read for every URI. Phorge rejects that on an
    observed URI - "Available types for this URI are: default, none,
    observe, mirror" - so the command failed on any repository that
    observes a remote, which is the common case.
    """

    def test_a_built_in_uri_demotes_to_read(self):
        from phabfive.diffusion.fetchers import demotion_io

        assert demotion_io({"builtin": {"protocol": "ssh"}}) == "read"

    def test_an_observed_uri_demotes_to_none(self):
        from phabfive.diffusion.fetchers import demotion_io

        assert demotion_io({"builtin": {"protocol": None}}) == "none"

    def test_a_missing_builtin_key_is_treated_as_observed(self):
        from phabfive.diffusion.fetchers import demotion_io

        assert demotion_io({}) == "none"

    def test_apply_uses_the_per_uri_target(self, diffusion):
        plan = {
            "repository_phid": "PHID-REPO-1",
            "transactions": [],
            "demotions": [
                {
                    "id": 1,
                    "uri": "a",
                    "io": "observe",
                    "display": "always",
                    "target_io": "none",
                },
                {
                    "id": 2,
                    "uri": "b",
                    "io": "read",
                    "display": "always",
                    "target_io": "read",
                },
            ],
        }
        diffusion.edit_uri = MagicMock()

        diffusion.apply_uri_create(plan)

        assert [c.kwargs["io"] for c in diffusion.edit_uri.call_args_list] == [
            "none",
            "read",
        ]


class TestApiErrorsAreNotSwallowed:
    """The generic message hid a validation error that named the fix."""

    def test_uri_edit_surfaces_what_the_api_said(self, diffusion):
        from phabricator import APIError

        diffusion.phab.diffusion.uri.edit.side_effect = APIError(
            "ERR-CONDUIT-CORE", 'Value "read" is not a valid IO setting'
        )

        with pytest.raises(PhabfiveDataException, match="not a valid IO setting"):
            diffusion.apply_uri_edit(1, [{"type": "io", "value": "read"}])

    def test_repo_edit_surfaces_what_the_api_said(self, diffusion):
        from phabricator import APIError

        diffusion.phab.diffusion.repository.edit.side_effect = APIError(
            "ERR-CONDUIT-CORE", "Some specific validation detail"
        )

        with pytest.raises(PhabfiveDataException, match="specific validation detail"):
            diffusion.apply_repo_edit(1, [{"type": "name", "value": "x"}])


class TestUriEditNamesTheRepository:
    """A URI string does not identify a repository; two can carry the same one.

    Scanning a bulk dry run, "Would apply to git@host:group/project.git"
    does not say which repository is about to change. On a real instance two
    repositories claimed one remote, one live and one retired, and the
    output for disabling either was identical.
    """

    def test_describe_repository_prefers_the_short_name(self, diffusion):
        assert (
            diffusion.describe_repository(_repo("thing", short_name="thing"))
            == "R1 (thing)"
        )

    def test_describe_repository_falls_back_to_the_callsign(self, diffusion):
        repo = _repo("thing")
        repo["fields"]["shortName"] = None
        repo["fields"]["callsign"] = "THING"

        assert diffusion.describe_repository(repo) == "R1 (THING)"

    def test_describe_repository_falls_back_to_the_name(self, diffusion):
        repo = _repo("thing")
        repo["fields"]["shortName"] = None

        assert diffusion.describe_repository(repo) == "R1 (thing)"

    def test_describe_repository_copes_with_no_name_at_all(self, diffusion):
        repo = _repo("thing")
        repo["fields"] = {"shortName": None, "callsign": None, "name": None}

        assert diffusion.describe_repository(repo) == "R1"

    def test_resolve_returns_the_repository_alongside_the_uri(self):
        from phabfive.diffusion.resolvers import resolve_uri_and_repo

        repo = _repo("myrepo")
        repo["attachments"]["uris"]["uris"] = [
            {"id": 3, "fields": {"uri": {"display": "git@example.com:g/p.git"}}}
        ]

        found_repo, found_uri = resolve_uri_and_repo(
            _phab_with_repos([repo]), "myrepo", "git@example.com:g/p.git"
        )

        assert found_repo["id"] == 1
        assert found_uri["id"] == 3

    def test_two_repositories_sharing_a_remote_are_told_apart(self):
        """The case that prompted this: same URI, different repository."""
        from typer.testing import CliRunner

        from phabfive.cli.diffusion import diffusion_app

        URI = "git@example.com:group/shared.git"
        seen = []

        for rid, name in ((86, "live-one"), (89, "retired-one")):
            repo = _repo(name, short_name=name)
            repo["id"] = rid
            mock_diffusion = MagicMock()
            mock_diffusion.get_uri_and_repo.return_value = (repo, _uri())
            mock_diffusion.link_repository.return_value = (
                f"https://phabricator.example.com/R{rid} ({name})"
            )
            mock_diffusion.build_uri_edit.return_value = (
                [{"type": "disable", "value": True}],
                [{"field": "Disabled", "old": "False", "new": "True"}],
            )

            with patch(
                "phabfive.cli.diffusion._get_diffusion_app", return_value=mock_diffusion
            ):
                result = CliRunner().invoke(
                    diffusion_app,
                    ["uri", "edit", str(rid), URI, "--disable", "--dry-run"],
                )
            seen.append(result.output)

        assert "R86 (live-one)" in seen[0]
        assert "R89 (retired-one)" in seen[1]
        assert seen[0] != seen[1]


class TestBranchListIsRetired:
    """`diffusion branch list` is now `repo show --show-branches`.

    The sub-app is removed rather than shimmed, the way `--new-uri` and the
    `-n/-i/-d/-c` short flags were, so a script still spelling it out is
    answered by the parser instead of quietly doing something else.
    """

    def _invoke(self, argv):
        from typer.testing import CliRunner

        from phabfive.cli.diffusion import diffusion_app

        mock_diffusion = MagicMock()

        with patch(
            "phabfive.cli.diffusion._get_diffusion_app", return_value=mock_diffusion
        ):
            result = CliRunner().invoke(diffusion_app, argv)

        return result, mock_diffusion

    def test_the_command_no_longer_exists(self):
        result, diffusion = self._invoke(["branch", "list", "R42"])

        assert result.exit_code != 0
        diffusion.get_branches_formatted.assert_not_called()

    def test_the_sub_app_no_longer_exists(self):
        result, _ = self._invoke(["branch"])

        assert result.exit_code != 0

    def test_diffusion_no_longer_offers_a_branch_command(self):
        result, _ = self._invoke(["--help"])

        assert "branch" not in result.output

    def test_the_monogram_now_shows_the_repository(self):
        """R5 was the branch listing; it is the repository now."""
        from phabfive.cli import preprocess_monograms

        assert preprocess_monograms(["phabfive", "R5"])[1:] == [
            "diffusion",
            "repo",
            "show",
            "R5",
        ]


class TestUriIoAndDisplayValues:
    """The constants describe Phorge's values, and nothing else reaches it.

    They used to describe neither: `hidden` is not a display value, `never`
    is not an I/O one, and `uri edit --io` was checked nowhere at all - so
    `demotion_io` returned values its own constant forbade while the CLI
    advertised a `write` that does not exist.
    """

    def test_display_values_are_the_ones_phorge_returns(self):
        from phabfive.constants import DISPLAY_CHOICES

        assert DISPLAY_CHOICES == ["default", "always", "never"]

    def test_io_values_cover_every_setting_phorge_has(self):
        from phabfive.constants import IO_URI_VALUES

        assert IO_URI_VALUES == [
            "default",
            "observe",
            "mirror",
            "read",
            "readwrite",
            "none",
        ]

    def test_create_offers_a_narrower_set_on_purpose(self):
        from phabfive.constants import IO_NEW_URI_CHOICES, IO_URI_VALUES

        assert IO_NEW_URI_CHOICES == ["default", "observe", "mirror", "none"]
        assert set(IO_NEW_URI_CHOICES) < set(IO_URI_VALUES)

    def test_every_demotion_target_is_a_valid_io_value(self):
        """demotion_io used to return two values its own constant refused."""
        from phabfive.constants import IO_URI_VALUES
        from phabfive.diffusion.fetchers import demotion_io

        targets = {
            demotion_io({"builtin": {"protocol": "ssh"}}),
            demotion_io({"builtin": {"protocol": None}}),
        }

        assert targets == {"read", "none"}
        assert targets <= set(IO_URI_VALUES)

    def test_an_invalid_io_is_refused(self, diffusion):
        from phabfive.exceptions import PhabfiveConfigException

        with pytest.raises(PhabfiveConfigException, match="'write' is not valid"):
            diffusion.build_uri_edit(_uri(), io="write")

    def test_an_invalid_display_is_refused(self, diffusion):
        from phabfive.exceptions import PhabfiveConfigException

        with pytest.raises(PhabfiveConfigException, match="'nope' is not valid"):
            diffusion.build_uri_edit(_uri(), display="nope")

    def test_refusing_sends_nothing(self, diffusion):
        from phabfive.exceptions import PhabfiveConfigException

        with pytest.raises(PhabfiveConfigException):
            diffusion.build_uri_edit(_uri(), io="write")

        diffusion.phab.diffusion.uri.edit.assert_not_called()

    def test_the_error_names_the_values_that_would_work(self, diffusion):
        from phabfive.exceptions import PhabfiveConfigException

        with pytest.raises(PhabfiveConfigException) as excinfo:
            diffusion.build_uri_edit(_uri(), io="write")

        assert "'readwrite'" in str(excinfo.value)

    def test_readwrite_is_accepted(self, diffusion):
        transactions, _ = diffusion.build_uri_edit(_uri(), io="readwrite")

        assert transactions == [{"type": "io", "value": "readwrite"}]


class TestDeprecatedUriValueSpellings:
    """The spellings the CLI advertised keep working, silently.

    `--display hidden` and `--io never` are what phabfive told people to
    write, so scripts write them. They resolve to what Phorge calls the
    same thing before anything compares or sends the value.
    """

    def test_hidden_still_means_never(self, diffusion):
        transactions, changes = diffusion.build_uri_edit(
            _uri(display="always"), display="hidden"
        )

        assert transactions == [{"type": "display", "value": "never"}]
        assert changes == [{"field": "Display", "old": "always", "new": "never"}]

    def test_never_still_means_none_for_io(self, diffusion):
        transactions, changes = diffusion.build_uri_edit(_uri(io="observe"), io="never")

        assert transactions == [{"type": "io", "value": "none"}]
        assert changes == [{"field": "I/O", "old": "observe", "new": "none"}]

    def test_a_deprecated_spelling_of_the_current_value_is_no_change(self, diffusion):
        """Resolved before the comparison, so it is not an edit to nothing."""
        assert diffusion.build_uri_edit(_uri(display="never"), display="hidden") == (
            [],
            [],
        )
        assert diffusion.build_uri_edit(_uri(io="none"), io="never") == ([], [])

    def test_create_still_takes_never(self, diffusion):
        repo = _repo("myrepo")
        diffusion.phab.diffusion.repository.search.return_value = {"data": [repo]}
        diffusion.passphrase.get_credential_record.return_value = {
            "phid": "PHID-CDTL-1",
            "type": "token",
            "monogram": "K1",
        }

        plan, _ = diffusion.build_uri_create(
            repository_name="myrepo",
            new_uri="git@example.com:group/project.git",
            io="never",
            display="hidden",
            credential="K1",
        )

        assert {"type": "io", "value": "none"} in plan["transactions"]
        assert {"type": "display", "value": "never"} in plan["transactions"]


class TestUriEditIoCli:
    """An unusable --io is answered, not sent and not a traceback."""

    URI = "https://github.com/dynamist/phabfive.git"

    def _invoke(self, args):
        from typer.testing import CliRunner

        from phabfive.cli.diffusion import diffusion_app
        from phabfive.diffusion import Diffusion

        mock_diffusion = MagicMock()
        mock_diffusion.get_uri_and_repo.return_value = (_repo("myrepo"), _uri())
        mock_diffusion.describe_repository.return_value = "R1 (myrepo)"
        # The real builder, so the CLI is tested against the real validation.
        mock_diffusion.build_uri_edit = MagicMock(
            side_effect=lambda *a, **kw: Diffusion.build_uri_edit(
                mock_diffusion, *a, **kw
            )
        )

        with patch(
            "phabfive.cli.diffusion._get_diffusion_app", return_value=mock_diffusion
        ):
            result = CliRunner().invoke(diffusion_app, ["uri", "edit", *args])
        return result, mock_diffusion

    def test_an_invalid_io_is_reported_not_sent(self):
        result, diffusion = self._invoke(["myrepo", self.URI, "--io=write"])

        assert result.exit_code == 1
        assert "ERROR:" in result.output
        assert "Traceback" not in result.output
        diffusion.apply_uri_edit.assert_not_called()

    def test_a_deprecated_io_spelling_still_applies(self):
        result, diffusion = self._invoke(["myrepo", self.URI, "--io=never"])

        assert result.exit_code == 0
        diffusion.apply_uri_edit.assert_called_once_with(
            10, [{"type": "io", "value": "none"}]
        )

    def test_a_deprecated_display_spelling_still_applies(self):
        result, diffusion = self._invoke(["myrepo", self.URI, "--display=hidden"])

        # _uri() is already display=never, which is what hidden means.
        assert result.exit_code == 0
        assert "No changes" in result.output
        diffusion.apply_uri_edit.assert_not_called()

    def test_the_help_lists_the_values_phorge_takes(self):
        from typer.testing import CliRunner

        from phabfive.cli.diffusion import diffusion_app

        # Wide enough that rich prints the help rather than eliding it.
        result = CliRunner().invoke(
            diffusion_app, ["uri", "edit", "--help"], env={"COLUMNS": "200"}
        )
        output = " ".join(result.output.split())

        assert "default, observe, mirror, read, readwrite, none" in output
        assert "default, always, never" in output
        # The list it used to advertise, of which two thirds were not values.
        assert "default, read, write, never" not in output
        assert "hidden" not in output


def _show_repo(
    repo_id=5,
    name="phabfive",
    short_name="phabfive",
    callsign=None,
    status="active",
    description="A CLI for Phorge",
    policy=None,
    space_phid=None,
    uris=None,
    browse_uri=None,
    is_importing=False,
):
    """A repository as diffusion.repository.search answers with it."""
    fields = {
        "name": name,
        "shortName": short_name,
        "callsign": callsign,
        "status": status,
        "vcs": "git",
        "defaultBranch": "master",
        "isImporting": is_importing,
        "description": {"raw": description},
        "policy": policy
        if policy is not None
        else {"view": "users", "edit": "admin", "diffusion.push": "users"},
        "spacePHID": space_phid,
        "dateCreated": 1700000000,
        "dateModified": 1700000001,
    }

    if browse_uri:
        fields["browseUri"] = browse_uri

    return {
        "id": repo_id,
        "phid": f"PHID-REPO-{repo_id}",
        "fields": fields,
        "attachments": {"uris": {"uris": uris if uris is not None else []}},
    }


def _show_uri(uri, io="observe", display="always", disabled=False):
    return {
        "id": 1,
        "fields": {
            "uri": {"display": uri},
            "io": {"effective": io},
            "display": {"effective": display},
            "disabled": disabled,
        },
    }


def _showable(repos, branches=None, tags=None):
    """A Diffusion wired to a mock API, ready for repo_show()."""
    with (
        patch("phabfive.diffusion.core.Phabfive.__init__", return_value=None),
        patch("phabfive.diffusion.core.passphrase.Passphrase"),
    ):
        diffusion = Diffusion()

    diffusion.phab = MagicMock()
    diffusion.url = "http://phorge.localhost"
    diffusion.phab.diffusion.repository.search.return_value = {"data": repos}
    diffusion.phab.diffusion.branchquery.return_value = branches or []
    diffusion.phab.diffusion.tagsquery.return_value = tags or []
    diffusion.phab.phid.query.return_value = {}

    return diffusion


class TestRepoShowRecord:
    """`repo show` had no command at all; this is the record it answers with.

    Nested and capitalized the way maniphest's is, with Link first, because
    that is what `phabfive edit` reads back off stdin.
    """

    def test_link_comes_first(self):
        result = _showable([_show_repo()]).repo_show(["R5"])
        record = result["repositories"][0]

        assert list(record)[0] == "_url"
        assert record["_url"] == "http://phorge.localhost/R5"

    def test_the_instance_own_link_wins_when_it_reports_one(self):
        repo = _show_repo(browse_uri="http://phorge.localhost/source/phabfive/")
        record = _showable([repo]).repo_show(["R5"])["repositories"][0]

        assert record["_url"] == "http://phorge.localhost/source/phabfive/"

    def test_the_repository_section_names_every_way_in(self):
        repo = _show_repo(callsign="PHAB")
        record = _showable([repo]).repo_show(["R5"])["repositories"][0]

        assert record["Repository"] == {
            "Name": "phabfive",
            "Short Name": "phabfive",
            "Callsign": "PHAB",
            "Monogram": "R5",
            "Status": "active",
            "VCS": "git",
            "Default Branch": "master",
            "Description": "A CLI for Phorge",
            "Hosted": False,
            "Importing": False,
        }

    def test_a_monogram_a_callsign_and_a_short_name_all_resolve(self):
        repo = _show_repo(callsign="PHAB")
        diffusion = _showable([repo])

        for identifier in ("R5", "PHAB", "phabfive"):
            result = diffusion.repo_show([identifier])

            assert result["missing_ids"] == []
            assert result["repositories"][0]["Repository"]["Monogram"] == "R5"

    def test_a_repository_that_does_not_exist_is_reported(self):
        result = _showable([_show_repo()]).repo_show(["R5", "nope"])

        assert result["missing_ids"] == ["nope"]
        assert len(result["repositories"]) == 1

    def test_policy_keywords_are_labelled_the_way_the_web_ui_labels_them(self):
        repo = _show_repo(
            policy={"view": "public", "edit": "admin", "diffusion.push": "no-one"}
        )
        record = _showable([repo]).repo_show(["R5"])["repositories"][0]

        assert record["Policy"] == {
            "View": "Public (No Login Required)",
            "Edit": "Administrators",
            "Push": "No One",
        }

    def test_a_policy_phid_passes_through_unresolved(self):
        """Resolving it is its own piece of work, and inventing a name is worse."""
        repo = _show_repo(
            policy={
                "view": "PHID-PROJ-secret",
                "edit": "admin",
                "diffusion.push": "users",
            }
        )
        record = _showable([repo]).repo_show(["R5"])["repositories"][0]

        assert record["Policy"]["View"] == "PHID-PROJ-secret"

    def test_hosting_is_what_the_instance_says_it_is(self):
        """Phorge reports isHosted, and that is the answer."""
        repo = _show_repo(uris=[_show_uri("git@github.com:o/x.git", io="observe")])
        repo["fields"]["isHosted"] = True

        record = _showable([repo]).repo_show(["R5"])["repositories"][0]

        assert record["Repository"]["Hosted"] is True

    def test_a_hosted_repository_with_no_uris_is_still_hosted(self):
        """A freshly seeded repository answers with an empty URIs attachment.

        Which is why the field is read before the URIs are: deriving
        hosting from an empty list would call a hosted repository
        unhosted, and the seeded instance is exactly that case.
        """
        repo = _show_repo(uris=[])
        repo["fields"]["isHosted"] = True

        record = _showable([repo]).repo_show(["R5"])["repositories"][0]

        assert record["Repository"]["Hosted"] is True

    def test_hosting_falls_back_to_the_uris_when_unreported(self):
        """For an instance old enough not to report the field at all."""
        hosted = _show_repo(
            uris=[_show_uri("http://phorge/source/x.git", io="readwrite")]
        )
        observed = _show_repo(uris=[_show_uri("git@github.com:o/x.git", io="observe")])

        assert "isHosted" not in hosted["fields"]
        assert _showable([hosted]).repo_show(["R5"])["repositories"][0]["Repository"][
            "Hosted"
        ]
        assert not _showable([observed]).repo_show(["R5"])["repositories"][0][
            "Repository"
        ]["Hosted"]

    def test_the_optional_sections_are_absent_until_asked_for(self):
        record = _showable([_show_repo()]).repo_show(["R5"])["repositories"][0]

        for section in ("URIs", "Branches", "Tags", "Metadata"):
            assert section not in record

    def test_show_uris_describes_each_uri(self):
        repo = _show_repo(uris=[_show_uri("git@github.com:dynamist/phabfive.git")])
        record = _showable([repo]).repo_show(["R5"], show_uris=True)["repositories"][0]

        assert record["URIs"] == [
            {
                "URI": "git@github.com:dynamist/phabfive.git",
                "I/O": "observe",
                "Display": "always",
                "Disabled": False,
            }
        ]

    def test_show_branches_asks_branchquery(self):
        diffusion = _showable(
            [_show_repo()],
            branches=[
                {"shortName": "topic", "refType": "branch"},
                {"shortName": "master", "refType": "branch"},
            ],
        )

        record = diffusion.repo_show(["R5"], show_branches=True)["repositories"][0]

        assert record["Branches"] == ["master", "topic"]
        diffusion.phab.diffusion.branchquery.assert_called_once_with(repository=5)

    def test_show_tags_asks_tagsquery(self):
        """tagsquery spells the name differently, and says no refType at all."""
        diffusion = _showable([_show_repo()], tags=[{"name": "v1.0"}, {"name": "v0.9"}])

        record = diffusion.repo_show(["R5"], show_tags=True)["repositories"][0]

        assert record["Tags"] == ["v0.9", "v1.0"]
        diffusion.phab.diffusion.tagsquery.assert_called_once_with(repository=5)

    def test_a_branch_ref_that_is_not_a_branch_is_left_out(self):
        diffusion = _showable(
            [_show_repo()],
            branches=[
                {"shortName": "master", "refType": "branch"},
                {"shortName": "v1.0", "refType": "tag"},
            ],
        )

        record = diffusion.repo_show(["R5"], show_branches=True)["repositories"][0]

        assert record["Branches"] == ["master"]

    def test_show_metadata_carries_the_phids_and_the_timestamps(self):
        record = _showable([_show_repo()]).repo_show(["R5"], show_metadata=True)[
            "repositories"
        ][0]

        assert record["Metadata"]["PHID"] == "PHID-REPO-5"
        assert record["Metadata"]["ID"] == 5
        assert record["Metadata"]["Created"].startswith("20")

    def test_no_description_leaves_the_description_out(self):
        record = _showable([_show_repo()]).repo_show(["R5"], show_description=False)[
            "repositories"
        ][0]

        assert "Description" not in record["Repository"]

    def test_a_space_is_named_when_the_repository_is_in_one(self):
        diffusion = _showable([_show_repo(space_phid="PHID-SPCE-1")])
        diffusion.phab.phid.query.return_value = {
            "PHID-SPCE-1": {"fullName": "S2 Restricted"}
        }

        record = diffusion.repo_show(["R5"])["repositories"][0]

        assert record["Space"] == "S2 Restricted"

    def test_an_unnameable_space_never_fails_the_read(self):
        diffusion = _showable([_show_repo(space_phid="PHID-SPCE-1")])
        diffusion.phab.phid.query.side_effect = Exception("boom")

        record = diffusion.repo_show(["R5"])["repositories"][0]

        assert record["Space"] == "PHID-SPCE-1"

    def test_an_unreachable_repository_raises_rather_than_tracebacks(self):
        from phabricator import APIError

        diffusion = _showable([_show_repo()])
        diffusion.phab.diffusion.branchquery.side_effect = APIError(
            "ERR", "data is unavailable"
        )

        with pytest.raises(PhabfiveDataException):
            diffusion.repo_show(["R5"], show_branches=True)


class TestRepoShowFormats:
    """The formats have to agree - that is the whole point of the command."""

    def _result(self, show_metadata=True):
        repo = _show_repo(uris=[_show_uri("git@github.com:dynamist/phabfive.git")])
        diffusion = _showable(
            [repo],
            branches=[{"shortName": "master", "refType": "branch"}],
            tags=[{"name": "v1.0"}],
        )

        return diffusion, diffusion.repo_show(
            ["R5"],
            show_branches=True,
            show_tags=True,
            show_uris=True,
            show_metadata=show_metadata,
        )

    def _render(self, capsys, output_format, show_metadata=True):
        from phabfive.diffusion.display import display_repositories

        diffusion, result = self._result(show_metadata=show_metadata)
        display_repositories(result, output_format, diffusion)

        return capsys.readouterr().out

    def test_yaml_json_and_jsonl_carry_the_same_record(self, capsys):
        import json

        from ruamel.yaml import YAML

        as_yaml = YAML(typ="safe").load(self._render(capsys, "yaml"))
        as_json = json.loads(self._render(capsys, "json"))
        as_jsonl = [
            json.loads(line) for line in self._render(capsys, "jsonl").splitlines()
        ]

        assert as_yaml == as_json == as_jsonl

    def test_rich_is_the_same_record_in_yaml_shape(self, capsys):
        """Rich contributes hyperlinks and colour, not a different layout.

        Parsed rather than compared as text, and without --show-metadata:
        rich writes a timestamp bare the way maniphest's rich does, and a
        YAML parser reads that as a timestamp rather than as the string
        json and yaml agree on. Which is the reason to parse one of those
        two and not this one.
        """
        import json

        from ruamel.yaml import YAML

        as_rich = YAML(typ="safe").load(
            self._render(capsys, "rich", show_metadata=False)
        )

        assert as_rich == json.loads(self._render(capsys, "json", show_metadata=False))

    def test_json_and_jsonl_lead_with_the_link(self, capsys):
        import json

        record = json.loads(self._render(capsys, "json"))[0]

        assert list(record)[0] == "Link"
        assert record["Link"] == "http://phorge.localhost/R5"

    def test_no_internal_key_reaches_the_output(self, capsys):
        for output_format in ("rich", "yaml", "json", "jsonl", "tree"):
            assert "_link" not in self._render(capsys, output_format)
            assert "_url" not in self._render(capsys, output_format)

    def test_tree_renders_rather_than_falling_back(self, capsys):
        """--format is global; a format that quietly falls back is the bug."""
        output = self._render(capsys, "tree")

        assert "Repository" in output
        assert "Monogram: R5" in output
        # A tree, not the YAML-shaped rich output.
        assert "- Link:" not in output

    def test_an_unknown_format_falls_back_to_rich(self, capsys):
        assert self._render(capsys, "simple").startswith("- Link:")

    def test_the_aliases_reach_the_same_renderers(self, capsys):
        assert self._render(capsys, "strict") == self._render(capsys, "yaml")
        assert self._render(capsys, "ndjson") == self._render(capsys, "jsonl")


class TestRepoShowCli:
    """The CLI wiring: how the arguments arrive and what the exit code says."""

    def _invoke(self, args, result=None, side_effect=None):
        from typer.testing import CliRunner

        from phabfive.cli.diffusion import diffusion_app

        mock_diffusion = MagicMock()
        if side_effect is not None:
            mock_diffusion.repo_show.side_effect = side_effect
        else:
            mock_diffusion.repo_show.return_value = (
                result
                if result is not None
                else {"repositories": [], "missing_ids": []}
            )

        with patch(
            "phabfive.cli.diffusion._get_diffusion_app", return_value=mock_diffusion
        ):
            return CliRunner().invoke(diffusion_app, ["repo", "show", *args]), (
                mock_diffusion
            )

    def test_repositories_are_space_separated(self):
        _, diffusion = self._invoke(["R5", "R6"])

        assert diffusion.repo_show.call_args[0][0] == ["R5", "R6"]

    def test_repositories_are_comma_separated_too(self):
        _, diffusion = self._invoke(["R5,R6", "R7"])

        assert diffusion.repo_show.call_args[0][0] == ["R5", "R6", "R7"]

    def test_the_show_flags_are_passed_through(self):
        _, diffusion = self._invoke(
            ["R5", "--show-branches", "--show-tags", "--show-uris", "--show-metadata"]
        )

        assert diffusion.repo_show.call_args[1] == {
            "show_branches": True,
            "show_tags": True,
            "show_uris": True,
            "show_metadata": True,
            "show_description": True,
        }

    def test_nothing_is_shown_by_default(self):
        _, diffusion = self._invoke(["R5"])

        assert diffusion.repo_show.call_args[1] == {
            "show_branches": False,
            "show_tags": False,
            "show_uris": False,
            "show_metadata": False,
            "show_description": True,
        }

    def test_no_description_reaches_the_lookup(self):
        _, diffusion = self._invoke(["R5", "-n"])

        assert diffusion.repo_show.call_args[1]["show_description"] is False

    def test_a_repository_that_exists_exits_zero(self):
        result, _ = self._invoke(
            ["R5"],
            result={
                "repositories": [{"_url": "u", "_link": "u", "Repository": {}}],
                "missing_ids": [],
            },
        )

        assert result.exit_code == 0

    def test_a_missing_repository_is_a_failed_lookup(self):
        """Not an empty result - the same rule maniphest show follows."""
        result, _ = self._invoke(
            ["R9999"], result={"repositories": [], "missing_ids": ["R9999"]}
        )

        assert result.exit_code == 1

    def test_a_partial_result_still_fails(self):
        result, _ = self._invoke(
            ["R5", "R9999"],
            result={
                "repositories": [{"_url": "u", "_link": "u", "Repository": {}}],
                "missing_ids": ["R9999"],
            },
        )

        assert result.exit_code == 1

    def test_an_api_failure_is_reported_rather_than_tracebacked(self):
        result, _ = self._invoke(
            ["R5"], side_effect=PhabfiveDataException("data is unavailable")
        )

        assert result.exit_code == 1
        assert "data is unavailable" in result.output
        assert "Traceback" not in result.output


class TestEditHeadersLinkToPhabricator:
    """The header says which Phabricator object, not which git remote.

    The remote is already on the change line, so repeating it in the header
    spent the most prominent line restating the body, while the object
    being edited was named only by a monogram nothing could open.
    """

    def _diffusion(self, diffusion, url="https://phabricator.example.com"):
        diffusion.url = url
        return diffusion

    def test_link_names_the_monogram_and_the_short_name(self, diffusion):
        d = self._diffusion(diffusion)

        assert (
            d.link_repository(_repo("thing", short_name="thing"))
            == "https://phabricator.example.com/R1 (thing)"
        )

    def test_link_falls_back_to_callsign_then_name(self, diffusion):
        d = self._diffusion(diffusion)
        repo = _repo("thing")
        repo["fields"]["shortName"] = None
        repo["fields"]["callsign"] = "THING"

        assert d.link_repository(repo).endswith("/R1 (THING)")

    def test_link_without_any_name_is_just_the_url(self, diffusion):
        d = self._diffusion(diffusion)
        repo = _repo("thing")
        repo["fields"] = {"shortName": None, "callsign": None, "name": None}

        assert d.link_repository(repo) == "https://phabricator.example.com/R1"

    def test_the_describer_and_the_link_agree_on_the_name(self, diffusion):
        """Both read it through name_repository, so they cannot drift."""
        d = self._diffusion(diffusion)
        repo = _repo("thing", short_name="thing")

        assert d.describe_repository(repo) == "R1 (thing)"
        assert d.link_repository(repo).endswith("/R1 (thing)")

    def _invoke(self, args, changes):
        from typer.testing import CliRunner

        from phabfive.cli.diffusion import diffusion_app

        mock_diffusion = MagicMock()
        mock_diffusion.get_uri_and_repo.return_value = (_repo("myrepo"), _uri())
        mock_diffusion.link_repository.return_value = (
            "https://phabricator.example.com/R1 (myrepo)"
        )
        mock_diffusion.build_uri_edit.return_value = (
            [{"type": "disable", "value": True}],
            changes,
        )

        with patch(
            "phabfive.cli.diffusion._get_diffusion_app", return_value=mock_diffusion
        ):
            return CliRunner().invoke(diffusion_app, ["uri", "edit", *args])

    REMOTE = "git@example.com:group/project.git"

    def test_the_remote_is_not_repeated_in_the_header(self):
        result = self._invoke(
            [
                "myrepo",
                self.REMOTE,
                "--uri",
                "git@example.com:group/new.git",
                "--dry-run",
            ],
            [
                {
                    "field": "URI",
                    "old": self.REMOTE,
                    "new": "git@example.com:group/new.git",
                }
            ],
        )

        header = result.output.splitlines()[0]
        assert header.endswith("/R1 (myrepo):")
        assert self.REMOTE not in header

    def test_an_edit_that_leaves_the_uri_alone_still_names_it(self):
        """A --disable would otherwise not say which URI it meant."""
        result = self._invoke(
            ["myrepo", self.REMOTE, "--disable", "--dry-run"],
            [{"field": "Disabled", "old": "False", "new": "True"}],
        )

        lines = result.output.splitlines()
        assert lines[0].endswith("/R1 (myrepo):")
        assert lines[1] == f"  URI: {self.REMOTE}"
        assert "Disabled: False → True" in result.output

    def test_a_uri_change_is_not_listed_twice(self):
        result = self._invoke(
            [
                "myrepo",
                self.REMOTE,
                "--uri",
                "git@example.com:group/new.git",
                "--dry-run",
            ],
            [
                {
                    "field": "URI",
                    "old": self.REMOTE,
                    "new": "git@example.com:group/new.git",
                }
            ],
        )

        assert result.output.count("  URI:") == 1
