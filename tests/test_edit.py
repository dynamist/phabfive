# -*- coding: utf-8 -*-

"""Tests for phabfive edit command."""

# python std lib
from unittest import mock

# 3rd party imports
import pytest


class TestMonogramDetection:
    """Tests for monogram parsing and detection."""

    def test_parse_task_monogram_uppercase(self):
        """Test parsing T123 monogram."""
        from phabfive.edit import Edit

        edit_app = Edit()
        obj_type, obj_id = edit_app._parse_monogram("T123")
        assert obj_type == "task"
        assert obj_id == "123"

    def test_parse_task_monogram_lowercase(self):
        """Test parsing t456 monogram."""
        from phabfive.edit import Edit

        edit_app = Edit()
        obj_type, obj_id = edit_app._parse_monogram("t456")
        assert obj_type == "task"
        assert obj_id == "456"

    def test_parse_task_monogram_from_url(self):
        """Test extracting T789 from URL."""
        from phabfive.edit import Edit

        edit_app = Edit()
        obj_type, obj_id = edit_app._parse_monogram("https://phorge.example.com/T789")
        assert obj_type == "task"
        assert obj_id == "789"

    def test_parse_passphrase_monogram(self):
        """Test parsing K123 monogram."""
        from phabfive.edit import Edit

        edit_app = Edit()
        obj_type, obj_id = edit_app._parse_monogram("K123")
        assert obj_type == "passphrase"
        assert obj_id == "123"

    def test_parse_paste_monogram(self):
        """Test parsing P456 monogram."""
        from phabfive.edit import Edit

        edit_app = Edit()
        obj_type, obj_id = edit_app._parse_monogram("P456")
        assert obj_type == "paste"
        assert obj_id == "456"

    def test_parse_invalid_monogram(self):
        """Test parsing invalid monogram raises ValueError."""
        from phabfive.edit import Edit

        edit_app = Edit()
        with pytest.raises(ValueError, match="No valid monogram found"):
            edit_app._parse_monogram("invalid")


class TestPriorityNavigation:
    """Tests for priority raise/lower navigation."""

    def test_raise_from_wish_to_low(self):
        """Test raising priority from Wish to Low."""
        from phabfive.maniphest import Maniphest

        m = Maniphest()
        result = m._navigate_priority(0, "raise")
        assert result == "low"

    def test_raise_from_low_to_normal(self):
        """Test raising priority from Low to Normal."""
        from phabfive.maniphest import Maniphest

        m = Maniphest()
        result = m._navigate_priority(25, "raise")
        assert result == "normal"

    def test_raise_from_normal_to_high(self):
        """Test raising priority from Normal to High."""
        from phabfive.maniphest import Maniphest

        m = Maniphest()
        result = m._navigate_priority(50, "raise")
        assert result == "high"

    def test_raise_from_high_to_unbreak_skips_triage(self):
        """Test raising from High skips Triage and goes to Unbreak."""
        from phabfive.maniphest import Maniphest

        m = Maniphest()
        result = m._navigate_priority(80, "raise")
        assert result == "unbreak"

    def test_raise_from_unbreak_stays_at_unbreak(self):
        """Test raising from Unbreak stays at Unbreak (edge case)."""
        from phabfive.maniphest import Maniphest

        m = Maniphest()
        result = m._navigate_priority(100, "raise")
        assert result == "unbreak"

    def test_lower_from_unbreak_to_high_skips_triage(self):
        """Test lowering from Unbreak skips Triage and goes to High."""
        from phabfive.maniphest import Maniphest

        m = Maniphest()
        result = m._navigate_priority(100, "lower")
        assert result == "high"

    def test_lower_from_high_to_normal(self):
        """Test lowering priority from High to Normal."""
        from phabfive.maniphest import Maniphest

        m = Maniphest()
        result = m._navigate_priority(80, "lower")
        assert result == "normal"

    def test_lower_from_normal_to_low(self):
        """Test lowering priority from Normal to Low."""
        from phabfive.maniphest import Maniphest

        m = Maniphest()
        result = m._navigate_priority(50, "lower")
        assert result == "low"

    def test_lower_from_low_to_wish(self):
        """Test lowering priority from Low to Wish."""
        from phabfive.maniphest import Maniphest

        m = Maniphest()
        result = m._navigate_priority(25, "lower")
        assert result == "wish"

    def test_lower_from_wish_stays_at_wish(self):
        """Test lowering from Wish stays at Wish (edge case)."""
        from phabfive.maniphest import Maniphest

        m = Maniphest()
        result = m._navigate_priority(0, "lower")
        assert result == "wish"

    def test_raise_from_triage_goes_to_unbreak(self):
        """Test raising from Triage skips to Unbreak."""
        from phabfive.maniphest import Maniphest

        m = Maniphest()
        result = m._navigate_priority(90, "raise")
        assert result == "unbreak"

    def test_lower_from_triage_goes_to_high(self):
        """Test lowering from Triage goes to High (Triage sits between High and Unbreak)."""
        from phabfive.maniphest import Maniphest

        m = Maniphest()
        result = m._navigate_priority(90, "lower")
        assert result == "high"


class TestColumnNavigation:
    """Tests for column forward/backward navigation."""

    def test_navigate_forward_to_next_column(self):
        """Test navigating forward to next column."""
        from phabfive.maniphest import Maniphest

        m = Maniphest()

        # Mock column info
        columns = {
            "PHID-PCOL-1": {"name": "Backlog", "sequence": 0},
            "PHID-PCOL-2": {"name": "In Progress", "sequence": 1},
            "PHID-PCOL-3": {"name": "Done", "sequence": 2},
        }

        task_data = {
            "attachments": {
                "columns": {
                    "boards": {
                        "PHID-PROJ-board": {
                            "columns": [{"phid": "PHID-PCOL-1"}]
                        }
                    }
                }
            }
        }

        with mock.patch.object(m, '_get_column_info', return_value=columns):
            result = m._navigate_column("123", task_data, "forward", "PHID-PROJ-board")
            assert result == "PHID-PCOL-2"

    def test_navigate_backward_to_previous_column(self):
        """Test navigating backward to previous column."""
        from phabfive.maniphest import Maniphest

        m = Maniphest()

        columns = {
            "PHID-PCOL-1": {"name": "Backlog", "sequence": 0},
            "PHID-PCOL-2": {"name": "In Progress", "sequence": 1},
            "PHID-PCOL-3": {"name": "Done", "sequence": 2},
        }

        task_data = {
            "attachments": {
                "columns": {
                    "boards": {
                        "PHID-PROJ-board": {
                            "columns": [{"phid": "PHID-PCOL-3"}]
                        }
                    }
                }
            }
        }

        with mock.patch.object(m, '_get_column_info', return_value=columns):
            result = m._navigate_column("123", task_data, "backward", "PHID-PROJ-board")
            assert result == "PHID-PCOL-2"

    def test_navigate_forward_at_end_stays(self):
        """Test navigating forward at end column stays in place."""
        from phabfive.maniphest import Maniphest

        m = Maniphest()

        columns = {
            "PHID-PCOL-1": {"name": "Backlog", "sequence": 0},
            "PHID-PCOL-2": {"name": "Done", "sequence": 1},
        }

        task_data = {
            "attachments": {
                "columns": {
                    "boards": {
                        "PHID-PROJ-board": {
                            "columns": [{"phid": "PHID-PCOL-2"}]
                        }
                    }
                }
            }
        }

        with mock.patch.object(m, '_get_column_info', return_value=columns):
            result = m._navigate_column("123", task_data, "forward", "PHID-PROJ-board")
            assert result == "PHID-PCOL-2"

    def test_navigate_backward_at_start_stays(self):
        """Test navigating backward at start column stays in place."""
        from phabfive.maniphest import Maniphest

        m = Maniphest()

        columns = {
            "PHID-PCOL-1": {"name": "Backlog", "sequence": 0},
            "PHID-PCOL-2": {"name": "Done", "sequence": 1},
        }

        task_data = {
            "attachments": {
                "columns": {
                    "boards": {
                        "PHID-PROJ-board": {
                            "columns": [{"phid": "PHID-PCOL-1"}]
                        }
                    }
                }
            }
        }

        with mock.patch.object(m, '_get_column_info', return_value=columns):
            result = m._navigate_column("123", task_data, "backward", "PHID-PROJ-board")
            assert result == "PHID-PCOL-1"

    def test_navigate_by_exact_column_name(self):
        """Test navigating to column by exact name."""
        from phabfive.maniphest import Maniphest

        m = Maniphest()

        columns = {
            "PHID-PCOL-1": {"name": "Backlog", "sequence": 0},
            "PHID-PCOL-2": {"name": "In Progress", "sequence": 1},
            "PHID-PCOL-3": {"name": "Done", "sequence": 2},
        }

        task_data = {
            "attachments": {
                "columns": {
                    "boards": {
                        "PHID-PROJ-board": {
                            "columns": [{"phid": "PHID-PCOL-1"}]
                        }
                    }
                }
            }
        }

        with mock.patch.object(m, '_get_column_info', return_value=columns):
            result = m._navigate_column("123", task_data, "Done", "PHID-PROJ-board")
            assert result == "PHID-PCOL-3"

    def test_navigate_by_column_name_case_insensitive(self):
        """Test column name matching is case-insensitive."""
        from phabfive.maniphest import Maniphest

        m = Maniphest()

        columns = {
            "PHID-PCOL-1": {"name": "In Progress", "sequence": 0},
        }

        task_data = {
            "attachments": {
                "columns": {
                    "boards": {
                        "PHID-PROJ-board": {
                            "columns": [{"phid": "PHID-PCOL-1"}]
                        }
                    }
                }
            }
        }

        with mock.patch.object(m, '_get_column_info', return_value=columns):
            result = m._navigate_column("123", task_data, "in progress", "PHID-PROJ-board")
            assert result == "PHID-PCOL-1"

    def test_navigate_invalid_column_name_raises_error(self):
        """Test navigating to non-existent column raises ValueError."""
        from phabfive.maniphest import Maniphest

        m = Maniphest()

        columns = {
            "PHID-PCOL-1": {"name": "Backlog", "sequence": 0},
        }

        task_data = {
            "attachments": {
                "columns": {
                    "boards": {
                        "PHID-PROJ-board": {
                            "columns": [{"phid": "PHID-PCOL-1"}]
                        }
                    }
                }
            }
        }

        with mock.patch.object(m, '_get_column_info', return_value=columns):
            with pytest.raises(ValueError, match="Column 'Invalid' not found"):
                m._navigate_column("123", task_data, "Invalid", "PHID-PROJ-board")


class TestBoardColumnValidation:
    """Tests for board/column validation logic."""

    def test_single_board_auto_detect(self):
        """Test auto-detection when task is on single board."""
        from phabfive.edit import Edit

        edit_app = Edit()

        task_data = {
            "attachments": {
                "columns": {
                    "boards": {
                        "PHID-PROJ-board1": {"columns": [{"phid": "PHID-PCOL-1"}]}
                    }
                }
            }
        }

        board_phid, error = edit_app._validate_board_column_context(
            "123", task_data, "Done", None
        )

        assert board_phid == "PHID-PROJ-board1"
        assert error is None

    def test_multiple_boards_without_tag_errors(self):
        """Test error when task on multiple boards without --tag."""
        from phabfive.edit import Edit

        edit_app = Edit()

        task_data = {
            "attachments": {
                "columns": {
                    "boards": {
                        "PHID-PROJ-board1": {"columns": [{"phid": "PHID-PCOL-1"}]},
                        "PHID-PROJ-board2": {"columns": [{"phid": "PHID-PCOL-2"}]},
                    }
                }
            }
        }

        with mock.patch.object(edit_app, '_get_board_names', return_value=["Board1", "Board2"]):
            board_phid, error = edit_app._validate_board_column_context(
                "123", task_data, "Done", None
            )

            assert board_phid is None
            assert "multiple boards" in error
            assert "Board1" in error
            assert "Board2" in error

    def test_no_column_arg_no_validation(self):
        """Test no validation when --column not specified."""
        from phabfive.edit import Edit

        edit_app = Edit()

        task_data = {"attachments": {"columns": {"boards": {}}}}

        board_phid, error = edit_app._validate_board_column_context(
            "123", task_data, None, None
        )

        assert board_phid is None
        assert error is None


class TestStdinAutoDetection:
    """Tests for stdin auto-detection."""

    def test_stdin_piped_detected(self):
        """Test piped stdin is auto-detected."""
        from phabfive.edit import Edit

        edit_app = Edit()

        # Mock stdin as not a TTY (piped)
        with mock.patch('sys.stdin.isatty', return_value=False):
            # Mock stdin content
            yaml_content = "Link: https://example.com/T123\nTask:\n  Name: Test"
            with mock.patch('sys.stdin', mock.MagicMock()):
                with mock.patch.object(edit_app, '_parse_yaml_from_stdin', return_value=[]):
                    # Should try to read from stdin
                    result = edit_app.edit_objects()
                    # Returns error code 1 because no objects found
                    assert result == 1

    def test_no_stdin_no_object_id_errors(self):
        """Test error when no stdin and no object_id."""
        from phabfive.edit import Edit

        edit_app = Edit()

        # Mock stdin as TTY (not piped)
        with mock.patch('sys.stdin.isatty', return_value=True):
            result = edit_app.edit_objects()
            assert result == 1


class TestYAMLParsing:
    """Tests for YAML parsing from stdin."""

    def test_parse_single_task_from_yaml(self):
        """Test parsing single task from YAML."""
        from phabfive.edit import Edit
        from io import StringIO

        edit_app = Edit()

        yaml_data = """Link: https://example.com/T123
Task:
  Name: Test Task
  Status: Open
"""

        with mock.patch('sys.stdin', StringIO(yaml_data)):
            objects = edit_app._parse_yaml_from_stdin()

        assert len(objects) == 1
        assert objects[0]["object_type"] == "task"
        assert objects[0]["object_id"] == "123"

    def test_parse_multiple_tasks_from_yaml(self):
        """Test parsing multiple tasks from YAML stream."""
        from phabfive.edit import Edit
        from io import StringIO

        edit_app = Edit()

        yaml_data = """Link: https://example.com/T123
Task:
  Name: Task 1
---
Link: https://example.com/T456
Task:
  Name: Task 2
"""

        with mock.patch('sys.stdin', StringIO(yaml_data)):
            objects = edit_app._parse_yaml_from_stdin()

        assert len(objects) == 2
        assert objects[0]["object_id"] == "123"
        assert objects[1]["object_id"] == "456"

    def test_parse_yaml_missing_link_raises_error(self):
        """Test parsing YAML without Link field raises error."""
        from phabfive.edit import Edit
        from io import StringIO

        edit_app = Edit()

        yaml_data = """Task:
  Name: Test Task
"""

        with mock.patch('sys.stdin', StringIO(yaml_data)):
            with pytest.raises(ValueError, match="missing 'Link' field"):
                edit_app._parse_yaml_from_stdin()


class TestGroupObjectsByType:
    """Tests for grouping objects by type."""

    def test_group_mixed_objects(self):
        """Test grouping mixed object types."""
        from phabfive.edit import Edit

        edit_app = Edit()

        objects = [
            {"object_type": "task", "object_id": "123", "data": {}},
            {"object_type": "task", "object_id": "456", "data": {}},
            {"object_type": "passphrase", "object_id": "789", "data": {}},
        ]

        grouped = edit_app._group_objects_by_type(objects)

        assert "task" in grouped
        assert "passphrase" in grouped
        assert len(grouped["task"]) == 2
        assert len(grouped["passphrase"]) == 1
