# 验收与复验

每份验收结果记录源提交、运行环境、测试命令、返回码和证据路径。发布后的工作流结果与源码对应；旧提交通过不自动代表新版本通过。

## 核心功能

| 测试 | 必须证明 |
| --- | --- |
| 安装与 setup | 干净环境可安装；重复准备复用受管内容 |
| attach | 原规则保留；共享技能可读；重复接入稳定 |
| 项目隔离 | 不同 origin/分支的派生状态与正式记忆分开 |
| index | 哈希增量正确；无变化零工作；修改/删除/重命名被识别 |
| 备份恢复 | 文件摘要、分类策略、索引代次与源版本保持 |
| 公开源码扫描 | 发布文件中不包含真实项目记忆、任务日志或认证 |

## 执行核心测试

在已安装当前包的虚拟环境中，从源码根执行：

```bash
/workspace/external-tools/codexcloudkit-venv/bin/python -m unittest discover \
  -s tests -p 'test_*.py' -v
```

## 原生记忆验收

准备 `setup --profile memory` 后，可从源码根用该原生环境执行真实测试脚本：

```bash
PYTHONPATH=src /workspace/external-tools/codexcloudkit/memory/.venv/bin/python \
  scripts/verify_memory.py \
  --runtime-root /workspace/external-tools/codexcloudkit \
  --output /workspace/verification/native-memory.json
```

脚本使用一次性合成事实验证原生检索、重复同步、修改/重命名/删除、会话去重、全新目录恢复、分类策略和项目隔离。它不读取用户正式事实，也不是模型请求。输出的 `scope` 区分同机新目录与真实 hosted Runner。

## 执行器

使用可检查的真实子进程验证：失败、反馈修复、独立验收、持久断点、并发租约、同事件去重、熔断备用及 DONE 停止。合成 worker 的测试结果描述这些执行行为，不会被写成真实模型推理成功。

## 模型与业务

真实后端额外记录客户端版本、实际模型请求、输出、项目修改和独立验收。模型成功返回与项目功能验收分别判断。运行器长期恢复还需验证进程/机器重启后的接续，而不是只保留一个后台 PID。

每个业务仓库仍运行自己的构建、测试和用户验收条件。工具箱功能测试通过只验证共享工具。

## 复验入口

从当前源码根运行发行测试入口，并保存完整返回码与输出。CLI 的命令帮助可直接检查：

```bash
python -m cloudkit --help
python -m cloudkit setup --help
python -m cloudkit attach --help
python -m cloudkit index --help
```

准备后的环境继续按[快速开始](QUICKSTART_ZH.md)复验 setup、doctor、attach 和 index；模型后端按[自建 Runner](SELF_HOSTED_RUNNER_ZH.md)和运行计划的实际配置验证。
