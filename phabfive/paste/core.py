# -*- coding: utf-8 -*-
"""Core paste functionality for phabfive."""

# python std lib
import logging
import re

# phabfive imports
from phabfive.constants import (
    MONOGRAMS,
    PASTE_ORDER_DEFAULT,
    PASTE_ORDER_DIRECTIONS,
    PASTE_ORDER_FIELDS,
    PASTE_ORDER_SUGGESTIONS,
    PASTE_STATUS_CHOICES,
)
from phabfive.core import Phabfive
from phabfive.exceptions import (
    PhabfiveAPIException,
    PhabfiveConfigException,
    PhabfiveDataException,
    PhabfiveInputException,
)
from phabfive.maniphest.utils import time_constraint
from phabfive.options import value_list
from phabfive.ordering import parse_order, sort_records
from phabfive.pagination import search_all_pages
from phabfive.paste.formatters import build_paste_display_data

# 3rd party imports

log = logging.getLogger(__name__)


#: ``(field, direction)`` to what ``paste.search`` is sent as its order.
#:
#: PhabricatorPasteQuery's builtin orders are newest, created, oldest and
#: relevance, and its order columns are id, rank, fulltext-created and
#: fulltext-modified - there is no title order, which is why `title` is not
#: one of the fields `paste search --order` offers.
PASTE_API_ORDERS = {
    ("created", "desc"): "newest",
    ("created", "asc"): "oldest",
    ("relevance", None): "relevance",
}

#: The client-side key per order field, which tie-breaks what the server
#: already ordered. A paste's id and its creation order are the same thing.
PASTE_SORT_KEYS = {"created": lambda paste: paste.get("id") or 0}


def paste_id_list(value, option="--ids"):
    """Paste monograms as the integers ``paste.search`` constrains on.

    Accepts ``"P12,P13"``, ``["P12", 13]`` and a bare number, which is what
    a flag and a script between them produce.

    Raises
    ------
    PhabfiveInputException
        For anything that is not a paste monogram. Another application's
        monogram is not a paste, so ``T45`` is refused by name rather than
        sent as the id 45.
    """
    entries = value_list(value)

    if not entries:
        return None

    ids = []

    for entry in entries:
        monogram = entry[1:] if entry[:1] in ("P", "p") else entry

        if not monogram.isdigit():
            raise PhabfiveInputException(
                f"Invalid paste ID '{entry}' for {option}. Expected format: P123"
            )

        ids.append(int(monogram))

    return ids


def build_paste_search_constraints(
    text_query=None,
    author_phids=None,
    ids=None,
    phids=None,
    languages=None,
    statuses=None,
    created_after=None,
    created_before=None,
):
    """The ``paste.search`` constraints one search asks for.

    Library code, so every value is checked here rather than in the
    command: a status outside the two paste.search knows is answered by
    Phorge with an empty result rather than an error, which reads exactly
    like "no pastes are archived" and is how a typo goes unnoticed.

    ``modifiedStart``/``modifiedEnd`` are deliberately absent:
    PhabricatorPasteQuery has no modified-date constraint, and sending one
    is ERR-CONDUIT-CORE, "Parameter constraints includes an invalid key".

    Parameters
    ----------
    text_query : str, optional
        Free text, matched the way the web UI's search box matches it
    author_phids : list, optional
        Already-resolved user PHIDs. paste.search names this constraint
        "authors", where maniphest.search names its own "authorPHIDs".
    ids : str, int or list, optional
        Paste monograms or numbers, see :func:`paste_id_list`
    phids : str or list, optional
        Paste PHIDs
    languages : str or list, optional
        Syntax highlighting languages, any of which matches. Instance
        configuration (pygments.dropdown-choices), so the value is passed
        through rather than checked against a list phabfive would guess at.
    statuses : str or list, optional
        "active", "archived", or both
    created_after, created_before : str or int, optional
        TIME values, e.g. "7d", "2w"

    Returns
    -------
    dict
        The constraints, with nothing absent left in

    Raises
    ------
    PhabfiveConfigException
        If a status is not one paste.search knows
    PhabfiveInputException
        If an id or a time is not one
    """
    constraints = {}

    if text_query:
        constraints["query"] = str(text_query)

    if author_phids:
        constraints["authors"] = list(author_phids)

    id_list = paste_id_list(ids)
    if id_list:
        constraints["ids"] = id_list

    phid_list = value_list(phids)
    if phid_list:
        constraints["phids"] = phid_list

    language_list = value_list(languages)
    if language_list:
        constraints["languages"] = language_list

    status_list = value_list(statuses)
    if status_list:
        unknown = [s for s in status_list if s not in PASTE_STATUS_CHOICES]
        if unknown:
            raise PhabfiveConfigException(
                f"Unknown paste status {', '.join(repr(s) for s in unknown)}, "
                f"expected one of: {', '.join(PASTE_STATUS_CHOICES)}"
            )
        constraints["statuses"] = status_list

    created_start = time_constraint(created_after, "created-after")
    if created_start is not None:
        constraints["createdStart"] = created_start

    created_end = time_constraint(created_before, "created-before")
    if created_end is not None:
        constraints["createdEnd"] = created_end

    return constraints


class Paste(Phabfive):
    def _validate_identifier(self, id_):
        return re.match(f"^{MONOGRAMS['paste']}$", id_)

    def _convert_ids(self, ids):
        """
        Method used by print function
        """
        ids_list_int = []

        for id_ in ids:
            if not self._validate_identifier(id_):
                raise PhabfiveDataException(
                    f"Invalid paste ID '{id_}'. Expected format: P123"
                )

            id_ = id_.replace("P", "")
            # constraints takes int
            id_ = int(id_)
            ids_list_int.append(id_)

        return ids_list_int

    def create_paste(
        self, title=None, file=None, language=None, tags=None, subscribers=None
    ):
        """
        Wrapper that connects to Phabricator and creates paste from a file.

        :type title: str
        :type file: str
        :type language: str
        :type tags: list
        :type subscribers: list

        :rtype: dict
        """
        with open(file, "r") as f:
            text = f.read()

        return self.create_paste_from_content(
            title=title,
            content=text,
            language=language,
            tags=tags,
            subscribers=subscribers,
        )

    def create_paste_from_content(
        self, title=None, content=None, language=None, tags=None, subscribers=None
    ):
        """
        Create a paste with the given content.

        :type title: str
        :type content: str
        :type language: str
        :type tags: list
        :type subscribers: list

        :rtype: dict
        """
        tags = tags if tags else []
        subscribers = subscribers if subscribers else []

        transactions_values = [
            {"type": "title", "value": title},
            {"type": "text", "value": content},
            {"type": "language", "value": language},
            {"type": "projects.add", "value": tags},
            {"type": "subscribers.add", "value": subscribers},
        ]

        # Phabricator does not take None as a value
        transactions = [
            item for item in transactions_values if None not in item.values()
        ]

        try:
            id_and_phid = self.phab.paste.edit(transactions=transactions)
        except PhabfiveAPIException as a:
            raise PhabfiveDataException(str(a).replace("ERR-CONDUIT-CORE: ", ""))

        return id_and_phid["object"]

    def get_pastes(
        self, query_key=None, attachments=None, constraints=None, limit=None, order=None
    ):
        """Wrapper that connects to Phabricator and retrieves information about pastes.

        Follows the result cursor to the end, so callers see every paste
        rather than the first page. Conduit returns 100 rows per page, and an
        instance past that limit would otherwise hide pastes from every lookup
        that goes through here.

        `query_key` defaults to "all".

        `limit` is how many pastes to return in total, not the page size a
        page is asked for - forwarding it as the page size is what made
        `--limit 101` fail with ERR-INVALID-PAGE-SIZE. `None` means every
        paste.

        `order` is a builtin paste.search order name, or a column vector.
        Cursor paging respects it, so the pages stay in order as they are
        concatenated - which is what makes a limit the top N of the order
        asked for rather than an arbitrary page of it. Omitted leaves the
        order to the server, which is what every caller but a search wants.

        :type query_key: str
        :type attachments: dict
        :type constraints: dict
        :type limit: int
        :type order: str or list

        :rtype: list
        """
        kwargs = {}

        if order:
            kwargs["order"] = order

        return search_all_pages(
            self.phab.paste.search,
            limit=limit,
            queryKey=query_key or "all",
            attachments=attachments or {},
            constraints=constraints or {},
            **kwargs,
        )

    def get_pastes_formatted(self, ids=None):
        """Return list of dicts with 'id' and 'title' keys, sorted by title."""
        if ids:
            constraints = {"ids": self._convert_ids(ids=ids)}
            pastes = self.get_pastes(constraints=constraints)
        else:
            pastes = self.get_pastes()

        if not pastes:
            raise PhabfiveDataException("No data or other error")

        # sort based on title
        sorted_pastes = sorted(pastes, key=lambda key: key["fields"]["title"])

        return [
            {"id": f"P{item['id']}", "title": item["fields"]["title"]}
            for item in sorted_pastes
        ]

    def paste_show(self, paste_ids, show_content=True):
        """Show one or more pastes, as display records.

        Args:
            paste_ids: List of paste IDs (integers, without P prefix)
            show_content: Whether to include paste content

        Returns:
            dict with 'pastes', the records build_paste_display_data
            builds, and 'missing_ids'; None when no paste was found
        """
        attachments = {}
        if show_content:
            attachments["content"] = True

        pastes = self.get_pastes(
            constraints={"ids": paste_ids},
            attachments=attachments,
        )

        if not pastes:
            for paste_id in paste_ids:
                log.error(f"Paste P{paste_id} not found")
            return None

        # Report any pastes that were not found, matching maniphest show
        found_ids = {p["id"] for p in pastes}
        missing_ids = [pid for pid in paste_ids if pid not in found_ids]
        for paste_id in missing_ids:
            log.error(f"Paste P{paste_id} not found")

        # Let the caller tell a partial result from a complete one
        return {
            "pastes": self._display_data(pastes, show_content=show_content),
            "missing_ids": missing_ids,
        }

    def paste_search(self, constraints=None, limit=None, order=None):
        """Search pastes, as the records `paste_show` answers with.

        The same record as `paste_show` gives, less the content, which a
        search does not fetch.

        Parameters
        ----------
        constraints : dict, optional
            paste.search constraints, as
            :func:`build_paste_search_constraints` builds them
        limit : int, optional
            How many pastes to return in total; None for all of them
        order : str, optional
            Result ordering as "<field>[:asc|:desc]", e.g. "created:asc".
            The server does the ordering, so a limit keeps the first N of
            it. Defaults to newest first, which is what paste.search
            answers with when it is given no order at all.

        Returns
        -------
        dict
            {"pastes": [...]}, in the order asked for

        Raises
        ------
        PhabfiveConfigException
            If the order is not one of PASTE_ORDER_FIELDS
        """
        # Resolved before anything is fetched, so a bad --order fails fast
        order_field, order_direction = parse_order(
            order,
            PASTE_ORDER_FIELDS,
            PASTE_ORDER_DIRECTIONS,
            PASTE_ORDER_DEFAULT,
            suggestions=PASTE_ORDER_SUGGESTIONS,
        )
        log.info(f"Ordering results by '{order_field}:{order_direction}'")

        pastes = self.get_pastes(
            constraints=constraints,
            limit=limit,
            order=PASTE_API_ORDERS[(order_field, order_direction)],
        )

        # The server already ordered these; this settles the ties, so two
        # pastes of the same second do not swap places between runs.
        pastes = sort_records(pastes, order_field, order_direction, PASTE_SORT_KEYS)

        return {"pastes": self._display_data(pastes, show_content=False)}

    def _display_data(self, pastes, show_content=True):
        """The display records for a set of pastes, in one PHID lookup.

        Authors and Spaces are named by the same phid.query, however many
        pastes the set holds, rather than by one query per paste.
        """
        phids = sorted(
            {
                phid
                for paste in pastes
                for phid in (
                    paste.get("fields", {}).get("authorPHID"),
                    paste.get("fields", {}).get("spacePHID"),
                )
                if phid
            }
        )

        names = {}
        if phids:
            try:
                found = self.phab.phid.query(phids=phids) or {}
                names = {
                    phid: data for phid, data in found.items() if hasattr(data, "get")
                }
            except Exception as e:
                # Naming an author is a convenience, and a read must not
                # fail over it: the PHID stands in for the name instead.
                log.warning(f"Failed to resolve paste author and Space PHIDs: {e}")

        return build_paste_display_data(
            self.url,
            self.format_link,
            pastes,
            author_names={
                phid: data.get("name") or phid for phid, data in names.items()
            },
            space_map={
                phid: data.get("fullName") or data.get("name") or phid
                for phid, data in names.items()
            },
            show_content=show_content,
        )

    def get_paste_data(self, paste_id):
        """Get full paste data including content.

        Args:
            paste_id: Paste ID (integer, without P prefix)

        Returns:
            dict with paste data including content
        """
        pastes = self.get_pastes(
            constraints={"ids": [paste_id]},
            attachments={"content": True},
        )

        if not pastes:
            raise PhabfiveDataException(f"Paste P{paste_id} not found")

        paste = pastes[0]
        return {
            "id": paste["id"],
            "phid": paste["phid"],
            "title": paste["fields"].get("title", ""),
            "language": paste["fields"].get("language") or "text",
            "content": paste.get("attachments", {})
            .get("content", {})
            .get("content", ""),
        }

    def edit_paste(
        self,
        paste_id,
        title=None,
        content=None,
        language=None,
        tags=None,
        subscribers=None,
        dry_run=False,
    ):
        """Edit an existing paste.

        Args:
            paste_id: Paste ID (integer, without P prefix)
            title: New title (None to keep current)
            content: New content (None to keep current)
            language: New language (None to keep current)
            tags: List of project tags to add
            subscribers: List of subscriber usernames to add
            dry_run: If True, return changes without applying

        Returns:
            dict with changes made or to be made
        """
        # Build transactions
        transactions = []
        changes = []

        if title is not None:
            transactions.append({"type": "title", "value": title})
            changes.append({"field": "Title", "new": title})

        if content is not None:
            transactions.append({"type": "text", "value": content})
            changes.append({"field": "Content", "new": "(updated)"})

        if language is not None:
            transactions.append({"type": "language", "value": language})
            changes.append({"field": "Language", "new": language})

        if tags:
            transactions.append({"type": "projects.add", "value": tags})
            changes.append({"field": "Tags", "new": f"Added: {', '.join(tags)}"})

        if subscribers:
            transactions.append({"type": "subscribers.add", "value": subscribers})
            changes.append(
                {"field": "Subscribers", "new": f"Added: {', '.join(subscribers)}"}
            )

        if not transactions:
            return {
                "paste_id": paste_id,
                "changes": [],
                "message": "No changes specified",
            }

        if dry_run:
            return {"paste_id": paste_id, "changes": changes, "dry_run": True}

        # Apply changes
        try:
            self.phab.paste.edit(
                objectIdentifier=f"P{paste_id}",
                transactions=transactions,
            )
        except PhabfiveAPIException as e:
            raise PhabfiveDataException(str(e).replace("ERR-CONDUIT-CORE: ", ""))

        return {"paste_id": paste_id, "changes": changes}

    def add_paste_comment(self, paste_id, comment_text):
        """Add a comment to a paste.

        Args:
            paste_id: Paste ID (integer, without P prefix)
            comment_text: The comment text to add

        Returns:
            dict with 'success' and 'paste_id' keys
        """
        try:
            self.phab.paste.edit(
                objectIdentifier=f"P{paste_id}",
                transactions=[{"type": "comment", "value": comment_text}],
            )
        except PhabfiveAPIException as e:
            raise PhabfiveDataException(str(e).replace("ERR-CONDUIT-CORE: ", ""))

        return {"success": True, "paste_id": paste_id}

    def get_paste_url(self, paste_id):
        """Get the URL for a paste.

        Args:
            paste_id: Paste ID (integer, without P prefix)

        Returns:
            URL string for the paste
        """
        return f"{self.url}/P{paste_id}"


__all__ = [
    "PASTE_API_ORDERS",
    "PASTE_SORT_KEYS",
    "Paste",
    "build_paste_search_constraints",
    "paste_id_list",
]
