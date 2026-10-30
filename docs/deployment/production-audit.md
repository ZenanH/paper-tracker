# Docker 正式运行检查与修复

检查日期：2026-10-29（Asia/Shanghai）。仅本地构建与验证，产品版本仍为 1.0，未发布镜像或上传源码。本轮使用独立 audit/setup-audit 数据目录与端口，既有 paper-tracker-smoke 实例未重启、迁移或修改。

## 已修复

- 翻译结果完成数不足总数、其余任务取消时误报 succeeded；任务显示成功但缺失译题时按失败处理。没有启用过滤的实例显示 not_needed。
- 删除 AI key 后，queued 任务无法领取却永久占住更新锁；现在转为 blocked，重新配置后续做。替换/删除凭据会 fencing 在途请求；保存未变化的配置不会中断任务或重复计费。
- 数据库连接在 busy timeout 设置前执行 DDL；普通 web/worker 连接现在只配置连接参数，补充结构迁移由 init 在事务内执行。
- SQLite FULL 可自动回滚事务，原处理器再次执行 ROLLBACK 会掩盖原始错误；现在保留原始错误，验证失败事务不推进状态或检查点。
- 缺少备份恢复实现；新增 Online Backup API 一致性快照、16 MiB 原始块独立 gzip、块/整库 SHA-256、完成标志和原子发布、空间检查及独立目录恢复。恢复校验 SQLite 完整性、外键、原 AI key 解密，保留安装/归档/收藏并清除旧 worker 心跳。
- 健康检查使用完整状态接口并忽略 worker；web 改为轻量 ready，worker 单独检查心跳。Docker unhealthy 不会自动触发重启，仍需部署者监控。
- SIGTERM 可能消耗重试次数并等待租约；正常中断现在释放仍由该 worker 持有的租约，不修改已提交检查点，配置 120 秒停止宽限期。
- setup 依赖宿主机 Node、覆盖已有 UID/GID、遗漏持久化 CROSSREF_MAILTO、root 执行时沿用 UID 0；改用临时 Node 容器准备文件、复用已有身份、保存联系邮箱、等待服务健康；root 新实例默认准备 UID/GID 1000 的目录。只构建一个共享镜像供三个服务使用。
- worker 的 undici 依赖依赖 standalone 间接打包；现在显式复制运行依赖并处理 pnpm 符号链接冲突。补充任意层级 secrets、compose.env、.env 和备份块的构建上下文排除规则。

## 实测证据

- 23 项自动测试通过，TypeScript 检查通过；生产 Next.js 构建及 Linux ARM64 Docker 镜像构建成功。
- 独立 Compose 新实例：init 成功退出，web/worker 均 healthy，非 root、只读 secrets 挂载。
- 确定性模拟 Crossref：201 篇、每页 200，pub/online 查询共返回 402 条，DOI 去重 201 条；先返回 429，再成功恢复。模拟 AI 排除 67 篇、翻译 134 篇；payload 仅 ID 与标题，每批不超过 10 条。未使用真实用户 AI key。
- 分页 20 条，最后一页 14 条；收藏幂等；归档服务器全部 201 篇（包括 AI 隐藏项），收藏仍可访问；再次同步不复活归档。
- 模拟上游确认已收到 15 秒慢请求后停止 worker，小于 1 秒退出（详见结构化结果），任务恢复 queued、attempts 保持 0；重新启动继续检查点。
- 保留数据强制重建容器后安装时间/实例 ID、201 条归档、1 条收藏和加密 AI key 保留，连接测试仍可解密。
- 镜像内在线备份与恢复 CLI 成功，备份位于外挂 data/backups。多块测试另验证缺块、损坏、错误密钥和已有目标目录拒绝恢复，不产生假成功结果。
- 缺少挂载/密钥的镜像启动失败；单独失败 init 阻止 web/worker 启动。停止测试 worker 后，心跳检查失败且状态接口 workerAlive=false。
- SQLite max_page_count 故障注入触发真实 SQLITE_FULL，事务内状态和检查点均回滚，连接仍可继续使用。这是数据库容量上限测试，不等同于宿主机文件系统物理耗尽测试。
- 一步 setup 在当前没有 PATH 中 Node 的宿主环境完成；仅设置 WEB_PORT=3013 时 APP_URL 正确派生 localhost:3013。
- 真实 Crossref 小样本：停止测试 worker 前记录源返回 2,351 条、新增 1,422 条、重复 929 条，初始同步 7/33 本已完成，仍有任务运行。未将不完整同步描述为完整验收；AI 未配置，翻译等待配置，没有付费请求。
- 条件缓存返回 ETag/304，写请求错误 origin 返回 403。
- 镜像逐层检查 16 层，对照三个独立实例的 6 个密钥值，未发现实例密钥、用户数据库或 compose.env。结果见 image-layer-audit.json。
- 临时内存目录验证 root 初始化后目录属于 UID 1000、密钥权限 0600、自定义端口正确派生 APP_URL；PUID=0 被拒绝。

结构化结果见 production-audit-results.json，确定性模拟测试及重放步骤见仓库 [tests/docker/README.md](../../tests/docker/README.md)。

## 范围限制

本轮未声称支持 Linux amd64/Windows，未调用部署者真实付费 AI，未重新开展全套浏览器/真实 Tailscale 弱网/百万行压测。模拟源验证业务和故障路径，不能证明 Crossref 对所有期刊的完整覆盖或任何用户 AI 服务的可用性。在线 SQLite 仍要求同主机本地文件系统，备份密钥需要独立安全保存。
