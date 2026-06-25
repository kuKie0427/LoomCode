# TUI 视觉优化批次 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 TUI 视觉违规（diff_pane 硬编码色名）、解决 Header hairline 妥协、扩展 snapshot 防回归网、统一排版间距、增强交互反馈（静态视觉，spec §6 合规）。

**Architecture:** 5 个独立可提交的步骤，每步改动量小（1-3 文件）。先做代码改动（1A/1B/2/3），最后做 snapshot 扩展（1C）建立防回归网。所有视觉改动都是静态的，零动画，完全遵守 spec §6 的 5 种运动原语限制。

**Tech Stack:** Python 3.11+, Textual (CSS + widget), pytest-textual-snapshot (snap_compare), loom-ink 主题 token。

**Spec:** `docs/superpowers/specs/2026-06-26-tui-visual-polish-design.md`

**参考文件:**
- 主题 token 真相源: `loom/tui/app.py:70-92` (`_LOOM_INK_THEME`)
- 中央 CSS: `loom/tui/app.py:98-165` (`AgentTUIApp.CSS`)
- Header CSS: `loom/tui/header.py:484-498`
- ChatLog widgets CSS: `loom/tui/chat_log.py:173-605`
- 现有 snapshot 模式: `tests/test_tui_snapshot.py`, `tests/test_tui_header.py:95-175`

---

## Task 1: diff_pane.py token 化（1A）

**Files:**
- Modify: `loom/tui/diff_pane.py:31-43` (`colorize_diff` 函数)

- [ ] **Step 1: 替换硬编码色名为语义 token**

编辑 `loom/tui/diff_pane.py` 的 `colorize_diff` 函数，把 3 处硬编码 Rich 色名替换为 token：

```python
def colorize_diff(diff: str) -> str:
    """Convert a unified diff to Rich markup for Static rendering."""
    out: list[str] = []
    for line in diff.splitlines():
        if _HUNK.match(line):
            out.append(f"[$warning]{line}[/]")
        elif _ADD.match(line):
            out.append(f"[$success]{line}[/]")
        elif _REM.match(line):
            out.append(f"[$error]{line}[/]")
        else:
            out.append(line)
    return "\n".join(out)
```

同时更新文件顶部 docstring 的注释（第 12-16 行），把 `(loom-ink theme)` 后的颜色说明改为 token 引用：

```python
Per-line coloring (loom-ink theme tokens):
  + line ($success) — added
  - line ($error)   — removed
  @@ hunk header ($warning) — context marker
  default (foreground)   — context / unchanged
```

- [ ] **Step 2: 运行 diff_pane 相关测试验证无回归**

Run: `uv run pytest tests/test_diff_pane.py -v 2>/dev/null || uv run pytest tests/ -k diff -v`
Expected: PASS（若没有专门测试，跳到 Step 3）

- [ ] **Step 3: 快速验证**

Run: `scripts/verify-quick.sh`
Expected: PASS（quick script 会自动匹配 diff_pane 相关测试）

- [ ] **Step 4: 提交**

```bash
git add loom/tui/diff_pane.py
git commit -m "fix(tui): diff_pane uses semantic tokens $warning/$success/$error instead of hardcoded [yellow]/[green]/[red]

Spec §8.1 compliance. Hex values identical (zero visual change):
$warning=#8a7a3b, $success=#4a8a5b, $error=#8a3b3b."
```

---

## Task 2: Header 底部 hairline（1B）

**Files:**
- Modify: `loom/tui/app.py:114-124` (`#chat-log` CSS 规则)
- Modify: `loom/tui/header.py:493-497` (删除 NOTE 注释)

- [ ] **Step 1: 在 #chat-log CSS 加 border-top**

编辑 `loom/tui/app.py` 的 `AgentTUIApp.CSS`，在 `#chat-log` 规则块内加一行 `border-top: solid $hairline;`。改动后的规则应该是：

```css
#chat-log {
    height: 1fr;
    background: $background;
    padding: 1 2 0 2;
    border-top: solid $hairline;
    overflow-y: auto;
    overflow-x: hidden;
    scrollbar-background: $background;
    scrollbar-color: $text-muted;
    scrollbar-color-hover: $accent;
    scrollbar-size-vertical: 3;
}
```

- [ ] **Step 2: 删除 header.py 的 NOTE 注释**

编辑 `loom/tui/header.py`，删除 493-497 行的 NOTE 注释块（`/* NOTE: spec §4.3.1 calls for a hairline...`），因为 hairline 现在由 ChatLog border-top 提供，妥协已解决。

- [ ] **Step 3: 运行 Header snapshot 测试验证**

Run: `uv run pytest tests/test_tui_header.py -v`
Expected: 可能有 snapshot 失败（因为新增 hairline 改变了视觉）。如果失败，先看 diff 确认是预期的新 hairline，然后用 `--snapshot-update` 重新 baseline：

```bash
uv run pytest tests/test_tui_header.py -v --snapshot-update
```

- [ ] **Step 4: 运行 empty_layout snapshot 验证**

Run: `uv run pytest tests/test_tui_snapshot.py::test_empty_layout -v`
Expected: 同样可能失败（hairline 出现在 empty layout）。如果失败且 diff 确认是预期 hairline：

```bash
uv run pytest tests/test_tui_snapshot.py::test_empty_layout -v --snapshot-update
```

- [ ] **Step 5: 提交**

```bash
git add loom/tui/app.py loom/tui/header.py tests/__snapshots__/
git commit -m "fix(tui): add Header bottom hairline via #chat-log border-top

Spec §4.3.1 required a hairline but Textual height:1 + border-bottom
collapses content. Hairline now drawn on ChatLog top border (visually
equivalent to Header bottom). \$hairline token (#1a1e24) already defined.

Re-baselined 6 Header snapshots + 1 empty_layout snapshot to include
the new hairline."
```

---

## Task 3: 排版间距统一（步骤 2）

**Files:**
- Modify: `loom/tui/chat_log.py:210-218` (`UserMessage.DEFAULT_CSS`)
- Modify: `loom/tui/chat_log.py:285-291` (`AssistantSummary.DEFAULT_CSS`)
- Modify: `loom/tui/app.py:139-141` (`#chrome:focus-within` CSS)

- [ ] **Step 1: UserMessage padding 1 2 → 0 2**

编辑 `loom/tui/chat_log.py` 的 `UserMessage.DEFAULT_CSS`：

```css
UserMessage {
    background: $surface;
    color: $text;
    padding: 0 2;
    margin: 0 0 1 0;
    border: none;
}
```

- [ ] **Step 2: AssistantSummary padding 0 0 0 2 → 0 2**

编辑 `loom/tui/chat_log.py` 的 `AssistantSummary.DEFAULT_CSS`：

```css
AssistantSummary {
    height: auto;
    padding: 0 2;
    margin: 0 0 1 0;
}
```

- [ ] **Step 3: Composer 焦点态 5% → 8%**

编辑 `loom/tui/app.py` 的 `#chrome:focus-within` 规则（约 139-141 行）：

```css
#chrome:focus-within {
    background: $boost 8%;
}
```

- [ ] **Step 4: 运行 ChatLog 测试验证**

Run: `uv run pytest tests/test_chat_log*.py tests/test_tui_header.py -v`
Expected: 可能 snapshot 失败（padding 改变视觉）。如果失败且 diff 确认是预期改动：

```bash
uv run pytest tests/test_chat_log*.py tests/test_tui_header.py -v --snapshot-update
```

- [ ] **Step 5: 快速验证**

Run: `scripts/verify-quick.sh`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add loom/tui/chat_log.py loom/tui/app.py tests/__snapshots__/
git commit -m "style(tui): unify ChatLog message padding to 0 2 + Composer focus 8%

UserMessage padding 1 2 → 0 2 (matches AssistantMessage, removes 2-line
visual weight imbalance). AssistantSummary padding 0 0 0 2 → 0 2 (left
aligns with other messages). Composer focus-within \$boost 5% → 8% for
stronger focus indication."
```

---

## Task 4: 交互反馈增强 — C-1 error 状态（步骤 3 第一部分）

**Files:**
- Modify: `loom/tui/chat_log.py:266-275` (`SystemNote.DEFAULT_CSS` + 加 error 变体)
- Modify: `loom/tui/chat_log.py:975-985` (`append_system_note` 方法)

- [ ] **Step 1: SystemNote CSS 加 error 变体**

编辑 `loom/tui/chat_log.py` 的 `SystemNote.DEFAULT_CSS`，加 error 状态变体：

```css
SystemNote {
    height: auto;
    color: $text-muted;
    text-style: italic dim;
    padding: 0 2;
    margin: 0 0 1 0;
}
SystemNote.severity-error {
    background: $error 10%;
    color: $text;
    text-style: bold;
    border-left: outer $error;
    padding: 0 2;
}
```

- [ ] **Step 2: append_system_note 加 error icon + CSS class**

编辑 `loom/tui/chat_log.py` 的 `append_system_note` 方法（约 975-985 行），加 error 前缀 icon 和 CSS class：

```python
def append_system_note(self, text: str, severity: str = "info") -> None:
    self._force_flush_stream_buffer()
    self._finalize_streaming()
    self._current_body = None
    token = {
        "info": "text-muted",
        "success": "success",
        "warning": "warning",
        "error": "error",
    }.get(severity, "text-muted")

    if severity == "error":
        prefix = "✗"
        note = SystemNote(f"[${token}]{prefix}[/] [${token}]{text}[/]")
        note.add_class("severity-error")
    else:
        prefix = "·"
        note = SystemNote(f"[$text-muted]{prefix}[/] [${token}]{text}[/]")

    asyncio.create_task(self._mount_async(note))
    if self._sticky:
        self.scroll_end()
```

- [ ] **Step 3: 运行 SystemNote 测试验证**

Run: `uv run pytest tests/test_show_notification.py tests/test_chat_log*.py -v`
Expected: PASS（现有测试不应被破坏，因为 info/warning 路径不变）

- [ ] **Step 4: 快速验证**

Run: `scripts/verify-quick.sh`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add loom/tui/chat_log.py
git commit -m "feat(tui): error SystemNote gains background + left border + ✗ icon

C-1 of interaction feedback batch. severity=error now visually distinct
from warning: \$error 10% background + border-left: outer \$error + ✗
prefix icon. warning/info unchanged (text color only). Spec §6 compliant
(static, no animation)."
```

---

## Task 5: 交互反馈增强 — C-2 Header section 分隔（步骤 3 第二部分）

**Files:**
- Modify: `loom/tui/header.py:283-321` (`HeaderSectionButton.DEFAULT_CSS`)

- [ ] **Step 1: 强化 HeaderSectionButton 的 border-left**

编辑 `loom/tui/header.py` 的 `HeaderSectionButton.DEFAULT_CSS`，把 `border-left: solid $border` 改为更明显的 `border-left: solid $hairline`（$hairline=#1a1e24 比 $border=#1e2328 略深），并加 focus/hover 时的强化：

```css
HeaderSectionButton {
    width: 1fr;
    height: 1;
    background: transparent;
    padding: 0 1;
    border-left: solid $hairline;
}
HeaderSectionButton.first {
    border-left: none;
}
HeaderSectionButton:hover {
    text-style: bold;
    color: $text;
    border-left: solid $accent-dim;
}
HeaderSectionButton:focus {
    background: $boost 5%;
    text-style: bold;
    border-left: solid $accent-dim;
}
HeaderSectionButton.section-hidden {
    visibility: hidden;
    border-left: none;
}
HeaderSectionButton {
    opacity: 1.0;
}
HeaderSectionButton.pulsing {
    opacity: 1.0;
}
HeaderSectionButton.header-btn-active {
    background: $boost 8%;
    text-style: bold;
    color: $text;
}
```

**注意**：实施后用 snapshot 对比验证。如果 `$hairline` 与 `$border` 视觉差异不够明显，回退到选项 2（在 render 文本前加 `│` 字符）。但首选 CSS border 方案，更干净。

- [ ] **Step 2: 运行 Header snapshot 验证**

Run: `uv run pytest tests/test_tui_header.py -v`
Expected: snapshot 可能失败（border 颜色变化）。如果 diff 确认是预期改动：

```bash
uv run pytest tests/test_tui_header.py -v --snapshot-update
```

- [ ] **Step 3: 快速验证**

Run: `scripts/verify-quick.sh`
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add loom/tui/header.py tests/__snapshots__/
git commit -m "style(tui): strengthen HeaderSectionButton dividers — \$hairline + accent-dim on hover/focus

C-2 of interaction feedback batch. border-left color \$border → \$hairline
(slightly deeper). hover/focus states gain \$accent-dim border for
stronger active indication. Re-baselined Header snapshots."
```

---

## Task 6: 交互反馈增强 — C-3 ToolCallMarker 展开背景（步骤 3 第三部分）

**Files:**
- Modify: `loom/tui/chat_log.py:491-505` (`CollapsibleToolOutput.DEFAULT_CSS`)
- Modify: `loom/tui/chat_log.py:515-516` (`CollapsibleToolOutput.toggle` 方法)

- [ ] **Step 1: CollapsibleToolOutput CSS 加 .expanded 变体**

编辑 `loom/tui/chat_log.py` 的 `CollapsibleToolOutput.DEFAULT_CSS`，加展开状态变体：

```css
CollapsibleToolOutput {
    height: auto;
    max-height: 20;
    overflow-y: auto;
    background: $surface;
    padding: 1 2;
    margin: 0 0 1 2;
    border: none;
}
CollapsibleToolOutput.expanded {
    background: $surface 90%;
    border-left: outer $accent-dim;
}
CollapsibleToolOutput > Static {
    height: auto;
}
```

- [ ] **Step 2: toggle() 方法加/移 expanded class**

编辑 `loom/tui/chat_log.py` 的 `CollapsibleToolOutput.toggle` 方法（约 515-516 行）：

```python
def toggle(self) -> None:
    self.display = not self.display
    self.set_class(self.display, "expanded")
```

- [ ] **Step 3: 运行 ToolCallMarker 测试验证**

Run: `uv run pytest tests/test_thinking_marker_click.py tests/test_chat_log*.py -v`
Expected: PASS（toggle 行为不变，只是加了 CSS class）

- [ ] **Step 4: 快速验证**

Run: `scripts/verify-quick.sh`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add loom/tui/chat_log.py
git commit -m "feat(tui): CollapsibleToolOutput expanded state gains background + left border

C-3 of interaction feedback batch. toggle() now adds/removes 'expanded'
CSS class. Expanded state: \$surface 90% (slightly deeper) + border-left:
outer \$accent-dim (matches AssistantMessage). Visually distinguishes
expanded tool output from collapsed. Spec §6 compliant (static)."
```

---

## Task 7: snapshot baseline 扩展（1C）

**Files:**
- Modify: `tests/test_tui_snapshot.py` (新增 9 个 test 函数)
- Create: `tests/__snapshots__/test_tui_snapshot/*.raw` (9 个新 baseline)

- [ ] **Step 1: 新增 StatusBar 三个状态的 snapshot 测试**

在 `tests/test_tui_snapshot.py` 末尾加：

```python
from loom.tui.status_bar import StatusBar


def test_status_bar_idle(snap_compare):
    """StatusBar idle state (engine_state=idle)."""
    app = AgentTUIApp()
    async def run_before(pilot):
        await pilot.pause(0.1)
        pilot.app.engine_state = "idle"
        await pilot.pause(0.1)
    assert snap_compare(app, run_before=run_before, terminal_size=(120, 40))


def test_status_bar_streaming(snap_compare):
    """StatusBar streaming state (engine_state=streaming)."""
    app = AgentTUIApp()
    async def run_before(pilot):
        await pilot.pause(0.1)
        pilot.app.engine_state = "streaming"
        await pilot.pause(0.2)  # let shuttle animate to a frame
    assert snap_compare(app, run_before=run_before, terminal_size=(120, 40))


def test_status_bar_error(snap_compare):
    """StatusBar error state (engine_state=error)."""
    app = AgentTUIApp()
    async def run_before(pilot):
        await pilot.pause(0.1)
        pilot.app.engine_state = "error"
        await pilot.pause(0.1)
    assert snap_compare(app, run_before=run_before, terminal_size=(120, 40))
```

- [ ] **Step 2: 新增 ChatLog 交互状态的 snapshot 测试**

在 `tests/test_tui_snapshot.py` 继续加：

```python
from loom.tui.chat_log import (
    ChatLog,
    StreamingOverlay,
    ThinkingDisplay,
    ToolCallMarker,
    CollapsibleToolOutput,
)


def test_chatlog_streaming_with_thinking(snap_compare):
    """ChatLog with StreamingOverlay + ThinkingDisplay expanded."""
    app = AgentTUIApp()
    async def run_before(pilot):
        await pilot.pause(0.1)
        chat_log = pilot.app.query_one(ChatLog)
        streaming = StreamingOverlay("Generating response...")
        thinking = ThinkingDisplay("Let me analyze this step by step...")
        thinking.display = True
        await chat_log.mount(streaming)
        await chat_log.mount(thinking)
        await pilot.pause(0.1)
    assert snap_compare(app, run_before=run_before, terminal_size=(120, 40))


def test_chatlog_tool_call_collapsed(snap_compare):
    """ChatLog with ToolCallMarker in collapsed state."""
    app = AgentTUIApp()
    async def run_before(pilot):
        await pilot.pause(0.1)
        chat_log = pilot.app.query_one(ChatLog)
        marker = ToolCallMarker("bash", "rm -rf /tmp/foo", tool_input={"command": "rm -rf /tmp/foo"})
        marker.set_complete("exit 0", is_error=False)
        output = CollapsibleToolOutput("removed /tmp/foo")
        output.display = False
        marker.set_output_widget(output)
        await chat_log.mount(marker)
        await chat_log.mount(output)
        await pilot.pause(0.1)
    assert snap_compare(app, run_before=run_before, terminal_size=(120, 40))


def test_chatlog_tool_call_expanded(snap_compare):
    """ChatLog with ToolCallMarker in expanded state."""
    app = AgentTUIApp()
    async def run_before(pilot):
        await pilot.pause(0.1)
        chat_log = pilot.app.query_one(ChatLog)
        marker = ToolCallMarker("bash", "ls /tmp", tool_input={"command": "ls /tmp"})
        marker.set_complete("file1.txt\nfile2.log", is_error=False)
        output = CollapsibleToolOutput("file1.txt\nfile2.log")
        output.display = True
        output.set_class(True, "expanded")
        marker.set_output_widget(output)
        await chat_log.mount(marker)
        await chat_log.mount(output)
        await pilot.pause(0.1)
    assert snap_compare(app, run_before=run_before, terminal_size=(120, 40))
```

- [ ] **Step 3: 新增全屏模态的 snapshot 测试**

在 `tests/test_tui_snapshot.py` 继续加：

```python
from loom.tui.welcome import WelcomeBanner
from loom.tui.command_palette import CommandPaletteModal


def test_welcome_banner(snap_compare):
    """WelcomeBanner splash shown when ChatLog is empty."""
    app = AgentTUIApp()
    async def run_before(pilot):
        await pilot.pause(0.1)
        # WelcomeBanner is mounted by default when chat log is empty
        await pilot.pause(0.1)
    assert snap_compare(app, run_before=run_before, terminal_size=(120, 40))


def test_command_palette_open(snap_compare):
    """CommandPaletteModal open state."""
    app = AgentTUIApp()
    async def run_before(pilot):
        await pilot.pause(0.1)
        pilot.app.push_screen(CommandPaletteModal())
        await pilot.pause(0.2)
    assert snap_compare(app, run_before=run_before, terminal_size=(120, 40))


def test_model_picker_open(snap_compare):
    """ModelPicker open state (mock only anthropic connected to avoid timeout)."""
    from unittest.mock import patch
    app = AgentTUIApp()
    fake_cred = object()  # sentinel
    def _fake_get(provider_id: str):
        return fake_cred if provider_id == "anthropic" else None
    async def run_before(pilot):
        from loom.tui.model_picker import ModelPicker
        with patch("loom.agent.credential.credentials.get", side_effect=_fake_get):
            pilot.app.push_screen(ModelPicker())
            await pilot.pause(0.3)
    assert snap_compare(app, run_before=run_before, terminal_size=(120, 40))
```

- [ ] **Step 4: 首次运行建立 baseline**

Run: `uv run pytest tests/test_tui_snapshot.py -v --snapshot-update`
Expected: 9 个新 baseline 创建成功（+ 现有 2 个可能被更新）。检查输出确认所有测试 PASS。

- [ ] **Step 5: 再次运行验证 baseline 稳定**

Run: `uv run pytest tests/test_tui_snapshot.py -v`
Expected: 所有测试 PASS（不再需要 --snapshot-update）

- [ ] **Step 6: 提交**

```bash
git add tests/test_tui_snapshot.py tests/__snapshots__/test_tui_snapshot/
git commit -m "test(tui): expand snapshot baselines — StatusBar/ChatLog/modals (+9)

1C of visual polish batch. New baselines lock visual state for:
- StatusBar idle/streaming/error (3)
- ChatLog streaming+thinking, tool-call collapsed/expanded (3)
- WelcomeBanner, command_palette, model_picker (3)

ModelPicker test uses side_effect mock (only anthropic connected) to
avoid enumerating entire models.dev catalog (fix from
test_provider_status_indicator.py). Total baselines: 8 → 17."
```

---

## Task 8: 完整验证 + closeout

**Files:** 无代码改动

- [ ] **Step 1: 运行 ./init.sh 全量验证**

Run: `./init.sh`
Expected: "Verification Complete (all green)" with all pytest passed, 0 ruff, 0 mypy

- [ ] **Step 2: 运行 eval 验证**

Run: `uv run python -m loom.cli eval --kind harness`
Expected: ≥455/464 passed（与上次相同或更好，不应有新的 TUI 相关失败）

- [ ] **Step 3: 更新 feature_list.json**

在 `feature_list.json` 的 features 数组末尾加新条目：

```json
{
  "id": "f-tui-visual-polish",
  "name": "TUI visual polish batch — diff_pane tokens + hairline + snapshot expansion + typography + interaction feedback",
  "description": "5-part visual polish batch per docs/superpowers/specs/2026-06-26-tui-visual-polish-design.md: (1A) diff_pane.py hardcoded [yellow]/[green]/[red] → semantic tokens $warning/$success/$error (spec §8.1 compliance, zero visual change). (1B) Header bottom hairline via #chat-log border-top: solid $hairline (resolves Textual height:1 + border-bottom content collapse compromise). (1C) +9 snapshot baselines (StatusBar idle/streaming/error, ChatLog streaming+thinking/tool-call collapsed/expanded, WelcomeBanner, command_palette, model_picker) — total 8→17. (2) Typography unification: UserMessage padding 1 2 → 0 2, AssistantSummary padding 0 0 0 2 → 0 2, Composer focus 5%→8%. (3) Interaction feedback (all static, spec §6 compliant): C-1 error SystemNote gains $error 10% bg + left border + ✗ icon; C-2 HeaderSectionButton dividers $border→$hairline + $accent-dim on hover/focus; C-3 CollapsibleToolOutput.expanded gains $surface 90% bg + $accent-dim left border. Streaming cursor rejected (spec §2.2.2 forbids typewriter).",
  "dependencies": [
    "f-tui-keyboard-access",
    "f-tui-mcp-overlay-error-detail"
  ],
  "status": "done",
  "verification": "uv run pytest tests/test_tui_snapshot.py tests/test_tui_header.py tests/test_show_notification.py tests/test_thinking_marker_click.py -v && ./init.sh && uv run python -m loom.cli eval --kind harness",
  "evidence": "./init.sh → 'Verification Complete (all green)' with N pytest passed, 17 snapshots, 0 ruff, 0 mypy; uv run python -m loom.cli eval --kind harness → N/N passed (zero TUI regression); 9 new snapshot baselines added (8→17 total). Changes: loom/tui/diff_pane.py (3 lines token-ized), loom/tui/app.py (#chat-log border-top + #chrome focus 8%), loom/tui/header.py (HeaderSectionButton CSS + NOTE removed), loom/tui/chat_log.py (UserMessage/AssistantSummary padding + SystemNote.severity-error + CollapsibleToolOutput.expanded), tests/test_tui_snapshot.py (+9 tests)."
}
```

把 `N` 替换为实际数字。

- [ ] **Step 4: 追加 progress.md session 章节**

在 `progress.md` 末尾加：

```markdown
## Session: TUI 视觉优化批次 (2026-06-26)

**Feature**: `f-tui-visual-polish`

**Spec**: docs/superpowers/specs/2026-06-26-tui-visual-polish-design.md

**Changes** (5 atomic commits):
- 1A: diff_pane.py token-ize [yellow]/[green]/[red] → $warning/$success/$error
- 1B: #chat-log border-top: solid $hairline (Header hairline resolved)
- 2: UserMessage/AssistantSummary padding unified to 0 2; Composer focus 8%
- 3: C-1 error SystemNote bg+border+✗; C-2 HeaderSectionButton $hairline+$accent-dim; C-3 CollapsibleToolOutput.expanded bg+border
- 1C: +9 snapshot baselines (StatusBar/ChatLog/modals), 8→17 total

**Verification**: ./init.sh green; loom eval --kind harness N/N passed.

**Files changed**: 4 production files (diff_pane, app, header, chat_log), 1 test file (test_tui_snapshot +9), 17 snapshot baselines.
```

- [ ] **Step 5: 最终提交**

```bash
git add feature_list.json
git commit -m "docs(tracking): f-tui-visual-polish done — feature_list.json + progress.md

5 atomic commits: diff_pane tokens, hairline, typography, interaction
feedback (C-1/C-2/C-3), snapshot expansion (+9 baselines, 8→17).
./init.sh green, eval zero TUI regression."
```

(progress.md 不提交，已被 .gitignore)

---

## Self-Review 结果

**1. Spec coverage**: 
- 1A diff_pane token 化 → Task 1 ✓
- 1B Header hairline → Task 2 ✓
- 1C snapshot 扩展 → Task 7 ✓
- 2 排版间距 → Task 3 ✓
- 3 C-1 error → Task 4 ✓
- 3 C-2 section 分隔 → Task 5 ✓
- 3 C-3 展开背景 → Task 6 ✓
- 验证 + 工件更新 → Task 8 ✓

**2. Placeholder scan**: 无 TBD/TODO。所有代码块完整。

**3. Type consistency**: 
- `CollapsibleToolOutput.toggle()` 在 Task 6 用 `self.set_class(self.display, "expanded")` — 与 Task 7 的 `output.set_class(True, "expanded")` 一致 ✓
- `SystemNote.add_class("severity-error")` 在 Task 4 — CSS 类名 `.severity-error` 一致 ✓
- token 名 `$hairline`/`$accent-dim`/`$surface`/`$error` 都在 `_LOOM_INK_THEME` 中已定义 ✓
