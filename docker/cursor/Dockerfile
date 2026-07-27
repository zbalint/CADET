# Runs CADET's `cursor` provider fully containerized: a Linux build of the
# Cursor Agent CLI inside an isolated Docker container, replacing native
# Windows subprocess execution (which required a powershell.exe -File
# workaround for cmd.exe's line reparsing -- see providers/cursor.py's
# module docstring). See docs/ARCHITECTURE.md's "Containerized codex
# execution" section for the pattern this mirrors (Phase 4 here).
#
# Deliberately minimal: only what CADET's own curated allow-list actually runs
# (git status/log/show, pytest, unittest). Do NOT add node/npm/build-essential
# speculatively -- extend this image only when a real delegated task needs it.
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl tar git python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Official installer places the binary at ~/.local/bin/agent (root's home
# here, since RUN executes as root) -- NOT "cursor-agent", despite the
# package/product being called "Cursor Agent CLI". Confirmed both via the
# official docs (curl https://cursor.com/install -fsS | bash) and via
# providers/cursor.py's own module docstring, which already flagged this
# naming gotcha for whoever containerized cursor next.
RUN curl https://cursor.com/install -fsS | bash
ENV PATH="/root/.local/bin:${PATH}"
RUN agent --version

# Docker Desktop's WSL2 bind-mount UID presentation for the Windows host's
# /workspace almost never matches this container's UID, which git treats as
# "dubious ownership" and refuses to operate on by default. CADET's curated
# allow-list runs git status/log/show against exactly this mount.
RUN git config --system --add safe.directory /workspace

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
