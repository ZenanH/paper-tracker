# Docker 本地部署验收（2026-10-29）

使用 OrbStack，本机 macOS ARM64 构建并运行 Linux ARM64 镜像 `paper-tracker:1.0-local`。容器 Node 24.22.1、SQLite 3.53.4。未上传代码、未创建 CI、未推送镜像；Linux amd64 与 Windows 尚未验证。

- Dockerfile 多阶段构建成功；内置目录从仓库 CSV 离线生成，23,029 本，默认名单随镜像打包。
- init 成功退出后 web 和 worker 启动；web 健康检查通过，独立 worker 正常运行。
- 外挂目录：`.local/docker-smoke/paper-tracker-data`；生产模式测试端口 `3010`。开发实例 `3000` 和其数据目录未变。
- 非 root 运行；secrets 挂载只读。API key 密文落入宿主机数据库，页面查询不返回 key。
- 用明确标记的两篇合成论文验证：中英文标题、收藏、归档、取消关注、Crossref 邮箱、虚拟 API 配置均在删除并重建容器后保留。安装时间、实例 ID 不变，虚拟 key 使用原密钥仍可解密。测试未使用维护者真实凭据或消耗 AI 额度。
- 最终镜像再次重建后重复核对，结果一致。虚拟 AI 配置仅用于持久性测试，不是可用 AI 服务。
- 实际 API 返回 gzip，条件请求返回 304；错误 Origin 的写请求返回 403。

## 浏览器验收

原始结果：[browser-smoke.json](browser-smoke.json)。

Chrome，390×844 手机视口，400ms 延迟、下载 50 KiB/s、上传 20 KiB/s：

1. 首次加载后切换 New papers，显示 20 条论文。
2. 断网后切换 Favorites，连接状态提示失败。
3. 恢复网络后自动重新请求，显示两篇测试收藏，不需要手动刷新。
4. 手机与 1280×900 桌面视口均无横向溢出；页面脚本错误为 0。
5. 本轮测试约 12.6 秒。

上述浏览器记录是历史验收证据，其一次性测试数据和脚本已清理。当前可重复的容器验收入口为 `pnpm test:docker`，说明见 [tests/docker/README.md](../../tests/docker/README.md)；它不替代浏览器视觉验收。

本轮修复了编译后 worker 的 JSON import 声明、慢网首次点击早于 hydration、断网提示与恢复后重新加载。镜像去掉重复依赖后约 458 MiB（Docker 未压缩大小）。

本轮是本地生产容器和浏览器网络模拟验收，不等于真实 Tailscale 丢包测试、异机备份恢复或多架构发布验收。
