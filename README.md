# phabfive

A simple tool to interact with Phabricator.

## Features

A summary of the currently supported actions, as well as planned features:

- Passphrase
  - [ ] List secrets
  - [X] Get specified secret
  - [ ] Add secret
- Diffusion
  - [X] List repositories names
  - [ ] Get branches for specified repository
  - [ ] Get clone URI:s for specified repository
  - [ ] Add repository
  - [ ] Edit URI
- Paste
  - [X] List pastes
  - [ ] Get specified paste
  - [ ] Add paste

## Example usage

Grab a Phabricator token at https://<yourserver.com>/settings/panel/apitokens/

Configure:

     export PHAB_TOKEN=cli-ABC123
    --OR--
     echo "PHAB_TOKEN: cli-ABC123" > ~/.config/phabfive.yaml

Usage:

    phabfive passphrase K123


## Installation of a phabfive development environment

Install the `virtualenv` and `virtualenvwrapper` software packages.

Ensure to source the correct virtualenvwrapper shell script before installing the development environment, on Ubuntu that would be `source /usr/share/virtualenvwrapper/virtualenvwrapper.sh`.

Create a virtualenv for phabfive running on Python 2.7:
```
mkvirtualenv phabfive27 --python=python2.7
```

Install code in editable mode and pull in all dependencies:
```
workon phabfive27
pip install -e '.[test]'
```

Repeat this for Python 3, for example version 3.6.


## Build and run a phabfive Docker images

Build a Docker image:
```
docker build -t phabfive .
```

Run a one-off execution:
```
docker run --rm phabfive
docker run --rm phabfive passphrase --help
```


## Run unittests

This repo uses `pytest` module as the test runner and `tox` to orchestrate tests for various Python versions.

To run the tests locally on your machine for all supported and installed versions of Python 2 and 3:
```
make test
```

Or individually for Python 2.7 or Python 3.6:
```
tox -e py27
tox -e py36
```

Old versions of Python are available in the Deadsnakes PPA for Ubuntu or EPEL for Red Hat.


## Set up a Phabricator instance for tests

Follow the instructions at https://docs.docker.com/install/ to install Docker, once this is done continue with the steps below.

Go into the `tests` directory and start the Bitnami Docker image for Phabricator and MySQL. The documentation for that image is at: https://github.com/bitnami/bitnami-docker-phabricator
```
cd tests
docker-compose up -d
```

Now Phabricator is accessible on localhost. Go to http://127.0.0.1/ and log in with user `user` and password `bitnami1`, then head over to http://127.0.0.1/settings/user/user/page/apitokens/ to create your Conduit API token. Add those to `~/.config/phabfive.yaml`, here is an example:
```
PHAB_URL: http://127.0.0.1/api/
PHAB_TOKEN: api-2hwi... (your token)
```

You should now be able to run phabfive against your own local copy of Phabricator!

To view logs as they come in: `docker-compose logs -f`
To shut down containers and remove its data: `docker-compose down -v`


## LICENSE

Copyright (c) 2017-2018 Dynamist AB

See the LICENSE file provided with the source distribution for full details.
