import subprocess
import sys

from cadet import config

# Unlike agy_docker_setup.py/codex_docker_setup.py (a host credential file is
# copied as-is), cursor's Linux OAuth-into-a-volume flow is the closer
# template here -- but UNCONFIRMED for copilot (see providers/copilot.py's
# module docstring): `copilot login --help` documents that the token is
# normally stored in the OS credential store, falling back to a plain-text
# file under ~/.copilot/ only "if a credential store is not found" --
# expected (not yet live-tested) to be the case inside this minimal
# container. No exact filename is known yet, so check_volume below checks
# for *any* file under the mount rather than a fixed SEEDED_FILES list the
# way codex_docker_setup.py does.
#
# CADET_COPILOT_GITHUB_TOKEN (config.get_copilot_github_token(), forwarded by
# providers/copilot.py's build_docker_argv) is the simpler alternative to
# this whole login flow -- a fine-grained PAT with the "Copilot Requests"
# permission, or a `gh` CLI OAuth token, needs no interactive step at all.
# Prefer that if available; use this login flow only if a token isn't handy.


def login(volume_name=None, image=None) -> dict:
    """Runs a one-time OAuth login against the auth volume, mirroring
    cursor_docker_setup.py's login(). Streams the login subprocess's own
    stdout/stderr directly to the console (not captured) so the user sees
    the printed device-flow URL/code to complete in a browser. Blocks until
    the flow completes or fails.

    UNCONFIRMED: no NO_OPEN_BROWSER-equivalent env var was found in
    `copilot login --help`/`copilot help environment` the way cursor's OAuth
    flow documents one -- this container has no browser to launch, so if
    `copilot login` attempts to auto-open one internally, that attempt is
    expected to just silently fail while the flow still proceeds via the
    printed URL, but this has not been live-tested."""
    volume_name = volume_name or config.get_copilot_auth_volume()
    image = image or config.get_copilot_docker_image()

    subprocess.run(["docker", "volume", "create", volume_name], check=True, capture_output=True, text=True)

    result = subprocess.run([
        "docker", "run", "--rm",
        "-v", f"{volume_name}:/root/.copilot",
        image, "copilot", "login",
    ])
    if result.returncode != 0:
        raise RuntimeError(f"copilot login against volume {volume_name!r} failed (exit {result.returncode}).")
    return {"volume": volume_name}


def check_volume(volume_name=None) -> dict:
    """Reports whether the volume currently holds any file at all, via a
    throwaway alpine container -- unlike cursor's check_volume (a real
    `agent status` call against a known subcommand), copilot has no
    confirmed status/whoami subcommand, and the exact credential filename
    isn't known yet, so this is a coarser "is it empty or not" signal only."""
    volume_name = volume_name or config.get_copilot_auth_volume()
    result = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{volume_name}:/root/.copilot:ro", "alpine",
         "sh", "-c", "find /root/.copilot -type f 2>/dev/null"],
        capture_output=True, text=True,
    )
    files = [line for line in result.stdout.splitlines() if line.strip()]
    return {"volume": volume_name, "files": files, "logged_in": bool(files)}


def main():
    """Console entry point (`cadet-setup-copilot-docker`). Manual, one-time
    (or re-run-after-logging-out) setup step -- not run automatically by the
    MCP server. `--check` reports current state without starting a new login
    flow."""
    volume_name = config.get_copilot_auth_volume()

    if "--check" in sys.argv:
        result = check_volume(volume_name)
        print(f"Volume {result['volume']!r}: {'has' if result['logged_in'] else 'NO'} credential file(s).")
        for f in result["files"]:
            print(f"  {f}")
        sys.exit(0 if result["logged_in"] else 1)

    print(f"Starting copilot OAuth login against volume {volume_name!r}...")
    login(volume_name)
    print("Login complete.")


if __name__ == "__main__":
    main()
