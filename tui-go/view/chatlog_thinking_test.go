package view

import (
	"strings"
	"testing"
)

// TestToggleThinkingExpandsBody verifies that ctrl+t (ToggleThinking)
// folds/unfolds the thinking body. With default-expand on CompleteThinking,
// the body starts visible; toggle folds it, toggle again unfolds.
func TestToggleThinkingExpandsBody(t *testing.T) {
	cl := NewChatLog(80, 20)
	cl.StartAssistantTurn()
	cl.AppendThinkingDelta("I should consider the user's request carefully.")
	cl.CompleteThinking()

	// After CompleteThinking: body is auto-expanded (visible).
	got := cl.View()
	if !strings.Contains(got, "◦ thought") {
		t.Fatalf("after complete: missing thinking marker in view:\n%s", got)
	}
	if !strings.Contains(got, "consider the user's request") {
		t.Fatalf("after complete: thinking body should be auto-expanded, but got:\n%s", got)
	}

	// Toggle: fold the body.
	if !cl.ToggleThinking() {
		t.Fatalf("ToggleThinking returned false (no thinking to show)")
	}
	got = cl.View()
	if strings.Contains(got, "consider the user's request") {
		t.Fatalf("after toggle fold: body should be hidden, but got:\n%s", got)
	}

	// Toggle again: unfold.
	cl.ToggleThinking()
	got = cl.View()
	if !strings.Contains(got, "consider the user's request") {
		t.Fatalf("after toggle unfold: body should be visible, but got:\n%s", got)
	}
}

// TestHistoricalTurnsFreezeThinking verifies that when a new turn starts,
// the previous turn's thinking is frozen (shows "◦ thought") and does NOT
// reflect the current turn's live spinner. Regression guard for the
// "所有 think 标识符都会出现动画" bug.
func TestHistoricalTurnsFreezeThinking(t *testing.T) {
	cl := NewChatLog(80, 30)
	// Turn 1: thinking + complete.
	cl.StartAssistantTurn()
	cl.AppendThinkingDelta("first turn thinking")
	cl.CompleteThinking()
	cl.AppendTextDelta("first turn answer")

	// Turn 2: new thinking (active, not yet complete).
	cl.StartAssistantTurn()
	cl.AppendThinkingDelta("second turn thinking")

	got := cl.View()
	// Turn 1's thinking must show the frozen "◦ thought" form (NOT the
	// live spinner "thinking").
	if !strings.Contains(got, "◦ thought") {
		t.Fatalf("turn 1: missing frozen thinking marker:\n%s", got)
	}
	// Turn 2's thinking must show the live spinner.
	if !strings.Contains(got, "thinking") {
		t.Fatalf("turn 2: missing active thinking marker:\n%s", got)
	}
	// Turn 1's thinking text must NOT appear in turn 2's marker.
	// (It appears only if toggle-expanded; we did not toggle.)
}

// TestStartAssistantTurnResetsThinking verifies that starting a new turn
// does not carry over the previous turn's thinking text into the new
// turn's marker. Regression guard for the "只有思考没有最终输出" bug
// where stale thinking from turn N would show in turn N+1.
//
// Note: with default-expand, turn 1's thinking body IS visible in turn 2's
// view (as historical content). We only check that turn 2's NEW thinking
// marker (when it arrives) doesn't contain turn 1's text.
func TestStartAssistantTurnResetsThinking(t *testing.T) {
	cl := NewChatLog(80, 20)
	cl.StartAssistantTurn()
	cl.AppendThinkingDelta("turn 1 thinking")
	cl.CompleteThinking()
	cl.AppendTextDelta("turn 1 answer")

	// Start turn 2 — should reset thinking buffer.
	cl.StartAssistantTurn()
	// Add turn 2 thinking — should NOT contain turn 1's text.
	cl.AppendThinkingDelta("turn 2 thinking")
	// Turn 2's thinking should be its own text, not mixed with turn 1.
	// The key invariant: turn 2's thinkingText must be exactly "turn 2 thinking".
	for i := len(cl.lines) - 1; i >= 0; i-- {
		if cl.lines[i].role == "assistant" {
			if cl.lines[i].thinkingText != "turn 2 thinking" {
				t.Fatalf("turn 2 thinkingText = %q, want %q",
					cl.lines[i].thinkingText, "turn 2 thinking")
			}
			break
		}
	}
}

// TestToggleThinkingAfterToolCall verifies that thinking body remains
// expandable after the turn has progressed past thinking into tool calls.
// This mirrors the user's reported scenario: thinking done → tools ran →
// user presses ctrl+t → expect body to expand AND be scrolled into view
// (not pushed off-screen by GotoBottom).
func TestToggleThinkingAfterToolCall(t *testing.T) {
	// Use a larger viewport so thinking marker + body + tool all fit.
	// (Previously 5 rows, but default-expand now needs more room.)
	cl := NewChatLog(80, 20)
	cl.StartAssistantTurn()
	cl.AppendThinkingDelta("Let me read the file first.")
	cl.CompleteThinking()
	cl.StartToolCall("read_file", map[string]any{"path": "/tmp/x"}, "t1")
	cl.CompleteToolCall("t1", "ok", false)

	// Sanity: marker + tool both visible.
	got := cl.View()
	if !strings.Contains(got, "◦ thought") {
		t.Fatalf("missing thinking marker:\n%s", got)
	}

	// Toggle should fold the body (it's auto-expanded now).
	if !cl.ToggleThinking() {
		t.Fatalf("ToggleThinking returned false after tool call")
	}
	got = cl.View()
	// After folding, body should not be visible
	if strings.Contains(got, "Let me read the file first.") {
		t.Fatalf("after toggle fold: body should be hidden, got:\n%s", got)
	}
	// Toggle again to expand
	cl.ToggleThinking()
	got = cl.View()
	if !strings.Contains(got, "Let me read the file first.") {
		t.Fatalf("after toggle expand: body should be visible, got:\n%s", got)
	}
}

// TestMultiRoundThinkingReset verifies that StartNewLLMCall (triggered
// by EventAssistantMessageStart) resets the thinking buffer between
// LLM rounds within a single turn. Without this, round 2's thinking
// would append to round 1's stale buffer.
//
// Scenario: round 1 thinking + tool_use → round 2 thinking (final response).
// Expected: round 2's thinkingText should NOT contain round 1's thinking,
// and both rounds' thinking bodies should render in chronological order.
func TestMultiRoundThinkingReset(t *testing.T) {
	cl := NewChatLog(80, 30)
	cl.StartAssistantTurn()

	// Round 1: thinking + tool_use
	cl.AppendThinkingDelta("round 1 reasoning")
	cl.StartToolCall("bash", map[string]any{"cmd": "ls"}, "t1")
	cl.CompleteToolCall("t1", "output", false)

	// Per-LLM-call signal (EventAssistantMessageStart) — freezes round 1
	// thinking into its own chatLine + creates a new chatLine for round 2.
	cl.StartNewLLMCall()

	// Round 2: thinking only (DeepSeek reasoning-only response)
	cl.AppendThinkingDelta("round 2 final answer")
	cl.CompleteThinking()

	got := cl.View()
	// Both rounds' thinking should be visible (chronological order)
	if !strings.Contains(got, "round 1 reasoning") {
		t.Errorf("round 1 thinking missing from view:\n%s", got)
	}
	if !strings.Contains(got, "round 2 final answer") {
		t.Errorf("round 2 thinking missing from view:\n%s", got)
	}
	// Verify round 2's chatLine thinkingText is NOT mixed with round 1
	for i := len(cl.lines) - 1; i >= 0; i-- {
		if cl.lines[i].role == "assistant" && cl.lines[i].thinkingDone {
			if cl.lines[i].thinkingText != "round 2 final answer" {
				t.Errorf("round 2 thinkingText = %q, want %q",
					cl.lines[i].thinkingText, "round 2 final answer")
			}
			break
		}
	}
}

// TestPromoteThinkingToContentWithToolCalls verifies that
// PromoteThinkingToContent promotes thinking to body even when the turn
// had tool calls, as long as the final content is empty (DeepSeek
// reasoning-only response in the last round).
//
// This is the core regression test for "最终文本输出在了思考里" —
// previously the toolCallsThisTurn > 0 early return blocked promotion.
func TestPromoteThinkingToContentWithToolCalls(t *testing.T) {
	cl := NewChatLog(80, 20)
	cl.StartAssistantTurn()

	// Round 1: thinking + tool_use
	cl.AppendThinkingDelta("I need to check the file")
	cl.StartToolCall("bash", map[string]any{"cmd": "ls"}, "t1")
	cl.CompleteToolCall("t1", "output", false)

	// Per-LLM-call signal + round 2 reasoning-only response
	cl.StartNewLLMCall()
	cl.AppendThinkingDelta("The answer is 42")
	cl.CompleteThinking()

	// Turn ends — PromoteThinkingToContent should fire
	cl.PromoteThinkingToContent()

	got := cl.View()
	// The thinking text should appear as the assistant body (│ border)
	// because content was empty and thinking was the response.
	if !strings.Contains(got, "The answer is 42") {
		t.Errorf("promoted thinking missing from view:\n%s", got)
	}
}

// TestDefaultExpandedThinking verifies that thinking body is
// auto-expanded on completion (so the user sees reasoning without
// pressing Ctrl+T). This addresses the visual confusion where the
// thinking marker sat directly above the assistant body.
func TestDefaultExpandedThinking(t *testing.T) {
	cl := NewChatLog(80, 20)
	cl.StartAssistantTurn()
	cl.AppendThinkingDelta("my reasoning text")
	cl.CompleteThinking()

	got := cl.View()
	// Thinking body should be visible without toggling
	if !strings.Contains(got, "my reasoning text") {
		t.Errorf("thinking body not auto-expanded:\n%s", got)
	}
}
