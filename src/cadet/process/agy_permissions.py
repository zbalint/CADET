import json
import os
import sys

from cadet import config

# Scoped to CADET's own documented use cases only: read-only git inspection and
# running a delegated repo's test suite. Every entry needs BOTH a command() and an
# unsandboxed() pair, and both must be exact literal strings — agy's permissions.allow
# matching was empirically found to be literal-only (no wildcard/regex support despite
# vendor docs claiming otherwise), and on Windows every command() grant also needs a
# matching unsandboxed() grant because agy's --sandbox (AppContainer) denies C:\ access
# broadly, which blocks python.exe/git.exe outright. See SALTMDB memory 59766596 /
# 2aa99df6 / e84141c4 for the empirical findings this list is built from.
CURATED_ALLOWLIST = [
    "command(git status)",
    "unsandboxed(git status)",
    "command(git log)",
    "unsandboxed(git log)",
    "command(git show)",
    "unsandboxed(git show)",
    "command(git fetch && git status)",
    "unsandboxed(git fetch && git status)",
    "command(pytest tests/)",
    "unsandboxed(pytest tests/)",
    "command(python -m pytest tests/)",
    "unsandboxed(python -m pytest tests/)",
    "command(python -m unittest discover -s tests)",
    "unsandboxed(python -m unittest discover -s tests)",
]


def load_curated_allowlist() -> list[str]:
    return list(CURATED_ALLOWLIST)


def merge_allowlist(settings_path=None, curated=None) -> dict:
    """Additive-only merge into agy's settings.json: never removes or overwrites an
    existing entry (curated or user-added), preserves every other top-level key
    untouched. Tolerant of a missing file (fresh agy install)."""
    path = settings_path or config.get_agy_settings_path()
    curated = curated if curated is not None else load_curated_allowlist()

    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            settings = json.load(f)
    else:
        settings = {}
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    permissions = settings.setdefault("permissions", {})
    existing_allow = permissions.setdefault("allow", [])

    added = []
    already_present = []
    for rule in curated:
        if rule in existing_allow:
            already_present.append(rule)
        else:
            existing_allow.append(rule)
            added.append(rule)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")

    return {"added": added, "already_present": already_present}


def main():
    """Console entry point (`cadet-install-agy-permissions`). Manual, one-time,
    idempotent setup step — not run automatically by the MCP server, since silently
    mutating a file outside the repo on every server start is more invasive than
    warranted. `--check` reports drift without writing."""
    path = config.get_agy_settings_path()
    curated = load_curated_allowlist()

    if "--check" in sys.argv:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                existing_allow = json.load(f).get("permissions", {}).get("allow", [])
        else:
            existing_allow = []
        missing = [rule for rule in curated if rule not in existing_allow]
        if missing:
            print(f"{len(missing)} curated rule(s) missing from {path}:")
            for rule in missing:
                print(f"  {rule}")
            sys.exit(1)
        print(f"All {len(curated)} curated rules already present in {path}.")
        return

    result = merge_allowlist(path, curated)
    print(f"Merged curated agy permissions into {path}")
    print(f"  added ({len(result['added'])}):")
    for rule in result["added"]:
        print(f"    + {rule}")
    print(f"  already present ({len(result['already_present'])})")


if __name__ == "__main__":
    main()
