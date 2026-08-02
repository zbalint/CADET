import unittest
from unittest.mock import patch

from cadet.process.launcher import docker_user_flags


class TestDockerUserFlags(unittest.TestCase):
    """docker_user_flags() is what fixes the --cap-drop=ALL bug where root
    inside a containerized provider can't write bind-mounted files owned by
    the host user (CAP_DAC_OVERRIDE stripped). entrypoint.sh reads
    HOST_UID/HOST_GID and drops from root to that UID/GID via setpriv,
    which itself needs CAP_CHOWN (to chown the auth volume) and
    CAP_SETUID/CAP_SETGID (to actually change UID/GID) re-added on top of
    --cap-drop=ALL."""

    def test_posix_host_returns_cap_adds_and_host_ids(self):
        with patch("os.getuid", return_value=1000, create=True), \
             patch("os.getgid", return_value=1000, create=True):
            flags = docker_user_flags()
        self.assertEqual(
            flags,
            ["--cap-add=CHOWN", "--cap-add=SETUID", "--cap-add=SETGID",
             "-e", "HOST_UID=1000", "-e", "HOST_GID=1000"],
        )

    def test_reflects_real_host_uid_gid(self):
        with patch("os.getuid", return_value=42, create=True), \
             patch("os.getgid", return_value=99, create=True):
            flags = docker_user_flags()
        self.assertIn("-e", flags)
        self.assertIn("HOST_UID=42", flags)
        self.assertIn("HOST_GID=99", flags)

    def test_non_posix_host_returns_empty(self):
        """Windows hosts (docker.exe, no os.getuid) get the old (root,
        no-drop) container behavior unchanged -- Docker Desktop's WSL2-
        backend bind mounts don't hit the CAP_DAC_OVERRIDE bug this exists
        to fix."""
        with patch("cadet.process.launcher.hasattr", return_value=False):
            flags = docker_user_flags()
        self.assertEqual(flags, [])
