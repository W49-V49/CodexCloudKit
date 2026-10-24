# 精选记忆、增量与恢复

正式记忆用于保存经过验证的短结论；原始日志、完整会话、源代码和模型缓存使用各自的存储位置。项目切换时使用独立项目身份和分支。

## 每轮召回

先读取短目标和活动断点，再根据任务、依赖、历史失败和证据缺口定向检索。需要时扩展查询、读取正文和代码符号。没有固定记忆 Token 预算或固定召回条数。

同版本内容已经出现在当前会话或上下文代次中时，可复用已读正文；新会话仍应重新召回当前需要的事实。内容哈希、Git 变化、解析版本和模型指纹分别记录，避免把旧版本结果当成当前事实。

## 分类与容量

每条新事实记录类别、短结论、来源、适用条件、状态、源版本和真实验证证据。旧事实通过替代关系保留历史。

| 分类 | 内容 |
| --- | --- |
| foundation | 项目基础与稳定约定 |
| environment | 运行环境和部署结论 |
| tools | 工具与版本使用经验 |
| workflows | 工作流与执行方法 |
| memory | 记忆策略和索引经验 |
| failures | 已验证的失败根因与适用替代路线 |
| verification | 测试结论和可复现证据 |
| project | 具体项目的有效业务事实 |

默认治理策略为每项目/分支 256 MiB 分区、16 MiB 有效事实、每分类 2 MiB、最多 2,000 条有效事实、每条序列化记录 16 KiB。容量达到阈值后先导出可恢复快照，再做有依据的维护，不自动丢弃仍有效的事实。这些数值管理磁盘，不限制按需召回的 Token。

## 内容索引与事实后端

`cloudkit index` 生成按项目隔离的内容哈希清单。它用于发现新增、修改、删除和重命名，不等于把整个源码写进正式记忆，也不自动提供代码符号向量。

`cloudkit memory` 默认调用包内正式记忆协议，也可用 `--protocol` 接入已准备的兼容环境。通过 `setup --profile memory` 准备固定依赖；该配置需要 Python 3.12 或更新版本，后端固定为 Basic Memory 0.24.1。使用前完成实际 `status`、同步与检索验证。核心安装本身不下载正式后端和模型。

```bash
cloudkit setup --runtime-root /workspace/external-tools/codexcloudkit --profile memory
cloudkit memory --project-id example/business-project --branch main \
  --runtime-root /workspace/external-tools/codexcloudkit \
  --state-dir /workspace/persistent-memory/cloudkit -- status
```

## 初始化分类策略与同步

```bash
cloudkit memory --project-id example/business-project --branch main \
  --runtime-root /workspace/external-tools/codexcloudkit \
  --state-dir /workspace/persistent-memory/cloudkit -- govern \
  --policy /workspace/external-tools/codexcloudkit/memory/storage-policy.json
mkdir -p /workspace/business-project/docs/memory/knowledge
cloudkit_source_sha=$(git -C /workspace/business-project rev-parse HEAD)
cloudkit memory --project-id example/business-project --branch main \
  --runtime-root /workspace/external-tools/codexcloudkit \
  --state-dir /workspace/persistent-memory/cloudkit -- sync \
  --source /workspace/business-project/docs/memory/knowledge \
  --code-sha "$cloudkit_source_sha"
```

项目知识目录只放符合协议元数据格式的精选事实。空目录可以建立初始分区，不等于已有业务记忆。`govern` 不带策略路径时只读取当前治理状态；首次启用分类需提供发行配置中的策略文件。

重复同步同一来源与版本，核对新增、修改、删除、重命名、解析和向量更新计数。正式导出使用 `export --output ...`，恢复使用 `restore --archive ... --expected-sha256 ...`。导出结果提供快照摘要，用它校验后再切换活动状态。

## 恢复顺序

1. 读取最新快照元数据、源版本与预期 SHA256。
2. 验证项目、分支、文件清单、索引和模型版本。
3. 恢复正式后端、Markdown、分类策略、目录及执行断点。
4. 恢复完成后再做增量同步。
5. 重复同步应报告零解析和零向量重算。
6. 发布新快照时核对恢复时取得的版本或摘要；远端已更新则重新恢复并合并。

模型和下载缓存从独立位置重建。跨 Runner 恢复测试应使用不同目录或不同机器，并验证新会话可读、同会话去重、已完成步骤不重跑，以及分类策略与源哈希一致。

任务状态和快照放在配置的外部持久目录或私有控制存储中。公开源码仓库只保存通用实现与合成示例。
