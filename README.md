# phabfive

A command line tool to interact with Phabricator

The complete documentation for Phabfive can be found at [Read the Docs](https://phabfive.readthedocs.io/en/latest/)


## Features

A summary of the currently supported features:

- Passphrase
  - Get specified secret
- Diffusion
  - List repositories
  - Get branches for specified repository
  - Get clone URI for specified repository
  - Add repository
  - Edit URI
  - Create URI (observe repository)
- Paste
  - List pastes
  - Get specified paste
  - Add paste
- User
  - Who am I: information about the logged-in user
- Maniphest
  - Add comment to task
  - Show task summary or full details
  - Create multiple tasks via template config file


## Example usage

Grab a Phabricator token at `https://<yourserver.com>/settings/panel/apitokens/`

Configure:

     export PHAB_TOKEN=cli-ABC123
    --OR--
     echo "PHAB_TOKEN: cli-ABC123" > ~/.config/phabfive.yaml

Usage:

    phabfive passphrase K123


## Run local development phabricator instance

First add the following `127.0.0.1       phabricator.domain.tld` to your `/etc/hosts` file

Next start the mysql instance separate from the phabricator instance with

`docker compose -f docker-compose-phabricator.yml up mysql`

Then after the database is up and running yo ucan start the webserver with

`docker compose -f docker-compose-phabricator.yml up phabricator`

This startup will take some time to setup the demo instance. Once completed you can access your instance in your browser at `http://phabricator.domain.tld/`. On first use you need to setup your admin account. Most development for phabfive requires an API token, create one here `http://phabricator.domain.tld/settings/user/<username>/page/apitokens/`

Note there is no persistance disks so if the container is shutdown any data will be lost and you have to restart


## Building a release

For version schema we follow basic SemVer versioning schema system with the extensions that is defined by python in this PEP440 https://peps.python.org/pep-0440/ It allows for some post and dev releases if we need to, but in general we should only publish stable regular semver releases.

Instructions how to build a release and upload it to PyPi can be found here in the official build tools documentation site https://packaging.python.org/en/latest/tutorials/packaging-projects/

In order to be allowed to upload a new release for phabfive, you need to be a Owner or maintainer for this project inside PyPi. Currently only Johan and Henrik is owners. Ask them for assistance if you need upload permissions for a new release.


## LICENSE

Copyright (c) 2017-2023 Dynamist AB

See the LICENSE file provided with the source distribution for full details.
