#!/usr/bin/env python3
"""Resolve the next stable upstream version and allocate its RPM release."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

UPSTREAM = "pingdotgg/t3code"
FLOOR = (0, 1, 10)
VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
STABLE = re.compile(rf"v({VERSION})")
DOWNSTREAM = re.compile(rf"rpm-v({VERSION})-([1-9][0-9]*)")
MARKER = "<!-- t3code-rpm:"
BUNDLE = "verified-build.tar"


def api(path):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "t3code-rpm",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token := os.environ.get("GH_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request("https://api.github.com" + path, headers=headers)
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.load(response), response.headers.get("Link", "")


def get(path):
    return api(path)[0]


def releases(repo):
    page = 1
    while True:
        items, links = api(f"/repos/{repo}/releases?per_page=100&page={page}")
        yield from items
        if 'rel="next"' not in links:
            return
        page += 1


def version_tuple(version):
    return tuple(map(int, version.split(".")))


def stable_version(release):
    tag = STABLE.fullmatch(release["tag_name"])
    if tag and not release["draft"] and not release["prerelease"] and release["published_at"]:
        return tag[1]
    return None


def downstream_state(items):
    by_version = {}
    for item in items:
        match = DOWNSTREAM.fullmatch(item["tag_name"])
        if not match:
            continue
        first_line = (item.get("body") or "").partition("\n")[0]
        if not first_line.startswith(MARKER) or not first_line.endswith(" -->"):
            raise ValueError(f"Release {item['tag_name']} is missing its provenance marker")
        plan = json.loads(first_line[len(MARKER):-4])
        version, revision = match[1], int(match[2])
        if plan["version"] != version or plan["revision"] != revision:
            raise ValueError(f"Release metadata conflicts with tag {item['tag_name']}")
        by_version.setdefault(version, []).append((item, plan))
    return by_version


def next_revision(version, history, force, run_id):
    records = sorted(history.get(version, []), key=lambda record: record[1]["revision"])
    if not records:
        return 1, None
    item, plan = records[-1]
    if item["draft"]:
        return plan["revision"], (item, plan)
    if not force or (run_id and run_id in (plan.get("run_id"), plan.get("published_run_id"))):
        return None
    return plan["revision"] + 1, None


def candidates(version, manual):
    if version or manual:
        path = f"tags/v{version}" if version else "latest"
        release = get(f"/repos/{UPSTREAM}/releases/{path}")
        if not stable_version(release):
            raise ValueError(f"{release['tag_name']} is not a published stable release")
        return [release]
    found = []
    for release in releases(UPSTREAM):
        if not (stable := stable_version(release)):
            continue
        if version_tuple(stable) < FLOOR:
            break
        found.append(release)
    return sorted(found, key=lambda item: version_tuple(stable_version(item)))


def plan_release(repo, version, manual=False, force=False):
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    history = downstream_state(releases(repo)) if repo else {}
    active = [(item, plan) for records in history.values() for item, plan in records if item["draft"]]
    if len(active) > 1:
        raise ValueError("Multiple unpublished RPM releases need reconciliation")
    if active:
        pending = active[0][1]["version"]
        if version and version != pending:
            raise ValueError(f"Finish the pending {pending} publication before starting another version")
        version = pending

    for release in candidates(version, manual):
        stable = stable_version(release)
        selected = next_revision(stable, history, force, run_id)
        if selected is None:
            continue
        revision, reservation = selected
        commit = get(f"/repos/{UPSTREAM}/commits/{release['tag_name']}")["sha"]
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("Upstream API returned an invalid commit SHA")
        if any(record["commit"] != commit or record["upstream_release_id"] != release["id"]
               for _, record in history.get(stable, [])):
            raise ValueError(f"Previously recorded upstream tag {release['tag_name']} changed")
        published = [
            (version_tuple(v), entry["revision"], item["tag_name"])
            for v, records in history.items()
            for item, entry in records
            if not item["draft"] and (version_tuple(v), entry["revision"]) < (version_tuple(stable), revision)
        ]
        previous = max(published)[2] if published else ""
        if reservation:
            draft, plan = reservation
            assets = [asset for asset in draft["assets"]
                      if asset["name"] == BUNDLE and asset["state"] == "uploaded"]
            if len(assets) > 1:
                raise ValueError("Draft contains multiple verified bundles")
            return {**plan, "resume": bool(assets), "draft_id": draft["id"]}
        return {
            "version": stable, "tag": release["tag_name"], "commit": commit, "revision": revision,
            "upstream_release_id": release["id"], "published_at": release["published_at"],
            "run_id": run_id, "resume": False, "previous": previous,
        }
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--version", default="")
    parser.add_argument("--manual", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    version = args.version.removeprefix("v")
    if args.version and not re.fullmatch(VERSION, version):
        parser.error("version must be X.Y.Z or vX.Y.Z")
    if args.repo and not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        parser.error("invalid repository")
    try:
        result = plan_release(args.repo, version, args.manual, args.force)
    except (OSError, ValueError, KeyError) as error:
        sys.exit(f"Release detection failed: {error}")
    if result:
        print(json.dumps(result, sort_keys=True))
    else:
        print("All tracked stable versions are published.")
    if args.output:
        with args.output.open("a") as output:
            print(f"build={'true' if result else 'false'}", file=output)
            if result:
                print(f"resume={str(result['resume']).lower()}", file=output)
                print(f"plan={json.dumps(result, separators=(',', ':'))}", file=output)


if __name__ == "__main__":
    main()
