# 本地开发与检查

先阅读 AGENTS.md。产品展示名称为 `Paper-tracker`，包名、镜像名等技术标识使用 `paper-tracker`；维护者沟通使用中文，产品界面使用 en-US。实例只用于可信私网/Tailscale，直接进入工作台，网络负责访问控制。

```sh
pnpm install --frozen-lockfile
pnpm check:repository
python3 scripts/verify_journal_data.py
pnpm catalog
pnpm typecheck
pnpm test
pnpm build
```

`pnpm setup` 创建独立开发实例；`pnpm dev` 同时运行 web 和 worker。不要使用正式实例目录做测试。程序依赖版本由 package.json、.node-version 和 pnpm-lock.yaml 固定。

Docker 完整验收使用 `pnpm test:docker`。它创建独立项目、随机端口、临时密钥和模拟 Crossref/AI；结束后清理测试容器与目录，镜像可复用。无需真实 API key，不会向 Crossref 发请求。可用 `PAPER_TEST_KEEP=1` 保留失败现场，或预先构建 paper-tracker:ci-test 后执行 `node scripts/ci-docker-smoke.mjs --no-build`。

提交时包含相关测试、文档和 lockfile；不要提交实例目录、密钥、数据库、备份、node_modules 或生成目录。不要用删除用户状态修复同步或升级问题。第三方来源说明见 NOTICE.md。

当前只准备 CI，不创建远程仓库、不发布镜像、不递增产品版本。PR 不需要真实 AI 凭据；请不要在日志、截图或 issue 中粘贴密钥和数据库。
