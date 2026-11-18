# Build and release architecture

## Upstream build

The package follows [pingdotgg/t3code](https://github.com/pingdotgg/t3code). Stable `v0.1.10` resolves to commit `7d32075e3757ded707914e2450c3e392a9665765`. Its workspace pins Node 26.13.1, pnpm 12.14.0, Vite+ 1.4.0, Electron 44.10.1, and Electron Builder 26.16.1. At this tag some upstream package manifests still carry the prior version, so the build passes the stable release version explicitly.

The release resolver selects a stable release using GitHub's API. Source preparation fetches that tag, verifies it resolves to the API's full commit SHA, and archives the Git tree. The generated spec checks the archive's SHA-256. Node and the two Electron release archives use upstream checksum manifests. The native pnpm binary uses the npm registry's SHA-512 integrity. The build removes the Cursor SDK and Claude Agent SDK and their platform variants from the manifests and lock before the frozen workspace install. Native builds use upstream Cargo locks.

Source preparation verifies Electron's standard runtime and alternate FFmpeg archive, then replaces `libffmpeg.so` before creating the SRPM. The preparation script checks the exact Electron source tag's Chromium branding and disabled proprietary codecs in [its FFmpeg build settings](https://github.com/electron/electron/blob/v44.10.1/build/args/ffmpeg.gn). COPR uses this verified, prepared runtime from the SRPM. The same SRPM and the GitHub release carry `ffmpeg-corresponding-source.tar.gz`. That archive contains the exact Chromium FFmpeg tree, matching Electron sources with patches and build settings, and Chromium's dependency manifest. The installed `FFmpeg-NOTICE` identifies those revisions.

The Linux build invokes upstream's `dist:desktop:artifact --platform linux --target dir --arch x64`. Vite+ builds the desktop process, local server, and web assets. Rust builds the resource monitor and KDE and Hyprland capture helpers. A C helper links against Fedora's libsecret. Electron Builder assembles the unpacked application and upstream checks that its server bundle loads in isolation. Packaging adapts upstream's Ubuntu glibc ceiling to the build host's glibc version while retaining the ELF compatibility scan. See the [upstream build documentation](https://github.com/pingdotgg/t3code/blob/v0.1.10/docs/operations/development.md).

## RPM and test

The RPM installs the unpacked application under `/usr/lib64/t3code`, a launcher in `/usr/bin`, an application menu entry, hicolor icons, AppStream metadata, licenses, and build provenance. The launcher disables Electron self-updates so DNF owns package updates. The Chromium setuid sandbox helper is owned by root and has mode 4755. The application runs as the desktop user. RPM generates system ELF dependencies and the package keeps bundled ELF libraries private.

The CI build resolves the packaging stage's pnpm lock once. It embeds that lock in the built RPM and the SRPM so COPR recreates the exact staged dependency graph. Package verification checks the ASAR manifest, app version, restricted SDK exclusions, replacement FFmpeg digest, dependencies, ownership and modes, desktop integration, DNF installation and removal, and RPM verification. When a prior release is available, CI also exercises a DNF upgrade. The Xvfb test checks for a visible application window and a sandboxed Chromium renderer.

## Distribution and retries

The workflow uploads the validated SRPM and associated build artifacts to a GitHub draft. The workflow rejects draft bundles that predate the SDK and FFmpeg changes. A failed run keeps its draft. A subsequent scheduled or manual run reuses the draft bundle and checks COPR for a matching running or successful build before uploading the SRPM again. A failed COPR attempt can be retried while retaining the same RPM release. The dedicated project's manual repository regeneration mode keeps a successful build out of the public DNF metadata until the workflow checks the project signature and tests the signed candidate.

After the signed build passes, the workflow regenerates the COPR repository if necessary, refreshes DNF metadata, and verifies that the exact signed package is available. It then publishes the GitHub release. Workflow concurrency serializes release selection and publication. Only the default-branch publication workflow receives COPR credentials.

The build uses standard GitHub-hosted runners and needs network access on COPR for npm, crates.io, upstream toolchains, and native build headers. Dependencies are pinned or recorded, but Fedora buildroot updates, timestamps and build paths can affect binary reproducibility. The release checksums identify the exact artifacts made available by each successful run. COPR signs the distributed binary RPM. The architecture uses COPR's SRPM rebuild and signing path.
