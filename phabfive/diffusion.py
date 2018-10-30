# -*- coding: utf-8 -*-

# python std lib
import re

# phabfive imports
from phabfive.constants import MONOGRAMS, REPO_STATUS_CHOICES
from phabfive.core import Phabfive
from phabfive.exceptions import PhabfiveDataException

# 3rd party imports
from phabricator import APIError


class Diffusion(Phabfive):
    def __init__(self):
        super(Diffusion, self).__init__()

    def create_repository(self, name=None, vcs=None, status=None):
        """Phabfive wrapper that connects to Phabricator and creates repositories.

        :type name: str

        :rtype: unicode
        """
        repos = self.get_repositories()

        transactions = [
            {"type": "name", "value": name},
            {"type": "vcs", "value": "git"},
            {"type": "status", "value": "active"},
        ]

        for repo in repos:
            if name in repo["fields"]["name"]:
                raise PhabfiveDataException("Name of repository already exist")

        new_repo = self.phab.diffusion.repository.edit(transactions=transactions)

        return new_repo["object"]["phid"]

    def print_created_repository_url(self, name=None):
        """Method used by the Phabfive CLI.
        """
        created_repo_phid = self.create_repository(name)

        repos = self.get_repositories(attachments={"uris": "--url"})

        for repo in repos:
            uris = repo["attachments"]["uris"]["uris"]
            get_repo_phid = uris[0]["fields"]["repositoryPHID"]

            if get_repo_phid == created_repo_phid:
                print("git clone {}".format(uris[0]["fields"]["uri"]["effective"]))

    def get_repositories(self, query_key=None, attachments=None, constraints=None):
        """Phabfive wrapper that connects to Phabricator and retrieves information
        about repositories.

        `query_key` defaults to "all".

        :type query_key: str
        :type attachments: dict
        :type constraints: dict

        :rtype: dict
        """
        query_key = "all" if not query_key else query_key
        attachments = {} if not attachments else attachments
        constraints = {} if not constraints else constraints
        response = self.phab.diffusion.repository.search(
            queryKey=query_key, attachments=attachments, constraints=constraints
        )

        repositories = response.get("data", {})

        return repositories

    def get_branches(self, repo_id=None, repo_callsign=None, repo_shortname=None):
        """Wrapper that connects to Phabricator and retrieves information about branches
        for a specified repository.

        :type repo_id: str
        :type repo_callsign: str
        :type repo_shortname: str

        :rtype: dict or None
        """
        if repo_id:
            try:
                return self.phab.diffusion.branchquery(repository=repo_id)
            except APIError as e:
                raise PhabfiveDataException(e.message)
        elif repo_callsign:
            # TODO: probably catch APIError here as well
            return self.phab.diffusion.branchquery(callsign=repo_callsign)
        else:
            resolved = self._resolve_shortname_to_id(repo_shortname)
            if resolved:
                return self.phab.diffusion.branchquery(repository=resolved)
            else:
                raise PhabfiveDataException(
                    'Repository "{0}" is not a valid repository.'.format(repo_shortname)
                )

    def print_repositories(self, status=None, url=False):
        """Method used by the Phabfive CLI.
        """
        status = REPO_STATUS_CHOICES if not status else status

        repos = self.get_repositories(attachments={"uris": url})

        if not repos:
            raise PhabfiveDataException("No data or other error.")

        # filter based on active or inactive status
        repos = [repo for repo in repos if repo["fields"]["status"] in status]

        # sort based on name
        repos = sorted(repos, key=lambda key: key["fields"]["name"])

        if url:
            for repo in repos:
                uris = repo["attachments"]["uris"]["uris"]

                # filter based on visibility
                repo_urls = [
                    uri["fields"]["uri"]["effective"]
                    for uri in uris
                    if uri["fields"]["display"]["effective"] == "always"
                ]

                print(", ".join(repo_urls))
        else:
            for repo in repos:
                repo_name = repo["fields"].get("name", "")
                print(repo_name)

    def print_branches(self, repo):
        """Method used by the Phabfive CLI.
        """
        if self._validate_identifier(repo):
            repo = repo.replace("R", "")
            branches = self.get_branches(repo_id=repo)
        else:
            branches = self.get_branches(repo_shortname=repo)

        branch_names = sorted(
            [
                branch["shortName"]
                for branch in branches
                if branch["refType"] == "branch"
            ]
        )

        for branch_name in branch_names:
            print(branch_name)

    def _validate_identifier(self, repo_id):
        return re.match("^" + MONOGRAMS["diffusion"] + "$", repo_id)

    def _resolve_shortname_to_id(self, shortname):
        repos = self.get_repositories()

        repo_ids = [
            repo["id"] for repo in repos if repo["fields"]["shortName"] == shortname
        ]

        return repo_ids[0] if repo_ids else None
