# 多模型提供商（Multi-Model Providers）

## §1 概述

loom 支持 6 个 LLM 提供商，通过统一的接口切换：

| 提供商 | provider_id | 类型 |
|---|---|---|
| Anthropic | `anthropic` | 原生 SDK |
| OpenAI | `openai` | Chat Completions API |
| DeepSeek | `deepseek` | OpenAI 兼容 |
| Ollama（本地） | `ollama` | OpenAI 兼容 |
| OpenRouter | `openrouter` | OpenAI 兼容 |

默认提供商是 Anthropic，保持向后兼容。所有提供商共享相同的流式接口、工具调用格式和错误处理。你只需要切换模型名称，就可以在不同提供商之间移动。

模型 ID 的格式是 `provider_id/model_name`，例如 `anthropic/claude-sonnet-4-5` 或 `openai/gpt-4o`。为了向后兼容，裸写模型名（不加前缀）会自动加上 `anthropic/` 前缀。

---

## §2 快速开始

三步上手多模型：

**第一步：保存 API 密钥**

```bash
loom auth login anthropic
# 会提示你输入密钥，存储到 ~/.loom/auth.json
```

也可以直接设置环境变量：

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
# 或者
export OPENAI_API_KEY="sk-..."
export DEEPSEEK_API_KEY="sk-..."
```

**第二步：查看可用模型**

```bash
loom models                    # 列出所有提供商支持的模型
loom models --verbose          # 带定价信息
loom models openai             # 只看 OpenAI 的模型
```

**第三步：用指定模型启动**

```bash
# 命令行指定模型
loom run --model deepseek/deepseek-chat
loom run --model openai/gpt-4o
loom run --model ollama/llama3

# TUI 启动后，输入 /model 命令切换
/model openai/gpt-4o
/model anthropic/claude-sonnet-4-5
```

---

## §3 提供商列表

| provider_id | 环境变量 | 默认 API 地址 | 默认模型 |
|---|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` | `https://api.anthropic.com` | `claude-sonnet-4-5` |
| `openai` | `OPENAI_API_KEY` | `https://api.openai.com/v1` | `gpt-4o` |
| `deepseek` | `DEEPSEEK_API_KEY` | `https://api.deepseek.com/v1` | `deepseek-chat` |
| `ollama` | `OLLAMA_API_KEY`（可选） | `http://localhost:11434/v1` | `llama3` |
| `openrouter` | `OPENROUTER_API_KEY` | `https://openrouter.ai/api/v1` | `anthropic/claude-3.5-sonnet` |

**注意**：Anthropic 提供商的 `default_base_url` 在代码中为 None，实际会从 `ANTHROPIC_BASE_URL` 环境变量读取，兜底值为 `https://api.deepseek.com/anthropic`（兼容 loom 早期版本的 DeepSeek 代理地址）。如果你要直连 Anthropic 官方 API，请设置 `ANTHROPIC_BASE_URL=https://api.anthropic.com`。

**模型 ID 示例**：

- `anthropic/claude-sonnet-4-5` — Anthropic Claude Sonnet
- `anthropic/claude-opus-4-1` — Anthropic Claude Opus
- `openai/gpt-4o` — OpenAI GPT-4o
- `openai/o3-mini` — OpenAI o3-mini
- `deepseek/deepseek-chat` — DeepSeek V3 对话模型
- `deepseek/deepseek-reasoner` — DeepSeek R1 推理模型
- `ollama/llama3` — Ollama 本地 Llama 3
- `ollama/qwen2.5` — Ollama 本地 Qwen 2.5
- `openrouter/anthropic/claude-3.5-sonnet` — 通过 OpenRouter 调用

**各提供商支持的完整模型列表**：

Anthropic 原生支持：`claude-sonnet-4-5`、`claude-opus-4-1`、`claude-haiku-3-5`、`claude-haiku-4-5`。此外还保留了两个 DeepSeek 模型作为向后兼容入口：`deepseek-v4-flash` 和 `deepseek-v4-pro`（通过 DeepSeek 的 Anthropic 兼容 API 调用）。

OpenAI 支持：`gpt-4o`、`gpt-4o-mini`、`gpt-4-turbo`、`gpt-3.5-turbo`、`o1`、`o1-mini`、`o3-mini`。

DeepSeek 支持：`deepseek-chat`（V3 对话模型，输入 $0.27/百万 token，输出 $1.10/百万 token）、`deepseek-reasoner`（R1 推理模型，输出 $2.19/百万 token）。

Ollama 支持：`llama3`（8K 上下文）、`llama3.1`（128K 上下文）、`qwen2.5`（32K 上下文）、`mistral`（32K 上下文）、`codellama`（16K 上下文）。本地推理免费。

OpenRouter 支持：`anthropic/claude-3.5-sonnet`、`openai/gpt-4o`、`google/gemini-pro`、`meta-llama/llama-3.1-405b-instruct`。价格需查阅 openrouter.ai 最新定价。

---

## §4 凭据存储

loom 使用四层优先级查找 API 密钥（从高到低）：

1. **系统密钥环（OS keyring）** — 最高优先级。需要安装 `keyring` 和 `keyrings.alt` 包。可在代码中通过 `use_keyring=False` 关闭。
2. **`LOOM_AUTH_CONTENT` 环境变量** — JSON 格式，用于子代理凭据继承：
   ```json
   {"anthropic": {"api_key": "sk-ant-..."}, "openai": {"api_key": "sk-..."}}
   ```
   父代理自动设置此变量，子代理无需额外配置。
3. **提供商专属环境变量** — `ANTHROPIC_API_KEY`、`OPENAI_API_KEY`、`DEEPSEEK_API_KEY` 等。
4. **`~/.loom/auth.json` 文件** — 持久化存储，通过 `loom auth login` 写入。

文件存储的安全保障：

- 文件权限为 `chmod 600`（仅所有者可读写）
- 目录权限为 `chmod 700`
- 写入采用原子操作：先写入临时文件，再 `os.replace` 覆盖
- JSON 解析失败时自动备份损坏文件为 `.bak.<timestamp>.json`

**查看凭据状态**：

```bash
loom auth list
# PROVIDER        SOURCE               KEY (last 4)      BASE URL
# --------------------------------------------------------------------------------
# anthropic       keyring              ...abcd           -
# openai          file                 ...efgh           -
# deepseek        env                  ...ijkl           https://my-proxy.com/v1
```

`SOURCE` 列显示该凭据来自哪个层级（`keyring` / `env` / `file` / `loom_auth_content`）。

**管理命令**：

```bash
loom auth login anthropic                    # 交互式输入密钥
loom auth login openai --base-url https://my-proxy.com/v1  # 自定义地址
loom auth logout openai                      # 删除凭据
```

**关于系统密钥环**：

- OS keyring 是最高优先级层，提供最安全的存储方式
- 无头服务器需要额外安装：`pip install keyring keyrings.alt`
- macOS 上默认使用系统钥匙串（Keychain Access），存储位置为 `login` 钥匙串，服务名为 `loom`
- Linux 上默认使用 Secret Service API（需要 dbus 服务）
- 如果密钥环不可用（例如 Docker 容器或无 dbus 环境），loom 会静默降级到文件存储，不会报错
- 生产环境中可以通过设置 `use_keyring=False` 强制走文件存储，避免对密钥环的依赖
- 所有凭据都存在一个文件里：`~/.loom/auth.json`，格式为 JSON 对象，每个 provider_id 一个 key

---

## §5 模型选择优先级

当你启动 loom 时，系统按以下优先级决定使用哪个模型（从高到低）：

| 优先级 | 来源 | 示例 |
|---|---|---|
| 1 | `--model` CLI 参数 | `loom run --model openai/gpt-4o` |
| 2 | `MODEL` 环境变量 | `export MODEL=openai/gpt-4o` |
| 3 | `.minicode/config.json` `model` 字段 | `{"model": "openai/gpt-4o"}` |
| 4 | `.minicode/state/model.json` default 字段 | TUI 中 `/model` 设置后自动记录 |
| 5 | 首个注册商家的首个模型 | `anthropic/claude-sonnet-4-5`（兜底） |

**切换模型的方法**：

- 命令行启动时：`loom run --model deepseek/deepseek-chat`
- TUI 运行时：输入 `/model deepseek/deepseek-chat` 后回车
- TUI 中也可使用 `/model` 无参数，会弹出选择器界面

`/model` 设置会记录到 `.minicode/state/model.json`，下次启动时自动恢复。最近使用的 10 个模型都会保存在 MRU 列表中。状态文件的结构如下：

```json
{
  "recent": [
    {"provider_id": "anthropic", "model_id": "claude-sonnet-4-5"},
    {"provider_id": "openai", "model_id": "gpt-4o"}
  ],
  "default": "anthropic/claude-sonnet-4-5"
}
```

- `recent` 数组按 MRU 排序（最近使用的排最前），自动去重，最多 10 条
- `default` 字段单独保存用户设置的默认模型，不会因为被挤出 recent 列表而丢失
- 文件写入采用原子操作，JSON 损坏时有自动备份机制

**项目级配置**：

创建 `.minicode/config.json` 文件，写入：

```json
{
  "model": "openai/gpt-4o"
}
```

loom 会从当前目录向上逐级查找此文件（直到 `$HOME`），找到即止。

---

## §6 自定义 OpenAI 兼容提供商

如果你有自建的服务（例如 vllm、TGI、本地代理），可以通过环境变量覆写基础地址，使用 `openai` 提供商：

```bash
# 自定义 vllm 端点
OPENAI_BASE_URL=https://my-vllm.example.com/v1 \
OPENAI_API_KEY=sk-custom-key \
  loom run --model openai/custom-model

# 自定义代理
OPENAI_BASE_URL=http://localhost:8080/v1 \
OPENAI_API_KEY=sk-local \
  loom run --model openai/local-model
```

原理：`openai` 提供商读取 `OPENAI_BASE_URL` 环境变量作为 API 地址，你可以将其指向任何兼容 OpenAI Chat Completions 格式的服务端。

常见的兼容服务包括：

- **vllm**：`OPENAI_BASE_URL=https://your-vllm-endpoint/v1`
- **Text Generation Inference（TGI）**：`OPENAI_BASE_URL=https://your-tgi-endpoint/v1`
- **LocalAI**：`OPENAI_BASE_URL=http://localhost:8080/v1`
- **Azure OpenAI**：`OPENAI_BASE_URL=https://your-resource.openai.azure.com/v1`
- **Together AI**：`OPENAI_BASE_URL=https://api.together.xyz/v1`
- **Groq**：`OPENAI_BASE_URL=https://api.groq.com/openai/v1`

如果需要注册全新的提供商类型（不兼容 OpenAI 格式），可以提 GitHub issue 或直接编辑 `MODEL_PROFILES` 配置（位于 `loom/agent/providers/_openai_shared.py`）。新增提供商只需要提供 provider_id、base_url、env_var 和模型列表，无需手写 provider 类。

---

## §7 常见问题

| 问题 | 解决方法 |
|---|---|
| "No API key found" | 运行 `loom auth login <provider>` 或设置环境变量（如 `export ANTHROPIC_API_KEY=sk-...`） |
| "Context length exceeded" | 换用上下文更大的模型，或减少 prompt 长度 |
| "Unknown provider 'xxx'" | 检查 provider_id 拼写。当前支持的 ID：`anthropic`、`openai`、`deepseek`、`ollama`、`openrouter` |
| OS keyring 不可用 | `pip install keyring keyrings.alt` 安装；无头服务器也可使用文件存储 |
| 模型切换无效 | 确认模型 ID 格式为 `provider_id/model_name`，例如 `openai/gpt-4o` 而不是 `gpt-4o`（裸名称会自动加 `anthropic/` 前缀） |
| 401 认证失败 | 检查 API 密钥是否有效，或运行 `loom auth login <provider>` 重新录入 |
| 流式输出中断 | 检查网络连接；自建服务确保兼容 OpenAI SSE 格式 |
| 定价信息显示 $? | 该模型没有注册定价数据，不影响使用。本地模型（Ollama）免费，OpenRouter 需查阅其网站 |
| `.minicode/config.json` 不生效 | 确认文件在启动目录或上级目录中，格式为 `{"model": "provider_id/model_name"}` |
| LOOM_AUTH_CONTENT 格式错误 | 确保 JSON 格式正确，每个 provider 需包含 `api_key` 字段 |
| 子代理凭据未继承 | 确认父代理设置了 `LOOM_AUTH_CONTENT` 环境变量。loom 会自动处理，如果手动启动子进程需自行设置 |

---

## §8 设计参考

loom 的多模型架构参考了 opencode 的提供商系统（`opencode-dev/packages/llm/src/providers/`）和凭据系统（`opencode-dev/packages/core/src/credential/`）。

Python 实现采用基于 ABC 的抽象基类分发，通过 `@register` 装饰器将各提供商注册到全局 `PROVIDERS` 字典。每个 provider 类只需实现 `stream()` 和 `context_window()` 两个抽象方法，即可接入 agent 循环。

OpenAI 兼容的提供商（DeepSeek、Ollama、OpenRouter）通过 `MODEL_PROFILES` 配置驱动，`register_compatible_profiles()` 在模块加载时自动为每个 profile 生成 `OpenAICompatibleProvider` 的子类并注册。新增一个 OpenAI 兼容提供商只需添加一条 profile 记录，不需要写任何新代码。

凭据系统使用四层优先级链，灵感来自 opencode 的 `auth/index.ts`，但增加了 OS keyring 作为最高优先级层，并通过 `LOOM_AUTH_CONTENT` 环境变量实现子代理凭据继承。ModelState（模型状态持久化）和 ProjectConfig（项目级配置）的设计也借鉴了 opencode 的 `state/` 和 `config/` 模块。

loom 的开源地址：https://github.com/lanf0/loom。欢迎提 issue 讨论新提供商或反馈使用问题。
