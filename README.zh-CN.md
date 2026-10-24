# CodexCloudKit｜Codex 云端开发加速器

**一次部署，跨项目复用，让 Codex 更快进入开发状态。**

把共享工具、技能、项目记忆和执行恢复流程放进云端开发环境。业务仓库保留自己的代码、规则与依赖；通用运行环境由工具箱集中准备。

[English](README.md) · [快速开始](docs/QUICKSTART_ZH.md) · [云端部署](docs/CLOUD_DEPLOYMENT_ZH.md) · [完整文档](docs/INDEX_ZH.md)

## 核心工作流

1. 在云端安装轻量核心并准备共享运行目录。
2. 接入业务仓库，保留项目规则并生成共享技能引用。
3. 读取目标和持久断点，按需召回经过验证的项目记忆。
4. 调用实际执行后端，独立运行构建、功能和验收命令。
5. 根据证据修复、恢复或切换路线，验收通过后停止该目标续派。

```bash
python3 -m venv /workspace/external-tools/codexcloudkit-venv
/workspace/external-tools/codexcloudkit-venv/bin/python -m pip install .
/workspace/external-tools/codexcloudkit-venv/bin/cloudkit setup   --runtime-root /workspace/external-tools/codexcloudkit
/workspace/external-tools/codexcloudkit-venv/bin/cloudkit attach   --project-root /workspace/business-project   --runtime-root /workspace/external-tools/codexcloudkit   --state-dir /workspace/persistent-memory/cloudkit
```

## 共享什么，项目保留什么

| 共享层 | 业务项目 |
| --- | --- |
| 通用技能、工具入口、运行收据 | 自己的源码和依赖锁 |
| 内容哈希与增量清单机制 | 独立项目身份、分支和目标 |
| 记忆协议和执行器接入 | 独立记忆、断点与验收证据 |

核心默认不下载完整语言生态或大型二进制套件。七项可选配置覆盖 core、memory、queue、languages、reverse、design 和 code-intelligence。RQ 仅递送执行事件，Git 保持唯一任务状态。需要的工具、模型执行后端和正式记忆协议由任务配置，并分别做实际调用验证。

## 文档入口

- [快速开始](docs/QUICKSTART_ZH.md)
- [当前 Codex Cloud、发布与启动](docs/CLOUD_DEPLOYMENT_ZH.md)
- [自建云 Runner](docs/SELF_HOSTED_RUNNER_ZH.md)
- [新仓库接入](docs/NEW_REPOSITORY_ZH.md)
- [记忆、分类与恢复](docs/MEMORY_AND_RECOVERY_ZH.md)
- [配置与命令](docs/CONFIGURATION_ZH.md)
- [语言、逆向、图表与代码智能](docs/TOOLCHAINS_ZH.md)
- [多 Agent 与目标闭环](docs/AGENT_WORKFLOWS_ZH.md)
- [目标计划与执行命令](docs/EXECUTION_ZH.md)
- [可选 Redis/RQ 队列](docs/QUEUE_ZH.md)
- [执行器与事件工具选型](docs/EXECUTOR_CHOICES_ZH.md)
- [技能、工具与 MCP](docs/SKILLS_AND_TOOLS_ZH.md)
- [升级、故障与清理](docs/OPERATIONS_ZH.md)
- [实际验收与复验](docs/ACCEPTANCE_ZH.md)

无人值守执行需要真实可用的后端、认证、持久状态和唤醒路径。项目的验收文档分别报告调度器子进程测试、真实模型请求与业务功能测试。
