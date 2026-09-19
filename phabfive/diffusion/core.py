# -*- coding: utf-8 -*-

"""Main Diffusion class that orchestrates all submodules."""

import logging

from phabricator import APIError

from phabfive import passphrase
from phabfive.constants import IO_NEW_URI_CHOICES, DISPLAY_CHOICES
from phabfive.core import Phabfive
from phabfive.diffusion.fetchers import fetch_branches, fetch_repositories, fetch_uris
from phabfive.diffusion.formatters import (
    format_branches,
    format_repositories,
    format_uris,
)
from phabfive.diffusion.resolvers import (
    resolve_object_identifier,
    resolve_shortname_to_id,
    resolve_uri_record,
)
from phabfive.diffusion.validators import (
    validate_credential_type,
    validate_repo_identifier,
)
from phabfive.exceptions import PhabfiveConfigException, PhabfiveDataException

log = logging.getLogger(__name__)


class Diffusion(Phabfive):
    def __init__(self):
        super(Diffusion, self).__init__()
        self.passphrase = passphrase.Passphrase()

    # Wrapper methods that delegate to submodules while maintaining self.phab access

    def _validate_identifier(self, repo_id):
        """Validate repository identifier format."""
        return validate_repo_identifier(repo_id)

    def _validate_credential_type(self, credential):
        """Validate credential type and extract PHID."""
        return validate_credential_type(credential)

    def _resolve_shortname_to_id(self, shortname):
        """Resolve repository shortname to ID."""
        return resolve_shortname_to_id(self.phab, shortname)

    def get_object_identifier(self, repo_name=None, uri_name=None):
        """
        Identify repository or URI object identifier.

        Parameters
        ----------
        repo_name : str, optional
            Repository name to look up
        uri_name : str, optional
            URI name to look up (requires repo_name)

        Returns
        -------
        str
            Object identifier for the repository or URI
        """
        return resolve_object_identifier(self.phab, repo_name, uri_name)

    def get_repositories(self, query_key=None, attachments=None, constraints=None):
        """
        Connect to Phabricator and retrieve information about repositories.

        Parameters
        ----------
        query_key : str, optional
            Query key, defaults to "all"
        attachments : dict, optional
            Attachments to include
        constraints : dict, optional
            Search constraints

        Returns
        -------
        list
            List of repository data dicts
        """
        return fetch_repositories(self.phab, query_key, attachments, constraints)

    def get_branches(self, repo_id=None, repo_callsign=None, repo_shortname=None):
        """
        Connect to Phabricator and retrieve branches for a repository.

        Parameters
        ----------
        repo_id : str, optional
            Repository ID
        repo_callsign : str, optional
            Repository callsign
        repo_shortname : str, optional
            Repository short name

        Returns
        -------
        list
            List of branch data
        """
        return fetch_branches(self.phab, repo_id, repo_callsign, repo_shortname)

    def get_uris(self, repo_id=None, clone_uri=None):
        """
        Connect to Phabricator and list URIs for a specific repository.

        Parameters
        ----------
        repo_id : str
            Repository ID or short name
        clone_uri : bool, optional
            If True, only return clone URIs

        Returns
        -------
        list
            List of URI strings
        """
        clone_uri = clone_uri if clone_uri else False
        return fetch_uris(self.phab, repo_id, clone_uri)

    def get_uris_formatted(self, repo, clone_uri=False):
        """Return list of URI strings for a repository."""
        return format_uris(self.phab, repo, clone_uri)

    def get_repositories_formatted(self, status=None, include_url=False):
        """Return list of repository dicts with 'name' and optionally 'urls' keys."""
        return format_repositories(self.phab, status, include_url)

    def get_branches_formatted(self, repo):
        """Return sorted list of branch names for a repository."""
        return format_branches(self.phab, repo)

    # Core operations that remain in the main class

    def create_repository(self, name=None, vcs=None, status=None):
        """
        Create a new repository in Phabricator.

        Parameters
        ----------
        name : str
            Repository name
        vcs : str, optional
            Version control system, defaults to "git"
        status : str, optional
            Repository status, defaults to "active"

        Returns
        -------
        str
            PHID of the new repository

        Raises
        ------
        PhabfiveDataException
            If repository already exists or API error
        """
        vcs = vcs or "git"
        status = status or "active"

        repos = self.get_repositories()

        for repo in repos:
            if name in (repo["fields"]["name"], repo["fields"]["shortName"]):
                raise PhabfiveDataException(f"Repository {name} already exists")

        transactions = self.to_transactions(
            {
                "name": name,
                "shortName": name,
                "vcs": vcs,
                "status": status,
            }
        )

        new_repo = self.phab.diffusion.repository.edit(
            transactions=transactions,
        )

        return new_repo["object"]["phid"]

    def edit_repositories(self, names=None, status=None):
        """
        Edit repositories in Phabricator.

        Parameters
        ----------
        names : list
            List of repository names to edit
        status : str
            New status value

        Returns
        -------
        bool
            True on success
        """
        repos = self.get_repositories()

        for repo in repos:
            for name in names:
                if repo["fields"]["name"] == name:
                    transactions = self.to_transactions(
                        {"status": status},
                    )

                    self.phab.diffusion.repository.edit(
                        transactions=transactions,
                        objectIdentifier=repo["id"],
                    )

        return True

    def create_uri(
        self, repository_name=None, new_uri=None, io=None, display=None, credential=None
    ):
        """
        Create a new URI for a repository.

        Parameters
        ----------
        repository_name : str
            Repository name or ID
        new_uri : str
            URI to create
        io : str, optional
            I/O mode, defaults to "default"
        display : str, optional
            Display mode, defaults to "always"
        credential : str
            Credential ID (e.g., "K123")

        Returns
        -------
        str
            The created URI

        Raises
        ------
        PhabfiveConfigException
            If io or display values are invalid
        PhabfiveDataException
            If repository does not exist or API error
        """
        repository_phid = ""
        repository_exist = False

        io = io or "default"
        display = display or "always"

        if io not in IO_NEW_URI_CHOICES:
            raise PhabfiveConfigException(
                f"'{io}' is not valid. Valid IO values are 'default', 'observe', 'mirror' or 'never'"
            )

        if display not in DISPLAY_CHOICES:
            raise PhabfiveConfigException(
                f"'{display}' is not valid. Valid Display values are 'default', 'always' or 'hidden'"
            )

        repos = self.get_repositories(attachments={"uris": True})

        # Check if input of repository_name is an id
        if self._validate_identifier(repository_name):
            repository_id = repository_name.replace("R", "")

            for repo in repos:
                existing_repo_id = repo["id"]

                if int(repository_id) == existing_repo_id:
                    repository_name = repo["fields"]["shortName"]

        get_credential = self.passphrase.get_secret(ids=credential)
        credential_phid = self._validate_credential_type(credential=get_credential)

        for repo in repos:
            name = repo["fields"]["shortName"]

            if repository_name == name:
                display_off = "never"
                io_read_only = "read"
                repository_exist = True
                log.info(f"Repository '{repository_name}' exists, updating URIs")
                repository_phid = repo["phid"]
                uris = repo["attachments"]["uris"]["uris"]

                for i in range(len(uris)):
                    uri = uris[i]["fields"]["uri"]["display"]
                    object_identifier = uris[i]["id"]
                    self.edit_uri(
                        uri=uri,
                        io=io_read_only,
                        display=display_off,
                        object_identifier=object_identifier,
                    )

        if not repository_exist:
            raise PhabfiveDataException(
                f"'{repository_name}' does not exist. Please create a new repository"
            )

        transactions = self.to_transactions(
            {
                "repository": repository_phid,
                "uri": new_uri,
                "io": io,
                "display": display,
                "credential": credential_phid,
            }
        )

        try:
            self.phab.diffusion.uri.edit(transactions=transactions)
        except APIError as e:
            raise PhabfiveDataException(str(e))

        return new_uri

    def get_uri_record(self, repo_name, uri_name):
        """Fetch one repository URI in full.

        Parameters
        ----------
        repo_name : str
            Repository short name
        uri_name : str
            URI as displayed

        Returns
        -------
        dict
            The URI object
        """
        return resolve_uri_record(self.phab, repo_name, uri_name)

    def _describe_credential(self, credential_phid):
        """Name a credential for display, never revealing its secret.

        Parameters
        ----------
        credential_phid : str or None
            PHID of the currently bound credential

        Returns
        -------
        str
            The credential's monogram, its PHID, or "(none)"
        """
        if not credential_phid:
            return "(none)"

        try:
            found = self.phab.phid.query(phids=[credential_phid])
            return found[credential_phid].get("name") or credential_phid
        except Exception:
            # Naming it is a convenience; never fail an edit over it.
            return credential_phid

    def build_uri_edit(
        self,
        uri_record,
        uri=None,
        io=None,
        display=None,
        credential=None,
        disable=None,
    ):
        """Compute the transactions for a URI edit, without applying them.

        Parameters
        ----------
        uri_record : dict
            The URI as it stands, from get_uri_record
        uri : str, optional
            New URI value
        io : str, optional
            New I/O mode
        display : str, optional
            New display mode
        credential : str, optional
            Credential monogram (e.g. "K2")
        disable : bool, optional
            Whether to disable the URI

        Returns
        -------
        tuple
            (transactions, changes) - the Conduit transactions to apply and a
            human-readable description of each one. The credential is named by
            its monogram; its secret never reaches the description.

        Raises
        ------
        PhabfiveDataException
            If the credential is missing or of an unusable type
        """
        fields = uri_record.get("fields", {})

        credential_phid = None
        if credential:
            secret = self.passphrase.get_secret(credential)
            credential_phid = self._validate_credential_type(credential=secret)

        candidates = [
            ("uri", uri, "URI", fields.get("uri", {}).get("display"), None),
            ("io", io, "I/O", fields.get("io", {}).get("raw"), None),
            ("display", display, "Display", fields.get("display", {}).get("raw"), None),
            ("disable", disable, "Disabled", fields.get("disabled"), None),
            # The credential travels as a PHID but is shown by monogram, so its
            # secret never reaches the description of the change.
            (
                "credential",
                credential_phid,
                "Credential",
                fields.get("credentialPHID"),
                credential,
            ),
        ]

        transactions = []
        changes = []

        for kind, value, label, current, shown_new in candidates:
            if value is None or current == value:
                # Not asked for, or already at the target value.
                continue

            transactions.append({"type": kind, "value": value})

            if kind == "credential":
                old_shown = self._describe_credential(current)
            else:
                old_shown = "(none)" if current is None else str(current)

            changes.append(
                {
                    "field": label,
                    "old": old_shown,
                    "new": str(shown_new if shown_new is not None else value),
                }
            )

        return transactions, changes

    def apply_uri_edit(self, object_identifier, transactions):
        """Send prepared transactions to Diffusion.

        Parameters
        ----------
        object_identifier : str
            URI object identifier
        transactions : list
            Transactions from build_uri_edit

        Raises
        ------
        PhabfiveDataException
            If the API rejects the edit
        """
        try:
            self.phab.diffusion.uri.edit(
                transactions=transactions, objectIdentifier=object_identifier
            )
        except APIError:
            raise PhabfiveDataException("No valid input or other error")

    def edit_uri(
        self,
        uri=None,
        io=None,
        display=None,
        credential=None,
        disable=None,
        object_identifier=None,
        uri_record=None,
        dry_run=False,
    ):
        """
        Edit an existing URI.

        Thin composition of build_uri_edit and the Conduit call, so a caller
        that wants to show the change before making it can stop in between.

        Parameters
        ----------
        uri : str, optional
            URI value
        io : str, optional
            I/O mode
        display : str, optional
            Display mode
        credential : str, optional
            Credential monogram (e.g. "K2")
        disable : bool, optional
            Disable the URI
        object_identifier : str, optional
            URI object identifier
        uri_record : dict, optional
            The URI as it stands; only needed to describe the change
        dry_run : bool
            Show the change without making it

        Returns
        -------
        dict
            {'changes': [...], 'dry_run': bool}

        Raises
        ------
        PhabfiveDataException
            If API error occurs
        """
        transactions, changes = self.build_uri_edit(
            uri_record or {},
            uri=uri,
            io=io,
            display=display,
            credential=credential,
            disable=disable,
        )

        if not transactions:
            return {"changes": [], "dry_run": dry_run}

        if dry_run:
            return {"changes": changes, "dry_run": True}

        self.apply_uri_edit(object_identifier, transactions)

        return {"changes": changes, "dry_run": False}
