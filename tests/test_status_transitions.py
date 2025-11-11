# -*- coding: utf-8 -*-

# 3rd party imports
import pytest

# phabfive imports
from phabfive.status_transitions import (
    StatusPattern,
    _parse_single_condition,
    parse_status_patterns,
    get_status_order,
)
from phabfive.exceptions import PhabfiveException


class TestGetStatusOrder:
    def test_open(self):
        assert get_status_order("Open") == 0

    def test_blocked(self):
        assert get_status_order("Blocked") == 1

    def test_wontfix(self):
        assert get_status_order("Wontfix") == 2

    def test_invalid(self):
        assert get_status_order("Invalid") == 3

    def test_duplicate(self):
        assert get_status_order("Duplicate") == 4

    def test_resolved(self):
        assert get_status_order("Resolved") == 5

    def test_case_insensitive(self):
        assert get_status_order("OPEN") == 0
        assert get_status_order("blocked") == 1
        assert get_status_order("ReSOlvEd") == 5

    def test_unknown_status(self):
        assert get_status_order("Unknown") is None

    def test_none_input(self):
        assert get_status_order(None) is None

    def test_empty_string(self):
        assert get_status_order("") is None


class TestParseSingleCondition:
    def test_parse_raised(self):
        result = _parse_single_condition("raised")
        assert result == {"type": "raised"}

    def test_parse_lowered(self):
        result = _parse_single_condition("lowered")
        assert result == {"type": "lowered"}

    def test_parse_from_simple(self):
        result = _parse_single_condition("from:Open")
        assert result == {"type": "from", "status": "Open"}

    def test_parse_from_with_raised(self):
        result = _parse_single_condition("from:Open:raised")
        assert result == {"type": "from", "status": "Open", "direction": "raised"}

    def test_parse_from_with_lowered(self):
        result = _parse_single_condition("from:Resolved:lowered")
        assert result == {"type": "from", "status": "Resolved", "direction": "lowered"}

    def test_parse_to(self):
        result = _parse_single_condition("to:Resolved")
        assert result == {"type": "to", "status": "Resolved"}

    def test_parse_in(self):
        result = _parse_single_condition("in:Open")
        assert result == {"type": "in", "status": "Open"}

    def test_parse_been(self):
        result = _parse_single_condition("been:Resolved")
        assert result == {"type": "been", "status": "Resolved"}

    def test_parse_never(self):
        result = _parse_single_condition("never:Resolved")
        assert result == {"type": "never", "status": "Resolved"}

    def test_invalid_type(self):
        with pytest.raises(PhabfiveException) as exc:
            _parse_single_condition("invalid:Open")
        assert "Invalid status condition type" in str(exc.value)

    def test_missing_colon(self):
        with pytest.raises(PhabfiveException) as exc:
            _parse_single_condition("notavalidpattern")
        assert "Invalid status condition syntax" in str(exc.value)

    def test_empty_status_name(self):
        with pytest.raises(PhabfiveException) as exc:
            _parse_single_condition("from:")
        assert "Empty status name" in str(exc.value)

    def test_direction_on_non_from(self):
        with pytest.raises(PhabfiveException) as exc:
            _parse_single_condition("to:Open:raised")
        assert "Direction modifier only allowed for 'from' patterns" in str(exc.value)

    def test_invalid_direction(self):
        with pytest.raises(PhabfiveException) as exc:
            _parse_single_condition("from:Open:invalid")
        assert "Invalid direction" in str(exc.value)

    def test_parse_not_in(self):
        result = _parse_single_condition("not:in:Open")
        assert result == {"type": "in", "status": "Open", "negated": True}

    def test_parse_not_from(self):
        result = _parse_single_condition("not:from:Resolved")
        assert result == {"type": "from", "status": "Resolved", "negated": True}

    def test_parse_not_from_with_direction(self):
        result = _parse_single_condition("not:from:Open:raised")
        assert result == {"type": "from", "status": "Open", "direction": "raised", "negated": True}

    def test_parse_not_been(self):
        result = _parse_single_condition("not:been:Resolved")
        assert result == {"type": "been", "status": "Resolved", "negated": True}

    def test_parse_not_raised(self):
        result = _parse_single_condition("not:raised")
        assert result == {"type": "raised", "negated": True}

    def test_parse_not_lowered(self):
        result = _parse_single_condition("not:lowered")
        assert result == {"type": "lowered", "negated": True}


class TestParseStatusPatterns:
    def test_single_simple_pattern(self):
        patterns = parse_status_patterns("raised")
        assert len(patterns) == 1
        assert len(patterns[0].conditions) == 1
        assert patterns[0].conditions[0]["type"] == "raised"

    def test_single_from_pattern(self):
        patterns = parse_status_patterns("from:Open:raised")
        assert len(patterns) == 1
        assert patterns[0].conditions[0] == {
            "type": "from",
            "status": "Open",
            "direction": "raised",
        }

    def test_or_patterns_comma(self):
        patterns = parse_status_patterns("in:Open,in:Resolved")
        assert len(patterns) == 2
        assert patterns[0].conditions[0] == {"type": "in", "status": "Open"}
        assert patterns[1].conditions[0] == {"type": "in", "status": "Resolved"}

    def test_and_conditions_plus(self):
        patterns = parse_status_patterns("from:Open+in:Resolved")
        assert len(patterns) == 1
        assert len(patterns[0].conditions) == 2
        assert patterns[0].conditions[0] == {"type": "from", "status": "Open"}
        assert patterns[0].conditions[1] == {"type": "in", "status": "Resolved"}

    def test_complex_pattern(self):
        patterns = parse_status_patterns("from:Open:raised+in:Resolved,to:Wontfix")
        assert len(patterns) == 2
        # First pattern: AND conditions
        assert len(patterns[0].conditions) == 2
        assert patterns[0].conditions[0] == {
            "type": "from",
            "status": "Open",
            "direction": "raised",
        }
        assert patterns[0].conditions[1] == {"type": "in", "status": "Resolved"}
        # Second pattern
        assert len(patterns[1].conditions) == 1
        assert patterns[1].conditions[0] == {"type": "to", "status": "Wontfix"}

    def test_empty_pattern(self):
        with pytest.raises(PhabfiveException) as exc:
            parse_status_patterns("")
        assert "Empty status pattern" in str(exc.value)

    def test_whitespace_only_pattern(self):
        with pytest.raises(PhabfiveException) as exc:
            parse_status_patterns("   ")
        assert "Empty status pattern" in str(exc.value)


class TestStatusPatternMatching:
    def test_matches_in_current_status(self):
        """Test 'in:STATUS' pattern matches current status"""
        pattern = StatusPattern([{"type": "in", "status": "Open"}])

        transactions = []
        current_status = "Open"

        assert pattern.matches(transactions, current_status) is True

    def test_matches_in_different_status(self):
        """Test 'in:STATUS' pattern doesn't match different status"""
        pattern = StatusPattern([{"type": "in", "status": "Resolved"}])

        transactions = []
        current_status = "Open"

        assert pattern.matches(transactions, current_status) is False

    def test_matches_from_status(self):
        """Test 'from:STATUS' pattern matches transition from that status"""
        pattern = StatusPattern([{"type": "from", "status": "Open"}])

        transactions = [
            {"oldValue": "Open", "newValue": "Resolved", "dateCreated": 1234567890}
        ]
        current_status = "Resolved"

        assert pattern.matches(transactions, current_status) is True

    def test_matches_to_status(self):
        """Test 'to:STATUS' pattern matches transition to that status"""
        pattern = StatusPattern([{"type": "to", "status": "Resolved"}])

        transactions = [
            {"oldValue": "Open", "newValue": "Resolved", "dateCreated": 1234567890}
        ]
        current_status = "Resolved"

        assert pattern.matches(transactions, current_status) is True

    def test_matches_been_status(self):
        """Test 'been:STATUS' pattern matches any occurrence of status"""
        pattern = StatusPattern([{"type": "been", "status": "Open"}])

        transactions = [
            {"oldValue": "Open", "newValue": "Blocked", "dateCreated": 1234567890},
            {"oldValue": "Blocked", "newValue": "Resolved", "dateCreated": 1234567900}
        ]
        current_status = "Resolved"

        assert pattern.matches(transactions, current_status) is True

    def test_matches_never_status(self):
        """Test 'never:STATUS' pattern matches when status never occurred"""
        pattern = StatusPattern([{"type": "never", "status": "Wontfix"}])

        transactions = [
            {"oldValue": "Open", "newValue": "Resolved", "dateCreated": 1234567890}
        ]
        current_status = "Resolved"

        assert pattern.matches(transactions, current_status) is True

    def test_matches_raised(self):
        """Test 'raised' pattern matches status progression"""
        pattern = StatusPattern([{"type": "raised"}])

        transactions = [
            {"oldValue": "Open", "newValue": "Resolved", "dateCreated": 1234567890}
        ]
        current_status = "Resolved"

        assert pattern.matches(transactions, current_status) is True

    def test_matches_lowered(self):
        """Test 'lowered' pattern matches status regression"""
        pattern = StatusPattern([{"type": "lowered"}])

        transactions = [
            {"oldValue": "Resolved", "newValue": "Open", "dateCreated": 1234567890}
        ]
        current_status = "Open"

        assert pattern.matches(transactions, current_status) is True

    def test_matches_not_in(self):
        """Test 'not:in:STATUS' pattern negates the match"""
        pattern = StatusPattern([{"type": "in", "status": "Open", "negated": True}])

        transactions = []
        current_status = "Resolved"

        assert pattern.matches(transactions, current_status) is True

    def test_matches_and_conditions(self):
        """Test multiple AND conditions must all match"""
        pattern = StatusPattern([
            {"type": "been", "status": "Open"},
            {"type": "in", "status": "Resolved"}
        ])

        transactions = [
            {"oldValue": "Open", "newValue": "Resolved", "dateCreated": 1234567890}
        ]
        current_status = "Resolved"

        assert pattern.matches(transactions, current_status) is True

    def test_matches_and_conditions_fail(self):
        """Test multiple AND conditions fail if any doesn't match"""
        pattern = StatusPattern([
            {"type": "been", "status": "Open"},
            {"type": "in", "status": "Wontfix"}
        ])

        transactions = [
            {"oldValue": "Open", "newValue": "Resolved", "dateCreated": 1234567890}
        ]
        current_status = "Resolved"

        assert pattern.matches(transactions, current_status) is False

    def test_matches_from_with_raised_direction(self):
        """Test 'from:STATUS:raised' matches status change with progression"""
        pattern = StatusPattern([{"type": "from", "status": "Open", "direction": "raised"}])

        transactions = [
            {"oldValue": "Open", "newValue": "Resolved", "dateCreated": 1234567890}
        ]
        current_status = "Resolved"

        assert pattern.matches(transactions, current_status) is True

    def test_matches_from_with_lowered_direction(self):
        """Test 'from:STATUS:lowered' matches status change with regression"""
        pattern = StatusPattern([{"type": "from", "status": "Resolved", "direction": "lowered"}])

        transactions = [
            {"oldValue": "Resolved", "newValue": "Open", "dateCreated": 1234567890}
        ]
        current_status = "Open"

        assert pattern.matches(transactions, current_status) is True

    def test_case_insensitive_matching(self):
        """Test status matching is case-insensitive"""
        pattern = StatusPattern([{"type": "in", "status": "OPEN"}])

        transactions = []
        current_status = "open"

        assert pattern.matches(transactions, current_status) is True
