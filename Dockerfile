FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && ln -s /root/.local/bin/uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml ./
COPY chronosync ./chronosync
COPY chronosync_client ./chronosync_client
COPY alembic.ini ./
COPY alembic ./alembic

RUN uv sync --frozen --no-dev 2>/dev/null || uv sync --no-dev

RUN useradd --create-home --shell /bin/bash chrono \
    && mkdir -p /var/lib/chronosync \
    && chown -R chrono:chrono /app /var/lib/chronosync
USER chrono

EXPOSE 8088

CMD ["uv", "run", "chronosync", "run"]
