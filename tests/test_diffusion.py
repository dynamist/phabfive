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
    """A credential is named by monogram; its secret never reaches the output."""

    SECRET = "-----BEGIN OPENSSH PRIVATE KEY-----\nhunter2\n"

    def _diffusion(self, diffusion):
        diffusion.passphrase.get_secret = MagicMock(
            return_value={
                "id": 2,
                "monogram": "K2",
                "type": "ssh-key-text",
                "material": {"privateKey": self.SECRET},
            }
        )
        diffusion._validate_credential_type = MagicMock(
            return_value="PHID-CDTL-newcred"
        )
        return diffusion

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
        mock_diffusion.get_uri_record.return_value = _uri()
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
        diffusion.get_uri_record.assert_not_called()


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
            ["uri", "edit", "R42", "someuri", "--io=read"], "get_uri_record"
        )

        assert result.exit_code == 1
        assert "does not exist" in result.output
        assert "Traceback" not in result.output

    def test_uri_edit_reports_a_missing_uri_cleanly(self):
        result = self._invoke(
            ["uri", "edit", "myrepo", "nosuchuri", "--io=read"], "get_uri_record"
        )

        assert result.exit_code == 1
        assert "Traceback" not in result.output
