# 0.8.0 (2026-03-30)

## Prelude

Major release featuring a migration from docopt-ng to Typer for the CLI framework, Arcanist-compatible configuration, and numerous new features including shell completion, JSON output, parent/subtask support, and search metadata embedding.

## Upgrade Notes

* **Breaking change:** Configuration now uses Arcanist-compatible `.arcrc` format. Existing `~/.config/phabfive.yaml` configurations need to be migrated. Run `phabfive user setup` for guided setup.
* **CLI framework migrated from docopt-ng to Typer** - Command syntax is unchanged, but help output and error messages may differ.
* **`--format=strict` renamed to `--format=yaml`** - The `strict` alias is still accepted for backwards compatibility.

## New Features

### CLI & Shell Completion
* **Typer-based CLI** - Migrated from docopt-ng to Typer for better argument parsing, type safety, and help output
* **Smart shell completion** - Tab completion for `--priority`, `--status`, `--column` values, and all other option values
* **JSON output format** - New `--format=json` option for machine-readable output

### Maniphest Enhancements
* **Multi-task show** - `maniphest show` now accepts multiple task IDs (e.g., `phabfive maniphest show T123 T456`)
* **Parents and subtasks** - New `maniphest parents` and `maniphest subtasks` commands, plus Parents/Subtasks fields in task output
* **Search metadata Query embedding** - `--show-metadata` now includes a `Query` section with original search parameters, enabling downstream tools to access search context without duplicate arguments

### Configuration
* **Arcanist-compatible configuration** - Reads credentials from `.arcrc` files, compatible with Arcanist/Phorge tooling
* **Interactive server selector** - When multiple hosts are configured in `.arcrc`, presents an interactive selection prompt

### Other
* **Passphrase structured output** - `passphrase show` now outputs structured data
* **Optional ptpython REPL** - Enhanced REPL experience when ptpython is installed

## Bug Fixes

* Fix JSON output producing invalid JSON array for multiple tasks
* Fix monogram shortcuts not working with global options
* Improve error handling and standardize error format
* Fix passphrase show subcommand for monogram shortcuts

## Other Notes

* Replaced flake8 with ruff for linting
* Updated license to SPDX identifier format
* Removed unused MANIFEST.in
* Updated pre-commit hooks
* Cleaned up constants.py


# 0.7.1 (2026-03-07)

## Bug Fixes

* Fix standalone executables exiting silently without output


# 0.7.0 (2026-03-07)

## Prelude

This release adds interactive first-run configuration, standalone executables for all major platforms, and improved developer experience with Sigstore-signed releases.

## New Features

### Interactive Setup
* **First-run configuration** - New `phabfive user setup` command guides users through initial configuration with interactive prompts
* **Secure token input** - API token input is masked with dots for security
* **Smart reconfiguration** - Warns before overwriting existing working configuration

### Release Artifacts
* **Standalone executables** - Pre-built binaries for Linux, macOS, and Windows (AMD64 and ARM64)
* **Sigstore signing** - All executables are cryptographically signed for verification
* **RC tag support** - Release candidates skip PyPI for testing the release process

## Bug Fixes

* Fix Rich markup escaping for square brackets in user content
* Fix project lookup failing when more than 100 projects exist

## Other Notes

* Version now sourced solely from `pyproject.toml` using `importlib.metadata`
* Added `AGENTS.md` for AI coding agent guidance
* Removed unused `__author__`, `__email__`, `__url__` module attributes


# 0.6.0 (2026-03-07)

## Prelude

This release brings significant internal refactoring, new search filters, and automated release infrastructure. The codebase has been restructured with maniphest, diffusion, and transitions modules converted to package structure for better maintainability.

## Upgrade Notes

* **Breaking change:** Closed tasks are now hidden by default in `maniphest search`. Use `--all` to include closed tasks.

## New Features

### Search Enhancements
* **Space filtering** - New `--space` flag for filtering tasks by Phabricator Space
* **Assignee filtering** - New `--assigned` filter with `@me` shortcut and OR logic support (comma-separated values)
* **Date range filters** - New `--created-before` and `--updated-before` filters
* **Time unit support** - Date filters now accept time units (h, d, w, m, y) e.g., `--created-after 2d`
* **Auto-detect strict format** - `--format=strict` is automatically enabled when output is piped (#131)

### Configuration
* **Arcanist support** - Added support for reading configuration from `.arcrc` files
* **IPv6 and port support** - `PHAB_URL` validation now accepts IPv6 addresses and port numbers

### User Experience
* **Syntax hints** - Helpful hints displayed for invalid `--status`, `--priority`, and `--column` values

## Bug Fixes

* Fix project lookup failing when more than 100 projects exist (#140)
* Fix Rich markup issues with square brackets in user content
* Handle deleted working directory gracefully
* Handle KeyboardInterrupt gracefully in CLI
* Improve Conduit access denied error message for passphrase
* Fix formatting issues on readthedocs.io
* Skip permission checks on Windows

## Refactoring

* Convert maniphest module to package structure
* Convert diffusion module to package structure
* Consolidate transition modules into package
* Move display logic from library to CLI layer
* Improve method naming across modules
* Rename TransitionPattern to ColumnPattern for consistency

## Other Notes

* Added GitHub Actions workflow for automated PyPI releases using trusted publishing (OIDC)
* Applied ruff formatting across codebase


# 0.5.0 (2026-01-10)

## Prelude

Feature release focused on improved output formatting, enhanced task management, and better user experience. This release introduces multiple output formats, clickable hyperlinks, CLI-based task creation, and search templates.

## Upgrade Notes

* **New dependency:** `rich>=13.0.0` added for enhanced terminal output formatting

## New Features

### Output Formatting
* **Multiple output formats** - New `--format` option supporting `rich` (default), `tree`, and `strict` modes
* **Clickable hyperlinks** - Terminal hyperlinks for task IDs, column names, assignees, and board names with `--hyperlink` option
* **ASCII mode** - Use `--ascii` flag for terminals without Unicode support (uses hyphens instead of bullets)

### Maniphest Enhancements
* **CLI-based task creation** - Create tasks directly from command line with `maniphest create`
* **Show task comments** - New `--show-comments` flag to display comments when viewing tasks
* **Comment shorthand** - Simplified syntax for adding comments to tasks
* **Assignee display** - Task views now show assignee information and history
* **YAML search templates** - Define reusable search queries with multi-document YAML support
* **Enhanced free-text search** - More flexible filtering options for task searches

### Developer Experience
* **Modernized Phorge environment** - Updated Docker development setup with configurable environment variables
* **Improved Makefile** - Added `lock` and `upgrade` targets for dependency management

## Bug Fixes

* Fixed comments not displaying in task show output
* Fixed ASCII bullet character (now uses hyphen instead of asterisk)
* Fixed input validation and logging configuration issues
* Improved UX and logging for maniphest search command
* Enhanced error handling and code clarity

## Other Notes

* Normalized error message format to use "ERROR - " prefix consistently
* Updated CLI option style from `--option=<style>` to `--option=STYLE` for consistency with docopt conventions
* Improved test coverage for maniphest task search functionality


# 0.4.0 (2025-11-12)

## Prelude

Major feature release focused on significantly expanding Maniphest capabilities and modernizing the project infrastructure. This release introduces advanced task filtering, search patterns, template v2 system, and comprehensive developer tooling with Phorge Docker setup.

## Upgrade Notes

* **Python support bumped to minimum version 3.10** (adds support for 3.13 and 3.14)
* **Project management migrated to modern `pyproject.toml`** - replaced `setup.py` with PEP 621 compliant configuration
* **Switched to `uv` for dependency management** - faster, more reliable package management
* **Dependency updates:**
  - `docopt` → `docopt-ng` for improved Python 3 support
  - Added `ruamel-yaml>=0.18.16`
  - Updated `mkdocs>=1.6.1`

## New Features

### Maniphest Enhancements
* **Advanced filtering system** - Filter tasks by status, priority, and projects with complex logic
* **Wildcard project search** - Search and resolve projects using pattern matching
* **Search negation support** - Exclude items from search results with negation patterns
* **Pagination for large result sets** - Automatically handles API pagination for projects and tasks
* **Template v2 system** - Complete rewrite with variable dependency resolution and improved structure
* **Task transition management** - Advanced filtering for status, priority, and project transitions
* **Project column inspection** - Query project boards to see columns and associated tasks
* **Monogram support** - View tasks using T123 format directly from CLI
* **YAML output improvements** - Proper formatting using yaml libraries

### Developer Experience
* **Phorge Docker environment** - Automated local Phorge setup for testing and development
* **Enhanced Makefile** - Dependency checks, Phorge management commands, improved build targets
* **REPL tab completion** - Navigate commands more efficiently in interactive mode
* **Comprehensive documentation:**
  - Detailed maniphest CLI guide (`docs/maniphest-cli.md`)
  - Phorge setup instructions (`docs/phorge-setup.md`)
  - Release process documentation (`docs/releasing.md`)
* **ReadTheDocs integration** - Hosted documentation at readthedocs.org

### Testing & Quality
* **Windows CI support** - Cross-platform testing in CI matrix

## Bug Fixes
* Fixed logging output to use stderr instead of stdout
* Improved URL validation and parsing logic
* Corrected YAML output formatting issues

## Other Notes
* Added `.editorconfig` for consistent code style
* Enhanced `.flake8` configuration
* Added `dependabot` support for automated dependency updates


# 0.3.0 (2023-01-13)

## Prelude

Maintenance release where we focus more on updating the current code and less on new features

The main new features to look for is the updated docker-compose.yml solution

Second major feature is the new maniphest app where we can query, add comment and create a batch of tasks from config file


## Upgrade notes

* Python support bumped up to minimum version of python 3.9


## New features

* Add in dependabot support to check for new python packages
* [#51](https://github.com/dynamist/phabfive/pull/51) - Add support for rendering a batch of tickets and bulk create tickets at one time
* Update support and logging feature to be more modern and better configurable from CLI
* Added new dependency jinja2


# 0.2.0 (2022-03-17)

## Prelude

Update to accommodate new Python versions and updated dependencies.

## Upgrade Notes

* Python 2.7 support has been dropped. The minimum version of Python now supported by Phabfive is version 3.8.

## Bug Fixes

* [#40](https://github.com/dynamist/phabfive/pull/40) - Update to anyconfig API >= 0.10.0

# 0.1.0 (2019-11-01)

## Prelude

Initial release of Phabfive.

Supported Phabricator app endpoints:

 - passphrase
 - diffusion
 - paste
 - user

## New Features

* [#23](https://github.com/dynamist/phabfive/pull/23) - Function to get clone uri(s) from repo
* [#22](https://github.com/dynamist/phabfive/pull/22) - Functionality to create Paste
* [#21](https://github.com/dynamist/phabfive/pull/21) - Raise exception when Conduit access is not accepted for Passphrase
* [#20](https://github.com/dynamist/phabfive/pull/20) - Add functionality to edit URI
* [#19](https://github.com/dynamist/phabfive/pull/19) - Feature/edit uri
* [#16](https://github.com/dynamist/phabfive/pull/16) - Feature/observe repositories
* [#14](https://github.com/dynamist/phabfive/pull/14) - Print data from user.whoami
* [#12](https://github.com/dynamist/phabfive/pull/12) - Errors now print to stderr
* [#11](https://github.com/dynamist/phabfive/pull/11) - Default to only listing active repositories
* [#10](https://github.com/dynamist/phabfive/pull/10) - Adding shortName
* [#9](https://github.com/dynamist/phabfive/pull/9) - Feature/get specified paste
* [#8](https://github.com/dynamist/phabfive/pull/8) - Repositories can now be created
* [#6](https://github.com/dynamist/phabfive/pull/6) - Avoid string default
* [#5](https://github.com/dynamist/phabfive/pull/5) - Pastes can now be listed, sort based on title
* [#3](https://github.com/dynamist/phabfive/pull/3) - Added Paste app

## Other Notes

* [#24](https://github.com/dynamist/phabfive/pull/24) - Enable RTD build and docs updates
* [#18](https://github.com/dynamist/phabfive/pull/18) - Add code coverage to tox
* [#17](https://github.com/dynamist/phabfive/pull/17) - Proper flake8 with Black
* [#4](https://github.com/dynamist/phabfive/pull/4) - Add encrypted notification config to .travis.yml
* [#2](https://github.com/dynamist/phabfive/pull/2) - Black-linting
* [#1](https://github.com/dynamist/phabfive/pull/1) - Added travis
