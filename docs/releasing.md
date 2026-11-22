# Build and release packages

The project uses setuptools/PyPA `build` for Python distributions, PyInstaller for bundled backends, Vite/React and Tauri for desktop applications, and tufup/TUF for signed application updates. GitHub Actions builds the native packages and GitHub CLI publishes releases. Decky uses its existing ZIP packager.

## Release contents

The native matrix contains Linux x86-64/ARM64, macOS Intel/Apple Silicon, and Windows x86-64. Asset names use the normalized Python version, such as `0.3.0a1`, and architecture names `x86_64` or `aarch64`:

| Asset pattern | Purpose |
| --- | --- |
| `8BitDo-Updater_VERSION_linux-ARCH.AppImage` | Linux desktop app. |
| `8BitDo-Updater_VERSION_macos-ARCH.dmg` | macOS desktop installer containing the complete `.app`. |
| `8BitDo-Updater_VERSION_windows-x86_64-setup.exe` | Per-user Windows desktop installer. |
| `8BitDo-Updater_VERSION_windows-x86_64-portable.zip` | Complete Windows portable desktop directory. |
| `eightbitdo-updater-VERSION-OS-ARCH.tar.gz` | Standalone CLI archive for Linux/macOS. |
| `eightbitdo-updater-VERSION-windows-x86_64.zip` | Standalone Windows CLI archive. |
| `eightbitdo_updater-VERSION-py3-none-any.whl` and `eightbitdo_updater-VERSION.tar.gz` | Python wheel and source distribution. |
| `8bitdo-deck.zip` | Decky frontend and shared backend. |
| `eightbitdo-cli-OS-ARCH-VERSION.tar.gz` and `eightbitdo-desktop-OS-ARCH-VERSION.tar.gz` | Signed-update target archives; clients install these through tufup. |
| `SHA256SUMS` and `provenance.jsonl` | Distribution checksums and GitHub build-attestation bundle. |

`OS` is `linux`, `macos`, or `windows`. There are five CLI archives, six desktop downloads, and ten tufup target archives. Update targets contain an application unit and its manifest; they are not additional manual installers.

Linux builds use native Ubuntu 22.04 runners, targeting glibc 2.35 or later. macOS builds target macOS 11 or later on separate Intel and Apple Silicon runners; Windows uses its x86-64 runner. See [Checks](../.github/workflows/checks.yml) for current runner labels and tool versions. This matrix establishes build/smoke coverage, not device or firmware qualification. macOS and Windows firmware writes and recovery remain disabled.

Application archives include the bundled backend/runtime and notices, not vendor firmware. Current macOS and Windows packages have no Apple Developer ID/notarization or Windows Authenticode signature. TUF signing and GitHub attestations authenticate update content/provenance independently; they do not provide OS publisher identity or suppress launch prompts.

## Build locally

Use the Python, Node, Rust, and pnpm versions declared by the workflow/repository. Install Tauri's [host build prerequisites](https://v2.tauri.app/start/prerequisites/). Linux also needs `xvfb` for packaged GUI smoke checks. Commands below use a POSIX shell; on Windows, use Git Bash or the corresponding PowerShell commands and `.exe` paths.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r requirements-build.txt
pnpm install --frozen-lockfile
python -m unittest discover -s tests -v
pnpm run typecheck
pnpm run build
pnpm run test:ui
pnpm exec playwright install chromium
pnpm run test:desktop
```

Build and check the Python distributions and bundled backend:

```sh
python -m build --no-isolation --outdir out/python
python scripts/smoke_distributions.py --wheel out/python/*.whl
python -m PyInstaller --clean --noconfirm --distpath out/binary --workpath out/pyinstaller packaging/eightbitdo.spec
python scripts/smoke_distributions.py --binary out/binary/eightbitdo
python scripts/package_distributions.py prepare
```

Use `out/binary/eightbitdo.exe` on Windows. Keep output directories clean so a wheel glob selects one file. `prepare` collects Python/native-runtime, Rust dependency, and bundled JavaScript notices, archives the CLI, and stages the backend in `src-tauri/binaries/` with Tauri's required target-triple suffix.

Build the desktop for the current host. Replace `BUNDLE` with `appimage` on Linux, `app,dmg` on macOS, or `nsis` on Windows:

```sh
pnpm exec tauri build --bundles BUNDLE
cargo test --locked --manifest-path src-tauri/Cargo.toml
python scripts/package_distributions.py desktop
python scripts/smoke_desktop.py
python scripts/package.py
```

On macOS, request both `app,dmg`: the `.app` must remain available for smoke checks and signed update packaging after the DMG is built. Tauri runs the desktop Vite build, then bundles the UI and sidecar. Native packages are assembled under `out/release/`; `out/update-inputs/` contains application units for the signing job. The final command creates `out/8bitdo-deck.zip` from the previously built Decky frontend.

For UI development, `pnpm run dev:desktop` opens a browser preview without hardware access. After staging a backend, `pnpm exec tauri dev` runs the desktop bridge. Browser tests supply fixtures only in the test harness; the shipped app does not fabricate connected devices.

`--smoke-test` on the desktop is a fixed, read-only mode: no webview is created, and only sidecar `api info` runs. It checks API and normalized application versions before exiting. The package smoke script launches the assembled app, using extraction and Xvfb on Linux. Tests and builds never authorize firmware writes.

The lockfile includes runtime dependencies and the build group from `pyproject.toml`. Regenerate it after reviewing toolchain changes:

```sh
uv pip compile pyproject.toml --group build --python-version 3.11 --universal --generate-hashes --output-file requirements-build.txt
```

Keep constraints, hashes, `pnpm-lock.yaml`, `src-tauri/Cargo.lock`, and relevant bundled-component notices current together. Building on a newer Linux host can raise the binary ABI requirement; the release workflow uses its fixed baseline. See [Tauri's AppImage guidance](https://v2.tauri.app/distribute/appimage/).

## Version and tag conventions

Update these version sources together:

- `eightbitdo/__init__.py`: Python/PEP 440 version, read by Python build metadata.
- `package.json`, `src-tauri/tauri.conf.json`, and `src-tauri/Cargo.toml`: equivalent SemVer spelling.
- `src-tauri/Cargo.lock`: the desktop package's matching version.

| Release | Python version | Desktop/Decky version | Tag |
| --- | --- | --- | --- |
| Alpha | `0.3.0a1` | `0.3.0-alpha.1` | `v0.3.0-alpha.1` |
| Beta | `0.3.0b1` | `0.3.0-beta.1` | `v0.3.0-beta.1` |
| Release candidate | `0.3.0rc1` | `0.3.0-rc.1` | `v0.3.0-rc.1` |
| Stable | `0.3.0` | `0.3.0` | `v0.3.0` |

`python scripts/check_version.py --tag TAG` compares source and tag versions using `packaging.Version`. A mismatch stops publication. A stable app version does not add firmware authorizations or establish hardware qualification.

## Signing infrastructure

The application bundles a public TUF root and repository configuration. Public metadata lives on the `updates` branch, separately for each product/OS/architecture. The `EIGHTBITDO_TUF_SIGNING_KEYS` repository secret supplies the online targets, snapshot, and timestamp private keys plus public role keys. The private root key stays local and outside CI, reserved for controlled root maintenance.

[Release](../.github/workflows/release.yml) uses `scripts/publish_updates.py prepare` to add signed targets for all ten repositories. The [daily refresh workflow](../.github/workflows/refresh-updates.yml) renews online metadata without the private root key. Release and refresh jobs share a concurrency group to avoid overwriting metadata history. Keep the workflow enabled and investigate failures before metadata expires. Root expiry/rotation remains a separate maintainer task; see [application-update trust management](self-updates.md#refresh-metadata-and-rotate-keys).

Do not regenerate a trust root for an ordinary release. Existing installations depend on its rotation chain. Never publish private keys or commit local key directories. TUF infrastructure must be provisioned before a release can publish app updates; a fork needs its own reviewed trust root, repository configuration, metadata histories, and online-key secret.

## Publish through GitHub Actions

1. Update the versions and write reviewed release notes in `docs/releases/VERSION.md`, using the SemVer spelling without `v` (for example, `docs/releases/0.3.0-alpha.1.md`). Review support claims, API changes, artwork mappings, and notices. The release workflow requires this file and uses the same text for GitHub release notes and signed TUF update notes. Wait for Checks to pass on the intended source revision.
2. Tag that commit and push the tag. Replace `RELEASE_TAG` with its version, such as `v0.3.0-alpha.1`:

   ```sh
   git tag -a RELEASE_TAG -m "Release RELEASE_TAG"
   git push origin refs/tags/RELEASE_TAG
   ```

3. Watch Release. It reruns the native/frontend builds, validates versions, prepares signed app-update targets, checks asset completeness, computes checksums, and records provenance. It uploads assets to a draft release and publishes the release before publishing metadata that points clients at those downloads. Prerelease tags publish as prereleases.
4. Verify the release assets, source revision, attestations, and metadata publication. Clients must be able to download the targets referenced by signed metadata. Do not reuse a published version for a later fix.

The workflow uses scoped `GITHUB_TOKEN` and OIDC attestation permissions, plus the online TUF key secret. It does not need a personal access token or publish to PyPI. A failure before release publication must not be bypassed by manually publishing an incomplete draft.

If the release publishes but metadata publication fails, recover the prepared public metadata from the retained workflow artifact and reconcile it with the current `updates` branch under the same publication lock. Preserve numbered roots and metadata versions. Do not rebuild different bytes under the published version or point metadata at missing downloads.

## Verify a release download

Download the desired asset and `SHA256SUMS`. On Linux, run `sha256sum --check --ignore-missing SHA256SUMS` in their directory. On macOS, compare `shasum -a 256 ASSET_FILE` with the corresponding entry; in PowerShell, use `Get-FileHash .\ASSET_FILE -Algorithm SHA256`.

Use GitHub CLI to verify the repository's attestation:

```sh
gh attestation verify ASSET_FILE --repo trevor-scheer/8bitdo-deck
```

Replace `ASSET_FILE` with the downloaded asset. Add `--bundle provenance.jsonl` to use the downloaded attestation bundle. GitHub's [verification reference](https://cli.github.com/manual/gh_attestation_verify) explains signer identity and offline limits. Checksums, attestations, TUF signatures, OS signing, and firmware qualification answer different questions; none substitutes for the others.
