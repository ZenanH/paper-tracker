FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.source="https://github.com/ZenanH/paper-tracker"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    PAPER_TRACKER_DATA_DIR=/data

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 papertracker \
    && useradd --uid 1000 --gid papertracker --no-create-home papertracker \
    && mkdir -p /data \
    && chown papertracker:papertracker /data

WORKDIR /app
COPY assets/ ./assets/
COPY index.html favicon.ico journal.json ./
COPY scripts/ ./scripts/
COPY server/ ./server/

USER 1000:1000
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).close()"

CMD ["python3", "server/run.py"]
