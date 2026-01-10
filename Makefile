.PHONY: help clean cleanpy cleanall cleantox cleanvenv test install up down logs shell image docs format

# Detect container runtime (prefer podman)
CONTAINER_RUNTIME = $(or \
	$(shell command -v podman 2>/dev/null), \
	$(shell command -v docker 2>/dev/null) \
)

COMPOSE_FILE := compose.yml

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

format: ## format code using ruff
	ruff format .

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

image: check-runtime ## build phabfive container image
	$(CONTAINER_RUNTIME) build -f Dockerfile -t phabfive .

check-runtime: ## Checks runtime and exits if not found
	@echo "Checking runtime..."
	@if [ -z "$(CONTAINER_RUNTIME)" ]; then \
		echo "Error: Neither podman nor docker found."; \
		exit 1; \
	fi
	@$(CONTAINER_RUNTIME) --version

##@ Phorge

down: check-runtime ## stop and remove phorge containers
	$(CONTAINER_RUNTIME) compose -f $(COMPOSE_FILE) down

up: check-runtime ## start phorge (mariadb detached, phorge in foreground)
	@echo "Starting mariadb in background..."
	@$(CONTAINER_RUNTIME) compose -f $(COMPOSE_FILE) up -d mariadb
	@echo "Waiting for mariadb to be ready..."
	@sleep 3
	@echo "Starting phorge in foreground (logs will be visible)..."
	$(CONTAINER_RUNTIME) compose -f $(COMPOSE_FILE) up --build phorge

logs: check-runtime ## view logs from phorge containers
	$(CONTAINER_RUNTIME) compose -f $(COMPOSE_FILE) logs -f

shell: check-runtime ## open shell in phorge container
	$(CONTAINER_RUNTIME) compose -f $(COMPOSE_FILE) exec phorge /bin/bash
