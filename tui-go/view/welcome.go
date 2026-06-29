package view

import (
	"strings"

	"github.com/charmbracelet/lipgloss"
)

// WelcomeBanner renders the idle-state splash shown while the ChatLog is
// empty. Mirrors loom/tui/chat_log.py::WelcomeBanner (lines 350-442).
//
// Composition: a 3D extruded "loom" wordmark with a three-tone gradient
// that produces the "3D block sitting on a surface" effect:
//
//	$accent-light  ── row 0 (top face, the lit edge)
//	$accent        ── rows 1–3 (body, the front face)
//	$accent-dim    ── row 4 (bottom face, the shadow)
//
// Below the wordmark: tagline + slash-command hints.
// Pure still image — no animation. Dismissed on first user message.
//
// In the original Python TUI the WelcomeBanner class was dead code (never
// mounted); the actual welcome screen was WelcomeModal (a ModalScreen
// with an Input). The Go TUI uses the composer as the input instead,
// so we render only the visual identity piece (the 3D wordmark +
// tagline + hints) as a splash in the empty ChatLog area.

// 3D extruded "loom" wordmark, 25 cols × 5 rows. Row 2 has a 1-cell `▀`
// stencil cutout inside each 'o' (opencode-style filled hole). Row 4 is
// split so `▄` chars use $accent-dim (shadow) and `█` chars stay in
// $accent (body color, matching the side edges).
const (
	loomWordmarkRow0      = "  ▀▀▀ █▀▀▀█ █▀▀▀█ █▀▀█▀▀█"
	loomWordmarkRow1      = "  ███ █   █ █   █ █  █  █"
	loomWordmarkRow2      = "  ███ █ ▀ █ █ ▀ █ █  █  █"
	loomWordmarkRow3      = "  ███ █   █ █   █ █  █  █"
	loomWordmarkRowBottom = "  ▄▄▄ █▄▄▄█ █▄▄▄█ █▄▄█▄▄█"
)

// colorizeTop renders row 0 — top face. `▀` in $accent-light, `█` in $accent.
func colorizeTop(row string) string {
	var b strings.Builder
	for _, ch := range row {
		switch ch {
		case '▀':
			b.WriteString(accentLightStyle.Render("▀"))
		case '█':
			b.WriteString(accentStyle.Render("█"))
		default:
			b.WriteRune(ch)
		}
	}
	return b.String()
}

// colorizeBody renders body rows 1-3 — `▀` (stencil cutout) in $accent-light,
// rest in $accent.
func colorizeBody(row string) string {
	var b strings.Builder
	for _, ch := range row {
		switch ch {
		case '▀':
			b.WriteString(accentLightStyle.Render("▀"))
		case '█':
			b.WriteString(accentStyle.Render("█"))
		default:
			b.WriteRune(ch)
		}
	}
	return b.String()
}

// colorizeBottom renders row 4 — bottom face. `▄` in $accent-dim, `█` in $accent.
func colorizeBottom(row string) string {
	var b strings.Builder
	for _, ch := range row {
		switch ch {
		case '▄':
			b.WriteString(accentDimStyle.Render("▄"))
		case '█':
			b.WriteString(accentStyle.Render("█"))
		default:
			b.WriteRune(ch)
		}
	}
	return b.String()
}

// RenderWelcomeBanner builds the welcome splash as a centered block.
// width/height are the chat area dimensions; the banner is centered
// horizontally and vertically within them. Returns "" if the area is too
// small (< 30 cols or < 8 rows) to show the wordmark legibly.
func RenderWelcomeBanner(width, height int) string {
	if width < 30 || height < 8 {
		return ""
	}
	wordmark := strings.Join([]string{
		colorizeTop(loomWordmarkRow0),
		colorizeBody(loomWordmarkRow1),
		colorizeBody(loomWordmarkRow2),
		colorizeBody(loomWordmarkRow3),
		colorizeBottom(loomWordmarkRowBottom),
	}, "\n")

	tagline := welcomeTaglineStyle.Render("weaving intent into action")
	hints := welcomeHintsStyle.Render("/help  ·  /model <name>  ·  /connect  ·  /clear  ·  /resume")

	block := wordmark + "\n\n" + tagline + "\n\n" + hints
	return lipgloss.Place(width, height, lipgloss.Center, lipgloss.Center, block)
}

// ── WelcomeBanner styles ─────────────────────────────────────────────
//
// Mirrors WelcomeBanner's DEFAULT_CSS (chat_log.py:373-381):
//
//	WelcomeBanner { padding: 2 0 0 0; margin: 0 0 1 0; content-align: center middle }
//	tagline: $text-muted italic
//	hints:   $text-faint
var (
	accentLightStyle    = lipgloss.NewStyle().Foreground(colorAccentLight)
	accentStyle         = lipgloss.NewStyle().Foreground(colorAccent)
	accentDimStyle      = lipgloss.NewStyle().Foreground(colorAccentDim)
	welcomeTaglineStyle = lipgloss.NewStyle().Foreground(colorTextMuted).Italic(true)
	welcomeHintsStyle   = lipgloss.NewStyle().Foreground(colorTextFaint)
)
