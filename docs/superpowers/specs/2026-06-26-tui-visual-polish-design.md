# TUI 视觉优化批次 — 设计 spec

**日期**: 2026-06-26
**范围**: 修复违规 + 扩展 snapshot 锁 + 排版精修 + 交互反馈增强
**spec 依据**: `docs/tui-design-language.md` §2.2 (motion), §4.3.1 (Header), §6 (instant), §8.1 (no hardcoded color names)

---

## 设计决策汇总

| 步骤 | 决策 | 选择 |
|---|---|---|
| 1A | diff_pane token 化 | 语义 token `$warning/$success/$error` |
| 1B | Header hairline | ChatLog `border-top: solid $hairline` |
| 1C | snapshot baseline 扩展 | StatusBar + ChatLog 交互 + 全屏模态 |
| 2 | 排版间距 | 统一 padding `0 2` |
| 3 | 交互反馈 | C-1 error 强化 + C-2 section 分隔 + C-3 展开背景 |

---

## 1A. diff_pane.py token 化

**问题**: `loom/tui/diff_pane.py:36,38,40` 硬编码 `[yellow]/[green]/[red]` Rich 标签，违反 spec §8.1「无字面色名」规则。

**方案**: 替换为语义 token：
- `@@` hunk header → `[$warning]`
- `+` added → `[$success]`
- `-` removed → `[$error]`

**视觉影响**: 零。`$warning=#8a7a3b`、`$success=#4a8a5b`、`$error=#8a3b3b` 与当前硬编码的 yellow/green/red 在 loom-ink 主题下完全一致。

**改动文件**: `loom/tui/diff_pane.py`（3 行）

---

## 1B. Header 底部 hairline

**问题**: spec §4.3.1 要求 Header 底部 hairline 分隔，但 Textual 在 `height:1` widget 上 `border-bottom` 会吃掉唯一内容行。当前用 `$panel` 背景反差妥协，反差太弱。

**方案**: 在 `AgentTUIApp.CSS` 的 `#chat-log` 规则加 `border-top: solid $hairline`。
- Header 保持 `height:1` 不变（不碰 Textual 限制）
- hairline 画在 ChatLog 顶部，视觉上等同于 Header 底部 hairline
- `$hairline` token (`#1a1e24`) 已在主题中定义

**改动文件**: `loom/tui/app.py`（CSS 1 行）+ 删除 `loom/tui/header.py:493-497` 的 NOTE 注释

---

## 1C. snapshot baseline 扩展

**问题**: 当前仅 8 个 snapshot baseline（Header 6 + empty layout 1 + permission modal 1），未覆盖大多数屏幕。任何颜色/间距改动无法被 CI 捕获回归。

**方案**: 新增 snapshot baseline 覆盖以下屏幕：

### StatusBar 各状态（3 个）
- `test_status_bar_idle` — engine_state=idle
- `test_status_bar_streaming` — engine_state=streaming（shuttle 动画在第 2 帧）
- `test_status_bar_error` — engine_state=error

### ChatLog 交互状态（3 个）
- `test_chatlog_streaming_with_thinking` — StreamingOverlay + ThinkingDisplay 展开
- `test_chatlog_tool_call_collapsed` — ToolCallMarker 收起状态
- `test_chatlog_tool_call_expanded` — ToolCallMarker 展开状态

### 全屏模态（3 个）
- `test_welcome_banner` — 启动时 WelcomeBanner 显示
- `test_command_palette_open` — CommandPaletteModal 打开
- `test_model_picker_open` — ModelPicker 打开（mock 仅 anthropic 连接，避免超时）

**改动文件**: `tests/test_tui_snapshot.py`（新增 9 个 test 函数）+ 9 个新 `.raw` baseline 文件

**模式**: 遵循现有 `snap_compare(app, run_before=..., terminal_size=(120, 40))` 模式。ModelPicker 测试用 `side_effect` mock 限制只 anthropic 连接（参考 `tests/test_provider_status_indicator.py` 的 fix）。

---

## 2. 排版与间距精修

**问题**: ChatLog 各消息类型 padding/margin 不一致：
- `UserMessage` padding `1 2`（胖 2 行）vs `AssistantMessage` padding `0 2`（瘦）
- `AssistantSummary` padding `0 0 0 2`（左缩进与其他不齐）

**方案**: 统一所有消息类型 padding 为 `0 2`：
- `UserMessage`: padding `1 2` → `0 2`
- `AssistantMessage`: 保持 `0 2`
- `StreamingOverlay`: 保持 `0 2`
- `SystemNote`: 保持 `0 2`
- `AssistantSummary`: padding `0 0 0 2` → `0 2`
- `TurnLabel`: margin `1 0 0 0` 保持（顶部留白做 turn 分隔）
- `TurnSeparator`: 保留（spec §4.1 component #16，有独立语义）

**视觉影响**: ChatLog 整体高度略减（每条 user message 省 2 行），视觉重心更均衡。在 1C 的 snapshot 保护下，任何回归立即可见。

**改动文件**: `loom/tui/chat_log.py`（2 处 CSS：UserMessage + AssistantSummary）

**附加**: Composer 焦点态 `focus-within: $boost 5%` → `8%`（更明显）。改动文件：`loom/tui/app.py` CSS。

---

## 3. 交互反馈增强

**spec §6 合规**: 所有增强都是静态视觉，零动画。流式光标已从清单移除（spec §2.2.2 禁止 per-character typewriter animation）。

### C-1. error 状态视觉强化
**问题**: SystemNote severity=error 仅文字色 `$error`，与 warning 区分不明显。

**方案**: severity=error 时：
- 背景色 `$error 10%`（半透明红）
- 左色边框 `border-left: outer $error`
- 前缀 icon `✗`（替代当前的 `·`）
- warning 保持现状（仅文字色，避免过度强调非错误状态）

**改动文件**: `loom/tui/chat_log.py`（`append_system_note` 方法 + `SystemNote` CSS 加 error 变体）

### C-2. Header section 分隔符
**问题**: 3 个 HeaderSectionButton 之间仅靠 `border-left: solid $border` 分隔，视觉太弱。

**方案**: 两个选择（实施时验证哪个视觉效果更好）：
- 选项 1：保留 `border-left: solid $border`，但把颜色从 `$border` 改为 `$hairline` 更深的 token
- 选项 2：移除 `border-left`，在非首个 button 的 render 文本前加 `│` 字符（`$border` 色）

两个选项都保持 `width: 1fr` 布局不变。实施时用 snapshot 对比决定。

**改动文件**: `loom/tui/header.py`（`HeaderSectionButton._render_section` 或 CSS）

### C-3. ToolCallMarker 展开内容背景
**问题**: `CollapsibleToolOutput` 已有 `background: $surface`，但 `toggle()` 只翻转 `display` 布尔值，不区分展开/收起的视觉状态。展开时内容区与周围 ChatLog 背景对比不够明显。

**方案**: `CollapsibleToolOutput.toggle()` 翻转 `display` 时同步加/移 `expanded` CSS class，展开时强化视觉：
- 加 `border-left: outer $accent-dim`（与 AssistantMessage 一致的左色边框）
- 背景从 `$surface` 改为 `$surface 90%`（略深，展开时更突出）

**改动文件**: `loom/tui/chat_log.py`（`CollapsibleToolOutput.toggle()` + CSS 加 `.expanded` 变体）

---

## 验证策略

每个步骤独立可提交，验证命令：

```bash
# 步骤 1A + 1B + 2 + 3（代码改动）
scripts/verify-quick.sh  # 快速验证匹配的测试

# 步骤 1C（snapshot 新增）
uv run pytest tests/test_tui_snapshot.py -v
# 首次运行用 --snapshot-update 建立 baseline，后续运行验证

# 完整验证（closeout）
./init.sh
uv run python -m loom.cli eval --kind harness
```

---

## 不在范围内

- 流式光标（spec §2.2.2 禁止）
- 任何 fade/slide/typewriter 动画（spec §6 禁止）
- HTML mockup state 6/7 与 per-section toggle 同步（单独的 docs 任务，非代码改动）
- diff_pane placeholder 完善（spec 附录 A 提到的 `loom/tui/widgets.py` stale 引用，单独清理）

---

## 实施顺序

1. **1A** diff_pane token 化（3 行，5 分钟）
2. **1B** Header hairline（CSS 1 行 + 删 NOTE，5 分钟）
3. **2** 排版间距统一（2 处 CSS + Composer 焦点，10 分钟）
4. **3** 交互反馈（C-1/C-2/C-3，30 分钟）
5. **1C** snapshot baseline 扩展（9 个新测试，30 分钟）
6. 完整验证 + 提交

每步独立提交，便于回滚。
