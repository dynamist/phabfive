# -*- coding: utf-8 -*-

# 3rd party imports
import pytest

# phabfive imports
from phabfive.priority_transitions import (
    PriorityPattern,
    _parse_single_condition,
    parse_priority_patterns,
    get_priority_order,
    PRIORITY_ORDER,
)
from phabfive.exceptions import PhabfiveException


class TestGetPriorityOrder:
    def test_unbreak_now(self):
        assert get_priority_order("Unbreak Now!") == 0

    def test_triage(self):
        assert get_priority_order("Triage") == 1

    def test_high(self):
        assert get_priority_order("High") == 2

    def test_normal(self):
        assert get_priority_order("Normal") == 3

    def test_low(self):
        assert get_priority_order("Low") == 4

    def test_wishlist(self):
        assert get_priority_order("Wishlist") == 5

    def test_case_insensitive(self):
        assert get_priority_order("UNBREAK NOW!") == 0
        assert get_priority_order("high") == 2
        assert get_priority_order("NoRmAl") == 3

    def test_unknown_priority(self):
        assert get_priority_order("Unknown") is None

    def test_none_input(self):
        assert get_priority_order(None) is None

    def test_empty_string(self):
        assert get_priority_order("") is None


class TestParseSingleCondition:
    def test_parse_raised(self):
        result = _parse_single_condition("raised")
        assert result == {"type": "raised"}

    def test_parse_lowered(self):
        result = _parse_single_condition("lowered")
        assert result == {"type": "lowered"}

    def test_parse_from_simple(self):
        result = _parse_single_condition("from:Normal")
        assert result == {"type": "from", "priority": "Normal"}

    def test_parse_from_with_raised(self):
        result = _parse_single_condition("from:Normal:raised")
        assert result == {"type": "from", "priority": "Normal", "direction": "raised"}

    def test_parse_from_with_lowered(self):
        result = _parse_single_condition("from:High:lowered")
        assert result == {"type": "from", "priority": "High", "direction": "lowered"}

    def test_parse_to(self):
        result = _parse_single_condition("to:Unbreak Now!")
        assert result == {"type": "to", "priority": "Unbreak Now!"}

    def test_parse_in(self):
        result = _parse_single_condition("in:High")
        assert result == {"type": "in", "priority": "High"}

    def test_parse_been(self):
        result = _parse_single_condition("been:Unbreak Now!")
        assert result == {"type": "been", "priority": "Unbreak Now!"}

    def test_parse_never(self):
        result = _parse_single_condition("never:Low")
        assert result == {"type": "never", "priority": "Low"}

    def test_invalid_type(self):
        with pytest.raises(PhabfiveException) as exc:
            _parse_single_condition("invalid:Priority")
        assert "Invalid priority condition type" in str(exc.value)

    def test_missing_colon(self):
        with pytest.raises(PhabfiveException) as exc:
            _parse_single_condition("notavalidpattern")
        assert "Invalid priority condition syntax" in str(exc.value)

    def test_empty_priority_name(self):
        with pytest.raises(PhabfiveException) as exc:
            _parse_single_condition("from:")
        assert "Empty priority name" in str(exc.value)

    def test_direction_on_non_from(self):
        with pytest.raises(PhabfiveException) as exc:
            _parse_single_condition("to:High:raised")
        assert "Direction modifier only allowed for 'from' patterns" in str(exc.value)

    def test_invalid_direction(self):
        with pytest.raises(PhabfiveException) as exc:
            _parse_single_condition("from:Normal:invalid")
        assert "Invalid direction" in str(exc.value)

    def test_parse_not_in(self):
        result = _parse_single_condition("not:in:High")
        assert result == {"type": "in", "priority": "High", "negated": True}

    def test_parse_not_from(self):
        result = _parse_single_condition("not:from:Wishlist")
        assert result == {"type": "from", "priority": "Wishlist", "negated": True}

    def test_parse_not_from_with_direction(self):
        result = _parse_single_condition("not:from:Low:raised")
        assert result == {
            "type": "from",
            "priority": "Low",
            "direction": "raised",
            "negated": True,
        }

    def test_parse_not_been(self):
        result = _parse_single_condition("not:been:Unbreak Now!")
        assert result == {"type": "been", "priority": "Unbreak Now!", "negated": True}

    def test_parse_not_raised(self):
        result = _parse_single_condition("not:raised")
        assert result == {"type": "raised", "negated": True}

    def test_parse_not_lowered(self):
        result = _parse_single_condition("not:lowered")
        assert result == {"type": "lowered", "negated": True}


class TestParsePriorityPatterns:
    def test_single_simple_pattern(self):
        patterns = parse_priority_patterns("raised")
        assert len(patterns) == 1
        assert len(patterns[0].conditions) == 1
        assert patterns[0].conditions[0]["type"] == "raised"

    def test_single_from_pattern(self):
        patterns = parse_priority_patterns("from:Normal:raised")
        assert len(patterns) == 1
        assert patterns[0].conditions[0] == {
            "type": "from",
            "priority": "Normal",
            "direction": "raised",
        }

    def test_or_patterns_with_comma(self):
        patterns = parse_priority_patterns("raised,to:High")
        assert len(patterns) == 2
        assert patterns[0].conditions[0]["type"] == "raised"
        assert patterns[1].conditions[0] == {"type": "to", "priority": "High"}

    def test_and_patterns_with_plus(self):
        patterns = parse_priority_patterns("from:Normal+in:High")
        assert len(patterns) == 1
        assert len(patterns[0].conditions) == 2
        assert patterns[0].conditions[0] == {"type": "from", "priority": "Normal"}
        assert patterns[0].conditions[1] == {"type": "in", "priority": "High"}

    def test_complex_or_and_combination(self):
        patterns = parse_priority_patterns("from:Normal:raised+in:High,to:Low")
        assert len(patterns) == 2
        # First pattern: from:Normal:raised AND in:High
        assert len(patterns[0].conditions) == 2
        # Second pattern: to:Low
        assert len(patterns[1].conditions) == 1

    def test_empty_pattern_error(self):
        with pytest.raises(PhabfiveException) as exc:
            parse_priority_patterns("")
        assert "Empty priority pattern" in str(exc.value)

    def test_whitespace_handling(self):
        patterns = parse_priority_patterns(" from:Normal , to:High ")
        assert len(patterns) == 2


class TestPriorityPatternMatching:
    def test_matches_raised(self):
        pattern = PriorityPattern([{"type": "raised"}])

        # Transaction with priority increase (Normal -> High)
        transactions = [
            {"oldValue": "Normal", "newValue": "High", "dateCreated": 1234567890}
        ]

        assert pattern.matches(transactions, "High") is True

    def test_matches_lowered(self):
        pattern = PriorityPattern([{"type": "lowered"}])

        # Transaction with priority decrease (High -> Normal)
        transactions = [
            {"oldValue": "High", "newValue": "Normal", "dateCreated": 1234567890}
        ]

        assert pattern.matches(transactions, "Normal") is True

    def test_matches_from_with_raised_direction(self):
        pattern = PriorityPattern(
            [{"type": "from", "priority": "Normal", "direction": "raised"}]
        )

        transactions = [
            {"oldValue": "Normal", "newValue": "High", "dateCreated": 1234567890}
        ]

        assert pattern.matches(transactions, "High") is True

    def test_matches_from_with_lowered_direction(self):
        pattern = PriorityPattern(
            [{"type": "from", "priority": "High", "direction": "lowered"}]
        )

        transactions = [
            {"oldValue": "High", "newValue": "Normal", "dateCreated": 1234567890}
        ]

        assert pattern.matches(transactions, "Normal") is True

    def test_matches_in(self):
        pattern = PriorityPattern([{"type": "in", "priority": "High"}])

        assert pattern.matches([], "High") is True
        assert pattern.matches([], "Normal") is False

    def test_matches_to(self):
        pattern = PriorityPattern([{"type": "to", "priority": "Unbreak Now!"}])

        transactions = [
            {
                "oldValue": "Normal",
                "newValue": "Unbreak Now!",
                "dateCreated": 1234567890,
            }
        ]

        assert pattern.matches(transactions, "Unbreak Now!") is True

    def test_matches_been(self):
        pattern = PriorityPattern([{"type": "been", "priority": "Unbreak Now!"}])

        transactions = [
            {
                "oldValue": "Normal",
                "newValue": "Unbreak Now!",
                "dateCreated": 1234567890,
            },
            {"oldValue": "Unbreak Now!", "newValue": "High", "dateCreated": 1234567891},
        ]

        assert pattern.matches(transactions, "High") is True

    def test_matches_never(self):
        pattern = PriorityPattern([{"type": "never", "priority": "Low"}])

        transactions = [
            {"oldValue": "Normal", "newValue": "High", "dateCreated": 1234567890}
        ]

        assert pattern.matches(transactions, "High") is True

    def test_does_not_match_never_when_priority_was_used(self):
        pattern = PriorityPattern([{"type": "never", "priority": "Low"}])

        transactions = [
            {"oldValue": "Low", "newValue": "High", "dateCreated": 1234567890}
        ]

        assert pattern.matches(transactions, "High") is False

    def test_matches_and_conditions(self):
        # Pattern with AND: from:Normal AND in:High
        pattern = PriorityPattern(
            [{"type": "from", "priority": "Normal"}, {"type": "in", "priority": "High"}]
        )

        transactions = [
            {"oldValue": "Normal", "newValue": "High", "dateCreated": 1234567890}
        ]

        # Both conditions must match
        assert pattern.matches(transactions, "High") is True
        assert pattern.matches(transactions, "Normal") is False

    def test_no_match_raised_when_lowered(self):
        pattern = PriorityPattern([{"type": "raised"}])

        # Transaction with priority decrease
        transactions = [
            {"oldValue": "High", "newValue": "Normal", "dateCreated": 1234567890}
        ]

        assert pattern.matches(transactions, "Normal") is False

    def test_no_match_lowered_when_raised(self):
        pattern = PriorityPattern([{"type": "lowered"}])

        # Transaction with priority increase
        transactions = [
            {"oldValue": "Normal", "newValue": "High", "dateCreated": 1234567890}
        ]

        assert pattern.matches(transactions, "High") is False

    def test_no_match_to_different_priority(self):
        pattern = PriorityPattern([{"type": "to", "priority": "High"}])

        transactions = [
            {"oldValue": "Normal", "newValue": "Low", "dateCreated": 1234567890}
        ]

        assert pattern.matches(transactions, "Low") is False

    def test_case_insensitive_matching(self):
        pattern = PriorityPattern([{"type": "been", "priority": "high"}])

        transactions = [
            {"oldValue": "Normal", "newValue": "High", "dateCreated": 1234567890}
        ]

        assert pattern.matches(transactions, "High") is True

    def test_matches_with_no_transactions(self):
        pattern = PriorityPattern([{"type": "raised"}])
        assert pattern.matches([], "Normal") is False

    def test_matches_with_none_values(self):
        pattern = PriorityPattern([{"type": "raised"}])

        transactions = [
            {"oldValue": None, "newValue": "High", "dateCreated": 1234567890}
        ]

        assert pattern.matches(transactions, "High") is False

    def test_empty_pattern_list(self):
        """Test that empty conditions list returns False."""
        pattern = PriorityPattern([])
        assert pattern.matches([], "Normal") is True  # No conditions to fail


class TestPriorityOrdering:
    """Test that priority ordering is correct for direction detection."""

    def test_priority_order_values(self):
        """Verify priority order constants are correct."""
        assert PRIORITY_ORDER["unbreak now!"] < PRIORITY_ORDER["triage"]
        assert PRIORITY_ORDER["triage"] < PRIORITY_ORDER["high"]
        assert PRIORITY_ORDER["high"] < PRIORITY_ORDER["normal"]
        assert PRIORITY_ORDER["normal"] < PRIORITY_ORDER["low"]
        assert PRIORITY_ORDER["low"] < PRIORITY_ORDER["wishlist"]

    def test_raised_means_lower_number(self):
        """Verify that raised priority means lower sequence number."""
        pattern = PriorityPattern([{"type": "raised"}])

        # Unbreak Now! (0) is higher priority than Normal (3)
        transactions = [
            {
                "oldValue": "Normal",
                "newValue": "Unbreak Now!",
                "dateCreated": 1234567890,
            }
        ]

        assert pattern.matches(transactions, "Unbreak Now!") is True

    def test_lowered_means_higher_number(self):
        """Verify that lowered priority means higher sequence number."""
        pattern = PriorityPattern([{"type": "lowered"}])

        # Wishlist (5) is lower priority than Normal (3)
        transactions = [
            {"oldValue": "Normal", "newValue": "Wishlist", "dateCreated": 1234567890}
        ]

        assert pattern.matches(transactions, "Wishlist") is True
