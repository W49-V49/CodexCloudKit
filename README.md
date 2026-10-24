# CodexCloudKit

**Codex cloud development accelerator**

Accelerate cloud development with reusable tools, skills, memory, and multi-agent workflows.

Prepare a shared runtime once, attach it to different business repositories, and continue from durable project state. CodexCloudKit keeps project rules and application dependencies with each project while shared tooling remains outside the source checkout.

[中文](README.zh-CN.md) · [Quick start](docs/QUICKSTART_ZH.md) · [Cloud deployment](docs/CLOUD_DEPLOYMENT_ZH.md) · [Documentation](docs/INDEX_ZH.md)

## Start from a checkout

```bash
python3 -m venv /workspace/external-tools/codexcloudkit-venv
/workspace/external-tools/codexcloudkit-venv/bin/python -m pip install .
/workspace/external-tools/codexcloudkit-venv/bin/cloudkit setup   --runtime-root /workspace/external-tools/codexcloudkit
/workspace/external-tools/codexcloudkit-venv/bin/cloudkit doctor   --runtime-root /workspace/external-tools/codexcloudkit
```

The core preparation uses a small shared runtime. Project-specific language packages, browser services and model backends are configured for the task that needs them.

## What it does

| Capability | Behavior |
| --- | --- |
| Shared setup | Prepare reusable skills and a versioned runtime receipt |
| Project attachment | Preserve project rules and attach shared guidance with repeatable links |
| Incremental inventory | Compare content hashes and reuse unchanged entries |
| Memory integration | Built-in protocol with an optional memory profile and project-bound operations |
| Execution integration | Built-in supervisor with configured workers and independent acceptance commands |
| Recovery | Keep runtime state outside public source and resume saved work |

The configured model backend supplies real model execution. Supervisor subprocess fixtures, model calls and business acceptance are measured separately in [verification](docs/ACCEPTANCE_ZH.md).

## Attach a business repository

```bash
/workspace/external-tools/codexcloudkit-venv/bin/cloudkit attach   --project-root /workspace/business-project   --runtime-root /workspace/external-tools/codexcloudkit   --state-dir /workspace/persistent-memory/cloudkit
```

Read the project's existing `AGENTS.md` together with the generated `AGENTS.cloudkit.md`. Start the task with a concrete goal and acceptance commands. [New repository workflow](docs/NEW_REPOSITORY_ZH.md).

## Deploy and operate

- [Current Codex Cloud and Legacy setup](docs/CLOUD_DEPLOYMENT_ZH.md)
- [Self-hosted cloud Runner](docs/SELF_HOSTED_RUNNER_ZH.md)
- [Configuration and CLI](docs/CONFIGURATION_ZH.md)
- [Optional language, reverse, design and code tools](docs/TOOLCHAINS_ZH.md)
- [Curated memory and recovery](docs/MEMORY_AND_RECOVERY_ZH.md)
- [Agent workflows](docs/AGENT_WORKFLOWS_ZH.md)
- [Worker plans and execution](docs/EXECUTION_ZH.md)
- [Skills and MCP](docs/SKILLS_AND_TOOLS_ZH.md)
- [Operations and troubleshooting](docs/OPERATIONS_ZH.md)
- [Architecture](docs/ARCHITECTURE_ZH.md)

See [compatibility](docs/COMPATIBILITY.md) for the checked environment and [acceptance](docs/ACCEPTANCE_ZH.md) for reproducible tests.
