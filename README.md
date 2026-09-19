# 论文追踪

一个由 GitHub Actions 更新、通过 GitHub Pages 发布的个人论文日报。站点按北京时间展示目标期刊每日新上线的论文，并保留滚动三个自然月的历史记录。

## 本地预览

```bash
python3 -m http.server 8000
```

浏览器打开 `http://localhost:8000`。页面必须通过 HTTP 访问，直接打开 `index.html` 时浏览器通常不允许读取 JSON 数据。

## 数据任务

```bash
# 从固定 ShowJCR 2025 升级版快照生成期刊主数据
python3 scripts/sync_cas.py

# 更新昨日论文，并检查新收录的迟到补录
python3 scripts/collect.py --mode daily

# 首次回填滚动三个月
python3 scripts/collect.py --mode backfill

# 检查出版社页面的影响因子
python3 scripts/update_metrics.py
```

中文翻译只在 GitHub Actions 中运行。本地不会调用模型或生成译文缓存；请在 GitHub Actions 页面手动触发 `Backfill paper history`，日常任务由 `Daily paper update` 自动完成。

翻译使用配置好的 OpenAI 兼容 `POST /v1/chat/completions` 接口。模型通过函数调用返回严格结构化结果，CI 会检查每个输入 ID 是否恰好返回一次，并按输入顺序写回。日更只翻译新论文，旧译文升级由 `Backfill paper history` 分批执行；翻译失败时保留英文标题并标记待翻译，不阻塞整日报。

在仓库 Settings -> Secrets and variables -> Actions 中配置：

- Secret `LLM_API_KEY`：接口密钥。
- Variable `LLM_BASE_URL`：接口基础地址，例如 `https://provider.example/v1`，程序会追加 `/chat/completions`。
- Variable `LLM_MODEL`：供应商要求的模型名称。

不要把 API Key 写入仓库文件、workflow、网页或对话。切换模型后，缓存引擎标识会变化；如需把三个月历史重新翻译，手动运行 `Backfill paper history` 并设置合适的 `translation_limit`。

## 自动化

- `Daily paper update`：北京时间每天 01:00 采集并翻译新标题。
- `Backfill paper history`：手动执行，分批回填三个月数据与翻译。
- `Quarterly impact factor check`：每年 1、4、7、10 月检查出版社指标页面。
- `Deploy GitHub Pages`：`main` 分支更新后发布静态站点。

采集失败与“当天无论文”分开记录。影响因子只有在页面同时出现明确的 JIF 标签和年份时才会自动覆盖旧值。

## 数据口径

- 业务时区：`Asia/Shanghai`
- 归档日期：Crossref 首次在线发表日期优先
- 中科院分区：2025 升级版，数据整理来源 ShowJCR 固定快照
- 去重：DOI 优先
- 翻译：GitHub Actions 内调用配置的 OpenAI 兼容模型，严格校验结构化输出后写入持久缓存
- CI 状态：页面优先读取公开 GitHub Actions 状态，并用本地浏览器缓存和静态快照兜底
- 页面设置：可在当前浏览器隐藏/恢复期刊并切换浅色、深色或跟随系统主题；不会修改仓库采集配置

完整约定见 [AGENTS.md](AGENTS.md)。

## Docker 运行

仓库提供多阶段 `Dockerfile`（nginx:alpine 承载静态站点，gzip 预压缩）。

```bash
# 构建
docker build -t paper-tracker:latest .

# 运行（绑定 Tailscale IP 与本机回环）
docker run -d --name paper-tracker --restart unless-stopped \
  -p <TAILSCALE_IP>:8899:80 \
  -p 127.0.0.1:8899:80 \
  -e TZ=Asia/Shanghai \
  paper-tracker:latest
```

或使用 `docker-compose up -d`。

访问：`http://<TAILSCALE_IP>:8899/`（Tailscale 组网内）或 `http://127.0.0.1:8899/`（本机）。
健康检查端点：`/healthz`。

### 更新站点数据

站点为纯静态，数据更新后重新构建镜像即可：

```bash
docker build -t paper-tracker:latest . && docker restart paper-tracker
```
