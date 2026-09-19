# 论文追踪

一个通过 Docker 自托管运行的个人论文追踪工具。

## 主要功能

- 每天北京时间 01:00 自动采集目标期刊的新论文并翻译中文标题。
- 展示每日论文，以及滚动三个自然月的历史和补录记录。
- 展示中科院 2025 升级版分区、Top 标记和可核验的影响因子。
- 支持已读归档、恢复归档和跨设备收藏。
- 可在设置中新增、删除期刊，或立即运行一次更新。
- 可在设置中配置翻译/过滤模型，并恢复误过滤的论文。
- 论文、翻译、归档和收藏数据保存在宿主机，升级容器不会覆盖。

完整的数据规则、技术设计和维护约定见 [AGENTS.md](AGENTS.md)。

## Docker 部署

要求：Docker、Docker Compose 和 Tailscale。

```bash
git clone https://github.com/ZenanH/paper-tracker.git
cd paper-tracker

cp .env.example .env
chmod 600 .env

# 自动写入本机 Tailscale IPv4 地址
sed -i "s/^PAPER_TRACKER_TAILSCALE_IP=.*/PAPER_TRACKER_TAILSCALE_IP=$(tailscale ip -4)/" .env
sed -i "s/^PAPER_TRACKER_UID=.*/PAPER_TRACKER_UID=$(id -u)/" .env
sed -i "s/^PAPER_TRACKER_GID=.*/PAPER_TRACKER_GID=$(id -g)/" .env

# CROSSREF_MAILTO 可按需填写。

mkdir -p data/days archive secrets
chmod 700 secrets
chown -R "$(id -u):$(id -g)" data archive secrets

docker compose pull
docker compose up -d
```

启动后在右上角“设置”中填写模型 Base URL、API Key 和模型名称；未配置时只采集论文，不翻译也不过滤。

启动后访问：

- Tailscale：`http://<TAILSCALE_IP>:8899/`
- 本机：`http://127.0.0.1:8899/`

查看运行状态：

```bash
docker compose ps
docker compose logs -f journal-manager
```

## 更新

```bash
git pull --ff-only
docker compose pull
docker compose up -d --remove-orphans
```

`data/`、`archive/`、`secrets/` 和 `.env` 独立于 Docker 镜像，更新代码和容器不会删除已有论文、翻译、归档、收藏或模型配置。
