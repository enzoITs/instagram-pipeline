# syntax=docker/dockerfile:1
# Two runtime targets from one file:
#   api    - FastAPI + migrations, no ML dependencies (~300 MB)
#   worker - Celery + scraping + NLP models (CPU torch)

ARG PYTHON_IMAGE=docker.io/library/python:3.11-slim

FROM ${PYTHON_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN groupadd --system app \
    && useradd --system --gid app --home-dir /home/app --create-home app
WORKDIR /app

# ---------------------------------------------------------------------------- api
FROM base AS api
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app app ./app
USER app
EXPOSE 8000
# Migrations run before the server starts (single API service applies them).
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-2}"]

# ---------------------------------------------------------------------------- worker
FROM base AS worker
COPY requirements.txt requirements-worker.txt ./
RUN pip install -r requirements-worker.txt \
    && python -m nltk.downloader -q -d /usr/local/share/nltk_data stopwords \
    && mkdir -p /home/app/.cache/huggingface \
    && chown -R app:app /home/app/.cache
ENV HF_HOME=/home/app/.cache/huggingface
COPY --chown=app:app app ./app
USER app
CMD ["sh", "-c", "exec celery -A app.celery_app worker --loglevel=${LOG_LEVEL:-INFO} --concurrency=${CELERY_CONCURRENCY:-2}"]
