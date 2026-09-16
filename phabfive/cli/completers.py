# -*- coding: utf-8 -*-
"""Shell completion functions for phabfive CLI options."""

from typing import List, Optional

from phabfive import cache
from phabfive.constants import (
    MANIPHEST_ORDER_DIRECTIONS,
    MANIPHEST_ORDER_FIELDS,
    PASTE_LANGUAGES,
    REPO_STATUS_CHOICES,
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
    completions = []
    completions.extend(v for v in values if v.startswith(incomplete))

    # For prefixes, offer full prefix:value combinations instead of bare prefix
    # This allows continued completion after selecting (e.g., "in:" -> "in:high")
    for prefix in PATTERN_PREFIXES:
        if prefix.startswith(incomplete):
            completions.extend(f"{prefix}{v}" for v in values)

    return completions


def _get_priorities() -> List[str]:
    """Get priority names - tries API first, falls back to defaults."""
    from phabfive.maniphest.fetchers import get_api_priority_names

    return _get_values_with_api_fallback(
        lambda phab: get_api_priority_names(phab),
        DEFAULT_PRIORITY_VALUES,
    )


def _get_statuses() -> List[str]:
    """Get status keys (e.g., "open", "resolved") - tries API first, falls back to defaults."""
    from phabfive.maniphest.fetchers import get_api_status_map

    def fetch_statuses(phab):
        status_map = get_api_status_map(phab)
        return list(status_map.get("statusMap", {}).keys())

    return _get_values_with_api_fallback(fetch_statuses, DEFAULT_STATUS_VALUES)


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

    Offers status names, pattern prefixes (e.g., "in:open") and the
    raised/lowered keywords.
    """
    completions = _complete_with_prefixes(incomplete, _get_statuses())
    completions.extend(_starting_with(incomplete, FILTER_DIRECTION_KEYWORDS))
    return completions


def _board_context(ctx) -> Optional[str]:
    """Get the board name given with --tag, if any.

    --tag is a single value on search and edit but repeatable on create,
    where the first tag is the board context.
    """
    tag_value = ctx.params.get("tag") if ctx else None
    if isinstance(tag_value, (list, tuple)):
        tag_value = tag_value[0] if tag_value else None
    return tag_value or None


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
    projects = []
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


def complete_tag(incomplete: str) -> list[str | tuple[str, str]]:
    """Complete tag (project) names from API.

    Matching is case-insensitive. Completions follow the case the user typed
    (e.g. "gun" -> "gunnar-core"), because Typer drops completions that don't
    start with the typed text; project names resolve case-insensitively, so
    the value still works.

    A name shared by several projects (e.g. milestones named "Sprint 1" in
    different parents) is offered once, with a description listing the
    project IDs to use instead. Milestones and subprojects are described
    with their parent. Descriptions are shown by zsh and fish.

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching project names, as (name, description) tuples where a
        description applies
    """
    # No default values for tags - they are instance-specific. _fetch_or_none
    # rather than the fallback helper, so that an unreachable API is not
    # mistaken for an instance that has no matching project.
    projects = _fetch_or_none(lambda phab: _fetch_projects_named(phab, incomplete))
    if projects is None:
        return []

    records = _project_records(projects)

    incomplete_lower = incomplete.lower()
    by_name = {}
    for record in records:
        name = record["name"]
        if name.lower().startswith(incomplete_lower):
            by_name.setdefault(name.lower(), []).append(record)

    completions = []
    for _, matches in sorted(by_name.items()):
        value = _in_typed_case(incomplete, matches[0]["name"])

        if len(matches) > 1:
            ids = ", ".join(
                _describe_project_id(record)
                for record in sorted(matches, key=lambda record: record["id"])
            )
            completions.append((value, f"ambiguous, use the ID: {ids}"))
        elif matches[0]["parent"]:
            completions.append((value, f"in {matches[0]['parent']}"))
        else:
            completions.append(value)

    return completions


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
    constraints = {}
    if incomplete.strip():
        constraints["nameLike"] = incomplete
    if not include_disabled:
        constraints["isDisabled"] = False

    users = []
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


def _user_cache_text(incomplete: str) -> str:
    """Normalise the typed text into the query it actually produces.

    _fetch_users_named drops the nameLike constraint for whitespace-only
    input, so that has to key the same entry as an empty string. Matching is
    case-insensitive, so the key is lowercased.
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


def _narrowed_from_cache(text: str, include_disabled: bool, directory, ttl):
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
    text = _user_cache_text(incomplete)

    # Resolved once: every probe below would otherwise re-read the config,
    # which costs more than the lookups it is meant to save
    directory, ttl = cache.context(USER_CACHE_NAMESPACE)

    cached = _narrowed_from_cache(text, include_disabled, directory, ttl)
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


def _user_completions(incomplete: str, include_disabled: bool) -> list:
    """Return (username, real name or None) pairs matching the typed text.

    Only usernames that start with the typed text are offered, because Typer
    drops the rest; the real name is offered as a description instead, so
    searching for "Bergstrom" cannot complete "sonja.bergstrom".
    """
    if incomplete.startswith("@"):
        # No username starts with "@", so the API has nothing to add here
        return [(ME_SHORTCUT, "yourself")] if ME_SHORTCUT.startswith(incomplete) else []

    records = _cached_user_records(incomplete, include_disabled)

    # @me is only offered before a username is typed, since it can never be
    # a prefix of one
    pairs = [(ME_SHORTCUT, "yourself")] if not incomplete else []

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
    fields first, then the two directions once a ":" is typed.

    Parameters
    ----------
    incomplete : str
        The incomplete value being typed

    Returns
    -------
    list
        Matching order completions
    """
    if ":" in incomplete:
        field = incomplete.split(":", 1)[0]
        if not MANIPHEST_ORDER_DIRECTIONS.get(field):
            # Unknown field, or one that takes no direction
            return []
        return _complete_fixed(incomplete, [f"{field}:asc", f"{field}:desc"])

    return _complete_fixed(incomplete, MANIPHEST_ORDER_FIELDS)
