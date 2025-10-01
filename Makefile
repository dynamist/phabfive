.PHONY: help clean cleanpy cleanall cleantox cleanvenv test install phorgedown phorgeup phorgereset phorgelogs phorgeshell

# Detect container runtime (prefer podman)
CONTAINER_RUNTIME := $(shell command -v podman 2> /dev/null)
ifndef CONTAINER_RUNTIME
    CONTAINER_RUNTIME := $(shell command -v docker 2> /dev/null)
endif
ifndef CONTAINER_RUNTIME
    $(error Neither podman nor docker found. Please install one of them.)
endif

COMPOSE_FILE := compose-phorge.yml

# http://marmelab.com/blog/2016/02/29/auto-documented-makefile.html
help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'


clean: ## remove temporary files created by build tools
	-rm -f MANIFEST
	-rm -rf dist/
	-rm -rf build/

cleanpy: ## remove temporary python files
	-find . -type f -name "*~" -exec rm -f "{}" \;
	-find . -type f -name "*.orig" -exec rm -f "{}" \;
	-find . -type f -name "*.rej" -exec rm -f "{}" \;
	-find . -type f -name "*.pyc" -exec rm -f "{}" \;
	-find . -type f -name "*.parse-index" -exec rm -f "{}" \;
	-find . -type d -name "__pycache__" -exec rm -rf "{}" \;

cleanall: clean cleanpy ## all the above (not cleantox or cleanvenv)

cleantox: ## remove files created by tox
	-rm -rf .tox/

cleanvenv: ## remove files created by virtualenv
	-rm -rf .venv/

test: ## run test suite
	tox --skip-missing-interpreters

sdist: ## make a source distribution
	python setup.py sdist

bdist: ## build a wheel distribution
	python setup.py bdist_wheel

install: ## install package
	python setup.py install

phorgedown: ## stop and remove phorge containers
	$(CONTAINER_RUNTIME) compose -f $(COMPOSE_FILE) down

phorgeup: ## start phorge (mariadb detached, phorge in foreground)
	@echo "Starting mariadb in background..."
	@$(CONTAINER_RUNTIME) compose -f $(COMPOSE_FILE) up -d mariadb
	@echo "Waiting for mariadb to be ready..."
	@sleep 3
	@echo "Starting phorge in foreground (logs will be visible)..."
	$(CONTAINER_RUNTIME) compose -f $(COMPOSE_FILE) up --build phorge

phorgereset: ## stop, remove, rebuild and start phorge
	@echo "Stopping and removing containers..."
	@$(CONTAINER_RUNTIME) compose -f $(COMPOSE_FILE) down
	@echo "Starting mariadb in background..."
	@$(CONTAINER_RUNTIME) compose -f $(COMPOSE_FILE) up -d mariadb
	@echo "Waiting for mariadb to be ready..."
	@sleep 3
	@echo "Starting phorge in foreground (logs will be visible)..."
	$(CONTAINER_RUNTIME) compose -f $(COMPOSE_FILE) up --build phorge

phorgelogs: ## view logs from phorge containers
	$(CONTAINER_RUNTIME) compose -f $(COMPOSE_FILE) logs -f

phorgeshell: ## open shell in phorge container
	$(CONTAINER_RUNTIME) compose -f $(COMPOSE_FILE) exec phorge /bin/bash
