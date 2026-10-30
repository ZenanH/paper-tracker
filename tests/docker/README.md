# 独立 Docker 验证

仓库根目录执行 `pnpm test:docker`，或预先构建后执行：

```sh
docker build -t paper-tracker:ci-test .
node scripts/ci-docker-smoke.mjs --no-build
```

无需宿主机安装 npm 依赖，但需要指定版本的 Node.js、Docker 和 Compose。测试脚本只导入 Node 标准库。

每次创建 `.local/ci-docker-*` 目录和唯一 Compose 项目，使用随机本机端口、非 root 身份与临时密钥。模拟源响应同一窗口内的 201 篇论文，支持多页、429、标题过滤与翻译；论文日期取请求窗口，不绑定某个历史月份。

流程验证：新实例初始化、错误 origin 拒绝、旧登录接口移除、201 篇去重、67 篇排除、134 篇翻译、分页、收藏幂等、整刊归档、重复同步不复活、确认请求在途后 SIGTERM、重建数据保留、在线备份恢复、原密钥解密、缺挂载拒绝和 init 失败阻止启动。接口响应不包含明文 key。所有 AI key 均为测试假值。

成功/失败均清理自己的 Compose 容器及临时目录。`PAPER_TEST_KEEP=1` 可保留目录供排查，报告写在该次目录的 results.json，不覆盖仓库历史验收记录。镜像由调用者复用或删除，脚本不执行全局 Docker prune。

`node tests/docker/prepare-check.mjs` 额外在临时内存目录验证 root 准备后的非 root 所有权、0600 密钥、自定义端口 origin 和拒绝 PUID=0。

这些模拟验收不能证明真实 Crossref 覆盖率、真实用户 AI 服务可用性或 Tailscale 网络质量。
