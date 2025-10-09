.PHONY: help clean cleanpy cleanall cleantox cleanvenv test install phorge-down phorge-up phorge-logs phorge-shell phabfive-build phabfive-run phabfive-run-dev docs

# Detect container runtime (prefer podman)
CONTAINER_RUNTIME := $(shell command -v podman 2> /dev/null)
ifndef CONTAINER_RUNTIME
    CONTAINER_RUNTIME := $(shell command -v docker 2> /dev/null)
endif
ifndef CONTAINER_RUNTIME
    $(error Neither podman nor docker found. Please install one of them.)
endif

COMPOSE_FILE := compose-phorge.yml

# Detect host's phabfive config file (OS-specific via appdirs)
PHABFIVE_HOST_CONFIG := $(shell \
	if [ -f "$(HOME)/Library/Application Support/phabfive.yaml" ]; then \
		echo "$(HOME)/Library/Application Support/phabfive.yaml"; \
	elif [ -f "$(HOME)/.config/phabfive.yaml" ]; then \
		echo "$(HOME)/.config/phabfive.yaml"; \
	fi)

# Build mount flag if config file exists
PHABFIVE_CONFIG_MOUNT := $(if $(PHABFIVE_HOST_CONFIG),-v "$(PHABFIVE_HOST_CONFIG):/root/.config/phabfive.yaml:ro",)

# http://marmelab.com/blog/2016/02/29/auto-documented-makefile.html
help:
	@awk 'BEGIN {FS = ":.*?## "; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} \
		/^[a-zA-Z_0-9-]+:.*?##/ { printf "  \033[36m%-28s\033[0m %s\n", $$1, $$2 } \
		/^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

##@ Essential Development

install: ## install package and dev dependencies
	uv sync --group dev

test: install ## run test suite
	uv run tox --skip-missing-interpreters

docs: ## build and serve documentation
	uv sync --extra docs
	uv run mkdocs serve --livereload
##@ Cleanup

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

##@ Build

sdist: clean ## make a source distribution
	uv build --sdist

bdist: clean ## build a wheel distribution
	uv build --wheel

##@ Phorge Testing

phorge-down: ## stop and remove phorge containers
	$(CONTAINER_RUNTIME) compose -f $(COMPOSE_FILE) down

phorge-up: ## start phorge (mariadb detached, phorge in foreground)
	@echo "Starting mariadb in background..."
	@$(CONTAINER_RUNTIME) compose -f $(COMPOSE_FILE) up -d mariadb
	@echo "Waiting for mariadb to be ready..."
	@sleep 3
	@echo "Starting phorge in foreground (logs will be visible)..."
	$(CONTAINER_RUNTIME) compose -f $(COMPOSE_FILE) up --build phorge

phorge-logs: ## view logs from phorge containers
	$(CONTAINER_RUNTIME) compose -f $(COMPOSE_FILE) logs -f

phorge-shell: ## open shell in phorge container
	$(CONTAINER_RUNTIME) compose -f $(COMPOSE_FILE) exec phorge /bin/bash

##@ Docker

phabfive-build: ## build phabfive docker image
	$(CONTAINER_RUNTIME) build -f Dockerfile -t phabfive .

phabfive-run: phabfive-build ## run phabfive in docker container with ARGS="your args here"
	$(CONTAINER_RUNTIME) run --rm \
		--env PHAB_TOKEN --env PHAB_URL \
		$(PHABFIVE_CONFIG_MOUNT) \
		phabfive $(ARGS)

phabfive-run-dev: phabfive-build ## run phabfive connected to local phorge instance with ARGS="your args here"
	$(CONTAINER_RUNTIME) run --rm \
		--env PHAB_TOKEN --env PHAB_URL \
		--add-host=phorge.domain.tld:host-gateway \
		--add-host=phorge-files.domain.tld:host-gateway \
		$(PHABFIVE_CONFIG_MOUNT) \
		phabfive $(ARGS)
