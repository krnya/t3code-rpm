#!/usr/bin/env bash
set -euo pipefail
if [[ $# != 7 ]]; then
    echo 'Usage: build.sh SOURCE_DIR OUTPUT_DIR UPSTREAM_JSON TOOLCHAIN_JSON DESKTOP_LOCK WORKSPACE_PREP ELECTRON_ZIP' >&2
    exit 2
fi
source_dir=$(realpath "$1")
output=$(realpath -m "$2")
upstream=$(realpath "$3")
toolchain=$(realpath "$4")
desktop_lock=$(realpath "$5")
workspace_prep=$(realpath "$6")
electron_zip=$(realpath "$7")
cd "$source_dir"
mkdir -p "$output" "$output/tools"
# The upstream closure check needs a temp directory outside the source tree.
tmp=$(mktemp -d "${TMPDIR:-/var/tmp}/t3code-rpm.XXXXXXXX")
trap 'rm -rf -- "$tmp"' EXIT
export TMPDIR="$tmp"

json() {
    python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$1" "$2"
}

download() {
    local url=$1 path=$2 checksum=$3 program=$4
    if ! echo "$checksum  $path" | "$program" --check --status; then
        curl --fail --silent --show-error --location --retry 3 --proto '=https' \
            --proto-redir '=https' --output "$path.partial" "$url"
        echo "$checksum  $path.partial" | "$program" --check --strict
        mv "$path.partial" "$path"
    fi
}

version=$(json "$upstream" version)
node_version=$(json "$toolchain" node_version)
pnpm_version=$(json "$toolchain" pnpm_version)
tools="$output/tools"
node_archive="node-v$node_version-linux-x64.tar.xz"
download "https://nodejs.org/dist/v$node_version/$node_archive" "$tools/$node_archive" \
    "$(json "$toolchain" node_sha256)" sha256sum
download "$(json "$toolchain" pnpm_url)" "$tools/pnpm.tgz" "$(json "$toolchain" pnpm_sha512)" sha512sum
mkdir -p "$tools/node" "$tools/pnpm"
tar -xJf "$tools/$node_archive" -C "$tools/node" --strip-components=1
tar -xzf "$tools/pnpm.tgz" -C "$tools/pnpm" --strip-components=1
install -m 0755 "$tools/pnpm/pnpm" "$tools/node/bin/pnpm"
export PATH="$tools/node/bin:$PATH"
export CI=true
export CARGO_BUILD_JOBS=${T3_BUILD_JOBS:-4}
export CARGO_INCREMENTAL=0
export pnpm_config_store_dir="${T3_PNPM_STORE:-$output/pnpm-store}"
# node-gyp should only use headers for the selected Electron version for
# native rebuilds. Generic workspace builds can use the verified Node headers.
export npm_config_nodedir="$tools/node"
test "$(node --version)" = "v$node_version"
test "$(pnpm --version)" = "$pnpm_version"
python3 "$workspace_prep" "$source_dir"
pnpm install --frozen-lockfile --filter @t3tools/monorepo --filter '@t3tools/scripts...' \
    --filter '@t3tools/desktop...' --filter 't3...' --filter '@t3tools/web...'
unset npm_config_nodedir
export PATH="$source_dir/node_modules/.bin:$PATH"
export T3_RPM_ELECTRON_DIST="$electron_zip"
export T3_RPM_BUILD_COMMIT="$(json "$upstream" commit)"
export T3_RPM_GLIBC="$(getconf GNU_LIBC_VERSION | cut -d ' ' -f 2)"
export T3_RPM_DESKTOP_LOCK="$desktop_lock"
export T3_RPM_STAGE_LOCK_OUT="$output/desktop-pnpm-lock.yaml"

# Use upstream's staging, native rebuilds and validation. Supply the verified
# Electron zip, reproduce the staged dependency graph, and carry SHA metadata
# when building from an extracted source archive.
python3 - <<'PY'
from pathlib import Path

path = Path("scripts/build-desktop-artifact.ts")
script = path.read_text()
changes = [
    (
        '  const commitHash = yield* resolveGitCommitHash(repoRoot);',
        '  const commitHash = process.env.T3_RPM_BUILD_COMMIT?.slice(0, 12) ?? '
        '(yield* resolveGitCommitHash(repoRoot));',
    ),
    (
        '    buildConfig.linux = {',
        '    buildConfig.electronDist = process.env.T3_RPM_ELECTRON_DIST;\n'
        '    buildConfig.linux = {',
    ),
    (
        '  const stagePackageJsonString = yield* encodeJsonString(stagePackageJson);',
        '  delete stagePackageJson.dependencies["@cursor/sdk"];\n'
        '  for (const name of Object.keys(stagePatchedDependencies)) {\n'
        '    if (name.startsWith("@cursor/sdk@")) delete stagePatchedDependencies[name];\n'
        '  }\n'
        '  const stagePackageJsonString = yield* encodeJsonString(stagePackageJson);',
    ),
    (
        '  yield* Effect.log("[desktop-artifact] Installing staged production dependencies...");\n'
        '  const installCommand = yield* resolveSpawnCommand("vp", [...STAGE_INSTALL_ARGS]);',
        """
  yield* Effect.log("[desktop-artifact] Installing staged production dependencies...");
  const stagedLock = path.join(stageAppDir, "pnpm-lock.yaml");
  const pin = process.env.T3_RPM_DESKTOP_LOCK!;
  const seeded = (yield* fs.readFileString(pin)).trim().length > 0;
  if (seeded) yield* fs.copyFile(pin, stagedLock);
  const installCommand = yield* resolveSpawnCommand(
    "vp", [...STAGE_INSTALL_ARGS, ...(seeded ? ["--frozen-lockfile"] : ["--no-frozen-lockfile"])],
  );""".strip("\n"),
    ),
    (
        '  yield* stageClerkPasskeyNativeBinaries(stageAppDir, options.platform, options.arch);',
        '  yield* fs.copyFile(stagedLock, process.env.T3_RPM_STAGE_LOCK_OUT!);\n'
        '  yield* stageClerkPasskeyNativeBinaries(stageAppDir, options.platform, options.arch);',
    ),
]
for before, after in changes:
    if script.count(before) != 1:
        raise SystemExit(f"Upstream desktop builder changed. Expected one match for {before!r}")
    script = script.replace(before, after)
path.write_text(script)

path = Path("scripts/linux-glibc.ts")
script = path.read_text()
import re
script, count = re.subn(
    r'const GLIBC_BUILD_CEILING = "[0-9.]+";',
    'const GLIBC_BUILD_CEILING = process.env.T3_RPM_GLIBC!;',
    script,
)
if count != 1:
    raise SystemExit("Upstream glibc ceiling implementation changed")
path.write_text(script)
PY

# Populate the upstream Electron npm package for scripts that call require("electron").
electron_dir=$(realpath apps/desktop/node_modules/electron)
mkdir -p "$electron_dir/dist"
unzip -qo "$T3_RPM_ELECTRON_DIST" -d "$electron_dir/dist"
chmod 0755 "$electron_dir/dist/electron"
printf electron > "$electron_dir/path.txt"

vp run dist:desktop:artifact --platform linux --target dir --arch x64 \
    --build-version "$version" --output-dir "$output" --verbose

echo "$(json "$toolchain" ffmpeg_library_sha256)  $output/linux-unpacked/libffmpeg.so" | sha256sum --check --strict
test -x "$output/linux-unpacked/t3code"
test -f "$output/linux-unpacked/chrome-sandbox"
test -s "$output/linux-unpacked/resources/app.asar"
# Only glibc variants are used on Fedora. Do not pull a musl runtime into the RPM.
find "$output/linux-unpacked/resources/app.asar.unpacked" -depth -type d \
    \( -name '*-linux-x64-musl' -o -name 'musl_x64' -o -name 'linux-arm64' \) -exec rm -r -- {} +
test -z "$(find "$output/linux-unpacked" -name 'cursorsandbox' -print -quit)"
test -s "$output/desktop-pnpm-lock.yaml"
icon_source=$(sed -n 's/.*productionLinuxIconPng: "\([^"]*\)".*/\1/p' scripts/lib/brand-assets.ts)
test -f "$icon_source"
mkdir -p "$output/icons"
for size in 16 32 48 64 128 256 512; do
    magick "$icon_source" -resize "${size}x${size}" "$output/icons/${size}x${size}.png"
done
