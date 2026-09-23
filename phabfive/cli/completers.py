# -*- coding: utf-8 -*-
"""Shell completion functions for phabfive CLI options."""

from collections import Counter
from typing import List, Optional

from phabfive import cache
from phabfive.constants import (
    MANIPHEST_ORDER_DIRECTIONS,
    MANIPHEST_ORDER_FIELDS,
    PASTE_LANGUAGES,
    POLICY_LABELS,
    PROJECT_COLORS,
    PROJECT_ICONS,
    PROJECT_STATUS_CHOICES,
    REPO_STATUS_CHOICES,
    USER_ROLE_ANY,
    USER_ROLES,
)

# Pattern prefixes for transition filters
PATTERN_PREFIXES = ["in:", "not:in:", "from:", "to:", "been:", "never:"]

# Keywords accepted by the priority and status filters
FILTER_DIRECTION_KEYWORDS = ["raised", "lowered"]

# Keywords accepted when editing a priority
PRIORITY_CHANGE_KEYWORDS = ["raise", "lower"]

# Keywords accepted when editing a column, and by the column filter
COLUMN_DIRECTIONS = ["forward", "backward"]

# Default values used when API is unavailable
DEFAULT_PRIORITY_VALUES = [
    "unbreak",
    "triage",
    "high",
    "normal",
    "low",
    "wish",
]
DEFAULT_STATUS_VALUES = [
    "open",
    "resolved",
    "wontfix",
    "invalid",
    "duplicate",
]


def _get_values_with_api_fallback(fetch_func, default_values: List[str]) -> List[str]:
    """Try to fetch values from API, fall back to defaults.

    Parameters
    ----------
    fetch_func : callable
        Function that takes a phab client and returns a list of values
    default_values : list
        Default values to use if API call fails

    Returns
    -------
    list
        Values from API or defaults
    """
    import contextlib
    import io

    try:
        from phabfive.core import Phabfive

        # Suppress warnings during completion (they break shell completion output)
        with contextlib.redirect_stderr(io.StringIO()):
            pf = Phabfive()
            return fetch_func(pf.phab)
    except Exception:
        return default_values


# Returned by the fallback helper when the API could not be reached at all,
# which a caller has to tell apart from a lookup that found nothing
_FETCH_FAILED = object()


def _fetch_or_none(fetch_func):
    """Return what the API gave, or None when it could not be reached.

    _get_values_with_api_fallback answers any failure with the caller's
    defaults, so an empty list means both "no matches" and "the API blew up".
    Caching the second would silence completion for a whole TTL.
    """
    result = _get_values_with_api_fallback(fetch_func, _FETCH_FAILED)
    return None if result is _FETCH_FAILED else result


def _complete_with_prefixes(incomplete: str, values: List[str]) -> List[str]:
    """Complete values with pattern prefix support.

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed
    values : list
        Available values to complete

    Returns
    -------
    list
        Matching completions
    """
    # If starts with a pattern prefix, complete the value after prefix
    for prefix in PATTERN_PREFIXES:
        if incomplete.startswith(prefix):
            remainder = incomplete[len(prefix) :]
            return [f"{prefix}{v}" for v in values if v.startswith(remainder)]

    # Complete bare values
    completions: list[str] = []
    completions.extend(v for v in values if v.startswith(incomplete))

    # For prefixes, offer full prefix:value combinations instead of bare prefix
    # This allows continued completion after selecting (e.g., "in:" -> "in:high")
    for prefix in PATTERN_PREFIXES:
        if prefix.startswith(incomplete):
            completions.extend(f"{prefix}{v}" for v in values)

    return completions


# Namespaces the instance-wide value lists are cached under, and the one key
# each of them has: unlike users and projects there is nothing to key on but
# the instance, which the cache directory already is
PRIORITY_CACHE_NAMESPACE = "priorities"
STATUS_CACHE_NAMESPACE = "statuses"
SPACE_CACHE_NAMESPACE = "spaces"
VALUES_CACHE_KEY = "all"


def _cached_values(namespace: str, fetch, default: List[str]) -> List[str]:
    """Return a whole value list for the instance, from cache where possible.

    Priorities and statuses are instance configuration: they change when
    somebody reconfigures Phorge, which is why they are fresh for a week
    rather than minutes. Only an answer is remembered - an empty list is not
    one, since no instance has zero priorities or zero statuses, so it means
    the lookup failed even where no exception reached us.
    """
    directory, ttl = cache.context(namespace)

    cached = cache.get(namespace, VALUES_CACHE_KEY, ttl=ttl, directory=directory)
    if isinstance(cached, list) and cached:
        return cached

    values = _fetch_or_none(fetch)
    if not values:
        return default

    cache.set(namespace, VALUES_CACHE_KEY, values, ttl=ttl, directory=directory)
    return values


def _get_priorities() -> List[str]:
    """Get priority names - tries API first, falls back to defaults."""
    from phabfive.maniphest.fetchers import get_api_priority_names

    return _cached_values(
        PRIORITY_CACHE_NAMESPACE,
        get_api_priority_names,
        DEFAULT_PRIORITY_VALUES,
    )


def _fetch_status_keys(phab) -> List[str]:
    """Fetch the status keys, letting a failed lookup fail.

    Deliberately not get_api_status_map: that answers a broken lookup with an
    invented map, which the commands need - they have to have a map to
    validate and display with - but completion must not mistake for the
    server's answer. DEFAULT_STATUS_VALUES is what completion falls back to,
    and a caller that remembers the answer must not write fiction down.
    """
    return list(phab.maniphest.querystatuses().get("statusMap", {}).keys())


def _get_statuses() -> List[str]:
    """Get status keys (e.g., "open", "resolved") - tries API first, falls back to defaults."""
    return _cached_values(
        STATUS_CACHE_NAMESPACE, _fetch_status_keys, DEFAULT_STATUS_VALUES
    )


def _fetch_spaces(phab) -> list:
    """Every visible Space, as [monogram, name] pairs.

    fetch_all_spaces raises rather than answering a half-probed instance with
    a shortened list, which is what makes its answer safe to remember: a
    truncated Space list cannot be told apart from a complete one once it is
    written down.
    """
    from phabfive.maniphest.resolvers import fetch_all_spaces

    return [
        [monogram, entry["name"]] for monogram, entry in fetch_all_spaces(phab).items()
    ]


def _get_spaces() -> list:
    """Every visible Space - tries the API, offers nothing when it fails.

    There is no list of default Spaces to fall back to the way there is for
    priorities and statuses: which Spaces exist is particular to the instance,
    and an invented one would complete to a value that cannot resolve. An
    instance with no Spaces at all answers with an empty list, which is not
    remembered, so it is probed on each tab - harmless, since nothing on such
    an instance takes --space anyway.
    """
    return _cached_values(SPACE_CACHE_NAMESPACE, _fetch_spaces, [])


def _space_completions(incomplete: str) -> list:
    """The Spaces matching what has been typed, as (value, description) pairs.

    A monogram is always offered, described by the Space's name. The name is
    offered too once something has been typed that it matches, since it
    resolves just as well and is what somebody is likely to be typing; with
    nothing typed yet the monograms alone are the shorter list, and carry the
    names as their descriptions anyway.

    A name several Spaces share is left out, and only their monograms offered,
    the way an ambiguous project name is: completing to it would earn nothing
    but the "Space 'X' is ambiguous" error.
    """
    spaces = [(monogram, name) for monogram, name in _get_spaces()]
    shared = Counter(name.lower() for _, name in spaces)

    typed = incomplete.lower()
    pairs = []

    for monogram, name in spaces:
        if monogram.lower().startswith(typed):
            pairs.append((_in_typed_case(incomplete, monogram), name))

        if typed and shared[name.lower()] == 1 and name.lower().startswith(typed):
            pairs.append((_in_typed_case(incomplete, name), monogram))

    return pairs


def complete_space(incomplete: str) -> list[str | tuple[str, str]]:
    """Complete Spaces for placing a task in one.

    Used by maniphest create --space and edit --space, where the value has to
    name exactly one Space, so wildcards are not offered.

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching monograms and names, as (value, description) tuples
    """
    return _as_completions(_space_completions(incomplete))


def complete_space_filter(incomplete: str) -> list[str | tuple[str, str]]:
    """Complete a Space filter, which takes a comma-separated list.

    Used by maniphest search --space, where "S1,S3" means "tasks in either".
    Only the Space after the last comma is completed, and the ones before it
    are kept in the offered value, since the shell replaces the whole word. A
    wildcard is left alone: "*rch*" is a prefix of nothing.

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching Spaces, each prefixed with what was already typed
    """
    typed, comma, last = incomplete.rpartition(",")

    return _as_completions(_space_completions(last), prefix=f"{typed}{comma}")


def _starting_with(incomplete: str, values: List[str]) -> List[str]:
    """Return the values that start with the incomplete text."""
    return [v for v in values if v.startswith(incomplete)]


def complete_priority(incomplete: str) -> List[str]:
    """Complete priority values for setting a priority (maniphest create).

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching priority completions
    """
    return _starting_with(incomplete, _get_priorities())


def complete_priority_change(incomplete: str) -> List[str]:
    """Complete priority values for changing a priority (edit).

    Like complete_priority, plus the raise/lower navigation keywords.
    """
    return _starting_with(incomplete, _get_priorities() + PRIORITY_CHANGE_KEYWORDS)


def complete_priority_filter(incomplete: str) -> List[str]:
    """Complete priority filter patterns (maniphest search).

    Offers priority names, pattern prefixes (e.g., "in:high") and the
    raised/lowered keywords.
    """
    completions = _complete_with_prefixes(incomplete, _get_priorities())
    completions.extend(_starting_with(incomplete, FILTER_DIRECTION_KEYWORDS))
    return completions


def complete_status(incomplete: str) -> List[str]:
    """Complete status values for setting a status (create, edit).

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching status completions
    """
    return _starting_with(incomplete, _get_statuses())


def complete_status_filter(incomplete: str) -> List[str]:
    """Complete status filter patterns (maniphest search).

    Offers status names, pattern prefixes (e.g., "in:open"), the
    raised/lowered keywords and the open/closed/any scope keywords.
    """
    from phabfive.transitions.status import STATUS_SCOPE_KEYWORDS

    completions = _complete_with_prefixes(incomplete, _get_statuses())
    completions.extend(_starting_with(incomplete, FILTER_DIRECTION_KEYWORDS))
    completions.extend(_starting_with(incomplete, STATUS_SCOPE_KEYWORDS))
    return completions


def _board_context(ctx) -> Optional[str]:
    """Get the board name given with --tag, if any.

    --tag is a single value on search and edit but repeatable and
    comma-separated on create, where the first tag is the board context -
    the same one the command itself picks, so "--tag=Board,Other" completes
    columns of Board.
    """
    from phabfive.options import split_list_option

    values = split_list_option(ctx.params.get("tag") if ctx else None)
    return values[0] if values else None


def _matching_board_columns(ctx, incomplete: str) -> List[str]:
    """Return column names on the --tag board that match the incomplete text."""
    tag_value = _board_context(ctx)
    if not tag_value:
        return []

    incomplete_lower = incomplete.lower()
    return [
        c
        for c in _get_board_columns(tag_value)
        if c.lower().startswith(incomplete_lower)
    ]


def complete_column(ctx, args: List[str], incomplete: str) -> List[str]:
    """Complete column names from the board specified by --tag (maniphest create).

    Parameters
    ----------
    ctx : click.Context
        Click context with parsed parameters
    args : list
        Command line arguments (unused, ctx.params preferred)
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching column completions
    """
    return _matching_board_columns(ctx, incomplete)


def complete_column_change(ctx, args: List[str], incomplete: str) -> List[str]:
    """Complete column names for moving a task (edit).

    Like complete_column, plus the forward/backward navigation keywords.
    """
    incomplete_lower = incomplete.lower()
    completions = [d for d in COLUMN_DIRECTIONS if d.startswith(incomplete_lower)]
    completions.extend(_matching_board_columns(ctx, incomplete))
    return completions


def complete_column_filter(ctx, args: List[str], incomplete: str) -> List[str]:
    """Complete column filter patterns (maniphest search).

    Offers the forward/backward keywords, pattern prefixes, the wildcard
    and, if --tag is given, column names from that board.
    """
    incomplete_lower = incomplete.lower()
    completions = [d for d in COLUMN_DIRECTIONS if d.startswith(incomplete_lower)]
    completions.extend(p for p in PATTERN_PREFIXES if p.startswith(incomplete))
    if not incomplete or "*".startswith(incomplete):
        completions.append("*")
    completions.extend(_matching_board_columns(ctx, incomplete))
    return completions


def _get_board_columns(tag_name: str) -> List[str]:
    """Fetch column names for a board/project.

    Parameters
    ----------
    tag_name : str
        Project/board name

    Returns
    -------
    list
        Column names, or empty list if not found
    """
    import contextlib
    import io

    try:
        from phabfive.core import Phabfive
        from phabfive.maniphest.resolvers import resolve_project_phids

        with contextlib.redirect_stderr(io.StringIO()):
            pf = Phabfive()
            # Resolve project name to PHID
            phids = resolve_project_phids(pf.phab, tag_name)
            if not phids:
                return []

            board_phid = phids[0]  # Use first match

            # Fetch columns for this board
            result = pf.phab.project.column.search(
                constraints={"projects": [board_phid]}
            )

            if not result.get("data"):
                return []

            # Extract column names
            return [col["fields"]["name"] for col in result["data"]]
    except Exception:
        return []


# Stop fetching projects for --tag completion after this many matches
TAG_COMPLETION_LIMIT = 500


def _fetch_projects_named(phab, incomplete: str) -> list:
    """Fetch projects whose name has a word starting with the incomplete text.

    Uses the project.search "name" constraint so matching happens on the
    server, and follows the result cursor up to TAG_COMPLETION_LIMIT.
    """
    constraints = {"name": incomplete} if incomplete.strip() else {}
    projects: list = []
    after = None

    while len(projects) < TAG_COMPLETION_LIMIT:
        kwargs = {"constraints": constraints, "limit": 100}
        if after:
            kwargs["after"] = after
        result = phab.project.search(**kwargs)
        projects.extend(result.get("data", []))
        after = (result.get("cursor") or {}).get("after")
        if not after:
            break

    return projects


def _project_records(projects: list) -> list:
    """Reduce project.search results to the fields completion actually reads.

    The raw objects carry phids, dates and policies that no completer looks
    at. The parent is flattened to its name, which is all the descriptions
    below need.
    """
    records = []
    for proj in projects:
        fields = proj.get("fields", {})
        parent = fields.get("parent") or {}
        records.append(
            {
                "id": proj.get("id"),
                "name": fields.get("name", ""),
                "parent": parent.get("name") or "",
            }
        )
    return records


# Namespace the project lookups are cached under
PROJECT_CACHE_NAMESPACE = "projects"


def _matching_projects(records: list, text: str) -> list:
    """Return the records whose name starts with the lowercased text.

    This is the same predicate complete_tag offers on, deliberately: see
    _narrowed_projects_from_cache for why reusing it is what makes a narrowed
    answer equal to an uncached one rather than merely contain it.
    """
    return [record for record in records if record["name"].lower().startswith(text)]


def _narrowed_projects_from_cache(text: str, directory, ttl):
    """Return records for text out of a cached entry, or None.

    The project.search "name" constraint matches word prefixes, and it
    tokenises the query too, so "sprint 1" is two constraints rather than one
    string. If P is a prefix of T then every constraint P imposes is implied
    by the corresponding one in T, so everything matching T also matches P: a
    cached result for a shorter prefix is a superset of the one being asked
    for, and one lookup answers every prefix extending it.

    The filter applied to it is a whole-name prefix match rather than an
    attempt to reproduce that tokenisation locally. Phorge splits on non-word
    characters, so a search for "Core" really does return "GUNNAR-Core" and a
    naive split() here would disagree. Reusing the predicate complete_tag
    offers on avoids the question: a name starting with T matches the server's
    constraint for T as well as for P, so the narrowed answer is exactly the
    uncached one, not a superset of it. Names the server returned for T but
    that do not start with T are dropped either way.

    One thing makes that unsound, and it is checked here: a truncated entry
    stopped at TAG_COMPLETION_LIMIT, so it is an arbitrary subset rather than
    the whole answer, and nothing can be narrowed out of it. It still answers
    its own key, which is exactly what an uncached lookup would have returned.
    """
    for length in range(len(text), -1, -1):
        prefix = text[:length]
        entry = cache.get(PROJECT_CACHE_NAMESPACE, prefix, ttl=ttl, directory=directory)
        if entry is cache.MISS or not isinstance(entry, dict):
            continue

        records = entry.get("records")
        if records is None:
            continue

        if prefix == text:
            return records

        if entry.get("truncated"):
            continue

        return _matching_projects(records, text)

    return None


def _cached_project_records(incomplete: str) -> list:
    """Return project records for the typed text, from cache where possible."""
    text = _cache_text(incomplete)

    # Resolved once: every probe below would otherwise re-read the config,
    # which costs more than the lookups it is meant to save
    directory, ttl = cache.context(PROJECT_CACHE_NAMESPACE)

    cached = _narrowed_projects_from_cache(text, directory, ttl)
    if cached is not None:
        return cached

    projects = _fetch_or_none(lambda phab: _fetch_projects_named(phab, incomplete))
    if projects is None:
        # The API was unavailable; offer nothing, and do not remember that
        return []

    records = _project_records(projects)
    cache.set(
        PROJECT_CACHE_NAMESPACE,
        text,
        {"records": records, "truncated": len(projects) >= TAG_COMPLETION_LIMIT},
        ttl=ttl,
        directory=directory,
    )
    return records


def _project_completions(incomplete: str) -> list:
    """Return (project name, description or None) pairs matching the typed text.

    Matching is case-insensitive. Completions follow the case the user typed
    (e.g. "gun" -> "gunnar-core"), because Typer drops completions that don't
    start with the typed text; project names resolve case-insensitively, so
    the value still works.

    A name shared by several projects (e.g. milestones named "Sprint 1" in
    different parents) is offered once, with a description listing the
    project IDs to use instead. Milestones and subprojects are described
    with their parent.
    """
    # No default values for tags - they are instance-specific
    records = _cached_project_records(incomplete)

    by_name: dict[str, list] = {}
    for record in _matching_projects(records, incomplete.lower()):
        by_name.setdefault(record["name"].lower(), []).append(record)

    pairs: list[tuple[str, str | None]] = []
    for _, matches in sorted(by_name.items()):
        value = _in_typed_case(incomplete, matches[0]["name"])

        if len(matches) > 1:
            ids = ", ".join(
                _describe_project_id(record)
                for record in sorted(matches, key=lambda record: record["id"])
            )
            pairs.append((value, f"ambiguous, use the ID: {ids}"))
        elif matches[0]["parent"]:
            pairs.append((value, f"in {matches[0]['parent']}"))
        else:
            pairs.append((value, None))

    return pairs


def complete_tag(incomplete: str) -> list[str | tuple[str, str]]:
    """Complete tag (project) names from API.

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching project names, as (name, description) tuples where a
        description applies. Descriptions are shown by zsh and fish.
    """
    return _as_completions(_project_completions(incomplete))


def complete_tag_list(incomplete: str) -> list[str | tuple[str, str]]:
    """Complete a tag option that takes a comma-separated list.

    Used by the value-adding --tag of maniphest create and paste create/edit,
    where "Backend,QA" adds both. Only the tag after the last comma is
    completed, and the ones before it are kept in the offered value, since
    the shell replaces the whole word.

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching project names, each prefixed with the tags already typed
    """
    typed, comma, last = incomplete.rpartition(",")

    return _as_completions(_project_completions(last), prefix=f"{typed}{comma}")


def _in_typed_case(incomplete: str, name: str) -> str:
    """Return name so that it starts with the incomplete text as typed."""
    if name.startswith(incomplete):
        return name
    if incomplete.islower():
        return name.lower()
    if incomplete.isupper():
        return name.upper()
    return incomplete + name[len(incomplete) :]


def _describe_project_id(record) -> str:
    """Describe a project as its ID plus parent name, e.g. "9 (QA)"."""
    parent = record["parent"]
    return f"{record['id']} ({parent})" if parent else str(record["id"])


# Stop fetching users for username completion after this many matches
USER_COMPLETION_LIMIT = 500

# The only completion that starts with "@"; every option taking a username
# also takes this shortcut for the current user
ME_SHORTCUT = "@me"


def _fetch_users_named(phab, incomplete: str, include_disabled: bool) -> list:
    """Fetch users whose username or real name contains the incomplete text.

    Uses the user.search "nameLike" constraint so matching happens on the
    server, and follows the result cursor up to USER_COMPLETION_LIMIT.
    nameLike is a substring match over both the username and the real name,
    so it returns a superset of the usernames that start with the text.
    """
    constraints: dict[str, str | bool] = {}
    if incomplete.strip():
        constraints["nameLike"] = incomplete
    if not include_disabled:
        constraints["isDisabled"] = False

    users: list = []
    after = None

    while len(users) < USER_COMPLETION_LIMIT:
        kwargs = {"constraints": constraints, "limit": 100}
        if after:
            kwargs["after"] = after
        result = phab.user.search(**kwargs)
        users.extend(result.get("data", []))
        after = (result.get("cursor") or {}).get("after")
        if not after:
            break

    return users


# Namespace the user lookups are cached under
USER_CACHE_NAMESPACE = "users"


def _cache_text(incomplete: str) -> str:
    """Normalise the typed text into the query it actually produces.

    Both _fetch_users_named and _fetch_projects_named drop their name
    constraint for whitespace-only input, so that has to key the same entry as
    an empty string. Matching is case-insensitive, so the key is lowercased.
    """
    return incomplete.lower() if incomplete.strip() else ""


def _user_cache_key(text: str, include_disabled: bool) -> str:
    """Key one user.search query. The scope is part of it: a lookup that left
    disabled accounts out is not an answer to one that wants them."""
    scope = "all" if include_disabled else "enabled"
    return f"{scope}|{text}"


def _user_records(users: list) -> list:
    """Reduce user.search results to the fields completion actually reads.

    The raw objects carry phid, id, dates and policies - roughly ten times the
    bytes, for nothing any completer looks at.
    """
    records = []
    for user in users:
        fields = user.get("fields", {})
        records.append(
            {
                "username": fields.get("username", ""),
                "realName": fields.get("realName") or "",
                "disabled": "disabled" in (fields.get("roles") or []),
            }
        )
    return records


def _narrowed_users_from_cache(text: str, include_disabled: bool, directory, ttl):
    """Return records for text out of a cached entry, or None.

    The nameLike constraint is a case-insensitive substring match, so if P is
    a prefix of T then every user matching T also matches P. A cached result
    for a shorter prefix is therefore a superset of the one being asked for,
    and can be filtered down locally instead of queried again - which is what
    makes every keystroke after the first one free.

    Two things make that unsound, and both are checked here:

    - a truncated entry stopped at USER_COMPLETION_LIMIT, so it is an
      arbitrary subset rather than the whole answer, and nothing can be
      narrowed out of it. It still answers its own key, which is exactly what
      an uncached lookup would have returned.
    - an entry that excluded disabled accounts is not a superset of one that
      includes them, so the scope has to match.
    """
    for length in range(len(text), -1, -1):
        prefix = text[:length]
        entry = cache.get(
            USER_CACHE_NAMESPACE,
            _user_cache_key(prefix, include_disabled),
            ttl=ttl,
            directory=directory,
        )
        if entry is cache.MISS or not isinstance(entry, dict):
            continue

        records = entry.get("records")
        if records is None:
            continue

        if prefix == text:
            return records

        if entry.get("truncated"):
            continue

        # Apply what the server would have applied for the longer text
        return [
            record
            for record in records
            if text in record["username"].lower() or text in record["realName"].lower()
        ]

    return None


def _cached_user_records(incomplete: str, include_disabled: bool) -> list:
    """Return user records for the typed text, from cache where possible."""
    text = _cache_text(incomplete)

    # Resolved once: every probe below would otherwise re-read the config,
    # which costs more than the lookups it is meant to save
    directory, ttl = cache.context(USER_CACHE_NAMESPACE)

    cached = _narrowed_users_from_cache(text, include_disabled, directory, ttl)
    if cached is not None:
        return cached

    users = _fetch_or_none(
        lambda phab: _fetch_users_named(phab, incomplete, include_disabled)
    )
    if users is None:
        # The API was unavailable; offer nothing, and do not remember that
        return []

    records = _user_records(users)
    cache.set(
        USER_CACHE_NAMESPACE,
        _user_cache_key(text, include_disabled),
        {"records": records, "truncated": len(users) >= USER_COMPLETION_LIMIT},
        ttl=ttl,
        directory=directory,
    )
    return records


def _user_completions(
    incomplete: str, include_disabled: bool, shortcuts: bool = True
) -> list:
    """Return (username, real name or None) pairs matching the typed text.

    Only usernames that start with the typed text are offered, because Typer
    drops the rest; the real name is offered as a description instead, so
    searching for "Bergstrom" cannot complete "sonja.bergstrom".

    `shortcuts` is False for an option that takes a **stored** username and
    nothing else - `user search --usernames`, whose constraint matches the
    name exactly. Offering `@me` there would hand the shell a value the
    command is documented to refuse.
    """
    if incomplete.startswith("@"):
        # No username starts with "@", so the API has nothing to add here
        if not shortcuts:
            return []
        return [(ME_SHORTCUT, "yourself")] if ME_SHORTCUT.startswith(incomplete) else []

    records = _cached_user_records(incomplete, include_disabled)

    # @me is only offered before a username is typed, since it can never be
    # a prefix of one
    pairs: list[tuple[str, str | None]] = (
        [(ME_SHORTCUT, "yourself")] if shortcuts and not incomplete else []
    )

    incomplete_lower = incomplete.lower()
    for record in sorted(records, key=lambda r: r["username"].lower()):
        username = record["username"]
        if not username.lower().startswith(incomplete_lower):
            continue
        if record["disabled"] and not include_disabled:
            # A cached wider lookup can carry accounts this caller must not offer
            continue
        pairs.append((_in_typed_case(incomplete, username), record["realName"] or None))

    return pairs


def _as_completions(pairs, prefix: str = "") -> list[str | tuple[str, str]]:
    """Format (value, description) pairs for Typer, dropping empty descriptions."""
    return [
        (f"{prefix}{value}", description) if description else f"{prefix}{value}"
        for value, description in pairs
    ]


def complete_user(incomplete: str) -> list[str | tuple[str, str]]:
    """Complete usernames for setting an assignee or subscriber.

    Used by --assign and --subscribe, where the value has to name an account
    that can be assigned work, so disabled accounts are left out. Matching is
    case-insensitive and completions follow the case the user typed, the same
    way complete_tag does; the user.search "usernames" constraint that
    resolves them is case-insensitive too. Real names are offered as
    descriptions, which zsh and fish show.

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching usernames plus the @me shortcut, as (value, description)
        tuples where a real name is known
    """
    return _as_completions(_user_completions(incomplete, include_disabled=False))


def complete_user_list(incomplete: str) -> list[str | tuple[str, str]]:
    """Complete usernames for an option that adds a comma-separated list.

    Used by --subscribe, where "@me,alice" adds both. Like complete_user this
    leaves disabled accounts out. Only the name after the last comma is
    completed, and the names before it are kept in the offered value, since
    the shell replaces the whole word.

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching usernames, each prefixed with the names already typed
    """
    typed, comma, last = incomplete.rpartition(",")
    pairs = _user_completions(last, include_disabled=False)
    return _as_completions(pairs, prefix=f"{typed}{comma}")


def complete_user_filter(incomplete: str) -> list[str | tuple[str, str]]:
    """Complete usernames for a filter that takes a single username.

    Used by paste search --author. Unlike complete_user this offers disabled
    accounts, because filtering for what a former colleague left behind is a
    reasonable thing to search for.

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching usernames plus the @me shortcut
    """
    return _as_completions(_user_completions(incomplete, include_disabled=True))


def complete_user_list_filter(incomplete: str) -> list[str | tuple[str, str]]:
    """Complete a user filter that takes a comma-separated list.

    Used by maniphest search --assigned and --author, where "@me,user1,user2"
    means "any of these". Only the name after the last comma is completed, and
    the names before it are kept in the offered value, since the shell replaces
    the whole word.

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching usernames, each prefixed with the names already typed
    """
    typed, comma, last = incomplete.rpartition(",")
    pairs = _user_completions(last, include_disabled=True)
    return _as_completions(pairs, prefix=f"{typed}{comma}")


def complete_username_list(incomplete: str) -> list[str | tuple[str, str]]:
    """Complete a comma-separated list of **stored** usernames.

    `user search --usernames` sends the `usernames` constraint, which matches
    the name a user is stored under exactly, so `phabfive.users.user_name_list`
    refuses anything starting with "@". Offering `@me` here would therefore
    complete a value guaranteed to exit 1, which is why this is a separate
    completer from :func:`complete_user_list_filter` rather than a flag on it.

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching usernames, each prefixed with the names already typed, and
        no shortcut
    """
    typed, comma, last = incomplete.rpartition(",")
    pairs = _user_completions(last, include_disabled=True, shortcuts=False)
    return _as_completions(pairs, prefix=f"{typed}{comma}")


def complete_language(incomplete: str) -> List[str]:
    """Complete programming language values for syntax highlighting.

    Uses the default languages from Phabricator/Phorge pygments.dropdown-choices.

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching language completions
    """
    incomplete_lower = incomplete.lower()
    return [lang for lang in PASTE_LANGUAGES if lang.startswith(incomplete_lower)]


# Credential types accepted by passphrase search --type ("ssh" is an alias for "key")
PASSPHRASE_TYPES = ["password", "token", "key", "ssh", "note"]


def _complete_fixed(incomplete: str, values: List[str]) -> List[str]:
    """Complete from a fixed list of values."""
    return [v for v in values if v.startswith(incomplete)]


def complete_cache_namespace(incomplete: str) -> list[str | tuple[str, str]]:
    """Complete the namespaces `cache clear` accepts.

    Every known namespace is offered, not only the ones with something in
    them, so the vocabulary is discoverable and clearing an empty one is
    harmless. What is cached shows up as the description instead.

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching namespaces, described by what they hold
    """
    from phabfive import cache

    try:
        described = {
            entry["Namespace"]: entry for entry in cache.describe()["Namespaces"]
        }
    except (OSError, KeyError):
        described = {}

    completions: list[str | tuple[str, str]] = []
    for namespace in cache.known_namespaces():
        if not namespace.startswith(incomplete):
            continue

        entry = described.get(namespace)
        if entry is None:
            completions.append((namespace, "nothing cached"))
            continue

        records = entry.get("Records")
        held = "" if records is None else f", {records} records"
        lookups = entry["Lookups"]
        unit = "lookup" if lookups == 1 else "lookups"
        completions.append((namespace, f"{lookups} {unit}{held}"))

    return completions


def complete_cached_host(incomplete: str) -> List[str]:
    """Complete the hosts that have something cached, for cache clear --url.

    Reads only the cache directory, so it works with no token, no
    configuration and no reachable server, which is the situation this
    option exists for.

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching cached hostnames
    """
    from phabfive import cache

    try:
        hosts = cache.cached_hosts()
    except OSError:
        return []

    # A scheme is accepted but never cached, so completions have to carry back
    # whatever was typed: typer keeps only values that start with it
    # (typer/core.py), so returning a bare host for "http://ph" offers nothing
    scheme, separator, typed = incomplete.rpartition("//")
    prefix = scheme + separator

    return [f"{prefix}{host}" for host in hosts if host.startswith(typed)]


def complete_policy(incomplete: str) -> list[str | tuple[str, str]]:
    """Complete a policy option: --visible-to, --editable-by and --can-push.

    The four keywords are a constant rather than a lookup, because Phorge has
    no policy.query endpoint to ask. Past them the grammar branches on the
    first character, and so does this: "#" completes project names and "@"
    completes usernames, from the same lookups --tag and --assign use.

    A project's display name is offered rather than its hashtag, and works:
    the project.search "slugs" constraint normalises what it is given, so
    "#Human Resources" resolves the same project "#human_resources" does.

    @me is offered too, since a policy resolves it to whoever is running the
    command. _user_completions spells it with its "@", so it is added here
    rather than passed through, which would prefix it twice.

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching keywords, project names or usernames, described where there
        is something to say
    """
    if incomplete.startswith("#"):
        return _as_completions(_project_completions(incomplete[1:]), prefix="#")

    if incomplete.startswith("@"):
        pairs = (
            [(ME_SHORTCUT[1:], "yourself")]
            if ME_SHORTCUT.startswith(incomplete)
            else []
        )
        pairs += [
            (username, description)
            for username, description in _user_completions(
                incomplete[1:], include_disabled=False
            )
            if username != ME_SHORTCUT
        ]
        return _as_completions(pairs, prefix="@")

    keywords = [
        (keyword, label)
        for keyword, label in POLICY_LABELS.items()
        if keyword.startswith(incomplete)
    ]

    # Offered as the start of a value rather than a whole one, so the two
    # halves of the grammar the keywords do not cover are discoverable
    prefixes = [
        (prefix, description)
        for prefix, description in (("#", "a project"), ("@", "a user"))
        if not incomplete
    ]

    return _as_completions(keywords + prefixes)


def complete_repo_status(incomplete: str) -> List[str]:
    """Complete the repository status filter for diffusion repo list.

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching status completions
    """
    return _complete_fixed(incomplete, REPO_STATUS_CHOICES + ["all"])


# The icons in use on the instance, cached for as long as the priorities and
# statuses are, and for the same reason: they are instance configuration
PROJECT_ICON_CACHE_NAMESPACE = "project-icons"


def _fetch_project_icons(phab) -> List[str]:
    """The stock icons, plus every icon a project on the instance carries.

    The lookup itself is `phabfive.project.core.icons_in_use`: the same
    question is asked by `phabfive spec validate`, which may not import
    anything under `phabfive.cli`, so it lives in library code and this is
    the completion's way in. Imported inside the function because completion
    runs on every TAB and most completions never ask the server at all.

    Lets a failed lookup fail, so that _cached_values falls back to the
    stock list without writing it down as the instance's answer.
    """
    from phabfive.project.core import icons_in_use

    return icons_in_use(phab)


def complete_project_icon(incomplete: str) -> List[str]:
    """Complete a project icon: the stock ones and any in use on the instance."""
    icons = _cached_values(
        PROJECT_ICON_CACHE_NAMESPACE, _fetch_project_icons, PROJECT_ICONS
    )

    return _complete_fixed(incomplete, icons)


def complete_project_color(incomplete: str) -> List[str]:
    """Complete a project colour, from the set Phorge fixes in its code."""
    return _complete_fixed(incomplete, PROJECT_COLORS)


def complete_project_status(incomplete: str) -> List[str]:
    """Complete the project status filter for project search --status."""
    return _complete_fixed(incomplete, PROJECT_STATUS_CHOICES)


def complete_user_role(incomplete: str) -> List[str]:
    """Complete a comma-separated list of the roles user.search reports.

    Only the role after the last comma is completed; the ones before it are
    kept as typed.
    """
    typed, comma, last = incomplete.rpartition(",")
    already = set(typed.split(",")) if typed else set()

    return [
        f"{typed}{comma}{role}"
        for role in USER_ROLES
        if role.startswith(last) and role not in already
    ]


def complete_user_role_or_any(incomplete: str) -> List[str]:
    """Complete ``user search --role``, which also takes ``any``.

    ``any`` is offered only as the first value, since it asks for every user
    and combining it with a role would mean just that role.
    """
    completions = complete_user_role(incomplete)
    if "," not in incomplete and USER_ROLE_ANY.startswith(incomplete):
        completions.append(USER_ROLE_ANY)
    return completions


def forget_projects(icons=False) -> None:
    """Drop the cached project completions after a project was written.

    A project created or renamed would otherwise not complete, or complete
    under its old name, until the cache expired. Only the namespaces are
    dropped - nothing a write returns is ever put into the cache - and it is
    best effort, like every cache operation: a write that succeeded must not
    be reported as failed because a cache file could not be removed.

    Parameters
    ----------
    icons : bool, optional
        Drop the cached icons too, for a write that set one - so a custom
        icon just used is offered straight away
    """
    namespaces = [PROJECT_CACHE_NAMESPACE]

    if icons:
        namespaces.append(PROJECT_ICON_CACHE_NAMESPACE)

    try:
        cache.clear(namespaces=namespaces)
    except Exception:
        pass


def complete_passphrase_type(incomplete: str) -> List[str]:
    """Complete credential types for passphrase search --type.

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching credential type completions
    """
    return _complete_fixed(incomplete, PASSPHRASE_TYPES)


def complete_order(incomplete: str) -> List[str]:
    """Complete result ordering values for maniphest search --order.

    Completes progressively rather than dumping every spelling: the bare
    fields first, then the two directions once a ":" is typed. The rule is
    `phabfive.ordering.complete_order_value`, which every app's `--order`
    completer shares; this is maniphest's table handed to it, not a second
    copy of the logic.

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching order completions
    """
    from phabfive.ordering import complete_order_value

    return complete_order_value(
        incomplete, MANIPHEST_ORDER_FIELDS, MANIPHEST_ORDER_DIRECTIONS
    )


# Suffixes `phabfive spec validate` reads, exactly the ones
# phabfive.spec.loader detects a format from. Completion offers no other, so
# a TAB never proposes a file the loader would refuse on its name alone.
SPEC_FILE_EXTENSIONS = ("yaml", "yml", "json", "jsonl", "ndjson", "toml")


def complete_spec_file(incomplete: str) -> List[str]:
    """Complete a path to a spec file, filtered to the formats that load.

    The first file-path completer phabfive has - `--with` never had one - so
    it is written out here rather than left to the shell: click's own file
    completion offers every file, and offering a .md or a .png to `spec
    validate` is offering something the loader will refuse.

    Directories are offered with a trailing separator so completion keeps
    walking down; a dotfile is offered only once a dot is typed, which is
    what a shell does.

    Every failure answers with no completions. A directory that cannot be
    read must not break the shell's TAB.

    Parameters
    ----------
    incomplete : str
        The partial path being typed

    Returns
    -------
    list
        Matching paths, directories first-class and files filtered by suffix
    """
    import os
    from pathlib import Path

    directory, separator, prefix = incomplete.rpartition(os.sep)

    if separator:
        base = Path(directory or os.sep)
    else:
        base = Path(".")

    try:
        entries = sorted(base.iterdir(), key=lambda entry: entry.name)
    except OSError:
        return []

    matches = []

    for entry in entries:
        name = entry.name

        if not name.startswith(prefix):
            continue

        # A dotfile is hidden until the user says otherwise, as in a shell
        if name.startswith(".") and not prefix.startswith("."):
            continue

        shown = f"{directory}{separator}{name}" if separator else name

        try:
            is_directory = entry.is_dir()
        except OSError:
            continue

        if is_directory:
            matches.append(shown + os.sep)
        elif entry.suffix.lower().lstrip(".") in SPEC_FILE_EXTENSIONS:
            matches.append(shown)

    return matches
