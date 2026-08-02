#!/bin/sh
set -e

# launcher.py's _docker_user_flags() passes HOST_UID/HOST_GID (POSIX hosts
# only) so a bind-mounted /workspace owned by the host user is actually
# writable despite --cap-drop=ALL stripping CAP_DAC_OVERRIDE from this
# container's root user. Chown the auth volume ($AUTH_DIR, baked in by the
# Dockerfile) to match, then drop from root to that UID/GID via setpriv
# before exec'ing the real CLI -- the --cap-add=SETUID/SETGID docker run
# flags are only usable here, at startup, as root; once setpriv crosses uid
# 0 -> non-zero the kernel clears the process's capability sets
# automatically (no keepcaps set), so the CLI itself never runs with
# elevated capabilities. Falls back to plain root exec (old behavior) when
# HOST_UID/HOST_GID aren't set, e.g. non-POSIX hosts.
if [ -n "$HOST_UID" ] && [ -n "$HOST_GID" ]; then
    chown -R "$HOST_UID:$HOST_GID" "$AUTH_DIR"
    exec setpriv --reuid="$HOST_UID" --regid="$HOST_GID" --clear-groups --no-new-privs -- "$@"
fi
exec "$@"
