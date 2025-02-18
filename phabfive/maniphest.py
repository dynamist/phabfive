# -*- coding: utf-8 -*-

# python std lib
import json
import logging
from pathlib import Path
import time
import datetime
from shlex import quote

# phabfive imports
from phabfive.core import Phabfive
from phabfive.constants import *
from phabfive.exceptions import *

# 3rd party imports
import yaml
from jinja2 import Template

log = logging.getLogger(__name__)

class Maniphest(Phabfive):
    def __init__(self):
        super(Maniphest, self).__init__()

    def task_search(self, project, created_after=None, updated_after=None):
        """
        Search for Phabricator Maniphest tasks with given parameters.

        Parameters
        ----------
        project       (str, required): Project name.
        created_after (int, optional): Number of days ago the task was created.
        updated_after (int, optional): Number of days ago the task was updated.
        """

        if created_after:
            created_after = days_to_unix(created_after)
        if updated_after:
            updated_after = days_to_unix(updated_after)

        constraints = {}
        if project:
            constraints["projects"] = [str(project)]
        if created_after:
            constraints["createdStart"] = int(created_after)
        if updated_after:
            constraints["modifiedStart"] = int(updated_after)

        attachments = {
            "columns": True
        }

        log.debug(f"JSON constraints: \n{json.dumps(constraints, indent=2)}\n")
        log.debug(f"JSON attachments: \n{json.dumps(attachments, indent=2)}\n")

        result = self.phab.maniphest.search(constraints=constraints, attachments=attachments)

        log.debug(f"JSON result.response: \n{json.dumps(result.response, indent=2)}\n")

        for item in result.response["data"]:
            print(f"Link: {self.url}/T{item['id']}")
            fields = item.get("fields", {})
            date_closed = ""

            for key, value in fields.items():
                if key in ["dateCreated", "dateModified"]:
                    if value:
                        formatted_time = format_timestamp(value)
                        print(f"{key[4:]}: {formatted_time}")
                elif key == "dateClosed":
                    if value:
                        date_closed = format_timestamp(value)
                        print(f"Closed: {date_closed}")
                elif key == "name":
                    print(f"Name: '{value}'" if "[" in value else f"Name: {value}")

            status_name = fields.get("status", {}).get("name", "Unknown")
            print(f"Status: {status_name} {date_closed}")

            priority_name = fields.get("priority", {}).get("name", "Unknown")
            print(f"Priority: {priority_name}")

            boards = item.get("attachments", {}).get("columns", {}).get("boards", {})

            for board_data in boards.values():
                columns = board_data.get("columns", [])
                for column in columns:
                    column_name = column.get("name")
                    columns_no = len(columns)
                    if column_name:
                        print(f"Column: {column_name} {columns_no}")

            description_raw = fields.get("description", {}).get("raw", "")
            if description_raw:
                print(f"Description: |")
                print("  >", "  > ".join(description_raw.splitlines(True)), end="")
            else:
                print(f"Description: ''", end="")
            print("\n")

    def add_comment(self, ticket_identifier, comment_string):
        """
        :type ticket_identifier: str
        :type comment_string: str
        """
        result = self.phab.maniphest.edit(
            transactions=self.to_transactions({"comment": comment_string}),
            objectIdentifier=ticket_identifier,
        )

        return (True, result["object"])

    def info(self, task_id):
        """
        :type task_id: int
        """
        # FIXME: Add validation and extraction of the int part of the task_id
        result = self.phab.maniphest.info(task_id=task_id)

        return (True, result)

    def create_from_config(self, config_file, dry_run=False):
        if not config_file:
            raise PhabfiveException(f"Must specify a config file path")

        if Path(config_file).is_file():
            log.error(f"Config file '{config_file}' do not exists")

        with open(config_file) as stream:
            root_data = yaml.load(stream, Loader=yaml.Loader) # nosec-B506

        # Fetch all users in phabricator, used by subscribers mapping later
        # Extended support for phabricator instances with more than 100 (i.e. deftault query limit) users
        # BUT with limieted exception handling e.g. retries needed when phabricator instance is not responding to API calls
        users_query = raw_data = self.phab.user.search()
        
        while (len(raw_data.data) >= 100 and not raw_data.cursor["after"] is None):
            raw_data = self.phab.user.search(after=raw_data.cursor["after"])
            users_query.data.extend(raw_data.data)
        log.debug(users_query)
        
        username_to_id_mapping = {
            user["fields"]["username"]: user["phid"]
            for user in users_query["data"]
        }

        log.debug(username_to_id_mapping)

        # Fetch all projects in phabricator, used to map ticket -> projects later
        # Extended support for phabricator instances with more than 100 (i.e. deftault query limit) projects
        # BUT with limieted exception handling, e.g. retries needed when phabricator instance is not responding to API calls
        projects_query = raw_data = self.phab.project.search(constraints={"name": ""})
        
        while (len(raw_data.data) >= 100 and not raw_data.cursor["after"] is None):
            raw_data = self.phab.project.search(constraints={"name": ""},after=raw_data.cursor["after"])
            projects_query.data.extend(raw_data.data)
        log.debug(projects_query)

        project_name_to_id_map = {
            project["fields"]["name"]: project["phid"]
            for project in projects_query["data"]
        }

        log.debug(project_name_to_id_map)

        # Gather and remove variables to avoid using it or polluting the data later on
        variables = root_data["variables"]
        del root_data["variables"]

        # Helper lambda to slim down transaction handling
        add_transaction = lambda t, transaction_type, value : t.append(
            {"type": transaction_type, "value": value},
        )

        def r(data_block, variable_name, variables):
            """
            Helper method to simplify Jinja2 rendering of a given value to a set of variables
            """
            data = data_block.get(variable_name, None)

            if data:
                data_block[variable_name] = Template(data).render(variables)

        def pre_process_tasks(task_config):
            """
            This is the main parser that can be run recurse in order to sort out an individual ticket and recurse down
            to pre process each task and to query all internal ID:s and update the datastructure
            """
            log.debug("Pre processing tasks")
            log.debug(task_config)

            output = task_config.copy()

            # Render strings that should be possible to render with Jinja2
            r(output, "title", variables)
            r(output, "description", variables)

            # Validate and translate project names to internal project PHID:s
            project_phids = []

            for project_name in output.get("projects", []):
                project_phid = project_name_to_id_map.get(project_name, None)

                if not project_phid:
                    raise PhabfiveRemoteException(f"Project '{project_name}' is not found on the phabricator server")

                project_phids.append(project_phid)

            output["projects"] = project_phids

            # Validate and translate all subscriber users to PHID:s
            user_phids = []

            for subscriber_name in output.get("subscribers", []):
                log.debug(f"processing user {subscriber_name}")
                user_phid = username_to_id_mapping.get(subscriber_name, None)

                if not user_phid:
                    raise PhabfiveRemoteException(f"Subscriber '{subscriber_name}' not found as a user on the phabricator server")

                user_phids.append(user_phid)

            output["subscribers"] = user_phids

            # Recurse down and process all child tasks
            processed_child_tasks = []
            child_tasks = task_config.get("tasks", None)

            if child_tasks:
                processed_child_tasks = [
                    pre_process_tasks(task)
                    for task in child_tasks
                ]

            output["tasks"] = processed_child_tasks

            return output

        def recurse_build_transactions(task_config):
            """
            This block recurses over all tasks and builds the transaction set for this ticket and stores it
            in the data structure.
            """
            log.debug(f"Building transactions for task_config")
            log.debug(task_config)

            # In order to not cause issues with injecting data in a recurse traversal, copy the input,
            # modify the data and return data that is later used to build a new full data structure
            output = task_config.copy()

            # # Helper lambda to slim down transaction handling
            # add_transaction = lambda t, transaction_type, value : t.append(
            #     {"type": transaction_type, "value": value},
            # )

            transactions = []

            if "title" in task_config and "description" in task_config:
                add_transaction(transactions, "title", task_config["title"])
                add_transaction(transactions, "description", task_config["description"])
                add_transaction(transactions, "priority", task_config.get("priority", TICKET_PRIORITY_NORMAL))

                projects = task_config.get("projects", [])

                if projects:
                    add_transaction(transactions, "projects.set", projects)

                subscribers = task_config.get("subscribers", [])

                if subscribers:
                    add_transaction(transactions, "subscribers.set", subscribers)

                # Prepare all parent and subtasks, and check if we have a parent task from the config file
                subtasks = task_config.get("subtasks", [])

                if subtasks:
                    subtasks_phids = []

                    for ticket_id in subtasks:
                        search_result = self.phab.maniphest.search(
                            constraints={"ids": [int(ticket_id[1:])]},
                        )

                        if len(search_result["data"]) != 1:
                            raise PhabfiveRemoteException(f"Unable to find subtask ticket in phabricator instance with ID={ticket_id}")

                        subtasks_phids.append(search_result["data"][0]["phid"])

                    add_transaction(transactions, "subtasks.set", subtasks_phids)

                parents = task_config.get("parents", [])

                if parents:
                    parent_phids = []

                    for ticket_id in parents:
                        search_result = self.phab.maniphest.search(
                            constraints={"ids": [int(ticket_id[1:])]},
                        )

                        if len(search_result["data"]) != 1:
                            raise PhabfiveRemoteException(f"Unable to find parent ticket in phabricator instance with ID={ticket_id}")

                        parent_phids.append(search_result["data"][0]["phid"])

                    add_transaction(transactions, "parents.set", parent_phids)
            else:
                log.warning("Required fields 'title' and 'description' is not present in this data block, skipping ticket creation")

            output["transactions"] = transactions

            processed_child_tasks = []
            child_tasks = task_config.get("tasks", None)

            if child_tasks:
                # If there is child tasks to create, recurse down to all of them one by one
                processed_child_tasks = [
                    recurse_build_transactions(task)
                    for task in child_tasks
                ]
            else:
                processed_child_tasks = []

            output["tasks"] = processed_child_tasks

            return output

        def recurse_commit_transactions(task_config, parent_task_config):
            """
            This recurse functions purpose is to iterate over all tickets, commit them to phabricator
            and link them to eachother via the ticket hiearchy or explicit parent/subtask links.

            task_config is the current task to create and the parent_task_config is if we have a tree
            of tickets defined in our config file.
            """
            log.debug("\n -- Commiting task")
            log.debug(json.dumps(task_config,indent=2))
            log.debug(" ** parent block")
            log.debug(json.dumps(parent_task_config, indent=2))

            transactions_to_commit = task_config.get("transactions", [])

            if transactions_to_commit:
                # Parent ticket based on the task hiearchy defined in the config file we parsed is different
                # from the explicit "ticket parent" that can be defined
                if parent_task_config and "phid" in parent_task_config:
                    add_transaction(transactions_to_commit, "parents.add", [parent_task_config["phid"]])

                log.debug(" -- transactions to commit")
                log.debug(transactions_to_commit)

                if dry_run:
                    log.critical("Running with --dry-run, tickets !!WILL NOT BE!! commited to phabricator")
                else:
                    result = self.phab.maniphest.edit(
                        transactions=transactions_to_commit,
                    )

                    # Store the newly created ticket ID in the data structure so child tickets can look it up
                    task_config["phid"] = str(result["object"]["phid"])
            else:
                log.warning("No transactions to commit here, either a bug or root object that can't be transacted")

            child_tasks = task_config.get("tasks", None)

            if child_tasks:
                for child_task in child_tasks:
                    recurse_commit_transactions(child_task, task_config)

        # Main task recursion logic
        if "tasks" not in root_data:
            raise PhabfiveDataException(f"Config file must contain keyword tasks in the root")

        pre_process_output = pre_process_tasks(root_data)
        log.debug("Final pre_process_output")
        log.debug(json.dumps(pre_process_output, indent=2))
        log.debug("\n----------------\n")

        parsed_root_data = recurse_build_transactions(pre_process_output)
        log.debug(" -- Final built transactions")
        log.debug(json.dumps(parsed_root_data, indent=2))
        log.debug(" -- transactions for all tickets")
        log.debug(parsed_root_data)
        log.debug("\n")

        # Always start with a blank parent
        recurse_commit_transactions(parsed_root_data, None)

def days_to_unix(days):
    """
    Convert days into a UNIX timestamp.
    """
    seconds = int(days) * 24 * 3600
    return int(time.time()) - seconds

def format_timestamp(timestamp):
    """
    Convert UNIX timestamp to ISO 8601 string (readable time format).
    """
    dt = datetime.datetime.fromtimestamp(timestamp)
    return dt.strftime('%Y-%m-%dT%H:%M:%S')
