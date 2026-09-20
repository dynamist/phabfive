# -*- coding: utf-8 -*-

"""Cursor paging for Conduit ``*.search`` endpoints."""

#: The largest page Conduit will serve. Asking for more is answered with
#: ERR-INVALID-PAGE-SIZE rather than a truncated page, which is why a total
#: limit can never be forwarded as the page size.
MAX_PAGE_SIZE = 100


def search_all_pages(search, limit=None, **kwargs):
    """
    Run a ``*.search`` endpoint, following cursors until there is no more.

    Every Conduit search answers with at most 100 rows and a cursor; a caller
    that reads only the first response silently loses everything after it.

    Parameters
    ----------
    search : callable
        A bound ``*.search`` method, e.g. ``phab.paste.search``.
    limit : int, optional
        How many records to return in total, not the page size. Pages are
        asked for in chunks of at most 100 and the last one is asked for no
        larger than what is still missing, so a small limit costs a small
        request. ``None`` means every record.
    **kwargs
        Passed to the endpoint unchanged, e.g. queryKey, constraints,
        attachments, order.

    Returns
    -------
    list
        Records from every page read, in the order returned.
    """
    records = []
    after = None

    while True:
        page_kwargs = dict(kwargs)

        if limit is not None:
            page_kwargs["limit"] = min(MAX_PAGE_SIZE, limit - len(records))

        if after is not None:
            page_kwargs["after"] = after

        response = search(**page_kwargs)
        records.extend(response.get("data") or [])

        if limit is not None and len(records) >= limit:
            return records[:limit]

        after = (response.get("cursor") or {}).get("after")

        if not after:
            return records
