# syntax=docker/dockerfile:1
# Three build targets from one file:
#   api    - FastAPI + the built dashboard + Alembic (no ML dependencies, ~350 MB)
#   worker - Celery worker for the `analysis` queue (scraping + NLP models)
# The `collection` worker runs from the `api` image: it only calls the Graph API.

ARG PYTHON_IMAGE=docker.io/library/python:3.12-slim
ARG NODE_IMAGE=docker.io/library/node:22-alpine

# ---------------------------------------------------------------------- frontend
FROM ${NODE_IMAGE} AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# -------------------------------------------------------------------------- base
FROM ${PYTHON_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN groupadd --system app \
    && useradd --system --gid app --home-dir /home/app --create-home app
WORKDIR /app

# --------------------------------------------------------------------------- api
FROM base AS api
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app app ./app
COPY --chown=app:app scripts ./scripts
COPY --from=frontend --chown=app:app /build/dist ./frontend/dist
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"
# The API service applies the migrations; AUTO_MIGRATE=false keeps the app
# processes from racing on the same DDL.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-2}"]

# ------------------------------------------------------------------------ worker
FROM base AS worker
COPY requirements.txt requirements-worker.txt ./
RUN pip install -r requirements-worker.txt \
    && python -m nltk.downloader -q -d /usr/local/share/nltk_data stopwords \
    && mkdir -p /home/app/.cache/huggingface \
    && chown -R app:app /home/app/.cache
ENV HF_HOME=/home/app/.cache/huggingface
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app app ./app
USER app
CMD ["sh", "-c", "exec celery -A app.celery_app worker -Q analysis --loglevel=${LOG_LEVEL:-INFO} --concurrency=${CELERY_CONCURRENCY:-2}"]
