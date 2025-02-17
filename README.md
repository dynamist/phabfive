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

Configure in Linux:

```bash
export PHAB_TOKEN=cli-ABC123
# --OR--
echo "PHAB_TOKEN: cli-ABC123" > ~/.config/phabfive.yaml
```

Configure in Windows:

Phabfive looks for configuration in the following sequence in Windows environment:

```Commnad prompt
%ALLUSERSPROFILE%\phabfive\phabfive.yaml
%ALLUSERSPROFILE%\phabfive\phabfive.d\*.yaml
%LOCALAPPDATA%\phabfive\phabfive.yaml
%LOCALAPPDATA%\phabfive\phabfive.d\*.yaml
```

Make sure there is a minimum phabfive.yaml store in one of the location
e.g. 
```Commnad prompt
echo "PHAB_TOKEN: cli-ABC123" > %LOCALAPPDATA%\phabfive\phabfive.yaml
```

Additionally, due to connection to Phabricator server on HTTPS requires certificate verification, it is also recommended to install [pip_system_certs](https://pypi.org/project/pip-system-certs/) to ensure system store are linked to python.

```Commnad prompt
pip install pip_system_certs
```

Usage:

```bash
phabfive passphrase K123
phabfive --log-level=DEBUG user whoami
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

## Building a release

For version schema we follow basic [SemVer](https://semver.org/) versioning schema system with the extensions that is defined by python in this [PEP 440](https://peps.python.org/pep-0440/). It allows for some post and dev releases if we need to, but in general we should only publish stable regular semver releases.

Instructions how to build a release and upload it to PyPi can be found in the official [packaging documentation at Python.org](https://packaging.python.org/en/latest/tutorials/packaging-projects/).

In order to be allowed to upload a new release for phabfive, you need to be a *owner* or *maintainer* for this project inside PyPi. Currently only Johan and Henrik are owners. Ask them for assistance if you need permissions to upload a new release.

## LICENSE

Copyright (c) 2017-2024 Dynamist AB

See the LICENSE file provided with the source distribution for full details.
