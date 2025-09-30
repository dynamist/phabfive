# phabfive

A command line tool to interact with Phabricator.

The complete documentation for Phabfive can be found at [Read the Docs](https://phabfive.readthedocs.io/en/latest/).

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

```bash
export PHAB_TOKEN=cli-ABC123
# --OR--
echo "PHAB_TOKEN: cli-ABC123" > ~/.config/phabfive.yaml
```

Usage:

```bash
phabfive passphrase K123
```

## Run local development phabricator instance

First add the following to your `/etc/hosts` file:

```text
127.0.0.1       phabricator.domain.tld
```

Next start the mysql instance separate (in the first terminal) from the phabricator instance with

```bash
docker compose -f docker-compose-phabricator.yml up mysql
```

Then after the database is up and running you can start the webserver with (in a second terminal)

```bash
docker compose -f docker-compose-phabricator.yml up phabricator
```

This startup will take some time to setup the demo instance. Once completed you can access your instance in your browser at `http://phabricator.domain.tld/`. On first use you need to setup your admin account. Most development for phabfive requires an API token, create one here `http://phabricator.domain.tld/settings/user/<username>/page/apitokens/`.

Note there is no persistence disks so if the container is shutdown any data will be lost and you have to restart.


## Run local development phorge instance

First add the following to your `/etc/hosts` file:

```text
127.0.0.1       phorge.domain.tld
127.0.0.1       phorge-files.domain.tld
```

Start the services(mariadb and phorge)

```bash
# Docker users
docker compose -f docker-compose-phorge.yml up --build -d # Detached mode
```
```bash
# Podman users
podman compose -f docker-compose-phorge.yml up --build -d # Detached mode
```

This startup will take some time to setup the demo instance. Once completed you can access your instance in your browser at `http://phorge.domain.tld/`. On first use you need to setup your admin account. Most development for phabfive requires an API token, create one here `http://phorge.domain.tld/settings/user/<username>/page/apitokens/`.

Note
By default the instance won't persist any data so if the container is shutdown any data will be lost and you have to restart.

To change this behavior you can create a `.env` file with the following content:

```
MARIADB_STORAGE_MODE=mariadb_data:/var/lib/mysql
```

## Building a release

For version schema we follow basic [SemVer](https://semver.org/) versioning schema system with the extensions that is defined by python in this [PEP 440](https://peps.python.org/pep-0440/). It allows for some post and dev releases if we need to, but in general we should only publish stable regular semver releases.

Instructions how to build a release and upload it to PyPi can be found in the official [packaging documentation at Python.org](https://packaging.python.org/en/latest/tutorials/packaging-projects/).

In order to be allowed to upload a new release for phabfive, you need to be a *owner* or *maintainer* for this project inside PyPi. Currently only Johan and Henrik are owners. Ask them for assistance if you need permissions to upload a new release.

## LICENSE

Copyright (c) 2017-2024 Dynamist AB

See the LICENSE file provided with the source distribution for full details.
