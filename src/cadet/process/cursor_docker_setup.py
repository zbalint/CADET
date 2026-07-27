import subprocess
import sys

from cadet import config

# Unlike agy_docker_setup.py/codex_docker_setup.py, there is no host file to
# copy here -- cursor-agent's Linux build stores its real session credential
# (auth.json) under the XDG config dir ~/.config/cursor/, a completely
# different location than the Windows install's ~/.cursor/ (confirmed
# empirically via a full-home-directory capture during a real login). No
# cross-platform credential copy is possible, same "different OS, different
# credential file" situation agy's Phase 2 hit -- a one-time interactive
# OAuth login directly against the container is required instead.


def login(volume_name=None, image=None) -> dict:
    """Runs a one-time OAuth login against the auth volume, mirroring agy's
    Phase 2 interactive-login requirement. Streams the login subprocess's own
    stdout/stderr directly to the console (not captured) so the user sees the
    printed `cursor.com/loginDeepControl?...` URL to open in a browser --
    NO_OPEN_BROWSER=1 disables the CLI's own (container-side, therefore
    useless) attempt to launch a browser itself. Blocks until the browser
    flow completes. No `-it`/TTY is actually required here (confirmed
    empirically -- a plain non-interactive subprocess still receives and
    reports the completed login), unlike agy's documented `-it` requirement."""
    volume_name = volume_name or config.get_cursor_auth_volume()
    image = image or config.get_cursor_docker_image()

    subprocess.run(["docker", "volume", "create", volume_name], check=True, capture_output=True, text=True)

    result = subprocess.run([
        "docker", "run", "--rm",
        "-v", f"{volume_name}:/root/.config/cursor",
        "-e", "NO_OPEN_BROWSER=1",
        image, "agent", "login",
    ])
    if result.returncode != 0:
        raise RuntimeError(f"cursor-agent login against volume {volume_name!r} failed (exit {result.returncode}).")
    return {"volume": volume_name}


def check_volume(volume_name=None, image=None) -> dict:
    """Reports whether the volume currently holds valid credentials via a
    real `agent status` call. Unlike agy_docker_setup.py/codex_docker_setup.py's
    check_volume (which only checks file presence via a throwaway alpine
    container), cursor's own status command is a more reliable signal --
    token *validity*, not just file existence, is what actually matters, and
    running it requires the real image (alpine has no `agent` binary)."""
    volume_name = volume_name or config.get_cursor_auth_volume()
    image = image or config.get_cursor_docker_image()
    result = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{volume_name}:/root/.config/cursor", image, "agent", "status"],
        capture_output=True, text=True,
    )
    logged_in = result.returncode == 0 and "Not logged in" not in result.stdout
    return {"volume": volume_name, "logged_in": logged_in, "output": result.stdout.strip()}


def main():
    """Console entry point (`cadet-setup-cursor-docker`). Manual, one-time (or
    re-run-after-logging-out) setup step -- not run automatically by the MCP
    server. `--check` reports current login status without starting a new
    login flow."""
    volume_name = config.get_cursor_auth_volume()

    if "--check" in sys.argv:
        result = check_volume(volume_name)
        print(f"Volume {result['volume']!r}: {'logged in' if result['logged_in'] else 'NOT logged in'}")
        if result["output"]:
            print(result["output"])
        sys.exit(0 if result["logged_in"] else 1)

    print(f"Starting cursor-agent OAuth login against volume {volume_name!r}...")
    login(volume_name)
    print("Login complete.")


if __name__ == "__main__":
    main()
