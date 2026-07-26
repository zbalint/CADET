# Runs CADET's `agy` provider fully containerized: a Linux build of agy inside
# an isolated Docker container, replacing native Windows subprocess execution.
# See docs/ARCHITECTURE.md's "Containerized agy execution" section.
#
# Deliberately minimal: only what CADET's own curated allow-list actually runs
# (git status/log/show, pytest, unittest). Do NOT add node/npm/build-essential
# speculatively -- extend this image only when a real delegated task needs it.
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl tar git python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

ARG AGY_VERSION=1.1.7
ARG AGY_TARBALL_URL=https://github.com/google-antigravity/antigravity-cli/releases/download/${AGY_VERSION}/agy_cli_linux_x64.tar.gz

# The release tarball contains a single file named "antigravity" (no
# subdirectory) -- rename it to "agy" so the rest of CADET's argv construction
# (which invokes the binary as "agy", matching the Windows install's name)
# doesn't need a platform-specific branch.
RUN curl -fsSL "${AGY_TARBALL_URL}" -o /tmp/agy.tar.gz \
    && tar -xzf /tmp/agy.tar.gz -C /usr/local/bin \
    && rm /tmp/agy.tar.gz \
    && mv /usr/local/bin/antigravity /usr/local/bin/agy \
    && chmod +x /usr/local/bin/agy \
    && agy --version

# Docker Desktop's WSL2 bind-mount UID presentation for the Windows host's
# /workspace almost never matches this container's UID, which git treats as
# "dubious ownership" and refuses to operate on by default. CADET's curated
# allow-list runs git status/log/show against exactly this mount.
RUN git config --system --add safe.directory /workspace

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
