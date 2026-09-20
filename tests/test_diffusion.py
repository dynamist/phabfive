# -*- coding: utf-8 -*-

# 3rd party imports
from unittest.mock import MagicMock, patch

import pytest

# phabfive imports
from phabfive.constants import FORMAT_ALIASES
from phabfive.diffusion import Diffusion
from phabfive.exceptions import (
    PhabfiveConfigException,
    PhabfiveDataException,
    PhabfiveNameCollisionException,
)


def _repo(name, short_name=None, status="active", is_hosted=True):
    return {
        "id": 1,
        "phid": f"PHID-REPO-{name}",
        "fields": {
            "name": name,
            "shortName": short_name or name,
            "status": status,
            "isHosted": is_hosted,
        },
        "attachments": {"uris": {"uris": []}},
    }


def _builtin_uri(display, identifier, uri_id):
    """A built-in URI shaped the way the "uris" attachment returns one."""
    return {
        "id": uri_id,
        "phid": f"PHID-RURI-{identifier}",
        "fields": {
            "uri": {
                "raw": f"http://{identifier}",
                "display": display,
                "effective": display,
            },
            "io": {"raw": "default", "default": "read", "effective": "read"},
            "display": {"raw": "default", "default": "always", "effective": "always"},
            "credentialPHID": None,
            "builtin": {"protocol": "http", "identifier": identifier},
            "disabled": False,
        },
    }


def _with_builtin_uris(record, clone_name, callsign=None):
    """Attach the built-in URIs Phorge mints for a repository.

    One per shape the repository can be addressed by, in the order Phorge
    lists them: by id always, by short name when it has one, and by
    callsign when it has one. Every one of them ends in the clone name,
    which is why a rename moves the lot.
    """
    repo_id = record["id"]
    uris = [
        _builtin_uri(
            f"http://phorge.localhost/diffusion/{repo_id}/{clone_name}.git", "id", 1
        )
    ]

    if record["fields"].get("shortName"):
        uris.append(
            _builtin_uri(
                f"http://phorge.localhost/source/{clone_name}.git", "shortname", 2
            )
        )

    if callsign:
        uris.append(
            _builtin_uri(
                f"http://phorge.localhost/diffusion/{callsign}/{clone_name}.git",
                "callsign",
                3,
            )
        )

    record["attachments"]["uris"]["uris"] = uris

    return record


def _phab_with_repos(repos):
    phab = MagicMock()
    phab.diffusion.repository.search.return_value = {"data": repos}
    return phab


def _listing(repos):
    """A Diffusion wired to a mock API, ready for repo_list() and uri_list()."""
    with (
        patch("phabfive.diffusion.core.Phabfive.__init__", return_value=None),
        patch("phabfive.diffusion.core.passphrase.Passphrase"),
    ):
        diffusion = Diffusion()

    diffusion.phab = _phab_with_repos(repos)
    diffusion.url = "http://phorge.localhost"
    diffusion.format_link = lambda url, text: url
    diffusion.phab.phid.query.return_value = {}

    return diffusion


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
        assert _listing([]).repo_list()["repositories"] == []

    def test_uri_list_reports_unknown_repository(self):
        """No repositories means the one asked for does not exist.

        A raised exception rather than an empty list, so the CLI can tell
        an unknown repository apart from one that simply has no URIs.
        """
        with pytest.raises(PhabfiveDataException, match="not found"):
            _listing([]).uri_list("myrepo")

    def test_uri_list_is_empty_for_repository_without_uris(self):
        """An existing repository with no URIs is an empty result, not an error."""
        assert _listing([_repo("myrepo")]).uri_list("myrepo") == {"uris": []}

    def test_uri_list_reports_unknown_repository_among_others(self):
        with pytest.raises(PhabfiveDataException, match="not found"):
            _listing([_repo("myrepo")]).uri_list("otherrepo")

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


class TestFoldShortName:
    """What phabfive treats as the same short name."""

    def test_case_is_folded(self):
        from phabfive.diffusion.core import fold_short_name

        assert fold_short_name("MyRepo") == fold_short_name("myrepo")

    def test_the_three_punctuation_characters_a_slug_may_contain_are_folded(self):
        """Phorge allows only . - _ in a slug, so those are the whole set."""
        from phabfive.diffusion.core import fold_short_name

        folded = {
            fold_short_name(name)
            for name in ("myrepo", "my-repo", "my_repo", "my.repo", "My-Repo")
        }

        assert folded == {"myrepo"}

    def test_a_different_name_stays_different(self):
        from phabfive.diffusion.core import fold_short_name

        assert fold_short_name("my-repo") != fold_short_name("myrepos")

    def test_an_absent_name_folds_to_empty(self):
        """A repository may carry no short name at all."""
        from phabfive.diffusion.core import fold_short_name

        assert fold_short_name(None) == ""


class TestNearDuplicateShortNames:
    """A name Phorge would accept, but nobody could tell apart from one in place.

    Phorge refuses a short name that differs only in case - `repositorySlug`
    is a `utf8mb4_unicode_ci` column under a unique key - but it accepts one
    that differs only in punctuation, and a repository cannot be deleted
    afterwards.
    """

    def test_rejects_a_case_variant(self, diffusion):
        diffusion.phab.diffusion.repository.search.return_value = {
            "data": [_repo("myrepo")]
        }

        with pytest.raises(PhabfiveNameCollisionException, match="too similar"):
            diffusion.build_repo_create(name="MyRepo")

    @pytest.mark.parametrize("name", ["my-repo", "my_repo", "my.repo", "My-Repo"])
    def test_rejects_a_punctuation_variant(self, diffusion, name):
        """The case Phorge itself lets through."""
        diffusion.phab.diffusion.repository.search.return_value = {
            "data": [_repo("myrepo")]
        }

        with pytest.raises(PhabfiveNameCollisionException):
            diffusion.build_repo_create(name=name)

        diffusion.phab.diffusion.repository.edit.assert_not_called()

    def test_a_genuinely_different_name_is_untouched(self, diffusion):
        diffusion.phab.diffusion.repository.search.return_value = {
            "data": [_repo("myrepo")]
        }

        transactions, _ = diffusion.build_repo_create(name="genuinely-different")

        assert {"type": "shortName", "value": "genuinely-different"} in transactions

    def test_the_error_names_the_repository_it_collided_with(self, diffusion):
        """The user needs to know whether they meant the existing one."""
        existing = _repo("myrepo")
        existing["id"] = 86
        diffusion.phab.diffusion.repository.search.return_value = {"data": [existing]}

        with pytest.raises(PhabfiveNameCollisionException, match=r"R86 \(myrepo\)"):
            diffusion.build_repo_create(name="my-repo")

    def test_an_exact_clash_still_says_already_exists(self, diffusion):
        """And is a plain data error, because it has no override."""
        diffusion.phab.diffusion.repository.search.return_value = {
            "data": [_repo("myrepo")]
        }

        with pytest.raises(PhabfiveDataException, match="already exists") as caught:
            diffusion.build_repo_create(name="myrepo")

        assert not isinstance(caught.value, PhabfiveNameCollisionException)

    def test_an_exact_clash_outranks_a_near_one_behind_it(self, diffusion):
        """Otherwise the error offers an override that would not work."""
        diffusion.phab.diffusion.repository.search.return_value = {
            "data": [_repo("my-repo"), _repo("myrepo")]
        }

        with pytest.raises(PhabfiveDataException, match="already exists"):
            diffusion.build_repo_create(name="myrepo")

    def test_allow_similar_creates_the_near_duplicate(self, diffusion):
        diffusion.phab.diffusion.repository.search.return_value = {
            "data": [_repo("myrepo")]
        }

        transactions, _ = diffusion.build_repo_create(
            name="my-repo", allow_similar=True
        )

        assert {"type": "shortName", "value": "my-repo"} in transactions

    def test_allow_similar_does_not_reach_an_exact_clash(self, diffusion):
        """There is nothing to override - Phorge refuses it as well."""
        diffusion.phab.diffusion.repository.search.return_value = {
            "data": [_repo("myrepo")]
        }

        with pytest.raises(PhabfiveDataException, match="already exists"):
            diffusion.build_repo_create(name="myrepo", allow_similar=True)

    def test_the_check_costs_one_listing(self, diffusion):
        """The guard reuses the fetch the exact-match check already paid for."""
        diffusion.phab.diffusion.repository.search.return_value = {
            "data": [_repo("myrepo")]
        }

        diffusion.build_repo_create(name="genuinely-different")

        assert diffusion.phab.diffusion.repository.search.call_count == 1


class TestRenameIntoACollision:
    """`repo edit --short-name` claims a name the same way `repo create` does."""

    def _record(self, short_name="myrepo", repo_id=7):
        record = _repo(short_name)
        record["id"] = repo_id
        return record

    def test_rejects_a_near_duplicate_rename(self, diffusion):
        diffusion.phab.diffusion.repository.search.return_value = {
            "data": [_repo("other-repo")]
        }

        with pytest.raises(PhabfiveNameCollisionException, match="too similar"):
            diffusion.build_repo_edit(self._record(), short_name="otherrepo")

    def test_a_repository_does_not_collide_with_itself(self, diffusion):
        """Renaming `my-repo` to `myrepo` is a repunctuation, not a clash."""
        record = self._record(short_name="my-repo")
        diffusion.phab.diffusion.repository.search.return_value = {"data": [record]}

        transactions, _ = diffusion.build_repo_edit(record, short_name="myrepo")

        assert {"type": "shortName", "value": "myrepo"} in transactions

    def test_allow_similar_permits_the_rename(self, diffusion):
        diffusion.phab.diffusion.repository.search.return_value = {
            "data": [_repo("other-repo")]
        }

        transactions, _ = diffusion.build_repo_edit(
            self._record(), short_name="otherrepo", allow_similar=True
        )

        assert {"type": "shortName", "value": "otherrepo"} in transactions

    def test_an_edit_that_leaves_the_short_name_alone_fetches_nothing(self, diffusion):
        """The listing is only worth paying for when the name is actually moving."""
        diffusion.build_repo_edit(self._record(), default_branch="main")

        diffusion.phab.diffusion.repository.search.assert_not_called()

    def test_setting_the_short_name_to_what_it_already_is_fetches_nothing(
        self, diffusion
    ):
        diffusion.build_repo_edit(self._record(), short_name="myrepo")

        diffusion.phab.diffusion.repository.search.assert_not_called()


class TestRepoList:
    """The records `repo list` answers with - the ones `repo show` answers with."""

    def _names(self, result):
        return [r["Repository"]["Name"] for r in result["repositories"]]

    def test_filters_by_status_and_sorts_by_name(self):
        diffusion = _listing(
            [_repo("zeta"), _repo("alpha"), _repo("old", status="inactive")]
        )

        assert self._names(diffusion.repo_list(status=["active"])) == ["alpha", "zeta"]

    def test_inactive_repositories_are_listed_when_asked_for(self):
        diffusion = _listing([_repo("alpha"), _repo("old", status="inactive")])

        assert self._names(diffusion.repo_list(status=["inactive"])) == ["old"]

    def test_a_listed_repository_is_a_shown_one(self):
        """The same builder, so a list and a show cannot disagree."""
        repo = _show_repo()
        listed = _listing([repo]).repo_list()["repositories"][0]
        shown = _showable([repo]).repo_show(["R5"])["repositories"][0]

        assert listed == shown

    def test_the_uris_section_is_absent_until_asked_for(self):
        record = _listing([_show_repo()]).repo_list()["repositories"][0]

        assert "URIs" not in record

    def test_show_uris_describes_each_uri(self):
        repo = _show_repo(uris=[_show_uri("git@github.com:dynamist/phabfive.git")])
        record = _listing([repo]).repo_list(show_uris=True)["repositories"][0]

        assert record["URIs"] == [
            {
                "URI": "git@github.com:dynamist/phabfive.git",
                "Origin": "external",
                "Role": "Phorge pulls from here",
                "I/O": {"Raw": "observe", "Default": "none", "Effective": "observe"},
                "Display": {"Raw": "always", "Default": "never", "Effective": "always"},
                "Disabled": False,
            }
        ]

    def test_branches_and_tags_are_never_queried(self):
        """One query per repository is what a list command cannot pay."""
        diffusion = _listing([_show_repo()])

        diffusion.repo_list(show_uris=True)

        diffusion.phab.diffusion.branchquery.assert_not_called()
        diffusion.phab.diffusion.tagsquery.assert_not_called()


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
        repo = _repo("anything")
        repo["id"] = 5
        repo["attachments"]["uris"]["uris"] = [_show_uri("git@example.com:x.git")]

        result = _listing([repo]).uri_list("R5", clone_only=True)

        assert [uri["URI"] for uri in result["uris"]] == ["git@example.com:x.git"]


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

    @pytest.fixture(autouse=True)
    def _an_instance_of_one(self, diffusion):
        """A rename now asks the instance for a clash; these tests are not about that.

        TestRenameIntoACollision covers the check itself.
        """
        diffusion.phab.diffusion.repository.search.return_value = {"data": []}

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
        """Phorge derives the clone URIs from the short name, so a rename moves them."""
        record = _with_builtin_uris(self._record(), "oldname")

        transactions, changes = diffusion.build_repo_edit(record, short_name="newname")

        assert transactions == [{"type": "shortName", "value": "newname"}]
        assert {
            "field": "Built-in URI",
            "old": "http://phorge.localhost/source/oldname.git",
            "new": "http://phorge.localhost/source/newname.git",
        } in changes

    def test_a_name_change_leaves_a_short_named_repositorys_uris_alone(self, diffusion):
        """A short name in place is the clone name, so --name moves nothing."""
        record = _with_builtin_uris(self._record(), "oldname")

        _, changes = diffusion.build_repo_edit(record, name="newname")

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


class TestBuiltInUriWarning:
    """What a rename tells you it breaks, which is the only warning there is.

    Three repository shapes, because each one gets the warning wrong in its
    own way if the /source/ path is guessed rather than read off the record.
    """

    @pytest.fixture(autouse=True)
    def _an_instance_of_one(self, diffusion):
        diffusion.phab.diffusion.repository.search.return_value = {"data": []}

    def _uri_changes(self, changes):
        return [c for c in changes if c["field"].startswith("Built-in URI")]

    def _with_callsign(self):
        """A short name and a callsign: all three URI shapes at once."""
        record = _repo("oldname", short_name="oldname")
        record["id"] = 26
        record["fields"]["callsign"] = "PROBEA"
        return _with_builtin_uris(record, "oldname", callsign="PROBEA")

    def _without_short_name(self):
        """No short name, so the clone name is the name, and there is no /source/."""
        record = _repo("oldname")
        record["id"] = 24
        record["fields"]["shortName"] = None
        return _with_builtin_uris(record, "oldname")

    def _plain(self):
        """A short name and no callsign - the ordinary repository."""
        record = _repo("oldname", short_name="oldname")
        record["id"] = 25
        return _with_builtin_uris(record, "oldname")

    def test_every_shape_a_callsign_repository_is_addressed_by_is_named(
        self, diffusion
    ):
        _, changes = diffusion.build_repo_edit(
            self._with_callsign(), short_name="newname"
        )

        assert self._uri_changes(changes) == [
            {
                "field": "Built-in URI",
                "old": "http://phorge.localhost/diffusion/26/oldname.git",
                "new": "http://phorge.localhost/diffusion/26/newname.git",
            },
            {
                "field": "Built-in URI",
                "old": "http://phorge.localhost/source/oldname.git",
                "new": "http://phorge.localhost/source/newname.git",
            },
            {
                "field": "Built-in URI",
                "old": "http://phorge.localhost/diffusion/PROBEA/oldname.git",
                "new": "http://phorge.localhost/diffusion/PROBEA/newname.git",
            },
        ]

    def test_a_name_change_moves_the_uris_of_a_repository_with_no_short_name(
        self, diffusion
    ):
        """The case the old `if kind == "shortName"` guard let through silently."""
        _, changes = diffusion.build_repo_edit(
            self._without_short_name(), name="newname"
        )

        assert self._uri_changes(changes) == [
            {
                "field": "Built-in URI",
                "old": "http://phorge.localhost/diffusion/24/oldname.git",
                "new": "http://phorge.localhost/diffusion/24/newname.git",
            }
        ]

    def test_giving_a_short_named_repository_a_short_name_never_prints_none(
        self, diffusion
    ):
        """The old code interpolated a null short name as the literal "None"."""
        _, changes = diffusion.build_repo_edit(
            self._without_short_name(), short_name="newname"
        )

        assert self._uri_changes(changes) == [
            {
                "field": "Built-in URI",
                "old": "http://phorge.localhost/diffusion/24/oldname.git",
                "new": "http://phorge.localhost/diffusion/24/newname.git",
            }
        ]
        assert "None" not in str(changes)

    def test_the_ordinary_repository_still_reads_correctly(self, diffusion):
        _, changes = diffusion.build_repo_edit(self._plain(), short_name="newname")

        assert self._uri_changes(changes) == [
            {
                "field": "Built-in URI",
                "old": "http://phorge.localhost/diffusion/25/oldname.git",
                "new": "http://phorge.localhost/diffusion/25/newname.git",
            },
            {
                "field": "Built-in URI",
                "old": "http://phorge.localhost/source/oldname.git",
                "new": "http://phorge.localhost/source/newname.git",
            },
        ]

    def test_an_external_uri_is_not_claimed_to_move(self, diffusion):
        """A URI someone added is not derived from the clone name."""
        record = self._plain()
        record["attachments"]["uris"]["uris"].append(
            _uri("git@github.com:dynamist/oldname.git")
        )

        _, changes = diffusion.build_repo_edit(record, short_name="newname")

        assert all("github" not in c["old"] for c in self._uri_changes(changes))

    def test_a_name_change_on_a_short_named_repository_moves_nothing(self, diffusion):
        _, changes = diffusion.build_repo_edit(self._plain(), name="A New Name")

        assert self._uri_changes(changes) == []

    def test_setting_a_field_to_what_it_already_is_moves_nothing(self, diffusion):
        _, changes = diffusion.build_repo_edit(self._plain(), short_name="oldname")

        assert self._uri_changes(changes) == []

    def test_an_edit_that_is_not_a_rename_moves_nothing(self, diffusion):
        _, changes = diffusion.build_repo_edit(self._plain(), status="inactive")

        assert self._uri_changes(changes) == []

    def test_uris_hidden_by_a_view_policy_are_said_to_move_anyway(self, diffusion):
        """An empty attachment is a restricted view policy, not a repository
        without clone URIs - staying quiet would understate the rename."""
        record = _repo("oldname", short_name="oldname")

        _, changes = diffusion.build_repo_edit(record, short_name="newname")

        assert self._uri_changes(changes) == [
            {
                "field": "Built-in URIs",
                "old": None,
                "new": "not visible on this repository, and any it has move too",
            }
        ]


class TestBuiltinUriMoves:
    """The record-shape half, on its own."""

    def _record(self, uris):
        return {"attachments": {"uris": {"uris": uris}}}

    def test_a_repository_with_no_uris_moves_nothing(self):
        from phabfive.diffusion.formatters import builtin_uri_moves

        assert builtin_uri_moves(self._record([]), "old", "new") == []

    def test_a_uri_not_ending_in_the_clone_name_is_left_alone(self):
        """Only the last path segment is derived from the clone name."""
        from phabfive.diffusion.formatters import builtin_uri_moves

        record = self._record(
            [_builtin_uri("http://phorge.localhost/source/old/thing.git", "id", 1)]
        )

        assert builtin_uri_moves(record, "old", "new") == []

    def test_a_suffixless_uri_moves(self):
        """Not every VCS gets a ".git" appended."""
        from phabfive.diffusion.formatters import builtin_uri_moves

        record = self._record(
            [_builtin_uri("ssh://phorge.localhost/source/old", "shortname", 1)]
        )

        assert builtin_uri_moves(record, "old", "new") == [
            ("ssh://phorge.localhost/source/old", "ssh://phorge.localhost/source/new")
        ]

    def test_only_the_last_occurrence_of_the_clone_name_moves(self):
        from phabfive.diffusion.formatters import builtin_uri_moves

        record = self._record(
            [_builtin_uri("http://old.example.com/source/old.git", "shortname", 1)]
        )

        assert builtin_uri_moves(record, "old", "new") == [
            (
                "http://old.example.com/source/old.git",
                "http://old.example.com/source/new.git",
            )
        ]


class TestBuiltinCloneName:
    """Which field Phorge builds the clone URIs out of."""

    def test_the_short_name_wins_when_it_is_set(self):
        from phabfive.diffusion.formatters import builtin_clone_name

        assert builtin_clone_name({"name": "A Name", "shortName": "sn"}) == "sn"

    def test_the_name_is_used_when_there_is_no_short_name(self):
        from phabfive.diffusion.formatters import builtin_clone_name

        assert builtin_clone_name({"name": "a-name", "shortName": None}) == "a-name"


class TestRichIsYaml:
    """--format=rich is YAML-shaped, and is read back as YAML.

    Which means a value YAML would quote has to be quoted here too. Policy
    values are what made this reachable with ordinary data.
    """

    def _rendered(self, value):
        from phabfive.diffusion.display import _yaml_scalar

        return _yaml_scalar(value)

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("#security", "'#security'"),
            ("@admin", "'@admin'"),
            ("true", "'true'"),
            ("123", "'123'"),
            ("null", "'null'"),
            ("a: b", "'a: b'"),
            ("", "''"),
        ],
    )
    def test_a_value_yaml_would_read_back_differently_is_quoted(self, value, expected):
        """`Edit: #security` is the dangerous one: YAML reads it as an empty
        value plus a comment, so it round-trips clean and wrong."""
        assert self._rendered(value) == expected

    @pytest.mark.parametrize(
        "value",
        [
            "Public (No Login Required)",
            "S1 Default",
            "feature/telemetry",
            "gunnar-firmware",
            "A CLI for Phorge",
        ],
    )
    def test_an_ordinary_value_is_left_alone(self, value):
        assert self._rendered(value) == value

    def test_booleans_are_yaml_booleans(self):
        assert (self._rendered(True), self._rendered(False)) == ("true", "false")

    def test_a_long_value_stays_on_one_line(self):
        """ruamel folds a long scalar at its default width, and the renderer
        prints what comes back as a single line - so a folded answer arrived
        as a continuation with no indent and took the document with it."""
        value = "Sample hosted repository with history, " * 5

        assert "\n" not in self._rendered(value)

    def test_the_whole_record_parses_back_as_yaml(self):
        """The property the e2e format-agreement test asserts, in miniature."""
        from io import StringIO

        from ruamel.yaml import YAML

        from phabfive.diffusion.display import display_records_rich

        record = {
            "_url": "http://phorge.localhost/R5",
            "Policy": {
                "Visible To": "public",
                "Editable By": "#security",
                "Can Push": "@admin",
            },
        }

        console = MagicMock()
        printed = []
        console.print.side_effect = lambda line="", **kw: printed.append(str(line))

        display_records_rich(console, [record], MagicMock())

        parsed = YAML(typ="safe").load(StringIO("\n".join(printed)))

        assert parsed[0]["Policy"] == {
            "Visible To": "public",
            "Editable By": "#security",
            "Can Push": "@admin",
        }


class TestBuildPolicyEdit:
    """Policies are the one repository field that is not a scalar.

    Both ends of the change have to be resolved before either can be shown,
    and the transaction names are not the ones the edit form uses.
    """

    def _record(self, **policy):
        record = _repo("oldname", short_name="oldname")
        record["fields"]["policy"] = {
            "view": "users",
            "edit": "admin",
            "diffusion.push": "users",
            **policy,
        }
        return record

    def test_the_transaction_names_are_the_ones_conduit_accepts(self, diffusion):
        """Confirmed against the instance: diffusion.repository.edit lists
        `view`, `edit` and `policy.push` among its valid types and refuses
        `policy.view`, `policy.edit` and `push`. The two halves are declared
        in different places in Phorge, which is why they are spelled
        differently."""
        transactions, _ = diffusion.build_repo_edit(
            self._record(),
            visible_to="public",
            editable_by="public",
            can_push="public",
        )

        assert transactions == [
            {"type": "view", "value": "public"},
            {"type": "edit", "value": "public"},
            {"type": "policy.push", "value": "public"},
        ]

    def test_the_change_names_both_ends(self, diffusion):
        _, changes = diffusion.build_repo_edit(self._record(), visible_to="public")

        assert changes == [
            {
                "field": "Visible To",
                "old": "All Users",
                "new": "Public (No Login Required)",
            }
        ]

    def test_a_project_is_resolved_and_then_named(self, diffusion):
        """A PHID either side of an arrow says nothing about what changed."""
        diffusion.phab.project.search.return_value = {
            "data": [{"phid": "PHID-PROJ-infra"}]
        }
        diffusion.phab.phid.query.return_value = {
            "PHID-PROJ-infra": {
                "type": "PROJ",
                "name": "Infrastructure",
                "uri": "http://phorge.localhost/tag/infrastructure/",
            }
        }

        transactions, changes = diffusion.build_repo_edit(
            self._record(), editable_by="#infrastructure"
        )

        assert transactions == [{"type": "edit", "value": "PHID-PROJ-infra"}]
        assert changes == [
            {
                "field": "Editable By",
                "old": "Administrators",
                "new": "#infrastructure",
            }
        ]

    def test_a_policy_already_in_place_is_not_a_transaction(self, diffusion):
        transactions, changes = diffusion.build_repo_edit(
            self._record(), visible_to="users"
        )

        assert transactions == []
        assert changes == []

    def test_a_policy_outside_the_grammar_is_refused(self, diffusion):
        with pytest.raises(PhabfiveConfigException) as excinfo:
            diffusion.build_repo_edit(self._record(), visible_to="nonsense")

        assert "--visible-to" in str(excinfo.value)
        diffusion.phab.diffusion.repository.edit.assert_not_called()

    def test_policies_ride_along_with_the_scalar_fields(self, diffusion):
        transactions, changes = diffusion.build_repo_edit(
            self._record(), name="newname", visible_to="public"
        )

        assert [t["type"] for t in transactions] == ["name", "view"]
        assert [c["field"] for c in changes] == ["Name", "Visible To"]

    def test_a_repository_with_no_policy_field_still_edits(self, diffusion):
        """An older instance, or a record fetched without them."""
        record = _repo("oldname")

        transactions, changes = diffusion.build_repo_edit(record, visible_to="public")

        assert transactions == [{"type": "view", "value": "public"}]
        assert changes[0]["old"] == "(none)"

    def test_both_ends_of_every_arrow_come_out_of_one_lookup(self, diffusion):
        record = self._record(view="PHID-PROJ-old")
        diffusion.phab.project.search.return_value = {
            "data": [{"phid": "PHID-PROJ-new"}]
        }

        diffusion.build_repo_edit(record, visible_to="#new", editable_by="public")

        diffusion.phab.phid.query.assert_called_once()

    def test_a_self_lockout_is_reported_as_a_sentence(self, diffusion):
        """Phorge refuses the edit; the stack trace around it says nothing
        the sentence does not."""
        from phabricator import APIError

        diffusion.phab.diffusion.repository.edit.side_effect = APIError(
            "ERR-CONDUIT-CORE",
            "Validation errors:\n  - The view policy of this object would no "
            "longer allow you to view the object.",
        )

        with pytest.raises(PhabfiveDataException) as excinfo:
            diffusion.apply_repo_edit(1, [{"type": "view", "value": "no-one"}])

        message = str(excinfo.value)

        assert message.startswith("The view policy of this object")
        assert "Nothing was changed" in message


class TestRepoEditCli:
    """repo edit matches the vocabulary uri edit settled on."""

    def _invoke(self, args, built=None, apply_error=None, record=None):
        from typer.testing import CliRunner

        from phabfive.cli.diffusion import diffusion_app

        mock_diffusion = MagicMock()
        mock_diffusion.get_repo_record.return_value = record or _repo("oldname")
        mock_diffusion.build_repo_edit.return_value = built or (
            [{"type": "defaultBranch", "value": "main"}],
            [{"field": "Default branch", "old": "master", "new": "main"}],
        )
        mock_diffusion.apply_repo_edit.side_effect = apply_error

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

    def test_the_policy_options_are_passed_through(self):
        """Named the way the Phorge web UI labels them on the Policies panel,
        and mapped onto the internal keys the API names hang off."""
        result, diffusion = self._invoke(
            [
                "R42",
                "--visible-to=public",
                "--editable-by=#infrastructure",
                "--can-push=@admin",
                "--dry-run",
            ]
        )

        assert result.exit_code == 0
        assert diffusion.build_repo_edit.call_args.kwargs == {
            "name": None,
            "short_name": None,
            "default_branch": None,
            "status": None,
            "visible_to": "public",
            "editable_by": "#infrastructure",
            "can_push": "@admin",
            "allow_similar": False,
        }

    def test_setting_a_push_policy_on_a_non_hosted_repository_warns(self):
        """A warning and not a refusal, because Phorge allows it.

        `PhabricatorRepository::getPolicy` returns `getPushPolicy()`, a plain
        getter, and `PhabricatorRepositoryPushPolicyTransaction` is a real
        transaction type - so unlike a task's derived `Can Interact`, this is
        a policy that is genuinely stored and genuinely editable on any
        repository. It just goes unread until the repository is hosted, which
        a repository can become later.
        """
        result, diffusion = self._invoke(
            ["R42", "--can-push=admin", "--dry-run"],
            record=_repo("oldname", is_hosted=False),
        )

        assert result.exit_code == 0
        assert "not a hosted repository" in result.stderr
        # result.output, not result.stdout, the way the sibling dry-run test
        # above reads it: these invoke diffusion_app directly, so there is no
        # root --format to set and the format auto-detects to yaml on a
        # non-TTY - which since #344 puts a dry run's preview on stderr.
        assert "[DRY RUN]" in result.output
        diffusion.build_repo_edit.assert_called_once()

    def test_the_push_warning_does_not_stop_the_edit(self):
        result, diffusion = self._invoke(
            ["R42", "--can-push=admin", "--yes"],
            record=_repo("oldname", is_hosted=False),
        )

        assert result.exit_code == 0
        diffusion.apply_repo_edit.assert_called_once()

    def test_a_hosted_repository_is_not_warned_about(self):
        result, _ = self._invoke(
            ["R42", "--can-push=admin", "--dry-run"],
            record=_repo("oldname", is_hosted=True),
        )

        assert "not a hosted repository" not in result.stderr

    def test_only_a_push_policy_is_warned_about(self):
        """View and edit apply whether or not the repository is hosted."""
        result, _ = self._invoke(
            ["R42", "--visible-to=public", "--dry-run"],
            record=_repo("oldname", is_hosted=False),
        )

        assert "not a hosted repository" not in result.stderr

    def test_there_is_no_bare_edit_option(self):
        """`repo edit --edit` is unreadable, and --editable-by sidesteps it."""
        result, diffusion = self._invoke(["R42", "--edit=public"])

        assert result.exit_code != 0
        diffusion.apply_repo_edit.assert_not_called()

    def test_a_policy_alone_is_enough_to_ask_for(self):
        result, diffusion = self._invoke(["R42", "--visible-to=public"])

        assert result.exit_code == 0
        diffusion.apply_repo_edit.assert_called_once()

    def test_a_value_outside_the_grammar_is_never_sent(self):
        """Refused before the instance is reached, because Conduit reads an
        unknown value as a policy nobody satisfies rather than as a typo."""
        result, diffusion = self._invoke(["R42", "--visible-to=nonsense"])

        assert result.exit_code == 1
        assert "--visible-to must be one of" in result.output
        diffusion.get_repo_record.assert_not_called()
        diffusion.apply_repo_edit.assert_not_called()

    def test_a_project_that_does_not_exist_is_reported_cleanly(self):
        from typer.testing import CliRunner

        from phabfive.cli.diffusion import diffusion_app

        mock_diffusion = MagicMock()
        mock_diffusion.get_repo_record.return_value = _repo("oldname")
        mock_diffusion.build_repo_edit.side_effect = PhabfiveDataException(
            "Project '#nope' does not exist"
        )

        with patch(
            "phabfive.cli.diffusion._get_diffusion_app", return_value=mock_diffusion
        ):
            result = CliRunner().invoke(
                diffusion_app, ["repo", "edit", "R42", "--editable-by=#nope"]
            )

        assert result.exit_code == 1
        assert "does not exist" in result.output
        assert "Traceback" not in result.output
        mock_diffusion.apply_repo_edit.assert_not_called()

    def test_a_refused_edit_is_reported_cleanly(self):
        """A self-lockout reaches the CLI as a PhabfiveDataException, which
        used to go all the way out as a traceback."""
        result, diffusion = self._invoke(
            ["R42", "--visible-to=no-one", "--yes"],
            apply_error=PhabfiveDataException(
                "The view policy of this object would no longer allow you to "
                "view the object."
            ),
        )

        assert result.exit_code == 1
        assert "would no longer allow you" in result.output
        assert "Traceback" not in result.output

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

    def test_a_near_duplicate_is_refused_and_names_the_override(self):
        from typer.testing import CliRunner

        from phabfive.cli.diffusion import diffusion_app

        mock_diffusion = MagicMock()
        mock_diffusion.build_repo_create.side_effect = PhabfiveNameCollisionException(
            "Repository my-repo is too similar to R86 (myrepo)"
        )

        with patch(
            "phabfive.cli.diffusion._get_diffusion_app", return_value=mock_diffusion
        ):
            result = CliRunner().invoke(diffusion_app, ["repo", "create", "my-repo"])

        assert result.exit_code == 1
        assert "too similar" in result.output
        assert "--allow-similar" in result.output
        assert "cannot be deleted once created" in result.output
        mock_diffusion.apply_repo_create.assert_not_called()

    def test_an_exact_clash_does_not_offer_the_override(self):
        """It would not work - Phorge refuses an exact clash as well."""
        from typer.testing import CliRunner

        from phabfive.cli.diffusion import diffusion_app

        mock_diffusion = MagicMock()
        mock_diffusion.build_repo_create.side_effect = PhabfiveDataException(
            "Repository myrepo already exists as R86 (myrepo)"
        )

        with patch(
            "phabfive.cli.diffusion._get_diffusion_app", return_value=mock_diffusion
        ):
            result = CliRunner().invoke(diffusion_app, ["repo", "create", "myrepo"])

        assert result.exit_code == 1
        assert "--allow-similar" not in result.output

    def test_the_near_duplicate_check_runs_before_the_dry_run_prints(self):
        """A preview that misses the clash is worse than no preview."""
        from typer.testing import CliRunner

        from phabfive.cli.diffusion import diffusion_app

        mock_diffusion = MagicMock()
        mock_diffusion.build_repo_create.side_effect = PhabfiveNameCollisionException(
            "Repository my-repo is too similar to R86 (myrepo)"
        )

        with patch(
            "phabfive.cli.diffusion._get_diffusion_app", return_value=mock_diffusion
        ):
            result = CliRunner().invoke(
                diffusion_app, ["repo", "create", "my-repo", "--dry-run"]
            )

        assert result.exit_code == 1
        assert "[DRY RUN]" not in result.output

    def test_allow_similar_is_passed_through(self):
        result, diffusion = self._invoke(["my-repo", "--allow-similar", "--dry-run"])

        assert result.exit_code == 0
        assert diffusion.build_repo_create.call_args.kwargs["allow_similar"] is True

    def test_a_rename_collision_gives_the_reason_that_fits_a_rename(self):
        """A rename can be undone; a create cannot. They do not claim the same reason."""
        from typer.testing import CliRunner

        from phabfive.cli.diffusion import diffusion_app

        mock_diffusion = MagicMock()
        mock_diffusion.build_repo_edit.side_effect = PhabfiveNameCollisionException(
            "Repository otherrepo is too similar to R86 (other-repo)"
        )

        with patch(
            "phabfive.cli.diffusion._get_diffusion_app", return_value=mock_diffusion
        ):
            result = CliRunner().invoke(
                diffusion_app, ["repo", "edit", "R7", "--short-name=otherrepo"]
            )

        assert result.exit_code == 1
        assert "rewrites the built-in URIs" in result.output
        assert "cannot be deleted once created" not in result.output
        assert "--allow-similar" in result.output


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
    is_hosted=None,
):
    """A repository as diffusion.repository.search answers with it.

    ``is_hosted`` is left out of the record unless it is given, because an
    instance old enough not to report isHosted is a case the hosting
    fallback exists for and several tests exercise.
    """
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

    if is_hosted is not None:
        fields["isHosted"] = is_hosted

    return {
        "id": repo_id,
        "phid": f"PHID-REPO-{repo_id}",
        "fields": fields,
        "attachments": {"uris": {"uris": uris if uris is not None else []}},
    }


def _show_uri(uri, io="observe", display="always", disabled=False, builtin=None):
    """A URI as the uris attachment returns it.

    Phorge spells io and display as raw/default/effective; `io` and
    `display` here are what is written on the URI, which for anything but
    the literal "default" is also what is in force. `builtin` is the
    protocol Phorge generated the URI for, and None is a URI someone added.
    """
    return {
        "id": 1,
        "fields": {
            "uri": {"display": uri},
            "io": {"raw": io, "default": "none", "effective": io},
            "display": {"raw": display, "default": "never", "effective": display},
            "disabled": disabled,
            "builtin": {"protocol": builtin, "identifier": builtin and "shortname"},
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
        """And the push one is "Can Push", not "Pushable By".

        AphrontFormPolicyControl special-cases three capabilities into the
        "-able By" family - CAN_VIEW, CAN_EDIT and CAN_JOIN - and every
        other one falls through to its capability name. Push is not one of
        the three, so DiffusionPushCapability::getCapabilityName() names it
        and that returns "Can Push". "Pushable By" is a label the Policies
        management panel applies to its own row and nothing else uses.
        """
        repo = _show_repo(
            policy={"view": "public", "edit": "admin", "diffusion.push": "no-one"},
            is_hosted=True,
        )
        record = _showable([repo]).repo_show(["R5"], show_policy=True)["repositories"][
            0
        ]

        assert record["Policy"] == {
            "Visible To": "Public (No Login Required)",
            "Editable By": "Administrators",
            "Can Push": "No One",
        }

    def test_a_non_hosted_repository_has_no_push_policy_to_report(self):
        """Phorge declines to show one, and so does phabfive.

        DiffusionRepositoryPoliciesManagementPanel prints
        `Not a Hosted Repository` in place of the value on a repository it
        does not host, because nothing consults the stored policy there.
        Printing the stored value instead would read as a rule in force.
        """
        repo = _show_repo(
            policy={"view": "public", "edit": "admin", "diffusion.push": "users"},
            is_hosted=False,
        )
        record = _showable([repo]).repo_show(["R5"], show_policy=True)["repositories"][
            0
        ]

        assert record["Repository"]["Hosted"] is False
        assert record["Policy"]["Can Push"] == "Not a Hosted Repository"

    def test_the_policy_keys_are_the_same_whether_hosted_or_not(self):
        """A string and not a null or a dropped key.

        A reader consuming one record per line has to find the same keys on
        every line, and `Repository.Hosted` in the same record is what a
        script tests - so the push slot stays a string in every format.
        """
        hosted = _showable([_show_repo(is_hosted=True)]).repo_show(
            ["R5"], show_policy=True
        )
        observed = _showable([_show_repo(is_hosted=False)]).repo_show(
            ["R5"], show_policy=True
        )

        assert (
            hosted["repositories"][0]["Policy"].keys()
            == observed["repositories"][0]["Policy"].keys()
        )
        assert isinstance(observed["repositories"][0]["Policy"]["Can Push"], str)

    def test_the_other_two_policies_are_unaffected_by_hosting(self):
        """Only push is gated: view and edit apply to any repository."""
        repo = _show_repo(
            policy={"view": "public", "edit": "admin", "diffusion.push": "users"},
            is_hosted=False,
        )
        record = _showable([repo]).repo_show(["R5"], show_policy=True)["repositories"][
            0
        ]

        assert record["Policy"]["Visible To"] == "Public (No Login Required)"
        assert record["Policy"]["Editable By"] == "Administrators"

    def test_a_policy_phid_is_resolved_to_a_name(self):
        """And named in the spelling --visible-to would take, not
        "Infrastructure": no option accepts a project's display name."""
        repo = _show_repo(
            policy={
                "view": "PHID-PROJ-infra",
                "edit": "admin",
                "diffusion.push": "users",
            }
        )
        diffusion = _showable([repo])
        diffusion.phab.phid.query.return_value = {
            "PHID-PROJ-infra": {
                "type": "PROJ",
                "name": "Infrastructure",
                "uri": "http://phorge.localhost/tag/infrastructure/",
            }
        }

        record = diffusion.repo_show(["R5"], show_policy=True)["repositories"][0]

        assert record["Policy"]["Visible To"] == "#infrastructure"

    def test_a_policy_phid_the_instance_will_not_name_passes_through(self):
        """Inventing a name for a policy is worse than showing the PHID."""
        repo = _show_repo(
            policy={
                "view": "PHID-PROJ-secret",
                "edit": "admin",
                "diffusion.push": "users",
            }
        )
        record = _showable([repo]).repo_show(["R5"], show_policy=True)["repositories"][
            0
        ]

        assert record["Policy"]["Visible To"] == "PHID-PROJ-secret"

    def test_keyword_policies_cost_no_lookup(self):
        """Which is every instance that never named a project in a policy."""
        diffusion = _showable([_show_repo()])

        diffusion.repo_show(["R5"], show_policy=True)

        diffusion.phab.phid.query.assert_not_called()

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

        for section in ("URIs", "Branches", "Tags", "Metadata", "Policy"):
            assert section not in record

    def test_show_uris_describes_each_uri(self):
        repo = _show_repo(uris=[_show_uri("git@github.com:dynamist/phabfive.git")])
        record = _showable([repo]).repo_show(["R5"], show_uris=True)["repositories"][0]

        assert record["URIs"] == [
            {
                "URI": "git@github.com:dynamist/phabfive.git",
                "Origin": "external",
                "Role": "Phorge pulls from here",
                "I/O": {"Raw": "observe", "Default": "none", "Effective": "observe"},
                "Display": {"Raw": "always", "Default": "never", "Effective": "always"},
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


class TestThePolicySectionIsOptIn:
    """Policy joined the `--show-*` family, and pays for itself only when asked.

    Naming a policy that carries a PHID costs a ``phid.query``, and
    ``repo list`` paid it once for a whole instance to render a section most
    callers never asked for. So the gate is in the record builder and it
    covers the resolution too - which is the half that is invisible in the
    output, and so is counted here rather than read.
    """

    def _repo(self, **kwargs):
        """A repository whose view policy names a project, so that naming it
        is a round trip there is something to count."""
        return _show_repo(
            policy={
                "view": "PHID-PROJ-infra",
                "edit": "admin",
                "diffusion.push": "users",
            },
            is_hosted=True,
            **kwargs,
        )

    def test_repo_show_makes_no_phid_query_without_the_flag(self):
        diffusion = _showable([self._repo()])

        diffusion.repo_show(["R5"])

        diffusion.phab.phid.query.assert_not_called()

    def test_repo_show_makes_one_with_the_flag(self):
        diffusion = _showable([self._repo()])

        diffusion.repo_show(["R5"], show_policy=True)

        assert diffusion.phab.phid.query.call_count == 1
        assert diffusion.phab.phid.query.call_args[1]["phids"] == ["PHID-PROJ-infra"]

    def test_repo_list_makes_no_phid_query_without_the_flag(self):
        diffusion = _listing([self._repo()])

        diffusion.repo_list()

        diffusion.phab.phid.query.assert_not_called()

    def test_repo_list_makes_one_with_the_flag(self):
        diffusion = _listing([self._repo()])

        diffusion.repo_list(show_policy=True)

        assert diffusion.phab.phid.query.call_count == 1

    def test_the_section_is_absent_from_a_listing_until_asked_for(self):
        diffusion = _listing([self._repo()])

        assert "Policy" not in diffusion.repo_list()["repositories"][0]
        assert "Policy" in diffusion.repo_list(show_policy=True)["repositories"][0]

    def test_not_asked_and_not_hosted_are_different_answers(self):
        """A repository nobody asked about says nothing about its policies.

        A non-hosted repository that was asked still answers, with the marker
        the Policies management panel prints. Collapsing the two would make a
        missing section read as a hosting fact.
        """
        observed = _show_repo(is_hosted=False)

        silent = _showable([observed]).repo_show(["R5"])["repositories"][0]
        asked = _showable([observed]).repo_show(["R5"], show_policy=True)[
            "repositories"
        ][0]

        assert "Policy" not in silent
        assert asked["Policy"]["Can Push"] == "Not a Hosted Repository"

    @pytest.mark.parametrize("output_format", ["yaml", "json", "jsonl"])
    def test_every_format_agrees_that_it_is_absent(self, capsys, output_format):
        """The gate is in the builder, so no renderer can be the one that
        keeps printing it."""
        from phabfive.diffusion.display import display_repositories

        diffusion = _listing([self._repo()])

        display_repositories(diffusion.repo_list(), output_format, diffusion)

        assert "Policy" not in capsys.readouterr().out

    @pytest.mark.parametrize("output_format", ["yaml", "json", "jsonl"])
    def test_every_format_agrees_that_it_is_present(self, capsys, output_format):
        from phabfive.diffusion.display import display_repositories

        diffusion = _listing([self._repo()])

        display_repositories(
            diffusion.repo_list(show_policy=True), output_format, diffusion
        )

        assert "Can Push" in capsys.readouterr().out

    def test_the_cli_threads_the_flag(self):
        from typer.testing import CliRunner

        from phabfive.cli.diffusion import diffusion_app

        for command, method in (("show", "repo_show"), ("list", "repo_list")):
            for args, expected in (
                ([], False),
                (["--show-policy"], True),
                (["-P"], True),
            ):
                mock_diffusion = MagicMock()
                getattr(mock_diffusion, method).return_value = {
                    "repositories": [],
                    "missing_ids": [],
                }

                with patch(
                    "phabfive.cli.diffusion._get_diffusion_app",
                    return_value=mock_diffusion,
                ):
                    CliRunner().invoke(
                        diffusion_app,
                        [
                            "repo",
                            command,
                            *(["R5"] if command == "show" else []),
                            *args,
                        ],
                    )

                call = getattr(mock_diffusion, method).call_args

                assert call[1]["show_policy"] is expected


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

    def test_a_format_with_no_renderer_falls_back_to_rich(self, capsys):
        # value is a real format, but only passphrase and paste have a bare
        # value to print - diffusion registers no renderer and gets rich.
        assert self._render(capsys, "value").startswith("- Link:")

    def test_an_unknown_format_falls_back_to_rich(self, capsys):
        assert self._render(capsys, "nope").startswith("- Link:")

    @pytest.mark.parametrize(("alias", "resolved"), sorted(FORMAT_ALIASES.items()))
    def test_the_aliases_reach_the_same_renderers(self, capsys, alias, resolved):
        # render_records() resolves through FORMAT_ALIASES rather than its own
        # copy, so an alias cannot dispatch here and nowhere else.
        assert self._render(capsys, alias) == self._render(capsys, resolved)


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
            [
                "R5",
                "--show-branches",
                "--show-tags",
                "--show-uris",
                "--show-metadata",
                "--show-policy",
            ]
        )

        assert diffusion.repo_show.call_args[1] == {
            "show_branches": True,
            "show_tags": True,
            "show_uris": True,
            "show_metadata": True,
            "show_policy": True,
            "show_description": True,
        }

    def test_nothing_is_shown_by_default(self):
        _, diffusion = self._invoke(["R5"])

        assert diffusion.repo_show.call_args[1] == {
            "show_branches": False,
            "show_tags": False,
            "show_uris": False,
            "show_metadata": False,
            "show_policy": False,
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


def _credential_uri(credential_phid="PHID-CDTL-1", **kwargs):
    """A URI bound to a credential, as the uris attachment returns it."""
    uri = _show_uri(kwargs.pop("uri", "git@github.com:dynamist/phabfive.git"), **kwargs)
    uri["fields"]["credentialPHID"] = credential_phid

    return uri


class TestUriListRecord:
    """`uri list` printed bare strings; this is the record it answers with."""

    def test_every_field_the_web_ui_shows(self):
        repo = _repo("myrepo")
        repo["attachments"]["uris"]["uris"] = [_show_uri("git@example.com:x.git")]

        assert _listing([repo]).uri_list("myrepo") == {
            "uris": [
                {
                    "URI": "git@example.com:x.git",
                    "Origin": "external",
                    "Role": "Phorge pulls from here",
                    "I/O": {
                        "Raw": "observe",
                        "Default": "none",
                        "Effective": "observe",
                    },
                    "Display": {
                        "Raw": "always",
                        "Default": "never",
                        "Effective": "always",
                    },
                    "Credential": "(none)",
                    "Disabled": False,
                }
            ]
        }

    def test_the_display_uri_is_what_is_reported(self):
        """The two list commands used to read two different spellings.

        `repo list --url` read uri.effective and `uri list` read
        uri.display, so the same URI could be printed two ways. Every
        caller now reads the display URI, which is the one the web UI
        shows.
        """
        repo = _repo("myrepo")
        repo["attachments"]["uris"]["uris"] = [
            {
                "id": 1,
                "fields": {
                    "uri": {
                        "raw": "git@example.com:x.git",
                        "display": "git@example.com:x.git",
                        "effective": "ssh://git@example.com/x.git",
                    },
                    "io": {"effective": "observe"},
                    "display": {"effective": "always"},
                },
            }
        ]

        [uri] = _listing([repo]).uri_list("myrepo")["uris"]

        assert uri["URI"] == "git@example.com:x.git"

    def test_clone_keeps_only_the_uris_shown_as_clone_uris(self):
        repo = _repo("myrepo")
        repo["attachments"]["uris"]["uris"] = [
            _show_uri("git@example.com:shown.git", display="always"),
            _show_uri("git@example.com:hidden.git", display="never"),
        ]

        listed = _listing([repo])

        assert [uri["URI"] for uri in listed.uri_list("myrepo")["uris"]] == [
            "git@example.com:shown.git",
            "git@example.com:hidden.git",
        ]
        assert [
            uri["URI"] for uri in listed.uri_list("myrepo", clone_only=True)["uris"]
        ] == ["git@example.com:shown.git"]

    def test_a_credential_is_named_by_its_monogram(self):
        repo = _repo("myrepo")
        repo["attachments"]["uris"]["uris"] = [_credential_uri()]
        diffusion = _listing([repo])
        diffusion.phab.phid.query.return_value = {"PHID-CDTL-1": {"name": "K3"}}

        [uri] = diffusion.uri_list("myrepo")["uris"]

        assert uri["Credential"] == "K3"

    def test_the_secret_is_never_fetched(self):
        """Naming a credential is phid.query; passphrase is never asked."""
        repo = _repo("myrepo")
        repo["attachments"]["uris"]["uris"] = [_credential_uri()]
        diffusion = _listing([repo])
        diffusion.passphrase = MagicMock()
        diffusion.phab.phid.query.return_value = {"PHID-CDTL-1": {"name": "K3"}}

        diffusion.uri_list("myrepo")

        diffusion.passphrase.get_secret.assert_not_called()
        diffusion.passphrase.get_credential_record.assert_not_called()

    def test_an_unnameable_credential_falls_back_to_its_phid(self):
        repo = _repo("myrepo")
        repo["attachments"]["uris"]["uris"] = [_credential_uri()]
        diffusion = _listing([repo])
        diffusion.phab.phid.query.side_effect = Exception("lookup failed")

        [uri] = diffusion.uri_list("myrepo")["uris"]

        # Naming it is a convenience; it must never fail the read.
        assert uri["Credential"] == "PHID-CDTL-1"

    def test_one_lookup_per_distinct_credential(self):
        repo = _repo("myrepo")
        repo["attachments"]["uris"]["uris"] = [
            _credential_uri(uri="git@example.com:a.git"),
            _credential_uri(uri="git@example.com:b.git"),
            _credential_uri("PHID-CDTL-2", uri="git@example.com:c.git"),
        ]
        diffusion = _listing([repo])
        diffusion.phab.phid.query.return_value = {}

        diffusion.uri_list("myrepo")

        assert diffusion.phab.phid.query.call_count == 2


class TestUriMatrix:
    """A URI has four independent dimensions, and the record has all four.

    `uri list` reported two of them, flattened to their effective value
    (#375): there was no way to tell a URI Phorge generated from one
    somebody added, and no way to tell a value written on the URI from one
    it inherited.
    """

    def _record(self, **kwargs):
        repo = _repo("myrepo")
        repo["attachments"]["uris"]["uris"] = [
            _show_uri("git@example.com:x.git", **kwargs)
        ]

        [record] = _listing([repo]).uri_list("myrepo")["uris"]

        return record

    def test_a_generated_uri_is_built_in(self):
        assert self._record(builtin="ssh")["Origin"] == "built-in"

    def test_an_added_uri_is_external(self):
        assert self._record()["Origin"] == "external"

    def test_a_record_without_a_builtin_section_is_external(self):
        """Only builtin.protocol says which, and a sparse record has none."""
        from phabfive.diffusion.formatters import uri_origin

        assert uri_origin({"fields": {}}) == "external"

    @pytest.mark.parametrize(
        "io, role",
        [
            ("observe", "Phorge pulls from here"),
            ("mirror", "Phorge pushes here"),
            ("readwrite", "clone + push"),
            ("read", "clone (read-only)"),
            ("none", "not in use"),
        ],
    )
    def test_the_role_answers_what_the_uri_is_for(self, io, role):
        assert self._record(io=io)["Role"] == role

    def test_being_disabled_overrides_the_role(self):
        """A disabled observe URI is not pulled from, whatever io says."""
        record = self._record(io="observe", disabled=True)

        assert record["Role"] == "disabled"
        assert record["Disabled"] is True

    def test_an_io_value_phabfive_does_not_know_is_reported_as_it_is(self):
        """Phorge may grow one; answering with a guess would be worse."""
        assert self._record(io="teleport")["Role"] == "teleport"

    def test_io_is_published_as_all_three_spellings(self):
        assert self._record(io="observe")["I/O"] == {
            "Raw": "observe",
            "Default": "none",
            "Effective": "observe",
        }

    def test_display_is_published_as_all_three_spellings(self):
        assert self._record(display="always")["Display"] == {
            "Raw": "always",
            "Default": "never",
            "Effective": "always",
        }

    def test_an_inherited_value_keeps_what_it_inherited_from(self):
        """effective alone cannot say whether the value was chosen."""
        repo = _repo("myrepo")
        repo["attachments"]["uris"]["uris"] = [
            {
                "id": 1,
                "fields": {
                    "uri": {"display": "http://phorge/source/x.git"},
                    "io": {
                        "raw": "default",
                        "default": "readwrite",
                        "effective": "readwrite",
                    },
                    "display": {
                        "raw": "default",
                        "default": "always",
                        "effective": "always",
                    },
                    "builtin": {"protocol": "http", "identifier": "shortname"},
                },
            }
        ]

        [record] = _listing([repo]).uri_list("myrepo")["uris"]

        assert record["I/O"]["Raw"] == "default"
        assert record["I/O"]["Effective"] == "readwrite"
        assert record["Role"] == "clone + push"

    def test_a_sparse_record_is_answered_rather_than_raising(self):
        """An older instance may not send every key; a read must not fail."""
        repo = _repo("myrepo")
        repo["attachments"]["uris"]["uris"] = [
            {"id": 1, "fields": {"uri": {"display": "git@example.com:x.git"}}}
        ]

        [record] = _listing([repo]).uri_list("myrepo")["uris"]

        assert record["I/O"] == {"Raw": None, "Default": None, "Effective": None}
        assert record["Origin"] == "external"
        assert record["Role"] is None

    def test_the_uri_stays_the_first_field_and_stays_a_scalar(self):
        """Every renderer here takes the first field as the identifying one."""
        record = self._record()

        assert next(iter(record)) == "URI"
        assert isinstance(record["URI"], str)


class TestUriListFilters:
    """`uri list` is queryable, which is what #33 and #32 asked for."""

    def _uris(self, **filters):
        repo = _repo("myrepo")
        repo["attachments"]["uris"]["uris"] = [
            # The one Phorge generated, inheriting both its values.
            {
                "id": 1,
                "fields": {
                    "uri": {"display": "http://phorge/source/x.git"},
                    "io": {
                        "raw": "default",
                        "default": "readwrite",
                        "effective": "readwrite",
                    },
                    "display": {
                        "raw": "default",
                        "default": "always",
                        "effective": "always",
                    },
                    "disabled": False,
                    "builtin": {"protocol": "http", "identifier": "shortname"},
                },
            },
            _show_uri("git@example.com:observed.git", io="observe", display="never"),
            _show_uri("git@example.com:mirrored.git", io="mirror", display="always"),
            _show_uri(
                "git@example.com:off.git", io="none", display="never", disabled=True
            ),
        ]

        return [
            uri["URI"] for uri in _listing([repo]).uri_list("myrepo", **filters)["uris"]
        ]

    def test_no_filter_is_every_uri(self):
        assert len(self._uris()) == 4

    def test_io_keeps_the_uris_that_do_that(self):
        assert self._uris(io="observe") == ["git@example.com:observed.git"]

    def test_io_finds_a_uri_by_what_is_in_force_not_only_what_was_set(self):
        """The built-in URI never had "readwrite" written on it."""
        assert self._uris(io="readwrite") == ["http://phorge/source/x.git"]

    def test_io_default_finds_the_uris_that_inherit_it(self):
        """ "default" is never an effective value, so raw is what answers."""
        assert self._uris(io="default") == ["http://phorge/source/x.git"]

    def test_display_keeps_the_uris_shown_that_way(self):
        """Whether the URI was told to be shown or inherited being shown."""
        assert self._uris(display="always") == [
            "http://phorge/source/x.git",
            "git@example.com:mirrored.git",
        ]

    def test_builtin_keeps_what_phorge_generated(self):
        assert self._uris(builtin=True) == ["http://phorge/source/x.git"]

    def test_external_keeps_what_was_added(self):
        assert self._uris(builtin=False) == [
            "git@example.com:observed.git",
            "git@example.com:mirrored.git",
            "git@example.com:off.git",
        ]

    def test_disabled_keeps_only_the_disabled_ones(self):
        assert self._uris(disabled=True) == ["git@example.com:off.git"]

    def test_enabled_keeps_only_the_rest(self):
        assert "git@example.com:off.git" not in self._uris(disabled=False)

    def test_filters_combine(self):
        assert self._uris(builtin=False, display="always") == [
            "git@example.com:mirrored.git"
        ]

    def test_a_filter_matching_nothing_is_an_empty_result(self):
        assert self._uris(io="read") == []

    def test_a_filtered_out_uri_costs_no_credential_lookup(self):
        """Naming a credential is a round trip per credential."""
        repo = _repo("myrepo")
        repo["attachments"]["uris"]["uris"] = [
            _credential_uri(uri="git@example.com:observed.git", io="observe"),
            _credential_uri(
                "PHID-CDTL-2", uri="git@example.com:mirrored.git", io="mirror"
            ),
        ]
        diffusion = _listing([repo])
        diffusion.phab.phid.query.return_value = {}

        diffusion.uri_list("myrepo", io="observe")

        assert diffusion.phab.phid.query.call_count == 1

    def test_clone_is_the_display_filter_it_has_always_been(self):
        assert self._uris(clone_only=True) == self._uris(display="always")


class TestUriListFilterCli:
    """The options are validated before a request is made."""

    def _invoke(self, argv, uris=None):
        from typer.testing import CliRunner

        from phabfive.cli.diffusion import diffusion_app

        mock_diffusion = MagicMock()
        mock_diffusion.uri_list.return_value = {"uris": uris or []}

        with patch(
            "phabfive.cli.diffusion._get_diffusion_app", return_value=mock_diffusion
        ):
            result = CliRunner().invoke(diffusion_app, argv)

        return result, mock_diffusion

    def test_the_filters_reach_the_lookup(self):
        _, diffusion = self._invoke(
            ["uri", "list", "R5", "--io=observe", "--display=always", "--external"]
        )

        assert diffusion.uri_list.call_args[1] == {
            "clone_only": False,
            "io": "observe",
            "display": "always",
            "builtin": False,
            "disabled": None,
        }

    def test_builtin_and_disabled_are_the_other_half_of_each_pair(self):
        _, diffusion = self._invoke(["uri", "list", "R5", "--builtin", "--disabled"])

        assert diffusion.uri_list.call_args[1]["builtin"] is True
        assert diffusion.uri_list.call_args[1]["disabled"] is True

    def test_enabled_is_false_not_absent(self):
        _, diffusion = self._invoke(["uri", "list", "R5", "--enabled"])

        assert diffusion.uri_list.call_args[1]["disabled"] is False

    def test_an_old_spelling_resolves_to_what_phorge_calls_it(self):
        """--io=never and --display=hidden are what phabfive advertised."""
        _, diffusion = self._invoke(
            ["uri", "list", "R5", "--io=never", "--display=hidden"]
        )

        assert diffusion.uri_list.call_args[1]["io"] == "none"
        assert diffusion.uri_list.call_args[1]["display"] == "never"

    def test_an_io_value_that_is_not_one_is_refused_unsent(self):
        result, diffusion = self._invoke(["uri", "list", "R5", "--io=bogus"])

        assert result.exit_code == 1
        assert "not valid" in result.output
        diffusion.uri_list.assert_not_called()

    def test_a_display_value_that_is_not_one_is_refused_unsent(self):
        result, diffusion = self._invoke(["uri", "list", "R5", "--display=sometimes"])

        assert result.exit_code == 1
        assert "not valid" in result.output
        diffusion.uri_list.assert_not_called()

    def test_the_error_names_the_values_there_are(self):
        from phabfive.constants import IO_URI_VALUES

        result, _ = self._invoke(["uri", "list", "R5", "--io=bogus"])

        for value in IO_URI_VALUES:
            assert f"'{value}'" in result.output

    def test_both_halves_of_a_pair_is_refused(self):
        result, diffusion = self._invoke(
            ["uri", "list", "R5", "--builtin", "--external"]
        )

        assert result.exit_code == 1
        assert "--builtin and --external" in result.output
        diffusion.uri_list.assert_not_called()

    def test_both_halves_of_the_other_pair_is_refused(self):
        result, diffusion = self._invoke(
            ["uri", "list", "R5", "--disabled", "--enabled"]
        )

        assert result.exit_code == 1
        assert "--disabled and --enabled" in result.output
        diffusion.uri_list.assert_not_called()

    def test_a_filter_matching_nothing_is_still_a_success(self):
        result, _ = self._invoke(["uri", "list", "R5", "--io=read"])

        assert result.exit_code == 0
        assert result.output == ""


class TestListFormats:
    """Both list commands honour --format, and the formats agree (#372)."""

    def _repo_records(self):
        repo = _show_repo(uris=[_show_uri("git@github.com:dynamist/phabfive.git")])

        return _listing([repo]), _listing([repo]).repo_list(show_uris=True)

    def _uri_records(self):
        repo = _repo("myrepo")
        repo["attachments"]["uris"]["uris"] = [_show_uri("git@example.com:x.git")]

        return _listing([repo]), _listing([repo]).uri_list("myrepo")

    def _render(self, capsys, which, output_format):
        from phabfive.diffusion.display import display_repositories, display_uris

        if which == "repositories":
            diffusion, result = self._repo_records()
            display_repositories(result, output_format, diffusion)
        else:
            diffusion, result = self._uri_records()
            display_uris(result, output_format, diffusion)

        return capsys.readouterr().out

    @pytest.mark.parametrize("which", ["repositories", "uris"])
    def test_yaml_json_and_jsonl_carry_the_same_record(self, capsys, which):
        import json

        from ruamel.yaml import YAML

        as_yaml = YAML(typ="safe").load(self._render(capsys, which, "yaml"))
        as_json = json.loads(self._render(capsys, which, "json"))
        as_jsonl = [
            json.loads(line)
            for line in self._render(capsys, which, "jsonl").splitlines()
        ]

        assert as_yaml == as_json == as_jsonl

    @pytest.mark.parametrize("which", ["repositories", "uris"])
    def test_rich_is_the_same_record_in_yaml_shape(self, capsys, which):
        import json

        from ruamel.yaml import YAML

        as_rich = YAML(typ="safe").load(self._render(capsys, which, "rich"))

        assert as_rich == json.loads(self._render(capsys, which, "json"))

    @pytest.mark.parametrize("which", ["repositories", "uris"])
    def test_tree_renders_rather_than_falling_back(self, capsys, which):
        """--format is global; a format that quietly falls back is the bug."""
        output = self._render(capsys, which, "tree")

        assert "├──" in output or "└──" in output
        assert not output.startswith("- ")

    @pytest.mark.parametrize("which", ["repositories", "uris"])
    def test_no_internal_key_reaches_the_output(self, capsys, which):
        for output_format in ("rich", "yaml", "json", "jsonl", "tree"):
            assert "_link" not in self._render(capsys, which, output_format)
            assert "_url" not in self._render(capsys, which, output_format)

    def test_a_uri_record_has_no_link_to_lead_with(self, capsys):
        """A URI has no page of its own, so it leads with the URI itself."""
        import json

        record = json.loads(self._render(capsys, "uris", "json"))[0]

        assert list(record)[0] == "URI"
        assert "Link" not in record

    def test_a_repository_record_still_leads_with_its_link(self, capsys):
        import json

        record = json.loads(self._render(capsys, "repositories", "json"))[0]

        assert list(record)[0] == "Link"


class TestRepoListCli:
    """`repo list` ignored --format entirely and printed bare names (#372)."""

    def _invoke(self, args, result=None, side_effect=None):
        from typer.testing import CliRunner

        from phabfive.cli.diffusion import diffusion_app

        mock_diffusion = MagicMock()
        if side_effect is not None:
            mock_diffusion.repo_list.side_effect = side_effect
        else:
            mock_diffusion.repo_list.return_value = (
                result if result is not None else {"repositories": []}
            )

        with patch(
            "phabfive.cli.diffusion._get_diffusion_app", return_value=mock_diffusion
        ):
            return CliRunner().invoke(diffusion_app, ["repo", "list", *args]), (
                mock_diffusion
            )

    RECORD = {
        "_url": "http://phorge.localhost/R5",
        "_link": "http://phorge.localhost/R5",
        "Repository": {"Monogram": "R5", "Name": "phabfive"},
    }

    @pytest.mark.parametrize(
        "status, expected",
        [
            (None, ["active"]),
            ("active", ["active"]),
            ("inactive", ["inactive"]),
            ("all", ["active", "inactive"]),
        ],
    )
    def test_the_status_argument_reaches_the_lookup(self, status, expected):
        _, diffusion = self._invoke([status] if status else [])

        assert diffusion.repo_list.call_args[1]["status"] == expected

    def test_show_uris_reaches_the_lookup(self):
        _, diffusion = self._invoke(["--show-uris"])

        assert diffusion.repo_list.call_args[1]["show_uris"] is True

    def test_nothing_extra_is_shown_by_default(self):
        _, diffusion = self._invoke([])

        assert diffusion.repo_list.call_args[1]["show_uris"] is False

    def test_the_url_alias_still_works_and_says_it_is_deprecated(self):
        result, diffusion = self._invoke(["--url"])

        assert result.exit_code == 0
        assert diffusion.repo_list.call_args[1]["show_uris"] is True
        assert "--url is deprecated" in result.output

    def test_the_url_alias_is_hidden_from_the_help(self):
        result, _ = self._invoke(["--help"])

        assert "--show-uris" in result.output
        assert "--url" not in result.output

    def test_the_records_are_emitted_in_the_format_asked_for(self):
        import json

        from typer.testing import CliRunner

        from phabfive.cli import app

        mock_diffusion = MagicMock()
        mock_diffusion.repo_list.return_value = {"repositories": [self.RECORD]}

        with patch(
            "phabfive.cli.diffusion._get_diffusion_app", return_value=mock_diffusion
        ):
            result = CliRunner().invoke(
                app, ["--format=json", "diffusion", "repo", "list"]
            )

        assert result.exit_code == 0
        assert json.loads(result.output) == [
            {
                "Link": "http://phorge.localhost/R5",
                "Repository": {"Monogram": "R5", "Name": "phabfive"},
            }
        ]

    def test_an_empty_instance_exits_zero(self):
        result, _ = self._invoke([])

        assert result.exit_code == 0

    def test_an_api_failure_is_reported_rather_than_tracebacked(self):
        result, _ = self._invoke(
            [], side_effect=PhabfiveDataException("data is unavailable")
        )

        assert result.exit_code == 1
        assert "data is unavailable" in result.output
        assert "Traceback" not in result.output


class TestUriListCli:
    """`uri list` printed bare strings whatever --format asked for (#372)."""

    def _invoke(self, args, result=None, side_effect=None):
        from typer.testing import CliRunner

        from phabfive.cli.diffusion import diffusion_app

        mock_diffusion = MagicMock()
        if side_effect is not None:
            mock_diffusion.uri_list.side_effect = side_effect
        else:
            mock_diffusion.uri_list.return_value = (
                result if result is not None else {"uris": []}
            )

        with patch(
            "phabfive.cli.diffusion._get_diffusion_app", return_value=mock_diffusion
        ):
            return CliRunner().invoke(diffusion_app, ["uri", "list", *args]), (
                mock_diffusion
            )

    RECORD = {
        "URI": "git@example.com:x.git",
        "I/O": "observe",
        "Display": "always",
        "Credential": "K3",
        "Disabled": False,
    }

    def test_the_repository_reaches_the_lookup(self):
        _, diffusion = self._invoke(["R5"])

        assert diffusion.uri_list.call_args[0][0] == "R5"
        assert diffusion.uri_list.call_args[1] == {
            "clone_only": False,
            "io": None,
            "display": None,
            "builtin": None,
            "disabled": None,
        }

    def test_clone_reaches_the_lookup(self):
        _, diffusion = self._invoke(["R5", "--clone"])

        assert diffusion.uri_list.call_args[1]["clone_only"] is True

    def test_the_records_are_emitted_in_the_format_asked_for(self):
        import json

        from typer.testing import CliRunner

        from phabfive.cli import app

        mock_diffusion = MagicMock()
        mock_diffusion.uri_list.return_value = {"uris": [self.RECORD]}

        with patch(
            "phabfive.cli.diffusion._get_diffusion_app", return_value=mock_diffusion
        ):
            result = CliRunner().invoke(
                app, ["--format=json", "diffusion", "uri", "list", "R5"]
            )

        assert result.exit_code == 0
        assert json.loads(result.output) == [self.RECORD]

    def test_a_repository_without_uris_exits_zero(self):
        result, _ = self._invoke(["R5"])

        assert result.exit_code == 0

    def test_a_missing_repository_is_reported_cleanly(self):
        result, _ = self._invoke(
            ["R9999"], side_effect=PhabfiveDataException("Repository 'R9999' not found")
        )

        assert result.exit_code == 1
        assert "not found" in result.output
        assert "Traceback" not in result.output


class TestTableFormatCli:
    """`--format=table` is list-shaped, and global (#376).

    `repo list` and `uri list` render a grid from the same records every
    other format renders; `repo show` registers no table renderer and so
    falls back to rich, which is what "list-shaped" means in practice.
    """

    REPOS = {
        "repositories": [
            {
                "_url": "http://phorge.localhost/R5",
                "_link": "http://phorge.localhost/R5",
                "Repository": {
                    "Name": "phabfive",
                    "Callsign": None,
                    "Monogram": "R5",
                    "Status": "active",
                    "Default Branch": "master",
                },
                "Policy": {"Visible To": "All Users"},
            },
            {
                "_url": "http://phorge.localhost/R6",
                "_link": "http://phorge.localhost/R6",
                "Repository": {
                    "Name": "unifik",
                    "Callsign": None,
                    "Monogram": "R6",
                    "Status": "active",
                    "Default Branch": "main",
                },
                "Policy": {"Visible To": "All Users"},
            },
        ]
    }

    URIS = {
        "uris": [
            {
                "URI": "ssh://phorge@phorge.localhost/source/phabfive.git",
                "Origin": "built-in",
                "Role": "clone + push",
                "I/O": {
                    "Raw": "default",
                    "Default": "readwrite",
                    "Effective": "readwrite",
                },
                "Display": {
                    "Raw": "default",
                    "Default": "always",
                    "Effective": "always",
                },
                "Disabled": False,
            },
            {
                "URI": "git@github.com:dynamist/phabfive.git",
                "Origin": "external",
                "Role": "Phorge pulls from here",
                "I/O": {"Raw": "observe", "Default": "none", "Effective": "observe"},
                "Display": {"Raw": "never", "Default": "never", "Effective": "never"},
                "Disabled": False,
            },
        ]
    }

    def _invoke(self, argv, **returns):
        from rich.console import Console
        from typer.testing import CliRunner

        from phabfive.cli import app

        mock_diffusion = MagicMock()
        mock_diffusion.get_console.return_value = Console(
            force_terminal=False, no_color=True, width=400
        )

        for name, value in returns.items():
            getattr(mock_diffusion, name).return_value = value

        with patch(
            "phabfive.cli.diffusion._get_diffusion_app", return_value=mock_diffusion
        ):
            result = CliRunner().invoke(app, argv)

        assert result.exit_code == 0, result.output

        return [line.rstrip() for line in result.output.splitlines() if line.strip()]

    def test_repo_list_heads_its_columns_with_the_record_keys(self):
        rows = self._invoke(
            ["--format=table", "diffusion", "repo", "list"], repo_list=self.REPOS
        )

        assert rows[0].split() == [
            "Name",
            "Monogram",
            "Status",
            "Default",
            "Branch",
            "Visible",
            "To",
        ]

    def test_repo_list_writes_one_row_per_repository(self):
        rows = self._invoke(
            ["--format=table", "diffusion", "repo", "list"], repo_list=self.REPOS
        )

        assert len(rows) == 1 + len(self.REPOS["repositories"])
        assert rows[1].startswith("phabfive")
        assert rows[2].startswith("unifik")

    def test_repo_list_leaves_out_the_column_no_repository_filled_in(self):
        rows = self._invoke(
            ["--format=table", "diffusion", "repo", "list"], repo_list=self.REPOS
        )

        assert "Callsign" not in rows[0]

    def test_uri_list_keeps_a_long_uri_whole(self):
        rows = self._invoke(
            ["--format=table", "diffusion", "uri", "list", "R5"], uri_list=self.URIS
        )

        assert "ssh://phorge@phorge.localhost/source/phabfive.git" in rows[1]

    def test_uri_list_gives_the_matrix_one_column_each(self):
        """Six columns of Raw/Default/Effective is what rule 3 is for."""
        rows = self._invoke(
            ["--format=table", "diffusion", "uri", "list", "R5"], uri_list=self.URIS
        )

        assert rows[0].split() == [
            "URI",
            "Origin",
            "Role",
            "I/O",
            "Display",
            "Disabled",
        ]
        assert "readwrite (default)" in rows[1]
        assert "observe (set)" in rows[2]

    def test_uri_list_writes_one_row_per_uri(self):
        rows = self._invoke(
            ["--format=table", "diffusion", "uri", "list", "R5"], uri_list=self.URIS
        )

        assert len(rows) == 1 + len(self.URIS["uris"])

    def test_repo_show_falls_back_to_rich(self):
        rows = self._invoke(
            ["--format=table", "diffusion", "repo", "show", "R5"],
            repo_show={"repositories": self.REPOS["repositories"][:1]},
        )

        assert rows[0] == "- Link: http://phorge.localhost/R5"

    def test_the_table_has_as_many_rows_as_json_has_records(self):
        import json

        table = self._invoke(
            ["--format=table", "diffusion", "repo", "list"], repo_list=self.REPOS
        )
        as_json = self._invoke(
            ["--format=json", "diffusion", "repo", "list"], repo_list=self.REPOS
        )

        assert len(table) - 1 == len(json.loads("\n".join(as_json)))
