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

## Releasing

Releases are automated via GitHub Actions using PyPI trusted publishing (OIDC), and have
been since v0.6.0. There is no manual path: a hand-rolled upload was documented here
until 0.11.0, and its instructions had drifted far enough from the workflow that its
step calling TestPyPI mandatory read as current process while an `-rc` tag published
nowhere at all (#509). Git history has it if it is ever wanted.

### One-Time Setup

#### 1. Configure PyPI Trusted Publisher

On [pypi.org](https://pypi.org):

1. Go to **Manage** > **phabfive** > **Publishing**
2. Add trusted publisher with:
   - **Owner**: `dynamist`
   - **Repository**: `phabfive`
   - **Workflow**: `release.yml`
   - **Environment**: `pypi`

#### 2. Configure TestPyPI Trusted Publisher

A release candidate publishes to TestPyPI and nowhere else, so this is what makes an
`-rc` tag installable. On [test.pypi.org](https://test.pypi.org), the same way:

1. Go to **Manage** > **phabfive** > **Publishing**
2. Add trusted publisher with:
   - **Owner**: `dynamist`
   - **Repository**: `phabfive`
   - **Workflow**: `release.yml`
   - **Environment**: `testpypi`

#### 3. Create GitHub Environments

In repository **Settings** > **Environments**:

1. Create environments named `pypi` and `testpypi`
2. (Optional) Add protection rules requiring reviewer approval

#### 4. Make the Container Image Public

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

A tag containing `-rc` is a release candidate: it publishes to **TestPyPI** rather than
PyPI, and skips the `X.Y` and `latest` image tags, but builds the six executables,
pushes the image and creates a GitHub release marked as a prerelease. Install one the
way anyone else would, which is the half of a release a local build cannot check:

```bash
pip install --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ 'phabfive==0.11.0rc1'
```

The extra index is not optional: phabfive's dependencies are not on TestPyPI, so
resolution fails without somewhere real to find them.

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

## Additional Resources

- [Python Packaging User Guide](https://packaging.python.org/en/latest/tutorials/packaging-projects/)
- [PyPI Project Page](https://pypi.org/project/phabfive/)
- [TestPyPI Project Page](https://test.pypi.org/project/phabfive/)
- [GitHub Releases](https://github.com/dynamist/phabfive/releases)

## Troubleshooting

**`check-version` fails.** The tag and `pyproject.toml` disagree, or the version is a
`dev` one. The message names both. Move the tag rather than editing the version to match
it - every artifact is named after `pyproject.toml`, so a tag bent to fit publishes a
release full of artifacts for another version, which is what happened to `v0.10.0-rc.1`.

**A publish job fails with a permissions or OIDC error.** The trusted publisher is not
configured, or its environment name does not match. `pypi` and `testpypi` are separate
publishers on separate sites and both have to exist; see the one-time setup above.

**The version already exists.** Neither PyPI nor TestPyPI lets a version be replaced, and
deleting one does not free the filename. Cut the next candidate - `-rc.2` - rather than
trying to reuse a number. This is the reason to cut candidates at all.

**The build fails before anything is published.** `scripts/smoke.py` runs every artifact
before it goes anywhere, so a build that cannot start stops the release instead of
shipping. Reproduce it locally with `uv run python scripts/smoke.py --venv .venv`.
