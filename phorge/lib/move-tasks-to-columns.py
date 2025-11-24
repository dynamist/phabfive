#!/usr/bin/env python3
"""
Move tasks to appropriate workboard columns based on priority.

This script distributes tasks across workboard columns to simulate work in progress:
- Backlog (default column): wish priority, low priority
- Up Next: normal priority
- In Progress: high priority tasks
- In Review: unbreak priority
- Done: Some Q1-Q2 tasks to simulate completed work

Usage:
    uv run python phorge/lib/move-tasks-to-columns.py [test-files/mega-2024-simulation.yml]
"""

import sys
import logging
from pathlib import Path
from ruamel.yaml import YAML
from phabricator import Phabricator
import anyconfig
import appdirs

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def load_phabfive_config():
    """Load phabfive configuration from the standard location."""
    config_dir = Path(appdirs.user_config_dir("phabfive"))
    config_file = config_dir / "phabfive.yaml"

    if not config_file.exists():
        log.error(f"Config file not found: {config_file}")
        log.error("Please configure phabfive first")
        sys.exit(1)

    conf = anyconfig.load(str(config_file))
    return conf


def connect_to_phabricator(config):
    """Connect to Phabricator using config."""
    try:
        phab = Phabricator(host=config.get("host"), token=config.get("token"))
        phab.update_interfaces()
        return phab
    except Exception as e:
        log.error(f"Failed to connect to Phabricator: {e}")
        sys.exit(1)


def get_priority_from_name(priority_name):
    """Map priority name to numeric value for comparison."""
    priority_map = {
        "wish": 10,
        "triage": 25,
        "low": 25,
        "normal": 50,
        "high": 80,
        "unbreak": 100,
    }
    return priority_map.get(priority_name.lower(), 50)


def get_column_for_priority(priority_name, title):
    """
    Determine target column based on priority and title.

    Returns column name to move task to, or None to leave in Backlog.
    """
    priority_value = get_priority_from_name(priority_name)

    # Simulate some completed work - Q1 tasks with specific patterns
    if "Q1 2024" in title or "[BUG]" in title and priority_value < 80:
        # Some Q1 tasks and lower priority bugs are "done"
        return "Done"

    # Distribute based on priority
    if priority_value == 100:  # unbreak
        return "In Review"
    elif priority_value == 80:  # high
        return "In Progress"
    elif priority_value == 50:  # normal
        return "Up Next"
    elif priority_value <= 25:  # low, triage, wish
        return None  # Stay in Backlog

    return None  # Default: stay in Backlog


def get_column_phid(phab, project_phid, column_name):
    """Get column PHID by name for a specific project."""
    try:
        result = phab.project.column.search(constraints={"projects": [project_phid]})

        for col in result.get("data", []):
            col_name = col["fields"].get("name", "")

            # Default column has empty name
            if column_name == "Backlog" and col_name == "":
                return col["phid"]
            elif col_name == column_name:
                return col["phid"]

        return None
    except Exception as e:
        log.warning(f"Failed to fetch columns for project {project_phid}: {e}")
        return None


def move_task_to_column(phab, task_phid, project_phid, column_phid, column_name):
    """Move a task to a specific column on a board."""
    try:
        phab.maniphest.edit(
            objectIdentifier=task_phid,
            transactions=[{"type": "column", "value": [project_phid, column_phid]}],
        )
        return True
    except Exception as e:
        log.error(f"Failed to move task to {column_name}: {e}")
        return False


def process_yaml_file(yaml_file):
    """Parse YAML file and extract task information."""
    yaml = YAML()

    with open(yaml_file) as f:
        data = yaml.load(f)

    tasks = []

    def extract_tasks(task_list, parent_title=None):
        """Recursively extract tasks from YAML structure."""
        for task in task_list:
            title = task.get("title", "")
            priority = task.get("priority", "normal")
            projects = task.get("projects", [])

            tasks.append(
                {
                    "title": title,
                    "priority": priority,
                    "projects": projects,
                    "parent": parent_title,
                }
            )

            # Process subtasks
            subtasks = task.get("tasks", [])
            if subtasks:
                extract_tasks(subtasks, title)

    extract_tasks(data.get("tasks", []))
    return tasks


def main():
    """Main function to move tasks to appropriate columns."""
    if len(sys.argv) > 1:
        yaml_file = Path(sys.argv[1])
    else:
        yaml_file = (
            Path(__file__).parent.parent.parent
            / "test-files"
            / "mega-2024-simulation.yml"
        )

    if not yaml_file.exists():
        log.error(f"YAML file not found: {yaml_file}")
        sys.exit(1)

    log.info(f"Loading tasks from {yaml_file}")
    tasks_info = process_yaml_file(yaml_file)
    log.info(f"Found {len(tasks_info)} tasks to process")

    # Connect to Phabricator
    config = load_phabfive_config()
    phab = connect_to_phabricator(config)
    log.info("Connected to Phabricator")

    # Get project name to PHID mapping
    log.info("Fetching project information...")
    projects_result = phab.project.search(constraints={})
    project_map = {}
    for proj in projects_result.get("data", []):
        name = proj["fields"]["name"]
        phid = proj["phid"]
        project_map[name] = phid

    log.info(f"Found {len(project_map)} projects")

    # Process each task
    moved_count = 0
    skipped_count = 0

    for task_info in tasks_info:
        title = task_info["title"]
        priority = task_info["priority"]
        projects = task_info["projects"]

        # Determine target column
        target_column = get_column_for_priority(priority, title)

        if target_column is None:
            # Stay in Backlog
            skipped_count += 1
            continue

        # Search for the task by title
        try:
            search_result = phab.maniphest.search(constraints={"query": title}, limit=1)

            if not search_result.get("data"):
                log.warning(f"Task not found: {title}")
                continue

            task = search_result["data"][0]
            task_phid = task["phid"]
            task_id = task["id"]

            # Move task on each of its project boards
            for project_name in projects:
                project_phid = project_map.get(project_name)

                if not project_phid:
                    log.warning(f"Project not found: {project_name}")
                    continue

                # Get column PHID
                column_phid = get_column_phid(phab, project_phid, target_column)

                if not column_phid:
                    log.warning(
                        f"Column '{target_column}' not found for project {project_name}"
                    )
                    continue

                # Move the task
                if move_task_to_column(
                    phab, task_phid, project_phid, column_phid, target_column
                ):
                    log.info(f"✓ T{task_id} → {target_column} on {project_name}")
                    moved_count += 1

        except Exception as e:
            log.error(f"Error processing task '{title}': {e}")

    log.info("")
    log.info("=" * 60)
    log.info(f"Summary: Moved {moved_count} tasks, {skipped_count} stayed in Backlog")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
