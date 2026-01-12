# -*- coding: utf-8 -*-

"""PHID resolution functions for Maniphest operations."""

import difflib
import fnmatch
import logging

from phabfive.exceptions import (
    PhabfiveConfigException,
    PhabfiveRemoteException,
)

log = logging.getLogger(__name__)


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
        Project name, hashtag, or wildcard pattern.
        Supports: "*" (all), "prefix*", "*suffix", "*contains*"
        Matches against any project slug/hashtag (case-insensitive).

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

    # Fetch all projects from Phabricator regardless of exact match or not to be able to suggest project names
    log.debug("Fetching all projects from Phabricator")

    # Use project.query to get slugs (project.search doesn't return all hashtags)
    # Note: project.query doesn't support pagination, so we fetch all at once
    slug_to_phid = {}  # Maps each slug/hashtag to its project PHID
    phid_to_primary_name = {}  # Maps PHID to primary project name

    try:
        # project.query returns a Result object with 'data' key containing projects
        projects_result = phab.project.query()
        projects_data = projects_result.get("data", {})

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

    except Exception as e:
        log.error(f"Failed to fetch projects: {e}")
        return []

    log.debug(
        f"Fetched {len(phid_to_primary_name)} total projects with {len(slug_to_phid)} slugs/hashtags from Phabricator"
    )
    # Create case-insensitive lookup mappings for slugs
    lower_slug_to_phid = {slug.lower(): phid for slug, phid in slug_to_phid.items()}
    lower_slug_to_original = {slug.lower(): slug for slug in slug_to_phid.keys()}

    # Check if wildcard search is needed
    has_wildcard = "*" in project

    if has_wildcard:
        if project == "*":
            # Search all projects - return unique PHIDs
            unique_phids = list(set(slug_to_phid.values()))
            log.info(f"Wildcard '*' matched all {len(unique_phids)} projects")
            return unique_phids
        else:
            # Filter slugs by wildcard pattern (case-insensitive)
            # Use set to avoid duplicate PHIDs when multiple slugs of same project match
            matching_phids = set()
            matching_display_names = []

            for slug_lower in lower_slug_to_phid.keys():
                if fnmatch.fnmatch(slug_lower, project.lower()):
                    phid = lower_slug_to_phid[slug_lower]
                    if phid not in matching_phids:
                        matching_phids.add(phid)
                        # Use primary name for display
                        matching_display_names.append(phid_to_primary_name[phid])

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
    if project_lower in lower_slug_to_phid:
        phid = lower_slug_to_phid[project_lower]
        matched_slug = lower_slug_to_original[project_lower]
        primary_name = phid_to_primary_name[phid]
        log.debug(
            f"Found case-insensitive match for project '{project}' -> slug '{matched_slug}' (primary: '{primary_name}')"
        )
        return [phid]
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
        List of project names

    Returns
    -------
    dict
        Dictionary with 'phids' (list of PHIDs) and 'slugs' (list of URL slugs)

    Raises
    ------
    PhabfiveConfigException
        If any project is not found or wildcards are used
    """
    if not project_names:
        return {"phids": [], "slugs": []}

    # Fetch all projects to get both PHIDs and slugs
    try:
        projects_result = phab.project.query()
        projects_data = projects_result.get("data", {})
    except Exception as e:
        raise PhabfiveRemoteException(f"Failed to fetch projects: {e}")

    # Build lookup maps
    name_to_phid = {}
    name_to_slug = {}
    for phid, project_data in projects_data.items():
        primary_name = project_data["name"]
        slugs = project_data.get("slugs", [])
        # Use first slug for URL, or lowercase name if no slugs
        primary_slug = slugs[0] if slugs else primary_name.lower().replace(" ", "-")

        # Map by primary name (case-insensitive)
        name_to_phid[primary_name.lower()] = phid
        name_to_slug[primary_name.lower()] = primary_slug

        # Also map by each slug
        for slug in slugs:
            if slug:
                name_to_phid[slug.lower()] = phid
                name_to_slug[slug.lower()] = primary_slug

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
        else:
            not_found.append(name)

    if not_found:
        raise PhabfiveConfigException(f"Project(s) not found: {', '.join(not_found)}")

    return {"phids": phids, "slugs": slugs}
