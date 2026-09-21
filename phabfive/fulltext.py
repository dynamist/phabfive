# -*- coding: utf-8 -*-
"""Phorge's full-text search syntax, and the hint for when it finds nothing.

The text argument of `maniphest search`, `paste search` and `project search`
is passed to Phorge's full-text index as it stands. That index matches whole
words, stemmed - "teams" finds "Team", but "eam" finds nothing - and matches
part of a word only when the word is written with Phorge's substring operator,
"~eam". That is how the web UI's search boxes behave too, so the syntax is
passed through rather than rewritten: what works in one works in the other.

The cost is that a search for part of a word finds nothing and says nothing
about why. So when a text search comes back empty, `no_match_hint` names the
operators and offers the query with every plain word made a substring search.

The operators, each checked against a Phorge instance:

==============  ===============================================
``word``        the whole word, stemmed
``~word``       any word containing it, ignoring case
``"a phrase"``  those words in that order
``-word``       leaves out what contains the word
``title:word``  only the title (or name) is searched
==============  ===============================================
"""

# python std lib
import re
import shlex

SUBSTRING_OPERATOR = "~"

# A quoted phrase is one token, however many words it holds
_TOKEN = re.compile(r'"[^"]*"|\S+')


def _is_plain(token):
    """Whether a token is a bare word, with no operator of its own."""
    return not (token[:1] in (SUBSTRING_OPERATOR, "-", '"') or ":" in token)


def substring_query(query):
    """The query with every plain word made a substring search.

    Parameters
    ----------
    query : str
        What was searched for

    Returns
    -------
    str or None
        The query with "~" in front of each word that had no operator, or
        None when there is no such word - a query already written with
        operators is not second-guessed

    Examples
    --------
    >>> substring_query("eam")
    '~eam'
    >>> substring_query('sal "a phrase" -ops')
    '~sal "a phrase" -ops'
    >>> substring_query("~eam") is None
    True
    """
    tokens = _TOKEN.findall(query or "")

    if not any(_is_plain(token) for token in tokens):
        return None

    return " ".join(
        f"{SUBSTRING_OPERATOR}{token}" if _is_plain(token) else token
        for token in tokens
    )


def no_match_hint(query):
    """What to tell somebody whose text search found nothing.

    Parameters
    ----------
    query : str or None
        The text that was searched for

    Returns
    -------
    str or None
        The hint, or None when there was no text, or when every word already
        carries an operator and there is nothing to suggest
    """
    suggestion = substring_query(query)

    if suggestion is None:
        return None

    return (
        "Text search matches whole words, not parts of words. "
        f"To match part of a word, prefix it with ~: {shlex.quote(suggestion)}\n"
        'Other operators: "a phrase" in that order, -word to leave a word out, '
        "title:word to search only titles."
    )


__all__ = ["SUBSTRING_OPERATOR", "no_match_hint", "substring_query"]
