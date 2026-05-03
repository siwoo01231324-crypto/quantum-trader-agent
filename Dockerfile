# syntax=docker/dockerfile:1.6
# QTA Phase 2 KIS 모의계좌 운영 컨테이너 (Issue #133)
#
# Multi-arch (linux/amd64, linux/arm64) — Oracle Cloud ARM Ampere 배포 대상.
# Build:
#   docker buildx build --platform linux/arm64,linux/amd64 -t qta-phase2:latest .
# Local quick:
#   docker build -t qta-phase2:latest .
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim

# native deps for pandas/numpy/lightgbm/scipy wheels (libgomp for OpenMP).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libgomp1 \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (cache-friendly layer — pyproject.toml changes less often).
COPY pyproject.toml README.md* ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

# Source code (changes most often → last layer).
COPY src ./src
COPY scripts ./scripts

# Runtime config
ENV PYTHONPATH=/app/src:/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Seoul

# Run as non-root
RUN useradd -m -u 1000 qta \
    && mkdir -p /data/logs /data/reports /app/.omc/state \
    && chown -R qta:qta /app /data
USER qta

ENTRYPOINT ["python"]
CMD ["--version"]
