// Package markdown wraps Glamour for CJK-aware Markdown rendering.
//
// Glamour is the Charm shelf's terminal Markdown renderer (the same one
// used by `gh` for issue rendering). It uses go-runewidth internally for
// display-width calculation, which handles CJK double-width correctly.
//
// Usage:
//
//	out, err := markdown.Render("# Hello\n\nworld")           // legacy 80-col wrap
//	out, err := markdown.RenderWidth("# Hello\n\nworld", 100) // explicit width
//
// Renderers are cached by width — constructing a new TermRenderer per
// call is expensive (it builds a fresh AST parser), so we keep one
// renderer per distinct width in a sync.Map.
package markdown

import (
	"strings"
	"sync"

	"github.com/charmbracelet/glamour"
)

var (
	// renderers caches TermRenderer instances by width. Key is the
	// word-wrap width; value is *glamour.TermRenderer. The cache avoids
	// reconstructing the AST parser on every render call.
	renderers sync.Map

	// initErrs caches construction errors by width (rare — usually a
	// bad glamour style option). Cleared on successful construction.
	initErrs sync.Map
)

// defaultWidth is the fallback word-wrap width when the caller doesn't
// specify one (e.g. legacy Render() calls). 80 matches the typical
// terminal width and what `gh` uses.
const defaultWidth = 80

// getRenderer returns a cached TermRenderer for the given word-wrap
// width, constructing one on first use. Construction errors are also
// cached so we don't retry on every call.
func getRenderer(width int) (*glamour.TermRenderer, error) {
	if v, ok := initErrs.Load(width); ok {
		return nil, v.(error)
	}
	if v, ok := renderers.Load(width); ok {
		return v.(*glamour.TermRenderer), nil
	}
	// Use "dark" style instead of AutoStyle: AutoStyle sends an OSC 11
	// query to the terminal to detect background color, but under
	// Bubble Tea's AltScreen that query is captured as literal text
	// and rendered onto the screen as garbled escape sequences.
	// "dark" is a fixed, dark-background-friendly palette.
	r, err := glamour.NewTermRenderer(
		glamour.WithStandardStyle("dark"),
		glamour.WithWordWrap(width),
	)
	if err != nil {
		initErrs.Store(width, err)
		return nil, err
	}
	renderers.Store(width, r)
	return r, nil
}

// Render converts Markdown to ANSI-styled text suitable for the terminal.
// Uses the default 80-column word wrap. Equivalent to RenderWidth(md, 80).
//
// If the renderer failed to construct on a prior call, returns the input
// unchanged + the error (caller may show the raw markdown as a fallback).
func Render(md string) (string, error) {
	return RenderWidth(md, defaultWidth)
}

// RenderWidth converts Markdown to ANSI-styled text with explicit word-wrap
// width. Use this when the caller knows the actual viewport width (e.g.
// ChatLog passes c.viewport.Width minus padding/border so glamour wraps
// at the correct column instead of the hardcoded 80).
//
// The renderer is cached per width — the first call for a given width
// pays the construction cost; subsequent calls reuse the cached renderer.
func RenderWidth(md string, width int) (string, error) {
	if width < 1 {
		width = 1
	}
	r, err := getRenderer(width)
	if err != nil {
		return md, err
	}
	return r.Render(md)
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
