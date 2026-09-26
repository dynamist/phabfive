.PHONY: help install tools test smoke docs format lock upgrade clean cleanpy cleanall cleantox cleanvenv sdist bdist image check-runtime check-tools clear-cache cluster destroy phorge-image deploy up down reset logs ps shell creds validate test-k8s test-e2e ci-deploy ci-test

# Detect container runtime (prefer podman)
CONTAINER_RUNTIME = $(or \
	$(shell command -v podman 2>/dev/null), \
	$(shell command -v docker 2>/dev/null) \
)

# Shared k3d cluster, see k8s/cluster/k3d.yaml. Every kubectl call names the
# context explicitly so nothing here ever acts on another cluster.
CLUSTER := dynamist
KUBE_CONTEXT := k3d-$(CLUSTER)
NAMESPACE := phorge
KUBECTL = mise exec -- kubectl --context $(KUBE_CONTEXT) -n $(NAMESPACE)
K3D = mise exec -- k3d

# libc of the phabfive image built by `make image` (gnu or musl)
LIBC ?= gnu

# Kustomize overlay to deploy (local or ci)
OVERLAY ?= local
PHORGE_IMAGE := dynamist/phorge
BUILD_DIR := .k8s

# Same URL as k8s/base/config.env, so the cache clearing below targets the
# instance in the cluster
PHORGE_URL ?= http://phorge.localhost

# http://marmelab.com/blog/2016/02/29/auto-documented-makefile.html
help:
	@awk 'BEGIN {FS = ":.*?## "; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} \
		/^[a-zA-Z_0-9-]+:.*?##/ { printf "  \033[36m%-28s\033[0m %s\n", $$1, $$2 } \
		/^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

##@ Essential Development

install: ## install package and dev dependencies
	uv sync --group dev

tools: ## install pinned CLI tools (k3d, kubectl, kubeconform) with mise
	mise install

test: install ## run test suite
	uv run tox --skip-missing-interpreters

smoke: ## install unlocked into a throwaway venv and run the built CLI
	rm -rf .smoke
	python3 -m venv .smoke
	.smoke/bin/python -m pip install --quiet --upgrade pip
	.smoke/bin/pip install --quiet .
	python3 scripts/smoke.py --venv .smoke
	rm -rf .smoke

docs: ## build and serve documentation
	uv sync --group docs
	uv run mkdocs serve --livereload

format: ## format code using ruff
	ruff format .

lock: ## sync uv.lock with pyproject.toml
	uv lock

upgrade: ## upgrade all dependencies to latest compatible versions
	uv lock --upgrade

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

image: check-runtime ## build phabfive scratch image (LIBC=gnu|musl)
	$(CONTAINER_RUNTIME) build --build-arg LIBC=$(LIBC) -t phabfive:$(LIBC) .

# Internal helpers, not listed in make help

check-runtime:
	@echo "Checking runtime..."
	@if [ -z "$(CONTAINER_RUNTIME)" ]; then \
		echo "Error: Neither podman nor docker found."; \
		exit 1; \
	fi
	@$(CONTAINER_RUNTIME) --version

check-tools:
	@command -v mise >/dev/null || { echo "Error: mise not found, see https://mise.jdx.dev"; exit 1; }
	@command -v docker >/dev/null || { echo "Error: docker not found, k3d runs the cluster in docker"; exit 1; }

clear-cache:
	@echo "Clearing the completion cache for $(PHORGE_URL)..."
	@uv run phabfive cache clear --url $(PHORGE_URL) 2>/dev/null \
		|| echo "  skipped, could not run phabfive (try: make install)"

##@ Cluster

cluster: check-tools ## create the shared k3d cluster, or reuse it
	@if $(K3D) cluster get $(CLUSTER) >/dev/null 2>&1; then \
		echo "Reusing k3d cluster $(CLUSTER)"; \
		$(K3D) cluster start $(CLUSTER) >/dev/null; \
	else \
		$(K3D) cluster create --config k8s/cluster/k3d.yaml; \
	fi
	@pinned=$$(sed -n 's|^image: rancher/k3s:\(.*\)|\1|p' k8s/cluster/k3d.yaml | tr - +); \
	running=$$(mise exec -- kubectl --context $(KUBE_CONTEXT) get nodes -o jsonpath='{.items[0].status.nodeInfo.kubeletVersion}'); \
	if [ "$$pinned" != "$$running" ]; then \
		echo "WARNING: cluster runs k3s $$running but k8s/cluster/k3d.yaml pins $$pinned, see make destroy"; \
	fi
	@echo "Waiting for the Traefik ingress..."
	@until mise exec -- kubectl --context $(KUBE_CONTEXT) -n kube-system get deploy traefik >/dev/null 2>&1; do sleep 3; done
	@mise exec -- kubectl --context $(KUBE_CONTEXT) -n kube-system rollout status deploy/traefik --timeout=5m

destroy: check-tools ## DELETE the shared cluster with every app and all data (FORCE=1 if other apps run)
	@others=$$(mise exec -- kubectl --context $(KUBE_CONTEXT) get ns -l 'dynamist.se/dev-app,dynamist.se/dev-app!=$(NAMESPACE)' -o name 2>/dev/null); \
	if [ -n "$$others" ] && [ "$(FORCE)" != "1" ]; then \
		echo "Other apps run in the cluster: $$others"; \
		echo "Use make reset to delete only phorge, or make destroy FORCE=1 to delete them too"; \
		exit 1; \
	fi
	$(K3D) cluster delete $(CLUSTER)
	@$(MAKE) --no-print-directory clear-cache

##@ Phorge

phorge-image: check-tools ## build the phorge image and import it into the cluster
	docker build -t $(PHORGE_IMAGE):dev phorge
	@mkdir -p $(BUILD_DIR)
	@# Tag by content, so the deployment only rolls out when the image changed
	@tag=dev-$$(docker image inspect -f '{{.Id}}' $(PHORGE_IMAGE):dev | cut -d: -f2 | cut -c1-12); \
	docker tag $(PHORGE_IMAGE):dev $(PHORGE_IMAGE):$$tag; \
	$(K3D) image import -c $(CLUSTER) $(PHORGE_IMAGE):$$tag; \
	echo $$tag > $(BUILD_DIR)/image-tag

deploy: check-tools ## apply the manifests of OVERLAY (local or ci) with the imported image
	@test -f $(BUILD_DIR)/image-tag || { echo "Error: no image imported yet, run make phorge-image"; exit 1; }
	@touch k8s/overlays/local/config.local.env
	@printf '%s\n' \
		'apiVersion: kustomize.config.k8s.io/v1beta1' \
		'kind: Kustomization' \
		'resources: [../k8s/overlays/$(OVERLAY)]' \
		'images: [{name: $(PHORGE_IMAGE), newTag: '"$$(cat $(BUILD_DIR)/image-tag)"'}]' \
		> $(BUILD_DIR)/kustomization.yaml
	mise exec -- kubectl --context $(KUBE_CONTEXT) apply -k $(BUILD_DIR)

up: cluster phorge-image deploy ## start phorge in the cluster and follow its logs until it is ready
	@$(KUBECTL) rollout status statefulset/mariadb --timeout=5m
	@$(KUBECTL) logs -f deploy/phorge --pod-running-timeout=5m & logs=$$!; \
	$(KUBECTL) rollout status deploy/phorge --timeout=20m; status=$$?; \
	sleep 2; kill $$logs 2>/dev/null; exit $$status

down: check-tools ## stop phorge and mariadb, keep data
	$(KUBECTL) scale deploy/phorge statefulset/mariadb --replicas=0

reset: check-tools ## DELETE the phorge namespace with all its data and clear its completion cache
	mise exec -- kubectl --context $(KUBE_CONTEXT) delete namespace $(NAMESPACE) --ignore-not-found --wait
	@$(MAKE) --no-print-directory clear-cache

logs: check-tools ## follow phorge logs
	$(KUBECTL) logs -f deploy/phorge

ps: check-tools ## show pods, services, ingress and volumes
	$(KUBECTL) get pods,svc,ingress,pvc

shell: check-tools ## open shell in the phorge pod
	$(KUBECTL) exec -it deploy/phorge -- /bin/bash

creds: check-tools ## print credentials of the running phorge
	@$(KUBECTL) exec deploy/phorge -- /usr/local/bin/lib/banner.sh

##@ Test

validate: ## validate the rendered manifests of all overlays
	@touch k8s/overlays/local/config.local.env
	@for overlay in k8s/overlays/*/; do \
		mise exec -- kubectl kustomize $$overlay | mise exec -- kubeconform -strict -summary || exit 1; \
	done

test-k8s: ## run the smoke, seed data and isolation tests in tests/k8s against the deployed phorge (PYTEST_ARGS="-k smoke" for pytest)
	PHABFIVE_LIVE_TESTS=1 mise exec -- uv run --no-project --with pytest --with requests --with pyyaml \
		pytest tests/k8s -p no:cacheprovider $(PYTEST_ARGS)

test-e2e: install ## run phabfive's end-to-end tests (the CLI against the deployed phorge, PYTEST_ARGS="-k whoami" for pytest)
	PHABFIVE_LIVE_TESTS=1 PHAB_URL=$(PHORGE_URL)/api/ PHAB_TOKEN=api-supersecr3tapikeyfordevelop1 \
		uv run pytest tests/e2e $(PYTEST_ARGS)

##@ CI

# Every app repo using the shared cluster provides ci-deploy and ci-test, so CI
# can deploy other apps next to this one without knowing them

ci-deploy: cluster phorge-image ## build and deploy the ci overlay, wait until it is ready
	$(MAKE) --no-print-directory deploy OVERLAY=ci
	$(KUBECTL) rollout status statefulset/mariadb --timeout=10m
	$(KUBECTL) rollout status deploy/phorge --timeout=30m

ci-test: ## run every test against the deployed ci overlay
	$(MAKE) --no-print-directory test-k8s PYTEST_ARGS="-v"
	$(MAKE) --no-print-directory test-e2e PYTEST_ARGS="-v"
