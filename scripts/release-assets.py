#!/usr/bin/env python3
"""Stage verified artifacts in a draft and publish after COPR promotion."""
import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tarfile

MARKER = "<!-- t3code-rpm:"
BUNDLE = "verified-build.tar"
FFMPEG_SOURCE = "ffmpeg-corresponding-source.tar.gz"
STATE_KEYS = ("version", "revision", "tag", "commit", "upstream_release_id", "published_at", "run_id", "previous")


def gh(*args, data=None, stdout=subprocess.PIPE):
    return subprocess.run(
        ["gh", *args], input=json.dumps(data).encode() if data is not None else None,
        stdout=stdout, check=True,
    ).stdout


def digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def save_asset(repo, item, path):
    asset = next((asset for asset in item["assets"] if asset["name"] == path.name), None)
    if asset:
        if asset["state"] == "uploaded":
            if asset["digest"] != "sha256:" + digest(path):
                raise ValueError(f"Existing release asset differs: {path.name}")
            return
        # An interrupted GitHub upload can leave a zero-byte starter asset.
        if asset["state"] != "starter" or asset["size"] != 0:
            raise ValueError(f"Unexpected upload state for {path.name}: {asset['state']}")
        gh("api", "--method", "DELETE", f"repos/{repo}/releases/assets/{asset['id']}")
    gh("release", "upload", item["tag_name"], str(path), "--repo", repo, stdout=None)


def metadata(item):
    first = (item.get("body") or "").partition("\n")[0]
    if not first.startswith(MARKER) or not first.endswith(" -->"):
        raise ValueError("Release is missing its publication metadata")
    return json.loads(first[len(MARKER):-4])


def body(plan, details):
    return f"{MARKER}{json.dumps(plan, separators=(',', ':'))} -->\n\n{details}"


def draft(repo, tag, plan, allow_create):
    base = f"repos/{repo}/releases"
    # List with write permissions so drafts are visible even before a tag exists.
    pages = json.loads(gh("api", "--paginate", "--slurp", f"{base}?per_page=100"))
    items = [item for page in pages for item in page if item["tag_name"] == tag]
    if len(items) > 1:
        raise ValueError(f"More than one GitHub release for {tag}")
    if not items:
        if not allow_create:
            raise ValueError("Expected an existing draft release")
        return json.loads(gh("api", "--method", "POST", base, "--input", "-", data={
            "tag_name": tag, "name": f"T3 Code {plan['version']} (RPM revision {plan['revision']})",
            "draft": True, "target_commitish": os.environ["GITHUB_SHA"],
            "body": body(plan, "Fedora 45 x86_64 verification in progress."),
        }))
    item = items[0]
    recorded = metadata(item)
    if any(recorded.get(key) != plan.get(key) for key in STATE_KEYS):
        raise ValueError("Release draft metadata differs from the build plan")
    return item


def verify(directory):
    subprocess.run(["sha256sum", "--check", "--strict", "SHA256SUMS"], cwd=directory, check=True)


def verify_ffmpeg_source(directory):
    toolchain = json.loads((directory / "toolchain.json").read_text())
    if toolchain.get("copr_profile") != 1:
        raise ValueError("This bundle predates the COPR dependency and FFmpeg changes")
    if digest(directory / FFMPEG_SOURCE) != toolchain["ffmpeg_source_sha256"]:
        raise ValueError("FFmpeg corresponding source does not match the build provenance")


def stage(repo, item, plan, input_dir, verified):
    verified.mkdir()
    asset = next((asset for asset in item["assets"] if asset["name"] == BUNDLE
                  and asset["state"] == "uploaded"), None)
    bundle = verified.parent / BUNDLE
    if asset:
        with bundle.open("wb") as output:
            gh("api", "-H", "Accept: application/octet-stream",
               f"repos/{repo}/releases/assets/{asset['id']}", stdout=output)
        if asset["digest"] != "sha256:" + digest(bundle):
            raise ValueError("Downloaded draft bundle differs from GitHub's asset digest")
    else:
        verify(input_dir)
        verify_ffmpeg_source(input_dir)
        with tarfile.open(bundle, "w") as archive:
            for entry in sorted(input_dir.iterdir()):
                if not entry.is_file() or entry.is_symlink():
                    raise ValueError(f"Unexpected build artifact: {entry.name}")
                archive.add(entry, arcname=entry.name)
        save_asset(repo, item, bundle)
    with tarfile.open(bundle) as archive:
        if any(member.name != Path(member.name).name or not member.isfile() for member in archive):
            raise ValueError("Unexpected member in the verified build bundle")
        archive.extractall(verified, filter="data")
    verify(verified)
    verify_ffmpeg_source(verified)
    built = json.loads((verified / "upstream.json").read_text())
    if any(built.get(key) != plan.get(key) for key in STATE_KEYS):
        raise ValueError("Verified build provenance does not match the release")


def publish(repo, item, plan, verified, output):
    evr = f"{plan['version']}-{plan['revision']}.fc45"
    names = [
        f"t3code-{evr}.x86_64.rpm", f"t3code-{evr}.src.rpm",
        "build-provenance.json", "copr-build.json", FFMPEG_SOURCE,
    ]
    for name in (f"t3code-{evr}.src.rpm", "build-provenance.json", FFMPEG_SOURCE):
        shutil.copyfile(verified / name, output / name)
    (output / "SHA256SUMS").write_text("".join(f"{digest(output / name)}  {name}\n" for name in names))
    for name in [*names, "SHA256SUMS"]:
        save_asset(repo, item, output / name)
    copr = json.loads((output / "copr-build.json").read_text())
    note = (
        f"Fedora 45 x86_64 · T3 Code {plan['version']} · RPM revision {plan['revision']}\n\n"
        f"Upstream commit: `{plan['commit']}`\n\n"
        f"[Verified COPR build](https://copr.fedorainfracloud.org/coprs/build/{int(copr['id'])}/)\n\n"
        "Use COPR for DNF updates. The x86_64 RPM attached here is signed by the COPR project.\n\n"
        "The matching source for Electron's LGPL FFmpeg library is included in "
        "`ffmpeg-corresponding-source.tar.gz` and in the SRPM."
    )
    published = {**plan, "published_run_id": os.environ["GITHUB_RUN_ID"]}
    gh("api", "--method", "PATCH", f"repos/{repo}/releases/{item['id']}", "--input", "-",
       data={"draft": False, "body": body(published, note), "make_latest": "legacy"}, stdout=None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("stage", "publish"))
    parser.add_argument("plan", type=Path)
    parser.add_argument("verified", type=Path)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    tag = f"rpm-v{plan['version']}-{plan['revision']}"
    plan = {key: plan[key] for key in STATE_KEYS}
    repo = os.environ["GITHUB_REPOSITORY"]
    item = draft(repo, tag, plan, allow_create=args.mode == "stage")
    if not item["draft"]:
        raise ValueError(f"Release {tag} is already published")
    if args.mode == "stage":
        stage(repo, item, plan, args.directory, args.verified)
    else:
        publish(repo, item, plan, args.verified, args.directory)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        sys.exit(str(error))
