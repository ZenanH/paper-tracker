# 本地 Docker 部署

镜像与发布尚未上传 GitHub/镜像仓库。本地标签为 `paper-tracker:1.0-local`，不递增发布版本。

需要 Docker Compose；初始化使用临时 Node 容器生成密钥，无需宿主机安装 Node.js：

```sh
./scripts/setup.sh
```

默认在 `./paper-tracker-data` 保存 `data/`、`secrets/`、非敏感参数 `compose.env`。AI API 仅通过页面设置，密文存入外挂数据库。已有实例缺少密钥时初始化停止，不重新生成替换密钥。

后续命令均带同一参数文件，避免端口、目录或 UID 发生变化：

```sh
docker compose --env-file paper-tracker-data/compose.env ps
docker compose --env-file paper-tracker-data/compose.env logs --tail=50
```

更新前停止旧 web/worker，保持数据目录，重新构建和初始化：

```sh
docker compose --env-file paper-tracker-data/compose.env down
docker compose --env-file paper-tracker-data/compose.env up -d --build
```

不要删除外挂数据或 secrets。web 与 worker 共享本机 SQLite，不能把目录改成跨主机文件盘。

默认仅绑定 `127.0.0.1:3000`。可信 Tailscale 私网访问时，可在 compose.env 把 BIND_ADDRESS 改为宿主机实际 Tailscale IP，并将 APP_URL 设为用户真实访问的 origin；写操作来源检查依赖此值。修改后重建容器。不默认开放所有网卡。

独立部署验收示例（不使用开发实例目录）：

```sh
PAPER_TRACKER_DATA=.local/docker-smoke/paper-tracker-data \
APP_URL=http://localhost:3010 WEB_PORT=3010 \
COMPOSE_PROJECT_NAME=paper-tracker-smoke ./scripts/setup.sh
```

Compose 启动 init 后才启动 web/worker，worker 不发布端口，web 提供 HTTP 健康检查。当前不宣称支持未测试的 Linux amd64 或 Windows 容器。


## 健康检查与更新

`web` 健康检查调用轻量 `/api/ready`，检查数据库和安装记录；`worker` 独立检查 25 秒内的数据库心跳。`docker compose ps` 应显示两个服务 healthy。Docker 不会因为 unhealthy 自动重启进程，应检查日志、目录权限和磁盘空间。worker 的停止宽限期为 120 秒，中断请求会释放租约，下次启动续做已提交的检查点，不消耗失败重试次数。

新实例若由 root 运行 setup，默认准备 UID/GID 1000 的非 root 服务目录；显式 PUID=0 被拒绝。普通用户默认使用自身 UID/GID。再次执行 setup 会保留 compose.env 中的 UID/GID，不将其自动替换成当前命令用户。显式设置 PUID/PGID 时，宿主机目录必须允许该身份读写，secrets 文件必须可读；不要使用 chmod 777。setup 会等待服务健康，初始化失败或健康超时以非零状态退出。

## 在线备份与恢复

在线生成一致性快照（可在 web/worker 运行时执行）：

```sh
docker compose --env-file paper-tracker-data/compose.env exec -T web node dist/scripts/backup.js
```

默认写入外挂的 `paper-tracker-data/data/backups/<snapshot-id>/`，包含 `COMPLETE`、`manifest.json` 和每个原始 16 MiB 块的独立 gzip 文件。只传输完成目录；同一快照丢块或损坏时，只需重新传输对应块，其他块可保留。恢复会验证每块长度、SHA-256、整库 hash、SQLite 完整性/外键和 AI key 解密。备份不是加密文件，请保护宿主机权限。

单独安全备份 `secrets/`，不要放进数据库备份目录。恢复使用原实例密钥。以下命令将快照恢复到外挂数据目录下尚不存在的 `restore-check` 子目录，不改在线数据库：

```sh
docker compose --env-file paper-tracker-data/compose.env exec -T web node dist/scripts/restore.js /app/instance/data/backups/<snapshot-id> /app/instance/data/restore-check
```

验证成功后，停止旧 web/worker，将新实例的数据目录指向恢复结果，并只读挂载原 secrets，再运行 init 和启动服务。也可以在停止服务后，将恢复出的数据库移入已准备好的新实例 data 目录。禁止把恢复文件直接覆盖到在线数据库，禁止把旧 WAL/SHM 与恢复数据库混用。恢复目标已存在、块缺失或密钥不匹配时，命令会失败；不要删除仍在使用的实例以绕过错误。

源代码环境可用 `pnpm backup [备份输出目录]` 和 `pnpm restore <快照目录> <新数据目录>`，通过 `PAPER_TRACKER_HOME` / `ENCRYPTION_KEY_FILE` 指定原实例。恢复保留原安装时间、关注、归档、收藏和 AI 结果，清除旧 worker 心跳；未完成任务依租约及检查点继续。
