# Release Process

This document describes how to build and publish new releases of phabfive to PyPI.

## Version Schema

We follow basic [SemVer](https://semver.org/) versioning with extensions defined by Python in [PEP 440](https://peps.python.org/pep-0440/).

**Version format:** `MAJOR.MINOR.PATCH`

- **MAJOR:** Incompatible API changes
- **MINOR:** New functionality (backwards compatible)
- **PATCH:** Bug fixes (backwards compatible)

**Examples:**

- `0.4.0` - Standard release
- `0.4.1` - Patch release
- `1.0.0` - Major release with breaking changes

PEP 440 also allows for post and dev releases if needed, but in general we should only publish stable regular SemVer releases.

## Building a Release

### Prerequisites

1. **PyPI Account & Permissions**
   - You must be an **owner** or **maintainer** for phabfive on PyPI
   - Currently only Henrik and Tim are owners
   - Ask them for assistance if you need upload permissions

2. **Tools Installed**
```bash
   # Install uv if not already installed
   curl -LsSf https://astral.sh/uv/install.sh | sh

   # Install build dependencies
   uv sync --group dev
```

### Release Steps

**1. Update Version**

Update the version in `pyproject.toml`:

```toml
[project]
name = "phabfive"
version = "0.5.0"  # ← update this
```

**2. Update CHANGELOG**

Add release notes to `CHANGELOG.md` documenting:
- New features
- Bug fixes
- Breaking changes (if any)
- Deprecations (if any)

**3. Commit and tag**

Commit the version bump and CHANGELOG.

**Note:** This is an important step!

```bash
git commit -m"Bump version: 0.5.0rc0 → 0.5.0"
git push origin

git tag -a v0.5.0 -m "Release version 0.5.0"
git push origin v0.5.0
```

**4. Build Distributions**

```bash
# Clean previous builds
make clean

# Build source distribution and wheel
uv build

# Verify build artifacts
ls -lh dist/
# Should see:
# phabfive-0.5.0.tar.gz
# phabfive-0.5.0-py3-none-any.whl
```

**5. Test the Build**

Before uploading anywhere, verify the build works locally to catch issues early:
```bash
# Create test environment
uv venv test-env
source test-env/bin/activate  # or test-env\Scripts\activate on Windows

# Install from wheel
uv pip install dist/phabfive-0.5.0-py3-none-any.whl

# Quick smoke test
phabfive --help

# Cleanup
deactivate
rm -rf test-env
```

**6. Upload to TestPyPI (MANDATORY)**

Before uploading to the main PyPI, you **must** test on TestPyPI to catch any packaging issues:

```bash
# Upload to TestPyPI using uv
export UV_PUBLISH_TOKEN=pypi-ABCDEF # ← token for test.pypi.org -- IMPORTANT
uv publish --publish-url https://test.pypi.org/legacy/
```

**Note:** Get your TestPyPI API token at https://test.pypi.org/manage/account/token/

**7. Test Installation from TestPyPI**

Verify the package installs correctly from TestPyPI:

```bash
# Create clean test environment
uv venv test-pypi-env
source test-pypi-env/bin/activate

# Install from TestPyPI
# --index-strategy unsafe-best-match: Required because uv's default security
#   prevents mixing package versions from different indexes. Since you control
#   both TestPyPI and PyPI for phabfive, this is safe and necessary.
# --extra-index-url: Allows dependencies (like mkdocs) to be installed from PyPI
uv pip install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  --index-strategy unsafe-best-match \
  phabfive

# Verify it works
phabfive --help

# Cleanup
deactivate
rm -rf test-pypi-env
```

If everything works, proceed to production upload.

**8. Upload to PyPI**

```bash
# Upload to production PyPI using uv
export UV_PUBLISH_TOKEN=pypi-ABCDEF # ← token for pypi.org -- IMPORTANT
uv publish
```

**Note:** Get your PyPI API token at https://pypi.org/manage/account/token/

**9. Create GitHub Release**

Go to https://github.com/dynamist/phabfive/releases/new and:

1. Select the tag you just created
2. Title: `v0.5.0`
3. Copy release notes from CHANGELOG
4. Attach build artifacts (optional)
5. Publish release

**10. Bump to dev release**

After you published the version it is equally important to bump the source code to a dev release so that your dev and test environments don't think twice if it is running a released version of phabfive or the latest code.

**Note:** This is an important step!

Update the version in `pyproject.toml`:

```toml
[project]
name = "phabfive"
version = "0.6.0-dev.0"  # ← update this
```

Then commit and push:

```bash
git commit -m"Bump version: 0.5.0 → 0.6.0-dev.0"
git push origin
```

## Additional Resources

- [Python Packaging User Guide](https://packaging.python.org/en/latest/tutorials/packaging-projects/)
- [PyPI Project Page](https://pypi.org/project/phabfive/)
- [GitHub Releases](https://github.com/dynamist/phabfive/releases)

## Troubleshooting

**Build fails:**

- Ensure `pyproject.toml` is valid
- Check that all required files are present
- Run tests first: `uv run pytest`

**Upload fails with authentication error:**

- Verify you have maintainer/owner permissions on PyPI and TestPyPI
- Use API tokens (required for `__token__` username)
- Set up tokens at https://pypi.org/manage/account/token/ and https://test.pypi.org/manage/account/token/
- Contact current owners for assistance

**Version already exists on PyPI:**

- You cannot overwrite existing versions
- Increment version number and rebuild
- Consider using post-releases (e.g., `0.5.0.post1`) for minor fixes
