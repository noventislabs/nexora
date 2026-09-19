FROM python:3.11-slim-bookworm

# FFmpeg is a hard requirement of the render pipeline. Without it the application
# still starts and reports FFMPEG: UNAVAILABLE rather than faking a render.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY nexora ./nexora
COPY alembic.ini ./
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 nexora \
 && mkdir -p /app/data/storage /app/data/render \
 && chown -R nexora:nexora /app
USER nexora

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn nexora.main:app --host 0.0.0.0 --port 8000 --workers 2"]
