# Both stages start from the same interpreter image and uv is copied in as a static binary.
# A uv image as the builder would install a managed standalone interpreter, and the virtual
# environment built against it would point at a python the runtime stage does not have.
# uv is pinned to the 0.12 line because pyproject requires uv_build>=0.12,<0.13.
FROM python:3.12-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependencies first, keyed on the lockfile alone, so editing src/ does not reinstall
# everything. --no-dev keeps pytest and ruff out of the image.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Then the project. README.md is required because pyproject declares it as the readme and
# the wheel build reads it. --no-editable installs a real copy, prompts included, so
# nothing at run time depends on /app/src still being there.
COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# The prompt texts ship as package data. If that ever regresses it should fail the build,
# not the first request.
WORKDIR /
RUN /app/.venv/bin/python -c "from port_tariff_agent.prompts import ALL_PROMPTS; assert ALL_PROMPTS, 'no prompts were packaged'; print('packaged prompts:', len(ALL_PROMPTS))"


# Runtime: no uv, no compiler, no source tree. The virtual environment and the data.
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    DATA_DIR=/app/data \
    PORT=8000

RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv

# The committed query-time artifacts, so the image answers a question with no ingestion and
# no host files (ADR-022, ADR-031). .dockerignore keeps the transcription cache out. Owned
# by the runtime user, so an ingest inside the container can write here and a named volume
# mounted over it inherits the ownership.
COPY --chown=app:app data ./data

USER app
EXPOSE 8000

# curl is not in the slim image and the interpreter is.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health', timeout=4).status == 200 else 1)"

# sh -c so an injected $PORT expands; exec so uvicorn is PID 1 and gets SIGTERM directly.
# One worker on purpose: sessions live in the process and the registry has one writer lock.
# No ENTRYPOINT, so `docker run <image> port-tariff ask "..."` runs the CLI instead.
CMD ["sh", "-c", "exec uvicorn port_tariff_agent.api:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers"]
