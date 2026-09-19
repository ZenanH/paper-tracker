# 论文追踪 · 静态站点
FROM nginx:1.27-alpine

LABEL org.opencontainers.image.title="paper-tracker" \
      org.opencontainers.image.description="每日论文追踪静态站点（北京时间展示目标期刊新论文）"

ENV TZ=Asia/Shanghai

COPY nginx.conf /etc/nginx/conf.d/default.conf

# 显式指定站点根目录，避免依赖镜像默认 WORKDIR
WORKDIR /usr/share/nginx/html
COPY index.html journal.json ./
COPY assets/ ./assets/
COPY data/   ./data/

# 不再预压缩：index.html / journal.json / data 均以只读卷挂载，
# 镜像内的 .gz 会与挂载内容脱节，被 nginx gzip_static 优先命中而返回过期内容。
# 动态 gzip 由 nginx.conf 的 `gzip on` 承担。

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1/healthz >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]
