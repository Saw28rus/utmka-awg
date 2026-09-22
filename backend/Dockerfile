# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libffi-dev openssh-client git curl docker-cli \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
COPY scripts/pip-install.sh /tmp/pip-install.sh
# Cache mount: обрыв PyPI не сбрасывает уже скачанные wheel. Скрипт сам переключает зеркала.
RUN --mount=type=cache,target=/root/.cache/pip \
    chmod +x /tmp/pip-install.sh && /tmp/pip-install.sh /app/requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
COPY scripts/entrypoint.sh ./scripts/entrypoint.sh
RUN chmod +x ./scripts/entrypoint.sh

EXPOSE 8000

CMD ["bash", "scripts/entrypoint.sh"]
