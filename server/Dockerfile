FROM python:3.12-alpine

LABEL org.opencontainers.image.title="paper-tracker-archive" \
      org.opencontainers.image.description="论文追踪「已归档」状态服务（只读 API，供站点反代访问）"

ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    ARCHIVE_FILE=/archive/archive.json \
    PORT=8080

WORKDIR /app
COPY archive-api.py ./

RUN mkdir -p /archive

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/api/healthz >/dev/null 2>&1 || exit 1

CMD ["python3", "archive-api.py"]
