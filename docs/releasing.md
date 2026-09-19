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

## Automated Release (Recommended)

Starting with v0.6.0, releases are automated via GitHub Actions using PyPI trusted publishing (OIDC).

### One-Time Setup

#### 1. Configure PyPI Trusted Publisher

On [pypi.org](https://pypi.org):

1. Go to **Manage** > **phabfive** > **Publishing**
2. Add trusted publisher with:
   - **Owner**: `dynamist`
   - **Repository**: `phabfive`
   - **Workflow**: `release.yml`
   - **Environment**: `pypi`

#### 2. Create GitHub Environment

In repository **Settings** > **Environments**:

1. Create environment named `pypi`
2. (Optional) Add protection rules requiring reviewer approval

#### 3. Make the Container Image Public

The first release pushes `ghcr.io/dynamist/phabfive` as a private package. In the
organization's **Packages** > **phabfive** > **Package settings**, change the
visibility to public.

### Release Steps

**1. Update Version**

Update the version in `pyproject.toml`:

```toml
[project]
name = "phabfive"
version = "0.6.0"  # ← update this
```

**2. Update CHANGELOG**

Add release notes to `CHANGELOG.md`.

**3. Commit and Push**

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "Release v0.6.0"
git push origin main
```

**4. Create and Push Tag**

```bash
git tag -a v0.6.0 -m "Release v0.6.0"
git push origin v0.6.0
```

This triggers the GitHub Actions workflow which will:

1. Check that the tag matches the version in `pyproject.toml`
2. Build the Python package (wheel and sdist)
3. Build standalone executables for 6 platforms:
   - Linux (AMD64, ARM64)
   - macOS (AMD64, ARM64)
   - Windows (AMD64, ARM64)
4. Sign executables with [Sigstore](https://www.sigstore.dev/) (keyless OIDC signing)
5. Build, push and sign the scratch container image `ghcr.io/dynamist/phabfive` for
   `linux/amd64` and `linux/arm64`, tagged `X.Y.Z`, `X.Y` and `latest` (glibc) and
   `X.Y.Z-musl`, `X.Y-musl` and `latest-musl` (musl)
6. Publish to PyPI using trusted publishing
7. Create GitHub Release with auto-generated notes and all artifacts

**The tag and `pyproject.toml` have to agree.** Every artifact is named after
`pyproject.toml` while the release is named after the tag, so if the two disagree the
release goes out full of artifacts for another version. That is what happened to
v0.10.0-rc.1, tagged over a `pyproject.toml` that still read `0.10.0-dev.0`: a
prerelease whose wheel, sdist and six executables were all named for the dev version,
with every job green. `scripts/check_version.py` now compares them before anything is
built, and refuses a `dev` version outright -- step 5 below leaves the tree on one, so
a tag pushed before the release bump is the easy mistake. Check it yourself with
`python3 scripts/check_version.py v0.11.0`.

**Every artifact is run before it goes anywhere.** `scripts/smoke.py` executes each
standalone executable before it is signed, the wheel and sdist after installing them
into a clean venv with plain `pip`, and the image tree inside the Dockerfile's `test`
stage. Publishing to PyPI and creating the release both depend on those checks
passing, so a build that cannot start stops the release instead of shipping. The two
release smoke runs are also given `--expect-version`, which catches from the other end
what a source-level comparison cannot see: a binary built from the wrong revision.

**Each executable is checked against its own name.** `scripts/check_arch.py` reads the
header of every built binary and refuses one whose architecture is not the one the asset
name promises. v0.10.0 published an arm64 binary as `phabfive-macos-amd64` -- `macos-14`
and `macos-latest` are both arm64 labels, so the row meant for Intel Macs was building on
Apple silicon, and an Intel Mac downloading it got "Bad CPU type in executable". The smoke
tests cannot see this: they run each binary on the machine that built it, where the
architecture is native whatever the runner turned out to be.

The macOS rows are pinned to `macos-15-intel` (x86_64) and `macos-15` (arm64) rather than
`macos-14` and `macos-latest`. Note that `macos-15-intel` is the last x86_64 image GitHub
will offer: it goes away in August 2027, and after that a macOS Intel binary cannot be
built on Actions at all.

This exists because v0.10.0-rc.1 shipped six executables that could not start at all
and every job still reported success: phabfive imported `click` without declaring it,
and nothing in the pipeline ever ran what it built. Run the same checks yourself at
any time with `make smoke`.

**Testing with RC tags:** Tags containing `-rc` (e.g., `v0.7.0-rc.1`) will skip PyPI publishing but still build executables, push the container image (without the `X.Y` and `latest` tags) and create a GitHub Release marked as a prerelease. Useful for testing the release process.

**Verifying signatures:** Users can verify downloaded executables with [cosign](https://docs.sigstore.dev/):

```bash
cosign verify-blob phabfive-linux-amd64 \
  --bundle phabfive-linux-amd64.sigstore.json \
  --certificate-identity-regexp="https://github.com/dynamist/phabfive" \
  --certificate-oidc-issuer="https://token.actions.githubusercontent.com"
```

The container image is verified the same way:

```bash
cosign verify ghcr.io/dynamist/phabfive:0.7.0 \
  --certificate-identity-regexp="https://github.com/dynamist/phabfive" \
  --certificate-oidc-issuer="https://token.actions.githubusercontent.com"
```

**5. Bump to Dev Version**

```bash
# Edit pyproject.toml to "0.7.0-dev.0"
git add pyproject.toml
git commit -m "Bump version to 0.7.0-dev.0"
git push origin main
```

## Manual Release (Legacy)

If you cannot use the automated workflow (e.g., trusted publishing not configured), follow these manual steps.

### Building a Release

#### Prerequisites

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

#### Release Steps

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
make smoke
```

That installs phabfive unlocked into a throwaway venv and runs `scripts/smoke.py`
against it -- the same script the release workflow runs on every artifact, so the
manual check and CI cannot drift apart. To check a specific built distribution
instead:

```bash
python -m venv /tmp/test-env
/tmp/test-env/bin/pip install dist/phabfive-0.5.0-py3-none-any.whl
python scripts/smoke.py --venv /tmp/test-env
rm -rf /tmp/test-env
```

Use plain `pip` rather than `uv pip`: uv applies `uv.lock` and the `exclude-newer`
window, which is what hid the missing `click` declaration from every test run. The
point of this check is to resolve dependencies the way a user installing from PyPI
does.

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

### Additional Resources

- [Python Packaging User Guide](https://packaging.python.org/en/latest/tutorials/packaging-projects/)
- [PyPI Project Page](https://pypi.org/project/phabfive/)
- [GitHub Releases](https://github.com/dynamist/phabfive/releases)

### Troubleshooting

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
