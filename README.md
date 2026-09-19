# 论文追踪

一个在本机自托管运行的个人论文日报。站点按北京时间展示目标期刊每日新上线的论文，并保留滚动三个自然月的历史记录。

数据采集、翻译、指标检查全部在本机 Docker 中完成。常驻的 `journal-manager` 在北京时间每日 01:00 执行更新，也为设置界面的期刊增删和手动运行提供内部 API。GitHub Actions 仅用于跨平台测试和 Docker 镜像构建发布。

## 本地预览

```bash
python3 -m http.server 8000
```

浏览器打开 `http://localhost:8000`。页面必须通过 HTTP 访问，直接打开 `index.html` 时浏览器通常不允许读取 JSON 数据。

## 数据任务

```bash
# 从随仓库保存的官方快照生成期刊主数据（离线，无需联网）
python3 scripts/sync_cas.py

# 更新昨日论文，并检查新收录的迟到补录
python3 scripts/collect.py --mode daily

# 首次回填滚动三个月
python3 scripts/collect.py --mode backfill

# 检查出版社页面的影响因子
python3 scripts/update_metrics.py

# 翻译标题（读取 LLM_* 环境变量）
python3 scripts/translate.py --limit 1000 --batch-size 8 --workers 2
```

## 每日自动化

`journal-manager` 容器每天北京时间 01:00 依次执行：

1. `collect.py --mode daily` 采集新论文
2. `translate.py` 翻译新增标题
3. 到期时检查出版社页面的影响因子
4. 校验生成数据

复制 `.env.example` 为项目根目录的 `.env`，并将权限设为 `0600`。Compose 会自动读取该文件；`.env` 已被 Git 忽略。主要配置包括：

- `CROSSREF_MAILTO`：Crossref 请求联系邮箱，可留空
- `LLM_BASE_URL`：接口基础地址，例如 `https://provider.example/v1`
- `LLM_API_KEY`：接口密钥
- `LLM_MODEL`：供应商要求的模型名称
- `PAPER_TRACKER_DATA_DIR`：论文数据及翻译缓存的宿主机持久目录，默认 `./data`
- `PAPER_TRACKER_ARCHIVE_DIR`：已读归档状态的宿主机持久目录，默认 `./archive`

不要把 API Key 写入仓库文件、日志、网页或对话。切换模型后缓存引擎标识会变化；如需把三个月历史重新翻译，运行 `translate.py --retranslate-existing`。

采集失败与"当天无论文"分开记录。影响因子只有在页面同时出现明确的 JIF 标签和年份时才会自动覆盖旧值。

## 数据口径

- 业务时区：`Asia/Shanghai`
- 归档日期：Crossref 首次在线发表日期优先
- 中科院分区：2025 升级版，官方平台停服前快照，随仓库保存并按键值校验锁定
- 去重：DOI 优先
- 翻译：本机调用配置的 OpenAI 兼容模型，严格校验结构化输出后写入持久缓存
- 数据状态：页面顶部展示最近一次采集的期刊成功/失败数与更新时间
- 页面设置：可在当前浏览器隐藏/恢复期刊并切换浅色、深色或跟随系统主题；不会修改服务端采集配置
- 期刊管理：设置中可输入期刊英文全名后添加并运行，也可删除期刊或立即执行日更

## 期刊管理

镜像内的 `journal.json` 是默认 32 本期刊种子。首次启动时会复制为数据目录中的 `journal-config.json`；此后用户增删只修改持久配置，升级镜像不会重新添加已经删除的默认期刊。

“添加并运行”会执行以下流程：

1. 在镜像内固定的 2025 分区快照中匹配标准刊名、ISSN/eISSN、中科院大类/小类分区和 Top
2. 尝试从 Crossref 最近论文定位出版社页面，并核验明确标注年份的 Journal Impact Factor
3. 回填滚动三个自然月内的论文
4. 使用 `.env` 中配置的 OpenAI 兼容接口翻译待翻译标题

固定快照不包含 JCR quartile，也不包含所有期刊的可验证 JIF。系统不会编造缺失指标；无法从出版社页面确认时会保留“待核实”状态。删除期刊会同时删除其日期数据、补录、采集状态、归档状态，以及不再被其他论文引用的翻译缓存。

## 页面结构

三个大标签：

| 标签 | 内容 |
| --- | --- |
| **每日论文** | 固定展示昨日论文，日期不可更改；勾选即归档 |
| **历史及补录** | 下分两个小标签：**历史**（按日浏览，范围滚动三个月）、**补录**（迟到补录 + 日期待核实） |
| **已归档** | 按论文日期展示已读记录，默认昨日，可切换到前三个月内任意日期 |

## 已归档

「每日论文」中每篇论文与每本期刊标题旁都有勾选框：

- 勾选单篇 → 标记为已读并归档
- 勾选期刊级「全部已读」→ 该刊当前列表整批归档
- 已归档论文从「每日论文」移出，可在「已归档」标签查看
- 「已归档」中可按单篇、可按期刊整组勾选，然后恢复所选为未读（方便复查）
- “清空归档”会从归档页隐藏全部当前归档，但仍保持已读，不会重新出现在每日或历史列表
- 设置中的“恢复归档”可把已清空的记录重新放回“已归档”页面

归档状态由 `archive-api` 服务持久化（容器内部服务，不对外暴露端口，经 nginx `/api/` 反代），
存储在 `archive/archive.json`，跨设备共享、清浏览器缓存不丢失。

完整约定见 [AGENTS.md](AGENTS.md)。

## Docker 运行

`Dockerfile` 用 nginx:alpine 承载静态站点，`Dockerfile.tasks` 承载采集、翻译和数据维护任务。数据与翻译缓存写入 `PAPER_TRACKER_DATA_DIR`，归档状态写入 `PAPER_TRACKER_ARCHIVE_DIR`。

```bash
# 构建
docker build -t paper-tracker:latest .

# 运行（绑定 Tailscale IP 与本机回环）
docker run -d --name paper-tracker --restart unless-stopped \
  -p <TAILSCALE_IP>:8899:80 \
  -p 127.0.0.1:8899:80 \
  -e TZ=Asia/Shanghai \
  -v "$PWD/data:/usr/share/nginx/html/data:ro" \
  -v "$PWD/index.html:/usr/share/nginx/html/index.html:ro" \
  -v "$PWD/journal.json:/usr/share/nginx/html/journal.json:ro" \
  paper-tracker:latest
```

推荐使用 Compose：

```bash
cp .env.example .env
chmod 600 .env
# 编辑 .env 后启动站点、归档 API 与期刊管理/调度服务
docker compose up -d --build
```

访问：`http://<TAILSCALE_IP>:8899/`（Tailscale 组网内）或 `http://127.0.0.1:8899/`（本机）。
健康检查端点：`/healthz`。

### 更新站点数据

数据任务通过按需启动的 `tasks` profile 运行，写入后页面即时可见：

```bash
docker compose --profile tasks run --rm tasks python3 scripts/collect.py --mode daily
docker compose --profile tasks run --rm tasks python3 scripts/translate.py --limit 300
docker compose --profile tasks run --rm tasks python3 scripts/collect.py --mode backfill
docker compose --profile tasks run --rm tasks python3 scripts/update_metrics.py
docker compose --profile tasks run --rm tasks python3 scripts/validate_data.py
```

正常运行时无需手动执行这些命令；设置中的播放按钮可立即触发一次日更，管理容器仍会在下一次北京时间 01:00 自动运行。

每次采集会按 `manifest.retention.start` 清理滚动三个自然月之外的日期文件、补录记录和不再被保留论文引用的翻译缓存。仅在改动 `index.html`、`assets/`、容器文件或 `nginx.conf` 时需要重建：

```bash
docker build -t paper-tracker:latest . && docker restart paper-tracker
```

## GitHub CI

`.github/workflows/docker-ci.yml` 在每次提交和 Pull Request 时运行：

- `ubuntu-latest`、`windows-latest`、`macos-latest`：执行 Python 语法检查、单元测试和静态数据校验。
- `ubuntu-latest`：在三种系统测试全部通过后，构建站点、`archive-api` 与任务容器三个 Linux Docker 镜像。
- 推送到 `main`：发布 `linux/amd64` 和 `linux/arm64` 多架构镜像到 GitHub Container Registry，并生成 `latest` 与 `sha-*` 标签。
- Pull Request：只构建验证，不发布镜像。

镜像地址：

```text
ghcr.io/zenanh/paper-tracker:latest
ghcr.io/zenanh/paper-tracker-archive:latest
ghcr.io/zenanh/paper-tracker-tasks:latest
```

CI 使用仓库自带的 `GITHUB_TOKEN` 发布镜像，不需要配置 LLM 或 Crossref 凭据。
