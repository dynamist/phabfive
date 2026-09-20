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
            mock_diffusion.describe_repository.return_value = f"R{rid} ({name})"
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


class TestBranchListFailsCleanly:
    """branch list was the last diffusion command without error handling.

    A repository can be listed and addressable and still fail to answer for
    its branches - the server reaches the repository record, then cannot
    reach its data. That surfaced as a raw traceback whose last line was
    the part worth reading.
    """

    def _invoke(self, argv, side_effect=None, branches=None):
        from typer.testing import CliRunner

        from phabfive.cli.diffusion import diffusion_app

        mock_diffusion = MagicMock()
        if side_effect is not None:
            mock_diffusion.get_branches_formatted.side_effect = side_effect
        else:
            mock_diffusion.get_branches_formatted.return_value = branches or []

        with patch(
            "phabfive.cli.diffusion._get_diffusion_app", return_value=mock_diffusion
        ):
            return CliRunner().invoke(diffusion_app, argv)

    def test_an_unreadable_repository_is_reported_cleanly(self):
        result = self._invoke(
            ["branch", "list", "R42"],
            side_effect=PhabfiveDataException("<branchquery> data is unavailable"),
        )

        assert result.exit_code == 1
        assert "data is unavailable" in result.output
        assert "Traceback" not in result.output

    def test_an_unknown_repository_is_reported_cleanly(self):
        result = self._invoke(
            ["branch", "list", "R9999"],
            side_effect=PhabfiveDataException("is not a valid repository"),
        )

        assert result.exit_code == 1
        assert "Traceback" not in result.output

    def test_a_repository_with_no_branches_is_not_an_error(self):
        """Empty is an empty result, not a failure."""
        result = self._invoke(["branch", "list", "R42"], branches=[])

        assert result.exit_code == 0
        assert result.output.strip() == ""

    def test_branches_are_listed_one_per_line(self):
        result = self._invoke(["branch", "list", "R42"], branches=["main", "topic"])

        assert result.exit_code == 0
        assert result.output.split() == ["main", "topic"]
