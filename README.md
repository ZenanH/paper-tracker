# Paper-tracker

个人期刊与论文工作台，使用 Docker Compose 自部署。打开实例直接进入暗色工作台；仅部署在可信私网或 Tailscale，访问控制由部署网络承担。

## Docker 启动

需要 Docker 和 Docker Compose，无需宿主机安装 Node.js：

```sh
./scripts/setup.sh
```

默认访问 [localhost:3000](http://localhost:3000)。setup 准备宿主机数据/密钥目录、构建本地镜像，完成数据库初始化后启动 web 和独立 worker。后续执行会保留原数据与密钥。

用户状态保存在 `paper-tracker-data/data/`，密钥保存在 `paper-tracker-data/secrets/`，部署参数保存在 `paper-tracker-data/compose.env`。更新不能删除这些目录。Tailscale 地址、APP_URL、备份恢复及升级流程见 [Docker 部署说明](docs/deployment/docker.md)。

## 功能与数据

- 搜索内置目录、预览分区/Top/IF 后关注期刊；新实例带有 33 本默认关注，AI 过滤默认关闭。
- Crossref 按北京时间窗口分页同步，浏览器关闭后由 worker 继续执行。
- 用户在 Settings 填写自己的 AI baseURL、key 和 model；无 AI 配置仍可同步、归档和收藏。
- AI 仅处理论文标题，不请求或保存摘要；过滤与翻译独立，失败保留原题。
- 整刊归档、收藏、取消关注和重建容器均保留服务端持久状态。
- 在线一致性备份采用 16 MiB 原始块独立 gzip 和 SHA-256；恢复时需要原实例密钥。

内置目录由锁定的本地 CSV 离线构建，不在运行时抓取查分站。来源、指标年份和权利说明见 [期刊数据说明](data/journals/README.md) 与 [NOTICE](NOTICE.md)。Crossref 的登记覆盖存在差异，项目不保证任意期刊 100% 完整实时收齐。

## 本地开发

使用 `.node-version` 指定的 Node.js 和 package.json 指定的 pnpm：

```sh
pnpm install --frozen-lockfile
pnpm setup
pnpm dev
```

开发状态位于 `.local/paper-tracker-data/`，与正式实例分开。更多检查和贡献说明见 [CONTRIBUTING.md](CONTRIBUTING.md)。

```sh
pnpm check:repository
pnpm typecheck
pnpm test
pnpm build
pnpm test:docker
```

## CI 与发布状态

[GitHub Actions CI](.github/workflows/ci.yml) 执行源码边界、数据校验、类型检查、测试、生产构建和 Linux AMD64 Docker 生命周期/备份恢复验收。main 推送通过全部验收后向 GHCR 发布 `ghcr.io/zenanh/paper-tracker`，版本取自 package.json（首次为 `1.0`）；固定版本标签禁止覆盖，已有版本须明确递增后再发布。失败未发布时保留原版本重试。

远端发布结果以 Actions 为准。ARM64 仍可手动验证；Windows 容器尚未实现。镜像包首次发布默认可能为私有，拉取需要 GitHub Packages 权限。

版本事实来源是 `package.json`：`1.0.0` 对应产品 `1.0`。未来发布依次为 `1.1`、`1.2`、…、`1.10`；本地构建与检查不递增。上传和发布准备说明见 [GitHub 准备说明](docs/deployment/github.md)。

业务约束见 [AGENTS.md](AGENTS.md)，历史验证范围见 [Docker 检查记录](docs/deployment/production-audit.md)。不要将历史测试结果理解为已完成跨平台发布验收。
