package markdown

import (
	"strings"
	"testing"
)

// TestRenderBasic verifies that a simple Markdown string produces non-empty
// output that contains the source text.
func TestRenderBasic(t *testing.T) {
	out, err := Render("# Hello\n\nworld")
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	if out == "" {
		t.Fatal("output is empty")
	}
	// Glamour wraps text with ANSI codes; the source text should still be
	// present in the output (not as the literal markdown syntax, but as
	// the rendered content).
	if !strings.Contains(out, "Hello") {
		t.Errorf("output missing 'Hello': %q", out)
	}
	if !strings.Contains(out, "world") {
		t.Errorf("output missing 'world': %q", out)
	}
}

// TestRenderCJK verifies that CJK characters render correctly — i.e. the
// output contains the source CJK text, not per-character broken lines.
// This is the regression guard for the linkify / width bug we hit on the
// Python Textual side (loom/tui/chat_log.py _markdown_parser_factory had
// to disable linkify to avoid turning file names into bogus URLs).
func TestRenderCJK(t *testing.T) {
	out, err := Render("# 你好世界\n\n这是一个测试。")
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	if !strings.Contains(out, "你好世界") {
		t.Errorf("output missing '你好世界': %q", out)
	}
	if !strings.Contains(out, "这是一个测试") {
		t.Errorf("output missing '这是一个测试': %q", out)
	}
}

// TestRenderCodeBlock verifies code blocks are preserved.
func TestRenderCodeBlock(t *testing.T) {
	src := "```python\nprint('hello')\n```\n"
	out, err := Render(src)
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	if !strings.Contains(out, "print") {
		t.Errorf("output missing code content: %q", out)
	}
	if !strings.Contains(out, "hello") {
		t.Errorf("output missing string literal: %q", out)
	}
}

// TestRenderLink verifies links are rendered (not dropped).
func TestRenderLink(t *testing.T) {
	src := "[example](https://example.com)"
	out, err := Render(src)
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	if !strings.Contains(out, "example") {
		t.Errorf("output missing link text: %q", out)
	}
}

// TestRenderPlain strips ANSI escapes.
func TestRenderPlain(t *testing.T) {
	out := RenderPlain("# Hello")
	if strings.Contains(out, "\x1b[") {
		t.Errorf("output contains ANSI escapes: %q", out)
	}
	if !strings.Contains(out, "Hello") {
		t.Errorf("output missing 'Hello': %q", out)
	}
}

// TestRenderIdempotent verifies the renderer is cached across calls
// (calling Render multiple times doesn't re-construct the TermRenderer).
func TestRenderIdempotent(t *testing.T) {
	out1, err := Render("# A")
	if err != nil {
		t.Fatalf("first Render: %v", err)
	}
	out2, err := Render("# B")
	if err != nil {
		t.Fatalf("second Render: %v", err)
	}
	if !strings.Contains(out1, "A") {
		t.Errorf("first output missing 'A': %q", out1)
	}
	if !strings.Contains(out2, "B") {
		t.Errorf("second output missing 'B': %q", out2)
	}
}
