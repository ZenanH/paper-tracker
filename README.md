<p align="center">
  <img src="assets/favicon.svg" alt="论文追踪图标" width="72" height="72">
</p>

<h1 align="center">论文追踪</h1>

<p align="center">一个通过 Docker 自托管运行的个人论文追踪工具。</p>


## 主要功能

- 每 12 小时（北京时间 01:00、13:00）检查最近三个自然月的新论文，并翻译中文标题；窗口包含当天。
- 不设日期待核实或补充论文分类；没有完整发表日期的论文按首次处理时所在月份的 1 日收录，之后取得完整日期会自动修正，原始日期和推定标记保留在后台。
- 三个入口：Journals、New papers、Favorites；Favorites 直接展示收藏，不设分类。
- 按期刊浏览最近三个自然月的论文，支持标题搜索、期刊标题行一键归档整刊，以及批量收藏。
- 展示中科院 2025 升级版分区、Top 标记和可核验的影响因子。
- 支持整刊已读归档和跨设备收藏，两种状态独立保存。
- 输入期刊英文名，匹配并核对 ISSN 后关注；取消关注停止采集并保留收藏，可重新关注；支持手动更新 IF 或立即更新论文。
- 设置中配置并验证翻译/过滤模型、自定义过滤规则、选择过滤期刊，支持深浅和系统主题。
- 论文、翻译、归档和收藏数据保存在宿主机，升级容器不会覆盖。
- 设置中通过 GitHub 授权将期刊、论文、中文标题和阅读状态备份到 Gist，也可预览并恢复自己的 Gist；不导出 API 配置或密钥。

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


## Gist 备份与恢复

先在 [GitHub OAuth Apps](https://github.com/settings/developers) 注册 OAuth App：

1. 名称可填 `Paper Tracker`，Homepage URL 和 Redirect URI 填自己的站点地址（本机预览为 `http://127.0.0.1:18898/`）。Device Flow 不使用 Redirect URI。
2. **Enable Device Flow 开启，Allow wildcard matching 关闭**。保留默认令牌有效期即可，过期后重新连接。
3. 将公开的 Client ID 填入站点 Settings → Gist backup → GitHub OAuth app，或在 `.env` 设置 `PAPER_TRACKER_GITHUB_CLIENT_ID`。不需要 Client Secret。
4. 点击 **Connect GitHub**，打开 GitHub 链接并输入页面授权码，确认 Gist 权限。不要把令牌填入页面或发送到对话中。
5. **Export to Gist** 新建一个 Secret Gist，完成后点击链接查看。Gist 不会公开列出，但知道链接的人可以读取。
6. 恢复时填写自己账号的 Gist URL 或 ID，点击 **Preview import** 核对数量，再点 **Import**。这会替换当前期刊、论文和阅读数据；模型/API 配置不受影响。

备份按期刊分片，包含三个月内普通论文、已有中文标题、归档标记及全部收藏（包括已取消关注和三个月前的收藏），另含期刊元数据及过滤规则。不包含模型配置、API Key、GitHub 令牌、日志、内部缓存或被过滤论文审计。

GitHub 凭据只保存在宿主 `secrets/github-gist.json`，文件权限 0600；Disconnect 删除本地令牌，若需撤销 GitHub 授权请前往 GitHub 设置。导入预览 30 分钟有效，导入前自动保存受管理数据的回退副本到 `archive/backups/`；中断时服务启动先回退。请自行定期管理备份目录空间。

Gist 是手动的精简数据备份，不能替代包含受保护配置的完整宿主备份。完整备份包含 secrets 时请先加密。
