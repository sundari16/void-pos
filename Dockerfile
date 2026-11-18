# syntax=docker/dockerfile:1
# The Void POS API: FastAPI on uvicorn, migrating the database on start.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, pinned by constraints.txt, so code changes don't reinstall them.
COPY pyproject.toml constraints.txt ./
COPY src ./src
RUN pip install -c constraints.txt ".[print]"

COPY alembic.ini ./
COPY migrations ./migrations
COPY docker/api-entrypoint.sh /usr/local/bin/api-entrypoint

RUN chmod 0755 /usr/local/bin/api-entrypoint \
    && useradd --system --uid 10001 --no-create-home voidpos
USER voidpos

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=6 \
    CMD python -c "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=2).status != 200)"

ENTRYPOINT ["api-entrypoint"]
