#!/usr/bin/env bash
set -euo pipefail
if [[ $# != 3 ]]; then
    echo 'Usage: package.sh RPM_TOPDIR ARTIFACT_DIRECTORY PACKAGING_COMMIT' >&2
    exit 2
fi
topdir=$(realpath "$1")
out=$(realpath -m "$2")
packaging_commit=$3
buildargs=(--define "_topdir $topdir" "$topdir/SPECS/t3code.spec")
sources="$topdir/SOURCES"
lock="$sources/desktop-pnpm-lock.yaml"
[[ "$(rpm --eval '%{?fedora}') $(uname -m)" == '45 x86_64' ]]
mkdir -p "$out"
test -z "$(find "$out" -mindepth 1 -maxdepth 1 -print -quit)"
rpmspec --parse "${buildargs[@]}" > "$out/t3code.spec.expanded"
rpmbuild -bb "${buildargs[@]}"
mapfile -t packages < <(find "$topdir/RPMS/x86_64" -maxdepth 1 -name 't3code-*.rpm')
[[ ${#packages[@]} == 1 ]]
rpmfile=${packages[0]}
# Preserve the exact staged graph from the verified binary build for COPR.
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
rpm2archive - < "$rpmfile" | tar -xz -C "$scratch" ./usr/share/doc/t3code/desktop-pnpm-lock.yaml
cp "$scratch/usr/share/doc/t3code/desktop-pnpm-lock.yaml" "$lock"
test -s "$lock"
rpmbuild -bs "${buildargs[@]}"
mapfile -t srpms < <(find "$topdir/SRPMS" -maxdepth 1 -name 't3code-*.src.rpm')
[[ ${#srpms[@]} == 1 ]]
cp "$rpmfile" "${srpms[0]}" "$out/"
cp "$sources"/{upstream.json,toolchain.json,desktop-pnpm-lock.yaml,ffmpeg-corresponding-source.tar.gz} "$out/"
python3 - "$out" "$packaging_commit" <<'PY'
import json
from pathlib import Path
import subprocess
import sys

out = Path(sys.argv[1])
build_packages = subprocess.check_output(
    ["rpm", "-qa", "--qf", "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n"], text=True
)
(out / "build-provenance.json").write_text(json.dumps({
    "packaging_commit": sys.argv[2],
    "target": "fedora-45-x86_64",
    "upstream": json.loads((out / "upstream.json").read_text()),
    "toolchain": json.loads((out / "toolchain.json").read_text()),
    "build_environment_rpms": sorted(build_packages.splitlines()),
}, indent=2) + "\n")
PY
(cd "$out" && sha256sum -- *.rpm *.json desktop-pnpm-lock.yaml ffmpeg-corresponding-source.tar.gz > SHA256SUMS)
