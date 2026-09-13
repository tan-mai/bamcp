# syntax=docker/dockerfile:1

# ---------- build stage: cai dep vao venv rieng ----------
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt

# ---------- runtime stage ----------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    BAMCP_CONFIG=/app/config.yaml \
    BAMCP_DATA_ROOT=/data

# User khong phai root. UID co dinh de quyen tren volume on dinh giua cac lan build.
RUN groupadd --system --gid 10001 bamcp \
    && useradd --system --uid 10001 --gid bamcp --no-create-home bamcp

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY server.py exchanges.py settings.py admin.py probe.py config.yaml ./

RUN mkdir -p /data/klines /data/bias /data/journal \
    && chown -R bamcp:bamcp /data /app

USER bamcp
EXPOSE 8848

# Path phai khop server.health_path trong config.yaml
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request as u, sys; sys.exit(0 if u.urlopen('http://127.0.0.1:8848/healthz', timeout=4).status == 200 else 1)"

CMD ["python", "server.py"]
