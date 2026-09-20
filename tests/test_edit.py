# -*- coding: utf-8 -*-

"""Tests for phabfive edit command."""

# python std lib
import contextlib
from unittest import mock

# 3rd party imports
import pytest


@pytest.fixture(autouse=True)
def mock_config(monkeypatch):
    """Mock phabfive configuration for all tests."""
    # Token must be exactly 32 alphanumeric characters
    monkeypatch.setenv("PHAB_TOKEN", "cli-testtoken1234567890123456789")
    monkeypatch.setenv("PHAB_URL", "https://phabricator.example.com/api/")

    # Mock the Phabricator client to avoid network calls
    mock_phab = mock.MagicMock()
    monkeypatch.setattr("phabfive.core.Phabricator", lambda **kwargs: mock_phab)


class TestMonogramDetection:
    """Tests for monogram parsing and detection."""

    def test_parse_task_monogram_uppercase(self):
        """Test parsing T123 monogram."""
        from phabfive.edit import Edit

        edit_app = Edit()
        obj_type, obj_id = edit_app.parse_monogram("T123")
        assert obj_type == "task"
        assert obj_id == "123"

    def test_parse_task_monogram_lowercase(self):
        """Test parsing t456 monogram."""
        from phabfive.edit import Edit

        edit_app = Edit()
        obj_type, obj_id = edit_app.parse_monogram("t456")
        assert obj_type == "task"
        assert obj_id == "456"

    def test_parse_task_monogram_from_url(self):
        """Test extracting T789 from URL."""
        from phabfive.edit import Edit

        edit_app = Edit()
        obj_type, obj_id = edit_app.parse_monogram("https://phorge.example.com/T789")
        assert obj_type == "task"
        assert obj_id == "789"

    def test_parse_passphrase_monogram(self):
        """Test parsing K123 monogram."""
        from phabfive.edit import Edit

        edit_app = Edit()
        obj_type, obj_id = edit_app.parse_monogram("K123")
        assert obj_type == "passphrase"
        assert obj_id == "123"

    def test_parse_paste_monogram(self):
        """Test parsing P456 monogram."""
        from phabfive.edit import Edit

        edit_app = Edit()
        obj_type, obj_id = edit_app.parse_monogram("P456")
        assert obj_type == "paste"
        assert obj_id == "456"

    def test_parse_invalid_monogram(self):
        """Test parsing invalid monogram raises ValueError."""
        from phabfive.edit import Edit

        edit_app = Edit()
        with pytest.raises(ValueError, match="No valid monogram found"):
            edit_app.parse_monogram("invalid")


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
                        "PHID-PROJ-board": {"columns": [{"phid": "PHID-PCOL-1"}]}
                    }
                }
            }
        }

        with mock.patch(
            "phabfive.maniphest.fetchers.get_column_info", return_value=columns
        ):
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
                        "PHID-PROJ-board": {"columns": [{"phid": "PHID-PCOL-3"}]}
                    }
                }
            }
        }

        with mock.patch(
            "phabfive.maniphest.fetchers.get_column_info", return_value=columns
        ):
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
                        "PHID-PROJ-board": {"columns": [{"phid": "PHID-PCOL-2"}]}
                    }
                }
            }
        }

        with mock.patch(
            "phabfive.maniphest.fetchers.get_column_info", return_value=columns
        ):
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
                        "PHID-PROJ-board": {"columns": [{"phid": "PHID-PCOL-1"}]}
                    }
                }
            }
        }

        with mock.patch(
            "phabfive.maniphest.fetchers.get_column_info", return_value=columns
        ):
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
                        "PHID-PROJ-board": {"columns": [{"phid": "PHID-PCOL-1"}]}
                    }
                }
            }
        }

        with mock.patch(
            "phabfive.maniphest.fetchers.get_column_info", return_value=columns
        ):
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
                        "PHID-PROJ-board": {"columns": [{"phid": "PHID-PCOL-1"}]}
                    }
                }
            }
        }

        with mock.patch(
            "phabfive.maniphest.fetchers.get_column_info", return_value=columns
        ):
            result = m._navigate_column(
                "123", task_data, "in progress", "PHID-PROJ-board"
            )
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
                        "PHID-PROJ-board": {"columns": [{"phid": "PHID-PCOL-1"}]}
                    }
                }
            }
        }

        with mock.patch(
            "phabfive.maniphest.fetchers.get_column_info", return_value=columns
        ):
            with pytest.raises(ValueError, match="Column 'Invalid' not found"):
                m._navigate_column("123", task_data, "Invalid", "PHID-PROJ-board")


class TestBoardColumnValidation:
    """Tests for board/column validation logic."""

    def test_single_board_auto_detect(self):
        """Test auto-detection when task is on single board."""
        from phabfive.edit.validators import validate_board_column_context

        task_data = {
            "attachments": {
                "columns": {
                    "boards": {
                        "PHID-PROJ-board1": {"columns": [{"phid": "PHID-PCOL-1"}]}
                    }
                }
            }
        }

        board_phid, error = validate_board_column_context(
            "123", task_data, "Done", None, None
        )

        assert board_phid == "PHID-PROJ-board1"
        assert error is None

    def test_multiple_boards_without_tag_errors(self):
        """Test error when task on multiple boards without --tag."""
        from phabfive.edit.validators import validate_board_column_context

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

        # Create mock maniphest with phab attribute
        mock_maniphest = mock.MagicMock()

        with mock.patch(
            "phabfive.edit.validators.get_board_names",
            return_value=["Board1", "Board2"],
        ):
            board_phid, error = validate_board_column_context(
                "123", task_data, "Done", None, mock_maniphest
            )

            assert board_phid is None
            assert "multiple boards" in error
            assert "Board1" in error
            assert "Board2" in error

    def test_no_column_arg_no_validation(self):
        """Test no validation when --column not specified."""
        from phabfive.edit.validators import validate_board_column_context

        task_data = {"attachments": {"columns": {"boards": {}}}}

        board_phid, error = validate_board_column_context(
            "123", task_data, None, None, None
        )

        assert board_phid is None
        assert error is None


class TestBatchReview:
    """Per-task review: the number of targets decides, not the field type."""

    @staticmethod
    def _tasks(n=2):
        return [{"object_id": str(100 + i)} for i in range(n)]

    @staticmethod
    def _maniphest(
        description="current", name="current title", boards=None, changes=None
    ):
        m = mock.MagicMock()
        m._get_task_data.return_value = {
            "fields": {
                "name": name,
                "description": {"raw": description},
            },
            "attachments": {"columns": {"boards": boards or {}}},
        }
        m.build_task_edit.return_value = (
            [{"type": "status", "value": "resolved"}],
            changes
            if changes is not None
            else [{"field": "Status", "old": "Open", "new": "Resolved"}],
        )
        return m

    @staticmethod
    @contextlib.contextmanager
    def _terminal(answers=()):
        """Pretend stdin is a terminal and feed the review loop given answers."""
        with (
            mock.patch("sys.stdin.isatty", return_value=True),
            mock.patch(
                "phabfive.edit.batch.prompt_each", side_effect=list(answers)
            ) as prompt,
        ):
            yield prompt

    def test_single_task_applies_without_prompt(self):
        """One task is not reviewed - that is the whole rule."""
        from phabfive.edit.batch import edit_tasks_batch

        maniphest = self._maniphest()

        with self._terminal() as prompt:
            retcode = edit_tasks_batch(self._tasks(1), maniphest, status="resolved")

        assert retcode == 0
        prompt.assert_not_called()
        assert maniphest.apply_task_edit.call_count == 1

    def test_batch_reviews_each_task(self):
        from phabfive.edit.batch import edit_tasks_batch

        maniphest = self._maniphest()

        with self._terminal(["y", "y"]) as prompt:
            retcode = edit_tasks_batch(self._tasks(2), maniphest, status="resolved")

        assert retcode == 0
        assert prompt.call_count == 2
        assert maniphest.apply_task_edit.call_count == 2

    def test_n_skips_only_that_task(self):
        from phabfive.edit.batch import edit_tasks_batch

        maniphest = self._maniphest()

        with self._terminal(["n", "y"]):
            retcode = edit_tasks_batch(self._tasks(2), maniphest, status="resolved")

        assert retcode == 0
        assert maniphest.apply_task_edit.call_count == 1
        assert maniphest.apply_task_edit.call_args[0][0] == "101"

    def test_a_applies_the_rest_without_prompting(self):
        from phabfive.edit.batch import edit_tasks_batch

        maniphest = self._maniphest()

        with self._terminal(["a"]) as prompt:
            retcode = edit_tasks_batch(self._tasks(3), maniphest, status="resolved")

        assert retcode == 0
        assert prompt.call_count == 1
        assert maniphest.apply_task_edit.call_count == 3

    def test_q_stops_and_is_not_a_failure(self):
        from phabfive.edit.batch import edit_tasks_batch

        maniphest = self._maniphest()

        with self._terminal(["q"]):
            retcode = edit_tasks_batch(self._tasks(3), maniphest, status="resolved")

        # Quitting is a choice, not an error.
        assert retcode == 0
        maniphest.apply_task_edit.assert_not_called()

    def test_yes_skips_the_review(self):
        from phabfive.edit.batch import edit_tasks_batch

        maniphest = self._maniphest()

        with self._terminal() as prompt:
            retcode = edit_tasks_batch(
                self._tasks(3), maniphest, status="resolved", force=True
            )

        assert retcode == 0
        prompt.assert_not_called()
        assert maniphest.apply_task_edit.call_count == 3

    def test_interactive_reviews_a_single_task(self):
        from phabfive.edit.batch import edit_tasks_batch

        maniphest = self._maniphest()

        with self._terminal(["y"]) as prompt:
            retcode = edit_tasks_batch(
                self._tasks(1), maniphest, status="resolved", interactive=True
            )

        assert retcode == 0
        assert prompt.call_count == 1

    def test_no_terminal_applies_structured_fields(self):
        """Agents editing status/column keep working, unprompted."""
        from phabfive.edit.batch import edit_tasks_batch

        maniphest = self._maniphest()

        with (
            mock.patch("sys.stdin.isatty", return_value=False),
            mock.patch("phabfive.edit.batch.prompt_each") as prompt,
        ):
            retcode = edit_tasks_batch(self._tasks(2), maniphest, status="resolved")

        assert retcode == 0
        prompt.assert_not_called()
        assert maniphest.apply_task_edit.call_count == 2

    def test_no_terminal_refuses_text_changes(self, capsys):
        """No terminal to show N diffs on, so make the caller say --yes."""
        from phabfive.edit.batch import edit_tasks_batch

        maniphest = self._maniphest()

        with mock.patch("sys.stdin.isatty", return_value=False):
            retcode = edit_tasks_batch(self._tasks(2), maniphest, description="new")

        assert retcode == 1
        maniphest.apply_task_edit.assert_not_called()
        err = capsys.readouterr().err
        assert "--yes required for non-interactive mode" in err
        assert "No tasks were modified." in err

    def test_no_terminal_text_changes_with_yes(self):
        from phabfive.edit.batch import edit_tasks_batch

        maniphest = self._maniphest()

        with mock.patch("sys.stdin.isatty", return_value=False):
            retcode = edit_tasks_batch(
                self._tasks(2), maniphest, description="new", force=True
            )

        assert retcode == 0
        assert maniphest.apply_task_edit.call_count == 2

    def test_dry_run_never_prompts_and_never_writes(self):
        """--dry-run decides whether a write happens; the review only answers a prompt."""
        from phabfive.edit.batch import edit_tasks_batch

        maniphest = self._maniphest()

        with self._terminal() as prompt:
            retcode = edit_tasks_batch(
                self._tasks(2), maniphest, description="new", dry_run=True
            )

        assert retcode == 0
        prompt.assert_not_called()
        maniphest.apply_task_edit.assert_not_called()

    def test_piped_input_is_not_reviewed_without_interactive(self):
        """Prompting on a pipe would hang an agent that cannot answer."""
        from phabfive.edit.batch import edit_tasks_batch

        maniphest = self._maniphest()

        with (
            mock.patch("sys.stdin.isatty", return_value=False),
            mock.patch("phabfive.edit.batch.open_tty") as open_tty,
            mock.patch("phabfive.edit.batch.prompt_each") as prompt,
        ):
            retcode = edit_tasks_batch(self._tasks(2), maniphest, status="resolved")

        assert retcode == 0
        open_tty.assert_not_called()
        prompt.assert_not_called()

    def test_interactive_reaches_past_a_pipe_to_the_terminal(self):
        from phabfive.edit.batch import edit_tasks_batch

        maniphest = self._maniphest()
        tty = mock.MagicMock()

        with (
            mock.patch("sys.stdin.isatty", return_value=False),
            mock.patch("phabfive.edit.batch.open_tty", return_value=tty),
            mock.patch("phabfive.edit.batch.prompt_each", return_value="y") as prompt,
        ):
            retcode = edit_tasks_batch(
                self._tasks(2), maniphest, status="resolved", interactive=True
            )

        assert retcode == 0
        assert prompt.call_count == 2
        # The prompt must read the terminal, not the pipe.
        assert prompt.call_args[0][1] is tty
        tty.close.assert_called_once()

    def test_interactive_without_any_terminal_refuses(self, capsys):
        """Applying unreviewed is the opposite of what --interactive asked for."""
        from phabfive.edit.batch import edit_tasks_batch

        maniphest = self._maniphest()

        with (
            mock.patch("sys.stdin.isatty", return_value=False),
            mock.patch("phabfive.edit.batch.open_tty", return_value=None),
            mock.patch("phabfive.edit.batch.prompt_each") as prompt,
        ):
            retcode = edit_tasks_batch(
                self._tasks(2), maniphest, status="resolved", interactive=True
            )

        assert retcode == 1
        prompt.assert_not_called()
        maniphest.apply_task_edit.assert_not_called()
        err = capsys.readouterr().err
        assert "--interactive needs a terminal" in err
        assert "No tasks were modified." in err

    def test_interactive_with_dry_run_still_previews(self):
        """--dry-run shows every change anyway, so it needs no terminal."""
        from phabfive.edit.batch import edit_tasks_batch

        maniphest = self._maniphest()

        with (
            mock.patch("sys.stdin.isatty", return_value=False),
            mock.patch("phabfive.edit.batch.open_tty", return_value=None),
            mock.patch("phabfive.edit.batch.prompt_each") as prompt,
        ):
            retcode = edit_tasks_batch(
                self._tasks(2),
                maniphest,
                status="resolved",
                interactive=True,
                dry_run=True,
            )

        assert retcode == 0
        prompt.assert_not_called()
        maniphest.apply_task_edit.assert_not_called()

    def test_task_with_no_changes_is_not_reviewed(self, capsys):
        from phabfive.edit.batch import edit_tasks_batch

        maniphest = self._maniphest()
        maniphest.build_task_edit.return_value = ([], [])

        with self._terminal() as prompt:
            retcode = edit_tasks_batch(self._tasks(2), maniphest, status="resolved")

        assert retcode == 0
        prompt.assert_not_called()
        maniphest.apply_task_edit.assert_not_called()
        assert "No changes (already at target state)" in capsys.readouterr().out

    def test_build_reuses_the_task_data_already_fetched(self):
        """Phase 1 fetched each task; the build must not fetch it again."""
        from phabfive.edit.batch import edit_tasks_batch

        maniphest = self._maniphest()

        with self._terminal() as prompt:
            edit_tasks_batch(self._tasks(2), maniphest, status="resolved", force=True)

        prompt.assert_not_called()
        assert maniphest._get_task_data.call_count == 2
        for call in maniphest.build_task_edit.call_args_list:
            assert call[0][1] is maniphest._get_task_data.return_value


@mock.patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None)
class TestBuildTaskEdit:
    """Splitting "what would change" from "do it" is what makes review possible."""

    @staticmethod
    def _task():
        return {
            "id": 123,
            "phid": "PHID-TASK-123",
            "fields": {
                "name": "A task",
                "priority": {"value": 50, "name": "Normal"},
                "status": {"value": "open", "name": "Open"},
                "ownerPHID": None,
                "description": {"raw": "old text"},
                "spacePHID": "PHID-SPCE-1",
            },
            "attachments": {
                "columns": {"boards": {}},
                "projects": {"projectPHIDs": []},
                "subscribers": {"subscriberPHIDs": []},
            },
        }

    def _maniphest(self):
        from phabfive.maniphest.core import Maniphest

        maniphest = Maniphest()
        maniphest.phab = mock.MagicMock()
        maniphest.url = "https://phorge.example.com/"
        maniphest.conf = {"PHAB_SPACE": "S1"}
        maniphest._get_task_data = mock.MagicMock(return_value=self._task())
        return maniphest

    def test_building_never_touches_the_api(self, mock_init):
        maniphest = self._maniphest()

        transactions, changes = maniphest.build_task_edit("123", title="New title")

        assert transactions == [{"type": "title", "value": "New title"}]
        assert changes == [{"field": "Title", "old": "A task", "new": "New title"}]
        maniphest.phab.maniphest.edit.assert_not_called()

    def test_supplied_task_data_is_not_refetched(self, mock_init):
        """Batch fetches in phase 1; the build must not pay for it again."""
        maniphest = self._maniphest()

        maniphest.build_task_edit("123", self._task(), title="New title")

        maniphest._get_task_data.assert_not_called()

    def test_task_data_is_fetched_when_omitted(self, mock_init):
        maniphest = self._maniphest()

        maniphest.build_task_edit("123", title="New title")

        maniphest._get_task_data.assert_called_once_with("123")

    def test_no_change_builds_no_transactions(self, mock_init):
        maniphest = self._maniphest()

        transactions, changes = maniphest.build_task_edit("123", title="A task")

        assert transactions == []
        assert changes == []

    def test_apply_sends_exactly_what_was_built(self, mock_init):
        maniphest = self._maniphest()
        transactions, _ = maniphest.build_task_edit("123", title="New title")

        maniphest.apply_task_edit("123", transactions)

        maniphest.phab.maniphest.edit.assert_called_once_with(
            objectIdentifier="T123", transactions=transactions
        )

    def test_edit_task_by_id_still_composes_the_two(self, mock_init):
        """The old entry point keeps its behavior, so its callers are untouched."""
        maniphest = self._maniphest()

        result = maniphest.edit_task_by_id(task_id="123", title="New title")

        assert result == {
            "task_id": "123",
            "changes": [{"field": "Title", "old": "A task", "new": "New title"}],
        }
        maniphest.phab.maniphest.edit.assert_called_once()

    def test_dry_run_builds_but_does_not_apply(self, mock_init):
        maniphest = self._maniphest()

        result = maniphest.edit_task_by_id(
            task_id="123", title="New title", dry_run=True
        )

        assert result["dry_run"] is True
        assert result["changes"]
        maniphest.phab.maniphest.edit.assert_not_called()

    def test_edit_task_by_id_accepts_prefetched_data(self, mock_init):
        maniphest = self._maniphest()

        maniphest.edit_task_by_id(
            task_id="123", title="New title", task_data=self._task()
        )

        maniphest._get_task_data.assert_not_called()
        maniphest.phab.maniphest.edit.assert_called_once()


@mock.patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None)
class TestBuildTaskEditColumn:
    """A column that is already the task's column is not a move (#404)."""

    # Two boards that both have a "Backlog", which is why the comparison is
    # by PHID and not by name.
    COLUMNS = {
        "PHID-PROJ-qa": {
            "PHID-PCOL-qa-backlog": {"name": "Backlog", "sequence": 0},
            "PHID-PCOL-qa-doing": {"name": "Doing", "sequence": 1},
        },
        "PHID-PROJ-ops": {
            "PHID-PCOL-ops-backlog": {"name": "Backlog", "sequence": 0},
            "PHID-PCOL-ops-done": {"name": "Done", "sequence": 1},
        },
    }

    @staticmethod
    def _task(board_columns=None, projects=None):
        """A task sitting in ``board_columns`` ({board PHID: column PHID})."""
        boards = {
            board: {"columns": [{"phid": column}]}
            for board, column in (board_columns or {}).items()
        }
        return {
            "id": 93,
            "phid": "PHID-TASK-93",
            "fields": {
                "name": "A task",
                "priority": {"value": 50, "name": "Normal"},
                "status": {"value": "open", "name": "Open"},
                "ownerPHID": None,
                "description": {"raw": "old text"},
                "spacePHID": "PHID-SPCE-1",
            },
            "attachments": {
                "columns": {"boards": boards},
                "projects": {
                    "projectPHIDs": list(
                        projects if projects is not None else (board_columns or {})
                    )
                },
                "subscribers": {"subscriberPHIDs": []},
            },
        }

    @contextlib.contextmanager
    def _maniphest(self):
        from phabfive.maniphest.core import Maniphest

        maniphest = Maniphest()
        maniphest.phab = mock.MagicMock()
        maniphest.url = "https://phorge.example.com/"
        maniphest.conf = {"PHAB_SPACE": "S1"}
        with mock.patch(
            "phabfive.maniphest.fetchers.get_column_info",
            side_effect=lambda phab, board_phid: self.COLUMNS[board_phid],
        ):
            yield maniphest

    def test_the_column_the_task_is_in_is_not_a_move(self, mock_init):
        with self._maniphest() as maniphest:
            transactions, changes = maniphest.build_task_edit(
                "93",
                self._task({"PHID-PROJ-qa": "PHID-PCOL-qa-backlog"}),
                board_phid="PHID-PROJ-qa",
                column="Backlog",
            )

        assert transactions == []
        assert changes == []

    def test_a_column_only_no_op_reports_no_changes(self, mock_init, capsys):
        """The observable half of #404: the short-circuit now fires."""
        from phabfive.edit.formatters import display_changes

        with self._maniphest() as maniphest:
            result = maniphest.edit_task_by_id(
                task_id="93",
                board_phid="PHID-PROJ-qa",
                column="Backlog",
                task_data=self._task({"PHID-PROJ-qa": "PHID-PCOL-qa-backlog"}),
            )
            display_changes("T93", result)

            maniphest.phab.maniphest.edit.assert_not_called()

        assert result["changes"] == []
        assert "T93: No changes (already at target state)" in capsys.readouterr().out

    def test_another_column_on_that_board_is_a_move(self, mock_init):
        with self._maniphest() as maniphest:
            transactions, changes = maniphest.build_task_edit(
                "93",
                self._task({"PHID-PROJ-qa": "PHID-PCOL-qa-backlog"}),
                board_phid="PHID-PROJ-qa",
                column="Doing",
            )

        assert transactions == [
            {"type": "column", "value": ["PHID-PCOL-qa-doing"]},
        ]
        assert changes == [{"field": "Column", "old": "Backlog", "new": "Doing"}]

    def test_a_board_the_task_is_not_on_still_moves_and_adds(self, mock_init):
        """No current column is not the target column, and the board is joined."""
        with self._maniphest() as maniphest:
            transactions, changes = maniphest.build_task_edit(
                "93",
                self._task({}),
                board_phid="PHID-PROJ-qa",
                column="Backlog",
            )

        assert transactions == [
            {"type": "projects.add", "value": ["PHID-PROJ-qa"]},
            {"type": "column", "value": ["PHID-PCOL-qa-backlog"]},
        ]
        assert changes == [{"field": "Column", "old": "(none)", "new": "Backlog"}]

    def test_a_same_named_column_on_another_board_is_not_the_task_s_column(
        self, mock_init
    ):
        """Comparing names would call this a no-op; comparing PHIDs does not."""
        task = self._task(
            {
                "PHID-PROJ-qa": "PHID-PCOL-qa-backlog",
                "PHID-PROJ-ops": "PHID-PCOL-ops-done",
            }
        )

        with self._maniphest() as maniphest:
            transactions, changes = maniphest.build_task_edit(
                "93", task, board_phid="PHID-PROJ-ops", column="Backlog"
            )

        assert transactions == [
            {"type": "column", "value": ["PHID-PCOL-ops-backlog"]},
        ]
        assert changes == [{"field": "Column", "old": "Done", "new": "Backlog"}]

    def test_forward_from_the_last_column_is_not_a_move(self, mock_init):
        """_navigate_column stays put at the end of the board."""
        with self._maniphest() as maniphest:
            transactions, changes = maniphest.build_task_edit(
                "93",
                self._task({"PHID-PROJ-ops": "PHID-PCOL-ops-done"}),
                board_phid="PHID-PROJ-ops",
                column="forward",
            )

        assert transactions == []
        assert changes == []


class TestStdinAutoDetection:
    """Tests for stdin auto-detection."""

    def test_stdin_piped_detected(self):
        """Test piped stdin is auto-detected."""
        from phabfive.edit import Edit

        edit_app = Edit()

        # Mock stdin as not a TTY (piped)
        with mock.patch("sys.stdin.isatty", return_value=False):
            with mock.patch("sys.stdin", mock.MagicMock()):
                with mock.patch(
                    "phabfive.edit.core.parse_yaml_from_stdin", return_value=[]
                ):
                    # Should try to read from stdin
                    result = edit_app.edit_objects()
                    # Returns error code 1 because no objects found
                    assert result == 1

    def test_no_stdin_no_object_id_errors(self):
        """Test error when no stdin and no object_id."""
        from phabfive.edit import Edit

        edit_app = Edit()

        # Mock stdin as TTY (not piped)
        with mock.patch("sys.stdin.isatty", return_value=True):
            result = edit_app.edit_objects()
            assert result == 1


class TestYAMLParsing:
    """Tests for YAML parsing from stdin."""

    def test_parse_single_task_from_yaml(self):
        """Test parsing single task from YAML."""
        from io import StringIO

        from phabfive.edit import Edit
        from phabfive.yaml_utils import parse_yaml_from_stdin

        edit_app = Edit()

        yaml_data = """Link: https://example.com/T123
Task:
  Name: Test Task
  Status: Open
"""

        with mock.patch("sys.stdin", StringIO(yaml_data)):
            objects = parse_yaml_from_stdin(edit_app.parse_monogram)

        assert len(objects) == 1
        assert objects[0]["object_type"] == "task"
        assert objects[0]["object_id"] == "123"

    def test_parse_multiple_tasks_from_yaml(self):
        """Test parsing multiple tasks from YAML stream."""
        from io import StringIO

        from phabfive.edit import Edit
        from phabfive.yaml_utils import parse_yaml_from_stdin

        edit_app = Edit()

        yaml_data = """Link: https://example.com/T123
Task:
  Name: Task 1
---
Link: https://example.com/T456
Task:
  Name: Task 2
"""

        with mock.patch("sys.stdin", StringIO(yaml_data)):
            objects = parse_yaml_from_stdin(edit_app.parse_monogram)

        assert len(objects) == 2
        assert objects[0]["object_id"] == "123"
        assert objects[1]["object_id"] == "456"

    def test_parse_yaml_missing_link_raises_error(self):
        """Test parsing YAML without Link field raises error."""
        from io import StringIO

        from phabfive.edit import Edit
        from phabfive.yaml_utils import parse_yaml_from_stdin

        edit_app = Edit()

        yaml_data = """Task:
  Name: Test Task
"""

        with mock.patch("sys.stdin", StringIO(yaml_data)):
            with pytest.raises(ValueError, match="missing 'Link' field"):
                parse_yaml_from_stdin(edit_app.parse_monogram)

    def test_parse_list_document_from_yaml(self):
        """Test parsing one document holding a list of tasks.

        This is the shape "--format=yaml" emits, so it is what
        "phabfive maniphest search | phabfive edit" actually pipes in. Before
        this was accepted the documented pipeline failed with
        "YAML document missing 'Link' field", because the Link lived one level
        down inside the list.
        """
        from io import StringIO

        from phabfive.edit import Edit
        from phabfive.yaml_utils import parse_yaml_from_stdin

        edit_app = Edit()

        yaml_data = """- Link: https://example.com/T123
  Task:
    Name: Task 1
- Link: https://example.com/T456
  Task:
    Name: Task 2
"""

        with mock.patch("sys.stdin", StringIO(yaml_data)):
            objects = parse_yaml_from_stdin(edit_app.parse_monogram)

        assert len(objects) == 2
        assert objects[0]["object_id"] == "123"
        assert objects[1]["object_id"] == "456"
        assert objects[0]["data"]["Task"]["Name"] == "Task 1"

    def test_parse_list_document_missing_link_raises_error(self):
        """A list entry without a Link is reported like a bare document is."""
        from io import StringIO

        from phabfive.edit import Edit
        from phabfive.yaml_utils import parse_yaml_from_stdin

        edit_app = Edit()

        yaml_data = """- Task:
    Name: Test Task
"""

        with mock.patch("sys.stdin", StringIO(yaml_data)):
            with pytest.raises(ValueError, match="missing 'Link' field"):
                parse_yaml_from_stdin(edit_app.parse_monogram)

    def test_parses_what_display_tasks_yaml_emits(self):
        """Round-trip the real emitter into the real parser.

        The two used to disagree: display_tasks_yaml emits one document
        holding a sequence, parse_yaml_from_stdin looked for a mapping per
        document, and every documented "search | edit" pipeline failed with
        "YAML document missing 'Link' field". The old tests fed the parser a
        hand-written literal, so nothing noticed. Feed it the emitter instead.
        """
        from io import StringIO

        from phabfive.display import display_tasks_yaml
        from phabfive.edit import Edit
        from phabfive.yaml_utils import parse_yaml_from_stdin

        edit_app = Edit()

        task_dicts = [
            {
                "_url": "https://example.com/T123",
                "Task": {"Name": "Task 1", "Status": "Open"},
            },
            {
                "_url": "https://example.com/T456",
                "Task": {"Name": "Task 2", "Status": "Open"},
            },
        ]

        emitted = StringIO()
        with contextlib.redirect_stdout(emitted):
            display_tasks_yaml(task_dicts)

        with mock.patch("sys.stdin", StringIO(emitted.getvalue())):
            objects = parse_yaml_from_stdin(edit_app.parse_monogram)

        assert [obj["object_id"] for obj in objects] == ["123", "456"]


class TestGroupObjectsByType:
    """Tests for grouping objects by type."""

    def test_group_mixed_objects(self):
        """Test grouping mixed object types."""
        from phabfive.yaml_utils import group_objects_by_type

        objects = [
            {"object_type": "task", "object_id": "123", "data": {}},
            {"object_type": "task", "object_id": "456", "data": {}},
            {"object_type": "passphrase", "object_id": "789", "data": {}},
        ]

        grouped = group_objects_by_type(objects)

        assert "task" in grouped
        assert "passphrase" in grouped
        assert len(grouped["task"]) == 2
        assert len(grouped["passphrase"]) == 1


class TestCommaSeparatedParsing:
    """Tests for comma-separated object ID parsing."""

    def test_parse_single_task_id(self):
        """Test parsing single task ID."""
        from phabfive.edit import Edit

        edit_app = Edit()
        result = edit_app.parse_object_ids("T123")

        assert len(result) == 1
        assert result[0] == ("task", "123")

    def test_parse_comma_separated_tasks(self):
        """Test parsing comma-separated task IDs."""
        from phabfive.edit import Edit

        edit_app = Edit()
        result = edit_app.parse_object_ids("T123,T456,T789")

        assert len(result) == 3
        assert result[0] == ("task", "123")
        assert result[1] == ("task", "456")
        assert result[2] == ("task", "789")

    def test_parse_comma_separated_with_spaces(self):
        """Test parsing comma-separated IDs with whitespace."""
        from phabfive.edit import Edit

        edit_app = Edit()
        result = edit_app.parse_object_ids("T123, T456 , T789")

        assert len(result) == 3
        assert result[0] == ("task", "123")
        assert result[1] == ("task", "456")
        assert result[2] == ("task", "789")

    def test_parse_mixed_types_raises_error(self):
        """Test that mixing object types raises ValueError."""
        from phabfive.edit import Edit

        edit_app = Edit()

        with pytest.raises(ValueError, match="Cannot mix object types"):
            edit_app.parse_object_ids("T123,K456")

    def test_parse_empty_string_raises_error(self):
        """Test that empty string raises ValueError."""
        from phabfive.edit import Edit

        edit_app = Edit()

        with pytest.raises(ValueError, match="No valid object IDs"):
            edit_app.parse_object_ids("")

    def test_parse_only_commas_raises_error(self):
        """Test that string with only commas raises ValueError."""
        from phabfive.edit import Edit

        edit_app = Edit()

        with pytest.raises(ValueError, match="No valid object IDs"):
            edit_app.parse_object_ids(",,,")


class TestPartitionSuggestions:
    """Tests for partition suggestion generation."""

    def test_generates_comma_separated_format(self):
        """Test that partition suggestions use comma-separated format."""
        from phabfive.edit.formatters import generate_partition_suggestions

        errors_by_boards = {
            frozenset(["Board1", "Board2"]): ["123", "456"],
        }

        result = generate_partition_suggestions(errors_by_boards)

        assert "phabfive edit T123,T456" in result
        assert '--tag="Board1"' in result
        assert "echo" not in result  # Should not use echo pipe format


class TestEditExpansion:
    """Tests for 'edit T123' → 'maniphest edit T123' expansion."""

    def test_edit_task_expansion(self):
        """Test 'edit T123' expands to 'maniphest edit T123'."""
        from phabfive.cli import preprocess_monograms

        result = preprocess_monograms(["phabfive", "edit", "T123"])
        assert result == ["phabfive", "maniphest", "edit", "T123"]

    def test_edit_task_expansion_with_options(self):
        """Test 'edit T123 --priority=high' expands correctly."""
        from phabfive.cli import preprocess_monograms

        result = preprocess_monograms(
            ["phabfive", "edit", "T123", "--priority=high", "--status=resolved"]
        )
        assert result == [
            "phabfive",
            "maniphest",
            "edit",
            "T123",
            "--priority=high",
            "--status=resolved",
        ]

    def test_edit_task_expansion_with_global_options(self):
        """Test '--format=yaml edit T123' expands correctly."""
        from phabfive.cli import preprocess_monograms

        result = preprocess_monograms(
            ["phabfive", "--format=yaml", "edit", "T123", "--priority=high"]
        )
        assert result == [
            "phabfive",
            "--format=yaml",
            "maniphest",
            "edit",
            "T123",
            "--priority=high",
        ]

    def test_edit_comma_separated_expansion(self):
        """Test 'edit T123,T456' expands to 'maniphest edit T123,T456'."""
        from phabfive.cli import preprocess_monograms

        result = preprocess_monograms(["phabfive", "edit", "T123,T456"])
        # Comma-separated monograms are not detected as individual monograms
        # The first is T123,T456 which doesn't match the monogram pattern
        # So no expansion happens for this case - it goes through the top-level edit
        assert result == ["phabfive", "edit", "T123,T456"]

    def test_edit_without_monogram_no_expansion(self):
        """Test 'edit --column=Done' (no monogram) is not expanded."""
        from phabfive.cli import preprocess_monograms

        result = preprocess_monograms(["phabfive", "edit", "--column=Done"])
        assert result == ["phabfive", "edit", "--column=Done"]

    def test_edit_alone_no_expansion(self):
        """Test 'edit' alone is not expanded."""
        from phabfive.cli import preprocess_monograms

        result = preprocess_monograms(["phabfive", "edit"])
        assert result == ["phabfive", "edit"]

    def test_maniphest_edit_not_double_expanded(self):
        """Test 'maniphest edit T123' is not expanded again."""
        from phabfive.cli import preprocess_monograms

        result = preprocess_monograms(["phabfive", "maniphest", "edit", "T123"])
        # "maniphest" is the first positional arg, not "edit"
        assert result == ["phabfive", "maniphest", "edit", "T123"]

    def test_existing_show_shortcut_still_works(self):
        """Test 'T123' still expands to 'maniphest show T123'."""
        from phabfive.cli import preprocess_monograms

        result = preprocess_monograms(["phabfive", "T123"])
        assert result == ["phabfive", "maniphest", "show", "T123"]

    def test_existing_comment_shortcut_still_works(self):
        """Test 'T123 "comment"' still expands to 'maniphest comment T123 ...'."""
        from phabfive.cli import preprocess_monograms

        result = preprocess_monograms(["phabfive", "T123", "Add a comment"])
        assert result == ["phabfive", "maniphest", "comment", "T123", "Add a comment"]

    def test_edit_passphrase_expansion(self):
        """Test 'edit K123' expands to 'passphrase edit K123'."""
        from phabfive.cli import preprocess_monograms

        result = preprocess_monograms(["phabfive", "edit", "K123"])
        assert result == ["phabfive", "passphrase", "edit", "K123"]

    def test_edit_paste_expansion(self):
        """Test 'edit P123' expands to 'paste edit P123'."""
        from phabfive.cli import preprocess_monograms

        result = preprocess_monograms(["phabfive", "edit", "P123"])
        assert result == ["phabfive", "paste", "edit", "P123"]
