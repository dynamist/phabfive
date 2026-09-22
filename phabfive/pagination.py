# -*- coding: utf-8 -*-

"""Cursor paging for Conduit search endpoints."""

#: The largest page Conduit will serve to a ``*.search`` method. Asking for
#: more is answered with ERR-INVALID-PAGE-SIZE rather than a truncated page,
#: which is why a total limit can never be forwarded as the page size.
MAX_PAGE_SIZE = 100


def page_records(data):
    """
    The records of one page, however that endpoint spells a page.

    A ``*.search`` method answers with a list, while the legacy ``*.query``
    methods answer with a dict keyed by PHID. Both answer an empty result
    with an empty list.

    Parameters
    ----------
    data : list, dict or None
        The ``data`` of one response.

    Returns
    -------
    list
        The records, in the order the response carried them.
    """
    if not data:
        return []

    if isinstance(data, dict):
        return list(data.values())

    return list(data)


def iter_pages(search, limit=None, **kwargs):
    """
    Yield each page of a Conduit search, following cursors until there is
    no more.

    Every Conduit search answers with at most 100 rows and a cursor; a caller
    that reads only the first response silently loses everything after it.

    Page at a time rather than all at once is what lets a caller that filters
    in Python stop early: it counts the matches itself and abandons the
    generator, so a limit costs the pages it takes to fill, not the whole
    instance.

    A page that fails - a timeout, a 429, a 5xx - is asked for again with
    the same cursor, by the retry policy phabfive.conduit mounts on every
    read (see phabfive.retry). So a long search resumes from the page that
    failed rather than starting over, and no page already yielded is
    fetched twice. Only when the retries run out does the error reach the
    caller, after the pages before it were yielded.

    Parameters
    ----------
    search : callable
        A bound search method, e.g. ``phab.paste.search`` or
        ``phab.passphrase.query``.
    limit : int, optional
        How many records to yield in total, not the page size. Pages are
        asked for in chunks of at most 100 and the last one is asked for no
        larger than what is still missing, so a small limit costs a small
        request. ``None`` means every record - which is what a caller that
        filters the records itself wants, since only it can count matches.
    **kwargs
        Passed to the endpoint unchanged, e.g. queryKey, constraints,
        attachments, order, needSecrets.

    Yields
    ------
    list
        The records of one page.
    """
    seen = 0
    after = None

    while True:
        page_kwargs = dict(kwargs)

        if limit is not None:
            page_kwargs["limit"] = min(MAX_PAGE_SIZE, limit - seen)

        if after is not None:
            page_kwargs["after"] = after

        response = search(**page_kwargs)
        records = page_records(response.get("data"))

        if limit is not None:
            records = records[: limit - seen]

        seen += len(records)

        yield records

        if limit is not None and seen >= limit:
            return

        after = (response.get("cursor") or {}).get("after")

        if not after:
            return


def search_all_pages(search, limit=None, **kwargs):
    """
    Run a Conduit search and return the records of every page.

    Parameters
    ----------
    search : callable
        A bound search method, e.g. ``phab.paste.search``.
    limit : int, optional
        How many records to return in total, not the page size. ``None``
        means every record.
    **kwargs
        Passed to the endpoint unchanged.

    Returns
    -------
    list
        Records from every page read, in the order returned.
    """
    return [
        record for page in iter_pages(search, limit=limit, **kwargs) for record in page
    ]


__all__ = [
    "MAX_PAGE_SIZE",
    "iter_pages",
    "page_records",
    "search_all_pages",
]
