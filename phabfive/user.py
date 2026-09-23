# -*- coding: utf-8 -*-

# python std lib
import json
import logging
import os
import stat
import unicodedata
from urllib.parse import urlparse

# 3rd party imports
from phabricator import Phabricator

# phabfive imports
from phabfive.constants import (
    USER_ORDER_DEFAULT,
    USER_ORDER_DIRECTIONS,
    USER_ORDER_FIELDS,
    USER_ORDER_SUGGESTIONS,
    USER_ROLE_CONSTRAINTS,
    USER_ROLES,
)
from phabfive.conduit import Conduit
from phabfive.core import Phabfive
from phabfive.exceptions import (
    PhabfiveAPIException,
    PhabfiveConfigException,
    PhabfiveInputException,
    PhabfiveRemoteException,
)
from phabfive.maniphest.utils import format_timestamp, time_constraint
from phabfive.options import value_list
from phabfive.ordering import parse_order, sort_records
from phabfive.pagination import iter_pages

log = logging.getLogger(__name__)


#: ``(field, direction)`` to what ``user.search`` is sent as its order.
#:
#: PhabricatorPeopleQuery's builtin orders are newest, created, oldest and
#: relevance - none of them by name - so A-Z is asked for as the "username"
#: column, which it does order by, and Z-A as "-username". A list is a
#: column vector and a string is a builtin; Conduit takes either.
USER_API_ORDERS = {
    ("username", "asc"): ["username"],
    ("username", "desc"): ["-username"],
    ("created", "desc"): "newest",
    ("created", "asc"): "oldest",
    ("relevance", None): "relevance",
}

#: The client-side key per order field, which tie-breaks what the server
#: already ordered. ``relevance`` is absent: the response carries no rank.
USER_SORT_KEYS = {
    "username": lambda user: (user.get("fields", {}).get("username") or "").casefold(),
    "created": lambda user: user.get("id") or 0,
}


def user_id_list(value, option="--ids"):
    """User ids as the integers ``user.search`` constrains on.

    A user has no monogram - Phorge names one by username or by PHID - so
    this takes the bare number, and a username goes to --usernames.

    Raises
    ------
    PhabfiveInputException
        For anything that is not a number
    """
    entries = value_list(value)

    if not entries:
        return None

    ids = []

    for entry in entries:
        if not entry.isdigit():
            raise PhabfiveInputException(
                f"Invalid user ID '{entry}' for {option}. Expected a number, "
                "e.g. 12; a username goes to --usernames."
            )

        ids.append(int(entry))

    return ids


def user_name_list(value, option="--usernames"):
    """Exact usernames, as ``user.search``'s `usernames` constraint takes them.

    A shortcut is not a username: the constraint matches the stored name
    exactly, so "@me" would be answered with an empty result rather than an
    error - which reads as "there is no such user".

    Raises
    ------
    PhabfiveInputException
        For a value that cannot be a username
    """
    names = value_list(value)

    for name in names:
        if name.startswith("@"):
            raise PhabfiveInputException(
                f"Invalid username '{name}' for {option}. Expected an exact "
                "username; --username matches any part of one instead."
            )

    return names or None


def _fold(text):
    """Text as nameLike compares it: without case, and without accents.

    The server matched "strom" and "STRÖM" alike against Bergström, so a
    field checked here has to fold the same way, or --realname would drop a user
    the server had just found.
    """
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _contains(field, text):
    """Whether the text is anywhere in the field, compared as nameLike does."""
    return _fold(text) in _fold(field)


class User(Phabfive):
    def whoami(self):
        """Return filtered user info dict with userName, realName, primaryEmail, uri."""
        response = self.phab.user.whoami()

        return {
            key: value
            for (key, value) in response.items()
            if key in ["userName", "realName", "primaryEmail", "uri"]
        }

    def whoami_configured_host(self):
        """Run whoami against the host phabfive is configured to use.

        Uses the PHAB_URL/PHAB_TOKEN that came out of the normal
        configuration chain, so it reports the host every other phabfive
        command talks to.

        Returns
        -------
        dict
            Same shape as one entry of :meth:`whoami_all_hosts`.
        """
        return self._whoami_for_host(
            self._normalize_url(self.conf["PHAB_URL"]),
            self.conf.get("PHAB_TOKEN"),
            phab=self.phab,
        )

    def _whoami_for_host(self, normalized_url, token, phab=None):
        """Run whoami against a single host and format the result.

        Parameters
        ----------
        normalized_url : str
            Full API URL, e.g. "https://phorge.example.com/api/".
        token : str or None
            API token for this host.
        phab : Phabricator, optional
            Existing client to reuse. A new one is built when omitted.

        Returns
        -------
        dict
            With "Host", "URL" and either "User" (plus "_link") or "Error".
        """
        parsed = urlparse(normalized_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        result = {
            "Host": parsed.netloc,
            "URL": normalized_url,
            "_base_url": base_url,
        }

        if not token:
            result["Error"] = "No token configured for this host"
            return result

        try:
            if phab is None:
                phab = Conduit(lambda: Phabricator(host=normalized_url, token=token))
            response = phab.user.whoami()

            user_name = response.get("userName", "")

            result["User"] = {
                "UserName": user_name,
                "RealName": response.get("realName", ""),
                "PrimaryEmail": response.get("primaryEmail", ""),
            }

            # Add _link for rich format (clickable hyperlink)
            result["_link"] = self.format_link(
                f"{base_url}/p/{user_name}/", user_name, show_url=False
            )
        except PhabfiveAPIException as e:
            result["Error"] = str(e).replace("ERR-CONDUIT-CORE: ", "")
        except PhabfiveRemoteException as e:
            result["Error"] = str(e)

        return result

    def whoami_all_hosts(self):
        """Run whoami against all hosts in ~/.arcrc.

        Returns
        -------
        list[dict]
            List of user info dicts, one per host. Each contains:
            - Host: FQDN only (e.g., "phabricator.example.com")
            - URL: Full API URL for PHAB_URL (e.g., "https://phabricator.example.com/api/")
            - User: dict with UserName, RealName, PrimaryEmail, Link
            - _link: Rich hyperlink to user profile (for rich format)
            - Error: error message if whoami failed for this host (optional)
        """
        arcrc_path = os.path.expanduser("~/.arcrc")

        if not os.path.exists(arcrc_path):
            raise PhabfiveConfigException(f"No .arcrc file found at {arcrc_path}")

        # Security check: ensure file has secure permissions
        self._check_arcrc_permissions(arcrc_path)

        try:
            with open(arcrc_path, "r") as f:
                arcrc_data = json.load(f)
        except json.JSONDecodeError as e:
            raise PhabfiveConfigException(f"Failed to parse {arcrc_path}: {e}")
        except IOError as e:
            raise PhabfiveConfigException(f"Failed to read {arcrc_path}: {e}")

        hosts = arcrc_data.get("hosts", {})

        if not hosts:
            raise PhabfiveConfigException("No hosts found in ~/.arcrc")

        results = []

        for host_uri, host_data in hosts.items():
            results.append(
                self._whoami_for_host(
                    self._normalize_url(host_uri), host_data.get("token")
                )
            )

        return results

    def _check_arcrc_permissions(self, file_path):
        """Check that ~/.arcrc has secure permissions.

        Note: This check is skipped on Windows.
        """
        # Skip permission check on Windows
        if os.name == "nt":
            return

        if not os.path.exists(file_path):
            return

        file_stat = os.stat(file_path)
        mode = file_stat.st_mode

        # Check if group or others have any permissions
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            actual_perms = oct(mode & 0o777)
            raise PhabfiveConfigException(
                f"{file_path} has insecure permissions ({actual_perms}). "
                "The file contains sensitive credentials and should only be readable by you. "
                f"Please run: chmod 600 {file_path}"
            )

    def search(
        self,
        query=None,
        username=None,
        realname=None,
        roles=None,
        not_roles=None,
        show_metadata=False,
        limit=None,
        ids=None,
        phids=None,
        usernames=None,
        created_after=None,
        created_before=None,
        order=None,
    ):
        """Search users, filtered by name and by the roles Phorge reports.

        Where each filter is applied is not an implementation detail here,
        because it decides what a search costs. `ids`, `phids`,
        `usernames`, the dates and the disabled, bot, list and admin roles
        are constraints user.search applies itself, so only matching users
        cross the wire. The rest - `username`, `realname`, and the
        verified, approved and activated roles, which have no constraint of
        their own - are matched on the records here, so a search using only
        those reads every user the token can see, a page at a time, and
        stops as soon as `limit` of them have matched.

        Parameters
        ----------
        query : str, optional
            Text to find anywhere in a username or real name, ignoring case:
            "holm" finds rholm and hholm
        username : str, optional
            Text to find anywhere in the username alone
        realname : str, optional
            Text to find anywhere in the real name alone
        roles : list, optional
            Roles a user must have, every one of them
        not_roles : list, optional
            Roles a user must not have, any of them
        show_metadata : bool, optional
            Include the Metadata section
        limit : int, optional
            How many matching users to return in total; None for all of them
        ids : list, optional
            User ids, as numbers. Every other filter still applies.
        phids : list, optional
            User PHIDs, the same way
        usernames : list, optional
            Exact usernames, which is what user.search's own constraint
            matches - `username` is the substring filter instead
        created_after, created_before : str or int, optional
            TIME values, e.g. "7d", "2w"
        order : str, optional
            Result ordering as "<field>[:asc|:desc]", e.g. "created:desc".
            The server does the ordering, so a limit keeps the first N of
            it. Defaults to username, which is the order this search has
            always printed.

        Returns
        -------
        list
            One record per user, in the order asked for; by username unless
            --order said otherwise

        Raises
        ------
        PhabfiveConfigException
            If a role is not one Phorge reports, or is both asked for and
            excluded, or the order is not one of USER_ORDER_FIELDS
        PhabfiveInputException
            If an id or a time is not one
        PhabfiveRemoteException
            If the search fails
        """
        # Resolved before anything is fetched, so a bad --order fails fast
        order_field, order_direction = parse_order(
            order,
            USER_ORDER_FIELDS,
            USER_ORDER_DIRECTIONS,
            USER_ORDER_DEFAULT,
            suggestions=USER_ORDER_SUGGESTIONS,
        )
        api_order = USER_API_ORDERS[(order_field, order_direction)]
        log.info(f"Ordering results by '{order_field}:{order_direction}'")

        roles = list(dict.fromkeys(roles or []))
        not_roles = list(dict.fromkeys(not_roles or []))

        unknown = [role for role in roles + not_roles if role not in USER_ROLES]
        if unknown:
            raise PhabfiveConfigException(
                f"Unknown role {', '.join(repr(role) for role in unknown)}, "
                f"expected one of: {', '.join(USER_ROLES)}"
            )

        both = [role for role in roles if role in not_roles]
        if both:
            raise PhabfiveConfigException(
                f"Cannot both require and exclude {', '.join(both)}"
            )

        constraints = {}

        # nameLike, not query. query is the full-text index, which matches
        # whole words only - "holm" found neither rholm nor hholm - while
        # nameLike matches any part of the username or the real name.
        #
        # There is no constraint for one of the two fields alone, and only one
        # nameLike, so --username and --realname are matched here. When nothing
        # else took nameLike, one of them is sent as it too: every user it
        # finds is a candidate, and it spares reading the whole instance.
        server_text = query or username or realname
        if server_text:
            constraints["nameLike"] = server_text

        id_list = user_id_list(ids)
        if id_list:
            constraints["ids"] = id_list

        phid_list = value_list(phids)
        if phid_list:
            constraints["phids"] = phid_list

        # Exact, where nameLike above matches any part of a name. Both can
        # be given: user.search ANDs its constraints.
        username_list = user_name_list(usernames)
        if username_list:
            constraints["usernames"] = username_list

        created_start = time_constraint(created_after, "created-after")
        if created_start is not None:
            constraints["createdStart"] = created_start

        created_end = time_constraint(created_before, "created-before")
        if created_end is not None:
            constraints["createdEnd"] = created_end

        # A role the server can filter on is sent as a constraint, so the
        # pages carry only matching users. The rest are matched here.
        for role, wanted in [(role, True) for role in roles] + [
            (role, False) for role in not_roles
        ]:
            if role in USER_ROLE_CONSTRAINTS:
                constraints[USER_ROLE_CONSTRAINTS[role]] = wanted

        local_roles = [role for role in roles if role not in USER_ROLE_CONSTRAINTS]
        local_not_roles = [
            role for role in not_roles if role not in USER_ROLE_CONSTRAINTS
        ]

        def matches(user):
            fields = user.get("fields", {})
            held = set(fields.get("roles") or [])

            if username and not _contains(fields.get("username"), username):
                return False
            if realname and not _contains(fields.get("realName"), realname):
                return False

            return all(role in held for role in local_roles) and not any(
                role in held for role in local_not_roles
            )

        filtering_here = bool(local_roles or local_not_roles or username or realname)

        users = []

        # A limit can only be forwarded when every match the server sends
        # counts; filtering here means counting matches ourselves, and
        # stopping as soon as there are enough.
        for page in iter_pages(
            self.phab.user.search,
            limit=None if filtering_here else limit,
            constraints=constraints,
            order=api_order,
        ):
            users.extend(user for user in page if matches(user))

            # The pages arrive in the order asked for, so stopping early
            # keeps the first N of that order rather than an arbitrary N
            if limit is not None and len(users) >= limit:
                users = users[:limit]
                break

        # The server already ordered these; this settles the ties
        users = sort_records(users, order_field, order_direction, USER_SORT_KEYS)

        return [self._user_record(user, show_metadata) for user in users]

    def _user_record(self, user, show_metadata=False):
        """One user as a display record.

        The User section is what `project show --show-members` lists for
        each member - Username, Name and Roles, the roles passed through
        unchanged - so the two can be compared line for line.
        """
        fields = user.get("fields", {})
        username = fields.get("username", "")
        url = f"{self.url}/p/{username}/"

        record = {
            "_url": url,
            "_link": self.format_link(url, username),
            "User": {
                "Username": username,
                "Name": fields.get("realName") or "",
                "Roles": list(fields.get("roles") or []),
            },
        }

        if show_metadata:
            record["Metadata"] = {
                "PHID": user.get("phid", ""),
                "ID": user.get("id"),
                "Created": format_timestamp(fields["dateCreated"])
                if fields.get("dateCreated")
                else "",
                "Modified": format_timestamp(fields["dateModified"])
                if fields.get("dateModified")
                else "",
            }

        return record


__all__ = [
    "USER_API_ORDERS",
    "USER_SORT_KEYS",
    "User",
    "user_id_list",
    "user_name_list",
]
