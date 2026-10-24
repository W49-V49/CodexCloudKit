# 可选工具配置

核心默认只准备共享技能与通用命令。需要时通过 `setup --profile` 安装固定版本工具，安装收据与依赖保存在 `--runtime-root` 指定的共享目录中。

| Profile | 内容 | 运行条件 |
| --- | --- | --- |
| `core` | 共享技能、接入、doctor 与增量清单 | Python 3.11+ |
| `memory` | Basic Memory 0.24.1、固定离线嵌入与正式协议 | Python 3.12+ |
| `queue` | RQ 2.12.0、redis 客户端与固定依赖 | Python 3.11+；另需实际 Redis/Valkey 服务 |
| `languages` | 语言编译器、格式化/检查工具与编辑器配置 | Linux x86_64、Python 3.12+、Node/npm/uv |
| `reverse` | Ghidra 12.2、Rizin 0.9.1、hexyl 0.17.0 及静态分析工具 | Linux x86_64、Python 3.12+、uv；自动准备所需 JDK |
| `design` | 图表检查、数据导入与 SVG/PNG 导出入口 | Linux x86_64、Python 3.12+；准备固定 Playwright 与兼容 Chromium |
| `code-intelligence` | 结构化代码图、语义查询与可选输出处理 | Linux x86_64、Python 3.12+、Node/npm/uv；复用 memory 配置 |

## 安装

```bash
cloudkit setup --runtime-root /workspace/external-tools/codexcloudkit --profile memory
cloudkit setup --runtime-root /workspace/external-tools/codexcloudkit --profile queue
cloudkit setup --runtime-root /workspace/external-tools/codexcloudkit --profile languages
cloudkit setup --runtime-root /workspace/external-tools/codexcloudkit --profile reverse
cloudkit setup --runtime-root /workspace/external-tools/codexcloudkit --profile design
cloudkit setup --runtime-root /workspace/external-tools/codexcloudkit --profile code-intelligence
```

这些是分别选择的配置，不要求每个任务全装。重复安装核对受管收据和包哈希，保留用户定制。完整安装包含语言包、JDK、Ghidra、模型和缓存，应先核对磁盘；低配环境可以只准备核心和当前任务需要的配置。

七项 profile 各自准备对应功能。`queue` 仅安装客户端和唤醒桥，不启动 Redis，也不替换正式记忆。实际服务和 worker 的部署见[RQ 队列](QUEUE_ZH.md)。

## 语言与编辑器

```bash
/workspace/external-tools/codexcloudkit/languages/language-tools run -- go version
/workspace/external-tools/codexcloudkit/languages/language-tools run -- rustc --version
/workspace/external-tools/codexcloudkit/languages/language-tools run -- nvim \
  -u /workspace/external-tools/codexcloudkit/languages/nvim-init.lua
```

使用 `language-tools run` 配置对应运行路径及缓存。裸 `nvim` 不会自动加载这份配置。业务项目仍按自己的依赖锁安装应用包。

## 二进制静态分析

```bash
/workspace/external-tools/codexcloudkit/reverse/bin/reverse-tools inventory
```

分析时显式给出输入与输出目录。需要反编译的步骤使用 headless Ghidra 与所需 JDK。不同架构与文件格式按目标工具的实际支持验证。

## 图表与代码智能

安装后的入口分别为 `design/bin/design-tools` 和 `code-intelligence/bin/codegraph-tools`。图表 SVG 与数据导入不依赖浏览器；design 配置准备固定 Playwright 1.64.1，并复用可用系统 Chromium 或下载受管 Chromium，供 PNG 导出和浏览器验证使用。代码图按项目与分支保存派生状态，不覆盖正式事实记忆。

可选 `context-output` 用于显式选择的短期工具输出处理，不自动捕获全部会话，也不取代正式记忆。

## 安装与功能验收

安装收据记录固定来源、文件哈希和准备结果；安装成功后仍需验证实际功能。语言配置运行编译、lint、格式化和 LSP；逆向配置核对真实良性夹具的解析与反编译；图表配置生成实际 SVG/PNG；代码智能检查结构查询、增量变化和语义候选。对应实测范围见[验收](ACCEPTANCE_ZH.md)。
