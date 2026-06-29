// Package view: shared utilities for ANSI-aware width calculation and padding.
package view

import (
	"strings"

	"github.com/mattn/go-runewidth"
)

// padRightBlock right-pads each line of s with spaces to width w.
// Ensures the viewport's right border draws continuously even when
// content lines are shorter than the viewport width.
func padRightBlock(s string, w int) string {
	if w <= 0 {
		return s
	}
	lines := strings.Split(s, "\n")
	for i, ln := range lines {
		vis := stripANSIWidth(ln)
		if vis < w {
			lines[i] = ln + strings.Repeat(" ", w-vis)
		}
	}
	return strings.Join(lines, "\n")
}

// stripANSIWidth returns the visible width of s after removing ANSI escapes.
// Note: counts each non-ANSI byte as width 1 — does NOT handle double-width
// CJK characters. For content that may contain CJK, callers should account
// for the discrepancy or use a width-aware library. In practice the TUI's
// bordered boxes are filled with ASCII labels + rendered Markdown, where
// the discrepancy is acceptable (borders still draw, just with slight offset).
func stripANSIWidth(s string) int {
	width := 0
	i := 0
	for i < len(s) {
		if s[i] == 0x1b && i+1 < len(s) && s[i+1] == '[' {
			j := i + 2
			for j < len(s) {
				c := s[j]
				if (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') {
					j++
					break
				}
				j++
			}
			i = j
			continue
		}
		width++
		i++
	}
	return width
}

// runeWidth returns the visible width of a single rune via runewidth.
// CJK glyphs return 2, ASCII returns 1, combining marks return 0.
// Used by InfoBar marquee/truncation to handle double-width characters
// correctly (slicing by byte would split a CJK glyph and corrupt the
// terminal). Mirrors lipgloss.Width's per-rune walk for non-ANSI strings.
func runeWidth(r rune) int { return runewidth.RuneWidth(r) }
