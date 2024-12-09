# -*- coding: utf-8 -*-

# python std lib
import json
import logging
from pathlib import Path

# phabfive imports
from phabfive.core import Phabfive
from phabfive.constants import *
from phabfive.exceptions import *

# 3rd party imports
import yaml
from jinja2 import Template

# alex added imports
import time
import datetime
#from rich import print

log = logging.getLogger(__name__)

HOST = "http://phorge.localhost/"

class Maniphest(Phabfive):
    def __init__(self):
        super(Maniphest, self).__init__()

    def alex_search(self, created_after=None, updated_after=None, project=None):
        print(f"{created_after=} days.")
        print(f"{updated_after=} days.")
        print(f"{project=}.\n")

        if created_after is not None:
            seconds = int(created_after) * 24 * 3600
            created_after = int(time.time()) - seconds
        
        if updated_after is not None:
            seconds = int(updated_after) * 24 * 3600
            updated_after = int(time.time()) - seconds

        constraints = {}
        if created_after:
            constraints["createdStart"] = int(created_after)
        if updated_after:
            constraints["modifiedStart"] = int(updated_after)
        if project:
            constraints["projects"] = [f"{project}"]
        
        attachments = {
            "columns": True
        }

        result = self.phab.maniphest.search(constraints=constraints, attachments=attachments)
        result = str(result)
        result = result[9:-1]
        result = result.replace("'", '"')
        result = result.replace("None", '"NULL"')
        result = result.replace("False", '"FALSE"')
        result = result.replace("True", '"TRUE"')
        js_object = json.loads(result)
        
        # If you're developing this app: uncomment below.
        #print(f"\nFull json data from maniphest.search {type(js_object)}\n\n{json.dumps(js_object, indent=4)}\n")

        print(f"Retrieving data for the project '{project}':\n")

        for item in js_object["data"]:
            print(f"Link: {HOST}T{item["id"]}\nID: {item["id"]}")
            
            fields = item["fields"]
            for key, value in fields.items():
                if key in ["name", "dateCreated", "dateModified", "dateClosed"]:
                    if key in ["dateCreated", "dateModified", "dateClosed"]:
                        if value != "NULL":
                            dt = datetime.datetime.fromtimestamp(value)
                            formatted_time = dt.strftime('%Y-%m-%d %H:%M:%S')
                            print(f"{key}: {formatted_time}")
                        else:
                            print(f"{key}: Not closed.")
                    else:
                        print(f"{key}: {value}")

            description_raw = fields["description"]["raw"]
            if description_raw == "":
                print("Description: No description.")
            else:
                print(f"Description: {description_raw}")
            
            status = fields["status"]
            for key, value in status.items():
                if key in ["name"]:
                    print(f"Status: {value}")

            priority = fields["priority"]
            for key, value in priority.items():
                if key in ["name"]:
                    print(f"Priority: {value}")
            
            attachments = item["attachments"]
            for key, value in attachments.items():
                if key == "columns":
                    boards = value.get("boards", {})
                    for board_phid, board_data in boards.items():
                        columns = board_data.get("columns", [])
                        for column in columns:
                            column_name = column.get("name")
                            if column_name:
                                print(f"Column Name: {column_name}")
            print("\n")
            
            #timestamp = 1733136254
            #readable_date = datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
            #print(readable_date)

        #column_result = self.phab.project.column.search()
        #print(column_result)
        
        #result = str(column_result)
        #result = result[9:-1]
        #result = result.replace("'", '"')
        #result = result.replace("None", '"NULL"')
        #result = result.replace("False", '"FALSE"')
        #result = result.replace("True", '"TRUE"')
        #js_object = json.loads(result)
        
        #print(f"\nFull json data from maniphest.search {type(js_object)}\n\n{json.dumps(js_object, indent=4)}\n")
            

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
        users_query = self.phab.user.search()
        username_to_id_mapping = {
            user["fields"]["username"]: user["phid"]
            for user in users_query["data"]
        }

        log.debug(username_to_id_mapping)

        # Fetch all projects in phabricator, used to map ticket -> projects later
        projects_query = self.phab.project.search(constraints={"name": ""})
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

#if __name__ != "__main__":