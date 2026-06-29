package view

import (
	"fmt"

	"github.com/charmbracelet/lipgloss"
)

// Header is a one-line status strip below the brand bar. It shows MCP
// server connection status as "● MCP:3/3" (healthy/total) when servers
// are configured, or is hidden entirely when no MCP servers exist.
//
// No tabs, no toggles, no expandable sections — the original TUI's
// tabbed MCP/Todo/Subagent panels are replaced by:
//   - Todo/Subagent: shown in the InfoBar above the Composer (live counts)
//   - MCP:           shown here as a single-line status summary
//   - Sessions:      accessible via the /sessions slash command (chat log)
//   - Models:        shown in the StatusBar (current model only)
//
// Visual language mirrors loom/tui/header.py MCP glyph aggregation
// (lines 125-178):
//   - ● healthy:    all configured servers report healthy state
//   - ◌ warning:    at least one server is in error state
//   - ○ disabled:   no servers configured (Header hidden)
type Header struct {
	mcpServers  []MCPServerStatus
	mcpHealthy  int
	mcpError    int
	mcpDisabled int

	// pulsePhase drives the 1Hz square-wave opacity pulse on the MCP
	// glyph when any server is in error state (draws attention). Toggled
	// by Tick() at ~1Hz. Idle (no errors) → freeze at full opacity.
	pulsePhase int
}

// MCPServerStatus is a single MCP server's status.
type MCPServerStatus struct {
	Name   string // server name (e.g. "github", "filesystem")
	State  string // "healthy" | "error" | "disabled"
	Tools  int    // number of tools exposed
	Detail string // error message or "ok"
}

// NewHeader returns an empty Header (hidden — no MCP servers configured).
func NewHeader() *Header { return &Header{} }

// SetMCPServers updates the MCP server status list. The aggregate glyph
// is computed from the worst-state: any error → ◌, all healthy → ●,
// empty → ○ (Header hidden).
func (h *Header) SetMCPServers(servers []MCPServerStatus) {
	h.mcpServers = servers
	h.mcpHealthy = 0
	h.mcpError = 0
	h.mcpDisabled = 0
	for _, s := range servers {
		switch s.State {
		case "healthy":
			h.mcpHealthy++
		case "error":
			h.mcpError++
		case "disabled":
			h.mcpDisabled++
		}
	}
}

// MCPServers returns the current MCP server status list (for /mcp panel).
func (h *Header) MCPServers() []MCPServerStatus { return h.mcpServers }

// Tick advances the pulse phase (called from the top-level tickMsg
// handler at ~1Hz). Toggles between 0 and 1 to produce a 1Hz square wave
// opacity pulse on the MCP glyph when servers are in error state.
// Idle (no errors) is unaffected — opacity stays at 1.0.
func (h *Header) Tick() {
	if h.mcpError == 0 {
		return // idle-freeze: no pulse when healthy
	}
	h.pulsePhase = (h.pulsePhase + 1) % 2
}

// mcpGlyph returns the worst-state glyph for the MCP section.
//   - any error → ◌ warning (pulsing when mcpError > 0)
//   - all healthy (and non-empty) → ● healthy
//   - empty → ○ disabled (Header hidden)
func (h *Header) mcpGlyph() string {
	total := h.mcpHealthy + h.mcpError + h.mcpDisabled
	if total == 0 {
		return glyphDisabled
	}
	if h.mcpError > 0 {
		return glyphWarning
	}
	return glyphHealthy
}

// Empty reports whether the Header has anything to show (no MCP servers
// configured). When true, the caller should skip the row entirely so
// the layout collapses.
func (h *Header) Empty() bool {
	total := h.mcpHealthy + h.mcpError + h.mcpDisabled
	return total == 0
}

// View renders the Header as "● MCP:3/3" (or "◌ MCP:2/3" when any error).
// Returns "" when Empty() — caller should skip the row so the layout
// collapses and the chat area gets the extra line.
func (h *Header) View() string {
	if h.Empty() {
		return ""
	}
	total := h.mcpHealthy + h.mcpError + h.mcpDisabled
	glyph := h.mcpGlyph()
	// Pulse: when pulsing (pulsePhase=1) and any error, render glyph dim
	// to produce the 1Hz opacity wave (1.0 → 0.5 → 1.0).
	if h.pulsePhase == 1 && h.mcpError > 0 {
		glyph = lipgloss.NewStyle().Faint(true).Render(glyph)
	}
	body := fmt.Sprintf(" MCP:%d/%d", h.mcpHealthy, total)
	return lipgloss.NewStyle().Foreground(colorTextMuted).Render(glyph) +
		lipgloss.NewStyle().Foreground(colorTextMuted).Render(body)
}

// ── glyph vocabulary ─────────────────────────────────────────────────
//
// Mirrors loom/tui/header.py:50-54 glyph constants:
//   - ● glyphHealthy  "all systems normal"
//   - ✓ glyphDone     "completed successfully" (used by InfoBar)
//   - ◌ glyphWarning  "at least one error"
//   - ◐ glyphActive   "currently running" (used by InfoBar)
//   - ○ glyphDisabled "no items / disabled"
const (
	glyphHealthy  = "●"
	glyphDone     = "✓"
	glyphWarning  = "◌"
	glyphActive   = "◐"
	glyphDisabled = "○"
)
