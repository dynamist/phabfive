# -*- coding: utf-8 -*-

"""Main Project class: reading and writing Phorge projects."""

import logging

from phabfive.constants import (
    PROJECT_POLICY_FIELDS,
    PROJECT_STATUS_ACTIVE,
    PROJECT_STATUS_ALL,
    PROJECT_STATUS_ARCHIVED,
)
from phabfive.core import Phabfive
from phabfive.exceptions import PhabfiveConfigException, PhabfiveDataException
from phabfive.maniphest.resolvers import resolve_space_phids
from phabfive.policy import resolve_policy_names
from phabfive.project.fetchers import fetch_projects, fetch_users_by_phid, name_spaces
from phabfive.project.formatters import (
    build_project_display_data,
    project_member_phids,
)
from phabfive.project.resolvers import resolve_project, resolve_user_phids

log = logging.getLogger(__name__)

PROJECT_STATUSES = (PROJECT_STATUS_ACTIVE, PROJECT_STATUS_ARCHIVED, PROJECT_STATUS_ALL)


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
            "active" (the default), "archived" or "all"
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
        if status not in PROJECT_STATUSES:
            raise PhabfiveConfigException(
                f"Unknown project status '{status}', "
                f"expected one of: {', '.join(PROJECT_STATUSES)}"
            )

        constraints = {"status": status}

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
