# 快速开始

在云端已检出的 CodexCloudKit 源码目录执行。准备 Python 与可写的 `/workspace`；详细版本见[兼容性](COMPATIBILITY.md)。

## 安装轻量核心

```bash
python3 -m venv /workspace/external-tools/codexcloudkit-venv
/workspace/external-tools/codexcloudkit-venv/bin/python -m pip install .
/workspace/external-tools/codexcloudkit-venv/bin/cloudkit setup   --runtime-root /workspace/external-tools/codexcloudkit
/workspace/external-tools/codexcloudkit-venv/bin/cloudkit doctor   --runtime-root /workspace/external-tools/codexcloudkit
```

`setup` 准备共享核心与技能；`doctor` 返回实际检测的命令与配置状态。重复 `setup` 应复用未变化的受管内容。

## 接入业务仓库

业务仓库已检出在 `/workspace/business-project` 后运行：

```bash
/workspace/external-tools/codexcloudkit-venv/bin/cloudkit attach   --project-root /workspace/business-project   --runtime-root /workspace/external-tools/codexcloudkit   --state-dir /workspace/persistent-memory/cloudkit
```

读取现有 `AGENTS.md` 和生成的 `AGENTS.cloudkit.md`。接入器保留项目内容；再次接入同一配置不会复制整套依赖进项目。

## 建立增量文件清单

```bash
/workspace/external-tools/codexcloudkit-venv/bin/cloudkit index   --project-root /workspace/business-project   --state-dir /workspace/persistent-memory/cloudkit
```

这是内容哈希派生清单，用于发现变化；正式事实记忆由配置的记忆协议管理。第二次无变化调用应报告零变化和已有条目复用。

## 开始任务

给 Codex 一个具体目标、必须通过的验收命令和项目路径，例如：

> 在 business-project 中实现目标功能。先读取项目规则、共享规则、短目标和活动断点，接入已配置记忆协议；需要时使用真实子 Agent。运行项目构建与功能验收，失败则按证据修复，通过后归档结果并写入精选结论。

进一步配置见[执行与 Agent 工作流](AGENT_WORKFLOWS_ZH.md)、[记忆恢复](MEMORY_AND_RECOVERY_ZH.md)与[Cloud 部署](CLOUD_DEPLOYMENT_ZH.md)。
