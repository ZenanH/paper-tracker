# 内置期刊数据

核验日期：2026-10-28。两份原始 CSV 是仓库内的只读数据快照，不需要用户上传，也不需要运行时连接查分网站。

来源为 [hitfyd/ShowJCR](https://github.com/hitfyd/ShowJCR)，固定提交 `4687a8f608f20aaf004299992f5d347dba009361`，上游目录为 `中科院分区表及JCR原始数据文件/`。原始文件字节未修改，校验和见 [manifest.json](manifest.json)。

| 文件 | 含义 | 记录数 |
| --- | --- | --- |
| `raw/FQBJCR2025-UTF8.csv` | 中科院分区表升级版 2025，大类、小类与 Top | 21,772 |
| `raw/JCR2025-UTF8.csv` | IF 指标年 2025，实际列名 `IF(2025)` | 22,643 |

这里的 IF 2025 是 2026 年发布的指标。不要误用 2025 年发布版的 2024 指标年数据。抽样：Nature 56.1，Science 47.3，IEEE TPAMI 20.4，Nature Communications 18.1。Nature 与 Nature Communications 的数值与 [出版商官方指标页](https://www.nature.com/nature-portfolio/about-journals/journal-metrics) 一致。

中科院表全部为 2025 年，共 1,789 条 Top；大类一区 1,451 条、二区 2,844 条、三区 4,583 条、四区 12,894 条。JCR 有 619 条 `<0.1` 与 49 条 `N/A`，不能一概转换成数值。

仅按 ISSN/EISSN 精确关联：21,386 条中科院记录唯一匹配 IF 表，386 条未匹配，无多义匹配。不要使用模糊刊名填充这些缺失。主目录需要两表并集；不能因没有 IF 丢掉有分区的期刊，也不能因缺少分区丢掉仅有 IF 的期刊。

在仓库根目录执行：

```sh
python3 scripts/verify_journal_data.py
```

脚本仅使用 Python 标准库，离线验证文件大小、SHA-256、Git blob SHA、表头、行宽、条数和统计。不生成或修改数据。应用的目录生成器尚未实现，后续必须从这些本地快照确定性生成只读 `/app/catalog/catalog.sqlite` 与刊号/名称索引；浏览器仅接收限量搜索结果，不下载全量 CSV、数据库或目录 JSON。具体契约见 [数据存储与慢网络传输决策](../../docs/architecture/data-and-network.md)。

保留来源与 [上游 GPL-3.0 许可证副本](../../third_party/ShowJCR-LICENSE.txt)。该许可证是上游仓库标注，不能据此认定中科院分区和 Clarivate JCR 原始数据另有无限制再分发授权。官方分区数据权利说明见 [使用协议](https://www.fenqubiao.com/User/UseProtocol.aspx)。此快照是社区整理来源，不是官方授权证明；不得移除来源或宣称数据原创。

业务规则及实现约束见仓库根目录 `AGENTS.md`；接口实测记录见 `docs/research/source-probes.json`。
