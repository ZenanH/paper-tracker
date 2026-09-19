# 论文追踪

一个在本机自托管运行的个人论文日报。站点按北京时间展示目标期刊每日新上线的论文，并保留滚动三个自然月的历史记录。

数据采集、翻译、指标检查全部在本机完成（Docker + systemd timer），运行不依赖外部 CI 服务。GitHub Actions 仅用于跨平台测试和 Docker 镜像构建发布。

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

systemd timer 每天北京时间 01:00 运行 `scripts/paper-tracker-daily.sh`，依次执行：

1. `collect.py --mode daily` 采集新论文
2. `translate.py` 翻译新增标题
3. 单元测试校验数据
4. `git commit` 留痕

凭据放在 `~/.openclaw/paper-tracker.env`（权限 0600），包含：

- `LLM_BASE_URL`：接口基础地址，例如 `https://provider.example/v1`
- `LLM_API_KEY`：接口密钥
- `LLM_MODEL`：供应商要求的模型名称

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
- 「已归档」中可按单篇、可按期刊整组勾选，然后恢复所选（方便复查）

归档状态由 `archive-api` 服务持久化（容器内部服务，不对外暴露端口，经 nginx `/api/` 反代），
存储在 `archive/archive.json`，跨设备共享、清浏览器缓存不丢失。

完整约定见 [AGENTS.md](AGENTS.md)。

## Docker 运行

`Dockerfile` 用 nginx:alpine 承载静态站点，数据目录只读挂载，数据更新即时生效。

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

或使用 `docker-compose up -d`。

访问：`http://<TAILSCALE_IP>:8899/`（Tailscale 组网内）或 `http://127.0.0.1:8899/`（本机）。
健康检查端点：`/healthz`。

### 更新站点数据

数据目录已只读挂载，`collect.py` 写入后页面即时可见，无需重建镜像或重启容器。仅在改动 `index.html`、`assets/` 或 `nginx.conf` 时需要重建：

```bash
docker build -t paper-tracker:latest . && docker restart paper-tracker
```

## GitHub CI

`.github/workflows/docker-ci.yml` 在每次提交和 Pull Request 时运行：

- `ubuntu-latest`、`windows-latest`、`macos-latest`：执行 Python 语法检查、单元测试和静态数据校验。
- `ubuntu-latest`：在三种系统测试全部通过后，构建站点与 `archive-api` 两个 Linux Docker 镜像。
- 推送到 `main`：发布 `linux/amd64` 和 `linux/arm64` 多架构镜像到 GitHub Container Registry，并生成 `latest` 与 `sha-*` 标签。
- Pull Request：只构建验证，不发布镜像。

镜像地址：

```text
ghcr.io/zenanh/paper-tracker:latest
ghcr.io/zenanh/paper-tracker-archive:latest
```

CI 使用仓库自带的 `GITHUB_TOKEN` 发布镜像，不需要配置 LLM 或 Crossref 凭据。
