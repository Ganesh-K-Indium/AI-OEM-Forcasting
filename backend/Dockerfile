FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PYTHONPATH=/app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libgomp1 curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
# ARG WITH_FOUNDATION=1 pulls torch + chronos-forecasting (large). Set 0 for a lean image (Chronos-2 then reports UNAVAILABLE).
ARG WITH_FOUNDATION=1
RUN if [ "$WITH_FOUNDATION" = "1" ]; then pip install --extra-index-url https://download.pytorch.org/whl/cpu ".[foundation]"; else pip install .; fi
COPY alembic.ini ./
COPY alembic ./alembic
RUN useradd -m app && mkdir -p /app/data && chown -R app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD curl -fs http://localhost:8000/api/v1/health || exit 1
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${API_WORKERS:-2} --timeout-graceful-shutdown 5"]
