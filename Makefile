.PHONY: help clean cleanpy cleanall cleantox cleanvenv test install

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

mkdocs-dev:
	mkdocs serve
