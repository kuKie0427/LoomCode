package view

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/lipgloss"
)

// PermissionModal renders a Y/N prompt for a server-initiated permission
// request. Visible state toggles via Show/Hide; View() returns "" when hidden.
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

// View renders the modal. Returns "" when hidden.
func (p *PermissionModal) View() string {
	if !p.visible {
		return ""
	}
	var b strings.Builder
	b.WriteString(lipgloss.NewStyle().Bold(true).Render("⚠ Permission Required") + "\n\n")
	b.WriteString(fmt.Sprintf("Tool: %s\n", p.toolName))
	b.WriteString(fmt.Sprintf("Args: %v\n", p.args))
	if p.reason != "" {
		b.WriteString(fmt.Sprintf("Reason: %s\n", p.reason))
	}
	b.WriteString("\n[Y] Allow  [N] Deny  [Esc] Cancel")
	return lipgloss.NewStyle().
		Border(lipgloss.RoundedBorder()).
		Padding(1, 2).
		Render(b.String())
}
