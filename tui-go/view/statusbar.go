package view

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/charmbracelet/lipgloss"
)

// EngineState mirrors loom/tui/status_bar.py::EngineState.
// 6 states the engine cycles through during a turn.
type EngineState string

const (
	StateIdle       EngineState = "idle"
	StateThinking   EngineState = "thinking"
	StateStreaming  EngineState = "streaming"
	StateExecuting  EngineState = "executing"
	StateCompacting EngineState = "compacting"
	StateError      EngineState = "error"
)

// ctx gear rail constants — mirrors loom/tui/status_bar.py L14-21.
const (
	ctxRailWidth   = 14
	ctxWarnRatio   = 0.60
	ctxDangerRatio = 0.85
)

// Gear frames + chain/tooth glyphs — mirrors _GEAR_FRAMES, _CHAIN, _TOOTH.
var (
	gearFrames = []string{"❋", "✻", "✜"}
	gearChain  = "┅"
	gearTooth  = "┄"
)

// Compact levels — mirrors _COMPACT_LEVELS.
// Each entry is (min_visible_width, level_name). The renderer tries from
// most verbose (full) to most compact (badge), stopping at the first
// that fits the available width.
var compactLevels = []struct {
	minWidth int
	level    string
}{
	{88, "full"},     // 0: model · branch · stats · ctx gear + numbers · badge
	{78, "nobranch"}, // 1: drop git branch
	{68, "cmodel"},   // 2: shorten model name
	{55, "cstats"},   // 3: compact turns/tools (drop tools count)
	{42, "norail"},   // 4: drop gear-rack, keep ctx numbers
	{32, "pct"},      // 5: ctx percentage only
	{0, "badge"},     // 6: engine badge only
}

// StatusBar shows the engine state, model name, git branch, workdir,
// ctx tokens rail, and clock — using the ink & sage "·" separator and
// engine badge glyphs from loom/tui/status_bar.py.
//
// Mirrors:
//
//	_SEP        = "·"
//	_render_engine_badge(state) → "[$color]<glyph> <state>[/]"
//	  idle      → "● idle"        $text-muted
//	  thinking  → "◌ thinking"    $warning
//	  streaming → "▸ streaming"   $accent
//	  executing → "⊙ executing"   $accent
//	  compacting→ "◌ compacting"  $secondary
//	  error     → "⊗ error"       $error
type StatusBar struct {
	model       string
	tokens      int // raw ctx_tokens count
	ctxWindow   int // model's context window (e.g. 128000)
	branch      string
	ready       bool
	workdir     string
	now         time.Time
	engineState EngineState
	phase       int // gear frame counter — incremented by Tick
	turns       int // user turn count
	toolCalls   int // cumulative tool calls this session
	width       int // last SetWidth — drives 7-level adaptive compression
}

// NewStatusBar returns a StatusBar with the model set to "unknown".
// ctxWindow defaults to 128k (matches _StubLLMClient.get_context_window).
func NewStatusBar() *StatusBar {
	return &StatusBar{
		model:       "unknown",
		now:         time.Now(),
		ctxWindow:   128_000,
		engineState: StateIdle,
	}
}

// SetModel updates the displayed model name.
func (s *StatusBar) SetModel(model string) { s.model = model }

// SetTokens updates the ctx_tokens count shown in the gear rail.
func (s *StatusBar) SetTokens(tokens int) { s.tokens = tokens }

// SetCtxWindow updates the model's context window size.
func (s *StatusBar) SetCtxWindow(window int) {
	if window > 0 {
		s.ctxWindow = window
	}
}

// SetBranch updates the displayed git branch.
func (s *StatusBar) SetBranch(branch string) { s.branch = branch }

// SetWorkdir updates the displayed working directory.
func (s *StatusBar) SetWorkdir(workdir string) { s.workdir = workdir }

// SetReady toggles the connection indicator.
func (s *StatusBar) SetReady(ready bool) {
	s.ready = ready
	if !ready {
		s.engineState = StateIdle
	}
}

// SetEngineState updates the current engine state — drives the badge
// glyph/color AND the gear frame rotation (idle freezes on frame 0).
func (s *StatusBar) SetEngineState(state EngineState) { s.engineState = state }

// SetStreaming is a backwards-compat shim — equivalent to:
//
//	busy=true  → StateStreaming
//	busy=false → StateIdle
//
// Prefer SetEngineState for the full 6-state machine.
func (s *StatusBar) SetStreaming(busy bool) {
	if busy {
		s.engineState = StateStreaming
	} else if s.engineState == StateStreaming {
		s.engineState = StateIdle
	}
}

// IncTurns bumps the user turn counter (call on user_message_send).
func (s *StatusBar) IncTurns() { s.turns++ }

// SetTurns sets the user turn counter (used by /clear to reset to 0).
func (s *StatusBar) SetTurns(n int) { s.turns = n }

// PeekTurns returns the current user turn count without mutating state.
// Used by /status to display session info.
func (s *StatusBar) PeekTurns() int { return s.turns }

// IncToolCalls bumps the tool call counter (call on tool_use_started).
func (s *StatusBar) IncToolCalls() { s.toolCalls++ }

// Tick updates the displayed clock AND bumps the gear frame counter.
// Call once per second via a tea.Cmd.
func (s *StatusBar) Tick() {
	s.now = time.Now()
	s.phase++
}

// View renders the status bar as a single padded line using the ink &
// sage "·" separator and 7-level adaptive compression.
func (s *StatusBar) View() string {
	available := 80 // default — SetWidth updates this
	if s.width > 0 {
		available = s.width - 2 // padding 0 1
	}
	if available < 8 {
		available = 8
	}
	for _, lvl := range compactLevels {
		if lvl.minWidth > available {
			continue
		}
		line := s.buildLine(lvl.level)
		if visibleWidth(line) <= available {
			return statusStyle.Render(line)
		}
	}
	// Fallback: badge only (always fits)
	return statusStyle.Render(s.engineBadge())
}

// SetWidth is called from the top-level model on WindowSizeMsg so the
// 7-level adaptive compression knows how much horizontal room is available.
func (s *StatusBar) SetWidth(w int) { s.width = w }

// buildLine assembles a status bar line for the given compact *level*.
// Mirrors _build_line in loom/tui/status_bar.py.
func (s *StatusBar) buildLine(level string) string {
	ratio := 0.0
	if s.ctxWindow > 0 {
		ratio = float64(s.tokens) / float64(s.ctxWindow)
	}
	isActive := s.engineState != StateIdle

	parts := []string{s.engineBadge()}

	// Model
	modelTier := statusModelStyle
	if isActive {
		modelTier = statusModelActiveStyle
	}
	if level == "cmodel" {
		parts = append(parts, modelTier.Render(abbrevModel(s.model)))
	} else {
		parts = append(parts, modelTier.Render(s.model))
	}

	// Git branch
	if level == "full" && s.branch != "" {
		parts = append(parts, statusBranchStyle.Render("⎇ "+s.branch))
	}

	// Turns/tools stats
	if level == "pct" || level == "badge" {
		// stats dropped
	} else if level == "norail" {
		parts = append(parts, statusStatsStyle.Render(fmt.Sprintf("%dt", s.turns)))
	} else {
		parts = append(parts, statusStatsStyle.Render(fmt.Sprintf("%dt·%dtl", s.turns, s.toolCalls)))
	}

	// Context
	switch level {
	case "badge":
		// ctx dropped
	case "pct":
		parts = append(parts, s.ctxNumbersOnly(ratio))
	case "norail":
		parts = append(parts, s.ctxNumbersFull(ratio))
	default: // full, nobranch, cmodel, cstats
		parts = append(parts, s.ctxRailFull(ratio))
	}

	// Workdir + time at the end (always shown if room)
	if level == "full" || level == "nobranch" || level == "cmodel" {
		if s.workdir != "" {
			parts = append(parts, statusWorkdirStyle.Render(shortenPath(s.workdir)))
		}
	}
	parts = append(parts, statusTimeStyle.Render(s.now.Format("15:04:05")))

	sep := statusSepStyle.Render(" · ")
	return strings.Join(parts, sep)
}

// ctxRailFull renders the 14-glyph gear rail + numeric tail.
// Format: "ctx: ┅┅┅❋┄┄┄┄┄┄┄ 12k/128k (9%)"
func (s *StatusBar) ctxRailFull(ratio float64) string {
	rail := s.renderCtxRail(ratio)
	tailColor := s.ctxTailColor(ratio)
	numbers := tailColor.Render(fmt.Sprintf("%s/%s (%.0f%%)",
		formatTokens(s.tokens), formatTokens(s.ctxWindow), ratio*100))
	return statusCtxLabelStyle.Render("ctx:") + " " + rail + " " + numbers
}

// ctxNumbersFull renders "12k/128k (9%)" — no rail, for compact level 4.
func (s *StatusBar) ctxNumbersFull(ratio float64) string {
	tailColor := s.ctxTailColor(ratio)
	return statusCtxLabelStyle.Render("ctx:") + " " + tailColor.Render(
		fmt.Sprintf("%s/%s (%.0f%%)",
			formatTokens(s.tokens), formatTokens(s.ctxWindow), ratio*100))
}

// ctxNumbersOnly renders "9%" — most compact (level 5).
func (s *StatusBar) ctxNumbersOnly(ratio float64) string {
	tailColor := s.ctxTailColor(ratio)
	return tailColor.Render(fmt.Sprintf("%.0f%%", ratio*100))
}

// renderCtxRail builds the 14-glyph gear rail.
// Mirrors _ctx_rail_components in loom/tui/status_bar.py.
//
//	Used section:  $success/$warning/$error ┅ (one per filled cell)
//	Current pos:   $accent-light gear frame (❋/✻/✜)
//	Unused section: $text-faint ┄
//
// Idle freezes the gear on frame 0; active rotates by phase.
func (s *StatusBar) renderCtxRail(ratio float64) string {
	ratio = clamp(ratio, 0.0, 1.0)
	pos := round(ratio * float64(ctxRailWidth-1))
	pos = clampInt(pos, 0, ctxRailWidth-1)

	frame := gearFrames[0]
	if s.engineState != StateIdle {
		frame = gearFrames[s.phase%len(gearFrames)]
	}

	semantic := s.ctxSemanticColor(ratio)
	var b strings.Builder
	for i := 0; i < ctxRailWidth; i++ {
		switch {
		case i < pos:
			b.WriteString(semantic.Render(gearChain))
		case i == pos:
			b.WriteString(statusGearFrameStyle.Render(frame))
		default:
			b.WriteString(statusGearToothStyle.Render(gearTooth))
		}
	}
	return b.String()
}

// ctxTailColor picks the color for the numeric ctx tail based on ratio.
//
//	ratio <  0.60 → $success
//	ratio <  0.85 → $warning
//	ratio >= 0.85 → $error
func (s *StatusBar) ctxTailColor(ratio float64) lipgloss.Style {
	switch {
	case ratio >= ctxDangerRatio:
		return statusCtxDangerStyle
	case ratio >= ctxWarnRatio:
		return statusCtxWarnStyle
	default:
		return statusCtxOkStyle
	}
}

// ctxSemanticColor picks the color for the rail's filled (chain) cells.
// Same thresholds as ctxTailColor but returns a dimmed version (matches
// the original TUI's `$success`/`$warning`/`$error` chain coloring).
func (s *StatusBar) ctxSemanticColor(ratio float64) lipgloss.Style {
	switch {
	case ratio >= ctxDangerRatio:
		return statusCtxDangerStyle
	case ratio >= ctxWarnRatio:
		return statusCtxWarnStyle
	default:
		return statusCtxOkStyle
	}
}

// engineBadge returns the colored "glyph state" string for the current
// engine state. Mirrors _render_engine_badge.
func (s *StatusBar) engineBadge() string {
	if !s.ready {
		return statusReadyDotStyle.Render("○ connecting")
	}
	switch s.engineState {
	case StateThinking:
		return statusThinkingStyle.Render("◌ thinking")
	case StateStreaming:
		return statusStreamingStyle.Render("▸ streaming")
	case StateExecuting:
		return statusExecutingStyle.Render("⊙ executing")
	case StateCompacting:
		return statusCompactingStyle.Render("◌ compacting")
	case StateError:
		return statusErrorBadgeStyle.Render("⊗ error")
	default:
		return statusIdleStyle.Render("● idle")
	}
}

// width is updated by SetWidth; kept as a private field.
// (declared at the bottom to keep the struct readable above)

// ── helpers ──────────────────────────────────────────────────────────

// formatTokens mirrors _format_tokens: 1234 → "1.2k", 1500000 → "1.5M".
func formatTokens(n int) string {
	if n < 1000 {
		return fmt.Sprintf("%d", n)
	}
	if n < 1_000_000 {
		return fmt.Sprintf("%.1fk", float64(n)/1000)
	}
	return fmt.Sprintf("%.1fM", float64(n)/1_000_000)
}

// abbrevModel mirrors _abbrev_model: "deepseek/deepseek-v4-flash" → "d-deepseek-v4-flash".
func abbrevModel(model string) string {
	if strings.Contains(model, "/") {
		// strings.Cut returns (before, after, found) — assign all three.
		provider, name, _ := strings.Cut(model, "/")
		short := "?"
		if provider != "" {
			short = string(provider[0])
		}
		return short + "-" + name
	}
	parts := strings.Split(model, "-")
	if len(parts) >= 3 {
		return strings.Join(parts[1:], "-")
	}
	return model
}

// PeekTokens returns the current ctx_tokens count. Used by main.go's
// estimateCtxTokens to accumulate (chars/4) on top of the running total
// rather than re-scanning history. Read-only — does not mutate state.
func (s *StatusBar) PeekTokens() int { return s.tokens }

// visibleWidth returns the printable width of a string after stripping
// ANSI escape sequences. Mirrors _visible_width (without Rich markup
// — lipgloss-rendered strings use ANSI, not Rich tags).
func visibleWidth(s string) int {
	w := 0
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
		w++
		i++
	}
	return w
}

func clamp(v, lo, hi float64) float64 {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

func clampInt(v, lo, hi int) int {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

func round(f float64) int {
	if f < 0 {
		return -round(-f)
	}
	if f-float64(int(f)) >= 0.5 {
		return int(f) + 1
	}
	return int(f)
}

// shortenPath reduces a long absolute path to ~-prefixed or last-2-segments form.
// e.g. /Users/lanf/pra/die/loop → ~/.../loop (when $HOME is /Users/lanf)
func shortenPath(p string) string {
	if home, err := os.UserHomeDir(); err == nil && home != "" {
		if strings.HasPrefix(p, home) {
			rest := strings.TrimPrefix(p, home)
			rest = strings.TrimPrefix(rest, string(filepath.Separator))
			if rest == "" {
				return "~"
			}
			if len(rest) > 25 {
				return filepath.Join("~", "...", filepath.Base(p))
			}
			return filepath.Join("~", rest)
		}
	}
	if len(p) > 30 {
		dir := filepath.Dir(p)
		return filepath.Join("...", filepath.Base(dir), filepath.Base(p))
	}
	return p
}
