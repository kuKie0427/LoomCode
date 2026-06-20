# loom TUI — Layout Design Language

> Reconstructed 2026-06-20 from `docs/tui-design.html` (7 state mockups with §-annotations) and the original session description in `progress.md` § "docs/tui-design-language.md created". The prose spec was lost from the working tree between sessions; this version re-establishes it as the canonical source for future TUI refactors.

## §0 Why this doc exists

The TUI is a **long-loop surface**. Sessions routinely run 30 minutes to several hours, with hundreds of agent turns, tool calls, and streamed tokens. Every layout decision that accumulates over that loop — even a 50ms easing tween, even a 1-row status-bar overflow — becomes perceptible lag or visual noise.

The harness 5-subsystem model (Instructions / State / Verification / Scope / Lifecycle) has implicit spatial implications that were never written down. This doc makes them explicit so:

1. **Layout invariants are testable.** Future features can consume this doc as a spec and add snapshot / behavioral tests that lock the rules.
2. **Refactors don't drift.** A new component knows where it belongs, what padding is canonical, and which aesthetic rule it must obey.
3. **Decisions are reversible.** When §7's "open layout decisions" need to be closed, this doc records why the current choice was made.

**Scope of this version (first pass):**

- **In scope:** spatial structure, hierarchy, regions, motion intent, component contracts.
- **Out of scope:** exact color values, typography rendering, animation easing curves. Those live in `docs/tui-design.html` (visual reference) and in Textual's theme tokens (`$background`, `$accent`, etc.).

**Companion artifact:** `docs/tui-design.html` — 7-state visual reference. Each state cites a section of this doc via `<span class="cite">§N — rule</span>`. If a §-citation in the HTML disagrees with this prose, **this prose wins** and the HTML must be updated.

---

## §1 Five subsystems → five regions (+ one aggregated rail)

The harness model has five subsystems. The TUI gives each one a **fixed on-screen region** so users build muscle memory: Instructions → left gutter, State → scroll area, Verification → bottom dock, Scope → composer, Lifecycle → full-screen overlays.

| Subsystem | Region | Implementation | Interaction |
|---|---|---|---|
| **Instructions** | Left gutter markers (`▎ you`, `▎ assistant`) | `TurnLabel` in `loom/tui/chat_log.py:110-125` | Read-only, scroll-pinned |
| **State** | ChatLog scroll area (`1fr`, scrolls) | `ChatLog(VerticalScroll)` in `loom/tui/chat_log.py:432-644` | Mouse wheel + scroll bar; sticky-at-bottom while streaming |
| **Verification** | StatusBar (1 line, fixed) | `StatusBar` in `loom/tui/status_bar.py:38-79` | Read-only glance (model / turns / tools / ctx ratio) |
| **Scope** | Composer (3–8 lines, soft-wrap, focused input) | `Composer(TextArea)` in `loom/tui/composer.py:6-46` | User typing; Enter submits |
| **Lifecycle** | Full-screen overlays | `PermissionScreen` in `loom/tui/screens.py`, `ToolCallModal` in `loom/tui/chat_log.py:307-363` | Modal gate; replaces, not floats |

| Aggregate rail | Region | Implementation | Interaction |
|---|---|---|---|
| **Header (summary rail)** | 1 line, dock top, collapsed default | `loom/tui/header.py` (Horizontal + 3 `HeaderSectionButton` children) — see §4.3 spec | Per-section click: each `HeaderSectionButton` toggles only that section's overlay (mutual exclusion) |

The Header is **not** a 6th subsystem. It is an **aggregated rail** that surfaces Scope + State + Lifecycle indicators (MCP / todo / subagent) in one glanceable line. See §4.3.

---

## §2 Long-loop aesthetic rules (six enforceable)

These are the rules the design enforces. Every component decision must be checkable against them.

### §2 rule 1 — Bounded re-layout

> Only the middle ChatLog region scrolls. Header, StatusBar, Composer are fixed-height across the entire session. Nothing else may resize mid-loop.

**Why:** The eye learns the screen geometry in the first 30 seconds and uses the fixed anchors (top of Header, status bar, composer caret) to navigate. Re-layout = lost anchor = re-scan cost. Over a 3-hour session, even one re-layout per minute becomes a 1-second-per-minute tax.

**Enforcement:** No `height: auto` or content-driven resize on Header, StatusBar, Composer. ChatLog is the only widget that may grow its content.

### §2 rule 2 — Quiet by default

> Idle states must not blink, pulse, or redraw. Motion is reserved for live work. When the loop is between iterations, the screen is a held still image with only the cursor in the composer.

**Why:** Animation in idle draws the eye away from the actual content. In a long session, even a 2Hz pulse becomes subliminally distracting. Motion should mean **"something is happening right now"** — keep it scarce so it stays meaningful.

**Enforcement:**
- ThinkingMarker spinner ticks **only while reasoning is in progress** (not after `set_complete()`).
- No animated borders on idle widgets.
- No "agent running…" text hints in the composer (the spinner + cursor + running tool marker carry that signal — see §5 anti-pattern: "no pending placeholder").

### §2 rule 3 — One anchor per iteration

> Each loop iteration produces **one spatial anchor**: a `▎` label, a tool marker, or a user message. Multiple anchors per iteration = layout fights itself.

**Why:** The eye tracks the conversation by following `▎ you` → `▎ assistant` → next `▎ you`. Adding extra labels per iteration (e.g. a separate "tool summary" row that isn't itself a marker) breaks the cadence.

**Enforcement:**
- Each turn has exactly one `TurnLabel` (`▎ you` or `▎ assistant`).
- Tool calls, thinking, and streamed text are children of the current turn label, **not** siblings with their own labels.

### §2 rule 4 — Predictable monotonic scroll

> New content always appears at the bottom of the scroll region and pushes older content up. Never insert in the middle. Never re-order past content.

**Why:** Inserting content mid-list breaks the reader's place. Re-ordering breaks the temporal narrative. Users scroll **down** to see new things and scroll **up** to revisit; any other motion is hostile.

**Enforcement:** `ChatLog._sticky = True` while streaming. On user scroll-up, `_sticky = False` (preserves their position). On scroll-to-end, `_sticky = True` again. See `chat_log.py:435-439`.

### §2 rule 5 — Indentation encodes nesting

> Tool output is indented 2 cols right of its marker. Thinking body is indented 2 cols right of the assistant label. User/assistant message bodies share the same left edge as their label.

**Why:** Visual hierarchy by **position**, not by color or weight. Two indentation levels (outer column + 2-col right) is the entire hierarchy. A third tier reads as decorative.

**Enforcement:**
- Outer column: `TurnLabel`, `ToolCallMarker`, `SystemNote`, user/assistant bodies — `margin-left: 0`.
- Second tier (2 cols right): `ThinkingDisplay` (`margin: 0 0 1 2`), `CollapsibleToolOutput` (`margin: 0 0 1 2`).
- No third tier. If a future component feels the need for one, redesign instead of indenting further.

### §2 rule 6 — Hard interrupts fill the screen

> Modal screens replace the entire viewport (PermissionScreen, ToolCallModal). They are not floats, not side-panels, not toasts.

**Why:** A consent gate that occupies 30% of the screen invites the user to "look around it." A full-screen replacement with a thick red border forces a decision. This is the **only** time the layout should hard-interrupt (see §5 anti-pattern: "hard interrupts for consent only").

**Enforcement:** `PermissionScreen` and `ToolCallModal` use `ModalScreen` (full-screen), with a thick red border (`PermissionScreen`) or thick primary border (`ToolCallModal`). No floating dialogs, no banner notifications.

---

## §3 Ergonomic layout grid

Six-region vertical stack. Updated from the original 5-region spec to include the Header at the top.

```
┌── Header (1 line, dock top, collapsible)        ── §4.3
├──────────────────────────────────────────────
│                                              │
│                                              │
│       ChatLog  (1fr, scrolls)                │
│                                              │
│                                              │
├──────────────────────────────────────────────
│ StatusBar   (1 line, fixed)                  ── §4
├──────────────────────────────────────────────
│ Composer    (3–8 lines, soft-wrap, focused)  ── §4
└──────────────────────────────────────────────
```

**Spacing constants (canonical values):**

| Element | Value | Why |
|---|---|---|
| Horizontal margin (left + right) | **2 cols each side** | Symmetry reads as calm; asymmetric margins read as active. The 2-col value is the "book width" — the eye-rest zone. |
| Vertical padding above ChatLog content | **1 row** | Visually separates Header from first message; matches the §3 spec. |
| Margin around `#chrome` | `0 2 1 2` (top=0, right=2, bottom=1, left=2) | Aligns StatusBar/Composer with ChatLog's content column. |
| Composer inner padding | `1` col left/right, `1` row bottom | Keeps typed text inside the 2-col margin column. |

**Two stable eye anchors:**

1. **Top anchor** — top edge of the Header (or top edge of ChatLog if Header is collapsed hidden). Click = expand Header overlay.
2. **Bottom anchor** — the composer caret (1 pixel above the bottom of `#chrome`). The caret never moves between sessions — same y-coordinate every time the user opens the app.

The ChatLog is read between these two anchors, **top-down**. The eye learns to flick between them in one saccade.

**Soft-wrap composer, never horizontal scroll:**

Multi-line prompts are the user's thinking space; horizontal motion breaks the writing flow. Composer caps height at 8 lines — long prompts scroll **inside** the composer, the rest of the layout never moves.

**Source of truth:** `loom/tui/app.py:255-259` (compose tree) + `loom/tui/app.py:38-94` (CSS).

---

## §4 Current layout map → component contracts

Twelve components in `loom/tui/`. Each has a fixed position, fixed height, and an interaction zone. Future components must claim a slot in this table.

| # | Component | File:line | Position | Size | Interaction zone |
|---|---|---|---|---|---|
| 1 | `Screen` | `app.py:38-42` | root | fills viewport | — |
| 2 | `ChatLog` | `app.py:43-59` | flex between Header and `#chrome` | `height: 1fr` | mouse-wheel scroll, click-to-focus |
| 3 | `StatusBar` | `app.py:71-76`, `status_bar.py:38-79` | inside `#chrome`, top | `height: 1` | read-only; reactive to `turns` / `tools` / `ctx_tokens` / `ctx_window` |
| 4 | `Composer` | `app.py:77-93`, `composer.py:6-46` | inside `#chrome`, bottom | `height: auto, max-height: 8, min-height: 3` | focused input; Enter submits; `/` triggers slash command |
| 5 | `#chrome` (Vertical wrapper) | `app.py:60-70` | `dock: bottom` | `height: auto` | `:focus-within` background boost (3%) |
| 6 | `Header` (summary rail) | `loom/tui/header.py:319-365` | `dock: top` (above ChatLog) | `height: 1` (Horizontal with 3 `HeaderSectionButton` children) | Each section button click expands only that section; see §4.3 |
| 7 | `TurnLabel` | `chat_log.py:110-125` | first child of each turn, inside ChatLog | `height: 1` | read-only; color-coded by role |
| 8 | `UserMessage` / `AssistantMessage` | `chat_log.py:128-157` | child of `TurnLabel` (same turn) | `height: auto` | read-only markdown; `padding: 0 2` |
| 9 | `ThinkingMarker` | `chat_log.py:209-273` | child of assistant turn, before thinking body | `height: 1` | click toggles `ThinkingDisplay` visibility |
| 10 | `ThinkingDisplay` | `chat_log.py:191-206` | child of assistant turn, indented 2 cols | `height: auto`, `display: False` default | reveals on ThinkingMarker click |
| 11 | `ToolCallMarker` | `chat_log.py:366-429` | child of assistant turn | `height: 1` | single-click toggle `CollapsibleToolOutput`; double-click opens `ToolCallModal` |
| 12 | `CollapsibleToolOutput` | `chat_log.py:275-304` | child of assistant turn, after ToolCallMarker, indented 2 cols | `max-height: 20`, `display: False` default | scrolls internally if output > 20 lines; truncated head + tail with `… N more lines omitted …` |
| 13 | `ToolCallModal` | `chat_log.py:307-363` | full-screen ModalScreen | `width: 80%, height: 80%` | deep-dive view; ESC closes |
| 14 | `PermissionScreen` | `screens.py:13-74` | full-screen ModalScreen | `width: 70%, height: auto` | 3 buttons: Allow once / Allow always / Deny; ESC = deny |
| 15 | `SystemNote` | `chat_log.py:179-188` | sibling of TurnLabel in ChatLog | `height: auto` | read-only italic dim |

All 15 components are currently implemented. `SystemNote` is not in the original spec; it lives in the State region as a sibling of `TurnLabel`. `Header` was added 2026-06-19 and refactored to per-section toggle 2026-06-20.

---

## §4.3 Header (summary rail)

Added 2026-06-19 to close §7's "no header region" open decision. The Header is a **first-class layout region** that aggregates three subsystems (Scope + State + Lifecycle) into one glanceable line at the top of the viewport.

### §4.3.1 Default state — collapsed

**Geometry:**
- `dock: top`, `height: 1` line
- A `Horizontal` container with 3 child widgets (`HeaderSectionButton`, one per subsystem)
- Each button has `width: 1fr` so the 3 buttons fill the 1-line horizontal track evenly (no dead zones between buttons)
- Background: slightly elevated from ChatLog (e.g. `$panel`)
- Bottom border: hairline (`$border`)
- Default-on (no toggle to hide the entire Header)

**Per-section buttons — each is its own click target:**

The collapsed line is NOT a single click target. Each section (MCP / Todos / Subagent) is its own independently clickable `HeaderSectionButton` widget. The 3 buttons sit side-by-side in spec order:

```
[ ● MCP:3/3 ]    [ ◐ 5/5 todos ]    [ ◐ 1 subagent ]
 ↑ clickable     ↑ clickable         ↑ clickable
```

Button widget IDs (for tests / DOM query):
- `#header-btn-mcp`
- `#header-btn-todo`
- `#header-btn-subagent`

**Glyph semantics — aggregate indicator reflects worst state (per button):**

| Button | Glyph | Meaning |
|---|---|---|
| MCP | `●` green | all servers healthy |
| MCP | `◌` yellow | any server error |
| MCP | `○` dim | all disabled |
| Todo | `◐` yellow | has active item |
| Todo | `✓` green | all done |
| Todo | `○` dim | empty |
| Subagent | `◐` yellow | has running |
| Subagent | (hidden) | count = 0 |

**Hide rule — zero state:** If any section's count is zero (MCP=0, todo=0, subagent=0), that button gets the `section-hidden` CSS class (`visibility: hidden`). The button is not rendered, not clickable, and does not occupy visual space. Subagent count = 0 hides the entire subagent button (no `0 subagent` placeholder).

**No `▼` prefix glyph.** In the original 2026-06-19 spec, a `▼` glyph was used to indicate "click to expand". In the 2026-06-20 per-section redesign, each button IS its own click affordance — no extra glyph is needed. The button text (glyph + label + count) is the affordance.

### §4.3.2 Expanded state — per-section overlay panel

**Design (2026-06-20 revision):** Each section has its own overlay. Only ONE overlay is visible at a time (mutual exclusion). The overlay shows only the clicked section — not all three sections together.

#### Per-section toggle behavior (3 cases)

When a `HeaderSectionButton` is clicked, the App handler `on_header_section_toggle` does exactly one of:

1. **No overlay open** → mount an overlay for the clicked section (expand).
2. **Overlay open for the SAME section** → remove the overlay (collapse / toggle off).
3. **Overlay open for a DIFFERENT section** → remove the old overlay, mount a new one for the clicked section (switch).

This is the **3-way toggle state machine**:

```
        click A    click A   click B     click C
collapsed ──→ [A] ──→ collapsed ──→ [B] ──→ [C]
       expand    collapse   switch    switch
                  (toggle)   (A → B)   (B → C)
```

#### Collapse mechanisms

Three independent ways to collapse the overlay:

1. **ESC key** — `("escape", "collapse_header", "Collapse header")` binding. Invokes `App.action_collapse_header()` which removes the overlay if one exists. Silent no-op when no overlay is open. The previous spec version said "ESC collapses" but the implementation was deferred; this is now wired (per spec §4.3.2).
2. **Click the same section button again** — toggle off (§4.3.2 case 2).
3. **Click anywhere outside the overlay** — `App.on_click` handler removes the overlay on any click event that is NOT on a `HeaderSectionButton`, the `Header` container, or the `HeaderOverlay` itself. The click is consumed (via `event.stop()`) at the Header/HeaderOverlay level so `App.on_click` only fires for outside clicks (chat log, status bar, composer, etc.).

**Click on the overlay CONTENT itself does NOT collapse** — the overlay consumes its own click events (`HeaderOverlay.on_click` calls `event.stop()`). The user is reading; accidental clicks on rows shouldn't dismiss the panel. This is consistent with the "no auto-load" anti-pattern (§5): no collapse without explicit user intent.

#### Overlay geometry (per section)

- The overlay sits **below** the 1-line Header, above the ChatLog
- `dock: top`, `height: auto`, `max-height: 16` (≈360px in Textual units)
- `overflow-y: auto` if the section's detail rows exceed `max-height`
- ChatLog geometry is **unchanged** — the overlay overlays the top of the ChatLog visually but does not reflow it
- Scroll position in ChatLog is **preserved** across collapse / expand / switch
- Background: `$panel 97%` — nearly opaque, slight transparency hints at the chat behind
- Bottom border: `solid $border` for visual separation from the ChatLog

#### Per-section content

The overlay renders **only the clicked section** (mutual exclusion — no mixing of MCP + Todos in one overlay):

1. **MCP overlay** — section header `● MCP:N/M` (or `◌` / `○` per worst-state glyph), then 2-col indented rows: `<glyph> <name> <state>` per server. Section header on outer column, detail rows indented 2 cols right.
2. **Todos overlay** — section header `◐ N/M todos` (or `✓` / `○` per glyph), then 2-col indented rows: `<glyph> N. <item text>` per todo. Active item highlighted with accent color + bold weight (per the spec's "row-active" convention from state-7 mockup).
3. **Subagent overlay** — section header `◐ N subagent`, then 2-col indented rows: `<glyph> <id> · <state> · <elapsed>` per subagent. **Summary only** — full output lives in ChatLog.

#### Per-section overlay IDs

Each overlay instance is mounted with a per-section ID to avoid `DuplicateIds` errors when switching (the old overlay may still be in the DOM pending async removal):
- `header-overlay-mcp`
- `header-overlay-todo`
- `header-overlay-subagent`

#### Indent levels — maximum 3 (per §2 rule 5)

- Outer column: section header (`<glyph> <Label>:N/M`)
- 2-col right: detail rows (`<glyph> <name>` / `<glyph> N. <text>` / `<glyph> <id> · <state>`)
- No fourth tier. This matches the ChatLog's two-level indentation convention.

#### Subagent row click behavior (subagent overlay only)

Clicking a subagent ID inside the Subagent overlay dismisses the overlay and **scrolls the ChatLog to that subagent's existing marker**. (Markers exist because subagent tool calls are already inline in the chat.) This applies only when the Subagent overlay is the active one.

#### Animation

**None.** Click = instant mount / remove / switch. No slide, no fade. See §6.

#### Why per-section + mutual exclusion

The original 2026-06-19 spec assumed a single overlay showing all 3 sections simultaneously. The 2026-06-20 revision switched to per-section toggles after the user reported the UX problem of "one click expands all three" with no way to inspect just one subsystem. The mutual exclusion invariant (only 1 overlay visible) prevents UI crowding over long sessions and matches the long-loop aesthetic (§2 rule 2 — quiet by default; only one region of motion at a time).

### §4.3.3 Why this honors the long-loop aesthetic

- **Collapsed line is the glance density ceiling** (§2 rule 2). Even idle, it shows the session's load shape in one line — but it never grows taller when collapsed.
- **Header is exactly 1 line in collapsed state** (§2 rule 1). It never grows regardless of how many MCPs or todos exist. The 3 buttons share this single line (each `width: 1fr`).
- **Index + topic memory pattern applied to layout** (§5). The collapsed line is the bounded always-on index; each per-section overlay is the on-demand topic. The index never grows; the topic loads only when the user asks.
- **Aggregate indicators, not exhaustive lists** (§5 anti-pattern). The collapsed line shows worst-state glyph + count, not every MCP/todo/subagent. The user sees the load at a glance.
- **Per-section toggle + mutual exclusion** (§4.3.2). Only ONE overlay is visible at a time, preventing UI crowding in long sessions. Each section gets its own bounded topic space; switching is instant (§6).

### §4.3.4 Implementation status

**Implemented** in Textual (`loom/tui/header.py`, `loom/tui/app.py`):

- **Initial implementation** (2026-06-19, commit `61cda27`): Header as a `Static` line with a single click-to-toggle overlay showing all 3 sections.
- **Per-section redesign** (2026-06-20, commit `0fc00b0`): Header as a `Horizontal` container with 3 `HeaderSectionButton` children; each section has its own per-section overlay (mutual exclusion); ESC + click-outside + click-same-section collapse mechanisms.
- **Mock data only** — `HeaderState` / `MCPServer` / `TodoItem` / `Subagent` are populated by `DEFAULT_MOCK_STATE` in `on_mount`. Real backend wiring of MCP server state, todo list results, and subagent count is **deferred** to a follow-up feature (the agent_loop must expose these signals to the TUI).
- **Test coverage**: 35 pytest tests in `tests/test_tui_header.py` + 14 eval cases in `loom/eval/cases/tui_header.py`. Snapshot baselines in `tests/__snapshots__/test_tui_header/`.

---

## §5 Anti-patterns

Layout consequences drawn from harness gotchas. Each anti-pattern is named so a future refactor can recognize and reject it.

### No "pending" state indicator

> Don't add a placeholder row between user-send and first thinking-frame ("Waiting for agent…", "Processing…").

**Layout consequence:** The TUI correctly jumps from "user sent" → "thinking spinner" → "streaming". A pending placeholder row would be dead pixels most of the time. The spinner + cursor + running tool marker carry the "loop is alive" signal — adding a fourth signal dilutes the others.

**Enforcement:** No text-based "waiting" indicators anywhere in the layout.

### 1-line StatusBar cap

> StatusBar must remain a single text line, even when context-window visualization gets fancy.

**Layout consequence:** A 2-line StatusBar breaks the bounded-re-layout rule (§2 rule 1) — users now have to track two fixed-height rows instead of one. The progress bar is already a glyph (`█`/`░`) compressed into the existing line.

**Enforcement:** `StatusBar.height: 1` (set in `app.py:71-76` and `status_bar.py:39-43`). Do not add a second row for warnings, suggestions, or tooltips — those belong in the expanded Header overlay or in the chat log itself.

### 3-tier progressive disclosure

> StatusBar (always) → marker line + ▎ label (visible when scrolled to) → collapsed body / modal (click to load). Never flatten all three.

**Layout consequence:** Flattening all three tiers (always-on status, always-on bodies, always-on modals) creates a wall of text. The 3-tier model keeps the default screen calm while letting the user drill into detail on demand.

**Enforcement:** Each chat-log element has a marker (`ThinkingMarker`, `ToolCallMarker`) that gates its body. `CollapsibleToolOutput.display = False` by default. Modals (`ToolCallModal`, `PermissionScreen`) only appear on explicit user action.

### Full-screen only for consent

> Modals must replace the full viewport. No floating dialogs, no banner notifications, no toast popups.

**Layout consequence:** A consent gate that occupies 30% of the screen invites the user to "look around it." A full-screen replacement with a thick red border forces a decision. Banners/toasts fade out before the user reads them in long sessions.

**Enforcement:** Only `PermissionScreen` and `ToolCallModal` exist. Both are `ModalScreen` (full-screen). New modal types must be full-screen by default — a half-screen variant requires explicit justification.

### Composer = local override

> The composer is the user's zone, not the agent's. No agent-driven overlays, tooltips, or decorations inside the composer region.

**Layout consequence:** If the agent wants to ask a clarifying question, it goes in the chat log as a regular `UserMessage`/`AssistantMessage` turn. If the system wants to show a hint, it goes in the StatusBar (1 line) or in a Header overlay (§4.3). Never inside the composer.

**Enforcement:** `Composer` contains only `TextArea` content. No floating widgets, no agent-driven decorations. `:focus-within` background boost (3%) is the only chrome reaction.

### Index + topic memory pattern applied to layout

> Always-on index must be bounded. On-demand topic detail must be loadable but never auto-loaded.

**Layout consequence:** StatusBar is the index (bounded 1 line). The collapsed Header is the aggregate index for sub-systems (bounded 1 line, with 3 section buttons sharing the row). Each per-section overlay (`header-overlay-{mcp,todo,subagent}`) is its own bounded topic — only one is visible at a time (mutual exclusion, §4.3.2). Modal bodies are deep-dive topics. None of the topics auto-load.

**Enforcement:** No component opens a modal, overlay, or detail view without explicit user action (click, keypress). The expanded Header overlay never auto-opens on errors. Even when an MCP server enters error state, the `◌` glyph change in the collapsed button is sufficient signal — the user clicks to investigate.

### No exhaustive lists in collapsed chrome

> The collapsed Header shows aggregate glyph + count, not every MCP/todo/subagent.

**Layout consequence:** Listing all 12 MCP servers in the collapsed 1-line Header is impossible (or scrolls within the line). The aggregate glyph + count tells the user "all healthy / one error / none" without enumeration.

**Enforcement:** Collapsed Header format is `<glyph> <section>:N/M` (counts only). Per-item enumeration lives in the expanded overlay (§4.3.2).

---

## §6 Motion intent

**All transitions are instant.** No easing. No sliding. No fade.

**Why:** Long sessions mean easing accumulates into perceptible lag. A 50ms fade that "feels nice" in a 5-second interaction becomes 1 second of dead UI in a 200-tool-call session. The long-loop aesthetic in motion form: **instant position changes, recorded state, no in-between.**

**Specific transitions that must be instant:**

| Transition | Mechanism | Where |
|---|---|---|
| ToolCallMarker glyph swap (`⊙ running` → `⊙ done` / `⊗ error`) | `set_complete()` updates text in one frame | `chat_log.py:420-429` |
| ThinkingMarker `⠋ spinner` → `◦ thought · Ns` | `set_complete()` updates text in one frame | `chat_log.py:268-272` |
| StreamingOverlay → AssistantMessage finalize | `update()` swap on turn end | `chat_log.py:574-585` |
| CollapsibleToolOutput toggle | `display = not display` (instant show/hide) | `chat_log.py:295-296` |
| Header section button click → overlay expand / switch / collapse | `screen.mount(overlay)` + `existing.remove()` (no slide) | `app.py:on_header_section_toggle` (§4.3.2) |
| Modal push/pop | `ModalScreen` replace (no fade) | built-in Textual |
| Scroll position change | `scroll_to(y=..., animate=False)` | `app.py:198` (mouse wheel path) |

**What is allowed to animate:**

- The ThinkingMarker spinner tick (10 frames × 50ms = 5fps; ticks during active reasoning only). This is **state visualization**, not a transition — the spinner represents ongoing work.
- Textual's built-in focus indicator (cursor blink in the composer). This is a system convention, not a UI choice.

**What is forbidden:**

- Slide-in / slide-out for modals, overlays, panels
- Fade-in / fade-out for any element
- Animated borders, pulsing colors, breathing opacity
- Scroll tweening (always `animate=False`)
- Progress-bar fill animation (the progress bar is a discrete glyph, not an animated fill)

---

## §7 Open layout decisions

Deliberately left undefined. Each item has a default behavior until a future refactor closes the decision.

### Two-pane mode (left panel)

**Question:** Should there be a persistent left pane (e.g. file tree, todo list, session history) alongside the ChatLog?

**Default:** No. The ChatLog occupies the full content width. The Header collapsed line + StatusBar cover the "what's the state" need.

**Why deferred:** Adds a fixed-width left panel that eats the 2-col eye-rest margin. In narrow terminals (< 80 cols) the ChatLog becomes unusable. No current feature requires this.

### Zen mode

**Question:** Should there be a `/zen` (or similar) command that hides Header + StatusBar for a distraction-free view?

**Default:** No. Header + StatusBar are part of the long-loop aesthetic — they are the glance anchors. Hiding them breaks §2 rule 1 (bounded re-layout).

**Why deferred:** Use case unclear. The composer caret + chat log are already the minimum needed for work; adding a mode that removes more risks turning the TUI into a plain CLI.

### Narrow-terminal minimums

**Question:** What is the minimum terminal width where the layout remains usable?

**Default:** Untested below 80 cols. The 2-col margin + Composer + StatusBar text are the tightest constraints.

**Why deferred:** No current user complaint. If a constraint emerges (e.g. a mobile/embedded use case), this becomes a hard layout decision (truncate StatusBar? remove Composer soft-wrap? stack Header above StatusBar instead of dock-top?).

### Header default-on vs default-off

**Question:** Should the Header be shown by default, or hidden behind a toggle (e.g. `/header`)?

**Decision (2026-06-19):** **Default-on.** The glance density it provides is part of the long-loop aesthetic. Hiding it forces the user to remember it exists.

**Status:** Closed.

### Header overlay auto-expand on errors

**Question:** Should the Header auto-expand when an MCP server enters error state?

**Decision (2026-06-19):** **No auto-expand.** Per §5 anti-pattern "no auto-load on errors." The aggregate `◌` glyph in the collapsed line is sufficient signal — the user clicks to see details.

**Status:** Closed.

### Click-outside-to-collapse for Header overlay

**Question:** Should clicking outside the expanded Header overlay collapse it?

**Default:** No. Only ESC collapses (and clicking the header line itself toggles). Click-outside-to-collapse would intercept normal clicks on the chat log while the overlay is up.

**Why deferred:** Conflict with the `opacity: 0.20` on ChatLog — clicks on the dimmed chat would also dismiss the overlay, which is hostile to "I want to scroll while the overlay is up." (Although scrolling is preserved across collapse/expand, click-outside-to-collapse would prevent reading the chat while overlay is open.)

---

## Appendix A — Source materials

- `docs/tui-design.html` (1443 lines, 64KB) — 7-state visual reference with §-annotations.
- `progress.md` § "docs/tui-design-language.md created (2026-06-19)" — original §0–§7 structure + scope decisions.
- `progress.md` § "Header (summary rail) added to design language + HTML mockup (2026-06-19)" — §4.3 Header sub-section addition.
- `docs/tui-scrolling.md` — scroll-behavior rules (§2 rule 4 enforcement detail).
- `loom/tui/app.py`, `chat_log.py`, `composer.py`, `status_bar.py`, `screens.py`, `widgets.py`, `messages.py`, `kitty_patch.py` — current implementation (1,456 LOC total).

## Appendix B — Change log

- **2026-06-19** — Original creation (221 lines, §0–§7). Lost from working tree.
- **2026-06-19** — Header (§4.3) added (318 lines). Closed §7's "no header region" decision.
- **2026-06-20** — Reconstructed from `docs/tui-design.html` (HTML mockup committed in `c2c9949`) + `progress.md` session descriptions. Header region kept, anti-patterns expanded with explicit enforcement notes, motion intent table added.
- **2026-06-20** — **§4.3 redesign aligned with code** (after commit `0fc00b0`). The 2026-06-19 §4.3 assumed a single overlay showing all 3 sections together, but the implementation (driven by the user's UX report) uses per-section toggles with mutual exclusion. Updated:
  - §4 component contract: Header entry now lists `loom/tui/header.py:319-365` and notes "Horizontal with 3 HeaderSectionButton children" (was "TBD — not yet implemented")
  - §4.3.1 Default state — collapsed: rewritten to describe the 3 independently clickable `HeaderSectionButton` widgets (`#header-btn-{mcp,todo,subagent}` IDs, `width: 1fr` each, `section-hidden` class on hide); removed the `▼` prefix glyph (each button is its own affordance)
  - §4.3.2 Expanded state — per-section overlay panel: rewritten to describe the 3-way toggle state machine (no-overlay / same / different), the 3 collapse mechanisms (ESC, click-same-section, click-outside), the mutual exclusion invariant, per-section overlay IDs (`header-overlay-{section}`), and "click on overlay content does NOT collapse"
  - §4.3.3 Why this honors the long-loop aesthetic: added a bullet on "Per-section toggle + mutual exclusion"
  - §4.3.4 Implementation status: updated from "Not yet implemented" to "Implemented (mock data only)" with commit references
  - §5 "Index + topic memory pattern" anti-pattern: clarified the Header overlay is now per-section (each is its own bounded topic)
- **HTML mockup status**: `docs/tui-design.html` still shows the original 2026-06-19 single-overlay design (states 6/7). It is **out of sync** with the new spec; a follow-up may update the mockup to show per-section toggles (states 6/7 → per-section states per mockup). The HTML is a visual reference, not a contract; the prose spec is authoritative.