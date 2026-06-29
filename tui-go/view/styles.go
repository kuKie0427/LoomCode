// Package view: shared lipgloss styles for the status bar.
//
// All colors come from theme.go (ink & sage palette). Status bar uses
// muted text for everything except the engine badge, which switches to
// $accent / $error / $warning / $secondary based on state — see
// statusbar.go::engineBadge for the dispatch.
//
// Mirrors loom/tui/status_bar.py semantic colors:
//
//	idle       ●  $text-muted
//	thinking   ◌  $warning
//	streaming  ▸  $accent
//	executing  ⊙  $accent
//	compacting ◌  $secondary
//	error      ⊗  $error
package view

import "github.com/charmbracelet/lipgloss"

var (
	// statusStyle wraps the whole status bar line — no background
	// (transparent over the chrome $surface), padded 0 1 like the
	// original #status-bar CSS.
	statusStyle = lipgloss.NewStyle().
			Padding(0, 1)

	// ── Engine badge colors (leftmost status element) ─────────────
	// One style per EngineState — mirrors _render_engine_badge.
	statusReadyDotStyle   = lipgloss.NewStyle().Foreground(colorTextMuted).Bold(true)
	statusIdleStyle       = lipgloss.NewStyle().Foreground(colorTextMuted).Bold(true)
	statusThinkingStyle   = lipgloss.NewStyle().Foreground(colorWarning).Bold(true)
	statusStreamingStyle  = lipgloss.NewStyle().Foreground(colorAccent).Bold(true)
	statusExecutingStyle  = lipgloss.NewStyle().Foreground(colorAccent).Bold(true)
	statusCompactingStyle = lipgloss.NewStyle().Foreground(colorSecondary).Bold(true)
	statusErrorBadgeStyle = lipgloss.NewStyle().Foreground(colorError).Bold(true)

	// ── Model field ────────────────────────────────────────────────
	// Active turn: $secondary (teal) to make the model "live".
	// Idle: $text-muted (grey).
	statusModelStyle       = lipgloss.NewStyle().Foreground(colorTextMuted)
	statusModelActiveStyle = lipgloss.NewStyle().Foreground(colorSecondary)

	// ── Other status fields ────────────────────────────────────────
	statusBranchStyle  = lipgloss.NewStyle().Foreground(colorSecondary)
	statusStatsStyle   = lipgloss.NewStyle().Foreground(colorForeground)
	statusWorkdirStyle = lipgloss.NewStyle().Foreground(colorTextMuted).Italic(true)
	statusTimeStyle    = lipgloss.NewStyle().Foreground(colorTextFaint)

	// ── Separator ──────────────────────────────────────────────────
	// Middle dot, $text-faint (barely visible).
	// Mirrors _SEP = "·" + $text-muted in loom/tui/status_bar.py.
	statusSepStyle = lipgloss.NewStyle().Foreground(colorTextFaint)

	// ── ctx gear rail ──────────────────────────────────────────────
	// 14-glyph rail: filled cells (chain) use ratio-driven semantic color;
	// the gear frame cell uses $accent-light; the unused tooth cells use
	// $text-faint. The "ctx:" label uses $text-muted.
	statusGearFrameStyle = lipgloss.NewStyle().Foreground(colorAccentLight).Bold(true)
	statusGearToothStyle = lipgloss.NewStyle().Foreground(colorTextFaint)
	statusCtxLabelStyle  = lipgloss.NewStyle().Foreground(colorTextMuted)

	// ctx numeric tail — three tiers by ratio (mirrors _ctx_color_class).
	//   < 0.60 → $success (dim green)
	//   < 0.85 → $warning (dim yellow)
	//   >= 0.85 → $error   (dim red)
	statusCtxOkStyle     = lipgloss.NewStyle().Foreground(colorSuccess)
	statusCtxWarnStyle   = lipgloss.NewStyle().Foreground(colorWarning)
	statusCtxDangerStyle = lipgloss.NewStyle().Foreground(colorError)
)
