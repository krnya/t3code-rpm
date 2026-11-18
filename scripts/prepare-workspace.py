#!/usr/bin/env python3
"""Remove restricted SDKs from the workspace before pnpm can fetch them."""

import json
from os.path import relpath
from pathlib import Path
import re
import sys

import yaml

RESTRICTED_SDK = re.compile(
    r"(?:@cursor/sdk|@anthropic-ai/claude-agent-sdk)(?=$|[-@/>])"
)

UNAVAILABLE_CLAUDE = """// Fedora build without the separately licensed Agent SDK.
function unavailable(): never {
  throw new Error("Claude Agent SDK is unavailable in this Fedora build.");
}
export const query = unavailable;
export const resolveSettings = unavailable;
export const forkSession = unavailable;
export const getSessionMessages = unavailable;
export const getSubagentMessages = unavailable;
"""


def remove_restricted(value):
    if isinstance(value, dict):
        return {
            key: remove_restricted(item)
            for key, item in value.items()
            if not isinstance(key, str) or not RESTRICTED_SDK.search(key)
        }
    if isinstance(value, list):
        return [
            remove_restricted(item)
            for item in value
            if not isinstance(item, str) or not RESTRICTED_SDK.search(item)
        ]
    if isinstance(value, str) and RESTRICTED_SDK.search(value):
        raise ValueError(f"Restricted SDK remains in a dependency value: {value}")
    return value


def prepare(root):
    config = root / "pnpm-workspace.yaml"
    workspace = remove_restricted(yaml.safe_load(config.read_text()))
    for pattern in workspace["packages"]:
        for directory in root.glob(pattern):
            manifest = directory / "package.json"
            if not manifest.is_file():
                continue
            before = json.loads(manifest.read_text())
            after = remove_restricted(before)
            if before != after:
                manifest.write_text(json.dumps(after, indent=2) + "\n")
    config.write_text(yaml.safe_dump(workspace, sort_keys=False))
    lock = root / "pnpm-lock.yaml"
    text = lock.read_text()
    sections = re.split(r"(?m)^---[ \t]*\r?\n", text)
    workspace_lock = sections[-1]
    rewritten = yaml.safe_dump(remove_restricted(yaml.safe_load(workspace_lock)), sort_keys=False)
    # pnpm 12 puts the package-manager lock in a separate leading document.
    if any(RESTRICTED_SDK.search(section) for section in sections[:-1]):
        raise ValueError("Restricted input in pnpm's package-manager lock")
    sections[-1] = rewritten
    lock.write_text("---\n".join(sections))

    # Claude's SDK imports run at startup. Keep its adapter loadable and fail
    # explicitly at the point a user requests an SDK-dependent operation.
    source = root / "apps/server/src"
    shim = source / "provider/DisabledClaudeSdk.ts"
    shim.write_text(UNAVAILABLE_CLAUDE)
    imports = 0
    for path in source.rglob("*.ts"):
        text = path.read_text()
        if 'from "@anthropic-ai/claude-agent-sdk"' not in text:
            continue
        relative = relpath(shim, path.parent)
        if not relative.startswith("."):
            relative = "./" + relative
        path.write_text(text.replace(
            'from "@anthropic-ai/claude-agent-sdk"', f'from "{relative}"'
        ))
        imports += 1
    if imports == 0:
        raise ValueError("The upstream Claude SDK import layout changed")


if __name__ == "__main__":
    try:
        prepare(Path(sys.argv[1]).resolve())
    except (OSError, KeyError, ValueError, yaml.YAMLError) as error:
        sys.exit(f"Could not prepare the COPR workspace: {error}")
