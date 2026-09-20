# -*- coding: utf-8 -*-

"""Main Diffusion class that orchestrates all submodules."""

import logging

from phabricator import APIError

from phabfive import passphrase
from phabfive.constants import IO_NEW_URI_CHOICES
from phabfive.core import Phabfive
from phabfive.diffusion.fetchers import (
    fetch_branches,
    fetch_refs,
    fetch_repositories,
    fetch_uris,
    demotion_io,
    find_repository,
    match_repository,
)
from phabfive.diffusion.formatters import (
    build_repository_display_data,
    format_refs,
    format_repositories,
    format_uris,
    ref_names,
)
from phabfive.diffusion.resolvers import (
    resolve_object_identifier,
    resolve_shortname_to_id,
    resolve_uri_and_repo,
    resolve_uri_record,
)
from phabfive.diffusion.validators import (
    resolve_display_value,
    resolve_io_value,
    validate_credential_type,
    validate_repo_identifier,
)
from phabfive.exceptions import PhabfiveDataException

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
        return format_refs(self.phab, repo, "branch")

    def get_tags_formatted(self, repo):
        """Return sorted list of tag names for a repository."""
        return format_refs(self.phab, repo, "tag")

    def _resolve_spaces(self, repos):
        """Name the spaces a set of repositories live in.

        Parameters
        ----------
        repos : list
            Repository records

        Returns
        -------
        dict
            Space PHID to its name. Empty when nothing is in a space, which
            is every instance that never turned Spaces on.
        """
        phids = {
            repo.get("fields", {}).get("spacePHID")
            for repo in repos
            if repo.get("fields", {}).get("spacePHID")
        }

        if not phids:
            return {}

        try:
            found = self.phab.phid.query(phids=list(phids))
        except Exception as e:
            # Naming the space is a convenience; never fail a read over it.
            log.warning(f"Failed to resolve space PHIDs: {e}")
            return {}

        return {
            phid: data.get("fullName") or data.get("name") or phid
            for phid, data in found.items()
        }

    def repo_show(
        self,
        repo_ids,
        show_branches=False,
        show_tags=False,
        show_uris=False,
        show_metadata=False,
        show_description=True,
    ):
        """
        Show one or more repositories in full.

        Modelled on Maniphest's task_show: one lookup for all of them, the
        records returned in the order they were asked for, and the ones that
        do not exist reported back so the caller can exit non-zero.

        Parameters
        ----------
        repo_ids : list[str]
            Repository monograms, callsigns or short names
        show_branches : bool, optional
            Include the repository's branches
        show_tags : bool, optional
            Include the repository's tags
        show_uris : bool, optional
            Include the repository's URIs
        show_metadata : bool, optional
            Include PHIDs and timestamps
        show_description : bool, optional
            Include the description

        Returns
        -------
        dict
            {"repositories": [...], "missing_ids": [...]}

        Raises
        ------
        PhabfiveDataException
            If the API refuses a branch or tag query
        """
        repos = fetch_repositories(self.phab, attachments={"uris": True})

        found = []
        missing_ids = []

        for repo_id in repo_ids:
            match = match_repository(repos, repo_id)

            if match is None:
                log.error(f"Repository '{repo_id}' not found")
                missing_ids.append(repo_id)
            else:
                found.append(match)

        branches_map = {}
        tags_map = {}

        for repo in found:
            if show_branches:
                branches_map[repo["id"]] = ref_names(
                    fetch_refs(self.phab, repo["id"], "branch"), "branch"
                )
            if show_tags:
                tags_map[repo["id"]] = ref_names(
                    fetch_refs(self.phab, repo["id"], "tag"), "tag"
                )

        repositories = build_repository_display_data(
            self.url,
            self.format_link,
            found,
            branches_map=branches_map,
            tags_map=tags_map,
            space_map=self._resolve_spaces(found),
            show_uris=show_uris,
            show_branches=show_branches,
            show_tags=show_tags,
            show_metadata=show_metadata,
            show_description=show_description,
        )

        return {"repositories": repositories, "missing_ids": missing_ids}

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
        transactions, _ = self.build_repo_create(name=name, vcs=vcs, status=status)

        return self.apply_repo_create(transactions)

    def build_repo_create(self, name=None, vcs=None, status=None):
        """Compute the transactions for a new repository, without creating it.

        Same build/apply split as build_repo_edit, so a caller can show what
        would be created before creating it. The duplicate check runs here
        rather than at apply time, so a dry run reports the clash too.

        Parameters
        ----------
        name : str
            Repository name, used as the short name as well
        vcs : str, optional
            Version control system, defaults to "git"
        status : str, optional
            Repository status, defaults to "active"

        Returns
        -------
        tuple
            (transactions, changes) - the Conduit transactions to apply and a
            human-readable description of each one

        Raises
        ------
        PhabfiveDataException
            If a repository of that name already exists
        """
        vcs = vcs or "git"
        status = status or "active"

        for repo in self.get_repositories():
            if name in (repo["fields"]["name"], repo["fields"]["shortName"]):
                raise PhabfiveDataException(f"Repository {name} already exists")

        candidates = [
            ("name", name, "Name"),
            ("shortName", name, "Short name"),
            ("vcs", vcs, "VCS"),
            ("status", status, "Status"),
        ]

        transactions = [{"type": kind, "value": value} for kind, value, _ in candidates]

        changes = [
            {"field": label, "old": "(none)", "new": str(value)}
            for _, value, label in candidates
        ]

        # The built-in clone URIs are derived from the short name, so creating
        # the repository mints them; build_repo_edit says the same on a rename.
        changes.append(
            {"field": "Built-in URIs", "old": "(none)", "new": f"/source/{name}.git"}
        )

        return transactions, changes

    def apply_repo_create(self, transactions):
        """Send prepared transactions to Diffusion.

        Parameters
        ----------
        transactions : list
            Transactions from build_repo_create

        Returns
        -------
        str
            PHID of the new repository

        Raises
        ------
        PhabfiveDataException
            If the API rejects the creation
        """
        try:
            new_repo = self.phab.diffusion.repository.edit(transactions=transactions)
        except APIError as e:
            raise PhabfiveDataException(str(e))

        return new_repo["object"]["phid"]

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
        plan, _ = self.build_uri_create(
            repository_name=repository_name,
            new_uri=new_uri,
            io=io,
            display=display,
            credential=credential,
        )

        self.apply_uri_create(plan)

        return new_uri

    def build_uri_create(
        self, repository_name=None, new_uri=None, io=None, display=None, credential=None
    ):
        """Compute what creating a URI would do, without doing it.

        Creating a URI is not additive: Phabricator publishes one clone URI per
        repository, so this demotes every URI already on the repository to
        io=read, display=never before adding the new one. That is invisible in
        the arguments, so the description names each URI it would demote.

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
            Credential monogram (e.g. "K123")

        Returns
        -------
        tuple
            (plan, changes) - what apply_uri_create needs, and a
            human-readable description of each effect. The credential is named
            by its monogram; its secret never reaches the description.

        Raises
        ------
        PhabfiveConfigException
            If io or display values are invalid
        PhabfiveDataException
            If the repository does not exist
        """
        io = resolve_io_value(io or "default", choices=IO_NEW_URI_CHOICES)
        display = resolve_display_value(display or "always")

        repos = self.get_repositories(attachments={"uris": True})
        repo = match_repository(repos, repository_name)

        if repo is None:
            raise PhabfiveDataException(
                f"'{repository_name}' does not exist. Please create a new repository"
            )

        record = self.passphrase.get_credential_record(credential)
        credential_phid = self._validate_credential_type(credential=record)

        demotions = []
        for uri in repo["attachments"]["uris"]["uris"]:
            fields = uri["fields"]
            current_io = fields["io"]["effective"]
            current_display = fields["display"]["effective"]
            target_io = demotion_io(fields)

            if current_io == target_io and current_display == "never":
                # Already where the demotion would put it.
                continue

            demotions.append(
                {
                    "id": uri["id"],
                    "uri": fields["uri"]["display"],
                    "io": current_io,
                    "display": current_display,
                    "target_io": target_io,
                }
            )

        plan = {
            "repository_phid": repo["phid"],
            "demotions": demotions,
            "transactions": [
                {"type": "repository", "value": repo["phid"]},
                {"type": "uri", "value": new_uri},
                {"type": "io", "value": io},
                {"type": "display", "value": display},
                {"type": "credential", "value": credential_phid},
            ],
        }

        changes = [
            {"field": "New URI", "old": "(none)", "new": str(new_uri)},
            {"field": "I/O", "old": "(none)", "new": io},
            {"field": "Display", "old": "(none)", "new": display},
            # The monogram, never the secret behind it.
            {"field": "Credential", "old": "(none)", "new": str(credential)},
        ]

        for demoted in demotions:
            changes.append(
                {
                    "field": f"Demotes {demoted['uri']}",
                    "old": f"io={demoted['io']}, display={demoted['display']}",
                    "new": f"io={demoted['target_io']}, display=never",
                }
            )

        return plan, changes

    def apply_uri_create(self, plan):
        """Demote the repository's existing URIs, then add the new one.

        Not atomic, and cannot be: Conduit has no transaction spanning
        several objects. Each demotion is its own edit, so a failure part
        way leaves the repository with some URIs already demoted and no new
        URI to replace them. Run build_uri_create first and read what it
        says before committing to this.

        Parameters
        ----------
        plan : dict
            The plan from build_uri_create

        Raises
        ------
        PhabfiveDataException
            If the API rejects the edit
        """
        for demoted in plan["demotions"]:
            self.edit_uri(
                uri=demoted["uri"],
                io=demoted["target_io"],
                display="never",
                object_identifier=demoted["id"],
            )

        try:
            self.phab.diffusion.uri.edit(transactions=plan["transactions"])
        except APIError as e:
            raise PhabfiveDataException(str(e))

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

    def get_uri_and_repo(self, repo_name, uri_name):
        """Fetch a URI and the repository that owns it, in one lookup.

        Parameters
        ----------
        repo_name : str
            Repository monogram, callsign or short name
        uri_name : str
            URI as displayed

        Returns
        -------
        tuple
            (repository, uri), both full records
        """
        return resolve_uri_and_repo(self.phab, repo_name, uri_name)

    @staticmethod
    def describe_repository(repo):
        """Name a repository the way a person would refer to it.

        Parameters
        ----------
        repo : dict
            A repository record

        Returns
        -------
        str
            e.g. "R86 (aws-redis)", or just "R86" when it has no name
        """
        fields = repo.get("fields", {})
        name = fields.get("shortName") or fields.get("callsign") or fields.get("name")
        monogram = f"R{repo['id']}"

        return f"{monogram} ({name})" if name else monogram

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
        PhabfiveConfigException
            If io or display is not a value Phorge has
        PhabfiveDataException
            If the credential is missing or of an unusable type
        """
        # Before anything compares them to what the URI already carries, so a
        # deprecated spelling of a value the URI is already at is no change.
        if io is not None:
            io = resolve_io_value(io)

        if display is not None:
            display = resolve_display_value(display)

        fields = uri_record.get("fields", {})

        credential_phid = None
        if credential:
            record = self.passphrase.get_credential_record(credential)
            credential_phid = self._validate_credential_type(credential=record)

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
        except APIError as e:
            raise PhabfiveDataException(str(e))

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

    def get_repo_record(self, repo_name):
        """Fetch a repository in full, so an edit can be shown before it is made.

        Parameters
        ----------
        repo_name : str
            Repository monogram, callsign or short name

        Returns
        -------
        dict
            The repository object, with 'id', 'phid' and 'fields' keys

        Raises
        ------
        PhabfiveDataException
            If the repository does not exist
        """
        repo = find_repository(self.phab, repo_name)

        if repo is None:
            raise PhabfiveDataException(f"Repository '{repo_name}' does not exist")

        return repo

    def build_repo_edit(
        self,
        repo_record,
        name=None,
        short_name=None,
        default_branch=None,
        status=None,
    ):
        """Compute the transactions for a repository edit, without applying them.

        Parameters
        ----------
        repo_record : dict
            The repository as it stands, from get_repo_record
        name : str, optional
            New human-readable name
        short_name : str, optional
            New short name
        default_branch : str, optional
            New default branch
        status : str, optional
            New status ("active" or "inactive")

        Returns
        -------
        tuple
            (transactions, changes) - the Conduit transactions to apply and a
            human-readable description of each one. A short name change also
            describes the built-in URIs it rewrites, which are derived from it
            and would otherwise change with nothing having said so.
        """
        fields = repo_record.get("fields", {})

        candidates = [
            ("name", name, "Name", fields.get("name")),
            ("shortName", short_name, "Short name", fields.get("shortName")),
            (
                "defaultBranch",
                default_branch,
                "Default branch",
                fields.get("defaultBranch"),
            ),
            ("status", status, "Status", fields.get("status")),
        ]

        transactions = []
        changes = []

        for kind, value, label, current in candidates:
            if value is None or current == value:
                # Not asked for, or already at the target value.
                continue

            transactions.append({"type": kind, "value": value})

            changes.append(
                {
                    "field": label,
                    "old": "(none)" if current is None else str(current),
                    "new": str(value),
                }
            )

            if kind == "shortName":
                # Phabricator derives the built-in clone URIs from the short
                # name, so this one edit silently moves every /source/ path.
                changes.append(
                    {
                        "field": "Built-in URIs",
                        "old": f"/source/{current}.git",
                        "new": f"/source/{value}.git",
                    }
                )

        return transactions, changes

    def apply_repo_edit(self, object_identifier, transactions):
        """Send prepared transactions to Diffusion.

        Parameters
        ----------
        object_identifier : str
            Repository object identifier
        transactions : list
            Transactions from build_repo_edit

        Raises
        ------
        PhabfiveDataException
            If the API rejects the edit
        """
        try:
            self.phab.diffusion.repository.edit(
                transactions=transactions, objectIdentifier=object_identifier
            )
        except APIError as e:
            raise PhabfiveDataException(str(e))

    def edit_repository(
        self,
        name=None,
        short_name=None,
        default_branch=None,
        status=None,
        object_identifier=None,
        repo_record=None,
        dry_run=False,
    ):
        """
        Edit an existing repository.

        Thin composition of build_repo_edit and the Conduit call, so a caller
        that wants to show the change before making it can stop in between.

        Parameters
        ----------
        name : str, optional
            New human-readable name
        short_name : str, optional
            New short name
        default_branch : str, optional
            New default branch
        status : str, optional
            New status
        object_identifier : str, optional
            Repository object identifier
        repo_record : dict, optional
            The repository as it stands; only needed to describe the change
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
        transactions, changes = self.build_repo_edit(
            repo_record or {},
            name=name,
            short_name=short_name,
            default_branch=default_branch,
            status=status,
        )

        if not transactions:
            return {"changes": [], "dry_run": dry_run}

        if dry_run:
            return {"changes": changes, "dry_run": True}

        self.apply_repo_edit(object_identifier, transactions)

        return {"changes": changes, "dry_run": False}
