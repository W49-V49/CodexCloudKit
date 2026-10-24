# 配置与命令

CodexCloudKit 使用显式路径配置，便于在不同云环境和业务仓库复用。同一组路径应传入安装、接入和恢复操作。

## 路径约定

| 路径 | 示例 | 用途 |
| --- | --- | --- |
| 源码 checkout | `/workspace/CodexCloudKit` | 当前发行源码 |
| CLI 虚拟环境 | `/workspace/external-tools/codexcloudkit-venv` | Python 包安装 |
| `--runtime-root` | `/workspace/external-tools/codexcloudkit` | 共享核心、技能与准备收据 |
| `--state-dir` | `/workspace/persistent-memory/cloudkit` | 项目状态、增量清单和恢复资料 |
| `--project-root` | `/workspace/business-project` | 实际业务仓库 |
| `--protocol` | 内置协议或已有控制器路径 | 覆盖默认正式记忆协议 |
| `--supervisor` | 内置执行器或兼容控制器路径 | 覆盖默认执行控制器 |

把 state 放在需要长期保留的云磁盘上。运行目录和状态目录不必位于源码仓库，也不需要与项目名称相同。

## 核心命令

```bash
cloudkit setup --runtime-root /workspace/external-tools/codexcloudkit
cloudkit doctor --runtime-root /workspace/external-tools/codexcloudkit
cloudkit attach --project-root /workspace/business-project   --runtime-root /workspace/external-tools/codexcloudkit   --state-dir /workspace/persistent-memory/cloudkit
cloudkit index --project-root /workspace/business-project   --state-dir /workspace/persistent-memory/cloudkit
```

安装进隔离虚拟环境时，将 `cloudkit` 换成该环境 `bin/cloudkit` 的绝对路径，或显式启用该虚拟环境。`python -m cloudkit` 提供同一 CLI 入口。

## 准备正式记忆

核心使用标准库；可选 `memory` 配置需要 Python 3.12 或更新版本，并额外准备正式记忆后端、固定依赖和离线嵌入模型：

```bash
cloudkit setup --runtime-root /workspace/external-tools/codexcloudkit --profile memory
cloudkit memory --project-id example/business-project --branch main \
  --runtime-root /workspace/external-tools/codexcloudkit \
  --state-dir /workspace/persistent-memory/cloudkit -- status
```

默认使用发行包内置记忆协议；`--protocol` 可接入已存在的兼容协议。CLI 转交原生协议的真实子命令和返回码。

其他可选 profile 为 `languages`、`reverse`、`design`、`code-intelligence`，具体前置与调用见[可选工具配置](TOOLCHAINS_ZH.md)。

## 可选事件队列

`setup --profile queue` 在共享 runtime 的 `queue/.venv` 准备锁定客户端。通过 `CLOUDKIT_REDIS_URL` 配置实际服务；未设置时桥接默认 `redis://127.0.0.1:16379/0`。

```bash
cloudkit setup --runtime-root /workspace/external-tools/codexcloudkit --profile queue
cloudkit queue --runtime-root /workspace/external-tools/codexcloudkit -- \
  --repository /workspace/private-control --queue cloudkit \
  inspect --task-id project-goal-001
```

`queue` 后的 `--` 分隔 CLI 与桥接参数。桥接全局参数为 `--repository`、`--project-root`、`--queue`、`--ref` 和 `--github-repository`，然后选择 `enqueue`、`worker` 或 `inspect`。完整注册、服务和备用路线见[队列部署](QUEUE_ZH.md)。Git 控制状态继续作为唯一来源。

`CLOUDKIT_RUNS_ROOT` 配置自动克隆恢复的根目录，默认 `/workspace/autonomy-runs`。不同消费者可以使用自己的目录，保持同一控制仓库、项目身份和分支；队列事件不依赖 producer 的本机绝对路径。

## 派生清单导出

```bash
cloudkit index --project-root /workspace/business-project \
  --state-dir /workspace/persistent-memory/cloudkit \
  --export /workspace/verification/content-inventory.json
```

输出包含 `export_sha256`。恢复时提供这份文件及其预期 SHA256：`restore-index --project-root ... --state-dir ... --archive ... --expected-sha256 ...`。它恢复派生文件清单；业务源码与正式记忆各自恢复。

## 持久控制仓库

内置执行器通过 `cloudkit run -- --repository /workspace/private-control` 接收控制仓库参数。先运行 `init --project-id ... --source-sha ...`，再注册计划和推进任务。具体步骤与计划字段见[目标执行](EXECUTION_ZH.md)。

## 命令自检

```bash
cloudkit --help
cloudkit setup --help
cloudkit doctor --help
cloudkit attach --help
cloudkit index --help
cloudkit restore-index --help
cloudkit memory --help
cloudkit mcp --help
cloudkit queue --help
cloudkit run --help
```

记忆和执行器需要对应的依赖及实际可用后端；其路径覆盖用于与已有部署对接。后端细节以当前发行帮助输出和[验收说明](ACCEPTANCE_ZH.md)为准。
