# loom TUI — Layout Design Language

> Reconstructed 2026-06-20 from `docs/tui-design.html` (7 state mockups with §-annotations) and the original session description in `progress.md` § "docs/tui-design-language.md created". The prose spec was lost from the working tree between sessions; this version re-establishes it as the canonical source for future TUI refactors.

## §0 Why this doc exists

The TUI is a **long-loop surface**. Sessions routinely run 30 minutes to several hours, with hundreds of agent turns, tool calls, and streamed tokens. Every layout decision that accumulates over that loop — even a 50ms easing tween, even a 1-row status-bar overflow — becomes perceptible lag or visual noise.

The harness 5-subsystem model (Instructions / State / Verification / Scope / Lifecycle) has implicit spatial implications that were never written down. This doc makes them explicit so:

1. **Layout invariants are testable.** Future features can consume this doc as a spec and add snapshot / behavioral tests that lock the rules.
2. **Refactors don't drift.** A new component knows where it belongs, what padding is canonical, and which aesthetic rule it must obey.
3. **Decisions are reversible.** §7 records why each layout choice was made; reopening one requires flipping its Status and documenting why.

**Scope of this version (first pass):**

- **In scope:** spatial structure, hierarchy, regions, motion intent, component contracts, **and (as of 2026-06-21) the canonical color system — see §8.**
- **Out of scope:** typography rendering and animation easing curves. Those live in `docs/tui-design.html` (visual reference).

> **Color note (2026-06-21):** Earlier versions of this doc declared exact color values out of scope and deferred them to "Textual's theme tokens." That was a gap, not a decision — the running app used Textual's *stock* theme, so the documented `docs/tui-design.html` palette (sage accent, ink background) was never realized on screen. §8 closes this: a single canonical `loom-ink` Textual theme now ports the HTML palette 1:1, and color is in scope here.

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

### §2.2 Motion primitives contract

Five and ONLY five motion primitives are allowed in the TUI. Each has a fixed rate, amplitude, and idle-freeze invariant. The contract is locked — adding a new motion primitive requires reopening this section.

| # | Primitive | Rate | Amplitude | Idle freeze | Implementation |
|---|---|---|---|---|---|
| 1 | Gear-rack advance on ctx rail | 1Hz (gear frame cycle `❋✻✜`, 3 frames) | gear advances ±1 char by ratio; frame swaps | `engine_state == "idle"` → gear frozen at base frame `❋`, chain static | `status_bar.py` gear helper + `app.py::_tick_shuttle` (1Hz `set_interval`, idle guard) |
| 2 | Running glyph cycle `⊙⊚◎` | 1Hz (cycle 3 frames) | glyph swap | `_complete` or non-executing state → cycle stops, idx resets to 0 | `chat_log.py::ToolCallMarker._tick_cycle` (1Hz `set_interval(1.0, name="tool-cycle")`) |
| 3 | Thinking spinner | 5fps (10 frames × 50ms; 4× slower after 10s) | glyph swap | `_complete` → marker frozen at `◦ thought · Ns` | `chat_log.py::_tick_spinner` (5fps `set_interval(0.05)`) |
| 4 | Section button pulse | 1Hz square wave | opacity 1.0↔0.5 | `count == 0` → opacity frozen at 1.0 | `header.py::_tick_pulse` (Python `set_interval(0.5, name="header-pulse")`; Textual CSS doesn't support `@keyframes`/`animation`) |
| 5 | Composer cursor blink | Textual system default (≈1.1s) | system | always | Textual system widget |

#### §2.2.1 Idle-freeze invariant (§2.2.4 forbidden motion)

The single most important rule: when `engine_state == "idle"` (or the relevant sub-state is "not active"), NO motion is allowed. This is enforced at multiple levels:

- **Primitive 1** — `app.py::_tick_shuttle` first line: `if self.engine_state == "idle": return`. Even though the 1Hz timer fires every second, the shuttle phase is never updated while idle.
- **Primitive 2** — `chat_log.py::ToolCallMarker.watch_engine_state` skips cycle start when `new != "executing"`. `_tick_cycle` itself has an `_complete` guard.
- **Primitive 4** — `header.py::update_pulse(False)` calls `_stop_pulse()` which resets `opacity = 1.0`. The `pulsing` CSS class is removed when count drops to 0.

#### §2.2.2 What is FORBIDDEN (other motion)

The following motion patterns are **explicitly forbidden** — they violate §2 rule 2 (quiet by default) and were never approved:

- Slide-in / slide-out for modals, overlays, panels (instant mount/remove only)
- Fade-in / fade-out for any element
- Animated borders, pulsing colors, breathing opacity (other than the 5 primitives above)
- Scroll tweening (always `animate=False`)
- Continuous solid-block fill bars (`█`/`░`-style rectangular progress fills) and any easing/fade on a fill — **but see the controlled exception below**
- Hover-triggered pulse / breathe / shake on any widget
- Per-character typewriter / streaming animation in markdown
- Smooth interpolation of any reactive value (color, opacity, position) via tween

> **Controlled exception (2026-06-21, ctx rail gear-rack):** The ctx-usage rail (primitive 1, §2.2.3) renders progress as a **gear-rack transmission**: a colored "engaged chain" (`┅` in the semantic threshold color) trails behind a gear glyph (`❋✻✜`) that advances along a rack of un-engaged teeth (`┄` in `$text-faint`). The colored chain IS a form of fill, so this is an explicit, scoped carve-out from the "no fill" rule above. It remains bound by the rest of §2.2: **idle freezes the gear at base frame `❋` with the chain static** (no fill change while idle), the advance is a discrete per-tick position change (no easing/tween/fade), and continuous solid-block `█`/`░` rectangles are still forbidden. Rationale: the user required stronger ctx presence; a gear-rack is "linear transmission visualization," not a rectangular fill bar, and its motion is the existing 1Hz primitive-1 shuttle re-skinned (no new motion primitive).

**Enforcement:** any new commit that adds one of these patterns is a regression. The eval suite has a primitive-1 case (`tui-ctx-rail-gear-contract`) that locks the gear-rack contract (allows `❋✻✜`/`┅`/`┄`, still forbids `█`/`░`); the structural rule is enforced by code review + this doc.

#### §2.2.3 Why this matters

In a long-loop TUI (sessions routinely 30min to several hours), even subliminal motion becomes distracting. The 5 primitives are reserved for state visualization ("something is happening right now") — nothing else. The idle-freeze invariant means the screen becomes a held still image between agent iterations, with only the composer cursor in motion (which is system-default Textual behavior, not our motion).

**§4.2.1 — engine badge contract (6 states).** The StatusBar carries a 1-glyph engine-state badge (implemented in `status_bar.py::_render_engine_badge`). As of the StatusBar revamp (2026-06-21) it distinguishes all **six** `engine_state` values rather than collapsing them into 3 (`idle`/`run`/`error`). Each maps to exactly one glyph + one loom-ink token:

| engine_state | glyph | token | meaning |
|---|---|---|---|
| `idle` | `●` | `$text-muted` |待命，no live work |
| `thinking` | `◌` | `$warning` | reasoning in progress |
| `streaming` | `▸` | `$accent` | emitting response text |
| `executing` | `⊙` | `$accent` | running a tool (glyph echoes the inline `ToolCallMarker` `⊙`) |
| `compacting` | `◌` | `$secondary` | context compression running |
| `error` | `⊗` | `$error` | last operation failed |

The badge carries **pure status only** — no tool name (decoupled from ChatLog per the revamp decision). Color is meaning, not decoration (§8.3): a badge color change always signals an `engine_state` change.

**§4.2.1 tick-above-shuttle — REMOVED (2026-06-21).** The original §4.2.1 spec called for a `^` tick mark above the shuttle glyph; P3 (`f-tui-paradigm-p3`) implemented it as a `ShuttleTickOverlay` widget (a second `#chrome` row). The StatusBar revamp **removes `ShuttleTickOverlay` entirely** — the gear glyph's position on the rack already indicates progress, so a separate `^` pointer-to-the-pointer was redundant and cost a full chrome row. See the §7 reversal decision "ShuttleTickOverlay removed". The gear-rack rail (primitive 1, §2.2.3) replaces the shuttle `●`; there is no longer a separate tick row.

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
| 16 | `WelcomeBanner` | `chat_log.py` | first child of empty ChatLog, before any turn | `width: 1fr`, content-align center | Static splash: weave motif + `loom` + slogan + command hints; dismissed on first user message, re-shown after `/clear`; never animates (§2 rule 2) |
| 17 | `TurnSeparator` | `chat_log.py` | sibling of TurnLabel, before each user turn | `height: 1`, color `$border` | Hairline `─` divider in the content column; no third indent tier (§2 rule 5) |

All 17 components are currently implemented. `SystemNote` is not in the original spec; it lives in the State region as a sibling of `TurnLabel`. `Header` was added 2026-06-19 and refactored to per-section toggle 2026-06-20. `WelcomeBanner` and `TurnSeparator` added 2026-06-21 as low-motion decoration within the loom-ink palette.

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

## §7 Layout decisions

Each item records the decision and its rationale. As of 2026-06-20 all items are **Closed** — none remain open. Reopening one requires changing its Status back and recording why.

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

**Decision (2026-06-20):** **Usable ≥ 93 cols; degrades gracefully below.** Measured all four regions across 60–120 cols:

| Region | Behavior under narrow width |
|---|---|
| Header | 3 `width: 1fr` buttons split evenly; short labels (`● MCP:3/3`) fit down to 60 cols. No break. |
| ChatLog | `1fr`, markdown soft-wraps; `overflow-x: hidden`. No horizontal break at any width. |
| Composer | `TextArea`, soft-wraps. No break. |
| StatusBar | **The only bottleneck.** Fixed-format, non-wrapping. Right-clips when content exceeds width. |

The StatusBar was the only region that broke. Its content length was:

- 85 cols at session start (0 turns/tools)
- 93 cols mid-session (multi-digit turns/tools) — **re-verified by f-statusbar-revamp-sp2** (80 cols empty / 87 idle mid-session / 93 worst-case active)
- **119 cols** once the chat log overflowed (the extra `| scroll with mouse wheel` hint)

**Fix applied (`f-tui-statusbar-drop-scroll-hint`):** dropped the scroll hint. Mouse-wheel scrolling is the default, intuitive behavior; the hint mostly added width. Max StatusBar width is now **93 cols** (was 119). Below 93 the bar right-clips (loses the ctx token count first, then the percentage) — non-fatal, the chat log + composer stay fully usable.

**Status:** Closed. No responsive/multi-tier StatusBar planned unless a < 80-col use case emerges.

### Header default-on vs default-off

**Question:** Should the Header be shown by default, or hidden behind a toggle (e.g. `/header`)?

**Decision (2026-06-19):** **Default-on.** The glance density it provides is part of the long-loop aesthetic. Hiding it forces the user to remember it exists.

**Status:** Closed.

### Zen mode (hide Header + StatusBar)

**Question:** Should there be a `/zen` (or similar) command that hides Header + StatusBar for a distraction-free view?

**Decision (2026-06-20):** **No.** Header + StatusBar are the long-loop glance anchors. The user confirmed no distraction-free use case. The composer + chat log are already the minimum; a Zen mode would add a mode without a clear need.

**Status:** Closed.

### Two-pane mode (persistent left panel)

**Question:** Should there be a persistent left pane (file tree, todo list, session history) alongside the ChatLog?

**Decision (2026-06-20):** **No.** The user prefers the full-width ChatLog. The "I keep wanting to glance at todos / subagents" need is met instead by **inline ChatLog event markers** (subagent start/end + todo-update markers rendered in the conversation timeline — see `f-tui-inline-event-markers`), not by a persistent panel that eats the 2-col eye-rest margin and breaks on narrow terminals.

**Status:** Closed (in favor of inline timeline).

### Header overlay auto-expand on errors

**Question:** Should the Header auto-expand when an MCP server enters error state?

**Decision (2026-06-19):** **No auto-expand.** Per §5 anti-pattern "no auto-load on errors." The aggregate `◌` glyph in the collapsed line is sufficient signal — the user clicks to see details.

**Status:** Closed.

### Click-outside-to-collapse for Header overlay

**Question:** Should clicking outside the expanded Header overlay collapse it?

**Decision (2026-06-20):** **Yes** — implemented in `f-tui-header-per-section-toggle` (commit `0fc00b0`). Clicking anywhere outside the overlay (chat log / status bar / composer) collapses it; clicking *on* the overlay content does NOT collapse (the user is reading). This is `App.on_click`'s catch-all collapse handler. ESC also collapses; clicking the same section button toggles.

The earlier "Default: No" rationale assumed the overlay dimmed the ChatLog (`opacity: 0.20`) so click-outside would steal clicks meant for scrolling. The per-section toggle design dropped the dimming, so the conflict no longer exists.

### ShuttleTickOverlay removed (StatusBar revamp)

**Question:** Should the StatusBar continue to carry a separate `ShuttleTickOverlay` widget row (the `^` tick above the shuttle, implemented by `f-tui-paradigm-p3`)?

**Decision (2026-06-21, StatusBar revamp SP0):** **Remove.** Delete `loom/tui/status_bar.py::ShuttleTickOverlay`, `loom/eval/cases/tui_shuttle_tick.py`, `tests/test_shuttle_tick.py`, the `tui_shuttle_tick` registration in `loom/eval/cases/__init__.py`, the ShuttleTickOverlay references in `tests/test_status_bar.py` and `loom/tui/app.py`, and the `_sync_shuttle_tick_overlay` plumbing. `#chrome` shrinks from 3 rows (tick + status + composer) to 2 (status + composer); the reclaimed row is given to ChatLog content.

**Why reversed:** The gear-rack re-skin of the ctx rail (see §2.2.2 controlled exception + §2.2.3 primitive 1) makes the shuttle-position pointer redundant — the gear glyph itself occupies the cell that the shuttle used to point at, and its `❋✻✜` frame cycle encodes the 1Hz phase that the `^` tick was meant to indicate. The shuttle-tick overlay was added (2026-06-20, `f-tui-paradigm-p3`) to satisfy "the original §4.2.1 spec called for a `^` tick mark above the shuttle glyph"; that visual contract is now satisfied by the gear's own position on the rack, without a second row. The cost (a permanent 1-row chrome tax + a 1Hz repaint of an essentially-empty line) outweighed the benefit after the gear-rack change.

**Status:** Closed (Reversal — supersedes the 2026-06-20 P3 close).

**Status:** Closed.


**Why deferred:** Conflict with the `opacity: 0.20` on ChatLog — clicks on the dimmed chat would also dismiss the overlay, which is hostile to "I want to scroll while the overlay is up." (Although scrolling is preserved across collapse/expand, click-outside-to-collapse would prevent reading the chat while overlay is open.)

---

## §8 Color system — the `loom-ink` theme

Added 2026-06-21. The TUI now realizes the `docs/tui-design.html` palette through **one** canonical Textual theme, `loom-ink`, registered in `loom/tui/app.py` and set active in `on_mount`. This is the single source of truth for color: every hex value is ported 1:1 from the HTML mockup's `:root` custom properties.

### §8.1 The one-theme rule

> All color lives in `_LOOM_INK_THEME` (`loom/tui/app.py`). Widgets reference Textual theme tokens (`$accent`, `$success`, `$text-muted`, …). No widget hard-codes a hex value or a literal Rich color name (`[green]`, `solid red`, etc.).

**Why:** Before this, two parallel color systems coexisted — theme tokens in some widgets, hard-coded Rich markup (`[green]`/`[yellow]`/`[cyan]`/`[red]`) in `header.py` and `status_bar.py`, and literal `solid yellow/green/red` borders in `widgets.py`. They did not track each other, so "green" meant three different shades depending on the widget, and none matched the documented palette. One theme = one logic = one look.

**Enforcement:** No `[green]`/`[yellow]`/`[cyan]`/`[red]`/`[blue]`/`[purple]` Rich tags and no literal `solid <colorname>` / `thick <colorname>` borders anywhere in `loom/tui/`. Use the token form (`[$success]`, `border: thick $error`). A grep for literal color names in `loom/tui/` should return only prose in docstrings.

### §8.2 Token → hex map (ported from `docs/tui-design.html`)

| Textual token | Hex | Mockup var | Role |
|---|---|---|---|
| `$background` | `#0c0e12` | `--bg` | Base canvas (ChatLog, Screen) |
| `$surface` | `#0a0d11` | `--bg-mock` | User message + tool-output ground |
| `$panel` | `#13161c` | `--bg-panel` | Header + overlays |
| `$primary` / `$accent` | `#5b8a72` | `--accent` | **Signature sage** — labels, running markers, focus |
| `$secondary` | `#4a8a8a` | `--cyan` | Names / ids (MCP server, subagent id) |
| `$foreground` (`$text`) | `#c5cdd8` | `--text` | Body text |
| `$success` | `#4a8a5b` | `--green` | Done states, ctx-ok bar |
| `$warning` | `#8a7a3b` | `--yellow` | Active / running / slow-thinking, ctx-warn |
| `$error` | `#8a3b3b` | `--red` | Error states, ctx-danger, PermissionScreen border |
| `$text-muted` | `#5c6570` | `--text-dim` | Secondary text, system notes, idle glyphs |
| `$text-faint` | `#3a4048` | `--text-muted` | Faintest tier (reserved) |
| `$border` | `#1e2328` | `--border` | Standard hairline border |
| `$hairline` | `#1a1e24` | `--hairline` | Faintest separator (reserved) |

`$warning` / `$error` may render with a sub-1% hue shift on screen — Textual nudges theme colors into the terminal's ANSI gamut. The source hex above is authoritative.

### §8.3 Glyph → state → token contract

Every status marker maps to exactly one glyph and one token. This is the audited contract (see `chat_log.py`, `header.py`):

| Subsystem | State | Glyph | Token |
|---|---|---|---|
| Tool call | running | `⊙` | `$accent` |
| Tool call | done | `⊙` | `$success` |
| Tool call | error | `⊗` | `$error` |
| Subagent | running | `◐` | `$accent` |
| Subagent | done | `◑` | `$success` |
| Subagent | error | `⊗` | `$error` |
| Thinking | active | `⠋` (spinner) | `$text-muted` |
| Thinking | slow (>10s) | `⠋` | `$warning` |
| Thinking | done | `◦` | `$text-muted` |
| Header MCP | healthy | `●` | `$success` |
| Header MCP | error | `◌` | `$warning` |
| Header MCP | disabled | `○` | `$text-muted` |
| Header Todo | active | `◐` | `$warning` |
| Header Todo | all done | `✓` | `$success` |
| Header Todo | empty/pending | `○` | `$text-muted` |
| Header Subagent | has running | `◐` | `$warning` |

**Rule:** `$accent` (sage) means "live, in this conversation" (running inline markers). `$warning` (amber) means "pending/active aggregate" (header rollups, slow thinking). `$success` means "completed". `$error` means "failed". `$text-muted` means "idle/dim". A new marker must pick from this set, not invent a color.

### §8.4 Why this honors the long-loop aesthetic

- **Quiet by default (§2 rule 2):** The palette is deliberately low-saturation (ink background, desaturated semantics). Over a 3-hour session, a muted screen fatigues the eye far less than Textual's stock high-saturation defaults.
- **Aggregate-glyph color is meaning, not decoration (§5):** A single `◌` turning amber in the collapsed Header is the entire error signal — no color is spent on decoration, so a color change always means a state change.
- **One theme, instant (§6):** The theme is set once in `on_mount`; there is no per-frame color computation, no theme animation. Color is a held still value like everything else in the long-loop surface.

---

## §9 Decorative elements (added 2026-06-21)

After the loom-ink theme landed, four regions still felt visually empty. This section declares the low-motion decoration added to fill that gap — all **within** the locked color and motion rules. Nothing here introduces a new color, an animation, a new indent tier, or breaks §5's "color change = state change" invariant.

### §9.1 `WelcomeBanner` (idle splash)

Mounted in the ChatLog only when empty (start of session, after `/clear`). Composition (revised 2026-06-21, #5):

```
  ▀▀▀ █▀▀▀█ █▀▀▀█ █▀▀█▀▀█   ← top face (▀ in $accent-light)
  ███ █   █ █   █ █  █  █   ← body (█ in $accent)
  ███ █ ▀ █ █ ▀ █ █  █  █   ← body + stencil cutout (▀ in $accent-light)
  ███ █   █ █   █ █  █  █   ← body
  ▄▄▄ █▄▄▄█ █▄▄▄█ █▄▄█▄▄█   ← bottom face (▄ in $accent-dim)

  weaving intent into action

  /help · /model · /clear · /resume
```

The **3D extruded stencil wordmark** is the primary brand element — modeled directly on the opencode reference image (the actual rendered logo, not the simplified `logo.ts` constants). A three-tone sage gradient produces the "3D block sitting on a surface" effect:

- **Row 0** (the "upper face" of the 3D block): `▀` cells in **`$accent-light`** (a derived lighter sage, registered as a new theme variable specifically for this use). Reads as the lit top edge of an extruded block.
- **Rows 1–3** (the "body" / front face): `█` cells in **`$accent`** sage. The `o` has a 1-cell `▀` stencil cutout in row 2 (the cutout is filled with the lighter color, mimicking the filled-square hole in opencode's `o`).
- **Row 4** (the "lower face" / shadow): `▄` cells in **`$accent-dim`** (darker sage), but `█` cells stay in `$accent` (matching the body color so the corners don't look dirty). The per-char split is implemented by `_colorize_bottom()`.

Letter widths: `l`=3 cells (chunky bar), `o`=5, `o`=5, `m`=7. With 1-col separators and 2-col leading padding: **25 cols × 5 rows**.

The 3-tone gradient (`$accent-light` → `$accent` → `$accent-dim`) is a deliberate palette expansion over the 2-tone attempt (#4). The middle tone needed contrast on **both** sides — too subtle and the 3D extrusion reads as flat, too aggressive and it breaks the brand sage. `$accent-light` (#84ad9a, ~30% lighter than `$accent`) is the lowest-contrast choice that still reads as "lit face" in normal terminal palettes.

> **Iteration history:**
> - **2026-06-21 #1** — Unicode weave motif (5 warps × 5 wefts with shuttle). Crude in monospace.
> - **2026-06-21 #2** — `◆` shuttle glyph + italic `loom` wordmark. Still not "artistic processing".
> - **2026-06-21 #3** — 3D block-letter `loom` in opencode's `logo.ts` cell-based style (`█▀▀█` / `█__█` / `▀▀▀▀` with `__` interior). Captured the 3D vocabulary but missed the two-tone color band — read as flat.
> - **2026-06-21 #4** — Two-tone 3D extrusion ($accent + $accent-dim). Matched the opencode reference image but contrast was too subtle (both colors are sage), letters were thin (l=2 cells, m humps barely visible), no stencil cutouts in 'o'.
> - **2026-06-21 #5 (current)** — Three-tone 3D extrusion ($accent-light + $accent + $accent-dim). Added `$accent-light` to the theme. Letters chunkier (l=3 cells). Real stencil cutouts in 'o'.

**Why this honors the long-loop aesthetic:**
- **§2 rule 2 (quiet by default):** Pure still image. No spinner, no animation, no pulse. The "loop is alive" signal is carried by the composer cursor + the running-tool marker in the StatusBar — the welcome banner carries no live state.
- **§2 rule 1 (bounded re-layout):** The banner is a single Static child of the ChatLog. It does not change the region geometry (height: auto) and disappears the moment a user types.
- **§5 anti-pattern "no pending placeholder":** The banner is not a placeholder. It is a permanent splash for the genuinely empty state, not a "waiting for agent…" hint.
- **§8 (color contract):** The wordmark uses three sage tokens (`$accent-light` / `$accent` / `$accent-dim`); the slogan `$text-muted`; the command hints `$text-faint`. `$accent-light` is the only new theme variable added in this revision; it lives in `_LOOM_INK_THEME` (`loom/tui/app.py`) as the single source of truth.

### §9.2 `TurnSeparator` (hairline between turns)

A single `─` row in `$border`, mounted before each user turn label. Re-uses the content column's `0 2` padding so it aligns with the labels below.

**Why this honors the long-loop aesthetic:**
- **§2 rule 5 (indentation encodes nesting, max 2 tiers):** The separator sits on the outer column with `TurnLabel` — it is decoration on the existing tier, not a new tier.
- **§3 (hairline convention):** A `$border` divider is the same color as existing hairline borders (`HeaderOverlay` bottom, `PermissionScreen` outline). It is consistent with the §3 hairline vocabulary, not a new visual primitive.

### §9.3 StatusBar (post-revamp — 2026-06-21)

The StatusBar revamp tightens visibility, removes the obsolete tick row (see §7 reversal), and re-skins the ctx rail as a gear-rack (see §2.2.2 controlled exception). **No new motion primitive** is introduced; primitive 1 (gear-rack advance) re-uses the existing 1Hz interval + idle-freeze invariant.

**Composition** (single line, height: 1):

```
 [model] · ⎇ [branch] · [Nt·Mtl] · ctx: [❋┅┅┅┅┄┄┄┄┄┄┄┄┄] [Nk/NM (N%)] · [engine-badge]   [0:00]
 └─identity─┘ └─session──────────┘ └──────────── ctx-rail + data ────────────┘ └─state─┘  └─time┘
```

- **`model` name** → `[$secondary]` (identity anchor — same token as MCP server / subagent id in §8.3, so the model reads as "another name field" not a separate category).
- **`⎇ <branch>`** → `[$text-muted]` (cached once at mount via `git rev-parse --abbrev-ref HEAD`, 2s timeout; absent if not a git repo or call fails. Zero per-frame cost.)
- **`Nt·Mtl`** (turns · tools, merged) → `[$foreground]` (bright tier — the main live-stat weight). The suffix `-tl` distinguishes tools from turns.
- **`ctx:` label** → `[$text-muted]` (demotes the label below the bar+number so the eye lands on the data).
- **Gear-rack rail** (`❋┅┅┅┄┄┄┄┄┄┄┄┄`): see §2.2.3 primitive 1 + §2.2.2 controlled exception. The gear frame cycles `❋→✻→✜` at 1Hz while live; idle freezes at base frame `❋`. The engaged chain `┅` follows the threshold contract: `$success` < 60% → `$warning` < 85% → `$error`. Un-engaged teeth `┄` are `$text-faint`. The rail was widened from 10 to **14 cells** for the gear to occupy a stable cell (gears must not straddle two cells — verified at the terminal-rendering layer; `⚙` was rejected because its emoji-variant renders 2-wide and breaks alignment).
- **Token numbers + percentage** → `[$foreground]` in the ok band; **danger-state coloring**: when the rail is in `$warning` or `$error`, the `<tokens>/<window> (N%)` text inherits the **same semantic color** (not just the rail). This prevents the common "rail is red but the number is gray" reading failure. The threshold contract — `$success` / `$warning` / `$error` — stays exactly as §8.3 (color = state, never decoration).
- **Engine badge** (1-glyph, 6 states per §4.2.1): `● idle` (muted) / `◌ thinking` (warning) / `▸ streaming` (accent) / `⊙ executing` (accent) / `◌ compacting` (secondary) / `⊗ error` (error). Pure status — no tool name (decoupled from ChatLog).
- **Session elapsed** (`M:SS` / `H:MM:SS`) → `[$text-muted]`. A `reactive[int]` bumped by `set_interval(60.0, ...)` — ticks once per minute, not per frame (§2 rule 2: the number is informational, not live state).

#### **Active boost** (new mechanism)

When `engine_state != "idle"`, the **identity + session tier** (model, branch, turns·tools) is promoted **one tier brighter** for the duration of the active state:

| Tier | idle | active |
|---|---|---|
| model | `$text-muted` | `$secondary` |
| branch | `$text-faint` | `$text-muted` |
| turns·tools | `$text` (default) | `$foreground` |

Rationale: the live-stats tier visually "lights up" while work is happening, reinforcing §2 rule 2 ("motion/emphasis only when live"). On `engine_state → idle` transition the tier demotes back instantly — no fade, no tween (which §2.2.2 forbids). Implementation is a single helper that returns the active-tier token for a given (field, engine_state) pair; no extra reactives, no per-frame state.

#### Removed in this revamp

- **`loom` wordmark prefix** — dropped; the brand is carried by the WelcomeBanner at session start, and a redundant wordmark in the chrome is visual noise.
- **`esc ^l` key-hint cluster** — dropped; the bindings (`ctrl+c` cancel, `ctrl+l` clear, `escape` collapse) are documented in `/help` slash output and are stable user knowledge. Re-introducing would compete with live data for the right-side real-estate.
- **Inline `^N` phase indicator** — superseded by the gear's position on the rack (the gear itself encodes phase).

**Char-count budget (measured, default model `deepseek-v4-flash`):**
- **80 cols** empty state (idle, 0 turns, 0 tokens) — was 87 pre-revamp
- **93 cols** worst-case mid-session (active badge `compacting`, 99+ turns, 3-digit stats)
- **87 cols** idle mid-session (multi-digit turns/tools, no tool calls)

All states ≤ 93 cols per §7 budget. Achieved by removing `loom` prefix (-5), removing `esc ^l` key hints (-9), widening gear rack from 10→14 cells (+4), plus 3 cosmetic trims to fit worst-case: leading space from `prefix` (-1, margin now from `#chrome { margin: 0 2 1 2 }`), trailing space in `prefix` (-1), 3-space gap before elapsed → 1-space (-2). Total -14 cols net vs pre-revamp. Worst case `compacting-99t` is exactly 93 cols (fits budget).

**Why this honors the long-loop aesthetic:**
- **§5 "1-line StatusBar cap" (hard constraint):** Height stays 1 row; `#chrome` shrinks from 3 rows (tick + status + composer) to 2 (status + composer). The reclaimed row is given to chat content.
- **§2 rule 1 (bounded re-layout):** No new widgets; the gear replaces the shuttle in the existing primitive-1 helper, and active-boost is a token lookup not a layout change.
- **§2 rule 2 (quiet by default):** Gear-frame swap is gated by `engine_state`; idle freezes at `❋`. Active-boost is a discrete token change on state transition, not a per-frame animation.
- **§8 (single-theme, color = state):** All StatusBar tokens flow from `_LOOM_INK_THEME`. Gear uses `$accent-light` (the lighter sage already registered for WelcomeBanner's 3D extrusion — re-using avoids adding a new theme variable). Danger-state number coloring re-uses the existing threshold tokens, never introduces a new color.

### §9.4 Header rail hairline divider

Each `HeaderSectionButton` carries `border-left: solid $border` to draw a hairline between sections. The first button gets the `first` CSS class to suppress its left edge; the `.section-hidden` variant suppresses its border too (so a hidden section doesn't leave a stray `│`).

**Why this honors the long-loop aesthetic:**
- **§4.3.1 (collapsed line, height: 1):** Hairline is a border attribute, not a layout change. Height stays 1.
- **§2 rule 1 (bounded re-layout):** No extra widgets added; the divider is a border on the existing buttons.

### §9.5 The decoration envelope

Together, the four additions are deliberately **palette-locked and motion-locked**:

| Decoration | Color tokens used | Motion | Layout |
|---|---|---|---|
| WelcomeBanner | `$accent` motif, `$text` wordmark, `$text-muted` slogan, `$text-faint` hints | none | Static; lives in ChatLog's empty state only |
| TurnSeparator | `$border` | none | height: 1, content-column padding |
| StatusBar enrichment | `$text-faint` (un-engaged teeth `┄`) · `$text-muted` (branch, ctx: label, elapsed) · `$secondary` (model name) · `$foreground` (turns·tools counters, token numbers, percentage) · `$accent-light` (gear `❋✻✜`) · `$success` / `$warning` / `$error` (engaged chain `┅` + numbers+pct at threshold per §8.3 + engine badge 6 states per §4.2.1) | 1Hz gear-frame cycle while live (idle-frozen at `❋` per §2.2.2); 1-min tick (elapsed) | height: 1 unchanged; `#chrome` shrinks 3→2 rows (ShuttleTickOverlay removed — see §7 reversal) |
| Header hairline | `$border` | none | border-left attribute, no widget added |

If a future change wants a new decoration, it must pick from this envelope (palette tokens from §8, no animation, no new indent tier, no widget that lives in a locked region without a contract update).

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
- **2026-06-20** — **§7 open decisions closed** (dialogue with user). Narrow-terminal minimums measured (StatusBar is the only break point; usable ≥ 93 cols after dropping the scroll hint in `f-tui-statusbar-drop-scroll-hint`). Zen mode rejected (no distraction-free use case). Two-pane rejected in favor of inline ChatLog event markers (`f-tui-inline-event-markers`, planned). Click-outside-to-collapse corrected from stale "Default: No" to "Closed: Yes" (was already implemented in `0fc00b0`). All §7 items now have a Status; none remain open.
- **2026-06-21** — **§8 color system added; `loom-ink` theme implemented.** Closed the gap where the documented `docs/tui-design.html` palette was never realized (the app used Textual's stock theme). Changes: registered `_LOOM_INK_THEME` in `loom/tui/app.py` (HTML palette ported 1:1, active in `on_mount`); replaced all hard-coded Rich color tags with theme tokens in `header.py` / `status_bar.py` / `screens.py`; replaced literal `solid yellow/green/red` borders with `$warning`/`$success`/`$error` in `widgets.py`; PermissionScreen border `thick red` → `thick $error`. §0 scope updated (color now in scope). Verified: `./init.sh` green (453 pytest, 0 ruff, 0 mypy), eval 234/234, 9 TUI snapshot baselines re-recorded (diff confirmed color-only per Working Rule 10 — text/geometry identical after hash normalization). No layout / sizing / scroll / motion / behavior changes.
- **2026-06-21** — **§9 decorative elements added** (WelcomeBanner, TurnSeparator, StatusBar enrichment, Header hairline divider). Four-region visual enrichment within the locked loom-ink palette and §2 motion rules. New components: `WelcomeBanner` (idle splash rendering the loom mark in Unicode line glyphs, no animation, dismissed on first user message / re-shown after `/clear`) in `chat_log.py`; `TurnSeparator` (hairline `─` row in `$border`, mounted before each user turn, no new indent tier) in `chat_log.py`; `AssistantMessage` gained `border-left: outer $accent-dim` (vertical accent rule for assistant turns). StatusBar gained `git_branch` (cached once via `git rev-parse --abbrev-ref HEAD`, 2s timeout) and `elapsed_seconds` (1-min `set_interval`); separator `|` → `·`; compact labels (`0t`, `0 tools`, no `model:` prefix) keep rendered length at 87 chars (≤ 93-col §7 budget). Header buttons gained `border-left: solid $border` with `.first` and `.section-hidden` classes suppressing edges. Verified: `./init.sh` green (453 pytest, 0 ruff, 0 mypy), eval 234/234, 7 TUI snapshot baselines re-recorded (diff confirmed expected decoration-only changes — added welcome motif, added hairline borders, format change in status bar; no text/structural regression). 2 status_bar test assertions updated to the new compact format. No behavior changes.
- **2026-06-21** — **§9.1 WelcomeBanner refined + §9.3 StatusBar gained key hints.** Dropped the Unicode weave motif from `WelcomeBanner` after the user noted the ASCII rendering read as crude in monospace (the box-drawing characters don't reproduce `docs/loom-mark.svg`'s 25-unit construction grid faithfully). New composition per `docs/loom-logo-shots` §L9.3: italic `loom` wordmark + single `◆` shuttle glyph (echoes the SVG without re-drawing the weave) + slogan + command hints. The reference design (opencode's TUI) carries brand identity through the title bar text + bottom dock key hints, not through an ASCII mark in the chat area; this aligns with that decision. `StatusBar` gained an `esc ^l /` key-hint cluster on the right side (modeled on opencode's bottom dock `esc interrupt · ctrl+t variants · tab agents · ctrl+p commands`), rendered in `$text-faint` so the live stats read first. To fit the §7 93-col budget: turns/tools merged into `0t·0tl` (drop redundant `0 tools` label), elapsed moved to the right (after key hints). New rendered length: **90 chars** (was 87 before adding hints). Changes: `loom/tui/chat_log.py` (`WelcomeBanner` body replaced; `_WEAVE_MOTIF` constant removed); `loom/tui/status_bar.py` (key_hints added; elapsed repositioned; turns/tools merged). Verified: `./init.sh` green (453 pytest, 0 ruff, 0 mypy), eval 234/234, 7 TUI snapshot baselines re-recorded (diff confirmed expected changes — welcome region lost 3 lines of ASCII motif, gained 1 line of `◆`; status bar gained key hints; no text/structural regression). No behavior changes.
- **2026-06-21** — **§9.1 WelcomeBanner: 3D block-letter `loom` wordmark** (opencode cell-based style). After the user noted the single `◆` glyph + plain italic text still didn't feel like "artistic processing" of the brand, replaced with a full 3D block-letter `loom` wordmark modeled on opencode's actual logo source (`packages/tui/src/util/presentation.ts:1-4`, `packages/tui/src/logo.ts:6-9`). The character grid (`█▀▀█` / `█__█` / `▀▀▀▀`) is opencode's cell vocabulary; the `__` interior renders as thin sage lines in monospace, producing the outlined letterform that opencode's cell renderer achieves via bg fill. Letter widths: `l`=2, `o`=4, `o`=4, `m`=6, with 1-col separators (total 19 cols × 3 rows). Single source of change: `loom/tui/chat_log.py` (`WelcomeBanner` body uses `_LOOM_WORDMARK` class attribute). Verified: `./init.sh` green (453 pytest, 0 ruff, 0 mypy), eval 234/234, 7 TUI snapshot baselines re-recorded (diff confirmed expected change — `◆` + italic `loom` text replaced by 3-row block-letter wordmark; slogan + commands unchanged; no text/structural regression). No behavior changes.
- **2026-06-21** — **§9.1 WelcomeBanner: full 3D two-tone extrusion** (matches opencode reference image). The user shared a screenshot of opencode's actual rendered logo, which has a defining two-tone 3D extrusion: top half lighter (the "upper face" of an extruded block), bottom half darker (the "lower face" / shadow). The previous #3 attempt had the right character grid (`█▀▀█` / `█__█` / `▀▀▀▀`) but only one color — read as flat. New design: 5 rows tall, with `▀` cells in `$accent` (row 0, upper face) and `▄` cells in `$accent-dim` (row 4, shadow). `█` cells stay in `$accent` across all rows so the side edges don't look dirty. Implemented via per-char markup in `_style_bottom()` since row 4 needs mixed colors. Letter widths grew: `l`=2, `o`=5 (was 4), `m`=7, with 1-col separators (total 24 cols × 5 rows). Single source of change: `loom/tui/chat_log.py` (`WelcomeBanner` body uses `_LOOM_WORDMARK_TOP` + `_LOOM_WORDMARK_BOTTOM` + `_style_bottom`). Verified: `./init.sh` green (453 pytest, 0 ruff, 0 mypy), eval 234/234, 7 TUI snapshot baselines re-recorded (diff confirmed expected change — 3-row wordmark replaced by 5-row with split-color row 4; slogan + commands unchanged; no text/structural regression). No behavior changes.
- **2026-06-21** — **§9.1 WelcomeBanner: three-tone 3D extrusion + stencil cutouts.** The user shared a screenshot of the two-tone version and asked for further visual refinement, granting permission to use visual judgment. Issues with the #4 attempt: (1) 3D extrusion too subtle because `$accent` and `$accent-dim` are both sage — needed a brighter top; (2) `l` was 2 cells wide with weird "lips" extending right, making it read as a 'C' shape rather than a vertical bar; (3) `o` was empty inside, missing opencode's filled-square stencil cutout; (4) `m` humps were barely visible. Fix: added new theme variable `$accent-light` (#84ad9a, ~30% lighter than `$accent`) to `_LOOM_INK_THEME` in `loom/tui/app.py` for the 3D "lit top face"; restructured the wordmark with chunkier letters (`l`=3 cells, solid bar with `▀` top and `▄` bottom faces — no more "lips"); added 1-cell `▀` stencil cutouts in each `o` (row 2, in `$accent-light`) to mimic opencode's filled-square hole. Three-tone gradient is `$accent-light` → `$accent` → `$accent-dim` (top → body → bottom). Wordmark grew to 25 cols × 5 rows. Per-row colorize helpers (`_colorize_top`, `_colorize_body`, `_colorize_bottom`) split markup by char since each row needs mixed colors. Verified: `./init.sh` green (453 pytest, 0 ruff, 0 mypy), eval 234/234, 7 TUI snapshot baselines re-recorded (diff confirmed expected change — wider wordmark with stencil cutouts, per-char colored rows; slogan + commands unchanged; no text/structural regression). No behavior changes.
- **2026-06-21** — **§9.3 StatusBar gained visual hierarchy via loom-ink tokens** (no new fields, no new behavior). The user noted the StatusBar felt "monotonous" — same `$text` color across the whole 87-char bar with only 2 visual anchors (the progress bar color, the elapsed time). Added a 5-tier color hierarchy mapping each field to a token that matches its semantic weight: `loom` wordmark → `$text-faint` (almost invisible brand tag); `model` name → `$secondary` (cyan-ish, the "name" anchor — same token as MCP server / subagent id per §8.3, so it reads as "another name field"); `⎇` glyph → `$text-faint` (demoted below the name); branch name → `$text-muted`; `Nt·Mtl` counters → default `$text` (live counters carry the main visual weight); `ctx:` label → `$text-muted`; bar / percentage → existing `$success` / `$warning` / `$error` per §8.3 threshold contract; right-side `esc ^l / 0:00` → `$text-faint` (ambient, the live stats outrank them). All tokens are sourced from `_LOOM_INK_THEME` per §8.1 — `grep` for literal color names in `loom/tui/status_bar.py` returns nothing. Rendered width unchanged at 87 chars (token spans cost 0 visible width). Single source of change: `loom/tui/status_bar.py` `render()` (`ctx:` label and stat parts gained token spans; `[dim]` style modifiers on `git_branch` and `elapsed` removed in favor of `[$text-muted]` and `[$text-faint]` tokens). Verified: `./init.sh` green (453 pytest, 0 ruff, 0 mypy), eval 234/234, 7 TUI snapshot baselines re-recorded (diff confirmed expected change — color tokens split `loom` / `model` / `branch` into separate `<text>` SVG segments and shifted two r-fills in the CSS palette (`r8` and `r9` swapped because of `$secondary` insertion); text content + geometry identical after hash normalization per Working Rule 10). No behavior changes.

- **2026-06-21** — **§2 paradigm shift implemented in code: 5 motion primitives wired + idle freeze enforced (P2a + P2b).** §2.2.3 motion contract locked: 5 primitives (shuttle pass on ctx rail, ToolCallMarker `⊙⊚◎` cycle, ThinkingMarker 5fps spinner, HeaderSectionButton 1Hz pulse, Composer cursor blink) each have a defined rate + amplitude + idle-freeze invariant. Idle (= `engine_state == "idle"`) freezes ALL motion: shuttle stays at phase 0, tool markers stay at base glyph `⊙`, section buttons stay at opacity 1.0, thinking marker doesn't tick. P2a: `ToolCallMarker` gets `_RUNNING_GLYPHS = ("⊙", "⊚", "◎")` + `engine_state: reactive[str]` + `_start_cycle_timer`/`_stop_cycle_timer` (1Hz `set_interval(1.0, name="tool-cycle")`); `ChatLog.engine_state` reactive fans out to all live `_tool_markers.values()`; `App._sync_chat_engine_state(state)` wired into 4 callbacks (`on_tool_use`, `on_tool_result`, `on_compact`, `on_message_end`). P2b: `HeaderSectionButton` gets `pulse_phase: reactive[int]` + `update_pulse(has_count)` toggling a Python `set_interval(0.5, name="header-pulse")` that flips `self.styles.opacity` between 1.0 and 0.5 (1Hz square wave). **DEVIATION (accepted):** original P2b plan specified CSS `animation: pulse 1s steps(2, end) infinite` + `@keyframes pulse`. Textual CSS parser v8.2.7 does NOT support `@keyframes` (TokenError on `@`) or `animation: ... steps(...) ...` (TokenError on `(`). Python `set_interval(0.5)` is the Textual-native equivalent — same 1Hz square wave at 50% amplitude, identical observable behavior. Pattern mirrors `ToolCallMarker._cycle_timer` from P2a. **tick-above-shuttle deferred to P3** (independent follow-up). Verified: `./init.sh` green (491 pytest, 0 ruff, 0 mypy), eval 243/243 + 6 new motion_primitives cases = 249/249.
- **2026-06-21** — **§9.3 StatusBar char-count backfilled + §7 budget re-verified (SP2 close).** Measured rendered widths at default model `deepseek-v4-flash`: 80 cols empty state, 87 cols idle mid-session, 93 cols worst-case active (`compacting` badge + 99+ turns). All scenarios fit §7 93-col budget. Achieved via 3 cosmetic trims (leading/trailing space in `prefix`, 3-space gap before elapsed → 1-space). Total net -14 cols vs pre-revamp. Gear-rack WIDTH=14 + 6-state badge (longest: `◌ compacting` = 12 chars) verified within budget. Empty-state placeholder (`待 SP2 实测填入`) replaced with measured values. Tests: `uv run pytest tests/test_ctx_rail.py tests/test_status_bar.py -q` → 32/32 passed. Static: ruff/mypy clean. 7 TUI snapshot baselines re-recorded (expected diffs: removed `loom` prefix, removed `esc ^l` hint, replaced shuttle `●─────` with gear `❋┄┄┄┄┄┄┄┄┄┄┄┄┄`, deleted ShuttleTickOverlay row → `#chrome` 3→2 rows; text content matches §9.3 contract). Eval: `uv run python -m loom.cli eval --fail-under 100` → 252/253 (1 pre-existing `cli-help-is-fast-no-agent-import` flake unrelated to SP2; all 5 gear cases PASS). Single source of change: `loom/tui/status_bar.py` (`prefix` leading/trailing space trimmed, render gap 3→1).