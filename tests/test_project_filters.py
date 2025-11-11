# -*- coding: utf-8 -*-

# 3rd party imports
import pytest

from phabfive.exceptions import PhabfiveException

# phabfive imports
from phabfive.project_filters import (
    ProjectPattern,
    _parse_single_project,
    parse_project_patterns,
)


class TestParseSingleProject:
    def test_parse_simple_project(self):
        result = _parse_single_project("ProjectA")
        assert result == "ProjectA"

    def test_parse_project_with_wildcard(self):
        result = _parse_single_project("Project*")
        assert result == "Project*"

    def test_parse_project_with_leading_wildcard(self):
        result = _parse_single_project("*Project")
        assert result == "*Project"

    def test_parse_project_with_both_wildcards(self):
        result = _parse_single_project("*Project*")
        assert result == "*Project*"

    def test_parse_empty_string(self):
        with pytest.raises(PhabfiveException) as exc:
            _parse_single_project("")
        assert "Empty project name" in str(exc.value)

    def test_parse_whitespace_only(self):
        with pytest.raises(PhabfiveException) as exc:
            _parse_single_project("   ")
        assert "Empty project name" in str(exc.value)

    def test_parse_with_leading_trailing_whitespace(self):
        result = _parse_single_project("  ProjectA  ")
        assert result == "ProjectA"


class TestProjectPatternMatches:
    def test_exact_match_single_project_with_spaces(self):
        """Test matching project names with spaces"""
        pattern = ProjectPattern(["Project A"])
        assert pattern.matches(["Project A"], {}) is True

    def test_exact_match_single_project(self):
        pattern = ProjectPattern(["ProjectA"])
        assert pattern.matches(["ProjectA"], {}) is True

    def test_exact_match_case_insensitive(self):
        pattern = ProjectPattern(["ProjectA"])
        assert pattern.matches(["projecta"], {}) is True
        assert pattern.matches(["PROJECTA"], {}) is True

    def test_no_match_different_project(self):
        pattern = ProjectPattern(["ProjectA"])
        assert pattern.matches(["ProjectB"], {}) is False

    def test_match_in_multiple_projects(self):
        pattern = ProjectPattern(["ProjectA"])
        assert pattern.matches(["ProjectA", "ProjectB"], {}) is True

    def test_wildcard_prefix_match(self):
        pattern = ProjectPattern(["Project*"])
        assert pattern.matches(["ProjectA"], {}) is True
        assert pattern.matches(["ProjectB"], {}) is True
        assert pattern.matches(["Project"], {}) is True

    def test_wildcard_prefix_no_match(self):
        pattern = ProjectPattern(["Project*"])
        assert pattern.matches(["MyProject"], {}) is False
        assert pattern.matches(["Legacy"], {}) is False

    def test_wildcard_suffix_match(self):
        pattern = ProjectPattern(["*Project"])
        assert pattern.matches(["MyProject"], {}) is True
        assert pattern.matches(["LegacyProject"], {}) is True
        assert pattern.matches(["Project"], {}) is True

    def test_wildcard_suffix_no_match(self):
        pattern = ProjectPattern(["*Project"])
        assert pattern.matches(["ProjectA"], {}) is False
        assert pattern.matches(["MyProjects"], {}) is False

    def test_wildcard_contains_match(self):
        pattern = ProjectPattern(["*Test*"])
        assert pattern.matches(["TestProject"], {}) is True
        assert pattern.matches(["MyTestProject"], {}) is True
        assert pattern.matches(["TestingCode"], {}) is True

    def test_wildcard_contains_no_match(self):
        pattern = ProjectPattern(["*Test*"])
        assert pattern.matches(["ProjectA"], {}) is False
        assert pattern.matches(["Code"], {}) is False

    def test_and_logic_both_match(self):
        """Test AND logic: both projects must match"""
        pattern = ProjectPattern(["ProjectA", "ProjectB"])
        assert pattern.matches(["ProjectA", "ProjectB"], {}) is True

    def test_and_logic_first_matches_second_doesnt(self):
        """Test AND logic: first matches but second doesn't"""
        pattern = ProjectPattern(["ProjectA", "ProjectB"])
        assert pattern.matches(["ProjectA", "ProjectC"], {}) is False

    def test_and_logic_neither_match(self):
        """Test AND logic: neither project matches"""
        pattern = ProjectPattern(["ProjectA", "ProjectB"])
        assert pattern.matches(["ProjectC", "ProjectD"], {}) is False

    def test_and_logic_with_wildcard(self):
        """Test AND logic with wildcard and exact match"""
        pattern = ProjectPattern(["Project*", "LegacyA"])
        assert pattern.matches(["ProjectA", "LegacyA"], {}) is True
        assert pattern.matches(["ProjectA"], {}) is False
        assert pattern.matches(["LegacyA"], {}) is False

    def test_empty_project_names(self):
        """Task with no projects should not match"""
        pattern = ProjectPattern(["ProjectA"])
        assert pattern.matches([], {}) is False

    def test_case_insensitive_wildcard(self):
        pattern = ProjectPattern(["project*"])
        assert pattern.matches(["PROJECTA"], {}) is True
        assert pattern.matches(["ProjectB"], {}) is True


class TestParseProjectPatterns:
    def test_single_project(self):
        patterns = parse_project_patterns("ProjectA")
        assert len(patterns) == 1
        assert patterns[0].project_names == ["ProjectA"]

    def test_comma_separated_projects_or_logic(self):
        """Comma creates OR between patterns"""
        patterns = parse_project_patterns("ProjectA,ProjectB")
        assert len(patterns) == 2
        assert patterns[0].project_names == ["ProjectA"]
        assert patterns[1].project_names == ["ProjectB"]

    def test_plus_separated_projects_and_logic(self):
        """Plus creates AND within a pattern"""
        patterns = parse_project_patterns("ProjectA+ProjectB")
        assert len(patterns) == 1
        assert patterns[0].project_names == ["ProjectA", "ProjectB"]

    def test_mixed_comma_and_plus(self):
        """Mixed: (ProjectA AND ProjectB) OR (ProjectC AND ProjectD)"""
        patterns = parse_project_patterns("ProjectA+ProjectB,ProjectC+ProjectD")
        assert len(patterns) == 2
        assert patterns[0].project_names == ["ProjectA", "ProjectB"]
        assert patterns[1].project_names == ["ProjectC", "ProjectD"]

    def test_wildcard_with_comma(self):
        patterns = parse_project_patterns("Project*,Legacy*")
        assert len(patterns) == 2
        assert patterns[0].project_names == ["Project*"]
        assert patterns[1].project_names == ["Legacy*"]

    def test_wildcard_with_plus(self):
        patterns = parse_project_patterns("Project*+Legacy*")
        assert len(patterns) == 1
        assert patterns[0].project_names == ["Project*", "Legacy*"]

    def test_empty_string(self):
        with pytest.raises(PhabfiveException) as exc:
            parse_project_patterns("")
        assert "Empty project pattern" in str(exc.value)

    def test_whitespace_only(self):
        with pytest.raises(PhabfiveException) as exc:
            parse_project_patterns("   ")
        assert "Empty project pattern" in str(exc.value)

    def test_trailing_comma(self):
        """Trailing comma creates empty group, which is skipped"""
        patterns = parse_project_patterns("ProjectA,")
        assert len(patterns) == 1
        assert patterns[0].project_names == ["ProjectA"]

    def test_trailing_plus(self):
        """Trailing plus creates empty part, which is skipped"""
        patterns = parse_project_patterns("ProjectA+")
        assert len(patterns) == 1
        assert patterns[0].project_names == ["ProjectA"]

    def test_leading_comma(self):
        """Leading comma creates empty group, which is skipped"""
        patterns = parse_project_patterns(",ProjectA")
        assert len(patterns) == 1
        assert patterns[0].project_names == ["ProjectA"]

    def test_multiple_commas(self):
        """Multiple commas create empty groups"""
        patterns = parse_project_patterns("ProjectA,,ProjectB")
        assert len(patterns) == 2
        assert patterns[0].project_names == ["ProjectA"]
        assert patterns[1].project_names == ["ProjectB"]

    def test_whitespace_around_operators(self):
        """Whitespace around operators should be trimmed"""
        patterns = parse_project_patterns("ProjectA , ProjectB + ProjectC")
        assert len(patterns) == 2
        assert patterns[0].project_names == ["ProjectA"]
        assert patterns[1].project_names == ["ProjectB", "ProjectC"]

    def test_complex_pattern(self):
        """Complex pattern: (A AND B) OR (C*) OR (D AND E*)"""
        patterns = parse_project_patterns("ProjectA+ProjectB,Project*,ProjectD+Legacy*")
        assert len(patterns) == 3
        assert patterns[0].project_names == ["ProjectA", "ProjectB"]
        assert patterns[1].project_names == ["Project*"]
        assert patterns[2].project_names == ["ProjectD", "Legacy*"]

    def test_case_sensitive_parsing_but_used_case_insensitive(self):
        """Project names are stored as-is but matched case-insensitive"""
        patterns = parse_project_patterns("ProjectA,projectb")
        assert len(patterns) == 2
        assert patterns[0].project_names == ["ProjectA"]
        assert patterns[1].project_names == ["projectb"]

    def test_project_names_with_spaces(self):
        """Project names can contain spaces"""
        patterns = parse_project_patterns("Project A,Project B")
        assert len(patterns) == 2
        assert patterns[0].project_names == ["Project A"]
        assert patterns[1].project_names == ["Project B"]

    def test_project_names_with_spaces_and_and_logic(self):
        """Project names with spaces and AND logic"""
        patterns = parse_project_patterns("Project A+Project B")
        assert len(patterns) == 1
        assert patterns[0].project_names == ["Project A", "Project B"]

    def test_project_names_with_spaces_mixed_operators(self):
        """Complex pattern with spaces: (Project A AND Project B) OR (Legacy Project C)"""
        patterns = parse_project_patterns("Project A+Project B,Legacy Project C")
        assert len(patterns) == 2
        assert patterns[0].project_names == ["Project A", "Project B"]
        assert patterns[1].project_names == ["Legacy Project C"]


class TestProjectPatternIntegration:
    """Integration tests for complete filtering scenarios"""

    def test_or_logic_first_pattern_matches(self):
        """OR logic: first pattern matches"""
        patterns = parse_project_patterns("ProjectA,ProjectB")
        task_projects = ["ProjectA"]
        # First pattern matches
        assert patterns[0].matches(task_projects, {}) is True
        # Check any pattern matches (simulating filter logic)
        assert any(p.matches(task_projects, {}) for p in patterns) is True

    def test_or_logic_second_pattern_matches(self):
        """OR logic: second pattern matches"""
        patterns = parse_project_patterns("ProjectA,ProjectB")
        task_projects = ["ProjectB"]
        # First pattern doesn't match
        assert patterns[0].matches(task_projects, {}) is False
        # Second pattern matches
        assert patterns[1].matches(task_projects, {}) is True
        # Check any pattern matches
        assert any(p.matches(task_projects, {}) for p in patterns) is True

    def test_or_logic_no_pattern_matches(self):
        """OR logic: no pattern matches"""
        patterns = parse_project_patterns("ProjectA,ProjectB")
        task_projects = ["ProjectC"]
        # Neither pattern matches
        assert all(not p.matches(task_projects, {}) for p in patterns) is True
        # Check any pattern matches
        assert any(p.matches(task_projects, {}) for p in patterns) is False

    def test_and_logic_all_conditions_match(self):
        """AND logic: all conditions in pattern match"""
        patterns = parse_project_patterns("ProjectA+ProjectB")
        task_projects = ["ProjectA", "ProjectB"]
        # Pattern matches
        assert patterns[0].matches(task_projects, {}) is True

    def test_and_logic_partial_conditions_match(self):
        """AND logic: only some conditions match"""
        patterns = parse_project_patterns("ProjectA+ProjectB")
        task_projects = ["ProjectA"]
        # Pattern doesn't match because ProjectB is missing
        assert patterns[0].matches(task_projects, {}) is False

    def test_wildcard_or_logic(self):
        """Wildcard with OR logic"""
        patterns = parse_project_patterns("Project*,Legacy*")
        # Matches first wildcard
        assert any(p.matches(["ProjectA"], {}) for p in patterns) is True
        # Matches second wildcard
        assert any(p.matches(["LegacyA"], {}) for p in patterns) is True
        # Matches neither wildcard
        assert any(p.matches(["CustomA"], {}) for p in patterns) is False
