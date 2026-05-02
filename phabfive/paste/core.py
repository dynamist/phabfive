# -*- coding: utf-8 -*-
"""Core paste functionality for phabfive."""

# python std lib
import re

# phabfive imports
from phabfive.constants import MONOGRAMS
from phabfive.core import Phabfive
from phabfive.exceptions import PhabfiveDataException

# 3rd party imports
from phabricator import APIError


class Paste(Phabfive):
    def __init__(self):
        super(Paste, self).__init__()

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
        except APIError as a:
            raise PhabfiveDataException(str(a).replace("ERR-CONDUIT-CORE: ", ""))

        return id_and_phid["object"]

    def get_pastes(self, query_key=None, attachments=None, constraints=None):
        """Wrapper that connects to Phabricator and retrieves information about pastes.

        `query_key` defaults to "all".

        :type query_key: str
        :type attachments: dict
        :type constraints: dict

        :rtype: dict
        """
        query_key = query_key or "all"
        attachments = attachments or {}
        constraints = constraints or {}

        response = self.phab.paste.search(
            queryKey=query_key,
            attachments=attachments,
            constraints=constraints,
        )

        pastes = response.get("data", {})

        return pastes

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
        """Get detailed paste information for display.

        Args:
            paste_ids: List of paste IDs (integers, without P prefix)
            show_content: Whether to include paste content

        Returns:
            dict with 'pastes' key containing list of paste data
        """
        attachments = {}
        if show_content:
            attachments["content"] = True

        pastes = self.get_pastes(
            constraints={"ids": paste_ids},
            attachments=attachments,
        )

        if not pastes:
            raise PhabfiveDataException("No pastes found")

        result = []
        for paste in pastes:
            paste_data = {
                "id": f"P{paste['id']}",
                "phid": paste["phid"],
                "title": paste["fields"].get("title", ""),
                "language": paste["fields"].get("language") or "text",
                "status": paste["fields"].get("status", "active"),
                "dateCreated": paste["fields"].get("dateCreated"),
                "dateModified": paste["fields"].get("dateModified"),
                "authorPHID": paste["fields"].get("authorPHID"),
            }

            # Add content if requested and available
            if show_content and "attachments" in paste:
                content_attachment = paste["attachments"].get("content", {})
                paste_data["content"] = content_attachment.get("content", "")

            # Resolve author name
            author_phid = paste_data.get("authorPHID")
            if author_phid:
                paste_data["author"] = self._resolve_phid_to_name(author_phid)

            result.append(paste_data)

        return {"pastes": result}

    def _resolve_phid_to_name(self, phid):
        """Resolve a PHID to a human-readable name."""
        try:
            response = self.phab.phid.query(phids=[phid])
            if response and phid in response:
                return response[phid].get("name", phid)
        except Exception:
            pass
        return phid
