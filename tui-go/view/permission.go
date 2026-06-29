package view

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/lipgloss"
)

// PermissionModal renders a Y/N prompt for a server-initiated permission
// request. Visible state toggles via Show/Hide; View() returns "" when
// hidden. When rendered via Render(width, height), the modal is centered
// on screen.
//
// Visual language mirrors loom/tui/screens.py::PermissionScreen:
//   - border: thick $error  (NOT rounded — see PermissionScreen #perm-dialog)
//   - padding: 1
//   - tool name: $secondary
//   - args: $text
//   - reason: $error (bold)
//   - keys: $accent (bold)
type PermissionModal struct {
	toolName  string
	args      map[string]any
	reason    string
	requestID string
	visible   bool
}

// NewPermissionModal returns a hidden modal.
func NewPermissionModal() *PermissionModal {
	return &PermissionModal{}
}

// Show makes the modal visible with the given prompt details.
// requestID is the server-initiated request id the TUI must reference in its
// permission_response reply.
func (p *PermissionModal) Show(requestID, toolName string, args map[string]any, reason string) {
	p.requestID = requestID
	p.toolName = toolName
	p.args = args
	p.reason = reason
	p.visible = true
}

// Hide hides the modal. The requestID is preserved in case the caller wants
// to read it via RequestID() before clearing.
func (p *PermissionModal) Hide() {
	p.visible = false
}

// Visible reports whether the modal is currently shown.
func (p *PermissionModal) Visible() bool {
	return p.visible
}

// RequestID returns the stored server-initiated request id.
func (p *PermissionModal) RequestID() string {
	return p.requestID
}

// View renders the modal as a bordered box. Returns "" when hidden.
// The caller should use Render(width, height) instead to get a centered overlay.
func (p *PermissionModal) View() string {
	if !p.visible {
		return ""
	}
	return p.Render(80, 24)
}

// Render builds the modal box and centers it within a (width, height) screen.
// Call this from the top-level View() with the current terminal size so the
// prompt appears in the middle of the screen instead of jammed at the bottom.
func (p *PermissionModal) Render(width, height int) string {
	if !p.visible {
		return ""
	}
	var b strings.Builder
	b.WriteString(permissionTitleStyle.Render("⚠  "+p.reason) + "\n\n")
	b.WriteString(fmt.Sprintf("Tool: %s\n", permissionToolStyle.Render(p.toolName)))
	b.WriteString(fmt.Sprintf("Args: %s\n", permissionArgsStyle.Render(formatArgs(p.args))))
	b.WriteString("\n" + permissionScopeStyle.Render(scopeExplanation(p.toolName)))
	b.WriteString("\n\n" + permissionKeysStyle.Render("[a] Allow once   [A] Allow always   [d/Esc] Deny"))
	box := permissionBoxStyle.Render(b.String())
	return lipgloss.Place(width, height, lipgloss.Center, lipgloss.Center, box)
}

// workspaceWriteTools mirrors permission_store.py::WORKSPACE_WRITE_TOOLS —
// tools that can never be "Allow always" granted (defense-in-depth).
var workspaceWriteTools = map[string]bool{
	"write_file": true,
	"edit_file":  true,
	"multi_edit": true,
	"edit_lines": true,
}

// scopeExplanation returns the scope help text shown in the modal.
// Mirrors PermissionScreen._scope_explanation (screens.py:61-77):
//
//   - write/edit tools: "Allow always is not available for write/edit
//     tools; you will be re-prompted each time."
//   - all other tools: "Allow always remembers this tool + pattern in
//     .minicode/permissions.json for 30 days."
func scopeExplanation(toolName string) string {
	if workspaceWriteTools[toolName] {
		return "Allow always is not available for write/edit tools; you will be re-prompted each time."
	}
	return "Allow always remembers this tool + pattern in .minicode/permissions.json for 30 days."
}

// formatArgs renders the args map as a single line, truncated for the modal.
func formatArgs(args map[string]any) string {
	if len(args) == 0 {
		return "{}"
	}
	parts := make([]string, 0, len(args))
	for k, v := range args {
		s := fmt.Sprintf("%v", v)
		if len(s) > 50 {
			s = s[:47] + "..."
		}
		parts = append(parts, fmt.Sprintf("%s=%s", k, s))
	}
	out := strings.Join(parts, " ")
	if len(out) > 200 {
		out = out[:197] + "..."
	}
	return out
}

// ── ink & sage permission styles ─────────────────────────────────────
//
// Mirrors PermissionScreen DEFAULT_CSS:
//
//	#perm-dialog { border: thick $error; padding: 1 }
//	Tool: [$secondary]{tool_name}
//	Args: $text
//	⚠ reason: $error bold
//	keys: $accent bold
//
// lipgloss.ThickBorder() = the "thick" border style (═══║║╗╝╚╔).
var (
	permissionBoxStyle = lipgloss.NewStyle().
				Border(lipgloss.ThickBorder()).
				BorderForeground(colorError).
				Padding(1, 2)

	permissionTitleStyle  = lipgloss.NewStyle().Bold(true).Foreground(colorError)
	permissionToolStyle   = lipgloss.NewStyle().Bold(true).Foreground(colorSecondary)
	permissionArgsStyle   = lipgloss.NewStyle().Foreground(colorForeground)
	permissionReasonStyle = lipgloss.NewStyle().Foreground(colorError).Italic(true)
	permissionScopeStyle  = lipgloss.NewStyle().Foreground(colorTextMuted).Italic(true)
	permissionKeysStyle   = lipgloss.NewStyle().Bold(true).Foreground(colorAccent)
)
