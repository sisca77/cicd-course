# syntax=docker/dockerfile:1.7

# ─────────────────────────────────────────────
# 1) Builder stage
# ─────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    VENV_PATH=/opt/venv

WORKDIR /build

RUN python -m venv ${VENV_PATH}
ENV PATH="${VENV_PATH}/bin:${PATH}"

RUN pip install --upgrade pip setuptools wheel

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt


# ─────────────────────────────────────────────
# 2) Runtime stage
# ─────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VENV_PATH=/opt/venv \
    PATH="/opt/venv/bin:${PATH}" \
    PORT=8000

WORKDIR /app

# non-root user 생성
RUN addgroup --system appgroup \
    && adduser --system --ingroup appgroup --home /app appuser

# builder에서 설치된 venv만 복사
COPY --from=builder /opt/venv /opt/venv

# 앱 소스 복사
COPY --chown=appuser:appgroup src/settlement ./settlement

USER appuser

EXPOSE 8000

# curl/wget 설치 없이 Python 표준 라이브러리로 healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()" || exit 1

CMD ["uvicorn", "settlement.main:app", "--host", "0.0.0.0", "--port", "8000"]