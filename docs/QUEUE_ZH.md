# 可选 RQ 唤醒队列

RQ 用于传递下一步执行事件。Git 控制分支继续保存唯一的任务状态、计划、租约、outbox、断点和验收证据；正式记忆仍由已有记忆协议管理。Redis 不建立第二份任务或事实主库。

## 准备队列客户端

```bash
cloudkit setup --runtime-root /workspace/external-tools/codexcloudkit --profile queue
```

`queue` 配置在独立虚拟环境安装固定 RQ 2.12.0、redis 客户端及其锁定依赖。安装收据核对全部发行版本；重复准备复用未变化依赖。它不启动 Redis 服务，也不配置模型认证。

需要一个实际可访问的 Redis 或兼容 Valkey 服务，以及已初始化的私有控制仓库、已注册任务和有效后端。官方 RQ 支持 Redis 5+、Valkey 7.2+；本次真实服务测试使用 Redis 7.2.16。

没有现成服务时，可在云端源码目录使用外置安装器准备固定 Redis 7.2.16。安装需要 Git、make 与兼容 C 编译器，源码、构建和 launcher 留在指定外部目录：

```bash
python3 /workspace/CodexCloudKit/scripts/install_redis.py \
  --runtime-root /workspace/external-tools/codexcloudkit/queue-runtime
mkdir -p /workspace/persistent-memory/cloudkit/redis
/workspace/external-tools/codexcloudkit/queue-runtime/bin/redis-server \
  --bind 127.0.0.1 --port 16379 \
  --dir /workspace/persistent-memory/cloudkit/redis --appendonly yes
```

安装器只准备并验证程序，不启动或修改系统服务。由云端服务管理器托管上面的 Redis 命令，再从另一个进程启动队列 worker。把连接通过环境配置交给 producer 和 worker，例如上述云端回环服务：

```bash
export CLOUDKIT_REDIS_URL=redis://127.0.0.1:16379/0
```

未设置此变量时，桥接默认连接 `redis://127.0.0.1:16379/0`。连接信息不进入队列事件或任务证据。不同服务器运行 producer/worker 时，它们连接同一队列服务，并使用相同的 Git 控制仓库身份、分支及持久状态。每个消费者通过自己的 `--repository` 和 `--project-root` 配置本机 checkout，目录可以与 producer 不同；省略 `project-root` 时由控制器按计划克隆恢复业务工作区。同一队列服务不同控制仓库时，使用不同队列名。

## 先持久注册，再入队

按[执行计划](EXECUTION_ZH.md)初始化私有控制仓库并注册目标，任务的计划、source SHA、验收及 outbox 先写入 Git。然后递送当前 outbox：

```bash
cloudkit queue --runtime-root /workspace/external-tools/codexcloudkit --   --repository /workspace/private-control   --project-root /workspace/business-project   --queue cloudkit --ref main --github-repository example/private-control   enqueue --task-id project-goal-001
```

把 `main` 和 `example/private-control` 替换为私有控制仓库真实默认分支和仓库身份。producer 递送任务与事件标识；实际工作目录由消费者的 `--project-root` 指定，不指定时由既有控制器按持久计划恢复。

通常不传 `--event-id`，让桥接读取当前持久 outbox 的事件。显式事件必须与当前 outbox 一致。确定性 job ID 减少重复递送；producer 和 worker 使用相同的 JSONSerializer。队列 payload/result 只包含任务、事件、路由和状态，不携带 plan、goal、正式记忆或 producer 的绝对 checkout 路径，消费者使用自己的本机配置恢复；最终去重、租约和副作用判断仍由 Git 控制器负责。

## 启动独立消费者

在另一进程或实际云端服务管理器中运行：

```bash
cloudkit queue --runtime-root /workspace/external-tools/codexcloudkit --   --repository /workspace/private-control   --project-root /workspace/business-project   --queue cloudkit --ref main --github-repository example/private-control   worker --poll
```

producer 和 worker 使用同一 `CLOUDKIT_REDIS_URL`、队列名及持久控制存储。后端命令宜使用绝对路径，确保独立 worker 可以找到相同的虚拟环境、模型客户端与验收工具。模型执行使用该云 Runner 已验证的实际认证。

一次性消化当前队列可用 `worker --burst`；`--max-jobs` 与 `--max-idle-time` 分别控制该消费者进程的工作数及空闲退出。进程退出不改变尚未完成的持久目标。

自动恢复业务目录的根由 `CLOUDKIT_RUNS_ROOT` 配置，默认 `/workspace/autonomy-runs`；临时 Runner 可指向自己的临时外部目录，恢复依据仍来自持久 Git。

worker 启动时从 Git outbox 补齐 Redis 丢失的待执行事件。消费者每次恢复 Git 最新任务，再进入既有执行器的一轮工作和独立验收。状态仍为 `READY` 或 `RETRY_WAIT` 且存在新 outbox 时立即递送下一事件；任务为 `DONE` 时不再入队。若存在有效租约，重复消费者不会再次取得执行权。

## 查看状态

```bash
cloudkit queue --runtime-root /workspace/external-tools/codexcloudkit --   --repository /workspace/private-control --queue cloudkit   inspect --task-id project-goal-001
```

`inspect` 返回 Git 任务状态、当前 outbox、已处理事件数量以及 Redis 可用性和待消费数量。队列为空不代表目标完成；以任务 `DONE` 和独立验收证据为准。连接失败不把已有 Git 状态清空。

## 失败和备用路线

入队、消费或连接失败时，保留 Git outbox 并归档错误类型，尝试既有事件派发；同类运输错误累计两次熔断该 RQ 路线，后续使用既有 `workflow_dispatch`、`repository_dispatch` 和小时巡检。工作流先按[自建 Runner](SELF_HOSTED_RUNNER_ZH.md)部署。

RQ 与 BullMQ 连接同一个 Redis 时，不能作为基础设施独立的两条备用路线。当前备用使用既有 GitHub 事件及小时巡检。若服务连接和后台身份尚未配置，状态如实保留为待接入，不把函数或队列安装当成真实模型能力。

模型 probe、worker 命令和验收步骤各有自己的超时；桥接据计划计算 job 超时。超时、worker 重启、重复事件和 Redis 中断通过真实进程测试，模型能力仍由实际模型请求和项目验收单独确认。

## 验收分层

| 层次 | 检查 |
| --- | --- |
| queue profile | 固定客户端安装、全部版本核对、重复零重装、原有 memory 配置保持 |
| 真 Redis 运输 | 真实入队、独立消费者、重复事件、重启恢复、服务中断与备用 |
| 持久执行器 | Git 租约、outbox、失败归档、修复和 DONE 停派 |
| 模型及业务 | 真实模型响应、代码变化、独立构建测试和项目验收 |

真实 Redis 测试中的命令夹具只证明运输与执行恢复，不被记为模型调用成功。队列模式是可选唤醒通道，现有 GitHub 续派流程可继续单独运行。
