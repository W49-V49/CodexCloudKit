# 接入新的业务仓库

不同项目共享工具箱，但保留自己的源码、依赖、规则、目标、记忆和执行证据。一个仓库可读取，不代表它已经加入当前 Cloud 环境或拥有相同凭据。

## 准备项目

在云环境检出项目，确认真实 Git 根、origin 和分支。项目名称与目录不一致时，使用实际目录作为 `--project-root`。

```bash
/workspace/external-tools/codexcloudkit-venv/bin/cloudkit attach   --project-root /workspace/business-project   --runtime-root /workspace/external-tools/codexcloudkit   --state-dir /workspace/persistent-memory/cloudkit
```

生成的 `AGENTS.cloudkit.md` 与原有 `AGENTS.md` 一起读取。已有规则和技能保持；冲突项从实际返回结果处理，不将项目自有内容改成共享模板。

## 项目启动清单

1. 读取短目标、构建命令、测试命令和用户验收条件。
2. 读取最近持久断点，核对项目身份、分支、源版本和租约。
3. 对配置的记忆协议恢复并做增量同步；首次项目与恢复错误分别处理。
4. 用 `doctor --project-root` 检查实际工具，再从项目清单安装依赖。
5. 按任务需要派发真实 Agent，运行独立验收，保存证据。

## 项目目标与记忆 MCP 断点

使用记忆 MCP 的检索入口前，项目应包含真实短目标 `docs/GOAL.md`，以及与当前分支一致的 `docs/automation/STATE.json`。项目状态至少记录 `project_id`、`work_branch` 或 `branch`、`status`、`goal_id` 和 `next_action`。

检索先读取已缓存控制分支的状态；没有相应缓存时读取项目内这份短断点。控制仓库与业务仓库分开时，按项目同步必要的短目标和断点字段，完整控制状态仍保留在私有存储。分支切换后更新分支绑定，不把另一个目标的状态当作当前证据。

## 重复接入与切换

同一配置重复 attach 应得到相同结果和可复用的共享链接。项目自己的依赖目录不移动到别的项目。切换业务仓库后重启与项目绑定的后端或 MCP；进程保留旧 cwd 时，不假定它已经服务于新项目。

新 Cloud 环境需要重新安装共享核心并配置访问。新业务仓库复用配置与固定版本，但不会自动继承其他环境的任务文件或个人连接。
