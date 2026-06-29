package view

import (
	"strings"
	"testing"
)

// TestInfoBarEmpty verifies the auto-hide invariant: when no todos and
// no subagents are registered, View() returns "" and Height() returns 0.
// This is the contract that lets main.go skip the InfoBar row entirely
// and reserve 0 lines in the layout.
func TestInfoBarEmpty(t *testing.T) {
	ib := NewInfoBar()
	if v := ib.View(); v != "" {
		t.Errorf("Empty InfoBar.View() = %q, want empty", v)
	}
	if h := ib.Height(); h != 0 {
		t.Errorf("Empty InfoBar.Height() = %d, want 0", h)
	}
	if !ib.Empty() {
		t.Errorf("Empty InfoBar.Empty() = false, want true")
	}
}

// TestInfoBarTodosOnly verifies the todos-only layout: full width,
// 1 line per todo, status glyph prefix, content truncated with "…".
func TestInfoBarTodosOnly(t *testing.T) {
	ib := NewInfoBar()
	ib.SetWidth(60)
	ib.SetTodos([]TodoItem{
		{ID: "1", Content: "implement feature", Status: "in_progress"},
		{ID: "2", Content: "write tests", Status: "pending"},
		{ID: "3", Content: "ship it", Status: "completed"},
	})
	out := ib.View()
	if out == "" {
		t.Fatal("View() returned empty for non-empty InfoBar")
	}
	if h := ib.Height(); h != 3 {
		t.Errorf("Height() = %d, want 3 (one per todo)", h)
	}
	lines := strings.Split(out, "\n")
	if len(lines) != 3 {
		t.Fatalf("View() produced %d lines, want 3", len(lines))
	}
	// Each line must start with the right status glyph.
	checks := []struct{ status, glyph string }{
		{"in_progress", "◐"},
		{"pending", "○"},
		{"completed", "✓"},
	}
	for i, c := range checks {
		if !strings.Contains(lines[i], c.glyph) {
			t.Errorf("line %d (%s): missing glyph %q — got %q", i, c.status, c.glyph, lines[i])
		}
	}
}

// TestInfoBarSubagentsOnly verifies the subagent-only layout: 2 lines
// per subagent (header + body), elapsed formatted as "Xs", and the
// description rendered on line 2.
func TestInfoBarSubagentsOnly(t *testing.T) {
	ib := NewInfoBar()
	ib.SetWidth(60)
	ib.AddSubagent("sa-1", "织针", "researching agent loop bottlenecks")
	out := ib.View()
	if out == "" {
		t.Fatal("View() returned empty for non-empty InfoBar")
	}
	if h := ib.Height(); h != 2 {
		t.Errorf("Height() = %d, want 2 (1 subagent × 2 lines)", h)
	}
	lines := strings.Split(out, "\n")
	if len(lines) != 2 {
		t.Fatalf("View() produced %d lines, want 2", len(lines))
	}
	// Header must contain agent name + "running" + an elapsed suffix.
	if !strings.Contains(lines[0], "织针") {
		t.Errorf("header line missing agent name: %q", lines[0])
	}
	if !strings.Contains(lines[0], "running") {
		t.Errorf("header line missing 'running' status: %q", lines[0])
	}
	if !strings.Contains(lines[0], "s") {
		t.Errorf("header line missing elapsed (Xs): %q", lines[0])
	}
	// Body must contain the description.
	if !strings.Contains(lines[1], "researching") {
		t.Errorf("body line missing description: %q", lines[1])
	}
}

// TestInfoBarSplitLayout verifies that when both todos and subagents
// are present, the View renders them side-by-side with a vertical
// divider, and the height is max(todoLines, subLines).
func TestInfoBarSplitLayout(t *testing.T) {
	ib := NewInfoBar()
	ib.SetWidth(80)
	ib.SetTodos([]TodoItem{
		{ID: "1", Content: "task A", Status: "in_progress"},
		{ID: "2", Content: "task B", Status: "pending"},
		{ID: "3", Content: "task C", Status: "completed"},
	})
	ib.AddSubagent("sa-1", "飞梭", "investigate codebase")
	out := ib.View()
	if out == "" {
		t.Fatal("View() returned empty for split layout")
	}
	// Height = max(3 todos, 2 subagent lines) = 3.
	if h := ib.Height(); h != 3 {
		t.Errorf("Height() = %d, want 3 (max of 3 todos, 2 sub lines)", h)
	}
	lines := strings.Split(out, "\n")
	if len(lines) != 3 {
		t.Fatalf("View() produced %d lines, want 3", len(lines))
	}
	// Each line must contain the divider (│) since both columns are non-empty.
	for i, ln := range lines {
		if !strings.Contains(ln, "│") {
			t.Errorf("split line %d missing divider │: %q", i, ln)
		}
	}
}

// TestInfoBarTodoCap verifies that SetTodos truncates to maxVisibleTodos
// (5). The 6th entry is dropped — without this cap, a long todo list
// would crowd the composer area.
func TestInfoBarTodoCap(t *testing.T) {
	ib := NewInfoBar()
	ib.SetWidth(80)
	todos := []TodoItem{
		{ID: "1", Content: "one", Status: "pending"},
		{ID: "2", Content: "two", Status: "pending"},
		{ID: "3", Content: "three", Status: "pending"},
		{ID: "4", Content: "four", Status: "pending"},
		{ID: "5", Content: "five", Status: "pending"},
		{ID: "6", Content: "six", Status: "pending"},
		{ID: "7", Content: "seven", Status: "pending"},
	}
	ib.SetTodos(todos)
	if h := ib.Height(); h != maxVisibleTodos {
		t.Errorf("Height() = %d, want %d (capped)", h, maxVisibleTodos)
	}
	out := ib.View()
	if strings.Contains(out, "six") || strings.Contains(out, "seven") {
		t.Errorf("capped todos leaked into View(): %q", out)
	}
}

// TestInfoBarSubagentRemove verifies that RemoveSubagent drops the
// entry by id and Empty() returns true when the last one is removed.
func TestInfoBarSubagentRemove(t *testing.T) {
	ib := NewInfoBar()
	ib.SetWidth(80)
	ib.AddSubagent("sa-1", "织针", "task one")
	ib.AddSubagent("sa-2", "飞梭", "task two")
	if h := ib.Height(); h != 4 {
		t.Errorf("Height after 2 subagents = %d, want 4", h)
	}
	ib.RemoveSubagent("sa-1")
	if h := ib.Height(); h != 2 {
		t.Errorf("Height after removing 1 = %d, want 2", h)
	}
	ib.RemoveSubagent("sa-2")
	if !ib.Empty() {
		t.Errorf("Empty() = false after removing all subagents")
	}
	if v := ib.View(); v != "" {
		t.Errorf("View() = %q after Empty, want empty", v)
	}
}

// TestInfoBarMarquee verifies that long descriptions scroll. After
// multiple Tick() calls, the rendered body should differ from the
// initial render (because scrollPhase advanced).
func TestInfoBarMarquee(t *testing.T) {
	ib := NewInfoBar()
	ib.SetWidth(40)
	// Long description that won't fit in 40-2=38 visible cols.
	longDesc := "this is a very long subagent description that should trigger marquee scrolling behavior"
	ib.AddSubagent("sa-1", "织针", longDesc)
	first := ib.View()
	// Advance the scroll phase several times.
	for i := 0; i < 5; i++ {
		ib.Tick()
	}
	second := ib.View()
	if first == second {
		t.Errorf("marquee did not advance: body unchanged after 5 ticks")
	}
}

// TestInfoBarMarqueeShortTextNoScroll verifies that short descriptions
// do NOT scroll — the rendered body should remain stable across ticks.
func TestInfoBarMarqueeShortTextNoScroll(t *testing.T) {
	ib := NewInfoBar()
	ib.SetWidth(80)
	ib.AddSubagent("sa-1", "织针", "short desc")
	first := ib.View()
	for i := 0; i < 10; i++ {
		ib.Tick()
	}
	second := ib.View()
	// The header's elapsed will differ (time passes), so we only
	// check that the body line didn't shift. Find the body line (line 2)
	// and compare just that.
	firstLines := strings.Split(first, "\n")
	secondLines := strings.Split(second, "\n")
	if len(firstLines) < 2 || len(secondLines) < 2 {
		t.Fatalf("expected >= 2 lines, got %d / %d", len(firstLines), len(secondLines))
	}
	if firstLines[1] != secondLines[1] {
		t.Errorf("short text body changed across ticks:\n first: %q\n second: %q", firstLines[1], secondLines[1])
	}
}
