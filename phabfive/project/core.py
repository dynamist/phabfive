# -*- coding: utf-8 -*-

"""Main Project class: reading and writing Phorge projects."""

import logging


from phabfive.constants import (
    PROJECT_COLORS,
    PROJECT_ICONS,
    PROJECT_MILESTONE_ICON,
    PROJECT_ORDER_DEFAULT,
    PROJECT_ORDER_DIRECTIONS,
    PROJECT_ORDER_FIELDS,
    PROJECT_ORDER_SUGGESTIONS,
    PROJECT_POLICY_FIELDS,
    PROJECT_POLICY_TRANSACTIONS,
    PROJECT_STATUS_ACTIVE,
    PROJECT_STATUS_ALL,
    PROJECT_STATUS_ANY,
    PROJECT_STATUS_CHOICES,
)
from phabfive.core import Phabfive
from phabfive.exceptions import (
    PhabfiveAPIException,
    PhabfiveConfigException,
    PhabfiveDataException,
    PhabfiveInputException,
)
from phabfive.maniphest.resolvers import (
    describe_space,
    describe_space_phid,
    resolve_space,
    resolve_space_phids,
)
from phabfive.options import value_list
from phabfive.ordering import parse_order, sort_records
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
from phabfive.project.resolvers import resolve_project
from phabfive.users import resolve_user_phids, user_list_edit

log = logging.getLogger(__name__)


#: ``(field, direction)`` to what ``project.search`` is sent as its order.
#:
#: A string is one of PhabricatorProjectQuery's builtin orders (name,
#: newest, created, oldest, relevance); a list is a column vector, which is
#: how Z-A is asked for - Phorge has no builtin for it, but "-name" is a
#: column it orders by. Every phabfive spelling maps to one of them, so the
#: server does the ordering and a limit is the top N of it rather than an
#: arbitrary page sorted afterwards.
PROJECT_API_ORDERS = {
    ("name", "asc"): "name",
    ("name", "desc"): ["-name"],
    ("created", "desc"): "newest",
    ("created", "asc"): "oldest",
    ("relevance", None): "relevance",
}

#: The client-side key for each order field, which tie-breaks what the
#: server already ordered. ``relevance`` is absent on purpose: the response
#: carries no rank, so the server's order is the only one there is.
PROJECT_SORT_KEYS = {
    "name": lambda project: (
        (project.get("fields", {}).get("name") or "").casefold(),
        project.get("id") or 0,
    ),
    "created": lambda project: project.get("id") or 0,
}


#: What a project's three policies are called in a preview, which is what the
#: web UI calls them. `phabfive.constants.PROJECT_POLICY_TRANSACTIONS` says
#: what each one is *sent* as; this says what a person reads.
PROJECT_POLICY_LABELS = {
    "view": "Visible To",
    "edit": "Editable By",
    "join": "Joinable By",
}


def _pair(value):
    """One reference as ``(value, shown)``, however the caller wrote it.

    A bare value is shown as itself, which is right for a policy keyword and
    wrong-looking but harmless for a PHID - a caller that has a better name
    for it passes the pair.
    """
    if isinstance(value, tuple) and len(value) == 2:
        return value[0], str(value[1])

    return value, str(value)


def _shown(value, label):
    """``(value, label, shown)``, the three arguments a preview line takes."""
    resolved, name = _pair(value)

    return resolved, label, name


def check_project_create_options(
    name, parent=None, milestone_of=None, icon=None, color=None, slugs=None
):
    """Everything about a new project that is wrong whatever it resolves to.

    Separated from building the transactions so that it can run **before**
    the first lookup: a create spec asking for a milestone with an icon is
    wrong however many projects it names, and refusing it costs no request.
    Both `Project.build_project_create` and
    `Project.project_create_transactions` call it, which is what keeps the
    command and a spec refusing the same combinations.

    Returns
    -------
    str
        The name with its surrounding whitespace removed, which is the name
        the rest of the create uses.

    Raises
    ------
    PhabfiveConfigException
        There is no name, the options contradict each other, or the colour
        is not one Phorge has.
    """
    name = (name or "").strip()

    if not name:
        raise PhabfiveConfigException("A project needs a name")

    if parent and milestone_of:
        raise PhabfiveConfigException(
            "--parent and --milestone-of cannot be combined: a project is "
            "either a subproject or a milestone"
        )

    if milestone_of and (icon or color or slugs):
        # Phorge shows a milestone with the milestone icon and its parent's
        # colour whatever is stored on it, and stores a hashtag on one that
        # project.search never reports - none of them does what it was asked
        # to, so none is sent. A stored colour is worse than ignored:
        # project.search matches it, so the milestone would turn up under a
        # colour it is never shown in.
        raise PhabfiveConfigException(
            "A milestone takes no --icon, --color or --slug: its icon is "
            "fixed, its colour is its parent's, and it has no hashtag"
        )

    check_project_color(color)

    return name


def project_id_list(value, option="--ids"):
    """Project ids as the integers ``project.search`` constrains on.

    A project has no monogram - Phorge names one by hashtag or by id - so
    this takes the bare number, and a hashtag is `slugs` rather than `ids`.

    Parameters
    ----------
    value : str, int, list or None
        ``"12,20"``, ``["12", 20]`` or ``12``
    option : str
        What to name in the error, e.g. ``"--ids"``

    Returns
    -------
    list or None
        The ids, or None when nothing was given

    Raises
    ------
    PhabfiveInputException
        For anything that is not a number. A hashtag is refused by name
        rather than sent as an id nobody meant.
    """
    entries = value_list(value)

    if not entries:
        return None

    ids = []

    for entry in entries:
        if not entry.isdigit():
            raise PhabfiveInputException(
                f"Invalid project ID '{entry}' for {option}. "
                "Expected a number, e.g. 12; a hashtag goes to --slug."
            )

        ids.append(int(entry))

    return ids


def project_slug_list(value):
    """Hashtags as ``project.search`` takes them in its `slugs` constraint.

    A leading ``#`` is how a person writes a hashtag and is not part of the
    slug, so it is dropped. Nothing else is normalised here: project.search
    derives the slug from what it is given exactly the way Phorge derives
    one from a project's name, which is what makes "which project owns this
    hashtag" a single question rather than a guess about spaces and case.
    """
    slugs = [entry.lstrip("#") for entry in value_list(value)]

    return [slug for slug in slugs if slug] or None


def check_project_color(color):
    """Refuse a colour Phorge does not have, before anything is sent.

    The colours are fixed in Phorge's code - projects.colors relabels them
    but cannot add one - so an unknown key is a typo. A write would be
    refused by the server anyway, but a dry run would not reach it and a
    search would answer with nothing at all.

    Raises
    ------
    PhabfiveConfigException
        If the colour is not one of PROJECT_COLORS
    """
    if color and color not in PROJECT_COLORS:
        raise PhabfiveConfigException(
            f"Unknown project color '{color}', "
            f"expected one of: {', '.join(PROJECT_COLORS)}"
        )


def icons_in_use(phab):
    """The stock icons, plus every icon a project on the instance carries.

    The icon set is instance configuration (projects.icons) and no Conduit
    method reports it, so the icons in use are the closest the API comes to
    naming a custom one. A configured icon that no project uses yet is not
    listed - the server still takes it - which is why every caller either
    completes with this or *warns* against it, and none refuses an icon it
    does not find.

    Library code, so it lets a failed lookup fail: `phabfive.cli.completers`
    falls back to the stock list on an error rather than writing the failure
    down as the instance's answer, and `phabfive.spec.online` must be able to
    tell a broken network from an unknown icon.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client

    Returns
    -------
    list
        PROJECT_ICONS, then any further icon in use, sorted
    """
    from phabfive.pagination import search_all_pages

    projects = search_all_pages(
        phab.project.search, constraints={"status": PROJECT_STATUS_ALL}
    )
    in_use = {
        icon
        for project in projects
        if (icon := (project.get("fields", {}).get("icon") or {}).get("key"))
    }

    # "milestone" is Phorge's to give to a milestone, not a value to choose
    in_use.discard(PROJECT_MILESTONE_ICON)

    return PROJECT_ICONS + sorted(in_use - set(PROJECT_ICONS))


class Project(Phabfive):
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
        ids=None,
        phids=None,
        slugs=None,
        watchers=None,
        order=None,
    ):
        """
        Search projects, as the records `show` answers with.

        Narrowed to PHAB_SPACE unless spaces names others, the way a task
        search is, so the two searches agree on where they look. "*" means
        every Space, and is sent as no Space constraint at all rather than as
        a list of every Space - which is what an audit of the whole instance
        asks for.

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
            Icon and colour keys, any of which matches. A milestone cannot
            have either of its own: Phorge shows every milestone with the
            "milestone" icon and its parent's colour, and so does this
            search - see _search_milestones_by_look.
        spaces : list, optional
            Space names, monograms or patterns, any of which matches. None
            means PHAB_SPACE, and "*" among them means every Space.
        show_policy, show_members : bool, optional
            Include that section
        limit : int, optional
            How many projects to return in total; None for all of them
        ids : list, optional
            Project ids, as numbers. Every other filter still applies, so
            this narrows a search rather than fetching those projects.
        phids : list, optional
            Project PHIDs, the same way
        slugs : list, optional
            Hashtags, with or without the leading "#". This is how to ask
            which project owns a hashtag: Phorge normalises the slug the
            way it derives one from a project's name, so "Web Team" and
            "web_team" find the same project.
        watchers : list, optional
            Usernames (@user, user, @me); a project matches if any of them
            watches it. Watching is not membership - a watcher follows a
            project's activity without being on it.
        order : str, optional
            Result ordering as "<field>[:asc|:desc]", e.g. "created:desc".
            The server does the ordering, so a limit keeps the first N of
            it. Defaults to name, which is the order this search has always
            printed.

        Returns
        -------
        dict
            {"projects": [...]}, in the order asked for; by name, then by
            ID, unless --order said otherwise

        Raises
        ------
        PhabfiveConfigException
            If a status, colour or order is unknown
        PhabfiveInputException
            If an id is not a number
        PhabfiveDataException
            If a user, project or Space named does not exist, or the search
            fails
        """
        # Resolved before anything is fetched, so a bad --order fails fast
        # rather than after a walk of the instance.
        order_field, order_direction = parse_order(
            order,
            PROJECT_ORDER_FIELDS,
            PROJECT_ORDER_DIRECTIONS,
            PROJECT_ORDER_DEFAULT,
            suggestions=PROJECT_ORDER_SUGGESTIONS,
        )
        api_order = PROJECT_API_ORDERS[(order_field, order_direction)]
        log.info(f"Ordering results by '{order_field}:{order_direction}'")

        if status not in PROJECT_STATUS_CHOICES:
            raise PhabfiveConfigException(
                f"Unknown project status '{status}', "
                f"expected one of: {', '.join(PROJECT_STATUS_CHOICES)}"
            )

        # A search would answer an unknown colour with an empty result, not
        # an error. Icons are instance configuration and cannot be checked.
        for color in colors or []:
            check_project_color(color)

        constraints = {
            "status": PROJECT_STATUS_ALL if status == PROJECT_STATUS_ANY else status
        }

        if query:
            constraints["query"] = query

        id_list = project_id_list(ids)
        if id_list:
            constraints["ids"] = id_list

        phid_list = value_list(phids)
        if phid_list:
            constraints["phids"] = phid_list

        slug_list = project_slug_list(slugs)
        if slug_list:
            constraints["slugs"] = slug_list

        if members:
            constraints["members"] = [
                phid
                for phid, _ in resolve_user_phids(
                    self.phab, members, option="--member"
                ).values()
            ]

        if watchers:
            constraints["watchers"] = [
                phid
                for phid, _ in resolve_user_phids(
                    self.phab, watchers, option="--watcher"
                ).values()
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

        space_phids = self._resolve_search_spaces(spaces)
        if space_phids:
            constraints["spaces"] = space_phids

        attachments = {"members": True} if show_members else None

        if icons or colors:
            found = self._search_by_look(
                constraints, attachments, icons, colors, milestones, limit, api_order
            )
        else:
            found = fetch_projects(
                self.phab,
                constraints=constraints,
                attachments=attachments,
                limit=limit,
                order=api_order,
            )

        # The server already ordered these; this settles the ties and is
        # what makes two searches merged by _search_by_look one ordering
        # rather than one list after the other.
        found = sort_records(found, order_field, order_direction, PROJECT_SORT_KEYS)

        # After the ordering, so the limit keeps the top N of what was
        # asked for rather than the first N that happened to arrive.
        if limit:
            found = found[:limit]

        projects = self._display_data(
            found, show_policy=show_policy, show_members=show_members
        )

        return {"projects": projects}

    def _search_by_look(
        self, constraints, attachments, icons, colors, milestones, limit, order=None
    ):
        """Search by icon and colour, the way Phorge shows them.

        project.search matches its icons and colors constraints against the
        values stored on a project. A milestone has stored ones too - but
        Phorge never shows them: it gives every milestone the "milestone"
        icon and its parent's colour, and those are also what project.search
        reports in the milestone's own fields. Left to the server, a search
        for the colour a milestone is shown in misses it, and a search for
        the colour it happens to have stored finds it.

        So the server is asked for projects that are not milestones, whose
        stored values are the ones shown, and milestones are matched here:
        on the "milestone" icon, and on whether their parent is of a colour
        asked for. The parents are looked up with the same colors constraint,
        which keeps "colour" meaning the colour a project was given - an
        archived parent included, which Phorge shows as "disabled".

        Parameters
        ----------
        constraints : dict
            Every other constraint of the search
        attachments : dict or None
            project.search attachments
        icons, colors : list or None
            What --icon and --color asked for
        milestones : bool or None
            --milestones / --no-milestones
        limit : int or None
            How many projects the caller asked for. A cap on each of the
            two searches, not the answer: both are ordered the same way, so
            the top N of the two together is among the first N of each, and
            the caller truncates once it has ordered them.
        order : str or list, optional
            What to send as project.search's order, see PROJECT_API_ORDERS

        Returns
        -------
        list
            project.search result items, each search in the server's order
        """
        found = []

        if milestones is not True:
            look = {"isMilestone": False}
            if icons:
                look["icons"] = list(icons)
            if colors:
                look["colors"] = list(colors)
            found += fetch_projects(
                self.phab,
                constraints={**constraints, **look},
                attachments=attachments,
                limit=limit,
                order=order,
            )

        if milestones is not False and (not icons or PROJECT_MILESTONE_ICON in icons):
            candidates = fetch_projects(
                self.phab,
                constraints={**constraints, "isMilestone": True},
                attachments=attachments,
                order=order,
            )

            if colors and candidates:
                parent_phids = sorted(
                    {
                        (item["fields"].get("parent") or {}).get("phid")
                        for item in candidates
                    }
                    - {None}
                )
                # Every status: an archived parent still has the colour it
                # was given, which is what a milestone takes from it
                parents = fetch_projects(
                    self.phab,
                    constraints={
                        "phids": parent_phids,
                        "colors": list(colors),
                        "status": PROJECT_STATUS_ALL,
                    },
                )
                matching = {parent["phid"] for parent in parents}
                candidates = [
                    item
                    for item in candidates
                    if (item["fields"].get("parent") or {}).get("phid") in matching
                ]

            found += candidates

        # Not truncated here: two searches concatenated are not in order
        # yet, and cutting before the caller sorts them would drop
        # milestones the ordering puts first.
        return found

    def _resolve_search_spaces(self, spaces):
        """The Space PHIDs a search is narrowed to, or None for every Space.

        Parameters
        ----------
        spaces : list or None
            What --space named. None falls back to PHAB_SPACE, which takes
            the same comma-separated patterns.

        Returns
        -------
        list or None
            Space PHIDs, or None when "*" asked for every Space - or when the
            default could not be resolved, which is warned about rather than
            failing a search nobody asked to narrow.
        """
        from_config = spaces is None

        if from_config:
            default_space = self.conf.get("PHAB_SPACE", "S1") or ""
            spaces = [part.strip() for part in default_space.split(",") if part.strip()]

        if not spaces or "*" in spaces:
            return None

        try:
            space_phids = []
            for space in spaces:
                space_phids.extend(resolve_space_phids(self.phab, space))
        except Exception as e:
            if not from_config:
                raise
            log.warning(
                f"Could not resolve default space '{', '.join(spaces)}': {e}. "
                "Showing all spaces."
            )
            return None

        if from_config:
            log.info(
                f"Filtering to space(s): {', '.join(spaces)} (from PHAB_SPACE). "
                "Projects in other spaces are excluded; "
                "use --space='*' to include all spaces."
            )

        return list(dict.fromkeys(space_phids))

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

    def hashtag_conflicts(self, names, exclude_phid=None):
        """Which of these names a project already answers to the hashtag of.

        A project cannot be deleted or archived through Conduit, so a name
        that Phorge would refuse has to be found **before** the first write
        rather than halfway through a batch - a create spec naming five
        projects must not create four of them and then stop. This answers
        for a whole document at once, which is what a validation pass over
        one asks.

        One request per name and not one for the whole list: `project.search`
        matches any of the slugs it is given, and its answer says which
        project was found and not which name found it. Phorge derives a slug
        from a name by rules phabfive does not restate, so mapping an answer
        back onto the name that caused it would mean guessing at those rules.
        A spec names a handful of projects, and a wrong answer here creates
        an object nobody can remove.

        Parameters
        ----------
        names : iterable of str
            The project names to ask about. A name is asked about once
            however many times it appears.
        exclude_phid : str, optional
            A project that is allowed to hold the hashtag, which is the
            project being edited when a rename is what is being checked.

        Returns
        -------
        dict
            ``{name: project}`` for the names that clash, where the project
            is the `project.search` record already holding that hashtag.
            Empty when every name is free.

        Raises
        ------
        PhabfiveDataException
            The instance could not be asked.
        """
        conflicts = {}

        for name in dict.fromkeys(names):
            try:
                found = (
                    self.phab.project.search(constraints={"slugs": [name]}).get("data")
                    or []
                )
            except Exception as e:
                raise PhabfiveDataException(f"Failed to look up project: {e}")

            taken = [project for project in found if project["phid"] != exclude_phid]

            if taken:
                conflicts[name] = taken[0]

        return conflicts

    @staticmethod
    def hashtag_taken_message(name, other):
        """The sentence a taken hashtag is refused with, wherever it is found.

        One spelling for the command and for a spec's validation pass, so a
        person who has seen it once recognises it in the other place.
        """
        return (
            f"Project name '{name}' generates the same hashtag as "
            f"{describe_project(other)} ({other['fields']['name']}). "
            "Choose a unique name."
        )

    def _hashtag_is_free(self, name, exclude_phid=None):
        """Refuse a name whose hashtag another project already has.

        Phorge derives a project's hashtag from its name and refuses a
        second project with the same one, and it does so when the edit is
        applied. Asked here too, so a dry run reports the clash rather than
        promising a create that would fail. project.search normalises the
        slug it is given the way Phorge derives one, so the name itself is
        what is asked about.
        """
        conflicts = self.hashtag_conflicts([name], exclude_phid=exclude_phid)

        if name in conflicts:
            raise PhabfiveDataException(
                self.hashtag_taken_message(name, conflicts[name])
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
        # Before a single name is looked up: a spec that asks for a
        # milestone with an icon is wrong whatever that icon resolves to,
        # and refusing it here costs no request.
        name = check_project_create_options(
            name,
            parent=parent,
            milestone_of=milestone_of,
            icon=icon,
            color=color,
            slugs=slugs,
        )

        # Here and not in `project_create_transactions`, so that the order
        # the user sees errors in does not change: this has always been the
        # first *request* `project create` makes, before the parent, the
        # members, the Space and the three policies are looked up. Deferring
        # it would answer a command naming both a bad parent and a taken
        # hashtag with the other error, after four more round trips. A
        # milestone is named after its number within its parent, so any
        # number of them may share a name and none is asked about.
        if not milestone_of:
            self._hashtag_is_free(name)

        resolved_parent = None
        if parent:
            parent_project = self.get_project(parent)
            resolved_parent = (
                parent_project["phid"],
                describe_project(parent_project),
            )

        resolved_milestone_of = None
        if milestone_of:
            of_project = self.get_project(milestone_of)
            resolved_milestone_of = (
                of_project["phid"],
                describe_project(of_project),
            )

        resolved_members = None
        if members:
            users = resolve_user_phids(self.phab, members, option="--member")
            resolved_members = [
                (phid, f"@{username or phid}") for phid, username in users.values()
            ]

        resolved_space = self._resolve_space_for_write(space) if space else None

        policies = self._resolve_create_policies(
            visible_to=visible_to,
            editable_by=editable_by,
            joinable_by=joinable_by,
        )

        return self.project_create_transactions(
            name,
            description=description,
            icon=icon,
            color=color,
            slugs=slugs,
            members=resolved_members,
            parent=resolved_parent,
            milestone_of=resolved_milestone_of,
            space=resolved_space,
            policies=policies,
            # Asked above, before anything was looked up, and asking again
            # would be a second `project.search` for the same answer.
            check_hashtag=False,
        )

    def _resolve_create_policies(
        self, visible_to=None, editable_by=None, joinable_by=None
    ):
        """The three policy keys as ``{key: (value, shown)}``, resolved once.

        `_build_policy_edit` answers the same question for a create, but as
        transactions rather than as values, which is one step further than a
        caller building the transactions itself needs.
        """
        transactions, changes = self._build_policy_edit(
            {},
            visible_to=visible_to,
            editable_by=editable_by,
            joinable_by=joinable_by,
            creating=True,
        )
        by_transaction = {
            transaction: key for key, transaction in PROJECT_POLICY_TRANSACTIONS.items()
        }

        return {
            by_transaction[transaction["type"]]: (transaction["value"], change["new"])
            for transaction, change in zip(transactions, changes)
        }

    def project_create_transactions(
        self,
        name,
        *,
        description=None,
        icon=None,
        color=None,
        slugs=None,
        members=None,
        parent=None,
        milestone_of=None,
        space=None,
        policies=None,
        check_hashtag=True,
    ):
        """The transactions that create one project, from values already resolved.

        The PHIDs-only half of :meth:`build_project_create`, and the half a
        create spec calls: a spec resolves every name in the whole document
        in one pass and then builds each project from what came back, where
        the command resolves one project's names and builds it once. Both
        end at these transactions, so neither can grow a field the other
        does not send.

        At most one request is made here - the hashtag check - and none at
        all with ``check_hashtag=False``, for a caller that has already
        asked `hashtag_conflicts` about every name in a document.

        Parameters
        ----------
        name : str
            The project's name.
        description, icon, color : str, optional
            Plain values; none of them names anything to look up.
        slugs : list, optional
            Additional hashtags, with or without their "#".
        members : list, optional
            Either PHIDs, or ``(PHID, shown)`` pairs. The second element is
            what the preview prints, so a PHID never has to reach a person.
        parent, milestone_of, space : str or tuple, optional
            A PHID, or a ``(PHID, shown)`` pair.
        policies : mapping, optional
            ``{"view"|"edit"|"join": value}`` or ``{key: (value, shown)}``,
            already through `phabfive.policy.resolve_policy_value`.
        check_hashtag : bool, optional
            Whether to ask the instance for a project already holding this
            name's hashtag. A milestone is never asked about: it is named
            after its number within its parent, and any number of them may
            share a name.

        Returns
        -------
        tuple
            (transactions, changes), exactly as `build_project_create`.

        Raises
        ------
        PhabfiveConfigException
            The options contradict each other, or the colour is not one.
        PhabfiveDataException
            The name's hashtag is taken.
        """
        name = check_project_create_options(
            name,
            parent=parent,
            milestone_of=milestone_of,
            icon=icon,
            color=color,
            slugs=slugs,
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
        if check_hashtag and not milestone_of:
            self._hashtag_is_free(name)

        add("name", name, "Name")

        if parent:
            add("parent", *_shown(parent, "Parent"))

        if milestone_of:
            add("milestone", *_shown(milestone_of, "Milestone Of"))

        if description:
            add("description", description, "Description")

        if icon:
            add("icon", icon, "Icon")

        if color:
            add("color", color, "Color")

        slugs = [slug.lstrip("#") for slug in slugs or [] if slug.lstrip("#")]
        if slugs:
            # The one collection transaction with no `.add` spelling:
            # `project.edit` replaces the whole list, which is safe only
            # because this creates the project it is sent for. A create spec
            # may therefore never write `slugs:` on an object it did not
            # create - see phabfive/spec/create.py's module docstring.
            add("slugs", slugs, "Hashtags", ", ".join(f"#{slug}" for slug in slugs))

        if members:
            pairs = [_pair(one) for one in members]
            phids = list(dict.fromkeys(phid for phid, _ in pairs))
            shown = {phid: label for phid, label in reversed(pairs)}
            add(
                "members.add",
                phids,
                "Members",
                ", ".join(shown[phid] for phid in phids),
            )

        if space:
            add("space", *_shown(space, "Space"))

        for key, transaction in PROJECT_POLICY_TRANSACTIONS.items():
            asked = (policies or {}).get(key)

            if asked is None:
                continue

            value, label = _pair(asked)
            add(transaction, value, PROJECT_POLICY_LABELS[key], label)

        return transactions, changes

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
        except PhabfiveAPIException as e:
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

        if is_milestone and (icon or color or add_slugs):
            # Phorge refuses an icon or colour transaction on a milestone
            # outright ("invalid type"), so say why before it does
            raise PhabfiveConfigException(
                f"{describe_project(project)} is a milestone, which takes no "
                "--icon, --color or --add-slug: its icon is fixed, its colour "
                "is its parent's, and it has no hashtag"
            )

        check_project_color(color)

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

        if add_members or remove_members:
            member_transactions, member_changes = user_list_edit(
                "members",
                "Members",
                project_member_phids(project),
                added=resolve_user_phids(self.phab, add_members or [], option="--join"),
                removed=resolve_user_phids(
                    self.phab, remove_members or [], option="--leave"
                ),
                sigil="@",
            )
            transactions.extend(member_transactions)
            changes.extend(member_changes)

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
            Phorge answered with rather than as the PhabfiveAPIException around it.
        """
        try:
            self.phab.project.edit(
                transactions=transactions, objectIdentifier=object_identifier
            )
        except PhabfiveAPIException as e:
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


__all__ = [
    "PROJECT_POLICY_LABELS",
    "Project",
    "check_project_create_options",
]
