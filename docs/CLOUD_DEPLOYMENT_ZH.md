# Codex Cloud 部署

本文适用于当前 Codex Cloud 的环境准备、Install script、Start skill 和发布流程。检查日期：2026-10-24。官方环境说明：<https://learn.chatgpt.com/docs/environments/cloud-environments>。

## 当前 Cloud 的工作方式

一个环境描述仓库、工具、依赖和访问设置。准备完成后发布其文件系统，新任务从该发布版本创建独立工作区；已有任务保留自己的工作状态。源码 push、保存配置和发布环境是不同动作。

在环境创建界面选择 CodexCloudKit 与业务仓库，确认它们分别拥有实际访问授权。准备工具箱默认挂载目录为 `/workspace/CodexCloudKit`。

## Install script

```bash
#!/usr/bin/env bash
set -euo pipefail
cd /workspace/CodexCloudKit
python3 -m venv /workspace/external-tools/codexcloudkit-venv
/workspace/external-tools/codexcloudkit-venv/bin/python -m pip install .
/workspace/external-tools/codexcloudkit-venv/bin/cloudkit setup   --runtime-root /workspace/external-tools/codexcloudkit
/workspace/external-tools/codexcloudkit-venv/bin/cloudkit doctor   --runtime-root /workspace/external-tools/codexcloudkit
```

核心默认轻量。业务所需的语言版本、浏览器、数据库与模型后端在实际依赖清单中固定并验证。若环境挂载路径不同，调整这里的工具箱目录和配置中的外部路径。

## Start skill

可以把下列启动说明放进环境的 Start skill：

> 每次任务读取所选业务仓库 AGENTS.md、短目标、验收要求和持久断点。用 cloudkit attach 接入共享运行目录，读取生成的 AGENTS.cloudkit.md。恢复该项目自己的状态与配置的记忆协议，按目标、依赖、历史故障和证据缺口召回。需要时使用真实多层 Agent，并保持共享文件单写入者。执行独立构建、功能和验收；失败后继续修复或切换有效路线。完成后只写回精选结论和证据。

每次进入业务任务执行项目接入；共享目录存在不表示当前项目的进程已经启动。配置变化后重新启动项目绑定的 MCP 或执行后端。

## 普通变量与网络密钥

当前 Cloud 的直接环境变量供程序读取。网络密钥则由程序取得占位值，代理在配置的 HTTPS 目的地替换真实凭据。需要原始进程值的客户端与只需向指定服务发送凭据的客户端应使用相应交付方式。

配置时核对 key、目标域和适用环境；个人 vault 只将请求的个人值交给对应任务。平台 GitHub 连接与新业务仓库授权分别验证。先读仓库元数据，再验证任务实际需要的写操作。

网络密钥的目标域、环境网络设置和平台联网要求分别生效。保持平台代理配置；联网失败先从目标域、代理、交付方式和实际错误诊断。

## 保存、发布与复验

1. 安装并测试共享核心和业务接入。
2. 保存脚本、启动说明、变量和仓库成员配置。
3. 选择 Publish 或 Republish。
4. 从新发布的环境启动一个新任务。
5. 运行 `doctor`、接入业务仓库，并复验实际需要的后端和工具。

后台仓库刷新保留缓存，不会自动重跑安装和启动命令。工具版本或锁变化后，回到环境准备显式安装、测试并重新发布。已存在的任务继续使用自己的工作状态。

## 保存状态与持久数据

按当前官方说明，任务保存状态默认可在最后启动或恢复后的七天内恢复。重要源码应提交到项目版本控制；精选记忆、任务断点和证据应保存到可持久挂载目录或配置的私有控制存储。发布环境保存文件系统，不自动维持后台模型进程的生命周期。

## Legacy 环境

旧入口 <https://learn.chatgpt.com/docs/environments/cloud-environment> 标为 Codex Cloud Legacy，使用 setup script 和可选 maintenance script。其 setup 独立 Bash、setup-only secrets 和最多 12 小时缓存说明属于 Legacy。

当前 Cloud 使用本篇前述 Install script、Start skill、网络密钥与发布文件系统流程。部署时先识别产品页面和配置字段，不混用两套密钥和缓存规则。
