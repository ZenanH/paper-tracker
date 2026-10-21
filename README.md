<p align="center">
  <img src="assets/favicon.svg" alt="论文追踪图标" width="72" height="72">
</p>

<h1 align="center">论文追踪</h1>

<p align="center">个人论文阅读工具，使用本机 Python 服务运行。</p>

## 主要功能

- 每 12 小时（北京时间 01:00、13:00）检查最近三个自然月的新论文，并翻译中文标题；窗口包含当天。
- 没有完整发表日期的论文按首次处理时所在月份的 1 日收录，之后取得完整日期会自动修正。
- 三个入口：Journals、New papers、Favorites；Favorites 直接展示收藏。
- 按期刊浏览最近三个月论文，支持标题搜索、整刊归档和批量收藏。
- 展示中科院 2025 升级版分区、Top 标记和可核验的影响因子。
- 取消关注停止采集并保留收藏，可按原稳定 ID 重新关注。
- 设置中配置翻译/过滤模型、过滤规则和 GitHub Gist 备份。
- 数据保存在本机 `data/live/`、`archive/` 和 `secrets/`，与代码更新分离。
- 点击页面顶部 **Refresh** 立即启动采集，对所选期刊的新论文执行过滤，再翻译标题；任务结束后自动重读列表。处理中可继续浏览，按钮禁用以避免重复任务。未配置模型时继续采集，跳过翻译与过滤。

完整的数据规则和维护约定见 [AGENTS.md](AGENTS.md)。

## 本机运行

要求：macOS 或 Linux、Python 3.10 或更新版本。应用不需要安装 Python 第三方包。

```bash
git clone https://github.com/ZenanH/paper-tracker.git
cd paper-tracker

mkdir -p data/live archive secrets
chmod 700 secrets
cp .env.example .env
chmod 600 .env

python3 scripts/run_local.py
```

打开 `http://127.0.0.1:18898/`。本机服务只监听回环地址；管理 API、归档 API 和内部数据文件不会作为独立公网端口或静态文件开放。按 `Ctrl-C` 停止本机服务。

启动管理服务会恢复中断任务，并在需要时启动补跑和定时采集；这些任务会写入 `data/live/` 和 `archive/`。运行数据、模型配置与 GitHub 令牌不会因更新代码而被覆盖。

2026-10-21 版本已按要求清空本机旧论文数据、阅读状态、运行报告和备份。首次启动从默认 32 刊初始化，之后保留用户修改；模型和 GitHub 凭据独立保留。刷新受数据源与模型处理耗时影响，页面显示实际阶段，不承诺即时完成。

可在 `.env` 中设置 `CROSSREF_MAILTO`（可选）和公开的 `PAPER_TRACKER_GITHUB_CLIENT_ID`。模型 Base URL、API Key 和模型名称在网页 Settings 中配置；密钥只保存于 `secrets/llm-config.json`。

更新代码时先在运行终端按 `Ctrl-C`，然后执行：

```bash
git pull --ff-only
python3 scripts/run_local.py
```

## Gist 备份与恢复

在 [GitHub OAuth Apps](https://github.com/settings/developers) 注册 OAuth App，开启 Enable Device Flow 并关闭 Allow wildcard matching。Device Flow 不使用 Redirect URI；本机预览地址可填 `http://127.0.0.1:18898/`。只需公开 Client ID，不需要 Client Secret。

在 Settings → Gist backup 中连接 GitHub、导出 Secret Gist，或预览并导入自己账号拥有的 Gist。Secret Gist 不会公开列出，但知道链接的人可以读取。备份不包含模型/API 配置、密钥、令牌、日志或内部缓存。恢复会替换期刊、论文和阅读数据，并在导入前保存本机回退副本。

## 部署方向

后续部署目标是 Vercel Free；其存储、函数和定时任务迁移尚未完成，本 README 目前只说明本机原生运行方式。
