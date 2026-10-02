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
# ORB: logic thuan, M5 lich su, scheduler + state, backtest, trang quan ly phien
COPY orb.py orb_history.py orb_runtime.py orb_backtest.py admin_orb.py ./
# TM - #ORB-RULES - ORB Rule Set: bo rule ORB rieng (orb.py import module nay)
COPY orb_rules.py ./

# TM - #GANN-TW - Gann Time Windows
COPY klines_coverage.py gann_pivots.py gann_windows.py ./

RUN mkdir -p /data/klines /data/bias /data/journal /data/journal/orb_skips \
             /data/orb/state /data/orb/logs /data/orb/history /data/orb/backtests \
             /data/time_windows/pivots \
    && chown -R bamcp:bamcp /data /app

USER bamcp
EXPOSE 8848

# Path phai khop server.health_path trong config.yaml
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request as u, sys; sys.exit(0 if u.urlopen('http://127.0.0.1:8848/healthz', timeout=4).status == 200 else 1)"

CMD ["python", "server.py"]
