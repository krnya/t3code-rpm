#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 || $# -gt 4 ]]; then
    echo 'Usage: test-package.sh RPM VERSION RELEASE_NUMBER [PREVIOUS_RPM]' >&2
    exit 2
fi
package=$(realpath "$1")
version=$2
revision=$3
previous=${4:-}
app=/usr/lib64/t3code
desktop=/usr/share/applications/t3code.desktop
expected="t3code-$version-$revision.fc45.x86_64"
[[ $(id -u) == 0 && $(rpm --eval '%{?fedora}') == 45 && $(uname -m) == x86_64 ]]
[[ "$(rpm -qp --qf '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}' "$package")" == "$expected" ]]
rpm -K --nosignature "$package"
rpm -qpl "$package"
# Only the test container installs the locally built unsigned artifact.
install_rpm() {
    dnf -y --setopt=localpkg_gpgcheck=0 --setopt=install_weak_deps=false --setopt=tsflags= install "$1"
}
if [[ -n "$previous" ]]; then
    install_rpm "$(realpath "$previous")"
    rpm -q t3code
    dnf -y --setopt=localpkg_gpgcheck=0 --setopt=install_weak_deps=false --setopt=tsflags= upgrade "$package"
else
    install_rpm "$package"
fi

[[ $(rpm -q t3code) == "$expected" ]]
rpm -ql t3code
rpm -V t3code
desktop-file-validate "$desktop"
test -x /usr/bin/t3code
test -x "$app/t3code"
test -s /usr/share/icons/hicolor/512x512/apps/t3code.png
test -s /usr/share/metainfo/com.t3tools.t3code.metainfo.xml
test -s /usr/share/licenses/t3code/LICENSE
test -s /usr/share/licenses/t3code/LICENSES.chromium.html
test -s /usr/share/licenses/t3code/FFmpeg-NOTICE
[[ $(stat -c '%u:%g %a' "$app/chrome-sandbox") == '0:0 4755' ]]
[[ $(find "$app" -type f -perm /6000 -print) == "$app/chrome-sandbox" ]]
test -z "$(find "$app" -type f -perm /022 -print -quit)"
if grep -En -- '(/(home/runner|github/workspace|work/|tmp/)|--no-sandbox|--disable-setuid-sandbox)' /usr/bin/t3code "$desktop"; then
    echo 'The launcher or desktop file refers to a build path or disables sandboxing.' >&2
    exit 1
fi
find "$app" -type l -print0 | while IFS= read -r -d '' entry; do
    target=$(realpath -e "$entry")
    [[ $target == "$app/"* ]]
done
for executable in "$app/t3code" "$app"/resources/{resource-monitor,hyprland-capture,kde-capture,browser-secret}/t3-*; do
    test -x "$executable"
    libraries=$(ldd "$executable")
    if [[ $libraries == *'not found'* ]]; then
        printf '%s\n' "$libraries" >&2
        exit 1
    fi
done
python3 - "$app/resources/app.asar" "$version" "$revision" <<'PY'
# An ASAR archive starts with two Chromium Pickle records and a JSON directory.
import hashlib
import json
from pathlib import Path
import struct
import sys

asar, version, revision = sys.argv[1:]
with open(asar, "rb") as archive:
    prefix_size, header_size, _, json_size = struct.unpack("<4I", archive.read(16))
    assert prefix_size == 4 and 0 < json_size <= header_size < 32 * 1024 * 1024
    entries = json.loads(archive.read(json_size))["files"]
    entry = entries["package.json"]
    archive.seek(8 + header_size + int(entry["offset"]))
    manifest = json.loads(archive.read(entry["size"]))
assert manifest["version"] == version, manifest["version"]
modules = entries["node_modules"]["files"]
for scope, sdk in (("@cursor", "sdk"), ("@anthropic-ai", "claude-agent-sdk")):
    assert not any(
        name == sdk or name.startswith(sdk + "-")
        for name in modules.get(scope, {}).get("files", {})
    ), f"Unexpected restricted SDK in the app: {scope}/{sdk}"
toolchain = json.loads(Path("/usr/share/doc/t3code/toolchain.json").read_text())
assert toolchain["copr_profile"] == 1
with Path(asar).parent.parent.joinpath("libffmpeg.so").open("rb") as ffmpeg:
    assert hashlib.file_digest(ffmpeg, "sha256").hexdigest() == toolchain["ffmpeg_library_sha256"]
provenance = json.loads(Path("/usr/share/doc/t3code/upstream.json").read_text())
assert provenance["version"] == version and provenance["revision"] == int(revision)
PY

testhome=$(mktemp -d)
if ! id t3code-smoke >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /bin/bash t3code-smoke
fi
chown t3code-smoke "$testhome"
trap 'rm -rf -- "$testhome"' EXIT
runuser -u t3code-smoke -- env HOME="$testhome" T3CODE_HOME="$testhome/state" \
    XDG_RUNTIME_DIR="$testhome" T3CODE_PORT=39477 dbus-run-session -- xvfb-run -a bash -s <<'SMOKE'
set -euo pipefail
/usr/bin/t3code --ozone-platform=x11 --disable-gpu > "$HOME/app.log" 2>&1 &
app_pid=$!
cleanup() {
    status=$?
    if (( status != 0 )); then cat "$HOME/app.log" >&2; fi
    kill "$app_pid" 2>/dev/null || true
    wait "$app_pid" 2>/dev/null || true
}
trap cleanup EXIT
for ((attempt = 0; attempt < 60; attempt++)); do
    kill -0 "$app_pid"
    if xdotool search --onlyvisible --name 'T3 Code' >/dev/null 2>&1 &&
       (: < "/dev/tcp/127.0.0.1/$T3CODE_PORT") 2>/dev/null; then
        for renderer in $(pgrep -u "$(id -u)" -f 't3code --type=renderer'); do
            grep -Eq '^Seccomp:[[:space:]]+2$' "/proc/$renderer/status"
            grep -Eq '^NoNewPrivs:[[:space:]]+1$' "/proc/$renderer/status"
            echo 'T3 Code started its local backend and opened a sandboxed X11 window.'
            exit 0
        done
    fi
    sleep 1
done
echo 'T3 Code did not start both its backend and a sandboxed window.' >&2
exit 1
SMOKE

dnf -y remove t3code
if rpm -q t3code; then
    echo 'The package remains installed after removal.' >&2
    exit 1
fi
test ! -e /usr/bin/t3code
test ! -e "$desktop"
test ! -e "$app"
