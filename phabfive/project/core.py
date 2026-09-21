# -*- coding: utf-8 -*-

"""Main Project class: reading and writing Phorge projects."""

import logging

from phabricator import APIError

from phabfive.constants import (
    PROJECT_POLICY_FIELDS,
    PROJECT_POLICY_TRANSACTIONS,
    PROJECT_STATUS_ACTIVE,
    PROJECT_STATUS_ALL,
    PROJECT_STATUS_ANY,
    PROJECT_STATUS_CHOICES,
)
from phabfive.core import Phabfive
from phabfive.exceptions import PhabfiveConfigException, PhabfiveDataException
from phabfive.maniphest.resolvers import (
    describe_space,
    describe_space_phid,
    resolve_space,
    resolve_space_phids,
)
from phabfive.policy import (
    policy_label,
    policy_lockout_message,
    resolve_policy_names,
    resolve_policy_value,
)
from phabfive.project.fetchers import fetch_projects, fetch_users_by_phid, name_spaces
from phabfive.project.formatters import (
    build_project_display_data,
    describe_project,
    project_member_phids,
)
from phabfive.project.resolvers import resolve_project, resolve_user_phids

log = logging.getLogger(__name__)


class Project(Phabfive):
    def __init__(self):
        super(Project, self).__init__()

    def get_project(self, ident, attachments=None):
        """Resolve one project identifier to its project.search record.

        Parameters
        ----------
        ident : str
            A #hashtag, bare hashtag, PHID, ID or exact name
        attachments : dict, optional
            project.search attachments to ask for on the way

        Returns
        -------
        dict
            The project.search result item

        Raises
        ------
        PhabfiveConfigException
            If nothing was given
        PhabfiveDataException
            If it names no project, or several
        """
        return resolve_project(self.phab, ident, attachments=attachments)

    def _display_data(
        self,
        projects,
        show_policy=False,
        show_members=False,
        show_metadata=False,
        show_description=True,
    ):
        """The display records for a set of projects, one lookup per kind.

        Spaces are always named; policies and members only when asked for,
        each at the cost of one batched lookup for the whole set however
        many projects it holds.
        """
        policy_names = None
        users = None

        if show_policy:
            policy_names = resolve_policy_names(
                self.phab,
                [
                    (project.get("fields", {}).get("policy") or {}).get(field)
                    for project in projects
                    for field in PROJECT_POLICY_FIELDS.values()
                ],
            )

        if show_members:
            users = fetch_users_by_phid(
                self.phab,
                [
                    phid
                    for project in projects
                    for phid in project_member_phids(project)
                ],
            )

        return build_project_display_data(
            self.url,
            self.format_link,
            projects,
            space_map=name_spaces(
                self.phab,
                [project.get("fields", {}).get("spacePHID") for project in projects],
            ),
            policy_names=policy_names,
            users=users,
            show_policy=show_policy,
            show_members=show_members,
            show_metadata=show_metadata,
            show_description=show_description,
        )

    def show(
        self,
        idents,
        show_policy=False,
        show_members=False,
        show_metadata=False,
        show_description=True,
    ):
        """Show one or more projects in full.

        Modelled on Diffusion's repo_show: the records come back in the
        order they were asked for, and the ones that could not be resolved
        are reported back so the caller can exit non-zero.

        Parameters
        ----------
        idents : list[str]
            Project identifiers, as resolve_project takes them
        show_policy, show_members, show_metadata : bool, optional
            Include that section
        show_description : bool, optional
            Include the description

        Returns
        -------
        dict
            {"projects": [...], "missing_ids": [...]}
        """
        attachments = {"members": True} if show_members else None

        found = []
        missing_ids = []

        for ident in idents:
            try:
                project = self.get_project(ident, attachments=attachments)
            except (PhabfiveConfigException, PhabfiveDataException) as e:
                log.error(str(e))
                missing_ids.append(ident)
                continue

            # The same project named twice - by hashtag and by ID, say - is
            # shown once, the way a set of identifiers reads.
            if all(project["phid"] != seen["phid"] for seen in found):
                found.append(project)

        projects = self._display_data(
            found,
            show_policy=show_policy,
            show_members=show_members,
            show_metadata=show_metadata,
            show_description=show_description,
        )

        return {"projects": projects, "missing_ids": missing_ids}

    def search(
        self,
        query=None,
        members=None,
        parents=None,
        ancestors=None,
        milestones=None,
        status=PROJECT_STATUS_ACTIVE,
        icons=None,
        colors=None,
        spaces=None,
        show_policy=False,
        show_members=False,
        limit=None,
    ):
        """
        Search projects, as the records `show` answers with.

        No Space is assumed. PHAB_SPACE narrows a task search by default, but
        a project search is how an instance is audited, and an audit that
        quietly left out the projects in other Spaces would be wrong without
        saying so - so a Space is filtered on only when one is named, the
        way `diffusion repo list` does it.

        Parameters
        ----------
        query : str, optional
            Free text, matched the way the web UI's search box matches it
        members : list, optional
            Usernames (@user, user, @me); a project matches if any of them
            is a member
        parents : list, optional
            Projects whose direct subprojects and milestones to list
        ancestors : list, optional
            Projects anywhere beneath which to list
        milestones : bool, optional
            True lists only milestones, False only projects that are not
            milestones, None both
        status : str, optional
            "active" (the default), "archived", or "any" for no status
            filter at all
        icons, colors : list, optional
            Icon and colour keys, any of which matches
        spaces : list, optional
            Space names, monograms or patterns, any of which matches
        show_policy, show_members : bool, optional
            Include that section
        limit : int, optional
            How many projects to return in total; None for all of them

        Returns
        -------
        dict
            {"projects": [...]}, sorted by name, then by ID

        Raises
        ------
        PhabfiveConfigException
            If a status is unknown
        PhabfiveDataException
            If a user, project or Space named does not exist, or the search
            fails
        """
        if status not in PROJECT_STATUS_CHOICES:
            raise PhabfiveConfigException(
                f"Unknown project status '{status}', "
                f"expected one of: {', '.join(PROJECT_STATUS_CHOICES)}"
            )

        constraints = {
            "status": PROJECT_STATUS_ALL if status == PROJECT_STATUS_ANY else status
        }

        if query:
            constraints["query"] = query

        if members:
            constraints["members"] = [
                phid for phid, _ in resolve_user_phids(self.phab, members).values()
            ]

        if parents:
            constraints["parents"] = [
                self.get_project(parent)["phid"] for parent in parents
            ]

        if ancestors:
            constraints["ancestors"] = [
                self.get_project(ancestor)["phid"] for ancestor in ancestors
            ]

        if milestones is not None:
            constraints["isMilestone"] = bool(milestones)

        if icons:
            constraints["icons"] = list(icons)

        if colors:
            constraints["colors"] = list(colors)

        if spaces:
            space_phids = []
            for space in spaces:
                space_phids.extend(resolve_space_phids(self.phab, space))
            constraints["spaces"] = list(dict.fromkeys(space_phids))

        attachments = {"members": True} if show_members else None

        found = fetch_projects(
            self.phab, constraints=constraints, attachments=attachments, limit=limit
        )
        found = sorted(
            found,
            key=lambda project: (
                (project.get("fields", {}).get("name") or "").casefold(),
                project["id"],
            ),
        )

        projects = self._display_data(
            found, show_policy=show_policy, show_members=show_members
        )

        return {"projects": projects}

    # Writing

    def get_project_for_edit(self, ident):
        """The project to edit, with what an edit has to compare against.

        project.search reports a project's primary hashtag and nothing else,
        while the "slugs" transaction replaces every additional one - so the
        full list is read from project.query, the one method that reports
        it, or adding a hashtag would quietly drop the others.
        """
        project = self.get_project(ident, attachments={"members": True})

        try:
            found = self.phab.project.query(phids=[project["phid"]])
            data = (found or {}).get("data") or {}
            legacy = data.get(project["phid"]) or next(iter(data.values()), {})
        except Exception as e:
            raise PhabfiveDataException(f"Failed to read project hashtags: {e}")

        project["_slugs"] = list(legacy.get("slugs") or [])

        return project

    def _hashtag_is_free(self, name, exclude_phid=None):
        """Refuse a name whose hashtag another project already has.

        Phorge derives a project's hashtag from its name and refuses a
        second project with the same one, and it does so when the edit is
        applied. Asked here too, so a dry run reports the clash rather than
        promising a create that would fail. project.search normalises the
        slug it is given the way Phorge derives one, so the name itself is
        what is asked about.
        """
        try:
            found = (
                self.phab.project.search(constraints={"slugs": [name]}).get("data")
                or []
            )
        except Exception as e:
            raise PhabfiveDataException(f"Failed to look up project: {e}")

        taken = [project for project in found if project["phid"] != exclude_phid]

        if taken:
            other = taken[0]
            raise PhabfiveDataException(
                f"Project name '{name}' generates the same hashtag as "
                f"{describe_project(other)} ({other['fields']['name']}). "
                "Choose a unique name."
            )

    def _resolve_space_for_write(self, space):
        """The one Space an edit names, as (PHID, description)."""
        resolved = resolve_space(self.phab, space)

        return resolved["phid"], describe_space(resolved)

    def build_project_create(
        self,
        name,
        description=None,
        icon=None,
        color=None,
        slugs=None,
        members=None,
        parent=None,
        milestone_of=None,
        space=None,
        visible_to=None,
        editable_by=None,
        joinable_by=None,
    ):
        """Compute the transactions for a new project, without creating it.

        The same build/apply split as `diffusion repo create`, so a dry run
        shows what would be created, and a clash is found before anything
        is sent.

        Parameters
        ----------
        name : str
            Project name
        description : str, optional
            Project description
        icon, color : str, optional
            Icon and colour keys; the server validates them
        slugs : list, optional
            Additional hashtags, with or without their "#"
        members : list, optional
            Usernames (@user, user, @me)
        parent : str, optional
            Project to create a subproject of
        milestone_of : str, optional
            Project to create a milestone of
        space : str, optional
            Space name or monogram
        visible_to, editable_by, joinable_by : str, optional
            Policies, in the grammar phabfive.policy accepts

        Returns
        -------
        tuple
            (transactions, changes)

        Raises
        ------
        PhabfiveConfigException
            If the options contradict each other, or a policy value is
            outside the grammar
        PhabfiveDataException
            If a project, user or Space named does not exist, or the name's
            hashtag is taken
        """
        name = (name or "").strip()

        if not name:
            raise PhabfiveConfigException("A project needs a name")

        if parent and milestone_of:
            raise PhabfiveConfigException(
                "--parent and --milestone-of cannot be combined: a project is "
                "either a subproject or a milestone"
            )

        if milestone_of and (icon or slugs):
            # Phorge ignores an icon on a milestone, and stores a hashtag on
            # one that project.search never reports - neither does what it
            # was asked to, so neither is sent.
            raise PhabfiveConfigException(
                "A milestone takes no --icon or --slug: its icon is fixed and "
                "it has no hashtag"
            )

        transactions = []
        changes = []

        def add(kind, value, label, shown=None):
            transactions.append({"type": kind, "value": value})
            changes.append(
                {
                    "field": label,
                    "old": None,
                    "new": str(value if shown is None else shown),
                }
            )

        # A milestone is named after its number within its parent, and any
        # number of them may share a name, so only the others are checked.
        if not milestone_of:
            self._hashtag_is_free(name)

        add("name", name, "Name")

        if parent:
            parent_project = self.get_project(parent)
            add(
                "parent",
                parent_project["phid"],
                "Parent",
                describe_project(parent_project),
            )

        if milestone_of:
            of_project = self.get_project(milestone_of)
            add(
                "milestone",
                of_project["phid"],
                "Milestone Of",
                describe_project(of_project),
            )

        if description:
            add("description", description, "Description")

        if icon:
            add("icon", icon, "Icon")

        if color:
            add("color", color, "Color")

        slugs = [slug.lstrip("#") for slug in slugs or [] if slug.lstrip("#")]
        if slugs:
            add("slugs", slugs, "Hashtags", ", ".join(f"#{slug}" for slug in slugs))

        if members:
            users = resolve_user_phids(self.phab, members)
            phids = list(dict.fromkeys(phid for phid, _ in users.values()))
            add(
                "members.add",
                phids,
                "Members",
                ", ".join(f"@{username or phid}" for phid, username in users.values()),
            )

        if space:
            space_phid, space_shown = self._resolve_space_for_write(space)
            add("space", space_phid, "Space", space_shown)

        policy_transactions, policy_changes = self._build_policy_edit(
            {},
            visible_to=visible_to,
            editable_by=editable_by,
            joinable_by=joinable_by,
            creating=True,
        )

        return transactions + policy_transactions, changes + policy_changes

    def apply_project_create(self, transactions):
        """Send prepared transactions to create a project.

        Returns
        -------
        dict
            {"id": ..., "phid": ...} of the new project

        Raises
        ------
        PhabfiveDataException
            If the API rejects the creation. A policy that would leave the
            creator without access is reported as the sentence Phorge
            answered with.
        """
        try:
            result = self.phab.project.edit(transactions=transactions)
        except APIError as e:
            raise PhabfiveDataException(policy_lockout_message(e) or str(e))

        return result["object"]

    def build_project_edit(
        self,
        project,
        name=None,
        description=None,
        icon=None,
        color=None,
        add_slugs=None,
        add_members=None,
        remove_members=None,
        space=None,
        visible_to=None,
        editable_by=None,
        joinable_by=None,
    ):
        """Compute the transactions for a project edit, without applying them.

        Anything already at its target - a name unchanged, a member already
        in, a hashtag already there - produces no transaction and no line,
        so "no changes" is an answer an edit can give.

        Parameters
        ----------
        project : dict
            The project as it stands, from get_project_for_edit
        name, description, icon, color : str, optional
            New values
        add_slugs : list, optional
            Hashtags to add, with or without their "#"
        add_members, remove_members : list, optional
            Usernames (@user, user, @me)
        space : str, optional
            Space name or monogram to move the project to
        visible_to, editable_by, joinable_by : str, optional
            New policies, in the grammar phabfive.policy accepts

        Returns
        -------
        tuple
            (transactions, changes)

        Raises
        ------
        PhabfiveConfigException
            If the options contradict each other or the project, or a policy
            value is outside the grammar
        PhabfiveDataException
            If a user, project or Space named does not exist, or a new
            name's hashtag is taken
        """
        fields = project.get("fields", {})
        is_milestone = fields.get("milestone") is not None

        if is_milestone and (icon or add_slugs):
            raise PhabfiveConfigException(
                f"{describe_project(project)} is a milestone, which takes no "
                "--icon or --add-slug: its icon is fixed and it has no hashtag"
            )

        if add_members and remove_members:
            both = set(add_members) & set(remove_members)
            if both:
                raise PhabfiveConfigException(
                    f"Cannot both add and remove {', '.join(sorted(both))}"
                )

        transactions = []
        changes = []

        current_description = fields.get("description") or ""
        if isinstance(current_description, dict):
            current_description = current_description.get("raw") or ""

        candidates = [
            ("name", name, "Name", fields.get("name")),
            ("description", description, "Description", current_description),
            ("icon", icon, "Icon", (fields.get("icon") or {}).get("key")),
            ("color", color, "Color", (fields.get("color") or {}).get("key")),
        ]

        if name is not None and name != fields.get("name") and not is_milestone:
            self._hashtag_is_free(name, exclude_phid=project["phid"])

        for kind, value, label, current in candidates:
            if value is None or value == current:
                continue

            transactions.append({"type": kind, "value": value})
            changes.append(
                {
                    "field": label,
                    "old": "(none)" if current in (None, "") else str(current),
                    "new": str(value),
                }
            )

        if add_slugs:
            current_slugs = list(project.get("_slugs") or [])
            new_slugs = [
                slug.lstrip("#")
                for slug in add_slugs
                if slug.lstrip("#") and slug.lstrip("#") not in current_slugs
            ]

            if new_slugs:
                # The whole list, because the transaction replaces it: sending
                # only the new ones would drop every hashtag already there.
                transactions.append(
                    {"type": "slugs", "value": current_slugs + new_slugs}
                )
                changes.append(
                    {
                        "field": "Hashtags",
                        "old": None,
                        "new": "Added: " + ", ".join(f"#{slug}" for slug in new_slugs),
                    }
                )

        current_members = set(project_member_phids(project))

        for values, kind, verb, wanted in (
            (add_members, "members.add", "Added", False),
            (remove_members, "members.remove", "Removed", True),
        ):
            if not values:
                continue

            users = resolve_user_phids(self.phab, values)
            picked = {
                phid: username
                for phid, username in users.values()
                if (phid in current_members) == wanted
            }

            if picked:
                transactions.append({"type": kind, "value": list(picked)})
                changes.append(
                    {
                        "field": "Members",
                        "old": None,
                        "new": f"{verb}: "
                        + ", ".join(
                            f"@{name or phid}" for phid, name in picked.items()
                        ),
                    }
                )

        if space:
            space_phid, space_shown = self._resolve_space_for_write(space)
            current_space = fields.get("spacePHID")

            if space_phid != current_space:
                transactions.append({"type": "space", "value": space_phid})
                changes.append(
                    {
                        "field": "Space",
                        "old": describe_space_phid(self.phab, current_space)
                        or "(none)",
                        "new": space_shown,
                    }
                )

        policy_transactions, policy_changes = self._build_policy_edit(
            fields.get("policy") or {},
            visible_to=visible_to,
            editable_by=editable_by,
            joinable_by=joinable_by,
        )

        return transactions + policy_transactions, changes + policy_changes

    def apply_project_edit(self, object_identifier, transactions):
        """Send prepared transactions to an existing project.

        Raises
        ------
        PhabfiveDataException
            If the API rejects the edit. A policy that would take the project
            away from whoever is applying it is reported as the sentence
            Phorge answered with rather than as the APIError around it.
        """
        try:
            self.phab.project.edit(
                transactions=transactions, objectIdentifier=object_identifier
            )
        except APIError as e:
            raise PhabfiveDataException(policy_lockout_message(e) or str(e))

    def _build_policy_edit(
        self,
        policy,
        visible_to=None,
        editable_by=None,
        joinable_by=None,
        creating=False,
    ):
        """The policy half of a project create or edit.

        Each value asked for is resolved before it is compared with the one
        in place, and both ends are named for the preview, the way a
        repository's and a task's policies are.

        Parameters
        ----------
        policy : dict
            The "policy" field of the project as it stands; empty on create
        visible_to, editable_by, joinable_by : str, optional
            New policies, in the grammar phabfive.policy accepts
        creating : bool, optional
            A new project has no policy in place to compare with, so every
            value asked for is sent, and shown without an arrow

        Returns
        -------
        tuple
            (transactions, changes)
        """
        asked = [
            ("view", visible_to, "Visible To", "--visible-to"),
            ("edit", editable_by, "Editable By", "--editable-by"),
            ("join", joinable_by, "Joinable By", "--joinable-by"),
        ]

        wanted = [
            (
                key,
                label,
                policy.get(PROJECT_POLICY_FIELDS[key]),
                resolve_policy_value(self.phab, value, option=option),
            )
            for key, value, label, option in asked
            if value is not None
        ]

        if not wanted:
            return [], []

        names = resolve_policy_names(
            self.phab,
            [current for _, _, current, _ in wanted] + [new for _, _, _, new in wanted],
        )

        transactions = []
        changes = []

        for key, label, current, new in wanted:
            if not creating and current == new:
                continue

            transactions.append(
                {"type": PROJECT_POLICY_TRANSACTIONS[key], "value": new}
            )
            changes.append(
                {
                    "field": label,
                    "old": None if creating else policy_label(current, names),
                    "new": policy_label(new, names),
                }
            )

        return transactions, changes
