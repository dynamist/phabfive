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

    def create_uri(
        self, repository_name=None, new_uri=None, io=None, display=None, credential=None
    ):
        """Phabfive wrapper that connects to Phabricator and create uri.

        :type repository_name: str
        :type uri: str
        :type io: str
        :type display: str
        :type credential: str

        :rtype: str
        """
        # For use in transaction further down to create new uri
        repository_phid = ""
        credential_phid = ""
        # Assume that repository_name does not yet exist
        repository_exist = False

        repos = self.get_repositories(attachments={"uris": "--url"})

        if self._validate_identifier(repository_name):
            repository_id = repository_name.replace("R", "")

            for repo in repos:
                exisiting_repo_id = repo["id"]
                if int(repository_id) == exisiting_repo_id:
                    repository_name = repo["fields"]["shortName"]

        get_credential = passphrase.Passphrase().get_secret(ids=credential)

        self._validate_credential_type(credential=get_credential)
        #TODO: Validate repos existent - create its own function
        for repo in repos:
            name = repo["fields"]["shortName"]
            # Repo exist. Edit its existing uris, setting I/O - Read Only, Display - Hidden
            if repository_name == name:
                repository_exist = True
                print("'{0}' exist".format(repository_name))
                # Existing repo PHID. Will be used further down to create new uri
                repository_phid = repo["phid"]
                # Amount of uris the repo has
                uris = repo["attachments"]["uris"]["uris"]

                for i in range(len(uris)):
                    uri = uris[i]["fields"]["uri"]["display"]
                    object_identifier = uris[i]["id"]

                    # Changing settings: I/O - Read Only(read), Display - Hidden(never)
                    self.edit_uri(
                        uri=uri,
                        io="read",
                        display="never",
                        object_identifier=object_identifier,
                    )

        # Repo does not exist. Create new repo (inactive), edit its uris, setting I/O - Read Only, Display - Hidden
        if repository_exist == False:
            print("'{0}' created".format(repository_name))

            repository_phid = self.create_repository(
                name=repository_name, status="inactive", observe=True
            )

            repos = self.get_repositories(attachments={"uris": "--url"})

            for repo in repos:
                # Amount of uris the repo has
                uris = repo["attachments"]["uris"]["uris"]
                get_repo_phid = uris[0]["fields"]["repositoryPHID"]

                if get_repo_phid == repository_phid:
                    for i in range(len(uris)):
                        uri = uris[i]["fields"]["uri"]["display"]
                        object_identifier = uris[i]["id"]
                        self.edit_uri(
                            uri=uri,
                            io="read",
                            display="never",
                            object_identifier=object_identifier,
                        )

            # TODO: change edit_repositoies to edit_repository
            created_repos_name = []
            created_repos_name.append(repository_name)
            # Activate repository
            self.edit_repositories(names=created_repos_name, status="active")
        # Create new uri
        # TODO: The choices of value of display are default, always, never. May be implemented
        value = "always"
        io = io if io else "default"

        transactions = [
            {"type": "repository", "value": repository_phid},
            {"type": "uri", "value": new_uri},
            {"type": "io", "value": io},  # observe repository
            {"type": "display", "value": value},
            {"type": "credential", "value": credential_phid},
        ]
        try:
            self.phab.diffusion.uri.edit(transactions=transactions)
        except APIError as e:
            raise PhabfiveDataException(e.message)

        return new_uri

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
        try:
            # object_identifier is neccessary when editing an exisiting URI but leave blank when creating new URI
            self.phab.diffusion.uri.edit(
                transactions=transactions, objectIdentifier=object_identifier
            )
        except APIError:
            pass
            # TODO: The APIError raises an I/O error which is not "correct"
            # due to different setting for uri depending on if it is default uri or observed/mirrored uri
        return True
        # TODO: Choose a suitable return when the function is being called in cli.py

    def print_uri(
        self, repository_name=None, new_uri=None, io=None, display=None, credential=None
    ):
        """Method used by the Phabfive CLI."""
        created_uri = self.create_uri(
            repository_name=repository_name,
            new_uri=new_uri,
            io=io,
            display=display,
            credential=credential,
        )

        print("NEW URI: {0}".format(created_uri))

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
