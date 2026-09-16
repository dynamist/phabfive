# -*- coding: utf-8 -*-

"""PHID resolution functions for Maniphest operations."""

import difflib
import fnmatch
import logging
import re

from phabfive.exceptions import (
    PhabfiveConfigException,
    PhabfiveRemoteException,
)

log = logging.getLogger(__name__)

# There is no spaces.search endpoint, so Spaces can only be found by asking
# phid.lookup about monograms. It takes a list of names, so the range costs a
# couple of requests rather than one per monogram. The chunk is a hedge: no
# per-request cap on how many names Conduit accepts is documented, and none was
# observed, so raising it to SPACE_PROBE_MAX would make this a single request.
#
# SPACE_PROBE_MAX only bounds matching a wildcard or a name. A Space named by
# monogram is looked up directly, however high its number.
SPACE_PROBE_CHUNK = 50
SPACE_PROBE_MAX = 100

_MONOGRAM = re.compile(r"^S\d+$", re.IGNORECASE)


def parse_plus_separated(values):
    """
    Parse plus-separated values from CLI options.

    Handles both string (single option) and list (multiple options) inputs,
    and splits values on '+' to support syntax like 'ProjectA+ProjectB'.

    Parameters
    ----------
    values : str, list, or None
        Value(s) from docopt - either a single string or a list of strings.
        May contain plus-separated values.

    Returns
    -------
    list
        Flattened list of individual values

    Examples
    --------
    >>> parse_plus_separated("ProjectA+ProjectB")
    ["ProjectA", "ProjectB"]
    >>> parse_plus_separated(["ProjectA+ProjectB", "ProjectC"])
    ["ProjectA", "ProjectB", "ProjectC"]
    >>> parse_plus_separated(["ProjectA", "ProjectB"])
    ["ProjectA", "ProjectB"]
    """
    if not values:
        return []

    # Convert single string to list for uniform processing
    if isinstance(values, str):
        values = [values]

    result = []
    for value in values:
        if "+" in value:
            # Split on + and add each part
            result.extend(part.strip() for part in value.split("+") if part.strip())
        else:
            result.append(value.strip())

    return result


PROJECT_PHID_PREFIX = "PHID-PROJ-"


def lookup_project_by_id(phab, project: str):
    """
    Look up a project by numeric ID (e.g. "8048") or PHID.

    Lets users target projects that are hard to reach by name, such as
    milestones that share a name with milestones of other projects.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    project : str
        Numeric project ID or project PHID (e.g. "PHID-PROJ-abc123").

    Returns
    -------
    dict or None
        The project.search result item, or None if the value is not an
        ID/PHID or no such project exists.
    """
    value = project.strip()
    if value.startswith(PROJECT_PHID_PREFIX):
        constraints = {"phids": [value]}
    elif value.isascii() and value.isdigit():
        constraints = {"ids": [int(value)]}
    else:
        return None

    try:
        result = phab.project.search(constraints=constraints)
    except Exception as e:
        log.debug(f"Project ID lookup failed for '{value}': {e}")
        return None

    data = result.get("data") or []
    return data[0] if data else None


def fetch_projects_by_phid(phab, phids):
    """
    Fetch projects by PHID, ordered by project ID.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    phids : list
        Project PHIDs

    Returns
    -------
    list
        project.search result items, or an empty list if the lookup fails
    """
    try:
        data = phab.project.search(constraints={"phids": list(phids)}).get("data")
    except Exception as e:
        log.debug(f"Project lookup by PHID failed: {e}")
        return []
    return sorted(data or [], key=lambda proj: proj["id"])


def ambiguous_project_message(name, projects):
    """
    Describe the projects an ambiguous project name matches.

    Several projects can share a name, typically milestones such as
    "Sprint 1" in different parent projects. Phorge shows these as
    "Sprint 1 (Development)" and "Sprint 1 (QA)".

    Parameters
    ----------
    name : str
        The project name as given by the user
    projects : list
        project.search result items for the matching projects, or PHIDs
        if the details could not be fetched

    Returns
    -------
    str
        Error message listing each match with its project ID
    """
    described = []
    for proj in projects:
        if isinstance(proj, str):
            described.append(proj)
            continue
        fields = proj["fields"]
        parent = fields.get("parent")
        label = f"{fields['name']} ({parent['name']})" if parent else fields["name"]
        described.append(f"{label}, ID {proj['id']}")

    return (
        f"Project name '{name}' is ambiguous, it matches: {'; '.join(described)}. "
        "Use the project ID or PHID instead."
    )


def resolve_project_phids(phab, project: str) -> list[str]:
    """
    Resolve project name, hashtag, or wildcard pattern to list of project PHIDs.

    Matches against all project slugs/hashtags, not just the primary name.
    This allows users to search using any hashtag associated with a project.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    project : str
        Project name, hashtag, numeric ID, PHID, or wildcard pattern.
        Supports: "*" (all), "prefix*", "*suffix", "*contains*"
        Matches against any project slug/hashtag (case-insensitive).
        A numeric value is treated as a project ID only if no project
        has that name or hashtag. A name shared by several projects is
        rejected as ambiguous; hashtags are unique and always resolve.

    Returns
    -------
    list
        List of project PHIDs matching the pattern. Empty list if no matches.
        Duplicates are automatically removed when multiple slugs match the same project.
    """
    # Validate project parameter
    if not project or project == "":
        log.error("No project name provided. Use '*' to search all projects.")
        return []

    # Check if wildcard search is needed early to optimize API calls
    has_wildcard = "*" in project

    # PHIDs are unambiguous, so look them up directly
    if project.startswith(PROJECT_PHID_PREFIX):
        proj = lookup_project_by_id(phab, project)
        if proj:
            log.debug(
                f"Found project by PHID '{project}' -> '{proj['fields']['name']}'"
            )
            return [proj["phid"]]
        log.error(f"Project '{project}' not found")
        return []

    # For exact match without wildcard, try direct lookup first (more efficient)
    if not has_wildcard:
        log.debug(f"Attempting direct lookup for project '{project}'")
        try:
            # project.search with slugs constraint searches hashtags
            result = phab.project.search(constraints={"slugs": [project]})
            if result.get("data"):
                proj = result["data"][0]
                phid = proj["phid"]
                name = proj["fields"]["name"]
                log.debug(f"Found project '{project}' -> '{name}' (PHID: {phid})")
                return [phid]
        except Exception as e:
            log.debug(f"Direct slug lookup failed: {e}")

        # Also try searching by name in case user provided the display name
        try:
            result = phab.project.search(constraints={"query": project})
            matches = [
                proj
                for proj in result.get("data", [])
                if proj["fields"]["name"].lower() == project.lower()
            ]
            if len(matches) > 1:
                log.error(ambiguous_project_message(project, matches))
                return []
            if matches:
                phid = matches[0]["phid"]
                name = matches[0]["fields"]["name"]
                log.debug(
                    f"Found project by name '{project}' -> '{name}' (PHID: {phid})"
                )
                return [phid]
        except Exception as e:
            log.debug(f"Name query lookup failed: {e}")

        # Fall back to numeric project ID (e.g. "8048" from /project/view/8048/)
        proj = lookup_project_by_id(phab, project)
        if proj:
            log.debug(
                f"Found project by ID '{project}' -> '{proj['fields']['name']}' (PHID: {proj['phid']})"
            )
            return [proj["phid"]]

    # For wildcard searches or when direct lookup fails, fetch all projects with pagination
    log.debug("Fetching all projects from Phabricator with pagination")

    # Use project.query to get all slugs (project.search doesn't return all hashtags)
    # project.query supports pagination via limit/offset parameters
    slug_to_phid = {}  # Maps each slug/hashtag (and primary name) to its project PHID
    hashtag_to_phid = {}  # Maps each lowercased hashtag to its project PHID
    phid_to_primary_name = {}  # Maps PHID to primary project name

    try:
        # Paginate through all projects
        page_size = 100
        offset = 0
        projects_data = {}

        while True:
            # project.query returns a Result object with 'data' key containing projects
            projects_result = phab.project.query(limit=page_size, offset=offset)
            page_data = projects_result.get("data", {})

            if not page_data:
                # No more projects
                break

            # Merge page results into accumulated data
            projects_data.update(page_data)

            # If we got fewer than page_size, we've reached the end
            if len(page_data) < page_size:
                break

            offset += page_size
            log.debug(
                f"Fetched {len(projects_data)} projects so far, fetching next page..."
            )

        # Process all projects (projects_data is a dict keyed by PHID)
        for phid, project_data in projects_data.items():
            primary_name = project_data["name"]
            phid_to_primary_name[phid] = primary_name

            # Always add the primary name as a searchable slug
            slug_to_phid[primary_name] = phid

            # Get all slugs (hashtags) for this project and add them too
            slugs = project_data.get("slugs", [])
            if slugs:
                for slug in slugs:
                    if slug:
                        slug_to_phid[slug] = phid
                        hashtag_to_phid[slug.lower()] = phid

    except Exception as e:
        log.error(f"Failed to fetch projects: {e}")
        return []

    log.debug(
        f"Fetched {len(phid_to_primary_name)} total projects with {len(slug_to_phid)} slugs/hashtags from Phabricator"
    )
    # Create case-insensitive lookup mappings for slugs
    lower_slug_to_phid = {slug.lower(): phid for slug, phid in slug_to_phid.items()}
    lower_slug_to_original = {slug.lower(): slug for slug in slug_to_phid.keys()}

    if has_wildcard:
        if project == "*":
            # Search all projects - return unique PHIDs
            unique_phids = list(phid_to_primary_name)
            log.info(f"Wildcard '*' matched all {len(unique_phids)} projects")
            return unique_phids
        else:
            # Match primary names and hashtags by wildcard pattern (case-insensitive).
            # Names are matched per project, since several projects can share one.
            pattern = project.lower()
            matching_phids = {
                phid
                for phid, primary_name in phid_to_primary_name.items()
                if fnmatch.fnmatch(primary_name.lower(), pattern)
            }
            matching_phids.update(
                phid
                for hashtag, phid in hashtag_to_phid.items()
                if fnmatch.fnmatch(hashtag, pattern)
            )
            matching_display_names = [phid_to_primary_name[p] for p in matching_phids]

            if not matching_phids:
                log.warning(f"Wildcard pattern '{project}' matched no projects")
                return []

            log.info(
                f"Wildcard pattern '{project}' matched {len(matching_phids)} "
                + f"project(s): {', '.join(sorted(matching_display_names))}"
            )
            return list(matching_phids)
    # Exact match - validate project exists (case-insensitive)
    # Match against any slug/hashtag
    log.debug(f"Exact match mode, validating project '{project}'")

    project_lower = project.lower()

    # Hashtags are unique, so they win over primary names
    if project_lower in hashtag_to_phid:
        phid = hashtag_to_phid[project_lower]
        log.debug(
            f"Found hashtag match for project '{project}' (primary: '{phid_to_primary_name[phid]}')"
        )
        return [phid]

    name_matches = sorted(
        phid
        for phid, primary_name in phid_to_primary_name.items()
        if primary_name.lower() == project_lower
    )
    if len(name_matches) > 1:
        projects = fetch_projects_by_phid(phab, name_matches) or name_matches
        log.error(ambiguous_project_message(project, projects))
        return []

    if name_matches:
        log.debug(f"Found case-insensitive name match for project '{project}'")
        return name_matches
    else:
        # Project not found - suggest similar slugs (case-insensitive)
        # Deduplicate suggestions by PHID to avoid showing same project multiple times
        cutoff = 0.6 if len(project_lower) > 3 else 0.4
        similar_slugs = difflib.get_close_matches(
            project_lower, lower_slug_to_phid.keys(), n=10, cutoff=cutoff
        )

        if similar_slugs:
            # Deduplicate by PHID - show primary names with matched slugs
            seen_phids = set()
            unique_suggestions = []

            for slug in similar_slugs:
                phid = lower_slug_to_phid[slug]
                if phid not in seen_phids:
                    seen_phids.add(phid)
                    primary_name = phid_to_primary_name[phid]
                    original_slug = lower_slug_to_original[slug]

                    # Format: "Primary Name (matched-slug)"
                    unique_suggestions.append(f"{primary_name} ({original_slug})")

            # Limit to 3 unique projects
            unique_suggestions = unique_suggestions[:3]

            log.error(
                f"Project '{project}' not found. Did you mean: {', '.join(unique_suggestions)}?"
            )
        else:
            log.error(f"Project '{project}' not found")
        return []


def resolve_user_phid(phab, username):
    """
    Resolve a single username to PHID.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    username : str
        Phabricator username

    Returns
    -------
    str or None
        User PHID, or None if not found
    """
    try:
        result = phab.user.search(constraints={"usernames": [username]})

        if result.get("data"):
            return result["data"][0]["phid"]

        return None
    except Exception as e:
        log.warning(f"Failed to resolve user '{username}': {e}")
        return None


def resolve_user_phids(phab, usernames):
    """
    Resolve multiple usernames to PHIDs.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    usernames : list
        List of Phabricator usernames

    Returns
    -------
    list
        List of user PHIDs

    Raises
    ------
    PhabfiveConfigException
        If any username is not found
    """
    if not usernames:
        return []

    try:
        result = phab.user.search(constraints={"usernames": usernames})

        found_users = {
            user["fields"]["username"].lower(): user["phid"]
            for user in result.get("data", [])
        }

        phids = []
        not_found = []

        for username in usernames:
            phid = found_users.get(username.lower())
            if phid:
                phids.append(phid)
            else:
                not_found.append(username)

        if not_found:
            raise PhabfiveConfigException(f"User(s) not found: {', '.join(not_found)}")

        return phids
    except PhabfiveConfigException:
        raise
    except Exception as e:
        raise PhabfiveRemoteException(f"Failed to resolve users: {e}")


def fetch_project_lookup_maps(phab):
    """
    Fetch all projects and build case-insensitive lookup maps.

    Uses project.query (paginated) because, unlike project.search, it returns
    each project's slugs/hashtags inline. Both the primary name and every slug
    are keyed lowercased so project references match case-insensitively.

    Hashtags are unique and take precedence over primary names. A primary
    name shared by several projects (e.g. milestones named "Sprint 1" in
    different parents) is left out of name_to_phid and listed in
    ambiguous_names instead, so it is never resolved to an arbitrary project.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client

    Returns
    -------
    tuple(dict, dict, dict)
        (name_to_phid, name_to_slug, ambiguous_names) where keys are lowercased
        primary names and slugs. name_to_slug maps each key to the project's
        primary URL slug. ambiguous_names maps each shared primary name to the
        PHIDs of the projects using it.
    """
    try:
        page_size = 100
        offset = 0
        projects_data = {}

        while True:
            projects_result = phab.project.query(limit=page_size, offset=offset)
            page_data = projects_result.get("data", {})

            if not page_data:
                break

            projects_data.update(page_data)

            if len(page_data) < page_size:
                break

            offset += page_size

    except Exception as e:
        raise PhabfiveRemoteException(f"Failed to fetch projects: {e}")

    name_to_phid = {}
    name_to_slug = {}
    name_phids = {}  # lowercased primary name -> PHIDs of projects using it
    primary_slugs = {}  # PHID -> primary URL slug

    for phid, project_data in projects_data.items():
        primary_name = project_data["name"]
        slugs = project_data.get("slugs", [])
        # Use first slug for URL, or lowercase name if no slugs
        primary_slugs[phid] = (
            slugs[0] if slugs else primary_name.lower().replace(" ", "-")
        )
        name_phids.setdefault(primary_name.lower(), []).append(phid)

    # Map by primary name (case-insensitive), unless several projects share it
    ambiguous_names = {}
    for name, phids in name_phids.items():
        if len(phids) > 1:
            ambiguous_names[name] = phids
        else:
            name_to_phid[name] = phids[0]
            name_to_slug[name] = primary_slugs[phids[0]]

    # Also map by each slug; hashtags are unique and win over names
    for phid, project_data in projects_data.items():
        for slug in project_data.get("slugs", []):
            if slug:
                name_to_phid[slug.lower()] = phid
                name_to_slug[slug.lower()] = primary_slugs[phid]
                ambiguous_names.pop(slug.lower(), None)

    return name_to_phid, name_to_slug, ambiguous_names


def resolve_project_phids_for_create(phab, project_names):
    """
    Resolve project names to PHIDs and slugs for task creation.

    Unlike resolve_project_phids() which supports wildcards for search,
    this requires exact matches and raises an error if any project is not found.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    project_names : list
        List of project names, hashtags, numeric IDs, or PHIDs

    Returns
    -------
    dict
        Dictionary with 'phids' (list of PHIDs) and 'slugs' (list of URL slugs)

    Raises
    ------
    PhabfiveConfigException
        If any project is not found, a name matches several projects, or
        wildcards are used
    """
    if not project_names:
        return {"phids": [], "slugs": []}

    name_to_phid, name_to_slug, ambiguous_names = fetch_project_lookup_maps(phab)

    phids = []
    slugs = []
    not_found = []

    for name in project_names:
        # Disallow wildcards for task creation
        if "*" in name:
            raise PhabfiveConfigException(
                f"Wildcards not allowed in project names for task creation: '{name}'"
            )

        name_lower = name.lower()
        if name_lower in name_to_phid:
            phids.append(name_to_phid[name_lower])
            slugs.append(name_to_slug[name_lower])
            continue

        if name_lower in ambiguous_names:
            matches = ambiguous_names[name_lower]
            raise PhabfiveConfigException(
                ambiguous_project_message(
                    name, fetch_projects_by_phid(phab, matches) or matches
                )
            )

        # Fall back to numeric project ID or PHID
        proj = lookup_project_by_id(phab, name)
        if proj:
            phids.append(proj["phid"])
            # Milestones usually have no hashtag, and a slug guessed from the
            # name could link to another project, so only use a real slug
            if proj["fields"].get("slug"):
                slugs.append(proj["fields"]["slug"])
        else:
            not_found.append(name)

    if not_found:
        raise PhabfiveConfigException(f"Project(s) not found: {', '.join(not_found)}")

    return {"phids": phids, "slugs": slugs}


def is_exact_monogram(space: str) -> bool:
    """Whether a Space was named by monogram, e.g. "S1", rather than by name."""
    return bool(space) and bool(_MONOGRAM.match(space))


def _lookup_names(phab, names: list[str]) -> dict:
    """Look up a batch of names, keeping only the ones that came back.

    Phorge answers with a JSON array rather than an object when nothing
    matches, so the response is not always a mapping and has to be unwrapped
    before it is indexed.

    Only names that were asked for are kept, so a lookup can never be credited
    with an answer to a question it was not asked.
    """
    result = phab.phid.lookup(names=names)
    found = getattr(result, "response", result)
    if not isinstance(found, dict):
        return {}
    return {name: found[name] for name in names if name in found}


def _space_entry(monogram: str, data) -> dict | None:
    """One Space in the shape the resolver works with, or None if unusable."""
    if not isinstance(data, dict) or "phid" not in data:
        return None
    return {
        "phid": data["phid"],
        "name": data.get("name", monogram),
        "fullName": data.get("fullName", monogram),
        "uri": data.get("uri", ""),
    }


def fetch_all_spaces(phab) -> dict:
    """Every Space the viewer can see, keyed by monogram in numeric order.

    phid.lookup is policy-filtered: a Space the viewer lacks permission for is
    omitted exactly as though it did not exist. Visible monograms are therefore
    sparse, so the whole range is asked about unconditionally - nothing may
    stop early on the basis of what has been found, or a viewer who can see S1
    and S90 would lose S90.

    Raises
    ------
    PhabfiveRemoteException
        If the lookup fails. A partially probed result is never returned: it is
        indistinguishable from a complete one, which is how a transient error
        would silently shorten somebody's Space list.
    """
    all_spaces = {}

    try:
        for start in range(1, SPACE_PROBE_MAX + 1, SPACE_PROBE_CHUNK):
            stop = min(start + SPACE_PROBE_CHUNK - 1, SPACE_PROBE_MAX)
            names = [f"S{i}" for i in range(start, stop + 1)]
            found = _lookup_names(phab, names)

            for monogram in names:
                entry = _space_entry(monogram, found.get(monogram))
                if entry is not None:
                    all_spaces[monogram] = entry
    except Exception as e:
        raise PhabfiveRemoteException(f"Failed to list spaces: {e}")

    log.debug(f"Found {len(all_spaces)} spaces: {list(all_spaces.keys())}")

    return all_spaces


def resolve_space_phids(phab, space: str, all_spaces: dict | None = None) -> list[str]:
    """
    Resolve a Space name, monogram, or wildcard pattern to list of PHIDs.

    Parameters
    ----------
    phab : Phabricator
        Phabricator API client
    space : str
        Space name (e.g., "Public"), monogram (e.g., "S1"), or wildcard pattern.
        Supports: "*" (all), "S*" (starts with S), "*Public*" (contains Public).
    all_spaces : dict, optional
        Spaces already fetched by `fetch_all_spaces`, to save re-enumerating
        them for each of several patterns. Fetched on demand when omitted.

    Returns
    -------
    list[str]
        List of Space PHIDs matching the pattern

    Raises
    ------
    PhabfiveConfigException
        If no spaces match the pattern
    PhabfiveRemoteException
        If the Spaces could not be fetched
    """
    if not space:
        raise PhabfiveConfigException("No space name provided")

    log.debug(f"Resolving space '{space}' to PHID(s)")

    try:
        # A monogram names one Space outright, so it can be looked up directly.
        # That is the common path - every search resolves PHAB_SPACE, normally
        # "S1" - and unlike enumerating, it has no ceiling to run into.
        if all_spaces is None and is_exact_monogram(space):
            monogram = space.upper()
            entry = _space_entry(
                monogram, _lookup_names(phab, [monogram]).get(monogram)
            )
            if entry is not None:
                log.debug(f"Resolved space '{space}' to PHID: {entry['phid']}")
                return [entry["phid"]]
            # Fall through, so the error can list what the viewer can see

        if all_spaces is None:
            all_spaces = fetch_all_spaces(phab)

        if not all_spaces:
            log.warning("No spaces found in Phabricator instance")
            return []

        # Check if wildcard search is needed
        has_wildcard = "*" in space

        if has_wildcard:
            if space == "*":
                # Return all space PHIDs
                phids = [s["phid"] for s in all_spaces.values()]
                log.info(
                    f"Wildcard '*' matched all {len(phids)} space(s): "
                    f"{', '.join(all_spaces.keys())}"
                )
                return phids
            else:
                # Filter by wildcard pattern (case-insensitive)
                matching_phids = []
                matching_names = []

                for monogram, space_data in all_spaces.items():
                    # Match against monogram, name, or fullName
                    candidates = [
                        monogram.lower(),
                        space_data["name"].lower(),
                        space_data["fullName"].lower(),
                    ]
                    pattern = space.lower()

                    if any(fnmatch.fnmatch(c, pattern) for c in candidates):
                        matching_phids.append(space_data["phid"])
                        matching_names.append(monogram)

                if not matching_phids:
                    raise PhabfiveConfigException(
                        f"Wildcard pattern '{space}' matched no spaces. "
                        f"Available: {', '.join(all_spaces.keys())}"
                    )

                log.info(
                    f"Wildcard pattern '{space}' matched {len(matching_phids)} "
                    f"space(s): {', '.join(matching_names)}"
                )
                return matching_phids

        # Exact match - check monogram first, then name
        space_lower = space.lower()

        # Check monogram (case-insensitive)
        for monogram, space_data in all_spaces.items():
            if monogram.lower() == space_lower:
                log.debug(f"Resolved space '{space}' to PHID: {space_data['phid']}")
                return [space_data["phid"]]

        # Check name (case-insensitive)
        for monogram, space_data in all_spaces.items():
            if space_data["name"].lower() == space_lower:
                log.debug(
                    f"Resolved space '{space}' via name to PHID: {space_data['phid']}"
                )
                return [space_data["phid"]]

        # Not found - suggest available spaces
        raise PhabfiveConfigException(
            f"Space '{space}' not found. Available spaces: {', '.join(all_spaces.keys())}"
        )

    except (PhabfiveConfigException, PhabfiveRemoteException):
        raise
    except Exception as e:
        raise PhabfiveRemoteException(f"Failed to resolve space '{space}': {e}")
