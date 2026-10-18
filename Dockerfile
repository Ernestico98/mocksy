# Mocksy in a container: Linux, so speech-to-text runs on faster-whisper (CPU).
# Build and run with docker compose (see README).

# ---- web UI
FROM node:24-slim AS web
WORKDIR /web
RUN corepack enable && corepack prepare pnpm@12.3.4 --activate
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

# ---- app
FROM python:3.11-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --shell /bin/bash mocksy
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY config.toml ./
COPY app/ app/
COPY prompts/ prompts/
COPY scripts/docker-entrypoint.sh /usr/local/bin/
COPY --from=web /web/dist web/dist
# Volumes mount over these (models: Kokoro, fetched on first start; .cache: Whisper; .claude: login)
RUN mkdir -p data models /home/mocksy/.cache/huggingface /home/mocksy/.claude \
    && chown -R mocksy:mocksy data models /home/mocksy

USER mocksy
# Claude Code runs the interviewer. Log in once with `claude auth login` inside the container
# (kept in the claude volume), or pass CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY.
# "stable", "latest" or an exact version. Docker caches this layer: to pick up a newer release,
# rebuild with --no-cache or pass --build-arg CLAUDE_CODE_VERSION=<version>.
ARG CLAUDE_CODE_VERSION=stable
RUN curl -fsSL https://claude.ai/install.sh | bash -s "$CLAUDE_CODE_VERSION" && rm -rf /home/mocksy/.claude/*
ENV PATH="/home/mocksy/.local/bin:$PATH" PYTHONUNBUFFERED=1 CLAUDE_CONFIG_DIR=/home/mocksy/.claude

EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s \
    CMD curl -fs http://127.0.0.1:8765/api/meta >/dev/null || exit 1
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8765"]
