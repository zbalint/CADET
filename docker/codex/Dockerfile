# Runs CADET's `codex` provider fully containerized: a Linux build of the
# OpenAI Codex CLI inside an isolated Docker container, replacing native
# Windows subprocess execution (which was already read-only-only, see
# `2854854f` — the Windows workspace-write sandbox helper never worked).
# See docs/ARCHITECTURE.md's "Containerized agy execution" section for the
# pattern this mirrors.
#
# Deliberately minimal: only what CADET's own curated allow-list actually runs
# (git status/log/show, pytest, unittest). Do NOT add node/npm/build-essential
# speculatively -- extend this image only when a real delegated task needs it.
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl tar git python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

ARG CODEX_VERSION=rust-v0.145.0
ARG CODEX_TARBALL_URL=https://github.com/openai/codex/releases/download/${CODEX_VERSION}/codex-x86_64-unknown-linux-musl.tar.gz

# The release tarball contains a single file named
# "codex-x86_64-unknown-linux-musl" (no subdirectory) -- rename it to "codex"
# so the rest of CADET's argv construction (which invokes the binary as
# "codex", matching the Windows install's name) doesn't need a
# platform-specific branch.
RUN curl -fsSL "${CODEX_TARBALL_URL}" -o /tmp/codex.tar.gz \
    && tar -xzf /tmp/codex.tar.gz -C /usr/local/bin \
    && rm /tmp/codex.tar.gz \
    && mv /usr/local/bin/codex-x86_64-unknown-linux-musl /usr/local/bin/codex \
    && chmod +x /usr/local/bin/codex \
    && codex --version

# Docker Desktop's WSL2 bind-mount UID presentation for the Windows host's
# /workspace almost never matches this container's UID, which git treats as
# "dubious ownership" and refuses to operate on by default. CADET's curated
# allow-list runs git status/log/show against exactly this mount.
RUN git config --system --add safe.directory /workspace

# Debian's /root defaults to 0700, which blocks traversal for any non-root
# UID -- entrypoint.sh drops from root to the host's UID/GID (see its own
# comment) so this container can actually write bind-mounted files owned by
# the host user under --cap-drop=ALL. o+x alone (no r/w) only grants path
# traversal into /root, not read/write of its contents.
RUN chmod o+x /root
ENV AUTH_DIR=/root/.codex

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
