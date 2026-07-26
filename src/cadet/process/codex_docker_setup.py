import os
import subprocess
import sys

from cadet import config

# Unlike agy (see agy_docker_setup.py's SEEDED_FILES), codex's containerized
# auth story is much simpler: empirically confirmed (2026-07-26/27, real
# `docker run` + real model calls, see docs/ARCHITECTURE.md's "Containerized
# codex execution" section) that `~/.codex/auth.json` alone -- copied as-is
# from the Windows host -- authenticates successfully inside the Linux
# container with zero interactive re-login step. `config.toml` was A/B
# tested and confirmed NOT required for auth+basic exec.
SEEDED_FILES = [
    "auth.json",
]


def seed_volume(volume_name=None, host_codex_dir=None) -> dict:
    """Idempotent: `docker volume create` is a no-op if the volume already
    exists. Always overwrites the seeded files on every call (unlike
    agy_permissions.py's additive merge) -- these are live auth tokens that
    should track the host's current login, not accumulate stale copies."""
    volume_name = volume_name or config.get_codex_auth_volume()
    host_codex_dir = host_codex_dir or os.path.expanduser("~/.codex")

    missing = [f for f in SEEDED_FILES if not os.path.isfile(os.path.join(host_codex_dir, f))]
    if missing:
        raise RuntimeError(
            f"Missing expected host codex auth file(s) under {host_codex_dir}: {missing}. "
            "Log in with the interactive `codex` CLI on the host first."
        )

    subprocess.run(["docker", "volume", "create", volume_name], check=True, capture_output=True, text=True)

    cp_script = "; ".join(
        f"mkdir -p /dst/$(dirname {f}) && cp /src/{f} /dst/{f}" for f in SEEDED_FILES
    )
    result = subprocess.run(
        [
            "docker", "run", "--rm",
            "-v", f"{volume_name}:/dst",
            "-v", f"{host_codex_dir}:/src:ro",
            "alpine", "sh", "-c", cp_script,
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Seeding volume {volume_name!r} failed: {result.stderr.strip()}")

    return {"volume": volume_name, "seeded": list(SEEDED_FILES)}


def check_volume(volume_name=None) -> dict:
    """Reports which of SEEDED_FILES are present in the volume, without writing."""
    volume_name = volume_name or config.get_codex_auth_volume()
    check_script = "; ".join(f"test -f /dst/{f} && echo PRESENT:{f} || echo MISSING:{f}" for f in SEEDED_FILES)
    result = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{volume_name}:/dst:ro", "alpine", "sh", "-c", check_script],
        capture_output=True, text=True,
    )
    present = [line.split(":", 1)[1] for line in result.stdout.splitlines() if line.startswith("PRESENT:")]
    missing = [line.split(":", 1)[1] for line in result.stdout.splitlines() if line.startswith("MISSING:")]
    return {"volume": volume_name, "present": present, "missing": missing}


def main():
    """Console entry point (`cadet-setup-codex-docker`). Manual, one-time (or
    re-run-after-re-authenticating-on-host) setup step -- not run
    automatically by the MCP server. `--check` reports state without
    writing."""
    volume_name = config.get_codex_auth_volume()

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
