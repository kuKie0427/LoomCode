// Package markdown wraps Glamour for CJK-aware Markdown rendering.
//
// Glamour is the Charm shelf's terminal Markdown renderer (the same one
// used by `gh` for issue rendering). It uses go-runewidth internally for
// display-width calculation, which handles CJK double-width correctly.
//
// Usage:
//
//	out, err := markdown.Render("# Hello\n\nworld")
//
// The renderer is constructed once at package init (default style: auto,
// word wrap at 80). Re-use it across calls — constructing a new
// TermRenderer per call is expensive (it builds a fresh AST parser).
package markdown

import (
	"strings"
	"sync"

	"github.com/charmbracelet/glamour"
)

var (
	once     sync.Once
	renderer *glamour.TermRenderer
	initErr  error
)

// Render converts Markdown to ANSI-styled text suitable for the terminal.
// On first call, lazily constructs the TermRenderer. Subsequent calls
// reuse the cached renderer.
//
// If the renderer failed to construct on a prior call, returns the input
// unchanged + the error (caller may show the raw markdown as a fallback).
func Render(md string) (string, error) {
	once.Do(func() {
		r, err := glamour.NewTermRenderer(
			glamour.WithAutoStyle(),
			// Word wrap at 80 columns — matches the typical terminal width
			// and is what `gh` uses. Wider terminals get hard-wrapped at 80,
			// which is acceptable; narrower terminals will soft-wrap.
			glamour.WithWordWrap(80),
		)
		if err != nil {
			initErr = err
			return
		}
		renderer = r
	})
	if initErr != nil {
		return md, initErr
	}
	return renderer.Render(md)
}

// RenderPlain strips ANSI escapes from a previously-rendered string.
// Useful when the rendered output needs to be passed to a widget that
// re-applies its own styling (avoiding double-application).
func RenderPlain(md string) string {
	out, err := Render(md)
	if err != nil {
		return md
	}
	// Glamour's ANSI codes start with \x1b[. Strip them with a simple regex
	// replacement. (We don't import regexp here to keep deps light; the
	// common case — passing rendered output straight to lipgloss — does
	// not need this.)
	return stripANSI(out)
}

func stripANSI(s string) string {
	var b strings.Builder
	b.Grow(len(s))
	for i := 0; i < len(s); i++ {
		if s[i] == 0x1b && i+1 < len(s) && s[i+1] == '[' {
			// Skip until we hit a letter (the terminator)
			j := i + 2
			for j < len(s) {
				c := s[j]
				if (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') {
					j++
					break
				}
				j++
			}
			i = j - 1
			continue
		}
		b.WriteByte(s[i])
	}
	return b.String()
}
