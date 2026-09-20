# -*- coding: utf-8 -*-

"""Main Diffusion class that orchestrates all submodules."""

import logging

from phabricator import APIError

from phabfive import passphrase
from phabfive.constants import (
    IO_NEW_URI_CHOICES,
    REPO_POLICY_FIELDS,
    REPO_POLICY_TRANSACTIONS,
    REPO_STATUS_CHOICES,
)
from phabfive.core import Phabfive
from phabfive.diffusion.fetchers import (
    fetch_branches,
    fetch_refs,
    fetch_repositories,
    demotion_io,
    find_repository,
    match_repository,
)
from phabfive.diffusion.filters import select_uris
from phabfive.diffusion.formatters import (
    build_repository_display_data,
    format_uri,
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
from phabfive.exceptions import (
    PhabfiveDataException,
    PhabfiveNameCollisionException,
)
from phabfive.policy import (
    policy_label,
    policy_lockout_message,
    resolve_policy_names,
    resolve_policy_value,
)

log = logging.getLogger(__name__)

# Phorge accepts only letters, digits, ".", "-" and "_" in a repository short
# name (assertValidRepositorySlug, src/applications/repository/storage/
# PhabricatorRepository.php), so those three characters are the whole of the
# punctuation two short names can differ by.
_SHORT_NAME_PUNCTUATION = str.maketrans("", "", "._-")


def fold_short_name(name):
    """Reduce a name to what makes two of them confusable.

    Phorge stores `repositorySlug` in a `utf8mb4_unicode_ci` column under a
    unique key and looks a slug up with a plain `IN`, so it already refuses a
    short name differing from an existing one only in case or in accents. It
    does not fold punctuation: `myrepo`, `my-repo`, `my_repo` and `my.repo`
    are four separate repositories to Phorge, and a repository cannot be
    deleted afterwards through Conduit or the web UI. Folding case and those
    three characters is what closes the gap Phorge leaves.

    Parameters
    ----------
    name : str or None
        A repository name or short name

    Returns
    -------
    str
        The folded form, or "" for an empty name
    """
    if not name:
        return ""

    return name.translate(_SHORT_NAME_PUNCTUATION).casefold()


def describe_repository(repo):
    """Name a repository in an error, by monogram and by the name it carries.

    Parameters
    ----------
    repo : dict
        A repository record

    Returns
    -------
    str
        e.g. "R86 (aws-redis)", or "R86" if it carries no name at all
    """
    label = Diffusion.name_repository(repo)
    monogram = f"R{repo['id']}"

    return f"{monogram} ({label})" if label else monogram


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

    def _resolve_policy_names(self, repos):
        """Name the projects, users and rules a set of policies point at.

        Parameters
        ----------
        repos : list
            Repository records

        Returns
        -------
        dict
            Policy PHID to its name. Empty when every policy is a keyword,
            which is the common case and costs no round trip at all.
        """
        values = [
            (repo.get("fields", {}).get("policy") or {}).get(field)
            for repo in repos
            for field in REPO_POLICY_FIELDS.values()
        ]

        return resolve_policy_names(self.phab, values)

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
            policy_names=self._resolve_policy_names(found),
            show_uris=show_uris,
            show_branches=show_branches,
            show_tags=show_tags,
            show_metadata=show_metadata,
            show_description=show_description,
        )

        return {"repositories": repositories, "missing_ids": missing_ids}

    def repo_list(self, status=None, show_uris=False):
        """
        List repositories, as the records ``repo_show`` answers with.

        The same builder, so a listed repository and a shown one cannot
        describe themselves differently. Only the per-repository sections
        differ: branches and tags are deliberately not offered here,
        because each costs one ``branchquery`` or ``tagsquery`` per
        repository, which would turn a list into hundreds of round trips
        on a large instance. URIs ride the search call's own attachment,
        so they cost nothing extra.

        Parameters
        ----------
        status : list, optional
            Statuses to keep, defaults to every status
        show_uris : bool, optional
            Include each repository's URIs

        Returns
        -------
        dict
            {"repositories": [...]}, sorted by name
        """
        status = status or REPO_STATUS_CHOICES

        # Asked for whether or not they are shown: repository_is_hosted
        # falls back to them on an instance that does not report isHosted,
        # and the attachment rides this same call.
        repos = fetch_repositories(self.phab, attachments={"uris": True})

        repos = [repo for repo in repos if repo["fields"].get("status") in status]
        repos = sorted(repos, key=lambda repo: repo["fields"].get("name") or "")

        repositories = build_repository_display_data(
            self.url,
            self.format_link,
            repos,
            space_map=self._resolve_spaces(repos),
            policy_names=self._resolve_policy_names(repos),
            show_uris=show_uris,
        )

        return {"repositories": repositories}

    def uri_list(
        self,
        repo,
        clone_only=False,
        io=None,
        display=None,
        builtin=None,
        disabled=None,
    ):
        """
        List a repository's URIs in full.

        Filtering happens before the credentials are named, so a URI the
        caller filtered away costs no ``phid.query``.

        Parameters
        ----------
        repo : str
            Repository monogram (e.g., "R123"), callsign or short name
        clone_only : bool, optional
            Keep only the URIs the instance shows as clone URIs
        io : str, optional
            Keep the URIs whose I/O is this, set or in force
        display : str, optional
            Keep the URIs whose display is this, set or in force
        builtin : bool, optional
            True keeps the built-in URIs, False the external ones
        disabled : bool, optional
            True keeps the disabled URIs, False the enabled ones

        Returns
        -------
        dict
            {"uris": [...]}. A repository with no URIs to list, and one
            whose URIs the filters all rejected, are both an empty result
            rather than a failure.

        Raises
        ------
        PhabfiveDataException
            If no such repository exists
        """
        repos = fetch_repositories(self.phab, attachments={"uris": True})

        match = match_repository(repos, repo)

        if match is None:
            raise PhabfiveDataException(f"Repository '{repo}' not found")

        uris = select_uris(
            match["attachments"]["uris"]["uris"],
            clone_only=clone_only,
            io=io,
            display=display,
            builtin=builtin,
            disabled=disabled,
        )

        credential_names = self._credential_names(uris)

        return {"uris": [format_uri(uri, credential_names) for uri in uris]}

    def _credential_names(self, uris):
        """Name the credentials a set of URIs is bound to, by monogram.

        One lookup per distinct credential, through the same
        ``_describe_credential`` an edit uses: it asks ``phid.query``,
        which answers with the monogram and nothing else. The secret is
        never read, here or anywhere on a read path.

        Parameters
        ----------
        uris : list
            URI records

        Returns
        -------
        dict
            Credential PHID to its monogram
        """
        names = {}

        for uri in uris:
            phid = uri.get("fields", {}).get("credentialPHID")

            if phid and phid not in names:
                names[phid] = self._describe_credential(phid)

        return names

    # Core operations that remain in the main class

    def create_repository(self, name=None, vcs=None, status=None, allow_similar=False):
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
        allow_similar : bool, optional
            Create it even though an existing repository differs from it only
            in case or punctuation

        Returns
        -------
        str
            PHID of the new repository

        Raises
        ------
        PhabfiveDataException
            If repository already exists or API error
        """
        transactions, _ = self.build_repo_create(
            name=name, vcs=vcs, status=status, allow_similar=allow_similar
        )

        return self.apply_repo_create(transactions)

    def find_name_collision(self, name, exclude_id=None):
        """Find the repository a name would clash with, exactly or nearly.

        One repository listing answers both questions, so this costs the same
        single paged fetch the exact-match check already cost.

        An exact match wins over a near one wherever both exist, whatever
        order the listing happens to be in: an exact clash cannot be
        overridden, so reporting a near one in its place would offer an
        override that does not work.

        Parameters
        ----------
        name : str
            The name being claimed
        exclude_id : int, optional
            A repository to ignore, so a rename does not collide with the
            repository being renamed

        Returns
        -------
        tuple or None
            (repo, exact) for the repository clashed with, or None if the
            name is free. `exact` is True for a match Phorge itself would
            refuse, False for one only the fold catches.
        """
        folded = fold_short_name(name)
        near = None

        for repo in self.get_repositories():
            if exclude_id is not None and repo.get("id") == exclude_id:
                continue

            fields = repo.get("fields", {})
            existing = (fields.get("name"), fields.get("shortName"))

            if name in existing:
                return repo, True

            if near is None and folded:
                if any(fold_short_name(value) == folded for value in existing):
                    near = (repo, False)

        return near

    def assert_name_is_free(self, name, exclude_id=None, allow_similar=False):
        """Refuse a name that clashes with a repository already on the instance.

        Parameters
        ----------
        name : str
            The name being claimed
        exclude_id : int, optional
            A repository to ignore, for a rename
        allow_similar : bool, optional
            Accept a near-duplicate. An exact match is refused regardless -
            Phorge would refuse it too, so there is nothing to override.

        Raises
        ------
        PhabfiveDataException
            If a repository of that exact name already exists
        PhabfiveNameCollisionException
            If one differs only in case or in "." "-" "_" punctuation, unless
            allow_similar is set
        """
        collision = self.find_name_collision(name, exclude_id=exclude_id)

        if collision is None:
            return

        repo, exact = collision

        if exact:
            raise PhabfiveDataException(
                f"Repository {name} already exists as {describe_repository(repo)}"
            )

        if allow_similar:
            return

        raise PhabfiveNameCollisionException(
            f"Repository {name} is too similar to {describe_repository(repo)}, "
            f"which already exists - they differ only in case or in "
            f'"." "-" "_" punctuation'
        )

    def build_repo_create(self, name=None, vcs=None, status=None, allow_similar=False):
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
        allow_similar : bool, optional
            Create the repository even though an existing one differs from it
            only in case or punctuation

        Returns
        -------
        tuple
            (transactions, changes) - the Conduit transactions to apply and a
            human-readable description of each one

        Raises
        ------
        PhabfiveDataException
            If a repository of that name already exists
        PhabfiveNameCollisionException
            If one is near enough to be confused with it, unless allow_similar
        """
        vcs = vcs or "git"
        status = status or "active"

        self.assert_name_is_free(name, allow_similar=allow_similar)

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
        name = Diffusion.name_repository(repo)
        monogram = f"R{repo['id']}"

        return f"{monogram} ({name})" if name else monogram

    @staticmethod
    def name_repository(repo):
        """The most human of the names a repository carries, if any.

        Parameters
        ----------
        repo : dict
            A repository record

        Returns
        -------
        str or None
            Short name, else callsign, else name, else None
        """
        fields = repo.get("fields", {})

        return fields.get("shortName") or fields.get("callsign") or fields.get("name")

    def link_repository(self, repo):
        """Point at a repository on this instance, by monogram.

        The change itself already names the remote being edited, so the
        header is the place to say which Phabricator object it belongs to -
        and to say it in a form that can be opened or pasted back into the
        CLI.

        Parameters
        ----------
        repo : dict
            A repository record

        Returns
        -------
        str
            e.g. "https://phabricator.example.com/R86 (aws-redis)"
        """
        name = Diffusion.name_repository(repo)
        url = f"{self.url}/R{repo['id']}"

        return f"{url} ({name})" if name else url

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
            The repository object, with 'id', 'phid' and 'fields' keys and
            its URIs attached

        Raises
        ------
        PhabfiveDataException
            If the repository does not exist
        """
        # The URIs attachment rides this same call because
        # repository_is_hosted falls back to it on an instance that does not
        # report isHosted, and repo edit consults it before it lets a push
        # policy be set on a repository that cannot take one.
        repo = find_repository(self.phab, repo_name, attachments={"uris": True})

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
        visible_to=None,
        editable_by=None,
        can_push=None,
        allow_similar=False,
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
        visible_to : str, optional
            New view policy, in the grammar phabfive.policy accepts
        editable_by : str, optional
            New edit policy
        can_push : str, optional
            New push policy
        allow_similar : bool, optional
            Accept a new short name that differs from an existing repository
            only in case or punctuation

        Returns
        -------
        tuple
            (transactions, changes) - the Conduit transactions to apply and a
            human-readable description of each one. A short name change also
            describes the built-in URIs it rewrites, which are derived from it
            and would otherwise change with nothing having said so.

        Raises
        ------
        PhabfiveConfigException
            If a policy value is outside the grammar
        PhabfiveDataException
            If a policy names a project or user that does not exist, or a new
            short name is already taken
        PhabfiveNameCollisionException
            If a new short name is near enough to an existing repository to be
            confused with it, unless allow_similar
        """
        fields = repo_record.get("fields", {})

        # A rename can walk into a collision exactly as a create can, and it
        # rewrites the built-in URIs on the way. Only pay for the repository
        # listing when the short name is actually moving, so `repo edit
        # --status=inactive` keeps costing what it costs today.
        if short_name is not None and short_name != fields.get("shortName"):
            self.assert_name_is_free(
                short_name,
                exclude_id=repo_record.get("id"),
                allow_similar=allow_similar,
            )

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

        policy_transactions, policy_changes = self._build_policy_edit(
            fields.get("policy") or {},
            visible_to=visible_to,
            editable_by=editable_by,
            can_push=can_push,
        )

        return transactions + policy_transactions, changes + policy_changes

    def _build_policy_edit(
        self, policy, visible_to=None, editable_by=None, can_push=None
    ):
        """The policy half of a repository edit.

        Kept apart from the scalar fields because a policy is not a scalar:
        each value asked for has to be resolved against the instance before
        it can be compared with the one in place, and both ends of the
        comparison are then named for the dry run - a PHID either side of an
        arrow says nothing about what changed.

        Parameters
        ----------
        policy : dict
            The "policy" field of the repository as it stands
        visible_to, editable_by, can_push : str, optional
            New policies, in the grammar phabfive.policy accepts

        Returns
        -------
        tuple
            (transactions, changes)
        """
        asked = [
            ("view", visible_to, "Visible To", "--visible-to"),
            ("edit", editable_by, "Editable By", "--editable-by"),
            ("push", can_push, "Can Push", "--can-push"),
        ]

        wanted = [
            (
                key,
                label,
                policy.get(REPO_POLICY_FIELDS[key]),
                resolve_policy_value(self.phab, value, option=option),
            )
            for key, value, label, option in asked
            if value is not None
        ]

        if not wanted:
            return [], []

        # One lookup for both ends of every arrow: the policy in place and
        # the one asked for are named out of the same phid.query.
        names = resolve_policy_names(
            self.phab,
            [current for _, _, current, _ in wanted] + [new for _, _, _, new in wanted],
        )

        transactions = []
        changes = []

        for key, label, current, new in wanted:
            if current == new:
                # Already at the target policy.
                continue

            transactions.append({"type": REPO_POLICY_TRANSACTIONS[key], "value": new})

            changes.append(
                {
                    "field": label,
                    "old": policy_label(current, names),
                    "new": policy_label(new, names),
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
            If the API rejects the edit. A policy that would take the
            repository away from whoever is applying it is rejected this way,
            and is reported as the sentence Phorge answered with rather than
            as the APIError around it.
        """
        try:
            self.phab.diffusion.repository.edit(
                transactions=transactions, objectIdentifier=object_identifier
            )
        except APIError as e:
            raise PhabfiveDataException(policy_lockout_message(e) or str(e))

    def edit_repository(
        self,
        name=None,
        short_name=None,
        default_branch=None,
        status=None,
        visible_to=None,
        editable_by=None,
        can_push=None,
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
        visible_to : str, optional
            New view policy
        editable_by : str, optional
            New edit policy
        can_push : str, optional
            New push policy
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
            visible_to=visible_to,
            editable_by=editable_by,
            can_push=can_push,
        )

        if not transactions:
            return {"changes": [], "dry_run": dry_run}

        if dry_run:
            return {"changes": changes, "dry_run": True}

        self.apply_repo_edit(object_identifier, transactions)

        return {"changes": changes, "dry_run": False}
