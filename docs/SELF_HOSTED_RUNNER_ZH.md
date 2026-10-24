# 自建云端 Linux Runner

在可持久保存数据的 Linux 云服务器或容器中运行工具箱。工作目录、认证与服务生命周期由该云环境提供；不需要用户电脑参与。

## 安装核心

将发行源码检出到 `/workspace/CodexCloudKit`，准备符合项目要求的 Python，再执行：

```bash
cd /workspace/CodexCloudKit
python3 -m venv /workspace/external-tools/codexcloudkit-venv
/workspace/external-tools/codexcloudkit-venv/bin/python -m pip install .
/workspace/external-tools/codexcloudkit-venv/bin/cloudkit setup   --runtime-root /workspace/external-tools/codexcloudkit
/workspace/external-tools/codexcloudkit-venv/bin/cloudkit doctor   --runtime-root /workspace/external-tools/codexcloudkit
```

将共享 runtime 与 state 目录放在需要保留的云磁盘上。业务仓库分别检出并 attach。安装核心不自动建立 Codex 登录、模型服务或数据库。

## 准备私有控制存储

将有写权限的私有控制仓库检出到独立目录，按[执行计划文档](EXECUTION_ZH.md)运行 `cloudkit run -- --repository ... init`。初始化支持空私有远端，并在已有状态存在时保留它。业务源码和控制存储分开检出，持续任务从该持久分支恢复。

## 配置实际执行后端

1. 安装与任务兼容的 Codex 客户端或其他明确配置的 worker。
2. 使用该云环境支持的官方登录或凭据交付方式。
3. 运行一次实际模型请求，保存版本、返回码和模型输出证据。
4. 用目标项目的独立验收命令验证后端产生的修改。
5. 通过恢复测试后配置服务管理器或运行平台的持续调度。

后端应有明确 argv、工作目录、超时、输出路径和备用路线。模型进程退出成功后，仍由独立验收命令决定项目是否完成。

## 持久服务与巡检

持续工作需要可运行的 worker、可用认证、持久状态、事件续派和周期巡检。把可恢复 supervisor 交给云服务管理器运行，而不是依赖聊天会话中的未托管后台进程。

预计超过一小时的任务先保存目标、断点和验收版本，再建立即时下一步触发及小时巡检。每次启动恢复现有任务，已通过的步骤从证据验证后复用；DONE 目标停止继续派发。

## 启用事件续派与小时巡检

发行包提供 [examples/autonomy.yml](../examples/autonomy.yml)。把它部署到**私有控制仓库**的默认分支，文件名保持 `.github/workflows/autonomy.yml`，供内置续派器查找。

```bash
cd /workspace/private-control
mkdir -p .github/workflows
cp /workspace/CodexCloudKit/examples/autonomy.yml .github/workflows/autonomy.yml
```

将文件提交并推送到这个控制仓库的实际默认分支。若控制仓库先在空远端初始化，不要假定默认分支一定叫 main；先在 GitHub 仓库设置中核对。

配置两项控制仓库 Actions variables：

| 变量 | 值 |
| --- | --- |
| `CLOUDKIT_SOURCE_REPOSITORY` | 可读取的工具箱发行仓库，格式为 owner/repository |
| `CLOUDKIT_SOURCE_REF` | 经过验收的完整工具箱发行提交 SHA |

模板使用标签 `[self-hosted, linux, x64, cloudkit]`。将云端 Linux x86_64 Runner 注册到该私有控制仓库并添加 `cloudkit` 标签；Runner 需具备 Python、Git、实际业务依赖和可工作的执行后端。

在启用持续任务前，用**该 Runner 运行账号**已有的官方 Codex 登录完成真实模型请求，并通过任务配置的 `probe_command` challenge 检查。版本输出不能替代模型探测。保持此 Runner 已验证的认证状态，无需从其他环境复制登录文件。

随后注册计划，从控制仓库 Actions 页面手动运行一次 `CloudKit task continuation`，填写实际任务 ID。模板支持 `workflow_dispatch`、备用 `repository_dispatch` 和 `17 * * * *` 小时巡检。每轮先持久化结果与 outbox，再请求下一轮；DONE 任务停止续派。小时巡检处理到期任务和过期租约，实际开始时间取决于 GitHub 队列。

模板 checkout 私有控制仓库和指定的工具箱发行版，在 `RUNNER_TEMP` 中建立独立虚拟环境并安装固定源版本，将该环境加入本轮 PATH，调用 `cloudkit.runtime.continuity tick`，再上传本轮 JSON 结果。模板中的 workflow 权限为 `contents: write`、`actions: write`；访问其他业务仓库还需 Runner 实际具备对应访问能力。

发布模板、注册 Runner 和配置变量不等于已经运行模型。以 Actions 的真实轮次结果、模型探针和项目验收判断持续工作是否建立；当前包中的模板是部署入口，运行状态由目标环境验证。

## 可选 Redis/RQ 即时唤醒

已有持久 Git 控制流程可继续使用上节 GitHub 事件和小时巡检。需要 Python 队列消费者时，额外准备 `setup --profile queue`、可访问 Redis/Valkey 服务，并在独立云进程托管 `cloudkit queue ... worker --poll`。初始化、入队和检查命令见[队列部署](QUEUE_ZH.md)。

队列只运输现有 outbox 事件；每次读取 Git 最新目标、租约和验收，`READY` 继续入队，`DONE` 停止。Redis 失败时保留 Git 状态并切既有 GitHub/小时路线。浏览器 Worker 和托管函数的适用场景见[执行器选型](EXECUTOR_CHOICES_ZH.md)。

## 独立验收层次

| 层次 | 验证内容 |
| --- | --- |
| 核心 CLI | 安装、重复 setup、attach 和 index |
| 执行器 | 失败反馈、修复、验收、断点恢复、熔断备用 |
| 模型后端 | 真实认证、模型输出、实际工具或代码变更 |
| 业务项目 | 构建、行为、测试及明确验收条件 |
| 长期运行 | 服务重启、过期租约、丢失连接和跨 Runner 接续 |

当前发行验证结果见[验收文档](ACCEPTANCE_ZH.md)，以对应提交和实测范围为准。
