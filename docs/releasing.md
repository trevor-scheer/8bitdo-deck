# Build and release packages

The project uses setuptools and PyPA `build` for Python distributions, PyInstaller for standalone Linux executables, the existing Decky ZIP packager, and GitHub Actions/CLI for releases. The same tested source revision supplies every package. No custom download installer or network service is required.

## Release contents

Each release contains these assets, using the normalized Python version in distribution filenames:

| Asset | Purpose |
| --- | --- |
| `eightbitdo-updater-VERSION-linux-x86_64.tar.gz` | Standalone Linux executable for x86-64, including Steam Deck. |
| `eightbitdo-updater-VERSION-linux-arm64.tar.gz` | Standalone Linux executable for ARM64. |
| `eightbitdo_updater-VERSION-py3-none-any.whl` | Python package, CLI entrypoints, and packaged host helper. |
| `eightbitdo_updater-VERSION.tar.gz` | Python source distribution. |
| `8bitdo-deck.zip` | Decky frontend and shared backend. |
| `SHA256SUMS` | SHA-256 manifest for the distribution assets. |
| `provenance.jsonl` | GitHub build attestation bundle. |

The binary archives contain `eightbitdo`, binary notices, and the bundled components' license material. They contain Python and the application, not vendor firmware images. Review third-party notices when upgrading the build toolchain; those notices describe the bundled components' licensing.

Both Linux architectures build natively on Ubuntu 22.04 runners, with glibc 2.35 as the binary baseline. Architecture build/smoke success is not hardware-update qualification. macOS, Windows, and musl Linux binaries are not produced by this workflow.

## Build locally

Use a clean virtual environment with the Python version used in [Checks](../.github/workflows/checks.yml). Install the hash-pinned build dependencies:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r requirements-build.txt
```

Build the wheel/source distribution and the executable for the current Linux host architecture:

```sh
python -m build --no-isolation --outdir out/python
python -m PyInstaller --clean --noconfirm --distpath out/binary --workpath out/pyinstaller packaging/eightbitdo.spec
python scripts/smoke_distributions.py --wheel out/python/*.whl
python scripts/smoke_distributions.py --binary out/binary/eightbitdo
```

Use a clean output directory so the wheel glob identifies one wheel. The PyInstaller spec supplies the entrypoint, package resources, and runtime configuration. Building on a newer Linux distribution can raise the binary's glibc requirement; release builds use the fixed CI baseline. See [PyInstaller's spec-file usage](https://pyinstaller.org/en/stable/usage.html) for supported build options.

Build the Decky package with the Node and pnpm versions declared by the repository:

```sh
pnpm install --frozen-lockfile
pnpm run typecheck
pnpm run build
pnpm run test:ui
python scripts/package.py
```

Run the backend tests and distribution smoke checks before publishing. Smoke checks exercise the installed/frozen entrypoints and resources without authorizing firmware writes; they do not replace hardware validation. Do not run firmware-installation commands as release-build checks.

`requirements-build.txt` is generated from the build dependency group in `pyproject.toml`. Regenerate it with:

```sh
uv pip compile --group build --python-version 3.11 --universal --generate-hashes --output-file requirements-build.txt
```

Keep the input constraints and generated hashes in the same review when updating the toolchain. Do not hand-edit package hashes. The backend's runtime dependencies and build dependencies are separate.

## Version and tag conventions

The Python version is defined in `eightbitdo/__init__.py`; the Python build metadata reads it. Decky's `package.json` uses the corresponding SemVer spelling. The release tag uses the Decky spelling prefixed with `v`:

| Release | Python version | Decky version | Tag |
| --- | --- | --- | --- |
| Alpha | `0.2.0a1` | `0.2.0-alpha.1` | `v0.2.0-alpha.1` |
| Beta | `0.2.0b1` | `0.2.0-beta.1` | `v0.2.0-beta.1` |
| Release candidate | `0.2.0rc1` | `0.2.0-rc.1` | `v0.2.0-rc.1` |
| Stable | `0.2.0` | `0.2.0` | `v0.2.0` |

The version check uses `packaging.Version` to compare normalized versions. A mismatched source, Decky package, or release tag stops publication. An application version or stable release label does not broaden the firmware authorization registry or establish a successful hardware update.

## Publish through GitHub Actions

1. Update the Python and Decky versions together. Review any changed API contract, support claims, and third-party notices. Commit the changes and wait for Checks to pass on the intended revision.
2. Create an annotated tag on that reviewed commit and push the tag. Replace `RELEASE_TAG` with its version tag, such as `v0.2.0-alpha.1`:

   ```sh
   git tag -a RELEASE_TAG -m "Release RELEASE_TAG"
   git push origin refs/tags/RELEASE_TAG
   ```

3. Watch [Release](../.github/workflows/release.yml). A `v*` tag triggers the reusable Checks build and version validation before publication. The workflow calculates checksums, records build provenance, uploads the assets to a draft release, and publishes only after those steps succeed. Prerelease tags publish as prereleases.
4. Verify the published asset set and run a download verification against the release. Confirm that the release points to the intended source revision. Do not replace or reuse a published version to hide a later fix; release another version.

The workflow uses GitHub's scoped `GITHUB_TOKEN` and OIDC attestation permissions. It does not require a personal access token or publish to PyPI. Repository Actions policy must permit the declared workflow permissions. A failed build leaves publication incomplete; inspect the failure and any draft before rerunning, rather than manually publishing a partial asset set.

## Verify a release download

Download the desired asset and `SHA256SUMS`, then run `sha256sum --check --ignore-missing SHA256SUMS` in their directory. Keep the filename unchanged so it matches the manifest.

To verify that an asset has a build attestation associated with this repository, use the GitHub CLI:

```sh
gh attestation verify ASSET_FILE --repo trevor-scheer/8bitdo-deck
```

Replace `ASSET_FILE` with the downloaded archive, wheel, source distribution, or plugin ZIP. For a locally downloaded attestation bundle, add `--bundle provenance.jsonl`. GitHub's [attestation verification reference](https://cli.github.com/manual/gh_attestation_verify) describes repository identity, signer-workflow restrictions, and offline verification limits. These checks establish artifact identity/provenance; they do not independently validate the proprietary firmware catalog or hardware safety.
