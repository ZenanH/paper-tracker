# GitHub CI 与镜像发布

用户已授权提交、推送并通过 CI 发布。目标仓库为 ZenanH/Paper-tracker，镜像使用 GHCR。具体流程见 `.github/workflows/ci.yml`。main 推送先运行全部源码与 AMD64 容器验收，成功后使用限定 packages:write 的 GITHUB_TOKEN 发布版本镜像。固定版本禁止覆盖；首版为 1.0，后续发布前明确递增 package.json 两段产品版本（对应三段包版本）。未发布的失败重试复用版本。不会发布 latest 或未经验证的平台。

## 上传内容

源码 app/components/lib/worker、scripts、tests、styles/public、锁定的 data/journals 原始数据与 manifest、third_party、文档，以及 Docker/Compose、锁文件、编辑器/Git 配置和 .github 文件应一并上传。

`.gitignore` 排除实例、密钥、数据库/WAL/SHM、备份、依赖和构建产物。`.gitattributes` 禁止改写原始 CSV 换行，以保持来源 SHA-256。`pnpm check:repository` 按 Git 实际候选集合检查：本地没有 Git 仓库时使用临时 Git 元数据，不修改项目的 Git 状态；在 CI 中也检查已被误跟踪的忽略文件。

不上传 `.local/`、`.next/`、dist、catalog、node_modules、.pnpm-store、tsconfig.tsbuildinfo、任何实例目录、compose.env、密钥或数据库备份。生成的期刊目录在构建时从本地快照重建。无需上传本地 Docker 镜像，CI 自行构建。

