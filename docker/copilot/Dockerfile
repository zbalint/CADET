# Runs CADET's `copilot` provider fully containerized: a Linux install of the
# GitHub Copilot CLI (`@github/copilot` npm package) inside an isolated Docker
# container, replacing native Windows subprocess execution (which required
# bypassing both copilot.cmd and copilot.ps1 via a direct node.exe +
# npm-loader.js invocation -- see providers/copilot.py's module docstring).
# See docs/ARCHITECTURE.md's "Containerized copilot execution" section for
# the pattern this mirrors (Phase 5 here, following codex's Phase 3 and
# cursor's Phase 4).
#
# Unlike codex/cursor (single static Linux binary), the Copilot CLI is an npm
# package with per-platform optionalDependencies (@github/copilot-linux-x64
# etc.) resolved automatically by npm at install time -- hence the node:22
# base image rather than debian:bookworm-slim + a raw binary download.
#
# Deliberately minimal: only what CADET's own curated allow-list actually runs
# (git status/log/show, pytest, unittest). Do NOT add build-essential
# speculatively -- extend this image only when a real delegated task needs it.
FROM node:22-bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates git python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

ARG COPILOT_VERSION=1.0.75
RUN npm install -g @github/copilot@${COPILOT_VERSION} && copilot --version

# Docker Desktop's WSL2 bind-mount UID presentation for the Windows host's
# /workspace almost never matches this container's UID, which git treats as
# "dubious ownership" and refuses to operate on by default. CADET's curated
# allow-list runs git status/log/show against exactly this mount.
RUN git config --system --add safe.directory /workspace

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
