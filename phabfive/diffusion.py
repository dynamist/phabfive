# -*- coding: utf-8 -*-

# python std lib
import re

# phabfive imports
from phabfive.constants import MONOGRAMS, REPO_STATUS_CHOICES
from phabfive.core import Phabfive
from phabfive.exceptions import PhabfiveDataException
from phabfive import passphrase

# 3rd party imports
from phabricator import APIError


VCS_CLONE_MAP = {"git": "git clone", "hg": "hg clone", "svn": "svn checkout"}


class Diffusion(Phabfive):
    def __init__(self):
        super(Diffusion, self).__init__()

    # TODO: create_repository() should call edit_repository(), they are using the same conduit
    def create_repository(self, name=None, vcs=None, status=None, observe=False):
        """Phabfive wrapper that connects to Phabricator and creates repositories.

        `vcs` defaults to "git".
        `status` defaults to "active".

        :type name: str
        :type vcs: str
        :type status: str

        :rtype: str
        """
        vcs = vcs if vcs else "git"
        status = status if status else "active"

        repos = self.get_repositories()

        for repo in repos:
            if name in repo["fields"]["name"]:
                if observe == True:
                    return "{0} already exist".format(name)
                else:
                    raise PhabfiveDataException("Name of repository already exist")

        transactions = [
            {"type": "name", "value": name},
            {"type": "shortName", "value": name},
            {"type": "vcs", "value": vcs},
            {"type": "status", "value": status},
        ]

        new_repo = self.phab.diffusion.repository.edit(transactions=transactions)

        return new_repo["object"]["phid"]

    # TODO: edit_repository() should take arguments (str) for transactions and object_identifier (str)
    def edit_repositories(self, names=None, status=None):
        """Phabfive wrapper that connects to Phabricator and edit repositories.

        :type repo_phid: list
        :type status: str

        :rtype: bool
        """
        repos = self.get_repositories()

        for repo in repos:
            for name in names:
                if repo["fields"]["name"] == name:
                    transactions = [{"type": "status", "value": status}]
                    object_identifier = repo["id"]

                    self.phab.diffusion.repository.edit(
                        transactions=transactions, objectIdentifier=object_identifier
                    )
        # TODO: Choose a suitable return when the function is being implemented in cli.py
        return True

    def edit_uri(self, uri=None, io=None, display=None, object_identifier=None):
        """Phabfive wrapper that connects to Phabricator and edit uri.

        :type uri: str
        :type io: str
        :type display: str
        :type object_identifier: str

        :rtype: bool
        """
        transactions = [
            {"type": "uri", "value": uri},
            {"type": "io", "value": io},
            {"type": "display", "value": display},
        ]
        # object_identifier is neccessary when editing an exisiting URI but leave blank when creating new URI
        self.phab.diffusion.uri.edit(
            transactions=transactions, objectIdentifier=object_identifier
        )
        # TODO: Choose a suitable return when the function is being implemented in cli.py
        return True

    def observe_repositories(self, credential=None, urls=None):
        """Phabfive wrapper that connects to Phabricator and observes repositories.

        :type credential: str
        :type urls: list

        :rtype: list
        """
        observed_repos_url = []
        created_repos_name = []
        credential_phid = ""
        get_credential = passphrase.Passphrase().get_secret(ids=credential)

        # Validate type of credential
        for key in get_credential:
            if "PHID" in key:
                credential_phid = key  # For use further down to create new uri
                credential_type = get_credential.get(key).get("type")
                if credential_type not in (
                    "ssh-generated-key",
                    "ssh-key-text",
                    "token",
                ):
                    raise PhabfiveDataException(
                        "{0} is not type of ssh-generated-key, ssh-key-text or token.".format(
                            credential
                        )
                    )

        for url in urls:
            split_url = url.split("/")
            repo_name = split_url[-1].split(".")[0]
            created_repo_phid = self.create_repository(
                name=repo_name, status="inactive", observe=True
            )
            if "already exist" in created_repo_phid:
                observed_repos_url.append(created_repo_phid)
                continue

            repos = self.get_repositories(attachments={"uris": "--url"})

            for repo in repos:
                uris = repo["attachments"]["uris"]["uris"]
                get_repo_phid = uris[0]["fields"]["repositoryPHID"]
                if get_repo_phid == created_repo_phid:
                    # Identify one of newly created repository's uri
                    uri_diffusion = uris[0]["fields"]["uri"]["display"]
                    object_identifier = uris[0]["id"]

                    self.edit_uri(
                        uri=uri_diffusion,
                        io="read",
                        display="never",
                        object_identifier=object_identifier,
                    )

                    uri_source = uris[1]["fields"]["uri"]["display"]
                    object_identifier = uris[1]["id"]

                    self.edit_uri(
                        uri=uri_source,
                        io="read",
                        display="always",
                        object_identifier=object_identifier,
                    )

                    observed_repos_url.append(repo)

            transactions = [
                {"type": "repository", "value": created_repo_phid},
                {"type": "uri", "value": url},
                {"type": "io", "value": "observe"},
                {"type": "display", "value": "never"},
                {"type": "credential", "value": credential_phid},
            ]

            self.phab.diffusion.uri.edit(transactions=transactions)

            created_repos_name.append(repo_name)

        self.edit_repositories(names=created_repos_name, status="active")

        return observed_repos_url

    def print_observed_repositories(self, credential=None, urls=None):
        """Method used by the Phabfive CLI."""

        observed_repositories = self.observe_repositories(
            credential=credential, urls=urls
        )

        for repo in observed_repositories:
            if "already exist" in repo:
                print(repo)
            else:
                created_url = repo["attachments"]["uris"]["uris"][1]["fields"]["uri"]["display"]
                print("{0}".format(created_url))

    def print_created_repository_url(self, name=None):
        """Method used by the Phabfive CLI."""
        created_repo_phid = self.create_repository(name)

        repos = self.get_repositories(attachments={"uris": "--url"})

        for repo in repos:
            uris = repo["attachments"]["uris"]["uris"]
            get_repo_phid = uris[0]["fields"]["repositoryPHID"]
            if get_repo_phid == created_repo_phid:
                print(
                    "{0} {1}".format(
                        VCS_CLONE_MAP[repo["fields"]["vcs"]],
                        uris[0]["fields"]["uri"]["effective"],
                    )
                )

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
                raise PhabfiveDataException(e)
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
        """Method used by the Phabfive CLI."""
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
        """Method used by the Phabfive CLI."""
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
