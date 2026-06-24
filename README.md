<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/loom-mark.svg">
    <img src="docs/loom-mark.svg" alt="LoomCode" width="72" height="72">
  </picture>
  <br>
  <strong>LoomCode</strong>
  <br>
  <em>weaving intent into action</em>
</p>

<p align="center">
  <b>一个可靠的、可自行托管的编程 AI 智能体</b><br>
  Python 编写 · 支持多模型 · 内置审查机制 · 完整验证管线
</p>

<p align="center">
  <a href="#quick-start"><kbd>🚀 快速开始</kbd></a>
  <a href="#triangle"><kbd>🔺 三角验证</kbd></a>
  <a href="#harness"><kbd>⚙️ Harness 流程</kbd></a>
  <a href="#capabilities"><kbd>📦 能力</kbd></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.13-blue?logo=python&logoColor=white" alt="Python 3.13">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License MIT">
  <img src="https://img.shields.io/badge/tests-750%2B-brightgreen" alt="Tests">
  <img src="https://img.shields.io/badge/eval-300%2B-orange" alt="Eval Cases">
  <img src="https://img.shields.io/badge/LOC-17k-lightgrey" alt="LOC">
</p>

---

<h2 id="quick-start">🚀 快速开始</h2>

<table>
<tr>
<td>

```bash
# 安装依赖
./init.sh

# 登录 API Key（一次即可）
uv run python -m loom.cli auth login anthropic

# 启动 TUI（推荐）或命令行
uv run python -m loom.cli tui
uv run python -m loom.cli run

# 跑评估套件
uv run python -m loom.cli eval
```

</td>
</tr>
</table>

<br>

---

<h2 id="triangle">🔺 两大核心设计</h2>

<p align="center">
  LoomCode 和其他 AI coding agent 最大的区别不是功能多少，而是两个系统设计。
</p>

<br>

<h3 align="center">一、三角验证架构</h3>

<blockquote>
  大多数 Agent 只有一个模型从头干到尾，没有人验证它做得对不对。
</blockquote>

LoomCode 把单干拆成三个角色互相制衡：

<br>

<pre style="background: #f6f8fa; padding: 16px; border-radius: 8px; line-height: 1.6;">

      你提需求
         │
         ▼
  ┌─────────────────────────────────────┐
  │       Orchestrator（编排者）          │
  │  理解意图，拆解任务，调度 G 和 R       │
  └──────────┬──────────────────────────┘
             │  task( feature_card + scope_envelope )
             ▼
  ┌──────────────────────┐   review()   ┌──────────────────────┐
  │    Generator（生成者）  │ ──────────→ │    Reviewer（审查者）  │
  │                      │              │                      │
  │  · 专注写代码          │              │  · 只能读，不能改代码   │
  │  · 输出 delta_report  │              │  · 三方对账：          │
  │    - 改了哪些文件      │              │    ① 报告 vs git diff │
  │    - 每文件几行        │              │    ② 范围是否越界      │
  │    - 测试结果          │              │    ③ 需求是否做完      │
  │                      │              │    ④ 代码质量          │
  └──────────────────────┘              └──────────┬───────────┘
                                                   │
                                          verdict + feedback_directive
                                                   │
                                                   ▼
                                        Orchestrator 决策
                                           │        │
                                       通过(done)  不通过(精确指令回头改)
                                                   最多3次 → 升级给你

</pre>

<br>

<table>
<tr>
<th>自动触发</th>
<th>安全硬性保护</th>
</tr>
<tr>
<td>

- ✅ 会话结束时自动审查
- ✅ 上下文压缩前自动审查
- 不需要人记得

</td>
<td>

- 🔒 Reviewer **只能读**（工具白名单硬限制）
- 🚫 行数对不上 git diff → **直接拒绝，不走 LLM**
- 🚫 改了范围外文件 → **直接拒绝**
- ⏱ 同一功能最多审 **3 次** → 标记 blocked

</td>
</tr>
</table>

<br>

<h3 align="center" id="harness">二、Harness 工作流程</h3>

<blockquote>
  不靠"模型更聪明"来保证可靠性，而是靠一套系统约束。
</blockquote>

<br>

<table>
<tr>
<th align="center">📋 范围控制</th>
<th align="center">⏳ 生命周期</th>
</tr>
<tr>
<td>

```
feature_list.json 状态机锁定

  not-started
       │
       ▼
  in-progress
       │
       ▼
  review-pending
       │
       ▼
     done

一次只做一个功能 (WIP=1)
```

</td>
<td>

```
SessionStart
  → 加载检查点
  → 恢复上下文

AgentLoop
  → 每一步保存检查点
  → 追踪 Token/费用

PreCompact
  → 压缩前自动审查

SessionEnd
  → 自动审查 + 跑验证
```

</td>
</tr>
<tr>
<th align="center">✅ 验证管线</th>
<th align="center">🔗 连续性</th>
</tr>
<tr>
<td>

```
./init.sh 一键验证
  ├── ruff（代码风格）
  ├── mypy（类型检查）
  ├── pytest（单元测试）
  └── eval 套件（行为验证）

脚本快速验证 ~5s
300+ eval 用例锁核心行为
```

</td>
<td>

```
检查点持久化
  → 崩溃了从上次继续

session-handoff
  → 跨会话无缝衔接

上下文压缩
  → Token 快满时自动摘要
  → 不丢关键信息
```

</td>
</tr>
</table>

<p align="center">
  <b>结果：</b>你可以放心跑几小时的自治任务，回来检查结果，而不是守在旁边盯着。
</p>

<br>

---

<h2 id="capabilities">📦 其他能力</h2>

<br>

<table>
<tr>
<td width="50%">

<h3>🎯 多模型切换</h3>

内置 6 个 Provider：

- Anthropic
- OpenAI
- DeepSeek
- Ollama
- OpenRouter
- 任意 OpenAI 兼容接口

运行时 `/model` 命令随时切换。<br>
API Key 本地加密存储 `~/.loom/auth.json`。

</td>
<td width="50%">

<h3>🛡️ 权限门控</h3>

每次 Agent 要读写文件、执行命令之前：

| 操作 | 说明 |
|---|---|
| **允许** | 放行一次 |
| **拒绝** | 禁止本次操作 |
| **持久化** | 本会话内自动放行同类操作 |

所有操作有日志追踪，不会"偷偷改了你不知道"。

</td>
</tr>
<tr>
<td width="50%">

<h3>🧩 MCP 支持</h3>

支持 Model Context Protocol 服务器，权限三级控制：

| 级别 | 行为 |
|---|---|
| 拒绝 | 不允许 |
| 自动允许 | 放行 |
| 每次询问 | 每次都问 |

</td>
<td width="50%">

<h3>🔌 可编程 Hooks</h3>

四个钩子点，插入自定义逻辑：

- **操作前** — 自动化审批
- **操作后** — 日志记录
- **会话开始** — 初始化
- **会话结束** — 清理/验证

</td>
</tr>
</table>

<br>

---

<h2>⚡ 快速参考</h2>

<table>
<tr>
<th>命令</th>
<th>说明</th>
</tr>
<tr><td><code>uv run python -m loom.cli run</code></td><td>启动命令行 REPL</td></tr>
<tr><td><code>uv run python -m loom.cli tui</code></td><td>启动 TUI 界面</td></tr>
<tr><td><code>uv run python -m loom.cli eval</code></td><td>运行评估套件</td></tr>
<tr><td><code>uv run python -m loom.cli auth login anthropic</code></td><td>配置 API Key</td></tr>
<tr><td><code>uv run python -m loom.cli --help</code></td><td>查看全部命令</td></tr>
<tr><td><code>./init.sh</code></td><td>安装 + 全量验证</td></tr>
<tr><td><code>scripts/verify-quick.sh</code></td><td>快速验证 (~5s)</td></tr>
</table>

<br>

---

<h2>📊 项目规模</h2>

<br>

<p align="center">

| 模块 | 代码行数 |
|:---|---:|
| 核心引擎 | ~5,000 |
| 评估框架 + 用例 | ~9,000 |
| TUI 界面 | ~2,800 |
| 测试 | ~7,500 |
| **总计** | **~17,000** |

</p>

<br>

---

<h2>📖 了解更多</h2>

<br>

<table>
<tr>
<td>

- **🔺 三角协议规范** → [`docs/triangle-protocol.md`](docs/triangle-protocol.md)
- **🏗️ 架构设计** → [`docs/architecture.md`](docs/architecture.md)
- **🔧 工具定义** → [`docs/tools.md`](docs/tools.md)

</td>
<td>

- **🔌 权限与钩子** → [`docs/hooks.md`](docs/hooks.md)
- **📏 上下文管理** → [`docs/context.md`](docs/context.md)
- **🧪 评估框架** → `loom/eval/runner.py`

</td>
<td>

- **🗺️ 路线图** → [`feature_list_roadmap.json`](feature_list_roadmap.json)
- **📋 工作规则** → [`AGENTS.md`](AGENTS.md)
- **📝 更新日志** → [`CHANGELOG.md`](CHANGELOG.md)

</td>
</tr>
</table>

<br>

---

<p align="center">
  <img src="docs/loom-mark.svg" alt="" width="24" height="24">
  <br>
  <em>weaving intent into action</em>
</p>
