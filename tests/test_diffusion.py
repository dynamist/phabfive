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

    def test_uri_list_is_empty(self):
        assert fetch_uris(_phab_with_repos([]), repo_id="myrepo") == []

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
