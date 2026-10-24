# 目标计划、执行与独立验收

`cloudkit run` 把参数传给内置执行控制器；`--supervisor` 可选择已有兼容执行器。控制器使用配置的私有控制仓库保存任务，不把真实任务写入公开源码分支。

## 目标计划

任务计划包含以下字段：

| 字段 | 内容 |
| --- | --- |
| `project_id` | 业务仓库身份 |
| `branch` | 业务分支 |
| `source_sha` | 完整 40 位源提交 |
| `goal` | 明确的业务目标 |
| `backends` | 具有独立执行路线的后端列表 |
| `acceptance` | 独立验收命令的 argv 数组列表 |
| `progress_files` | 用于识别输出变化的项目内相对路径 |
| `acceptance_timeout_seconds` | 单条验收的超时 |
| `acceptance_version` | 验收条件版本 |

每个后端包含 `id`、`independence_key`、`kind`、`command`、`probe_command`、`timeout_seconds` 和幂等性说明。真实 Codex 后端使用 `kind: codex`；合成子进程测试使用 `command_fixture`，两者分开记录。

## 能力探测

`probe_command` 接收带 challenge 的 JSON 请求，返回对应 challenge、backend_id、model_response 和 capability。仅输出版本号不能证明可执行模型工作。生产 worker 应通过实际模型请求取得响应，再运行任务。

工作请求通过 stdin JSON 提供目标、项目、源提交、验收命令、上一轮失败和断点。后端需要将真实执行结果写到 stdout/stderr；控制器归档脱敏证据。

## 初始化私有控制仓库

先把自己的私有控制仓库检出到 `/workspace/private-control`，配置有写权限的 `origin`。它可以是空仓库，无需已有 main 历史；业务仓库需要有真实源提交。初始化会建立独立的 `automation/state` 分支；已有状态会验证并保持。

```bash
cloudkit_source_sha=$(git -C /workspace/business-project rev-parse HEAD)
cloudkit run -- --repository /workspace/private-control init \
  --project-id example/business-project \
  --source-sha "$cloudkit_source_sha" --branch main
```

`project-id` 和源提交对应当前初始业务目标。真实任务和恢复快照保存在这个私有控制仓库，公开工具箱保留通用代码与合成示例。

## 注册与推进

在已初始化并连接到私有远端的控制仓库上注册计划：

```bash
cloudkit run -- --repository /workspace/private-control register   --task-id project-goal-001 --plan /workspace/worker-plan.json
cloudkit run -- --repository /workspace/private-control tick   --task-id project-goal-001 --project-root /workspace/business-project   --event-id initial-001 --owner cloud-worker
cloudkit run -- --repository /workspace/private-control run   --task-id project-goal-001 --project-root /workspace/business-project   --max-rounds 50
```

`tick` 推进一个实际工作与验收边界；`run` 连续推进指定轮次。一次进程退出后持续运行，需要由运行平台继续调用或托管该进程。控制仓库先按上节初始化，后端配置使用真实工作命令和能力探针；空目录不等于已恢复的持久状态。

## 读取任务状态

命令返回的 JSON 包含实际任务状态。`READY` 表示还需要下一轮；`WAITING_CAPABILITY` 表示当前需要的执行能力没有通过探测，断点与原因已保留；`DONE` 表示所需独立验收已通过。进程正常退出只表示本次调用结束，项目完成需核对任务状态和验收证据。

## 接受结果

模型退出成功后，控制器仍运行 `acceptance` 命令。验收失败时把证据反馈下一轮修复；重复失败或无进展触发路线熔断。模型请求、实际代码修改和项目验收分别记录，全部所需验收通过才为 DONE。

跨进程恢复依赖持久控制仓库、快照、项目源提交和输出指纹。非幂等且没有受支持恢复语义的中断操作先保留明确断点，不盲目重放副作用。
