<p align="center">
  <img src="assets/favicon.svg" alt="论文追踪图标" width="72" height="72">
</p>

<h1 align="center">论文追踪</h1>

<p align="center">个人论文阅读工具，通过 Docker 部署，使用挂载目录持久保存 JSON 数据。</p>

## 主要功能

- 每天北京时间 01:00 自动检查最近三个自然月的新论文；其他时间由用户在 New papers 页面点击 Refresh 手动更新。窗口包含当天。
- 没有完整发表日期的论文按首次处理时所在月份的 1 日收录，之后取得完整日期会自动修正。
- 三个入口：Journals、New papers、Favorites；Favorites 直接展示收藏。
- 按期刊浏览最近三个月论文，支持标题搜索、整刊归档和批量收藏。
- 展示中科院 2025 升级版分区、Top 标记和可核验的影响因子。
- 取消关注停止采集并保留收藏，可按原稳定 ID 重新关注。
- 设置中配置外观、翻译/过滤模型和标题过滤规则。
- 论文、配置、阅读状态、收藏和凭据统一保存在容器 `/data`，升级和重建容器保留数据。
- 更新状态和 **Refresh** 仅显示在 New papers 页面。点击一次即在后台检查全部已关注期刊，按过滤设置处理新论文，并使用用户在 Settings 保存的 API 翻译标题；任务结束后自动更新未归档论文列表，已归档记录不会重新出现。处理中可继续浏览，按钮禁用以避免重复任务。未配置模型或翻译失败时保留英文和待翻译状态。
- 刷新按关注列表顺序逐刊核对，顶部显示本轮 `2/32 journals checked` 等真实进度；失败期刊计入已处理数并单独显示失败数。全部期刊核对后，翻译和校验仍会继续显示实际阶段。
- Journals 的 **Update IF** 点击后立即显示旋转图标，旁边展示本轮 `2/32 journals checked` 进度；获取失败与指标未确认分别计数，完成后停止动效并保留结果。切换页面不取消任务，断线后自动重连。

完整的数据规则和维护约定见 [AGENTS.md](AGENTS.md)。

## Docker 部署

仅支持 Docker 部署。服务器需要 Docker Engine 和 Docker Compose v2；无需在宿主机安装 Python、数据库或前端依赖。镜像为 `ghcr.io/zenanh/paper-tracker:latest`，支持 `linux/amd64` 和 `linux/arm64`。

```bash
git clone https://github.com/ZenanH/paper-tracker.git
cd paper-tracker
cp .env.example .env

# 为本项目准备持久目录，默认使用宿主机 /data。
sudo install -d -m 700 -o 1000 -g 1000 /data

docker compose pull
./scripts/deploy.sh
```

容器以 UID/GID `1000:1000` 运行。目录必须可由该用户读写；自定义用户时同步修改 `.env` 中的 `PAPER_TRACKER_UID`、`PAPER_TRACKER_GID` 及宿主目录所有者。如果宿主机 `/data` 已有其他用途，在 `.env` 中将 `PAPER_TRACKER_DATA_DIR` 改为本项目专用目录，例如 `/srv/paper-tracker/data`，并提前创建该目录；容器内挂载位置始终为 `/data`。

部署脚本会读取服务器本机 Tailscale IPv4（`tailscale ip -4`），并将网页绑定到该地址的 TCP `8899` 端口。访问 `http://<服务器的 Tailscale IPv4>:8899/`。服务器须已安装并连接 Tailscale；如果 Tailscale IP 发生变化，再运行一次部署脚本以更新 Docker 端口绑定。归档和管理服务只监听容器内部回环地址，不发布独立端口。

请通过容器的 HTTP 地址访问，不要直接打开仓库中的 `index.html`：`file://` 无法读取服务端的期刊配置和 API。首次启动会先初始化默认 32 刊，论文列表在后台采集后陆续生成；已有关注列表（包括主动取消全部关注）保持原样。

已有镜像不可用时可从源码构建：

```bash
PAPER_TRACKER_BIND_IP="$(tailscale ip -4)" PAPER_TRACKER_PORT=8899 docker compose up -d --build
```

也可不使用 Compose，直接挂载服务器目录启动：

```bash
docker run -d --name paper-tracker --init --restart unless-stopped \
  -p "$(tailscale ip -4):8899:8080" \
  --mount type=bind,source=/data,target=/data \
  ghcr.io/zenanh/paper-tracker:latest
```

## 数据与更新

只需挂载一个可读写的 `/data`。所有持久文件都存放在该目录下：

```text
/data/
  journal-config.json          # 关注期刊和用户配置
  journals.json               # 期刊分区和影响因子
  manifest.json               # 论文日期清单
  days/YYYY-MM-DD.json         # 日期论文数据
  translations.json           # 翻译缓存
  filter-config.json          # 标题过滤设置
  title-classifications.json  # 分类缓存和审计
  excluded-papers.json        # 过滤记录
  collection-status.json      # 各期刊采集水位
  task-status.json            # 任务状态
  archive/
    archive.json              # 已读状态和永久收藏
  secrets/
    llm-config.json           # 模型配置与密钥
```

`/data/secrets` 权限为 `0700`，凭据文件为 `0600`。网页只公开论文日期清单、期刊元数据和日期论文文件；运行配置、审计、归档原文件及凭据不能作为静态文件访问。数据不会打进镜像或提交到 Git。

仓库根目录无需 `secrets/`、`data/` 或 `archive/`；旧 Gist 授权文件已移除。这里的 `/data/secrets/` 是当前模型配置的持久目录，仍须保留。本机验收容器的数据统一在 `.runtime/docker-data/`，不属于源代码，不应在清理代码时删除。

首次启动从默认 32 刊初始化，之后保留用户修改。常驻管理服务每天北京时间 01:00 更新论文；启动时恢复中断状态，若当天 01:00 的更新漏跑则补跑。其他时间由用户在 New papers 页面点击 **Refresh** 手动触发同一处理流程。实际完成时间取决于数据源和模型，不承诺即时完成。

经 Tailscale 远程访问时，直接打开宿主机 Tailscale IPv4 的 `8899` 端口。归档会在页面顶部显示逐批保存确认；超过 8 秒提示慢连接，提交未完成时浏览器会提示确认后再离开。实际归档由容器写入 `/data/archive/archive.json`。

可在 `.env` 中设置 `CROSSREF_MAILTO`（可选）。模型 Base URL、API Key 和模型名称在网页 Settings 中配置。

更新镜像并重建容器：

```bash
git pull --ff-only
docker compose pull
./scripts/deploy.sh
docker compose logs --tail=100 paper-tracker
```

挂载目录保持不变即可保留论文、收藏和设置。迁移服务器时先停止容器，再备份整个宿主数据目录；其中包含密钥和令牌，完整备份须加密。不要自动清空数据目录。

## CI 镜像构建

GitHub Actions 在 Pull Request 中执行单元测试、语法检查、镜像构建和容器持久化验收；测试使用临时数据，不调用真实模型。推送到 `main` 后，全部检查通过才向 GHCR 发布 `latest` 和提交 SHA 标签；`v*` 标签发布对应版本镜像。

镜像构建覆盖 `linux/amd64` 和 `linux/arm64`。CI 不执行日常采集，也不包含任何运行数据或凭据。GHCR 软件包需要设为 Public 才能匿名拉取；私有软件包需要先登录有权限的账号。

前端契约测试会检查 DOM ID 和静态资源内容 hash，防止 HTML 与脚本版本不一致导致管理按钮失效。旧的日期选择栏、归档隐藏/恢复操作和周维护调度已移除；保留的兼容处理仅用于读取既有阅读状态、稳定论文 ID 和安全恢复中断的旧数据迁移。
