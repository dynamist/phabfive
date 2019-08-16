# -*- coding: utf-8 -*-

# python std lib
import re

# phabfive imports
from phabfive.constants import MONOGRAMS, REPO_STATUS_CHOICES, Display, Io, Vcs
from phabfive.core import Phabfive
from phabfive.exceptions import PhabfiveDataException
from phabfive import passphrase

# 3rd party imports
from phabricator import APIError


VCS_CLONE_MAP = {Vcs.GIT: "git clone", Vcs.HG: "hg clone", Vcs.SVN: "svn checkout"}


class Diffusion(Phabfive):
    def __init__(self):
        super(Diffusion, self).__init__()
        self.passphrase = passphrase.Passphrase()

    def _validate_identifier(self, repo_id):
        return re.match("^" + MONOGRAMS["diffusion"] + "$", repo_id)

    def _validate_credential_type(self, credential):
        for key in credential:
            if "PHID" in key:
                credential_phid = key
                credential_type = credential.get(key).get("type")
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

    # TODO: create_repository() should call edit_repository(), they are using the same conduit
    def create_repository(self, name=None, vcs=None, status=None):
        """Phabfive wrapper that connects to Phabricator and creates repositories.

        `vcs` defaults to "git".
        `status` defaults to "active".

        :type name: str
        :type vcs: str
        :type status: str

        :rtype: str
        """
        vcs = vcs if vcs else Vcs.GIT
        status = status if status else Status.ACTIVE

        repos = self.get_repositories()

        if not repos:
            raise PhabfiveDataException("No data or other error.")

        for repo in repos:
            if name in repo["fields"]["name"]:
                raise PhabfiveDataException(
                    "Repository {0} already exists.".format(name)
                )

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

        repos = self.get_repositories(attachments={"uris": True})

        if not repos:
            raise PhabfiveDataException("No data or other error.")

        if self._validate_identifier(repository_name):
            repository_id = repository_name.replace("R", "")

            for repo in repos:
                exisiting_repo_id = repo["id"]
                if int(repository_id) == exisiting_repo_id:
                    repository_name = repo["fields"]["shortName"]

        # TODO: error handling, catch exception?
        get_credential = self.passphrase.get_secret(ids=credential)

        self._validate_credential_type(credential=get_credential)
        # TODO: Validate repos existent - create its own function
        for repo in repos:
            name = repo["fields"]["shortName"]
            # Repo exist. Edit its existing uris, setting I/O - Read Only, Display - Hidden
            if repository_name == name:
                repository_exist = True
                # TODO: never print in lib; if it exists then do nothing
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
                        io=Io.READ,
                        display=Display.NEVER,
                        object_identifier=object_identifier,
                    )
        # Repo does not exist. Return exit code 1
        if not repository_exist:
            print("'{0}' does not exist".format(repository_name))
            return exit(1)
            # TODO: raise an exception and let CLI handle print and exit

        # TODO: assert that display and io is any of the enum values
        display = Display.ALWAYS
        io = io if io else Io.DEFAULT

        transactions = [
            {"type": "repository", "value": repository_phid},
            {"type": "uri", "value": new_uri},
            {"type": "io", "value": io},
            {"type": "display", "value": display},
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

    # TODO: add support for repo_shortname, etc, see get_branches()
    # TODO: add support for handling active vs inactive repos
    # TODO: add support for handling hidden URIs
    def get_uris(self, repo_id=None):
        """Phabfive wrapper that connects to Phabricator and list uri for specific repository.

        :type repo_id: str

        :rtype: dict
        """
        ret = {}
        repos = self.get_repositories(attachments={"uris": True})

        if not repos:
            raise PhabfiveDataException("No data or other error.")

        for repo in repos:
            if repo_id == repo["fields"]["shortName"]:
                ret = repo

        return ret["attachments"]["uris"]["uris"]

    # TODO: the URIs should be sorted when printed
    def print_uri(self, repo):
        """Method used by the Phabfive CLI."""

        if self._validate_identifier(repo):
            repo = repo.replace("R", "")
            uris = self.get_uris(repo_id=repo)
        else:
            uris = self.get_uris(repo_id=repo)

        for i, item in enumerate(uris):
            print(uris[i]["fields"]["uri"]["display"])

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
                    if uri["fields"]["display"]["effective"] == Display.ALWAYS
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

    def _resolve_shortname_to_id(self, shortname):
        repos = self.get_repositories()

        repo_ids = [
            repo["id"] for repo in repos if repo["fields"]["shortName"] == shortname
        ]

        return repo_ids[0] if repo_ids else None
