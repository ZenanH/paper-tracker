# 论文追踪 · 静态站点
FROM nginx:1.27-alpine

LABEL org.opencontainers.image.title="paper-tracker" \
      org.opencontainers.image.description="每日论文追踪静态站点（北京时间展示目标期刊新论文）" \
      org.opencontainers.image.source="https://github.com/ZenanH/paper-tracker"

ENV TZ=Asia/Shanghai

COPY nginx.conf /etc/nginx/conf.d/default.conf

# 显式指定站点根目录，避免依赖镜像默认 WORKDIR
WORKDIR /usr/share/nginx/html
COPY index.html journal.json ./
COPY assets/ ./assets/
COPY data/   ./data/

# 预压缩：静态 JSON/JS/CSS 体积可减 ~70%，配合 gzip_static
RUN set -eux; \
    apk add --no-cache gzip; \
    find . -type f \( -name '*.json' -o -name '*.js' -o -name '*.css' -o -name '*.html' -o -name '*.svg' \) \
      -exec gzip -9 -k -f {} \; ; \
    apk del gzip

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1/healthz >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]
