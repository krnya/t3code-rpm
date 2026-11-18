#!/usr/bin/env python3
"""Create RPM sources from a verified stable upstream Git tag."""
import argparse
import base64
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.request

from prepare_electron import prepare_electron

UPSTREAM = "https://github.com/pingdotgg/t3code.git"
VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "t3code-rpm"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode()


def checksum(url, filename):
    entries = re.findall(rf"^([0-9a-f]{{64}}) [ *]{re.escape(filename)}$", fetch(url), re.M)
    if len(entries) != 1:
        raise ValueError(f"No unique official SHA-256 for {filename}")
    return entries[0]


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])


def prepare(plan, topdir, source_repo):
    version, commit, tag, revision = (plan[k] for k in ("version", "commit", "tag", "revision"))
    if (
        not re.fullmatch(VERSION, version)
        or tag != f"v{version}"
        or not re.fullmatch(r"[0-9a-f]{40}", commit)
        or type(revision) is not int or revision < 1
    ):
        raise ValueError("Invalid release plan")

    sources, specs = topdir / "SOURCES", topdir / "SPECS"
    sources.mkdir(parents=True, exist_ok=True)
    specs.mkdir(parents=True, exist_ok=True)
    if source_repo is None:
        source_repo = topdir / "upstream.git"
        source_repo.mkdir(exist_ok=True)
        git(source_repo, "init", "--bare", "--quiet")
        git(source_repo, "fetch", "--quiet", "--depth=1", UPSTREAM, f"refs/tags/{tag}")
        resolved = git(source_repo, "rev-parse", "FETCH_HEAD^{commit}")
    else:
        resolved = git(source_repo, "rev-parse", f"refs/tags/{tag}^{{commit}}")
    if resolved.decode().strip() != commit:
        raise ValueError(f"{tag} moved since release selection")

    epoch = git(source_repo, "show", "-s", "--format=%ct", commit).decode().strip()
    workspace = json.loads(git(source_repo, "show", f"{commit}:package.json"))
    desktop = json.loads(git(source_repo, "show", f"{commit}:apps/desktop/package.json"))
    node = workspace["engines"]["node"]
    pnpm = workspace["packageManager"].removeprefix("pnpm@")
    electron = desktop["dependencies"]["electron"]
    if not all(re.fullmatch(VERSION, v) for v in (node, pnpm, electron)):
        raise ValueError("Upstream Node, pnpm or Electron version is unpinned")

    archive = sources / f"t3code-{commit}.tar.gz"
    with archive.open("wb") as file, subprocess.Popen(
        ["git", "-C", str(source_repo), "archive", "--format=tar", f"--prefix=t3code-{commit}/", commit],
        stdout=subprocess.PIPE,
    ) as proc:
        with gzip.GzipFile(filename="", mode="wb", fileobj=file, mtime=0) as out:
            shutil.copyfileobj(proc.stdout, out)
        if proc.wait():
            raise RuntimeError("git archive failed")
    with archive.open("rb") as data:
        source_sha = hashlib.file_digest(data, "sha256").hexdigest()

    registry = "https://registry.npmjs.org"
    pnpm_package = "@pnpm/exe.linux-x64"
    dist = json.loads(fetch(f"{registry}/@pnpm%2Fexe.linux-x64/{pnpm}"))["dist"]
    pnpm_url = f"{registry}/{pnpm_package}/-/exe.linux-x64-{pnpm}.tgz"
    algorithm, _, encoded = dist["integrity"].partition("-")
    pnpm_digest = base64.b64decode(encoded, validate=True)
    if dist["tarball"] != pnpm_url or algorithm != "sha512" or len(pnpm_digest) != 64:
        raise ValueError("Unexpected pnpm distribution metadata")
    toolchain = {
        "node_version": node,
        "node_sha256": checksum(
            f"https://nodejs.org/dist/v{node}/SHASUMS256.txt", f"node-v{node}-linux-x64.tar.xz"
        ),
        "pnpm_version": pnpm,
        "pnpm_url": pnpm_url,
        "pnpm_sha512": pnpm_digest.hex(),
        **prepare_electron(electron, sources, topdir / "download-cache", fetch),
    }
    root = Path(__file__).resolve().parent.parent
    for name in ("t3code", "t3code.desktop"):
        shutil.copyfile(root / "packaging" / name, sources / name)
    for name in ("build.sh", "prepare-workspace.py"):
        shutil.copyfile(root / "scripts" / name, sources / name)
    for name, data in (
        ("toolchain.json", toolchain),
        ("upstream.json", {**plan, "source_date_epoch": int(epoch), "source_sha256": source_sha}),
    ):
        (sources / name).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")

    spec = (root / "packaging" / "t3code.spec").read_text()
    for key, value in {
        "VERSION": version, "REVISION": str(revision), "COMMIT": commit,
        "SOURCE_SHA": source_sha, "SOURCE_DATE_EPOCH": epoch,
        "ELECTRON_RPM_SHA": toolchain["electron_rpm_sha256"],
        "FFMPEG_SOURCE_SHA": toolchain["ffmpeg_source_sha256"],
    }.items():
        spec = spec.replace(f"@{key}@", value)
    if re.search(r"@[A-Z_]+@", spec):
        raise ValueError("Unexpanded RPM spec template variable")
    (specs / "t3code.spec").write_text(spec)
    (sources / "desktop-pnpm-lock.yaml").touch()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("topdir", type=Path)
    parser.add_argument("--source-repo", type=Path)
    args = parser.parse_args()
    try:
        prepare(json.loads(args.plan.read_text()), args.topdir.resolve(), args.source_repo)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        sys.exit(str(error))
