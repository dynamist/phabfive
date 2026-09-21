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
from phabfive.constants import USER_ROLE_CONSTRAINTS, USER_ROLES
from phabfive.conduit import Conduit
from phabfive.core import Phabfive
from phabfive.exceptions import (
    PhabfiveAPIException,
    PhabfiveConfigException,
    PhabfiveRemoteException,
)
from phabfive.maniphest.utils import format_timestamp
from phabfive.pagination import iter_pages

log = logging.getLogger(__name__)


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
    ):
        """Search users, filtered by name and by the roles Phorge reports.

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

        Returns
        -------
        list
            One record per user, sorted by username

        Raises
        ------
        PhabfiveConfigException
            If a role is not one Phorge reports, or is both asked for and
            excluded
        PhabfiveRemoteException
            If the search fails
        """
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
        ):
            users.extend(user for user in page if matches(user))

            if limit is not None and len(users) >= limit:
                users = users[:limit]
                break

        users.sort(key=lambda user: user["fields"]["username"].casefold())

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


__all__ = ["User"]
