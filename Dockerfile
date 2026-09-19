# 论文追踪 · 静态站点
FROM nginx:1.27-alpine

LABEL org.opencontainers.image.title="paper-tracker" \
      org.opencontainers.image.description="每日论文追踪静态站点（北京时间展示目标期刊新论文）"

ENV TZ=Asia/Shanghai

RUN sed -i -E 's/^worker_processes[[:space:]]+auto;/worker_processes 1;/' /etc/nginx/nginx.conf
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 显式指定站点根目录，避免依赖镜像默认 WORKDIR
WORKDIR /usr/share/nginx/html
COPY index.html journal.json ./
COPY assets/ ./assets/
RUN mkdir -p ./data

# 论文与翻译数据只从运行时持久卷读取，不写入镜像。
# 动态 gzip 由 nginx.conf 的 `gzip on` 承担。

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1/healthz >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]
