# 执行器与事件工具选型

核验日期：2026-10-24（Asia/Bangkok）。下表依据官方仓库、发行元数据和部署文档。当前选择 RQ 作为可选 Python 唤醒运输；模型执行、项目状态和独立验收继续复用现有控制器。

| 方案 | 当前核验版本/形态 | 合适位置 | 本方案处理 |
| --- | --- | --- | --- |
| Comlink | 4.4.2 | 浏览器或 Node worker 的 postMessage RPC | 未来界面需要日志/代码图计算时再选择；不加入 Python 主执行链 |
| workerize-loader | 2.0.2 | Webpack 模块转 Web Worker | 没有现成 Webpack 页面时不增加构建链 |
| JavaScript Greenlet | 1.1.0 | 将异步函数移到浏览器 Worker | 按具体 UI 需要接入；它不是 Python greenlet |
| Vercel Functions | 托管 Functions；可组合 Queues/Sandbox/World | Webhook，或单独部署的 durable Codex 服务 | 保留可行独立云路线，需要实际项目、部署身份和模型 Gateway 配置 |
| Supabase Edge Functions | 托管 Deno/TypeScript | 短 HTTP 事件与唤醒入口 | 可以传递已有事件，不直接承载完整 Python/JDK/Codex CLI 栈 |
| RQ | 2.12.0 | Python 队列与独立进程 worker | 已作为可选 `queue` 配置接入，只传事件，Git 为唯一任务状态 |
| BullMQ | Node 6.4.1；官方 Python 3.4.3 | Redis 或 PostgreSQL 队列 | 保留适合现有 Node/队列项目的备选，不同时维护第二套主队列 |

旧发布日期不是正式停止维护声明。workerize-loader 和 Greenlet 的 npm/GitHub 最新发布较早，但本轮未见归档或明确 deprecated，故不将其写成已经停止维护。

## 当前选择：可选 RQ

现有主控制器为 Python，RQ 可以用薄桥递送 task/event 标识并启动独立工作进程。收到事件后重新读取 Git 控制状态，复用已有租约、去重、实际模型 probe、验收与失败熔断，再保存下一 outbox。正式记忆不会搬进 Redis。

这减少了新运行时数量；不意味着队列提供模型认证。Redis 故障时已有 GitHub 事件与小时巡检继续作为独立运输路径。配置和真实命令见[RQ 队列](QUEUE_ZH.md)。

## Vercel 的长任务与 durable Codex

不能用旧版函数时长限制推断 Vercel 不能运行长任务。当前 Fluid Compute 文档列出 Hobby 单次最大 300 秒；Pro/Enterprise 通常最大 800 秒，指定 Node/Bun/Python runtime 的单函数配置还提供 1,800 秒扩展 Beta。

官方还提供实验性 `durable-harness/codex`：Codex app-server 和文件系统位于 Sandbox，Functions 驱动会话，Queues 重新投递，World 保存持久会话日志。任务可以跨函数超时和重新部署恢复。

这条路线需要实际 Vercel 项目、Sandbox 关联、部署身份，以及 AI Gateway/OIDC 或 Gateway Key。它是另外一套模型调用与部署配置，不能由现有 ChatGPT 登录或安装一个 Functions SDK 自动推断可用。本轮没有部署该托管服务。

官方依据：<https://vercel.com/docs/agent-harnesses/codex>、<https://vercel.com/docs/functions/configuring-functions/duration>。

## Supabase 事件入口

当前托管 Edge Functions 的官方资源为 256 MB 内存、每请求 CPU 2 秒；Free 墙钟 150 秒、Paid 400 秒，请求空闲 150 秒。`EdgeRuntime.waitUntil` 可继续后台工作，但仍使用该托管资源计时。

因此可以用它接收 Webhook 并唤醒既有控制器，完整 CLI/编译/反编译进程仍交给相应云 worker。自建 Edge Runtime 有自己的主机配置，不套用托管额度结论。实际项目和函数部署身份尚未配置时，只记录这条接入选择。

官方依据：<https://supabase.com/docs/guides/functions/limits>、<https://supabase.com/docs/guides/functions/background-tasks>。

## BullMQ 的适用条件

当前 BullMQ 已有官方 Python 客户端及 PostgreSQL 选项，不能把它写成仅支持 Node 或 Redis。对于已有 BullMQ 服务、Node 业务或明确需要相应队列功能的项目，可以配置独立适配器后验证。

阻塞 Node 主线程会影响锁续期，并可能导致 stalled job 与重投递；应按业务使用独立进程或线程。两个库共用一台 Redis 不构成服务独立的备用。当前包保持一个可选 RQ 运输入口，不为选型比较额外运行第二套队列或并建事实库。

官方依据：<https://docs.bullmq.io/guide/jobs/stalled>。

## 验证范围

浏览器三个库完成版本与适用性核验，未作为服务端依赖安装。Vercel、Supabase 完成当前官方部署资料核对，未声称账号已绑定或服务已上线。RQ 的锁定客户端、真实 Redis 运输和现有控制器分别验收；真实模型请求仍单独记录。
