# -*- coding: utf-8 -*-

# python std lib
import fnmatch
import logging

# phabfive imports
from phabfive.exceptions import PhabfiveInputException

log = logging.getLogger(__name__)


class ProjectPattern:
    """
    Represents a single project pattern with AND conditions.

    A pattern can have multiple conditions that must all match (AND logic).
    Multiple patterns can be combined with OR logic.

    Each condition is a project name or wildcard pattern. A task matches
    if it is assigned to all projects in the pattern (AND logic).
    """

    def __init__(self, project_names):
        """
        Parameters
        ----------
        project_names : list
            List of project names or wildcard patterns that must all match (AND logic).
            Examples: ["ProjectA"], ["ProjectA", "ProjectB"], ["Project*"]
        """
        self.project_names = project_names

    def __str__(self):
        """Return string representation of the pattern."""
        return "+".join(self.project_names)

    def matches(self, task_project_names, resolved_projects_map):
        """
        Check if all conditions in this pattern match (AND logic).

        A task matches if it belongs to all projects in this pattern.
        Project names support wildcards using fnmatch.

        Parameters
        ----------
        task_project_names : list
            List of project names that this task belongs to
        resolved_projects_map : dict
            Mapping of resolved project patterns to PHIDs.
            Example: {"ProjectA": "PHID-PROJ-abc", "ProjectB*": ["PHID-PROJ-xyz", ...]}

        Returns
        -------
        bool
            True if task belongs to all projects in this pattern, False otherwise
        """
        # All project conditions must match for the pattern to match
        for project_pattern in self.project_names:
            if not self._matches_project(
                project_pattern, task_project_names, resolved_projects_map
            ):
                return False
        return True

    def _matches_project(
        self, project_pattern, task_project_names, resolved_projects_map
    ):
        """
        Check if a task belongs to a project matching the given pattern.

        Parameters
        ----------
        project_pattern : str
            Project name or wildcard pattern (e.g., "ProjectA", "Project*")
        task_project_names : list
            List of project names the task belongs to
        resolved_projects_map : dict
            Mapping of resolved project patterns to PHIDs

        Returns
        -------
        bool
            True if task belongs to a project matching the pattern
        """
        # Check if pattern is a wildcard
        has_wildcard = "*" in project_pattern

        if has_wildcard:
            # Check if task's projects match the wildcard pattern
            for task_project in task_project_names:
                if fnmatch.fnmatch(task_project.lower(), project_pattern.lower()):
                    return True
            return False
        else:
            # Exact match (case-insensitive)
            project_pattern_lower = project_pattern.lower()
            for task_project in task_project_names:
                if task_project.lower() == project_pattern_lower:
                    return True
            return False


def _parse_single_project(project_str):
    """
    Parse a single project name or wildcard pattern.

    Parameters
    ----------
    project_str : str
        Project name or wildcard pattern (e.g., "ProjectA", "Project*")

    Returns
    -------
    str
        Project name or pattern

    Raises
    ------
    PhabfiveException
        If project name is empty
    """
    project_str = project_str.strip()

    if not project_str:
        raise PhabfiveInputException("Empty project name in pattern")

    return project_str


def parse_project_patterns(patterns_str):
    """
    Parse project pattern string into list of ProjectPattern objects.

    Supports:
    - Comma (,) for OR logic between patterns
    - Plus (+) for AND logic within a pattern
    - Wildcard (*) for pattern matching

    Parameters
    ----------
    patterns_str : str
        Pattern string like "ProjectA,ProjectB*" or "ProjectA+ProjectB"

    Returns
    -------
    list
        List of ProjectPattern objects

    Raises
    ------
    PhabfiveInputException
        If the string is empty, or a name on either side of a ``,`` or
        ``+`` is empty (``"a+"``, ``"a,,b"``)

    Examples
    --------
    >>> parse_project_patterns("ProjectA")
    [ProjectPattern(["ProjectA"])]

    >>> parse_project_patterns("ProjectA,ProjectB*")
    [ProjectPattern(["ProjectA"]), ProjectPattern(["ProjectB*"])]

    >>> parse_project_patterns("ProjectA+ProjectB")
    [ProjectPattern(["ProjectA", "ProjectB"])]
    """
    if not patterns_str or not patterns_str.strip():
        raise PhabfiveInputException("Empty project pattern")

    patterns = []

    # Every name has to be there: skipping an empty one would turn a
    # half-built `team+` into `team`, an AND search into a wider one that
    # still looks like an answer (#526). An empty OR group is refused too,
    # for one rule rather than two
    for or_group in patterns_str.split(","):
        if not or_group.strip():
            raise PhabfiveInputException("Empty project name next to ','")

        project_names = []
        for and_part in or_group.split("+"):
            if not and_part.strip():
                raise PhabfiveInputException(
                    "Empty project name next to '+'. A project whose name "
                    "contains '+' or ',' is named by its hashtag or ID"
                )
            project_names.append(_parse_single_project(and_part))

        patterns.append(ProjectPattern(project_names))

    return patterns
