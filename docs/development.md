## Installation in a development environment

Install the `virtualenv` and `virtualenvwrapper` software packages.

Ensure to source the correct virtualenvwrapper shell script before installing the development environment, on Ubuntu that would be `source /usr/share/virtualenvwrapper/virtualenvwrapper.sh`.

Create a virtualenv for phabfive running on Python 3.8:
```
mkvirtualenv phabfive --python=python3.8
```

Install code in editable mode and pull in all test and documentation dependencies:
```
workon phabfive37
pip install -e '.[test,docs]'
```



## Build and run in a Docker image

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

To run the tests locally on your machine for all supported and installed versions of Python 3:
```
make test
```

Or individually for Python 3.8 and Python 3.10:
```
tox -e py38
tox -e py310
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



## Run mkdocs locally for documentation updates

Overall documentation for mkdocs can be found at [Mkdocs homepage](https://www.mkdocs.org/#installation)

Install mkdocs in a virtualenv

```
pip install mkdocs
```

From the root of phabfive project folder run

```
$ mkdocs serve

INFO    -  Building documentation...
INFO    -  Cleaning site directory
[I 160402 15:50:43 server:271] Serving on http://127.0.0.1:8000
[I 160402 15:50:43 handlers:58] Start watching changes
[I 160402 15:50:43 handlers:60] Start detecting changes
```

Surf into `http://127.0.0.1:8000` to view the rendered docs.
