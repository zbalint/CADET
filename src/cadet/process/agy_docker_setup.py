import os
import subprocess
import sys

from cadet import config

# Only these 4 files are copied into the containerized agy's dedicated volume —
# deliberately NOT the full ~/.gemini tree (brain/, cache/, conversations/,
# crashes/, history.jsonl, ...), so the container never sees the host's
# interactive session history. See docs/ARCHITECTURE.md's "Containerized agy
# execution" section.
SEEDED_FILES = [
    "oauth_creds.json",
    "google_accounts.json",
    "installation_id",
    "settings.json",  # top-level ~/.gemini/settings.json: security.auth.selectedType
    # marks the account as already-logged-in; without it agy re-triggers the
    # interactive OAuth flow even with a valid oauth_creds.json present
    # (confirmed empirically in the container validation pass).
    "antigravity-cli/settings.json",  # agy's own permissions.allow, separate file
]


def seed_volume(volume_name=None, host_gemini_dir=None) -> dict:
    """Idempotent: `docker volume create` is a no-op if the volume already
    exists. Always overwrites the seeded files on every call (unlike
    agy_permissions.py's additive merge) — these are live auth tokens that
    should track the host's current login, not accumulate stale copies."""
    volume_name = volume_name or config.get_agy_gemini_volume()
    host_gemini_dir = host_gemini_dir or os.path.expanduser("~/.gemini")

    missing = [f for f in SEEDED_FILES if not os.path.isfile(os.path.join(host_gemini_dir, f))]
    if missing:
        raise RuntimeError(
            f"Missing expected host agy auth file(s) under {host_gemini_dir}: {missing}. "
            "Log in with the interactive `agy` CLI on the host first."
        )

    subprocess.run(["docker", "volume", "create", volume_name], check=True, capture_output=True, text=True)

    cp_script = "; ".join(
        f"mkdir -p /dst/$(dirname {f}) && cp /src/{f} /dst/{f}" for f in SEEDED_FILES
    )
    result = subprocess.run(
        [
            "docker", "run", "--rm",
            "-v", f"{volume_name}:/dst",
            "-v", f"{host_gemini_dir}:/src:ro",
            "alpine", "sh", "-c", cp_script,
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Seeding volume {volume_name!r} failed: {result.stderr.strip()}")

    return {"volume": volume_name, "seeded": list(SEEDED_FILES)}


def check_volume(volume_name=None) -> dict:
    """Reports which of SEEDED_FILES are present in the volume, without writing."""
    volume_name = volume_name or config.get_agy_gemini_volume()
    check_script = "; ".join(f"test -f /dst/{f} && echo PRESENT:{f} || echo MISSING:{f}" for f in SEEDED_FILES)
    result = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{volume_name}:/dst:ro", "alpine", "sh", "-c", check_script],
        capture_output=True, text=True,
    )
    present = [line.split(":", 1)[1] for line in result.stdout.splitlines() if line.startswith("PRESENT:")]
    missing = [line.split(":", 1)[1] for line in result.stdout.splitlines() if line.startswith("MISSING:")]
    return {"volume": volume_name, "present": present, "missing": missing}


def main():
    """Console entry point (`cadet-setup-agy-docker`). Manual, one-time (or
    re-run-after-re-authenticating-on-host) setup step — not run automatically
    by the MCP server. `--check` reports state without writing."""
    volume_name = config.get_agy_gemini_volume()

    if "--check" in sys.argv:
        result = check_volume(volume_name)
        print(f"Volume {result['volume']!r}: {len(result['present'])}/{len(SEEDED_FILES)} files present.")
        for f in result["missing"]:
            print(f"  missing: {f}")
        sys.exit(1 if result["missing"] else 0)

    result = seed_volume(volume_name)
    print(f"Seeded {result['volume']!r} with {len(result['seeded'])} file(s):")
    for f in result["seeded"]:
        print(f"  + {f}")


if __name__ == "__main__":
    main()
