# -*- coding: utf-8 -*-

# 3rd party imports
from unittest.mock import MagicMock, patch

import pytest

# phabfive imports
from phabfive.cli import completers
from phabfive.cli.completers import (
    USER_COMPLETION_LIMIT,
    complete_user,
    complete_user_filter,
    complete_user_list,
    complete_user_list_filter,
)


def _user(id_, username, real_name="", disabled=False):
    return {
        "id": id_,
        "phid": f"PHID-USER-{id_}",
        "fields": {
            "username": username,
            "realName": real_name,
            "roles": ["disabled"] if disabled else ["activated"],
        },
    }


def _phab(users, page_size=100):
    """Fake user.search: substring "nameLike", isDisabled, paged by cursor."""
    phab = MagicMock()

    def search(constraints, limit=100, after=None):
        matches = users
        if "nameLike" in constraints:
            text = constraints["nameLike"].lower()
            matches = [
                u
                for u in matches
                if text in u["fields"]["username"].lower()
                or text in u["fields"]["realName"].lower()
            ]
        if constraints.get("isDisabled") is False:
            matches = [u for u in matches if "disabled" not in u["fields"]["roles"]]
        start = int(after or 0)
        size = min(limit, page_size)
        page = matches[start : start + size]
        next_after = str(start + size) if start + size < len(matches) else None
        return {"data": page, "cursor": {"after": next_after}}

    phab.user.search.side_effect = search
    return phab


def _complete_with(phab, incomplete, completer=complete_user):
    """Run a username completer against a fake API client."""
    with patch.object(
        completers,
        "_get_values_with_api_fallback",
        side_effect=lambda fetch, default: fetch(phab),
    ):
        return completer(incomplete)


USERS = [
    _user(1, "sonja.bergstrom", "Sonja Bergstrom"),
    _user(2, "tommy.svensson", "Tommy Svensson"),
    _user(3, "sofia.lind", "Sofia Lind"),
    _user(4, "bot.builder"),
    _user(5, "sven.retired", "Sven Retired", disabled=True),
]


class TestServerSideLookup:
    def test_passes_typed_text_as_namelike_constraint(self):
        phab = _phab(USERS)
        _complete_with(phab, "son")
        assert phab.user.search.call_args.kwargs["constraints"] == {
            "nameLike": "son",
            "isDisabled": False,
        }

    def test_no_name_constraint_without_typed_text(self):
        phab = _phab(USERS)
        _complete_with(phab, "")
        assert phab.user.search.call_args.kwargs["constraints"] == {"isDisabled": False}

    def test_filter_includes_disabled_accounts(self):
        phab = _phab(USERS)
        _complete_with(phab, "sv", completer=complete_user_filter)
        assert phab.user.search.call_args.kwargs["constraints"] == {"nameLike": "sv"}

    def test_follows_cursor_beyond_first_page(self):
        users = [_user(i, f"user{i:03d}") for i in range(250)]
        phab = _phab(users, page_size=100)

        result = _complete_with(phab, "user")

        assert len(result) == 250
        assert "user249" in result
        assert phab.user.search.call_count == 3

    def test_stops_at_limit(self):
        users = [_user(i, f"user{i:04d}") for i in range(USER_COMPLETION_LIMIT + 300)]
        phab = _phab(users, page_size=100)

        _complete_with(phab, "user")

        assert phab.user.search.call_count == USER_COMPLETION_LIMIT // 100

    def test_api_failure_offers_nothing(self):
        with patch.object(completers, "_get_values_with_api_fallback", return_value=[]):
            assert complete_user("son") == []

    def test_me_shortcut_skips_the_api(self):
        phab = _phab(USERS)
        assert _complete_with(phab, "@m") == [("@me", "yourself")]
        phab.user.search.assert_not_called()


class TestMatching:
    def test_only_usernames_starting_with_typed_text(self):
        """nameLike also matches later words and real names."""
        result = _complete_with(_phab(USERS), "son")
        assert result == [("sonja.bergstrom", "Sonja Bergstrom")]

    def test_real_name_match_alone_is_not_offered(self):
        """Typer drops completions that don't start with the typed text."""
        assert _complete_with(_phab(USERS), "Bergstrom") == []

    def test_username_without_real_name_has_no_description(self):
        assert _complete_with(_phab(USERS), "bot") == ["bot.builder"]

    def test_disabled_account_is_not_offered_for_assigning(self):
        assert _complete_with(_phab(USERS), "sven") == []

    def test_disabled_account_is_offered_for_filtering(self):
        assert _complete_with(_phab(USERS), "sven", completer=complete_user_filter) == [
            ("sven.retired", "Sven Retired")
        ]

    def test_offered_in_username_order(self):
        result = _complete_with(_phab(USERS), "so")
        assert [value for value, _ in result] == ["sofia.lind", "sonja.bergstrom"]

    @pytest.mark.parametrize(
        "incomplete, expected",
        [
            ("son", "sonja.bergstrom"),
            ("SON", "SONJA.BERGSTROM"),
            ("Son", "Sonja.bergstrom"),
        ],
    )
    def test_follows_typed_case(self, incomplete, expected):
        """The user.search "usernames" constraint resolves case-insensitively."""
        assert _complete_with(_phab(USERS), incomplete)[0][0] == expected


class TestMeShortcut:
    def test_offered_before_anything_is_typed(self):
        assert _complete_with(_phab(USERS), "")[0] == ("@me", "yourself")

    @pytest.mark.parametrize("incomplete", ["@", "@m", "@me"])
    def test_offered_for_what_the_user_typed(self, incomplete):
        assert _complete_with(_phab(USERS), incomplete) == [("@me", "yourself")]

    def test_not_offered_once_a_username_is_typed(self):
        assert ("@me", "yourself") not in _complete_with(_phab(USERS), "so")

    def test_unknown_at_value_offers_nothing(self):
        assert _complete_with(_phab(USERS), "@you") == []


class TestUserListFilter:
    """Shared by maniphest search --assigned and --author."""

    def test_completes_a_single_name(self):
        assert _complete_with(
            _phab(USERS), "son", completer=complete_user_list_filter
        ) == [("sonja.bergstrom", "Sonja Bergstrom")]

    def test_keeps_the_names_already_typed(self):
        result = _complete_with(
            _phab(USERS), "@me,son", completer=complete_user_list_filter
        )
        assert result == [("@me,sonja.bergstrom", "Sonja Bergstrom")]

    def test_completes_after_a_trailing_comma(self):
        result = _complete_with(
            _phab(USERS), "@me,", completer=complete_user_list_filter
        )
        assert ("@me,@me", "yourself") in result
        assert ("@me,sonja.bergstrom", "Sonja Bergstrom") in result

    def test_looks_up_only_the_name_after_the_last_comma(self):
        phab = _phab(USERS)
        _complete_with(phab, "tommy.svensson,son", completer=complete_user_list_filter)
        assert phab.user.search.call_args.kwargs["constraints"]["nameLike"] == "son"

    def test_includes_disabled_accounts(self):
        result = _complete_with(
            _phab(USERS), "@me,sven", completer=complete_user_list_filter
        )
        assert result == [("@me,sven.retired", "Sven Retired")]


class TestUserList:
    """Shared by --subscribe on maniphest create/edit and paste create/edit."""

    def test_keeps_the_names_already_typed(self):
        result = _complete_with(_phab(USERS), "@me,son", completer=complete_user_list)
        assert result == [("@me,sonja.bergstrom", "Sonja Bergstrom")]

    def test_completes_after_a_trailing_comma(self):
        result = _complete_with(_phab(USERS), "@me,", completer=complete_user_list)
        assert ("@me,@me", "yourself") in result
        assert ("@me,sonja.bergstrom", "Sonja Bergstrom") in result

    def test_looks_up_only_the_name_after_the_last_comma(self):
        phab = _phab(USERS)
        _complete_with(phab, "tommy.svensson,son", completer=complete_user_list)
        assert phab.user.search.call_args.kwargs["constraints"]["nameLike"] == "son"

    def test_leaves_out_disabled_accounts(self):
        assert (
            _complete_with(_phab(USERS), "@me,sven", completer=complete_user_list) == []
        )
