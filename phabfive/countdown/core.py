# -*- coding: utf-8 -*-
"""Core countdown functionality for phabfive."""

import datetime
import re
import time

from phabricator import APIError

from phabfive.constants import MONOGRAMS
from phabfive.core import Phabfive
from phabfive.exceptions import PhabfiveDataException
from phabfive.maniphest.utils import parse_time_with_unit


class Countdown(Phabfive):
    def __init__(self):
        super().__init__()

    def _validate_identifier(self, id_):
        """Validate countdown identifier format (C123)."""
        return bool(re.match(f"^{MONOGRAMS['countdown']}$", id_))

    def _convert_ids(self, ids):
        """Convert countdown monograms to integer IDs."""
        ids_list_int = []

        for id_ in ids:
            if not self._validate_identifier(id_):
                raise PhabfiveDataException(
                    f"Invalid countdown ID '{id_}'. Expected format: C123"
                )
            ids_list_int.append(int(id_[1:]))

        return ids_list_int

    def parse_epoch(self, epoch_value):
        """Parse epoch value from ISO 8601 or relative time format.

        Supports:
        - ISO 8601: "2026-06-15T10:00:00", "2026-06-15"
        - Relative future: "+7d", "+2w", "+1m", "+1y", "+4h"
        - Unix timestamp: "1718438400"

        Returns:
            Unix timestamp (int)
        """
        if epoch_value is None:
            return None

        epoch_str = str(epoch_value).strip()

        # Handle relative time (+ prefix indicates future)
        if epoch_str.startswith("+"):
            relative_part = epoch_str[1:]  # Remove + prefix
            days = parse_time_with_unit(relative_part)
            # Convert days to seconds and add to current time
            seconds = int(days * 24 * 3600)
            return int(time.time()) + seconds

        # Handle Unix timestamp
        if epoch_str.isdigit():
            return int(epoch_str)

        # Handle ISO 8601 format
        formats = [
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
        ]

        for fmt in formats:
            try:
                dt = datetime.datetime.strptime(epoch_str, fmt)
                return int(dt.timestamp())
            except ValueError:
                continue

        raise PhabfiveDataException(
            f"Invalid epoch format: '{epoch_value}'. "
            "Expected ISO 8601 (2026-06-15T10:00:00), "
            "relative time (+7d, +2w, +1m), or Unix timestamp"
        )

    def get_countdowns(self, query_key=None, attachments=None, constraints=None):
        """Retrieve countdown objects from Phabricator.

        Args:
            query_key: Query key (defaults to "all")
            attachments: Optional attachments
            constraints: Search constraints

        Returns:
            List of countdown data dicts
        """
        query_key = query_key or "all"
        attachments = attachments or {}
        constraints = constraints or {}

        response = self.phab.countdown.search(
            queryKey=query_key,
            attachments=attachments,
            constraints=constraints,
        )

        return response.get("data", [])

    def countdown_show(self, countdown_ids):
        """Get detailed countdown information for display.

        Args:
            countdown_ids: List of countdown IDs (integers, without C prefix)

        Returns:
            dict with 'countdowns' key containing list of countdown data
            formatted like maniphest (with nested Countdown: section)
        """
        countdowns = self.get_countdowns(
            constraints={"ids": countdown_ids},
        )

        if not countdowns:
            raise PhabfiveDataException("No countdowns found")

        result = []
        for countdown in countdowns:
            monogram = f"C{countdown['id']}"
            url = self.get_countdown_url(countdown["id"])

            fields = countdown.get("fields", {})
            epoch_ts = fields.get("epoch")

            # Build nested Countdown section (like maniphest Task section)
            # Display label is "Name" to match Phorge web UI (even though API field is "title")
            countdown_fields = {
                "Name": fields.get("title", ""),
                "Epoch": self._format_epoch(epoch_ts),
                "Remaining": self._format_remaining(epoch_ts),
                "Status": fields.get("status", "active"),
            }

            # Add description if available (API returns {"raw": "...", "rendered": "..."})
            description_raw = fields.get("description", {}).get("raw", "")
            if description_raw:
                countdown_fields["Description"] = description_raw

            countdown_data = {
                "_url": url,
                "_link": self.format_link(url, monogram),
                "_epoch_unix": epoch_ts,
                "Countdown": countdown_fields,
            }

            # Resolve author name from PHID and add to Countdown section
            author_phid = fields.get("authorPHID")
            if author_phid:
                countdown_data["_author"] = self._resolve_phid_to_name(author_phid)

            result.append(countdown_data)

        return {"countdowns": result}

    def _format_epoch(self, epoch_ts):
        """Format epoch timestamp to ISO 8601 string."""
        if epoch_ts:
            dt = datetime.datetime.fromtimestamp(epoch_ts)
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        return None

    def _format_remaining(self, epoch_ts):
        """Format remaining time until epoch in human-readable form."""
        if not epoch_ts:
            return None

        now = int(time.time())
        diff = epoch_ts - now

        if diff <= 0:
            return "Completed"

        # Calculate units
        days = diff // (24 * 3600)
        hours = (diff % (24 * 3600)) // 3600
        minutes = (diff % 3600) // 60

        if days > 0:
            return f"{days}d {hours}h remaining"
        elif hours > 0:
            return f"{hours}h {minutes}m remaining"
        else:
            return f"{minutes}m remaining"

    def _resolve_phid_to_name(self, phid):
        """Resolve a PHID to a human-readable name."""
        try:
            response = self.phab.phid.query(phids=[phid])
            if response and phid in response:
                return response[phid].get("name", phid)
        except Exception:
            pass
        return phid

    def create_countdown(
        self,
        title,
        epoch,
        description=None,
        tags=None,
        subscribers=None,
    ):
        """Create a new countdown.

        Args:
            title: Countdown title
            epoch: Target date/time (ISO 8601, relative, or Unix timestamp)
            description: Optional description
            tags: List of project tags
            subscribers: List of subscriber usernames

        Returns:
            dict with countdown object info
        """
        tags = tags if tags else []
        subscribers = subscribers if subscribers else []

        # Parse epoch to Unix timestamp
        epoch_ts = self.parse_epoch(epoch)

        transactions = [
            {"type": "name", "value": title},
            {"type": "epoch", "value": epoch_ts},
        ]

        if description:
            transactions.append({"type": "description", "value": description})
        if tags:
            transactions.append({"type": "projects.add", "value": tags})
        if subscribers:
            transactions.append({"type": "subscribers.add", "value": subscribers})

        try:
            result = self.phab.countdown.edit(transactions=transactions)
        except APIError as e:
            raise PhabfiveDataException(str(e).replace("ERR-CONDUIT-CORE: ", ""))

        return result["object"]

    def edit_countdown(
        self,
        countdown_id,
        title=None,
        epoch=None,
        description=None,
        tags=None,
        subscribers=None,
        dry_run=False,
    ):
        """Edit an existing countdown.

        Args:
            countdown_id: Countdown ID (integer, without C prefix)
            title: New title (None to keep current)
            epoch: New epoch (None to keep current)
            description: New description (None to keep current)
            tags: List of project tags to add
            subscribers: List of subscriber usernames to add
            dry_run: If True, return changes without applying

        Returns:
            dict with changes made or to be made
        """
        transactions = []
        changes = []

        if title is not None:
            transactions.append({"type": "name", "value": title})
            changes.append({"field": "Name", "new": title})

        if epoch is not None:
            epoch_ts = self.parse_epoch(epoch)
            transactions.append({"type": "epoch", "value": epoch_ts})
            changes.append({"field": "Epoch", "new": self._format_epoch(epoch_ts)})

        if description is not None:
            transactions.append({"type": "description", "value": description})
            changes.append({"field": "Description", "new": "(updated)"})

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
                "countdown_id": countdown_id,
                "changes": [],
                "message": "No changes specified",
            }

        if dry_run:
            return {"countdown_id": countdown_id, "changes": changes, "dry_run": True}

        try:
            self.phab.countdown.edit(
                objectIdentifier=f"C{countdown_id}",
                transactions=transactions,
            )
        except APIError as e:
            raise PhabfiveDataException(str(e).replace("ERR-CONDUIT-CORE: ", ""))

        return {"countdown_id": countdown_id, "changes": changes}

    def add_countdown_comment(self, countdown_id, comment_text):
        """Add a comment to a countdown.

        Args:
            countdown_id: Countdown ID (integer, without C prefix)
            comment_text: The comment text to add

        Returns:
            dict with 'success' and 'countdown_id' keys
        """
        try:
            self.phab.countdown.edit(
                objectIdentifier=f"C{countdown_id}",
                transactions=[{"type": "comment", "value": comment_text}],
            )
        except APIError as e:
            raise PhabfiveDataException(str(e).replace("ERR-CONDUIT-CORE: ", ""))

        return {"success": True, "countdown_id": countdown_id}

    def get_countdown_url(self, countdown_id):
        """Get the URL for a countdown."""
        return f"{self.url}/C{countdown_id}"

    def get_countdown_data(self, countdown_id):
        """Get full countdown data for editing.

        Args:
            countdown_id: Countdown ID (integer, without C prefix)

        Returns:
            dict with countdown data
        """
        countdowns = self.get_countdowns(constraints={"ids": [countdown_id]})

        if not countdowns:
            raise PhabfiveDataException(f"Countdown C{countdown_id} not found")

        countdown = countdowns[0]
        fields = countdown.get("fields", {})

        return {
            "id": countdown["id"],
            "phid": countdown["phid"],
            "title": fields.get("title", ""),
            "epoch": fields.get("epoch"),
            "description": fields.get("description", {}).get("raw", ""),
        }
