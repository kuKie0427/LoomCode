// Package view implements the TUI widgets.
package view

import (
	"fmt"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/muesli/reflow/wordwrap"

	"github.com/lanf/loom-tui/markdown"
)

// MAIN_AGENT_NAME mirrors loom/tui/chat_log.py::MAIN_AGENT_NAME — the
// weaving-themed display name for the main orchestrator agent.
const MAIN_AGENT_NAME = "织轴"

// braille spinner frames — mirrors _SPINNER_FRAMES in chat_log.py.
// 10-frame cycle for the ThinkingMarker animation, 50ms per frame.
var thinkingSpinnerFrames = []string{"⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"}

// thinkingFlushInterval is the 50ms batch window for thinking deltas.
// Mirrors _THINKING_FLUSH_INTERVAL in chat_log.py — without batching,
// each thinking token triggers a Markdown re-parse and starves the
// streaming path.
const thinkingFlushInterval = 50 * time.Millisecond

// slowThinkingThreshold mirrors _SLOW_THRESHOLD_SECONDS = 10.0 — past
// this point the ThinkingMarker switches to $warning color and slows
// the spinner to 1 frame per 4 ticks (200ms effective).
const slowThinkingThreshold = 10 * time.Second

// ChatLog is the scrolling transcript viewport: user/assistant messages,
// streaming overlay, thinking display, and tool call markers.
//
// Visual language mirrors loom/tui/chat_log.py:
//   - NO outer rounded border — a single hairline border-top separates
//     the chat log from the header above (see main.go layout).
//   - Each turn is introduced by a "▎ you" / "▎ 织轴" TurnLabel in
//     $primary / $accent color (no background, bold+dim).
//   - UserMessage: $surface background, no border.
//   - AssistantMessage: $background background, $accent-dim LEFT border.
//   - ToolCallMarker: "⊙ name · running" → "✓ name · done" / "✗ name · error".
//   - ThinkingMarker: braille spinner "⠋ thinking · 12s" → "◦ thought · 12s"
//     on completion. Toggleable via main.go's key handler (Enter/Space
//     when the marker is the last chat line).
//   - SubagentMarker: "◐ agent · desc" (running, $accent) →
//     "◑ agent · desc · 12s" (done, $success dim) /
//     "⊗ agent · desc · 12s" (error, $error). Keyed by subagent_id so
//     EventSubagentEnd can find + freeze the right line.
type ChatLog struct {
	viewport  viewport.Model
	lines     []chatLine
	streamBuf strings.Builder
	thinking  strings.Builder // scratch buffer for the current turn's thinking
	toolCalls []toolCall
	width     int
	height    int

	// subagentMarkers tracks in-flight subagent lines by subagent_id so
	// CompleteSubagentMarker can find + freeze them. Pruned of done entries
	// opportunistically — keeps the slice short for linear scan.
	subagentMarkers []subagentMarker

	// Active-turn thinking scratch — the global spinner state for the
	// CURRENTLY-accumulating thinking turn. Per-turn frozen state lives
	// in each chatLine (see chatLine.thinkingDone/Text/Elapsed).
	//
	// thinkingActive: true while we're accumulating thinking_delta events
	//   for the current turn. Drives TickThinking spinner rotation.
	// thinkingStart:  when accumulation began (elapsed + slow-detect).
	// thinkingPhase:  braille spinner frame index (0-9).
	thinkingActive bool
	thinkingStart  time.Time
	thinkingPhase  int

	// thinkingMarkerY is the viewport Y offset of the latest thinking
	// marker row, recorded during render() so ToggleThinking can scroll
	// the viewport back to it when expanding the body. Without this,
	// GotoBottom() after expand pushes the body off-screen (the body
	// sits above newer tool-call / summary rows in the rendered output).
	thinkingMarkerY int

	// pendingThinkingScroll is set by ToggleThinking when expanding so
	// the next render() scrolls to the thinking marker instead of
	// GotoBottom(). Consumed (cleared) by render() after the scroll.
	pendingThinkingScroll bool

	// toolCallsThisTurn counts tool calls made since the current
	// assistant turn started. Reset on StartAssistantTurn, incremented
	// on StartToolCall. Used by PromoteThinkingToContent() to detect
	// DeepSeek reasoning-only responses (thinking + no text + no tools
	// → the thinking IS the response).
	toolCallsThisTurn int
}

type chatLine struct {
	role    string // "user", "assistant", "tool", "system", "subagent", "error"
	content string
	toolID  string // for "tool" role, links back to toolCalls entry

	// Per-turn thinking state — mirrors the per-AssistantMessage
	// ThinkingMarker in the original TUI. Each assistant line carries
	// its own thinking text + elapsed so historical turns render their
	// own frozen "◦ thought · 12s" instead of reflecting the live spinner.
	//
	// thinkingActive: this turn is currently accumulating thinking deltas.
	// thinkingDone:   this turn's thinking completed (frozen marker).
	// thinkingText:   accumulated thinking body (for toggle expand).
	// thinkingElapsed: formatted "12s" set when CompleteThinking fires.
	// thinkingExpanded: per-turn expand flag (Ctrl+T toggles LAST turn only).
	// thinkingIsResponse: when true, the model emitted its response as
	//   reasoning_content with no text content and no tool calls (DeepSeek
	//   thinking-mode behavior). Render the thinking text as the assistant
	//   body so the user sees the answer. Set by PromoteThinkingToContent().
	thinkingActive     bool
	thinkingDone       bool
	thinkingText       string
	thinkingElapsed    string
	thinkingExpanded   bool
	thinkingIsResponse bool
}

type toolCall struct {
	id      string
	name    string
	input   map[string]any
	output  string
	isError bool
	done    bool
}

// subagentMarker tracks a running SubagentMarker line so
// CompleteSubagentMarker can find it by subagent_id and freeze the
// glyph/color from ◐ (running) → ◑ (done) / ⊗ (error).
type subagentMarker struct {
	subagentID  string
	agentName   string
	description string
	lineIdx     int // index into ChatLog.lines
	done        bool
}

// NewChatLog builds a ChatLog sized to width x height.
func NewChatLog(width, height int) *ChatLog {
	vp := viewport.New(width, height)
	return &ChatLog{viewport: vp, width: width, height: height}
}

// SetSize resizes the underlying viewport. The chat log has no border or
// padding (mirrors the original TUI's #chat-log CSS: padding 1 2 0 2),
// so the full width is available for content. We reserve 1 column on
// each side as left/right text padding so TurnLabel "▎ you" aligns with
// the user message body below it.
func (c *ChatLog) SetSize(width, height int) {
	c.width = width
	c.height = height
	c.viewport.Width = width - 4 // 2 left + 2 right padding (matches $padding 0 2)
	if c.viewport.Width < 1 {
		c.viewport.Width = 1
	}
	c.viewport.Height = height
	if c.viewport.Height < 1 {
		c.viewport.Height = 1
	}
	c.render()
}

// AppendUserMessage appends a TurnSeparator (if not the first line) +
// "▎ you" TurnLabel + UserMessage body.
//
// The TurnSeparator mirrors loom/tui/chat_log.py::append_user_message
// (line 808): 60× `─` in $border + dim, padded 0 2, with a 1-row top
// margin. It visually separates turns so the user can scan back. We
// skip it before the very first message (no prior turn to separate).
func (c *ChatLog) AppendUserMessage(text string) {
	if len(c.lines) > 0 {
		sep := turnSepStyle.Render(strings.Repeat("─", turnSepWidth))
		c.lines = append(c.lines, chatLine{role: "separator", content: sep})
	}
	c.lines = append(c.lines, chatLine{role: "user", content: text})
	c.render()
}

// PromoteThinkingToContent handles the DeepSeek thinking-mode edge case:
// when the model emits its response entirely as reasoning_content (no text
// content), the thinking text IS the response. This marks the last
// assistant chatLine's thinkingIsResponse=true so the renderer shows it
// as the body instead of hiding it in a collapsed marker.
//
// Called from main.go on EventAssistantTurnEnd. Safe to call unconditionally —
// no-op when content is non-empty (the model emitted proper text).
//
// Note: we intentionally do NOT skip turns that had tool calls. In a
// multi-round turn (round 1: thinking + tool_use → round 2: thinking-only
// response), the last round's thinking may be the actual answer. With
// StartNewLLMCall now resetting the thinking buffer per round, the
// thinkingText on the chatLine reflects only the last round's reasoning,
// so promoting it is correct.
func (c *ChatLog) PromoteThinkingToContent() {
	for i := len(c.lines) - 1; i >= 0; i-- {
		if c.lines[i].role == "assistant" {
			line := &c.lines[i]
			if line.content == "" && line.thinkingDone && line.thinkingText != "" {
				line.thinkingIsResponse = true
				c.render()
			}
			return
		}
	}
}

// AppendAssistantSummary adds a "▣  {model} · {elapsed}" marker at the
// end of an assistant turn. Mirrors loom/tui/chat_log.py::AssistantSummary
// (line 333) — appears below the final assistant message.
//
// Format: `▣  {model} · {elapsed}`
//   - glyph ▣ in $accent (bold)
//   - model name in $foreground (default)
//   - ` · ` separator + elapsed in $text-muted
//
// elapsed is seconds (float64 from EventAssistantTurnEnd.Duration).
func (c *ChatLog) AppendAssistantSummary(model string, elapsed float64) {
	glyph := summaryGlyphStyle.Render("▣")
	modelStr := summaryModelStyle.Render(model)
	elapsedStr := summaryElapsedStyle.Render("· " + formatAssistantElapsed(elapsed))
	content := fmt.Sprintf("%s  %s %s", glyph, modelStr, elapsedStr)
	c.lines = append(c.lines, chatLine{role: "summary", content: content})
	c.render()
}

// AppendSystemLine appends a system notification line (e.g. subagent markers).
func (c *ChatLog) AppendSystemLine(text string) {
	c.lines = append(c.lines, chatLine{role: "system", content: text})
	c.render()
}

// ReplaceLastSystemLine finds the last "system" line whose content starts with
// `oldPrefix` and replaces it with `newText`. If no matching line exists, it
// appends `newText` as a new system line. Used by /sessions to replace the
// "Loading sessions…" placeholder with the actual list.
func (c *ChatLog) ReplaceLastSystemLine(oldPrefix, newText string) {
	for i := len(c.lines) - 1; i >= 0; i-- {
		if c.lines[i].role == "system" && strings.HasPrefix(c.lines[i].content, oldPrefix) {
			c.lines[i].content = newText
			c.render()
			return
		}
	}
	c.lines = append(c.lines, chatLine{role: "system", content: newText})
	c.render()
}

// Clear removes all lines and resets thinking/tool state. Mirrors
// slash_commands.py::handle_clear which calls chat_log.clear_content().
// Used by the /clear slash command.
func (c *ChatLog) Clear() {
	c.lines = nil
	c.toolCalls = nil
	c.subagentMarkers = nil
	c.streamBuf.Reset()
	c.thinking.Reset()
	c.thinkingActive = false
	c.thinkingPhase = 0
	c.pendingThinkingScroll = false
	c.render()
}

// StartNewLLMCall handles the per-LLM-call signal (EventAssistantMessageStart).
// Within a single assistant turn, the agent loop may make multiple LLM calls
// (e.g. round 1: thinking + tool_use → round 2: thinking + final text after
// tool returns). Each round's thinking must be independent — without this
// reset, round 2's thinking would append to round 1's stale buffer.
//
// What this does:
//
//  1. Freezes the current round's thinking into the last assistant chatLine
//     (so it shows as a frozen "◦ thought · Xs" marker, not a live spinner).
//
//  2. Resets the thinking scratch buffer so the next round starts clean.
//
//  3. Leaves thinkingActive=false so the next AppendThinkingDelta triggers
//     a fresh timer + reset.
//
//  4. If the last assistant chatLine already has thinking or content (i.e.
//     this is NOT the first LLM call of the turn), creates a NEW assistant
//     chatLine for the next round. This makes each round's thinking/body
//     render independently in chronological order:
//
//     ▎ 织轴
//     ◦ thought · 5s      ← round 1 thinking (frozen)
//     [round 1 thinking body]
//     ✓ bash · done       ← round 1 tool
//     ◦ thought · 3s      ← round 2 thinking (frozen)
//     [round 2 thinking body]
//     [round 2 assistant text]
//
//     Without this, all rounds share one chatLine and new thinking
//     overwrites the previous round's frozen thinking.
//
// Safe to call when no thinking is active (no-op for freeze). The first
// LLM call of a turn is a no-op for chatLine creation (StartAssistantTurn
// already created the first one and it's still empty).
func (c *ChatLog) StartNewLLMCall() {
	c.freezeActiveThinking()
	c.thinking.Reset()
	c.thinkingActive = false
	c.pendingThinkingScroll = false
	// Find the last assistant chatLine. If it already has thinking or
	// content, it represents a completed round — start a new chatLine
	// for the next round so each round renders independently.
	for i := len(c.lines) - 1; i >= 0; i-- {
		if c.lines[i].role == "assistant" {
			if c.lines[i].thinkingDone || c.lines[i].content != "" {
				c.lines = append(c.lines, chatLine{role: "assistant", content: ""})
			}
			break
		}
	}
	c.render()
}

// StartAssistantTurn resets the streaming buffer and starts a fresh
// assistant line with a "▎ 织轴" TurnLabel.
//
// Also freezes any leftover active thinking from the previous turn into
// that turn's chatLine, then resets the global thinking scratch buffer.
// Without this, the previous turn's thinking would bleed into the new
// turn (issue: "只有思考没有最终输出" — stale thinking from turn N
// would show in turn N+1's marker).
func (c *ChatLog) StartAssistantTurn() {
	c.streamBuf.Reset()
	// Freeze the previous turn's thinking if it was left active.
	c.freezeActiveThinking()
	c.thinking.Reset()
	c.thinkingActive = false
	c.toolCallsThisTurn = 0 // reset per-turn tool-call counter
	c.lines = append(c.lines, chatLine{role: "assistant", content: ""})
	c.render()
}

// freezeActiveThinking writes the current global thinking scratch state
// into the last assistant chatLine so it renders as a frozen "◦ thought"
// marker instead of the live spinner. No-op when nothing is active.
//
// Also default-expands the thinking body so historical turns show their
// reasoning inline (consistent with CompleteThinking's auto-expand).
func (c *ChatLog) freezeActiveThinking() {
	if !c.thinkingActive {
		return
	}
	for i := len(c.lines) - 1; i >= 0; i-- {
		if c.lines[i].role == "assistant" {
			c.lines[i].thinkingActive = false
			c.lines[i].thinkingDone = true
			c.lines[i].thinkingText = c.thinking.String()
			c.lines[i].thinkingElapsed = formatThinkingElapsed(time.Since(c.thinkingStart))
			c.lines[i].thinkingExpanded = true
			break
		}
	}
}

// AppendTextDelta appends a streamed text chunk to the current assistant line.
//
// A single assistant turn can span multiple LLM calls (e.g. thinking +
// tool_use in call 1, then final text in call 2 after the tool returns).
// Between calls, tool-call markers and subagent markers are appended to
// c.lines, so the last line is NOT necessarily the assistant line. We scan
// backwards to find the last assistant line — this is O(N) in the worst
// case but N is typically small (tool markers sit right below the
// assistant line, so the scan terminates in 1-3 steps).
func (c *ChatLog) AppendTextDelta(text string) {
	c.streamBuf.WriteString(text)
	for i := len(c.lines) - 1; i >= 0; i-- {
		if c.lines[i].role == "assistant" {
			c.lines[i].content = c.streamBuf.String()
			break
		}
	}
	c.render()
}

// AppendThinkingDelta accumulates thinking text and activates the
// ThinkingMarker (braille spinner). The first delta starts the timer;
// subsequent deltas just append. The marker line is rendered with the
// current spinner frame, NOT the full body — body only shows on toggle.
//
// Per-round reset: a single assistant turn can span multiple LLM calls
// (e.g. round 1: thinking + tool_use; round 2: thinking + final text
// after tool returns). Each round's thinking must be independent —
// without resetting c.thinking here, round 2's thinking would append
// to round 1's stale buffer, producing "round1 thinking + round2
// thinking" in a single marker. That corrupted buffer is what made
// the final text appear inside the thinking display: round 2's
// thinking (which contained the answer) was silently merged into
// round 1's frozen "◦ thought" marker.
//
// The reset fires only when thinkingActive transitions false→true
// (i.e. a NEW thinking round is starting). Mid-round deltas (true→true)
// do NOT reset — they continue accumulating the current round's text.
func (c *ChatLog) AppendThinkingDelta(text string) {
	if !c.thinkingActive {
		c.thinkingActive = true
		c.thinkingStart = time.Now()
		c.thinking.Reset() // clear stale buffer from prior round
	}
	c.thinking.WriteString(text)
	// Mirror into the last assistant line so render uses per-turn state.
	for i := len(c.lines) - 1; i >= 0; i-- {
		if c.lines[i].role == "assistant" {
			c.lines[i].thinkingActive = true
			c.lines[i].thinkingText = c.thinking.String()
			break
		}
	}
	c.render()
}

// CompleteThinking freezes the marker on "◦ thought · <elapsed>" and
// stops the spinner. Mirrors ThinkingMarker.set_complete.
//
// Per-turn: writes the frozen state into the last assistant chatLine.
func (c *ChatLog) CompleteThinking() {
	if !c.thinkingActive {
		return
	}
	elapsed := time.Since(c.thinkingStart)
	c.thinkingActive = false
	for i := len(c.lines) - 1; i >= 0; i-- {
		if c.lines[i].role == "assistant" {
			c.lines[i].thinkingActive = false
			c.lines[i].thinkingDone = true
			c.lines[i].thinkingText = c.thinking.String()
			c.lines[i].thinkingElapsed = formatThinkingElapsed(elapsed)
			// Default-expand the thinking body on completion so the
			// user can see the reasoning without pressing Ctrl+T. This
			// also visually separates the thinking content (italic, dim
			// background, no border) from the assistant body (│ left
			// border), preventing the "final text appears inside
			// thinking" visual confusion. User can still Ctrl+T to fold.
			c.lines[i].thinkingExpanded = true
			break
		}
	}
	c.render()
}

// TickThinking advances the braille spinner frame. Call this from the
// top-level model's tick handler (1Hz is fine — frame rotation is
// modulo'd internally). Mirrors ThinkingMarker._tick spinner logic.
// Returns true if the marker state changed (so the caller can decide
// whether to re-render the whole viewport).
func (c *ChatLog) TickThinking() bool {
	if !c.thinkingActive {
		return false
	}
	// Slow spin when past slowThinkingThreshold: 1 frame per 4 ticks.
	elapsed := time.Since(c.thinkingStart)
	if elapsed >= slowThinkingThreshold {
		if c.thinkingPhase%4 != 0 {
			c.thinkingPhase++
			return false
		}
	}
	c.thinkingPhase++
	return true
}

// ToggleThinking expands/collapses the full thinking body display.
// Mirrors ThinkingMarker.on_click + Enter/Space bindings.
// Returns true if toggled, false if there's no thinking to show.
//
// Operates on the LAST assistant line that has thinking — so historical
// turns' thinking can be re-expanded too. On expand, sets
// pendingThinkingScroll so the next render() scrolls the viewport to the
// thinking marker row instead of GotoBottom(). Without this, expanding
// would leave the body off-screen because newer tool-call and summary
// rows sit below it in the rendered output.
func (c *ChatLog) ToggleThinking() bool {
	// Find the last assistant line with thinking content.
	for i := len(c.lines) - 1; i >= 0; i-- {
		if c.lines[i].role != "assistant" {
			continue
		}
		line := &c.lines[i]
		if line.thinkingText == "" && !line.thinkingActive {
			return false
		}
		line.thinkingExpanded = !line.thinkingExpanded
		if line.thinkingExpanded {
			c.pendingThinkingScroll = true
		}
		c.render()
		return true
	}
	return false
}

// StartToolCall adds an "in progress" tool call marker:
//
//	"⊙ name · running   key=val key=val"
//
// Mirrors ToolCallMarker._RUNNING_GLYPHS = ("⊙", "⊚", "◎") at 1Hz.
func (c *ChatLog) StartToolCall(name string, input map[string]any, id string) {
	c.toolCallsThisTurn++
	c.toolCalls = append(c.toolCalls, toolCall{
		id: id, name: name, input: input,
	})
	c.lines = append(c.lines, chatLine{
		role:    "tool",
		content: fmt.Sprintf("⊙ %s · running   %s", name, formatToolInput(input)),
		toolID:  id,
	})
	c.render()
}

// CompleteToolCall marks a tool call done and updates its marker line:
//
//	success: "✓ name · done    → <output>"
//	error:   "✗ name · error   → <output>"
//
// Mirrors ToolCallMarker.tool-done (success dim) / .tool-error styling.
func (c *ChatLog) CompleteToolCall(id string, output string, isError bool) {
	idx := -1
	for i := range c.toolCalls {
		if c.toolCalls[i].id == id {
			c.toolCalls[i].output = output
			c.toolCalls[i].isError = isError
			c.toolCalls[i].done = true
			idx = i
			break
		}
	}
	if idx < 0 {
		return
	}
	name := c.toolCalls[idx].name
	for i := range c.lines {
		if c.lines[i].role == "tool" && c.lines[i].toolID == id {
			var mark, label, body string
			if isError {
				mark = toolErrStyle.Render("✗")
				label = toolErrStyle.Render(name + " · error")
				body = toolOutStyle.Render("→ " + truncate(output, 80))
			} else {
				mark = toolDoneStyle.Render("✓")
				label = toolDoneStyle.Render(name + " · done")
				body = toolOutStyle.Render("→ " + truncate(output, 80))
			}
			c.lines[i].content = fmt.Sprintf("%s %s   %s", mark, label, body)
			break
		}
	}
	c.render()
}

// AppendSubagentMarker adds a "◐ agent · description" running marker line
// and tracks it by subagent_id so CompleteSubagentMarker can find + freeze
// it later. Mirrors SubagentMarker three-state coloring:
//
//	running: "◐ agent · desc"               $accent (sage, bold)
//	done:    "◑ agent · desc · 12s"          $success (dim)
//	error:   "⊗ agent · desc · 12s"          $error
//
// The content is pre-styled (ANSI embedded) so render()'s "subagent" role
// case emits it verbatim without re-wrapping.
func (c *ChatLog) AppendSubagentMarker(subagentID, agentName, description string) {
	glyph := subagentRunGlyphStyle.Render("◐")
	label := subagentRunLabelStyle.Render(fmt.Sprintf("%s · %s", agentName, description))
	content := fmt.Sprintf("%s %s", glyph, label)
	c.lines = append(c.lines, chatLine{role: "subagent", content: content})
	c.subagentMarkers = append(c.subagentMarkers, subagentMarker{
		subagentID:  subagentID,
		agentName:   agentName,
		description: description,
		lineIdx:     len(c.lines) - 1,
	})
	c.render()
}

// CompleteSubagentMarker finds a running marker by subagent_id and freezes
// it to the done or error form. state="complete"/"success" → ◑ dim;
// state="error" (or any non-success value) → ⊗ error color.
//
// elapsed is seconds (float64 from the protocol). The marker line's content
// is rewritten in place — lineIdx stays stable because we only ever append
// to c.lines, never reorder or delete.
func (c *ChatLog) CompleteSubagentMarker(subagentID string, elapsed float64, state string) {
	for i := range c.subagentMarkers {
		m := &c.subagentMarkers[i]
		if m.subagentID != subagentID || m.done {
			continue
		}
		m.done = true
		elapsedStr := formatSubagentElapsed(elapsed)
		var glyph, label string
		if state == "error" {
			glyph = subagentErrGlyphStyle.Render("⊗")
			label = subagentErrLabelStyle.Render(
				fmt.Sprintf("%s · %s · %s", m.agentName, m.description, elapsedStr))
		} else {
			glyph = subagentDoneGlyphStyle.Render("◑")
			label = subagentDoneLabelStyle.Render(
				fmt.Sprintf("%s · %s · %s", m.agentName, m.description, elapsedStr))
		}
		content := fmt.Sprintf("%s %s", glyph, label)
		if m.lineIdx < len(c.lines) {
			c.lines[m.lineIdx].content = content
		}
		break
	}
	c.render()
}

// AppendErrorNote adds a system error line with severity=error styling.
// Mirrors SystemNote with severity="error": "✗ <message>" rendered in
// $error (bold). The content is pre-styled so render()'s "error" role
// case emits it verbatim — no muted/italic wrapping like plain system notes.
func (c *ChatLog) AppendErrorNote(message string) {
	glyph := errorNoteGlyphStyle.Render("✗")
	body := errorNoteBodyStyle.Render(message)
	content := fmt.Sprintf("%s %s", glyph, body)
	c.lines = append(c.lines, chatLine{role: "error", content: content})
	c.render()
}

// View returns the rendered viewport. NO outer border — the chat log
// is separated from the header by a single hairline drawn in main.go.
func (c *ChatLog) View() string {
	return c.viewport.View()
}

// IsEmpty reports whether the chat log has no content lines yet.
// Used by main.go to decide whether to render the WelcomeBanner splash
// in place of the empty chat area.
func (c *ChatLog) IsEmpty() bool {
	return len(c.lines) == 0
}

// ContentWidth returns the inner content width (viewport width minus
// left/right padding). Used by main.go to size the WelcomeBanner splash.
func (c *ChatLog) ContentWidth() int {
	return c.viewport.Width
}

// ContentHeight returns the inner content height (viewport height).
// Used by main.go to size the WelcomeBanner splash.
func (c *ChatLog) ContentHeight() int {
	return c.viewport.Height
}

// GotoBottom scrolls the viewport to the bottom (call after each append).
func (c *ChatLog) GotoBottom() {
	c.viewport.GotoBottom()
}

// ViewportHeight returns the visible viewport height (for PageUp/PageDown
// scroll sizing — half-page per press).
func (c *ChatLog) ViewportHeight() int { return c.viewport.Height }

// ScrollUp moves the viewport up by n lines. Clamped by the viewport.
func (c *ChatLog) ScrollUp(n int) {
	c.viewport.LineUp(n)
}

// ScrollDown moves the viewport down by n lines. Clamped by the viewport.
func (c *ChatLog) ScrollDown(n int) {
	c.viewport.LineDown(n)
}

// ScrollToTop jumps to the top of the scrollback.
func (c *ChatLog) ScrollToTop() {
	c.viewport.GotoTop()
}

// ScrollToBottom jumps to the bottom of the scrollback (e.g. after PgUp).
func (c *ChatLog) ScrollToBottom() {
	c.viewport.GotoBottom()
}

// Update forwards messages to the viewport (for mouse-wheel scrolling).
func (c *ChatLog) Update(msg tea.Msg) (viewport.Model, tea.Cmd) {
	return c.viewport.Update(msg)
}

// render rebuilds the viewport content from the accumulated lines.
//
// Markdown rendering is applied to assistant messages (mirrors
// AssistantMessage being a Markdown widget); user/tool/system lines are
// styled directly. Each turn is introduced by a TurnLabel row, then the
// body block, then a blank separator line.
//
// ThinkingMarker is rendered between the assistant TurnLabel and body
// when thinking is active (or has been completed within this turn) —
// it shows the live spinner + elapsed, OR the frozen "◦ thought · 12s"
// once done. The expanded ThinkingDisplay body renders below the marker
// when thinkingExpanded=true.
func (c *ChatLog) render() {
	var b strings.Builder
	// Track whether we've already rendered the TurnLabel for the
	// current assistant turn. Multiple assistant chatLines within a
	// single turn (one per LLM round) should only show ONE "▎ 织轴"
	// label at the top — subsequent rounds just append their thinking
	// marker + body inline.
	assistantLabelRendered := false
	for _, line := range c.lines {
		switch line.role {
		case "user":
			// User turn resets the assistant label flag.
			assistantLabelRendered = false
			// TurnLabel "▎ you" in $primary (sage), bold+dim.
			b.WriteString(userTurnLabelStyle.Render("▎ you") + "\n")
			// UserMessage: $surface background, padding 0 2.
			// Render markdown with the actual content width so glamour
			// word-wraps at the correct column. Content width = viewport.Width - padding(4).
			userContentWidth := c.viewport.Width - 4
			if userContentWidth < 1 {
				userContentWidth = 1
			}
			rendered, err := markdown.RenderWidth(line.content, userContentWidth)
			userBody := line.content
			if err == nil && rendered != "" {
				userBody = strings.TrimRight(rendered, "\n")
			}
			b.WriteString(userBodyStyle.Render(userBody) + "\n\n")
		case "assistant":
			// Only render the "▎ 织轴" TurnLabel for the FIRST assistant
			// chatLine in a turn. Subsequent rounds (created by
			// StartNewLLMCall) render their thinking/body inline without
			// a duplicate label.
			if !assistantLabelRendered {
				b.WriteString(asstTurnLabelStyle.Render("▎ "+MAIN_AGENT_NAME) + "\n")
				assistantLabelRendered = true
			}
			// Per-turn ThinkingMarker: render from this line's frozen
			// state (historical turns) or live spinner (current active
			// turn). Only the line with thinkingActive=true uses the
			// global spinner phase; all others show "◦ thought · 12s".
			if line.thinkingActive || line.thinkingDone {
				c.thinkingMarkerY = strings.Count(b.String(), "\n")
				b.WriteString(c.renderThinkingMarker(&line) + "\n")
				if line.thinkingExpanded {
					b.WriteString(c.renderThinkingBody(&line) + "\n")
				}
			}
			// DeepSeek thinking-mode fallback: when the model emits its
			// response entirely as reasoning_content (content="") with
			// no tool calls, the thinking text IS the response. In that
			// case render it as the assistant body so the user sees the
			// answer instead of an empty turn. The thinkingIsResponse
			// flag is set by PromoteThinkingToContent() when the turn
			// ends without tool calls.
			bodyText := line.content
			if bodyText == "" && line.thinkingIsResponse {
				bodyText = line.thinkingText
			}
			if bodyText == "" {
				b.WriteString("\n")
				continue
			}
			// Render markdown with the actual content width so glamour
			// word-wraps at the correct column instead of the hardcoded
			// 80. Content width = viewport.Width - padding(2) - border(1).
			contentWidth := c.viewport.Width - 3
			if contentWidth < 1 {
				contentWidth = 1
			}
			rendered, err := markdown.RenderWidth(bodyText, contentWidth)
			body := bodyText
			if err == nil && rendered != "" {
				body = strings.TrimRight(rendered, "\n")
			}
			b.WriteString(asstBodyStyle.Render(body) + "\n\n")
		case "tool":
			// ToolCallMarker: padded left by 2 (matches CSS padding 0 0 0 2).
			b.WriteString("  " + line.content + "\n")
		case "subagent":
			// SubagentMarker: content is pre-styled (◐/◑/⊗ + label).
			// Emit verbatim — wrapping would nest ANSI and muddy colors.
			b.WriteString("  " + line.content + "\n")
		case "error":
			// SystemNote severity=error: content is pre-styled (✗ + msg).
			b.WriteString("  " + line.content + "\n\n")
		case "separator":
			// TurnSeparator: blank line above + 60× ─ in $border dim.
			// Content already includes padding (0 2) via turnSepStyle.
			b.WriteString("\n" + line.content + "\n")
		case "summary":
			// AssistantSummary: pre-styled ▣ + model + elapsed.
			b.WriteString(line.content + "\n\n")
		case "system":
			// SystemNote: $text-muted, italic dim, padded 0 2.
			b.WriteString(systemStyle.Render("  "+line.content) + "\n\n")
		}
	}
	c.viewport.SetContent(b.String())
	// When ToggleThinking expanded the body, scroll to the thinking marker
	// so the body is visible instead of being pushed off-screen by the
	// GotoBottom that normally follows streaming/appends. Consume the flag
	// so subsequent renders (next delta, next tool call) resume GotoBottom.
	if c.pendingThinkingScroll && c.thinkingMarkerY > 0 {
		c.viewport.SetYOffset(c.thinkingMarkerY)
		c.pendingThinkingScroll = false
	} else {
		c.viewport.GotoBottom()
	}
}

// renderThinkingMarker emits the spinner or the frozen "done" form.
// Mirrors ThinkingMarker's render logic:
//
//	active:  "<spinner> thinking · <elapsed>s"
//	done:    "◦ thought · <elapsed>"
//
// Color: $text-muted by default, switches to $warning when slow
// (>10s elapsed while active).
//
// Per-turn: reads state from the chatLine so historical turns show their
// frozen "◦ thought · 12s" and only the active turn animates the spinner.
func (c *ChatLog) renderThinkingMarker(line *chatLine) string {
	if line.thinkingDone {
		return "  " + thinkingDoneStyle.Render("◦ thought · "+line.thinkingElapsed)
	}
	// Active — use global spinner phase + live elapsed.
	elapsed := time.Since(c.thinkingStart)
	elapsedStr := formatThinkingElapsed(elapsed)
	frame := thinkingSpinnerFrames[c.thinkingPhase%len(thinkingSpinnerFrames)]
	style := thinkingActiveStyle
	if elapsed >= slowThinkingThreshold {
		style = thinkingSlowStyle
	}
	return "  " + style.Render(frame+" thinking · "+elapsedStr)
}

// renderThinkingBody emits the full thinking text body when expanded.
// Uses the $boost 30% background + italic dim from ThinkingDisplay CSS.
//
// Per-turn: reads text from the chatLine.
func (c *ChatLog) renderThinkingBody(line *chatLine) string {
	body := line.thinkingText
	if body == "" {
		return ""
	}
	// Word-wrap at viewport width minus the 2-space left prefix and the
	// style's horizontal padding (2 each side). Keep lines inside the
	// boost background block instead of overflowing the viewport.
	wrapWidth := c.viewport.Width - 6
	if wrapWidth < 1 {
		wrapWidth = 1
	}
	wrapped := wordwrap.String(body, wrapWidth)
	// Prefix every wrapped line with two spaces so the thinking body
	// sits aligned with the thinking marker above it.
	lines := strings.Split(wrapped, "\n")
	for i, l := range lines {
		lines[i] = "  " + l
	}
	return thinkingBodyStyle.Render(strings.Join(lines, "\n")) + "\n"
}

// formatThinkingElapsed renders a duration as a compact seconds string.
//
//	5s   → "5s"
//	65s  → "1m5s"
//	125s → "2m5s"
func formatThinkingElapsed(d time.Duration) string {
	s := int(d.Seconds())
	if s < 60 {
		return fmt.Sprintf("%ds", s)
	}
	m := s / 60
	rem := s % 60
	return fmt.Sprintf("%dm%ds", m, rem)
}

// formatSubagentElapsed renders a float64 seconds value (from the protocol)
// as a compact elapsed string. Mirrors formatThinkingElapsed's output format
// but takes the raw float64 that SubagentEndParams carries.
//
//	5.0   → "5s"
//	65.3  → "1m5s"
//	125.0 → "2m5s"
func formatSubagentElapsed(elapsed float64) string {
	s := int(elapsed)
	if s < 60 {
		return fmt.Sprintf("%ds", s)
	}
	m := s / 60
	rem := s % 60
	return fmt.Sprintf("%dm%ds", m, rem)
}

// formatAssistantElapsed renders a float64 seconds value for the
// AssistantSummary marker. Mirrors loom/tui/chat_log.py:1123-1127:
//
//	< 60s  → "{secs}s"           (e.g. "12s")
//	>= 60s → "{mins}m {secs:02d}s" (e.g. "2m 30s")
//
// Note the space after "m" and zero-padded seconds — differs from
// formatSubagentElapsed which uses no space and no padding.
func formatAssistantElapsed(elapsed float64) string {
	s := int(elapsed)
	if s < 60 {
		return fmt.Sprintf("%ds", s)
	}
	m := s / 60
	rem := s % 60
	return fmt.Sprintf("%dm %02ds", m, rem)
}

// turnSepWidth is the number of ─ chars in a TurnSeparator. Mirrors
// loom/tui/chat_log.py:808 `TurnSeparator("─" * 60)`.
const turnSepWidth = 60

// formatToolInput renders the tool input as a short "key=val key=val" string.
func formatToolInput(input map[string]any) string {
	if len(input) == 0 {
		return ""
	}
	parts := make([]string, 0, len(input))
	for k, v := range input {
		s := fmt.Sprintf("%v", v)
		if len(s) > 40 {
			s = s[:37] + "..."
		}
		parts = append(parts, fmt.Sprintf("%s=%s", k, s))
	}
	return strings.Join(parts, " ")
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}

// ── ink & sage styles ────────────────────────────────────────────────
//
// Color tokens come from theme.go. The styles below mirror the original
// Textual TUI's DEFAULT_CSS for each widget:
//
//	TurnLabel           { color: $accent; bold+dim; padding 0; margin 1 0 0 0 }
//	TurnLabel.role-user { color: $primary }
//	UserMessage         { background: $surface; padding 0 2; border: none }
//	AssistantMessage    { background: $background; padding 0 2;
//	                       border-left: outer $accent-dim }
//	ToolCallMarker      { color: $accent; padding 0 0 0 2 }
//	ToolCallMarker.tool-done  { color: $success; dim }
//	ToolCallMarker.tool-error { color: $error }
//	SystemNote          { color: $text-muted; italic dim; padding 0 2 }
var (
	// TurnLabels — single line, no background, sage text bold+dim.
	// Mirrors TurnLabel { height: 1; color: $accent; text-style: bold dim }.
	userTurnLabelStyle = lipgloss.NewStyle().
				Foreground(colorPrimary).
				Bold(true).
				Faint(true) // Faint = Textual's "dim" modifier.
	asstTurnLabelStyle = lipgloss.NewStyle().
				Foreground(colorAccent).
				Bold(true).
				Faint(true)

	// Message bodies — width-constrained so the surface block and the
	// accent-dim left border span exactly the viewport inner width.
	userBodyStyle = lipgloss.NewStyle().
			Foreground(colorForeground).
			Padding(0, 2)
	asstBodyStyle = lipgloss.NewStyle().
			Foreground(colorForeground).
			Padding(0, 1).
			BorderLeft(true).
			BorderStyle(lipgloss.NormalBorder()).
			BorderForeground(colorAccentDim)

	// ToolCallMarker — glyph + name colored by terminal state.
	toolDoneStyle = lipgloss.NewStyle().Foreground(colorSuccess).Bold(true)
	toolErrStyle  = lipgloss.NewStyle().Foreground(colorError).Bold(true)
	toolOutStyle  = lipgloss.NewStyle().Foreground(colorTextMuted).Italic(true)

	// SystemNote / SubagentMarker — italic muted text.
	systemStyle = lipgloss.NewStyle().Foreground(colorTextMuted).Italic(true).Faint(true)

	// ── ThinkingDisplay / ThinkingMarker ────────────────────────────
	//
	// Mirrors chat_log.py:
	//   ThinkingMarker       { color: $accent; bold }
	//   ThinkingMarker.slow { color: $warning }      // >10s elapsed
	//   ThinkingMarker.done  { color: $success; dim }
	//   ThinkingDisplay      { background: $boost 30%; color: $text-muted;
	//                          padding 0 2; italic; border: none }
	thinkingActiveStyle = lipgloss.NewStyle().Foreground(colorAccent).Bold(true)
	thinkingSlowStyle   = lipgloss.NewStyle().Foreground(colorWarning).Bold(true)
	thinkingDoneStyle   = lipgloss.NewStyle().Foreground(colorSuccess).Faint(true)
	thinkingBodyStyle   = lipgloss.NewStyle().
				Background(colorBoost).
				Foreground(colorTextMuted).
				Italic(true).
				Padding(0, 2)

	// ── SubagentMarker three-state color ────────────────────────────
	//
	// Mirrors loom/tui/chat_log.py::SubagentMarker:
	//   .running  { color: $accent; bold }     // ◐
	//   .done     { color: $success; dim }      // ◑
	//   .error    { color: $error }             // ⊗
	// Glyph + label are styled separately so we can bold the glyph while
	// keeping the label at normal weight (matches the original's visual
	// hierarchy where the state indicator pops and the description recedes).
	subagentRunGlyphStyle  = lipgloss.NewStyle().Foreground(colorAccent).Bold(true)
	subagentRunLabelStyle  = lipgloss.NewStyle().Foreground(colorAccent)
	subagentDoneGlyphStyle = lipgloss.NewStyle().Foreground(colorSuccess).Bold(true)
	subagentDoneLabelStyle = lipgloss.NewStyle().Foreground(colorSuccess).Faint(true)
	subagentErrGlyphStyle  = lipgloss.NewStyle().Foreground(colorError).Bold(true)
	subagentErrLabelStyle  = lipgloss.NewStyle().Foreground(colorError)

	// ── SystemNote severity=error ──────────────────────────────────
	// Mirrors SystemNote with severity="error": "✗ <message>" in $error bold.
	// Plain system notes (severity=info) use the systemStyle above.
	errorNoteGlyphStyle = lipgloss.NewStyle().Foreground(colorError).Bold(true)
	errorNoteBodyStyle  = lipgloss.NewStyle().Foreground(colorError).Bold(true)

	// ── TurnSeparator ──────────────────────────────────────────────
	// Mirrors loom/tui/chat_log.py::TurnSeparator CSS:
	//   { color: $border; text-style: dim; padding: 0 2; margin: 1 0 0 0 }
	// 60× ─ chars, dimmed, with 2-col horizontal padding.
	turnSepStyle = lipgloss.NewStyle().
			Foreground(colorBorder).
			Faint(true).
			Padding(0, 2)

	// ── AssistantSummary ▣ ─────────────────────────────────────────
	// Mirrors loom/tui/chat_log.py::AssistantSummary:
	//   ▣  {model} · {elapsed}
	//   glyph ▣: $accent bold
	//   model: $foreground (default — no override)
	//   " · " + elapsed: $text-muted
	//   CSS: { width: 1fr; padding: 0 2; margin: 0 0 1 0 }
	summaryGlyphStyle   = lipgloss.NewStyle().Foreground(colorAccent).Bold(true)
	summaryModelStyle   = lipgloss.NewStyle().Foreground(colorForeground)
	summaryElapsedStyle = lipgloss.NewStyle().Foreground(colorTextMuted)
)
